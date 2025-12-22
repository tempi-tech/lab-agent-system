import asyncio
import os
import re
import discord
from datetime import datetime, timedelta, timezone
from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from . import config
from src.core import config as core_config

from src.core.agent_base import BaseAgent

DISCORD_URL_PREFIX = "https://discord.com/"
URL_PATTERN = re.compile(r"https?://\S+")
EXTERNAL_LINK_PLACEHOLDER = "[外部リンク]"



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
    lines = remove_empty_hidden_links_section(lines)
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
                if next_line.startswith("✨") or next_line.startswith("🔗") or next_line.startswith("🆕") or next_line.startswith("📅") or next_line.startswith("📝"):
                    result.append(lines[i])
                    break
                if next_line and "[参考](" not in next_line:
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
        "🔗 **隠れたお宝リンク**",
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


def remove_empty_hidden_links_section(lines: list[str]) -> list[str]:
    header = "🔗 **隠れたお宝リンク**"
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == header:
            j = i + 1
            has_discord_link = False
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line.startswith("🆕") or next_line.startswith("📅") or next_line.startswith("📝") or next_line.startswith("✨") or next_line.startswith("🔗"):
                    break
                if DISCORD_URL_PREFIX in next_line:
                    has_discord_link = True
                j += 1

            if not has_discord_link:
                i = j
                continue
            result.append(line)
            k = i + 1
            while k < j:
                if re.match(r"^\s*-?\s*なし", lines[k]):
                    k += 1
                    continue
                result.append(lines[k])
                k += 1
            i = j
            continue
        result.append(line)
        i += 1
    return result


def sanitize_message_content(text: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        trimmed = url.rstrip(").,!?、。）」】]")
        if trimmed.startswith(DISCORD_URL_PREFIX):
            return trimmed
        return EXTERNAL_LINK_PLACEHOLDER

    return URL_PATTERN.sub(replace_url, text)


class DailyReporterAgent(BaseAgent):
    def __init__(self):
        self.client = None # Will be set in on_ready
        self.action_namespace = "daily_reporter"

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

    async def generate_summary(self, target_channel):
        # 1. Calculate Time Threshold (24 hours ago in JST)
        now_utc = datetime.now(timezone.utc)
        threshold = now_utc - timedelta(hours=24)
        print(f"Fetching messages since: {threshold} (UTC)")

        # 2. Fetch Messages from All Channels
        all_messages = []
        candidates_for_mvp = []
        candidates_for_highlight = []

        for channel_id in config.SOURCE_CHANNELS:
            channel = self.client.get_channel(channel_id)
            if not channel:
                print(f"Warning: Could not find channel {channel_id}")
                continue
            
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
            
            author_display = f"{msg.author.name}"
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
        new_members_list = []
        if config.SOURCE_CHANNELS:
            guild = self.client.get_channel(config.SOURCE_CHANNELS[0]).guild
            if guild:
                print(f"Checking for new members in guild: {guild.name}")
                for member in guild.members:
                    if member.joined_at and member.joined_at > threshold:
                        if not member.bot:
                            new_members_list.append(f"<@{member.id}>")
        else:
            print("Warning: No source channels configured. Skipping new member detection.")
        
        new_members_str = " ".join(new_members_list) if new_members_list else "なし"
        print(f"New members found: {new_members_str}")

        if not formatted_messages and not new_members_list:
            await target_channel.send("今日は静かな一日でしたね。（メッセージも新メンバーもなし）")
            return

        history_text = "\n---\n".join(formatted_messages)
        
        print("Analyzing...")
        await target_channel.send(f"🕵️‍♀️ ラボちゃんが {len(config.SOURCE_CHANNELS)} つのチャンネルを巡回して分析中... (今日のハイライトは！？)")

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
            - URLは必ず `https://discord.com/` で始まるものだけを使う
            - メッセージ本文の外部URLは使わず、該当メッセージの `URL:` 行を参照する
            - URLは**空白や改行で分割しない**でそのまま出力する
            - 「hxxp」「h ps」「h\tt\tp」などの伏せ字は**絶対に使わない**
            - `URL:` が確認できないトピックは**出力しない**

            **出力形式（必ず守ること）:**
            - <トピック内容> [参考](<該当メッセージのURL>)

            例:
            - AIモデルの比較議論が白熱 [参考](https://discord.com/channels/xxx/yyy/zzz)
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

        link_curator = LlmAgent(
            name="LinkCurator",
            model=config.GEMINI_MODEL,
            instruction="""あなたは「リンク選別係」です。
            チャット履歴に含まれるURLの中から、**「みんなが見逃しそうな隠れたお宝情報」**や**「議論の裏付けとなる重要なドキュメント」**を最大3つ選んでください。
            
            単なる宣伝や既知の有名サイトは避けてください。
            - `https://discord.com/` で始まるURLのみを選んでください。
            - 外部リンクそのものは選ばず、該当メッセージの `URL:` 行（Discord内の元投稿）を使ってください。
            - URLは**空白や改行で分割しない**でそのまま出力する
            - 該当するリンクがなければ「なし」としてください。
            - 「hxxp」「h ps」「h\tt\tp」などの伏せ字は**絶対に使わない**
            
            出力形式:
            - <URL> (理由: <一言コメント>)
            なければ「なし」としてください。
            """,
            output_key=config.STATE_LINKS
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
            【リンク】: {{link_summary}}
            【新メンバー】: {{new_members}}
            
            ## 出力ルール
            - **スマホ1画面（10行以内）**に収まる超コンパクトなレポートにしてください。
            - ユーザーへの言及は `<@ユーザーID>` の形式を使ってメンションにしてください（入力のIDを使ってください）。
            - リンクはそのままURLを表示してください（Markdownリンク `[text](url)` はDiscordでプレビューされないことがあるため）。
            - **リンクがある場合は必ず「理由」も併記してください。**
            - `https://discord.com/` 以外のリンクは絶対に出力しないでください。
            - 外部サイトに触れる場合はURLを書かず、内容だけを要約してください。
            - URLは**空白や改行で分割しない**でそのまま出力する
            - 「hxxp」「h ps」「h\tt\tp」などの伏せ字は**絶対に使わない**
            - 絵文字をたくさん使って、とびきり元気にしてください！
            - **隠れたお宝リンクが無い場合**、そのセクションは**丸ごと省略**してください（「なし」と書かない）。
            - **新メンバーがいる場合のみ**、一番下に「🆕 New Members」セクションを作ってメンションしてください。いなければ省略。
            - **トピックの箇条書きは**「【トピック】」で渡される行を**改変せずそのまま貼り付け**てください（記号・リンク形式も変更禁止）。
            - **各セクション見出しは単独の行**にし、見出しの前後に改行を入れてください。
            
            ## フォーマット例
            📅 **今日のラボ日誌**

            📝 **トピック**
            - [トピック1] [参考](URL)
            - [トピック2] [参考](URL)

            ✨ **今日のハイライト**
            [発言内容の要約] (by <@123456789>)
            🔗 [元発言](https://discord.com/channels/...)
            
            🔗 **隠れたお宝リンク**
            - https://discord.com/channels/... (理由: 〇〇)
            
            🆕 **新しいセンパイ**
            <@987654321> ようこそッス！
            
            ## 禁止事項 (Negative Constraints)
            - 「はい、承知しました」「レポートを作成します」などの前置きは**一切禁止**です。
            - 出力は必ず `📅 **今日のラボ日誌**` から始めてください。
            """,
            output_key=config.STATE_FINAL_REPORT
        )

        sub_agents = [topic_summarizer, link_curator]
        initial_state = {"new_members": new_members_str}
        
        if candidates_for_highlight:
            sub_agents.append(highlight_scout)
            print(f"Highlight Candidates found: {len(set(candidates_for_highlight))} users.")
        else:
            print("No Highlight candidates found (only Bots). Skipping HighlightScout.")
            initial_state[config.STATE_HIGHLIGHT] = "本日は該当者なし（静かな一日でした）"

        analysis_phase = ParallelAgent(
            name="AnalysisPhase",
            sub_agents=sub_agents,
            description="Analyzes history."
        )

        summary_coordinator = SequentialAgent(
            name="SummaryCoordinator",
            sub_agents=[analysis_phase, editor_in_chief],
            description="Orchestrates the daily summary generation."
        )

        # Run ADK Agent
        runner = InMemoryRunner(agent=summary_coordinator, app_name=config.APP_NAME)
        await runner.session_service.create_session(app_name=config.APP_NAME, user_id=config.USER_ID, session_id=config.SESSION_ID, state=initial_state)
        
        prompt = f"""
        以下のチャット履歴を分析してレポートを作成してください：
        
        【新メンバー情報】
        {new_members_str}
        
        --- 履歴開始 ---
        {history_text}
        --- 履歴終了 ---
        """
        
        content = types.Content(role='user', parts=[types.Part(text=prompt)])
        
        try:
            async for event in runner.run_async(user_id=config.USER_ID, session_id=config.SESSION_ID, new_message=content):
                if event.is_final_response() and event.content and event.content.parts:
                    if event.author == "EditorInChief":
                        raw_text = event.content.parts[0].text.strip()
                        text = sanitize_report_output(raw_text)
                        await webhook.send(
                            content=text,
                            username=config.REPORTER_NAME
                        )
                    
        except Exception as e:
            await target_channel.send(f"❌ Error: {e}")
