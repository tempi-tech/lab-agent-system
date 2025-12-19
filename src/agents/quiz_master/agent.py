from __future__ import annotations

import os
import time
import secrets
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import discord

from src.core.agent_base import BaseAgent
from .config import QuizSpec, QuestionType
from .storage import JsonStore, QuizSessionState, Submission, QuestionGrading
from .utils import parse_quiz_command, normalize_choice, sha256_hex, deterministic_draw, clip
from .scoring import GeminiLLM, score_creative_answers


def _env_int_list(name: str) -> List[int]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    out: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


class QuizMasterAgent(BaseAgent):
    """
    One-night quiz runner agent.
    Admin controls with `!quiz ...` in a designated channel.

    Participant flow:
    - Bot posts each question as a message and creates a thread for answers.
    - Participants answer by sending a message in the thread.
    - `!quiz close` grades and posts interim results.
    """

    @property
    def name(self) -> str:
        return "quiz_master"

    def __init__(self):

        # Config / paths
        self.repo_root = Path(os.getenv("REPO_ROOT", ".")).resolve()
        self.data_dir = self.repo_root / "data" / "quiz_master"
        self.store = JsonStore(self.data_dir / "session.json")

        # Who can operate
        self.admin_user_ids = set(_env_int_list("QUIZ_ADMIN_USER_IDS"))
        self.admin_role_ids = set(_env_int_list("QUIZ_ADMIN_ROLE_IDS"))  # optional

        # Default quiz JSON
        self.default_quiz_path = os.getenv(
            "QUIZ_DEFAULT_CONFIG",
            str(Path("src/agents/quiz_master/quizzes/bonenkai_2025.json")),
        )

        # runtime
        self.client: Optional[discord.Client] = None
        self.quiz: Optional[QuizSpec] = None
        self.state: Optional[QuizSessionState] = None

        # LLM
        self.llm = GeminiLLM(model=os.getenv("QUIZ_GEMINI_MODEL", "gemini-3-flash-preview"))

    async def on_ready(self, client: discord.Client):
        self.client = client
        self.state = self.store.load()
        if self.state:
            # Best effort load quiz spec (path could change, so load default)
            try:
                self.quiz = QuizSpec.load_json(self.repo_root / self.default_quiz_path)
            except Exception:
                self.quiz = None

        print("[quiz_master] Ready. state_loaded=", bool(self.state))

    def _is_admin(self, message: discord.Message) -> bool:
        if not message.guild:
            return False
        if message.author.id in self.admin_user_ids:
            return True
        if self.admin_role_ids:
            # role-based permission
            try:
                member = message.guild.get_member(message.author.id)
                if member and any(r.id in self.admin_role_ids for r in member.roles):
                    return True
            except Exception:
                pass
        return False

    def _require_state(self) -> QuizSessionState:
        if not self.state:
            self.state = QuizSessionState()
        return self.state

    def _require_quiz(self) -> QuizSpec:
        if not self.quiz:
            # load default
            self.quiz = QuizSpec.load_json(self.repo_root / self.default_quiz_path)
        return self.quiz

    async def on_message(self, message: discord.Message):
        # ignore bot/self messages
        if message.author.bot:
            return

        # 1) Admin command handling
        cmd = parse_quiz_command(message.content)
        if cmd and self._is_admin(message):
            sub, args = cmd
            await self._handle_admin_command(message, sub, args)
            return

        # 2) Participant submissions in the active thread
        if not self.state or not self.quiz:
            return
        if not self.state.is_question_open or not self.state.thread_id:
            return
        if not isinstance(message.channel, discord.Thread):
            return
        if message.channel.id != self.state.thread_id:
            return

        await self._capture_submission(message)

    async def _handle_admin_command(self, message: discord.Message, sub: str, args: str):
        if sub in ("help", "h", "?"):
            await message.channel.send(self._help_text())
            return

        if sub == "reset":
            self.store.clear()
            self.state = None
            await message.channel.send("✅ クイズ状態をリセットしました。")
            return

        if sub == "load":
            # args: path or filename in quizzes/
            if not args:
                await message.channel.send("例: `!quiz load bonenkai_2025.json` もしくは `!quiz load path/to/file.json`")
                return
            path = self._resolve_quiz_path(args)
            self.quiz = QuizSpec.load_json(path)
            await message.channel.send(f"✅ クイズ設定を読み込みました: `{path}` / タイトル: **{self.quiz.title}**")
            return

        if sub == "start":
            # Start session in this channel (guild required)
            if not message.guild:
                await message.channel.send("このコマンドはサーバー内のチャンネルで実行してください。")
                return
            # allow `!quiz start <config>`
            if args:
                path = self._resolve_quiz_path(args)
                self.quiz = QuizSpec.load_json(path)
            else:
                self.quiz = self._require_quiz()

            st = QuizSessionState(
                quiz_id=self.quiz.quiz_id,
                quiz_title=self.quiz.title,
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                started_at=time.time(),
                current_index=-1,
            )

            # seed commit for fair draw
            seed = secrets.token_hex(16)
            st.draw_seed = seed
            st.draw_seed_hash = sha256_hex(seed)

            self.state = st
            self.store.save(st)

            await message.channel.send(
                f"🎉 **{self.quiz.title}** を開始します！\n"
                f"（抽選の公平性のため、seed hash を先に公開します）\n"
                f"seed_hash = `{st.draw_seed_hash}`\n"
                f"次の質問を出します…"
            )
            await self._post_next_question(message.channel)
            return

        if sub == "status":
            await message.channel.send(self._status_text())
            return

        if sub == "next":
            await self._post_next_question(message.channel)
            return

        if sub == "close":
            await self._close_and_grade(message.channel)
            return

        if sub == "leaderboard":
            await self._post_leaderboard(message.channel)
            return

        if sub == "end":
            # close if open
            if self.state and self.state.is_question_open:
                await self._close_and_grade(message.channel)
            await self._finalize(message.channel)
            return

        await message.channel.send("コマンドが分かりません。`!quiz help` を見てください。")

    def _resolve_quiz_path(self, arg: str) -> Path:
        # If arg includes slash or endswith .json, treat as path; else look in quizzes/
        candidate = Path(arg)
        if candidate.suffix.lower() != ".json":
            candidate = Path(arg + ".json")
        if candidate.is_absolute():
            return candidate
        # try relative as given
        direct = (self.repo_root / candidate)
        if direct.exists():
            return direct
        # try quizzes folder
        qdir = self.repo_root / "src" / "agents" / "quiz_master" / "quizzes"
        in_quizzes = qdir / candidate.name
        return in_quizzes

    async def _post_next_question(self, channel: discord.abc.Messageable):
        quiz = self._require_quiz()
        st = self._require_state()

        if st.is_question_open:
            await channel.send("⚠️ いまの質問が open のままです。先に `!quiz close` してください。")
            return

        next_index = st.current_index + 1
        if next_index >= len(quiz.questions):
            await channel.send("✅ すべての問題が出題済みです。`!quiz end` で終了できます。")
            return

        q = quiz.questions[next_index]
        st.current_index = next_index
        st.current_question_id = q.id
        st.is_question_open = True

        # Post question
        header = f"**Q{next_index+1}. {q.title}** ({q.points}pt / 制限 {q.time_limit_sec}s)\n"
        prompt = q.prompt.strip()
        body = header + "\n" + prompt + "\n\n"

        if q.type == QuestionType.KNOWLEDGE and q.options:
            opts = "\n".join([f"**{o.key}.** {o.text}" for o in q.options])
            body += opts + "\n\n"
            body += "📝 回答はこのスレッドに **A/B/C/D**（または 1/2/3/4）で送ってください。"
        else:
            body += f"📝 回答はこのスレッドに送ってください（{q.max_chars}文字目安）。AIが採点します。"

        msg = await channel.send(body)

        # Create thread for answers
        try:
            thread = await msg.create_thread(
                name=f"Q{next_index+1}-{q.id}",
                auto_archive_duration=60,  # minutes
            )
        except Exception:
            # fallback: no thread
            thread = None

        st.question_message_id = msg.id
        st.thread_id = thread.id if thread else 0

        # ensure dicts
        st.submissions.setdefault(q.id, {})
        st.grading.setdefault(q.id, QuestionGrading())

        self.store.save(st)

        if thread:
            await thread.send(
                "✅ このスレッドに回答してください！\n"
                "（上書き回答したい場合は、もう一度送ると最後のメッセージを採用します）"
            )
        await channel.send("⏱️ 司会: 時間になったら `!quiz close` で締めてください。")

    async def _capture_submission(self, message: discord.Message):
        assert self.state and self.quiz
        st = self.state
        q = self.quiz.questions[st.current_index]
        qid = q.id

        content = message.content.strip()
        if not content:
            return
        if len(content) > 4000:
            content = content[:4000] + "…"

        uid = str(message.author.id)

        # allow answer edit: keep latest
        by_user = st.submissions.setdefault(qid, {})
        if (not self.quiz.allow_answer_edit) and (uid in by_user):
            return

        by_user[uid] = Submission(
            user_id=message.author.id,
            user_display=message.author.display_name,
            content=content,
            created_at=message.created_at.timestamp(),
        )
        st.users[uid] = message.author.display_name
        self.store.save(st)

        # lightweight ack: react ✅ to reduce channel noise
        try:
            await message.add_reaction("✅")
        except Exception:
            pass

    async def _close_and_grade(self, channel: discord.abc.Messageable):
        quiz = self._require_quiz()
        st = self._require_state()

        if not st.is_question_open:
            await channel.send("⚠️ いま open な質問がありません。`!quiz next` で次へ。")
            return
        q = quiz.questions[st.current_index]
        qid = q.id
        st.is_question_open = False
        self.store.save(st)

        await channel.send(f"🔒 **Q{st.current_index+1} を締めました。採点します…**（type={q.type.value}）")

        subs = list((st.submissions.get(qid) or {}).values())
        if not subs:
            await channel.send("回答がありませんでした。")
            return

        if q.type == QuestionType.KNOWLEDGE:
            scores, meta = self._grade_knowledge(q, subs)
            reasons: Dict[str, str] = {}
        else:
            answers_for_llm: List[Tuple[str, str, str]] = [(str(s.user_id), s.user_display, s.content) for s in subs]
            scores, reasons, meta = await score_creative_answers(
                self.llm,
                question_title=q.title,
                question_prompt=q.prompt,
                rubric=q.rubric,
                answers=answers_for_llm,
                points=q.points,
            )

        # Persist grading
        g = st.grading.setdefault(qid, QuestionGrading())
        g.scores = {str(uid): int(sc) for uid, sc in scores.items()}
        g.reasons = {str(uid): str(rs) for uid, rs in reasons.items()}
        g.meta = meta

        # Update totals
        for uid, pts in scores.items():
            st.totals[uid] = int(st.totals.get(uid, 0)) + int(pts)

        self.store.save(st)

        # Announce result summary
        await self._post_question_result(channel, q, scores, reasons)

        # Optional: lucky draw among respondents
        candidates = list(scores.keys()) if scores else [str(s.user_id) for s in subs]
        lucky = deterministic_draw(st.draw_seed, f"lucky|{qid}", candidates)
        if lucky:
            name = st.users.get(str(lucky), f"<@{lucky}>")
            await channel.send(f"🎁 **ラッキー賞（抽選）**: {name} さん！")

    def _grade_knowledge(self, q, subs: List[Submission]) -> Tuple[Dict[str, int], Dict[str, str]]:
        # Determine correct option / accepted answers
        correct_opt = (q.correct_option or "").upper().strip()
        accepted = set([a.strip().lower() for a in (q.accepted_answers or []) if a.strip()])

        scores: Dict[str, int] = {}
        meta: Dict[str, str] = {"correct_option": correct_opt, "accepted_answers_count": str(len(accepted))}

        for s in subs:
            uid = str(s.user_id)
            raw = s.content.strip()
            if not raw:
                continue
            norm_choice = normalize_choice(raw)
            norm_text = raw.strip().lower()

            ok = False
            if correct_opt and norm_choice == correct_opt:
                ok = True
            elif accepted and norm_text in accepted:
                ok = True

            scores[uid] = q.points if ok else 0

        return scores, meta

    async def _post_question_result(
        self,
        channel: discord.abc.Messageable,
        q,
        scores: Dict[str, int],
        reasons: Dict[str, str],
    ):
        assert self.state
        st = self.state
        # sort by points desc
        ranking = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        top = ranking[:10]

        lines = []
        for uid, pts in top:
            name = st.users.get(uid, f"<@{uid}>")
            reason = reasons.get(uid, "")
            if reason:
                lines.append(f"- {name}: **{pts}pt** — {reason}")
            else:
                lines.append(f"- {name}: **{pts}pt**")

        msg = f"📊 **結果: {q.title}**\n" + "\n".join(lines)
        await channel.send(clip(msg, 1800))

        # If knowledge: show correct
        if q.type == QuestionType.KNOWLEDGE and q.correct_option:
            await channel.send(f"✅ 正解: **{q.correct_option}**")

        # Post leaderboard
        await self._post_leaderboard(channel)

    async def _post_leaderboard(self, channel: discord.abc.Messageable):
        assert self.state
        st = self.state
        totals_sorted = sorted(st.totals.items(), key=lambda kv: kv[1], reverse=True)
        top = totals_sorted[:10]
        lines = []
        for i, (uid, pts) in enumerate(top, start=1):
            name = st.users.get(uid, f"<@{uid}>")
            lines.append(f"{i}. {name} — **{pts}pt**")
        body = "\n".join(lines) if lines else "まだスコアがありません。"
        await channel.send("🏆 **暫定ランキング**\n" + body)

    async def _finalize(self, channel: discord.abc.Messageable):
        if not self.state:
            await channel.send("状態がありません。")
            return
        st = self.state
        await channel.send("✅ クイズを終了します。最終ランキングを発表します！")
        await self._post_leaderboard(channel)

        # Reveal seed for verification
        await channel.send(f"🔎 抽選seed（検証用）: `{st.draw_seed}`\nseed_hash: `{st.draw_seed_hash}`")

        # Winner draw: highest score wins, tie => deterministic draw among ties
        if not st.totals:
            await channel.send("参加者スコアがありません。")
            return
        max_score = max(st.totals.values())
        ties = [uid for uid, pts in st.totals.items() if pts == max_score]
        winner = ties[0] if len(ties) == 1 else deterministic_draw(st.draw_seed, "overall_winner", ties)
        if winner:
            name = st.users.get(winner, f"<@{winner}>")
            await channel.send(f"🥇 **優勝**: {name} さん（{max_score}pt）おめでとうございます！")

        # Optional: raffle among anyone who answered at least one question
        answered_any = set()
        for by_user in st.submissions.values():
            answered_any.update(by_user.keys())
        raffle = deterministic_draw(st.draw_seed, "consolation", list(answered_any))
        if raffle:
            name = st.users.get(raffle, f"<@{raffle}>")
            await channel.send(f"🎁 **参加賞（抽選）**: {name} さん！")

        await channel.send("（必要なら `!quiz reset` で状態クリアできます）")

    def _status_text(self) -> str:
        if not self.state:
            return "状態なし。`!quiz start` で開始。"
        st = self.state
        return (
            f"**状態**\n"
            f"- quiz: {st.quiz_title} ({st.quiz_id})\n"
            f"- guild_id: {st.guild_id}\n"
            f"- channel_id: {st.channel_id}\n"
            f"- index: {st.current_index}\n"
            f"- open: {st.is_question_open}\n"
            f"- current_qid: {st.current_question_id}\n"
            f"- thread_id: {st.thread_id}\n"
        )

    def _help_text(self) -> str:
        return (
            "**Quiz Master コマンド**（運営用）\n"
            "- `!quiz start [config]` : クイズ開始（このチャンネルで実行）\n"
            "- `!quiz next` : 次の問題を出す（前がclose済み前提）\n"
            "- `!quiz close` : 現在の問題を締めて採点\n"
            "- `!quiz leaderboard` : ランキング表示\n"
            "- `!quiz status` : 状態確認\n"
            "- `!quiz load <file>` : 設定JSON読み込み（quizzes/ か相対パス）\n"
            "- `!quiz end` : 終了（seed公開&優勝発表）\n"
            "- `!quiz reset` : 状態リセット\n"
            "\n"
            "参加者は、各問題の **スレッド** に回答メッセージを送るだけでOKです。"
        )
