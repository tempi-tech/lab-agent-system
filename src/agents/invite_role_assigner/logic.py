import asyncio
import csv
import re
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Set

import discord

from src.core.agent_base import BaseAgent
from .config import load_config, InviteRoleAssignerConfig
from .storage import MemberLogStore, MemberJoinRecord


class InviteRoleAssignerAgent(BaseAgent):
    """
    招待リンク（invite）の uses 差分から「どの招待で入ったか」を推測してロール付与するエージェント。

    仕様:
    - 招待枠URL  -> agi-lab + ログ「招待枠で参加」
    - 一般枠URL  -> agi-lab + ログ「一般枠で参加」
    - 検出失敗   -> agi-lab + 要確認 + ログ「検出失敗」
    """

    def __init__(self) -> None:
        self.cfg: InviteRoleAssignerConfig = load_config()
        self._client: Optional[discord.Client] = None
        self._guild: Optional[discord.Guild] = None

        self._invite_uses: Dict[str, int] = {}
        self._lock = asyncio.Lock()

        # 永続ログストレージ
        self._member_log = MemberLogStore(self.cfg.data_dir / "member_log.csv")

    @property
    def name(self) -> str:
        return "InviteRoleAssignerAgent"

    async def on_ready(self, client: discord.Client) -> None:
        self._client = client
        guild = client.get_guild(self.cfg.guild_id)
        if guild is None:
            try:
                guild = await client.fetch_guild(self.cfg.guild_id)
            except Exception:
                guild = None

        if guild is None:
            print(f"[{self.name}] ERROR: guild not found in cache/fetch. guild_id={self.cfg.guild_id}")
            return

        self._guild = guild

        # ベースライン（現在の招待 uses）を取得
        await self._refresh_invites_baseline(reason="startup")

        phase_count = len(self.cfg.phases)
        code_count = len(self.cfg.invited_codes)
        await self._log(f"✅ {self.name} ready. phases={phase_count}, codes={code_count}")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if not message.guild or message.guild.id != self.cfg.guild_id:
            return

        content = message.content.strip()

        # 既存コマンド
        if content == "!invrole generate":
            await self._cmd_generate_expert_invites(message)
            return

        if content == "!invrole delete_all":
            await self._cmd_delete_all_invites(message)
            return

        if content == "!invrole test":
            await self._cmd_generate_test_invite(message)
            return

        if content == "!invrole status":
            await self._cmd_status(message)
            return

        # 新コマンド
        if content == "!invrole phases":
            await self._cmd_phases(message)
            return

        if content.startswith("!invrole sync"):
            await self._handle_sync_command(message, content)
            return

        if content == "!invrole help":
            await message.channel.send(
                "**InviteRoleAssigner Commands**\n"
                "• `!invrole status` - システム状態を一括チェック\n"
                "• `!invrole phases` - フェーズ一覧と統計を表示\n"
                "• `!invrole generate` - 招待枠・一般枠の招待URL各1本を生成\n"
                "• `!invrole test` - テスト用招待リンク1本を生成\n"
                "• `!invrole delete_all` - Bot作成の招待リンクを全削除\n"
                "• `!invrole sync preview <csv_path>` - noteメンバーCSVと照合（プレビュー）\n"
                "• `!invrole sync execute <csv_path>` - ロール削除を実行\n"
                "• `!invrole help` - このヘルプを表示"
            )
            return

    # ========== 新コマンド: phases ==========

    async def _cmd_phases(self, message: discord.Message) -> None:
        """フェーズ一覧と統計を表示"""
        if not self.cfg.phases:
            await message.channel.send("⚠️ フェーズが設定されていません。")
            return

        # フェーズごとの参加人数を集計
        counts = self._member_log.count_by_phase()
        total = sum(counts.values())

        lines = ["**📊 フェーズ一覧**\n"]
        for phase_name, phase in self.cfg.phases.items():
            status = "✅" if phase.active else "⏸️"
            count = counts.get(phase_name, 0)
            code_count = len(phase.invite_codes)
            lines.append(
                f"{status} **{phase_name}**: {count}人参加 ({code_count} codes)\n"
                f"   {phase.description}"
            )

        lines.append(f"\n**合計**: {total}人")
        await message.channel.send("\n".join(lines))

    # ========== 新コマンド: sync ==========

    async def _handle_sync_command(self, message: discord.Message, content: str) -> None:
        """syncコマンドのハンドリング"""
        # 権限チェック
        member = message.author
        if not isinstance(member, discord.Member):
            return

        admin_role = message.guild.get_role(self.cfg.admin_role_id)
        if admin_role is None or admin_role not in member.roles:
            await message.channel.send("❌ このコマンドは管理者のみ実行できます。")
            return

        # コマンドパース: !invrole sync <subcommand> <csv_path>
        parts = content.split(maxsplit=3)
        if len(parts) < 4:
            await message.channel.send(
                "使い方: `!invrole sync preview <csv_path>` または `!invrole sync execute <csv_path>`"
            )
            return

        subcommand = parts[2]
        csv_path = parts[3]

        if subcommand == "preview":
            await self._cmd_sync_preview(message, csv_path)
        elif subcommand == "execute":
            await self._cmd_sync_execute(message, csv_path)
        else:
            await message.channel.send(
                f"❌ 不明なサブコマンド: `{subcommand}`\n"
                "使い方: `!invrole sync preview <csv_path>` または `!invrole sync execute <csv_path>`"
            )

    async def _cmd_sync_preview(self, message: discord.Message, csv_path: str) -> None:
        """noteメンバーCSVと照合してプレビュー"""
        await message.channel.send(f"🔄 CSVを読み込み中: `{csv_path}`")

        # CSV読み込み
        try:
            note_discord_ids, warnings = self._parse_note_csv(csv_path)
        except FileNotFoundError:
            await message.channel.send(f"❌ ファイルが見つかりません: `{csv_path}`")
            return
        except Exception as e:
            await message.channel.send(f"❌ CSV読み込みエラー: {type(e).__name__}: {e}")
            return

        # Discordサーバーのメンバーを取得
        guild = self._guild
        if guild is None:
            await message.channel.send("❌ Guildが見つかりません。")
            return

        general_role = guild.get_role(self.cfg.general_role_id)
        if general_role is None:
            await message.channel.send(f"❌ agi-labロールが見つかりません (ID: {self.cfg.general_role_id})")
            return

        # ロールを持っているメンバーを取得
        members_with_role = [m for m in guild.members if general_role in m.roles]
        discord_ids_with_role = {m.id for m in members_with_role}

        # 差分計算
        to_remove = discord_ids_with_role - note_discord_ids
        to_keep = discord_ids_with_role & note_discord_ids
        in_note_not_discord = note_discord_ids - discord_ids_with_role

        # 結果表示
        lines = [
            "**📊 Sync Preview**\n",
            f"noteメンバー（Discord ID有効）: {len(note_discord_ids)}人",
            f"Discordメンバー（{general_role.name}ロール保持）: {len(discord_ids_with_role)}人\n",
        ]

        if warnings:
            lines.append(f"⚠️ **警告** ({len(warnings)}件):")
            for w in warnings[:5]:
                lines.append(f"   • {w}")
            if len(warnings) > 5:
                lines.append(f"   ... 他 {len(warnings) - 5}件")
            lines.append("")

        if to_remove:
            lines.append(f"⚠️ **ロール削除対象**: {len(to_remove)}人")
            remove_list = []
            for uid in list(to_remove)[:10]:
                m = guild.get_member(uid)
                if m:
                    record = self._member_log.get_by_discord_id(uid)
                    phase_info = f", phase: {record.phase}" if record and record.phase else ""
                    joined_info = f" (入室: {record.timestamp[:10]}{phase_info})" if record else ""
                    remove_list.append(f"   • {m.display_name} (`{uid}`){joined_info}")
                else:
                    remove_list.append(f"   • (不明) `{uid}`")
            lines.extend(remove_list)
            if len(to_remove) > 10:
                lines.append(f"   ... 他 {len(to_remove) - 10}人")
        else:
            lines.append("✅ ロール削除対象: なし")

        lines.append(f"\n✅ ロール維持: {len(to_keep)}人")

        if in_note_not_discord:
            lines.append(f"❓ noteにいるがDiscordにいない: {len(in_note_not_discord)}人")

        lines.append(f"\n実行: `!invrole sync execute {csv_path}`")

        await message.channel.send("\n".join(lines))

    async def _cmd_sync_execute(self, message: discord.Message, csv_path: str) -> None:
        """ロール削除を実行"""
        await message.channel.send(f"🔄 CSVを読み込み中: `{csv_path}`")

        # CSV読み込み
        try:
            note_discord_ids, _ = self._parse_note_csv(csv_path)
        except FileNotFoundError:
            await message.channel.send(f"❌ ファイルが見つかりません: `{csv_path}`")
            return
        except Exception as e:
            await message.channel.send(f"❌ CSV読み込みエラー: {type(e).__name__}: {e}")
            return

        guild = self._guild
        if guild is None:
            await message.channel.send("❌ Guildが見つかりません。")
            return

        general_role = guild.get_role(self.cfg.general_role_id)
        if general_role is None:
            await message.channel.send(f"❌ agi-labロールが見つかりません")
            return

        # ロールを持っているメンバーから削除対象を抽出
        members_with_role = [m for m in guild.members if general_role in m.roles]
        to_remove_members = [m for m in members_with_role if m.id not in note_discord_ids]

        if not to_remove_members:
            await message.channel.send("✅ 削除対象がありません。")
            return

        await message.channel.send(
            f"⚠️ **{len(to_remove_members)}人** からロールを削除します。\n"
            f"続行しますか？ 60秒以内に `yes` と返信してください。"
        )

        # 確認待ち
        def check(m: discord.Message) -> bool:
            return (
                m.author.id == message.author.id
                and m.channel.id == message.channel.id
                and m.content.lower() == "yes"
            )

        try:
            await self._client.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            await message.channel.send("⏰ タイムアウト。キャンセルしました。")
            return

        # 実行
        await message.channel.send(f"🔄 ロール削除を開始します...")

        success = 0
        failed = 0

        for i, member in enumerate(to_remove_members):
            try:
                await member.remove_roles(general_role, reason="Sync: not in note.com members CSV")
                success += 1
                await self._log(f"🔄 Sync: {member.display_name} (`{member.id}`) からロール削除")
            except Exception as e:
                failed += 1
                await self._log(f"❌ Sync失敗: {member.display_name} - {type(e).__name__}: {e}")

            # 進捗報告（10人ごと）
            if (i + 1) % 10 == 0:
                await message.channel.send(f"進捗: {i + 1}/{len(to_remove_members)}")

            # レート制限対策
            await asyncio.sleep(0.5)

        result = f"✅ 完了: {success}人からロール削除"
        if failed > 0:
            result += f" (失敗: {failed}人)"
        await message.channel.send(result)
        await self._log(f"📊 Sync完了: {success}人削除, {failed}人失敗 by {message.author}")

    def _parse_note_csv(self, csv_path: str) -> Tuple[Set[int], List[str]]:
        """
        noteのCSVを読み込み、Discord IDのセットを返す。

        Returns:
            (discord_ids, warnings): Discord IDのセットと警告メッセージのリスト
        """
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {csv_path}")

        discord_ids: Set[int] = set()
        warnings: List[str] = []

        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                discord_value = row.get("Discord", "").strip()
                if not discord_value:
                    continue

                # Discord IDを抽出（18桁の数値）
                # 様々な形式に対応: "123456789", "user#1234", "@username" など
                match = re.search(r"\b(\d{17,20})\b", discord_value)
                if match:
                    discord_ids.add(int(match.group(1)))
                else:
                    # 数値でない場合は警告
                    name = row.get("名前", row.get("noteID", "unknown"))
                    warnings.append(f"{name}: '{discord_value}' は有効なDiscord IDではありません")

        return discord_ids, warnings

    # ========== 既存コマンド ==========

    async def _cmd_generate_expert_invites(self, message: discord.Message) -> None:
        """招待枠・一般枠の招待URL各1本を生成し、ログチャンネルに出力"""
        member = message.author
        if not isinstance(member, discord.Member):
            return

        admin_role = message.guild.get_role(self.cfg.admin_role_id)
        if admin_role is None or admin_role not in member.roles:
            await message.channel.send("❌ このコマンドは管理者のみ実行できます。")
            return

        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            await message.channel.send("❌ テキストチャンネルで実行してください。")
            return

        await message.channel.send("🔄 招待URL（招待枠・一般枠）を生成中（無期限・無制限）...")

        try:
            invited_invite = await channel.create_invite(
                max_age=0,
                max_uses=0,
                unique=True,
                reason="招待枠 invite generated by InviteRoleAssigner"
            )
            general_invite = await channel.create_invite(
                max_age=0,
                max_uses=0,
                unique=True,
                reason="一般枠 invite generated by InviteRoleAssigner"
            )
        except discord.Forbidden:
            await message.channel.send(
                "❌ 招待リンクの作成に失敗しました（Forbidden）。Botに **Create Invite** 権限が必要です。"
            )
            return
        except Exception as e:
            await message.channel.send(f"❌ 招待リンクの作成に失敗: {type(e).__name__}: {e}")
            return

        result_msg = (
            f"✅ **招待URLを生成しました** (無期限・無制限)\n\n"
            f"**招待枠URL** (QRコード用):\n{invited_invite.url}\n"
            f"コード: `{invited_invite.code}`\n\n"
            f"**一般枠URL** (QRコード用):\n{general_invite.url}\n"
            f"コード: `{general_invite.code}`\n\n"
            f"**JSON設定に追加:**\n```json\n\"新フェーズ\": {{\n  \"invite_codes\": [\"{invited_invite.code}\"]\n}}\n```"
        )

        await self._log(result_msg)
        await self._refresh_invites_baseline(reason="after_generate")
        await message.channel.send("✅ 完了！ログチャンネルに出力しました。")

    async def _cmd_generate_test_invite(self, message: discord.Message) -> None:
        """テスト用招待リンク1本を生成"""
        member = message.author
        if not isinstance(member, discord.Member):
            return

        admin_role = message.guild.get_role(self.cfg.admin_role_id)
        if admin_role is None or admin_role not in member.roles:
            await message.channel.send("❌ このコマンドは管理者のみ実行できます。")
            return

        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            await message.channel.send("❌ テキストチャンネルで実行してください。")
            return

        try:
            invite = await channel.create_invite(
                max_age=0,
                max_uses=0,
                unique=True,
                reason="Test invite generated by InviteRoleAssigner"
            )
            await message.channel.send(
                f"✅ テスト用招待リンク:\n{invite.url}\n"
                f"コード: `{invite.code}`"
            )
            await self._log(f"🧪 テスト招待生成: `{invite.code}` by {member}")
            await self._refresh_invites_baseline(reason="after_test_generate")
        except discord.Forbidden:
            await message.channel.send("❌ 招待リンクの作成に失敗しました（Forbidden）。")
        except Exception as e:
            await message.channel.send(f"❌ 招待リンクの作成に失敗: {type(e).__name__}: {e}")

    async def _cmd_delete_all_invites(self, message: discord.Message) -> None:
        """Bot が作成した招待リンクを全削除"""
        member = message.author
        if not isinstance(member, discord.Member):
            return

        admin_role = message.guild.get_role(self.cfg.admin_role_id)
        if admin_role is None or admin_role not in member.roles:
            await message.channel.send("❌ このコマンドは管理者のみ実行できます。")
            return

        guild = self._guild
        if guild is None:
            await message.channel.send("❌ Guildが見つかりません。")
            return

        await message.channel.send("🔄 招待リンクを削除中...")

        try:
            invites = await guild.invites()
        except discord.Forbidden:
            await message.channel.send("❌ 招待一覧の取得に失敗しました（Forbidden）。")
            return
        except Exception as e:
            await message.channel.send(f"❌ 招待一覧の取得に失敗: {type(e).__name__}: {e}")
            return

        bot_user = self._client.user
        deleted_count = 0
        failed_count = 0

        for invite in invites:
            if invite.inviter and invite.inviter.id == bot_user.id:
                try:
                    await invite.delete(reason="Deleted by !invrole delete_all command")
                    deleted_count += 1
                except Exception:
                    failed_count += 1

        await self._refresh_invites_baseline(reason="after_delete_all")

        result = f"✅ 削除完了: {deleted_count}件"
        if failed_count > 0:
            result += f" (失敗: {failed_count}件)"
        await message.channel.send(result)
        await self._log(f"🗑️ 招待リンク削除: {deleted_count}件削除 by {member}")

    async def _cmd_status(self, message: discord.Message) -> None:
        """システム状態を一括チェック"""
        guild = message.guild
        results: List[str] = []
        all_ok = True

        # 1. Bot接続
        if self._client and self._client.is_ready():
            results.append("✅ Bot接続: OK")
        else:
            results.append("❌ Bot接続: NG")
            all_ok = False

        # 2. Guild
        if self._guild:
            results.append(f"✅ Guild: {self._guild.name}")
        else:
            results.append("❌ Guild: 未取得")
            all_ok = False

        # 3. agi-lab ロール
        general_role = guild.get_role(self.cfg.general_role_id)
        if general_role:
            results.append(f"✅ agi-lab ロール: {general_role.name}")
        else:
            results.append(f"❌ agi-lab ロール: 見つからない (ID: {self.cfg.general_role_id})")
            all_ok = False

        # 4. 要確認ロール
        review_role = guild.get_role(self.cfg.review_role_id)
        if review_role:
            results.append(f"✅ 要確認ロール: {review_role.name}")
        else:
            results.append(f"❌ 要確認ロール: 見つからない (ID: {self.cfg.review_role_id})")
            all_ok = False

        # 5. 管理者ロール
        admin_role = guild.get_role(self.cfg.admin_role_id)
        if admin_role:
            results.append(f"✅ 管理者ロール: {admin_role.name}")
        else:
            results.append(f"❌ 管理者ロール: 見つからない (ID: {self.cfg.admin_role_id})")
            all_ok = False

        # 6. ログチャンネル
        log_ch = self._client.get_channel(self.cfg.log_channel_id)
        if log_ch:
            results.append(f"✅ ログチャンネル: #{log_ch.name}")
        else:
            results.append(f"❌ ログチャンネル: 見つからない (ID: {self.cfg.log_channel_id})")
            all_ok = False

        # 7. フェーズ設定
        if self.cfg.phases:
            results.append(f"✅ フェーズ: {len(self.cfg.phases)}個設定済み")
            for phase_name, phase in self.cfg.phases.items():
                status = "active" if phase.active else "inactive"
                results.append(f"   • {phase_name}: {len(phase.invite_codes)} codes ({status})")
        else:
            results.append("⚠️ フェーズ: 未設定")

        # 8. 招待枠コード（全フェーズ統合）
        if self.cfg.invited_codes:
            results.append(f"✅ 招待枠コード: {len(self.cfg.invited_codes)}個")
        else:
            results.append("⚠️ 招待枠コード: 未設定（全員「一般枠」になります）")

        # 9. ベースライン招待
        results.append(f"✅ 監視中の招待: {len(self._invite_uses)}個")

        # 10. メンバーログ
        log_count = len(self._member_log.read_all())
        results.append(f"✅ メンバーログ: {log_count}件記録済み")

        # 11. 招待枠コードがサーバーに存在するかチェック
        if self.cfg.invited_codes:
            missing_codes = [c for c in self.cfg.invited_codes if c not in self._invite_uses]
            if missing_codes:
                results.append(f"⚠️ 招待枠コードがサーバーに存在しません: {', '.join(missing_codes[:5])}")
                if len(missing_codes) > 5:
                    results.append(f"   ... 他 {len(missing_codes) - 5}個")
                all_ok = False
            else:
                results.append("✅ 招待枠コード: サーバーに存在確認済み")

        # 結果出力
        status_emoji = "✅" if all_ok else "⚠️"
        header = f"**{status_emoji} InviteRoleAssigner ステータス**\n"
        await message.channel.send(header + "\n".join(results))

        # ログチャンネルにもテスト送信
        if log_ch and all_ok:
            try:
                await log_ch.send("🔧 ステータスチェック: 正常動作中")
                await message.channel.send(f"\n📝 `#{log_ch.name}` にテストログを送信しました")
            except Exception as e:
                await message.channel.send(f"\n❌ ログチャンネルへの送信に失敗: {e}")

    # ========== 内部メソッド ==========

    async def _refresh_invites_baseline(self, reason: str) -> None:
        guild = self._guild
        if guild is None:
            return
        try:
            invites = await guild.invites()
            self._invite_uses = {inv.code: (inv.uses or 0) for inv in invites}
            if self.cfg.debug:
                await self._log(f"🔄 Baseline refreshed ({reason}). invites={len(self._invite_uses)}")
        except discord.Forbidden:
            await self._log(
                "❌ 招待一覧の取得に失敗しました（Forbidden）。Botに **Manage Server(Manage Guild)** 権限が必要です。"
            )
        except Exception as e:
            await self._log(f"❌ 招待一覧の取得に失敗: {type(e).__name__}: {e}")

    async def on_member_join(self, member: discord.Member) -> None:
        if self.cfg.debug:
            print(f"[{self.name}] DEBUG: on_member_join fired for {member} in guild {member.guild.id}")

        if member.guild.id != self.cfg.guild_id:
            if self.cfg.debug:
                print(f"[{self.name}] DEBUG: guild mismatch, skipping. expected={self.cfg.guild_id}")
            return

        if self.cfg.debug:
            print(f"[{self.name}] DEBUG: processing member join...")

        async with self._lock:
            used_code, debug_detail = await self._detect_used_invite_code(member.guild)

            # ロール取得
            general_role = member.guild.get_role(self.cfg.general_role_id)
            review_role = member.guild.get_role(self.cfg.review_role_id)

            # 付与するロール
            roles_to_add: List[discord.Role] = []
            if general_role:
                roles_to_add.append(general_role)

            # フェーズと招待タイプ判定
            phase = self.cfg.get_phase_for_code(used_code) if used_code else None
            if phase:
                invite_type = f"招待枠 ({phase})"
            elif used_code:
                invite_type = "一般枠"
            else:
                invite_type = "検出失敗"
                if review_role:
                    roles_to_add.append(review_role)

            # ロール付与
            if roles_to_add:
                try:
                    await member.add_roles(*roles_to_add, reason=f"InviteRoleAssigner: {invite_type}")
                except Exception as e:
                    await self._log(f"❌ ロール付与失敗: {member.mention} - {type(e).__name__}: {e}")

            # Discordログ出力
            code_str = used_code if used_code else "UNKNOWN"
            role_names = ", ".join([r.name for r in roles_to_add]) if roles_to_add else "なし"
            msg = (
                f"👤 {member.mention} (`{member.id}`) - **{invite_type}**\n"
                f"• invite: `{code_str}`\n"
                f"• roles: {role_names}"
            )
            if self.cfg.debug:
                msg += f"\n• debug: {debug_detail}"

            await self._log(msg)

            # CSV永続ログに記録
            detection_method = "uses_diff"
            if "vanished" in debug_detail:
                detection_method = "vanished"
            elif used_code is None:
                detection_method = "detection_failed"

            record = MemberJoinRecord.create(
                discord_id=member.id,
                username=str(member),
                invite_code=used_code or "UNKNOWN",
                phase=phase or "",
                role_assigned=role_names,
                detection_method=detection_method,
            )
            self._member_log.append(record)

            # ベースライン更新
            await self._refresh_invites_baseline(reason="after_join")

    async def _detect_used_invite_code(self, guild: discord.Guild) -> Tuple[Optional[str], str]:
        """
        直前ベースライン(self._invite_uses)と現在の招待 uses を比較し、
        使われた可能性が高い invite code を返す。
        """
        prev = self._invite_uses or {}

        retry_delays = [0, 0.8, 1.6]
        last_debug_detail = ""

        for attempt, delay in enumerate(retry_delays):
            if delay > 0:
                await asyncio.sleep(delay)

            try:
                invites = await guild.invites()
                current = {inv.code: (inv.uses or 0) for inv in invites}
            except discord.Forbidden:
                return None, "Forbidden while fetching invites"
            except Exception as e:
                return None, f"Error while fetching invites: {type(e).__name__}: {e}"

            changes: List[Tuple[str, int, int]] = []
            for code, curr_uses in current.items():
                prev_uses = prev.get(code, 0)
                if curr_uses > prev_uses:
                    changes.append((code, prev_uses, curr_uses))

            vanished: List[str] = []
            for code in prev.keys():
                if code not in current:
                    vanished.append(code)

            debug_parts = []
            if changes:
                debug_parts.append("changes=" + ", ".join([f"{c}:{p}->{n}" for c, p, n in changes]))
            else:
                debug_parts.append("changes=none")
            if vanished:
                debug_parts.append("vanished=" + ", ".join(vanished))
            if attempt > 0:
                debug_parts.append(f"retry={attempt}")
            last_debug_detail = "; ".join(debug_parts)

            if len(changes) == 1 and (changes[0][2] - changes[0][1]) == 1:
                return changes[0][0], last_debug_detail

            if len(changes) == 0 and len(vanished) == 1:
                return vanished[0], last_debug_detail

        return None, last_debug_detail

    async def _log(self, content: str) -> None:
        if not self._client:
            print(f"[{self.name}] {content}")
            return
        ch = self._client.get_channel(self.cfg.log_channel_id)
        if ch is None:
            try:
                ch = await self._client.fetch_channel(self.cfg.log_channel_id)
            except Exception:
                ch = None

        if isinstance(ch, (discord.TextChannel, discord.Thread)):
            try:
                await ch.send(content)
            except Exception:
                print(f"[{self.name}] Failed to send log. content={content}")
        else:
            print(f"[{self.name}] Log channel not found. content={content}")
