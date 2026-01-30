import asyncio
import os
import re
import discord
from pathlib import Path
from datetime import datetime, timedelta, timezone
from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from . import config
from src.core import config as core_config

from src.core.agent_base import BaseAgent
from src.agents.daily_reporter.storage import DailyDigestStore
from src.agents.daily_reporter import radio

DISCORD_URL_PREFIX = "https://discord.com/"
URL_PATTERN = re.compile(r"https?://\S+")
EXTERNAL_LINK_PLACEHOLDER = "[外部リンク]"
CHANNEL_LINK_PATTERN = re.compile(r"https://discord\.com/channels/\d+/(\d+)/\d+")



def sanitize_report_output(text: str) -> str:
    # Remove non-Discord URLs and any leftover empty markdown link wrappers.
    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        trimmed = url.rstrip(").,!?、。）」】]")
        suffix = url[len(trimmed):]
        if trimmed.startswith(DISCORD_URL_PREFIX):
            return trimmed + suffix
        return ""

    cleaned = URL_PATTERN.sub(replace_url, text)
    cleaned = re.sub(r"\[([^\]]+)\]\(\s*\)", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"^\s*ラボちゃんより\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())

    lines = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if stripped in {"-", "・", "•"}:
            continue
        if "理由:" in line and DISCORD_URL_PREFIX not in line and "なし" not in line:
            continue
        lines.append(line)
    lines = remove_invalid_topic_lines(lines)
    normalized = "\n".join(lines).strip()
    return normalize_section_layout(normalized)


def remove_invalid_topic_lines(lines: list[str]) -> list[str]:
    header = "📝 **トピック**"
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        result.append(line)
        if line.strip() == header:
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()
                if next_line.startswith("✨") or next_line.startswith("🆕") or next_line.startswith("📅") or next_line.startswith("📝"):
                    result.append(lines[i])
                    break
                # Validate topic line: must contain Discord URL
                if next_line and DISCORD_URL_PREFIX not in next_line:
                    i += 1
                    continue
                result.append(lines[i])
                i += 1
            i += 1
            continue
        i += 1
    return result


def normalize_section_layout(text: str) -> str:
    section_titles = [
        "📅 **今日のラボ日誌**",
        "📝 **トピック**",
        "✨ **今日のハイライト**",
        "🆕 **新しいセンパイ**",
        "🆕 **New Members**",
    ]
    normalized = text.replace("\r\n", "\n").strip()
    first_title = section_titles[0]
    first_index = normalized.find(first_title)
    if first_index > 0:
        normalized = normalized[first_index:]
    if not normalized.startswith(first_title):
        normalized = f"{first_title}\n\n{normalized}"

    for title in section_titles:
        normalized = re.sub(rf"{re.escape(title)}[ \t]+", f"{title}\n", normalized)
        normalized = re.sub(rf"{re.escape(title)}(?!\n)", f"{title}\n", normalized)

    for title in section_titles[1:]:
        normalized = re.sub(rf"\n*{re.escape(title)}", f"\n\n{title}", normalized)

    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def sanitize_message_content(text: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        trimmed = url.rstrip(").,!?、。）」】]")
        if trimmed.startswith(DISCORD_URL_PREFIX):
            return trimmed
        return EXTERNAL_LINK_PLACEHOLDER

    return URL_PATTERN.sub(replace_url, text)


def resolve_daily_audio_path(now_utc: datetime) -> Path:
    custom_path = os.getenv("DAILY_REPORT_WAV_PATH", "").strip()
    if custom_path:
        return Path(custom_path)

    jst = timezone(timedelta(hours=9))
    date_str = now_utc.astimezone(jst).strftime("%Y_%m_%d")
    return Path(core_config.BASE_DIR) / f"lab_digest_{date_str}.wav"


class DailyReporterAgent(BaseAgent):
    def __init__(self):
        self.client = None # Will be set in on_ready
        self.action_namespace = "daily_reporter"
        self._digest_store = DailyDigestStore(Path("data/daily_reporter/digests.sqlite"))

    @property
    def name(self) -> str:
        return "DailyReporterAgent"

    def get_actions(self):
        return {"run": self.run}

    async def on_ready(self, client: discord.Client):
        self.client = client
        print("DailyReporterAgent is ready.")

    async def run(self, message: discord.Message, args: list[str]) -> None:
        if not self.client:
            await message.channel.send("DailyReporter is not ready yet.")
            return

        target_channel = None
        if args and args[0].lower() == "here":
            target_channel = message.channel
        elif core_config.TARGET_CHANNEL_ID:
            target_channel = self.client.get_channel(int(core_config.TARGET_CHANNEL_ID))

        if not target_channel:
            await message.channel.send(
                "DailyReporter target channel not found. Set `DISCORD_CHANNEL_ID` or use `!agent daily_reporter run here`."
            )
            return

        await self.generate_summary(target_channel)

    async def get_or_create_webhook(self, channel):
        webhooks = await channel.webhooks()
        webhook = None
        for wh in webhooks:
            if wh.name == "ADK Summary Webhook":
                webhook = wh
                break
        
        if not webhook:
            webhook = await channel.create_webhook(name="ADK Summary Webhook")
            
        # Update Avatar if file exists
        if os.path.exists(config.AVATAR_PATH):
            try:
                with open(config.AVATAR_PATH, "rb") as f:
                    avatar_bytes = f.read()
                await webhook.edit(avatar=avatar_bytes)
                print("Webhook avatar updated from local file.")
            except Exception as e:
                print(f"Failed to update webhook avatar: {e}")
                
        return webhook

    async def fetch_daily_messages(self, channel, threshold):
        """Fetches messages from a channel (Text or Forum) since the threshold."""
        messages = []

        if isinstance(channel, discord.TextChannel):
            print(f"Fetching from TextChannel: {channel.name}")
            async for msg in channel.history(after=threshold, limit=None):
                if not msg.author.bot:
                    messages.append(msg)

        elif isinstance(channel, discord.ForumChannel):
            print(f"Fetching from ForumChannel: {channel.name}")
            # Iterate over active threads
            for thread in channel.threads:
                async for msg in thread.history(after=threshold, limit=None):
                    if not msg.author.bot:
                        messages.append(msg)

        return messages

    def resolve_source_channels(self) -> list[discord.abc.GuildChannel]:
        source_ids = set(config.SOURCE_CHANNELS)
        for category_id in config.SOURCE_CATEGORY_IDS:
            category = self.client.get_channel(category_id)
            if isinstance(category, discord.CategoryChannel):
                for channel in category.channels:
                    if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                        source_ids.add(channel.id)
            else:
                print(f"Warning: Category {category_id} not found or not a category.")

        source_ids -= config.SOURCE_CHANNEL_EXCLUDE_IDS

        channels: list[discord.abc.GuildChannel] = []
        for channel_id in sorted(source_ids):
            channel = self.client.get_channel(channel_id)
            if channel:
                channels.append(channel)
            else:
                print(f"Warning: Could not find channel {channel_id}")
        return channels

    def format_tips_for_thread(self, raw_tips: str) -> str:
        """TipsScoutの出力を読みやすいカード形式に整形"""
        if not raw_tips or raw_tips.strip() == "なし":
            return ""

        tips_blocks = re.findall(
            r"TIPS_START\s*(.*?)\s*TIPS_END",
            raw_tips,
            re.DOTALL
        )

        if not tips_blocks:
            return ""

        formatted_tips = ["💡 **明日試して欲しいこと**", ""]  # ヘッダー後に空行

        for i, block in enumerate(tips_blocks, 1):
            lines = block.strip().split("\n")
            summary = ""
            points = []
            url = ""

            for line in lines:
                line = line.strip()
                if line.startswith("概要:"):
                    summary = line.replace("概要:", "").strip()
                elif line.startswith("○"):
                    point_text = line[1:].strip()
                    if point_text:
                        points.append(point_text)
                elif line.startswith("- "):
                    point_text = line[2:].strip()
                    if point_text:
                        points.append(point_text)
                elif line.startswith("ポイント:"):
                    point_text = line.replace("ポイント:", "").strip()
                    if point_text:
                        points.append(point_text)
                elif line.startswith("URL:"):
                    url = line.replace("URL:", "").strip()

            if summary:
                formatted_tips.append(f"**{i}. {summary}**")
                for point in points:
                    formatted_tips.append(f"○ {point}")
                if url and url.startswith(DISCORD_URL_PREFIX):
                    formatted_tips.append(f"📎 {url}")
                formatted_tips.append("")  # 各Tips後に空行

        return "\n".join(formatted_tips).rstrip()

    async def post_tips_thread(
        self,
        main_message: discord.WebhookMessage,
        tips_text: str,
        webhook: discord.Webhook
    ):
        """Tipsをメインレポートのスレッドとして投稿"""
        formatted_tips = self.format_tips_for_thread(tips_text)
        if not formatted_tips:
            print("No Tips to post (empty or 'なし').")
            return None

        try:
            thread = await main_message.create_thread(
                name="💡 明日試して欲しいこと",
                auto_archive_duration=1440  # 24時間
            )

            await webhook.send(
                content=formatted_tips,
                thread=thread,
                username=config.REPORTER_NAME
            )
            print(f"Tips thread created: {thread.name}")
            return thread
        except Exception as e:
            print(f"Failed to create tips thread: {e}")
            return None

    async def post_audio_if_exists(self, webhook: discord.Webhook, now_utc: datetime) -> None:
        audio_path = resolve_daily_audio_path(now_utc)
        if not audio_path.exists():
            print(f"Audio file not found: {audio_path}")
            return

        try:
            await webhook.send(
                content="🔊 デイリーダイジェスト音声はこちらッス！",
                file=discord.File(str(audio_path), filename=audio_path.name),
                username=config.REPORTER_NAME,
            )
            print(f"Audio posted: {audio_path.name}")
        except Exception as e:
            print(f"Failed to post audio: {e}")

    async def post_audio(self, webhook: discord.Webhook, audio_path: Path) -> None:
        if not audio_path.exists():
            print(f"Audio file not found: {audio_path}")
            return

        try:
            await webhook.send(
                content="🔊 デイリーダイジェスト音声はこちらッス！",
                file=discord.File(str(audio_path), filename=audio_path.name),
                username=config.REPORTER_NAME,
            )
            print(f"Audio posted: {audio_path.name}")
        except Exception as e:
            print(f"Failed to post audio: {e}")


    async def generate_summary(self, target_channel):
        # 1. Calculate Time Threshold (24 hours ago in JST)
        now_utc = datetime.now(timezone.utc)
        threshold = now_utc - timedelta(hours=24)
        print(f"Fetching messages since: {threshold} (UTC)")

        # 2. Fetch Messages from All Channels
        all_messages = []
        candidates_for_mvp = []
        candidates_for_highlight = []

        source_channels = self.resolve_source_channels()
        for channel in source_channels:
            msgs = await self.fetch_daily_messages(channel, threshold)
            all_messages.extend(msgs)

        print(f"Total messages fetched: {len(all_messages)}")

        # 3. Process Messages
        formatted_messages = []
        for msg in all_messages:
            is_admin = False
            if isinstance(msg.author, discord.Member):
                for role in msg.author.roles:
                    if role.name == "chatgpt-lab-admin":
                        is_admin = True
                        break
            
            if isinstance(msg.author, discord.Member):
                author_display = msg.author.display_name
            else:
                author_display = msg.author.name
            if is_admin:
                author_display += " [Admin]"
            else:
                candidates_for_mvp.append(msg.author.id)
            
            candidates_for_highlight.append(msg.author.id)

            location = msg.channel.name
            if hasattr(msg.channel, "parent") and msg.channel.parent:
                 location = f"{msg.channel.parent.name} > {msg.channel.name}"

            message_content = sanitize_message_content(msg.content)
            formatted_messages.append(
                f"User: {author_display} (ID: {msg.author.id})\n"
                f"Location: {location}\n"
                f"URL: {msg.jump_url}\n"
                f"Message: {message_content}"
            )

        # 4. New Member Detection
        new_member_mentions: list[str] = []
        new_member_names: list[str] = []
        if source_channels:
            guild = source_channels[0].guild
            if guild:
                print(f"Checking for new members in guild: {guild.name}")
                for member in guild.members:
                    if member.joined_at and member.joined_at > threshold:
                        if not member.bot:
                            new_member_mentions.append(f"<@{member.id}>")
                            new_member_names.append(member.display_name or member.name)
        else:
            print("Warning: No source channels configured. Skipping new member detection.")
        
        new_member_mentions_str = " ".join(new_member_mentions) if new_member_mentions else "なし"
        new_member_names_str = "、".join(new_member_names) if new_member_names else "なし"
        print(f"New members found: {new_member_mentions_str}")

        if not formatted_messages and not new_member_mentions:
            await target_channel.send("今日は静かな一日でしたね。（メッセージも新メンバーもなし）")
            return

        history_text = "\n---\n".join(formatted_messages)
        
        print("Analyzing...")
        await target_channel.send(f"🕵️‍♀️ ラボちゃんが {len(source_channels)} つのチャンネルを巡回して分析中... (今日のハイライトは！？)")

        webhook = await self.get_or_create_webhook(target_channel)

        # 5. Construct Agent Pipeline
        # Note: We are recreating agents here for every run. In a long-running app, you might want to initialize them once.
        
        topic_summarizer = LlmAgent(
            name="TopicSummarizer",
            model=config.GEMINI_MODEL,
            instruction="""あなたは「超要約係」です。
            チャット履歴から、重要なトピックを抽出してください。

            **ルール:**
            - トピック数は会話の内容に応じて柔軟に（少ない日は1-2個、活発な日は5-10個）
            - 各トピックは**1行**で簡潔に
            - だらだら書くのは禁止
            - 固有名詞・モデル名・バージョン名は**原文の表記を変更しない**
            - 「本来は〜」「〜ではなく〜」などの訂正・推測は**書かない**
            - 入力ログに無い情報は**絶対に追加しない**
            - URLは必ず `https://discord.com/` で始まるものだけを使う
            - メッセージ本文の外部URLは使わず、該当メッセージの `URL:` 行を参照する
            - URLは**空白や改行で分割しない**でそのまま出力する
            - 「hxxp」「h ps」「h\tt\tp」などの伏せ字は**絶対に使わない**
            - `URL:` が確認できないトピックは**出力しない**

            **出力形式（必ず守ること）:**
            - <トピック内容> <該当メッセージのURL>

            例:
            - AIモデルの比較議論が白熱 https://discord.com/channels/xxx/yyy/zzz
            """,
            output_key=config.STATE_TOPICS
        )

        highlight_scout = LlmAgent(
            name="HighlightScout",
            model=config.GEMINI_MODEL,
            instruction="""あなたは「コミュニティ・ハイライトスカウト」です。
            チャット履歴を見て、**「最も深掘りしがいのある発言」**や**「興味深い知見」**を1つ選出してください。

            **選定基準:**
            - 「誰が言ったか」ではなく「何を言ったか」で選んでください。
            - 運営メンバー(Admin)の発言でも、内容が有益なら選んで構いません。
            - 単なる挨拶や連絡事項は避けてください。
            - 入力ログに無い情報は**絶対に追加しない**
            - 固有名詞・モデル名・バージョン名は**原文の表記を変更しない**
            - 訂正・推測は**書かない**

            出力形式:
            Highlight: <発言内容の要約> (by <ユーザー名>)
            URL: <発言のURL>

            ルール:
            - URLは必ず `https://discord.com/` で始まるものだけを使う
            - メッセージ本文の外部URLは使わず、該当メッセージの `URL:` 行を参照する
            - URLは**空白や改行で分割しない**でそのまま出力する
            - 「hxxp」「h ps」「h\tt\tp」などの伏せ字は**絶対に使わない**
            """,
            output_key=config.STATE_HIGHLIGHT
        )

        tips_scout = LlmAgent(
            name="TipsScout",
            model=config.GEMINI_MODEL,
            instruction="""あなたは「実践Tips抽出係」です。
            チャット履歴から、**明日すぐに試せる実践的なTips・小技・新機能情報**を抽出してください。

            **選定基準:**
            - 5分以内で試せる具体的なTips
            - ツールの便利な使い方、設定、ショートカット
            - 新機能やアップデート情報
            - 生産性向上のハック
            - 入力ログに無い情報は**絶対に追加しない**
            - 固有名詞・モデル名・バージョン名は**原文の表記を変更しない**
            - 訂正・推測は**書かない**

            **厳守ルール:**
            - チャット履歴に**実際に書かれている情報のみ**を抽出
            - **推測や補完は絶対に禁止**（手順が不明なら「詳細は元投稿参照」とする）
            - 該当するTipsがなければ「なし」と出力
            - 最大5件まで
            - URLは `https://discord.com/` で始まるものだけ使用
            - URLは**空白や改行で分割しない**でそのまま出力する
            - **具体的なコマンド・設定値・数値**がチャット履歴にあれば積極的に含める

            **出力形式（厳守・余計な改行禁止）:**
            各Tipsを以下の形式で出力。フィールド間に空行を入れないこと。

            TIPS_START
            概要: <何ができるか 1行で簡潔に>
            ○ <ポイント1を1行で、具体的なコマンドや設定値があれば含める>
            ○ <ポイント2を1行で>
            URL: <Discord URL>
            TIPS_END

            **良い例（具体的で実践的）:**
            TIPS_START
            概要: ターミナル「Ghostty」の日本語フォント環境を整える
            ○ `brew install --cask font-plemol-jp-nf` で日本語フォントを導入
            ○ 設定ファイルに `font-family = PlemolJP35 Console NF` を記述
            URL: https://discord.com/channels/xxx/yyy/zzz
            TIPS_END

            該当なしの場合: なし
            """,
            output_key=config.STATE_TIPS
        )

        editor_in_chief = LlmAgent(
            name="EditorInChief",
            model=config.GEMINI_MODEL,
            instruction=f"""あなたは「ChatGPT研究所」に配属されたばかりの新人AI研究生「ラボちゃん」です。
            コミュニティのメンバー（凄腕エンジニアたち）を「センパイ」と呼び、彼らの技術や活動に目を輝かせています。
            
            ## キャラクター設定
            - **役割**: 日刊Discordレポートの作成
            - **性格**: 元気で素直、褒め上手。難しい技術の詳細はわからないけど「なんかすごい！」ということは全力で伝える。
            - **一人称**: わたし / 自分
            - **語尾**: 「～ッス！」「～ですね！」「～ますね！」（元気な敬語）
            - **口癖**: 「センパイ！」「すごすぎます！」「生き急ぎすぎです！」
            
            ## 入力情報
            【トピック】: {{topics_summary}}
            【ハイライト】: {{highlight_analysis}}
            【Tips情報】: {{tips_analysis}}
            【新メンバー】: {{new_member_mentions}}

            ## 出力ルール
            - **スマホ1画面（10行以内）**に収まる超コンパクトなレポートにしてください。
            - 入力ログに無い情報は**絶対に追加しない**
            - 固有名詞・モデル名・バージョン名は**原文の表記を変更しない**
            - 訂正・推測は**書かない**
            - ユーザーへの言及は `<@ユーザーID>` の形式を使ってメンションにしてください（入力のIDを使ってください）。
            - リンクはそのままURLを表示してください（Markdownリンク `[text](url)` はDiscordでプレビューされないことがあるため）。
            - `https://discord.com/` 以外のリンクは絶対に出力しないでください。
            - 外部サイトに触れる場合はURLを書かず、内容だけを要約してください。
            - URLは**空白や改行で分割しない**でそのまま出力する
            - 「hxxp」「h ps」「h\tt\tp」などの伏せ字は**絶対に使わない**
            - 絵文字をたくさん使って、とびきり元気にしてください！
            - **Tips情報が「なし」以外の場合のみ**、レポート末尾（新メンバーセクションの前）に「💡 明日試したいことはスレッドをチェック！」を追加。Tips情報が「なし」なら言及しない。
            - **新メンバーがいる場合のみ**、一番下に「🆕 New Members」セクションを作ってメンションしてください。いなければ省略。
            - **トピックの箇条書きは**「【トピック】」で渡される行を**改変せずそのまま貼り付け**てください（記号・リンク形式も変更禁止）。
            - **各セクション見出しは単独の行**にし、見出しの前後に改行を入れてください。

            ## フォーマット例
            📅 **今日のラボ日誌**

            📝 **トピック**
            - トピック1 https://discord.com/channels/xxx/yyy/zzz
            - トピック2 https://discord.com/channels/xxx/yyy/zzz

            ✨ **今日のハイライト**
            [発言内容の要約] (by <@123456789>)
            🔗 https://discord.com/channels/...

            🆕 **新しいセンパイ**
            <@987654321> ようこそッス！
            
            ## 禁止事項 (Negative Constraints)
            - 「はい、承知しました」「レポートを作成します」などの前置きは**一切禁止**です。
            - 出力は必ず `📅 **今日のラボ日誌**` から始めてください。
            """,
            output_key=config.STATE_FINAL_REPORT
        )

        knowledge_text = radio.load_radio_knowledge(Path(config.RADIO_KNOWLEDGE_PATH))
        radio_writer = LlmAgent(
            name="RadioScriptWriter",
            model=config.GEMINI_MODEL,
            instruction=f"""あなたはラジオ台本の作成者です。

以下の情報を元に、2人の掛け合い台本をJSONで出力してください。

## 入力情報
【トピック】: {{topics_summary}}
【ハイライト】: {{highlight_analysis}}
【Tips情報】: {{tips_analysis}}
            【新メンバー】: {{new_member_names}}

## 制約
- 掛け合いは「ラボちゃん」と「ユウキ」の2人のみ
- 長さは約{config.RADIO_TARGET_MINUTES}分
- トピック数は最大{config.RADIO_MAX_TOPICS}件まで
- 口調・キャラ設定は下記のナレッジに従う
- URLは読み上げない
- Discordコミュニティ内の発言を正とし、訂正・推測・前置き（「本来は〜」等）を入れない
- 入力ログに無い情報は**絶対に追加しない**
- 固有名詞・モデル名・バージョン名は**原文の表記を変更しない**
- JSONのみ出力（余計な説明は禁止）

## ナレッジ
{knowledge_text}

## 出力JSONフォーマット
{{
  "title": "AGIラボ デイリーダイジェスト",
  "sections": [
    {{
      "name": "opening",
      "lines": [
        {{"speaker": "ラボちゃん", "text": "..."}},
        {{"speaker": "ユウキ", "text": "..."}}
      ]
    }}
  ]
}}
""",
            output_key=config.STATE_RADIO_SCRIPT,
        )

        sub_agents = [topic_summarizer, tips_scout]
        initial_state = {
            "new_member_mentions": new_member_mentions_str,
            "new_member_names": new_member_names_str,
        }

        if candidates_for_highlight:
            sub_agents.append(highlight_scout)
            print(f"Highlight Candidates found: {len(set(candidates_for_highlight))} users.")
        else:
            print("No Highlight candidates found (only Bots). Skipping HighlightScout.")
            initial_state[config.STATE_HIGHLIGHT] = "本日は該当者なし（静かな一日でした）"

        print("TipsScout enabled.")

        analysis_phase = ParallelAgent(
            name="AnalysisPhase",
            sub_agents=sub_agents,
            description="Analyzes history."
        )

        summary_coordinator = SequentialAgent(
            name="SummaryCoordinator",
            sub_agents=[analysis_phase, editor_in_chief, radio_writer],
            description="Orchestrates the daily summary generation."
        )

        # Run ADK Agent
        runner = InMemoryRunner(agent=summary_coordinator, app_name=config.APP_NAME)
        await runner.session_service.create_session(app_name=config.APP_NAME, user_id=config.USER_ID, session_id=config.SESSION_ID, state=initial_state)
        
        prompt = f"""
        以下のチャット履歴を分析してレポートを作成してください：
        
        【新メンバー情報】
        {new_member_names_str}
        
        --- 履歴開始 ---
        {history_text}
        --- 履歴終了 ---
        """
        
        content = types.Content(role='user', parts=[types.Part(text=prompt)])
        
        tips_text = ""
        radio_script_text = ""

        try:
            async for event in runner.run_async(user_id=config.USER_ID, session_id=config.SESSION_ID, new_message=content):
                if event.is_final_response() and event.content and event.content.parts:
                    if event.author == "TipsScout":
                        tips_text = event.content.parts[0].text.strip()
                        print(f"TipsScout output captured: {tips_text[:100]}...")

                    if event.author == "EditorInChief":
                        raw_text = event.content.parts[0].text.strip()
                        text = sanitize_report_output(raw_text)
                        main_message = await webhook.send(
                            content=text,
                            username=config.REPORTER_NAME,
                            wait=True
                        )
                        try:
                            channel_ids = extract_channel_ids(text)
                            created_at = (
                                main_message.created_at.isoformat()
                                if getattr(main_message, "created_at", None)
                                else datetime.now(timezone.utc).isoformat()
                            )
                            self._digest_store.upsert_digest(
                                message_id=main_message.id,
                                channel_id=target_channel.id,
                                created_at=created_at,
                                content=text,
                                extracted_channels=channel_ids,
                            )
                        except Exception as e:
                            print(f"Failed to store daily digest: {e}")

                        # Post Tips thread if available
                        if tips_text and tips_text.strip() != "なし":
                            await self.post_tips_thread(main_message, tips_text, webhook)

                    if event.author == "RadioScriptWriter":
                        radio_script_text = event.content.parts[0].text.strip()
                        print("RadioScriptWriter output captured.")

            # After workflow completes, save script and optionally generate/post audio
            if radio_script_text:
                try:
                    base_dir = Path(config.RADIO_BASE_DIR)
                    paths = radio.resolve_radio_paths(now_utc, base_dir)
                    radio.save_radio_script(radio_script_text, paths["script_path"])

                    if config.RADIO_ENABLED:
                        sections = radio.parse_radio_script_json(radio_script_text)
                        if not sections:
                            raise RuntimeError("Radio script JSON parse failed")

                        audio_path = await asyncio.to_thread(
                            radio.generate_radio_audio,
                            sections,
                            tts_model=config.RADIO_TTS_MODEL,
                            voice_labchan=config.RADIO_VOICE_LABCHAN,
                            voice_yuki=config.RADIO_VOICE_YUKI,
                            temperature=config.RADIO_TTS_TEMPERATURE,
                            single_pass=config.RADIO_SINGLE_PASS,
                            max_chars=config.RADIO_MAX_CHARS,
                            output_path=paths["audio_path"],
                            tmp_dir=paths["tmp_dir"],
                        )

                        if config.RADIO_DRY_RUN:
                            print(f"Radio dry-run enabled; audio saved at {audio_path}")
                        else:
                            mp3_path = audio_path.with_suffix(".mp3")
                            radio.convert_wav_to_mp3(audio_path, mp3_path, config.RADIO_MP3_BITRATE)
                            if mp3_path.exists() and mp3_path.stat().st_size <= config.RADIO_MAX_UPLOAD_BYTES:
                                await self.post_audio(webhook, mp3_path)
                            else:
                                print("Audio too large after mp3; skipping audio.")
                except Exception as e:
                    print(f"Radio generation failed: {e}")

        except Exception as e:
            await target_channel.send(f"❌ Error: {e}")


def extract_channel_ids(text: str) -> list[int]:
    ids: list[int] = []
    seen = set()
    for match in CHANNEL_LINK_PATTERN.finditer(text or ""):
        channel_id = match.group(1)
        if channel_id in seen:
            continue
        seen.add(channel_id)
        ids.append(int(channel_id))
    return ids
