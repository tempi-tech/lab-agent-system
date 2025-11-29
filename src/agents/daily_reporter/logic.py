import asyncio
import os
import discord
from datetime import datetime, timedelta, timezone
from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from . import config
from src.core import config as core_config

from src.core.agent_base import BaseAgent

class DailyReporterAgent(BaseAgent):
    def __init__(self):
        self.client = None # Will be set in on_ready

    @property
    def name(self) -> str:
        return "DailyReporterAgent"


    async def on_ready(self, client: discord.Client):
        self.client = client
        print("DailyReporterAgent is ready.")
        
        # Target is the test channel (from env)
        target_channel_id = core_config.TARGET_CHANNEL_ID
        target_channel = self.client.get_channel(int(target_channel_id)) if target_channel_id else None
        
        if target_channel:
            # In a real scenario, you might want to schedule this instead of running immediately on startup
            # For now, we keep the original behavior of running on startup
            await self.generate_summary(target_channel)
        else:
            print(f"DailyReporterAgent Error: Could not find target channel {target_channel_id}")

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

            formatted_messages.append(f"User: {author_display} (ID: {msg.author.id})\nLocation: {location}\nURL: {msg.jump_url}\nMessage: {msg.content}")

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
            """,
            output_key=config.STATE_HIGHLIGHT
        )

        link_curator = LlmAgent(
            name="LinkCurator",
            model=config.GEMINI_MODEL,
            instruction="""あなたは「リンク選別係」です。
            チャット履歴に含まれるURLの中から、**「みんなが見逃しそうな隠れたお宝情報」**や**「議論の裏付けとなる重要なドキュメント」**を最大3つ選んでください。
            
            単なる宣伝や既知の有名サイトは避けてください。
            
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
            - **リンクには必ず「理由」も併記してください。**
            - 絵文字をたくさん使って、とびきり元気にしてください！
            - **新メンバーがいる場合のみ**、一番下に「🆕 New Members」セクションを作ってメンションしてください。いなければ省略。
            - 最後に「ラボちゃんより」と添えてください。
            
            ## フォーマット例
            📅 **今日のラボ日誌**

            📝 **トピック**
            - [トピック1] [参考](URL)
            - [トピック2] [参考](URL)

            ✨ **今日のハイライト**
            [発言内容の要約] (by <@123456789>)
            🔗 [元発言](https://discord.com/channels/...)
            
            🔗 **隠れたお宝リンク**
            - https://example.com (理由: 〇〇)
            
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
                        text = event.content.parts[0].text.strip()
                        await webhook.send(
                            content=text,
                            username=config.REPORTER_NAME
                        )
                    
        except Exception as e:
            await target_channel.send(f"❌ Error: {e}")
