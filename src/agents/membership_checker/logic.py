"""MembershipCheckerAgent - note会員のDiscordサーバー参加状況を管理するエージェント"""
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import discord

from src.core.agent_base import BaseAgent

from .checker import (
    assign_roles,
    check_status,
    export_followup,
    find_latest_csv,
    sync_roles,
)
from .config import MembershipCheckerConfig, load_config


class MembershipCheckerAgent(BaseAgent):
    """note会員のDiscordサーバー参加状況を管理するエージェント

    機能:
    - !membership status: 参加状況レポート
    - !membership assign [preview|execute]: ロール付与
    - !membership followup: 未参加者リスト
    - !membership sync [preview|execute]: 退会者同期
    """

    def __init__(self) -> None:
        self.config: Optional[MembershipCheckerConfig] = None
        self._client: Optional[discord.Client] = None

    @property
    def name(self) -> str:
        return "membership_checker"

    async def on_ready(self, client: discord.Client) -> None:
        """初期化処理"""
        self._client = client
        self.config = load_config()

        # データディレクトリ作成
        self.config.data_dir.mkdir(parents=True, exist_ok=True)

        print(f"[{self.name}] Ready - Guild: {self.config.guild_id}")
        if self.config.debug:
            print(f"[{self.name}] Debug mode enabled")
            print(f"[{self.name}] CSV dir: {self.config.csv_dir}")
            print(f"[{self.name}] Log channel: {self.config.log_channel_id}")

    async def on_message(self, message: discord.Message) -> None:
        """メッセージ処理"""
        if not message.content.startswith("!membership"):
            return

        if not self.config or not self._client:
            return

        # 管理者チェック
        if not await self._is_admin(message.author):
            await message.channel.send("⚠️ このコマンドは管理者のみ使用できます。")
            return

        parts = message.content.split()
        if len(parts) < 2:
            await self._send_help(message.channel)
            return

        subcommand = parts[1].lower()
        args = parts[2:] if len(parts) > 2 else []

        guild = self._client.get_guild(self.config.guild_id)
        if not guild:
            await message.channel.send(
                f"❌ Guild {self.config.guild_id} が見つかりません"
            )
            return

        csv_path = find_latest_csv(self.config.csv_dir)
        if not csv_path:
            await message.channel.send(
                f"❌ CSVファイルが見つかりません: {self.config.csv_dir}"
            )
            return

        if subcommand == "status":
            await self._handle_status(message.channel, guild, csv_path)
        elif subcommand == "assign":
            execute = "execute" in args
            confirm_usernames = "confirm-usernames" in args or self.config.confirm_usernames
            await self._handle_assign(
                message.channel, guild, csv_path, execute, confirm_usernames
            )
        elif subcommand == "followup":
            include_no_email = "include-no-email" in args
            await self._handle_followup(
                message.channel, guild, csv_path, include_no_email
            )
        elif subcommand == "sync":
            execute = "execute" in args
            await self._handle_sync(message.channel, guild, csv_path, execute)
        else:
            await self._send_help(message.channel)

    async def run_scheduled_check(self) -> None:
        """定期チェック（GitHub Actions用）"""
        if not self.config or not self._client:
            print(f"[{self.name}] Not initialized")
            return
        config = self.config
        client = self._client

        guild = client.get_guild(config.guild_id)
        if not guild:
            print(f"[{self.name}] Guild {config.guild_id} not found")
            return

        csv_path = find_latest_csv(config.csv_dir)
        if not csv_path:
            print(f"[{self.name}] CSV not found in {config.csv_dir}")
            return

        log_channel: discord.TextChannel | None = None
        if config.log_channel_id:
            ch = client.get_channel(config.log_channel_id)
            if isinstance(ch, discord.TextChannel):
                log_channel = ch

        # ステータスチェック
        result = await check_status(guild, config, csv_path)
        report = self._format_status_report(result)

        if log_channel:
            await log_channel.send(report)

        # ファイル保存
        self._save_report("status", result)

        print(f"[{self.name}] Scheduled check completed")

    async def _is_admin(self, user: discord.Member | discord.User) -> bool:
        """管理者かどうかを確認"""
        if not self.config:
            return False

        if isinstance(user, discord.Member):
            admin_role = user.guild.get_role(self.config.admin_role_id)
            if admin_role and admin_role in user.roles:
                return True

        return False

    async def _send_help(self, channel: discord.abc.Messageable) -> None:
        """ヘルプメッセージを送信"""
        help_text = """```
!membership - AGIラボ会員管理コマンド

サブコマンド:
  status              参加状況レポートを表示
  assign [preview]    ロール付与（デフォルト: preview）
  assign execute      ロール付与を実行
  followup            未参加者リストを出力
  sync [preview]      退会者同期プレビュー
  sync execute        退会者からロール削除を実行

オプション:
  confirm-usernames   ユーザー名マッチングも含める（assignのみ）
  include-no-email    メールなしも含める（followupのみ）
```"""
        await channel.send(help_text)

    async def _handle_status(
        self,
        channel: discord.abc.Messageable,
        guild: discord.Guild,
        csv_path: Path,
    ) -> None:
        """参加状況レポート"""
        assert self.config is not None
        await channel.send(f"📊 会員状況を確認中... (CSV: `{csv_path.name}`)")

        result = await check_status(guild, self.config, csv_path)
        report = self._format_status_report(result)

        await channel.send(report)
        self._save_report("status", result)

    async def _handle_assign(
        self,
        channel: discord.abc.Messageable,
        guild: discord.Guild,
        csv_path: Path,
        execute: bool,
        confirm_usernames: bool,
    ) -> None:
        """ロール付与"""
        assert self.config is not None
        mode = "実行" if execute else "プレビュー"
        await channel.send(f"🔧 ロール付与 ({mode}) を実行中...")

        result = await assign_roles(
            guild, self.config, csv_path, execute, confirm_usernames
        )
        report = self._format_assign_report(result)

        await channel.send(report)
        self._save_report("assign", result)

    async def _handle_followup(
        self,
        channel: discord.abc.Messageable,
        guild: discord.Guild,
        csv_path: Path,
        include_no_email: bool,
    ) -> None:
        """未参加者リスト"""
        assert self.config is not None
        await channel.send("📋 未参加者リストを生成中...")

        result = await export_followup(guild, self.config, csv_path, include_no_email)
        report = self._format_followup_report(result)

        await channel.send(report)

        # CSV出力 & Discord添付
        if result["followup_list"]:
            csv_file = self._export_followup_csv(result)
            # ファイルをDiscordに添付して送信
            await channel.send(
                "📎 フォローアップリスト:",
                file=discord.File(csv_file, filename=csv_file.name),
            )

        self._save_report("followup", result)

    async def _handle_sync(
        self,
        channel: discord.abc.Messageable,
        guild: discord.Guild,
        csv_path: Path,
        execute: bool,
    ) -> None:
        """退会者同期"""
        assert self.config is not None
        mode = "実行" if execute else "プレビュー"
        await channel.send(f"🔄 退会者同期 ({mode}) を実行中...")

        result = await sync_roles(guild, self.config, csv_path, execute)
        report = self._format_sync_report(result)

        await channel.send(report)
        self._save_report("sync", result)

    def _format_status_report(self, result: dict) -> str:
        """ステータスレポートをフォーマット"""
        stats = result["statistics"]
        server = result["server"]
        members = result["members"]

        in_with = len(members["in_server_with_role"])
        in_without = len(members["in_server_without_role"])
        not_in = len(members["not_in_server"])
        un_in = len(members["username_in_server"])
        un_not = len(members["username_not_in_server"])

        report = f"""```
📊 AGIラボ Discord会員状況レポート
生成: {result['timestamp'][:19]}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 Discord紐付け統計:
  総数: {stats['total_discord_linked']}名
  ├ 有効ID: {stats['valid_ids']}名 (アクティブ: {stats['valid_ids_active']}名)
  └ ユーザー名のみ: {stats['username_only']}名 (アクティブ: {stats['username_only_active']}名)

🔗 サーバー: {server['name']}
  総メンバー: {server['member_count']}名
  ロール所持: {server['role_members']}名

━━ 有効ID会員 ━━
✅ サーバー参加: {in_with + in_without}名
   └ ロールあり: {in_with}名 / ロールなし: {in_without}名
❌ 未参加: {not_in}名

━━ ユーザー名会員 ━━
✅ サーバー参加: {un_in}名
❌ 未参加: {un_not}名
```"""

        if in_without > 0:
            report += f"\n⚠️ ロール未付与者: {in_without}名 → `!membership assign execute` で付与"

        return report

    def _format_assign_report(self, result: dict) -> str:
        """ロール付与レポートをフォーマット"""
        mode = "プレビュー" if result["preview"] else "実行結果"
        id_count = len(result["to_assign_id"])
        un_count = len(result["to_assign_username"])

        report = f"""```
🔧 ロール付与 ({mode})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
対象 (有効ID): {id_count}名
対象 (ユーザー名): {un_count}名
```"""

        if not result["preview"]:
            report += f"""```
✅ 付与成功: {len(result['assigned'])}名
❌ 付与失敗: {len(result['failed'])}名
```"""

        if result["to_assign_id"][:5]:
            report += "\n**対象者 (ID):**\n"
            for m in result["to_assign_id"][:5]:
                report += f"• {m['note_name']} → {m['discord_name']}\n"
            if id_count > 5:
                report += f"... 他 {id_count - 5}名\n"

        if result["preview"] and (id_count + un_count) > 0:
            report += "\n💡 実行: `!membership assign execute`"

        return report

    def _format_followup_report(self, result: dict) -> str:
        """フォローアップレポートをフォーマット"""
        count = len(result["followup_list"])
        with_email = len([m for m in result["followup_list"] if m["email"]])

        report = f"""```
📋 未参加者フォローアップリスト
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
対象者: {count}名
  └ メールあり: {with_email}名
```"""

        if result["followup_list"][:5]:
            report += "\n**未参加者:**\n"
            for m in result["followup_list"][:5]:
                email = f" | {m['email']}" if m["email"] else ""
                report += f"• {m['note_name']} ({m['plan']}){email}\n"
            if count > 5:
                report += f"... 他 {count - 5}名\n"

        return report

    def _format_sync_report(self, result: dict) -> str:
        """退会者同期レポートをフォーマット"""
        mode = "プレビュー" if result["preview"] else "実行結果"
        remove_count = len(result["to_remove"])

        report = f"""```
🔄 退会者同期 ({mode})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
noteアクティブ会員: {result['note_members']}名
Discordロール保持者: {result['discord_role_members']}名
  ├ 維持: {result['to_keep']}名
  └ 削除対象: {remove_count}名
```"""

        if not result["preview"]:
            report += f"""```
✅ 削除成功: {len(result['removed'])}名
❌ 削除失敗: {len(result['failed'])}名
```"""

        if result["to_remove"][:5]:
            report += "\n**削除対象:**\n"
            for m in result["to_remove"][:5]:
                report += f"• {m['discord_name']} ({m['username']})\n"
            if remove_count > 5:
                report += f"... 他 {remove_count - 5}名\n"

        if result["preview"] and remove_count > 0:
            report += "\n⚠️ 実行: `!membership sync execute` (元に戻せません)"

        return report

    def _save_report(self, report_type: str, result: dict) -> Path:
        """レポートをJSONで保存"""
        assert self.config is not None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{report_type}_{timestamp}.json"
        filepath = self.config.data_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        return filepath

    def _export_followup_csv(self, result: dict) -> Path:
        """フォローアップリストをCSVで出力"""
        assert self.config is not None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"followup_{timestamp}.csv"
        filepath = self.config.data_dir / filename

        with open(filepath, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "note_name",
                    "note_id",
                    "plan",
                    "email",
                    "discord_value",
                    "match_type",
                ],
            )
            writer.writeheader()
            writer.writerows(result["followup_list"])

        return filepath
