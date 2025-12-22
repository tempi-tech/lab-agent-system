# Lab Agent System への組み込み設計

## ディレクトリ構成

```
src/agents/invite_role_assigner/
  ├── __init__.py
  ├── config.py
  └── logic.py
```

## .env に追加する値（IDは必ず「ID」で）

**ロール名は後から変わるので、IDで持ちます。**

```
# 対象Guild
INVROLE_GUILD_ID=GUILD_ID

# 付与するロール
INVROLE_GENERAL_ROLE_ID=ROLE_ID     # agi-lab
INVROLE_EXPERT_ROLE_ID=ROLE_ID      # agi-lab*
INVROLE_REVIEW_ROLE_ID=ROLE_ID      # 要確認

# 通知先
INVROLE_ADMIN_ROLE_ID=ROLE_ID       # chatgpt-lab-admin
INVROLE_LOG_CHANNEL_ID=CHANNEL_ID     # #test

# 専門家14本の招待コード（URLでもOK。カンマ区切り）
# 例: https://discord.gg/INVITE_CODE_CODE の場合 "INVITE_CODE"
INVROLE_EXPERT_INVITE_CODES=code1,code2,code3,...

# （推奨）一般用招待コード。指定すると「未知の招待コード」を要確認に倒せます。
INVROLE_GENERAL_INVITE_CODE=generalCodeHere

# 任意
INVROLE_DEBUG=1
```

> もし `INVROLE_GENERAL_INVITE_CODE` を空にすると、
> 「専門家コード以外は一般」として扱うので、既存招待が残っていても運用が楽になります。
> ただし“想定外の招待”が混ざっても気づきにくいので、イベント時は指定推奨です。

---

# コード

## `src/agents/invite_role_assigner/config.py`

```
import os
import re
from dataclasses import dataclass
from typing import Optional, Set

_INVITE_CODE_RE = re.compile(r"(?:discord\.gg/|discord(?:app)?\.com/invite/)([A-Za-z0-9-]+)")

def _normalize_invite_code(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    m = _INVITE_CODE_RE.search(v)
    return m.group(1) if m else v

def _parse_codes_csv(csv: str) -> Set[str]:
    if not csv:
        return set()
    codes = set()
    for part in csv.split(","):
        code = _normalize_invite_code(part)
        if code:
            codes.add(code)
    return codes

def _get_int_env(name: str, default: Optional[int] = None) -> Optional[int]:
    v = os.getenv(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        raise ValueError(f"Env {name} must be an integer, got: {v!r}")

@dataclass(frozen=True)
class InviteRoleAssignerConfig:
    guild_id: int
    general_role_id: int
    expert_role_id: int
    review_role_id: int
    admin_role_id: int
    log_channel_id: int

    expert_invite_codes: Set[str]
    general_invite_code: Optional[str]

    debug: bool = False

def load_config() -> InviteRoleAssignerConfig:
    guild_id = _get_int_env("INVROLE_GUILD_ID")
    general_role_id = _get_int_env("INVROLE_GENERAL_ROLE_ID")
    expert_role_id = _get_int_env("INVROLE_EXPERT_ROLE_ID")
    review_role_id = _get_int_env("INVROLE_REVIEW_ROLE_ID")
    admin_role_id = _get_int_env("INVROLE_ADMIN_ROLE_ID")
    log_channel_id = _get_int_env("INVROLE_LOG_CHANNEL_ID")

    missing = [k for k, v in {
        "INVROLE_GUILD_ID": guild_id,
        "INVROLE_GENERAL_ROLE_ID": general_role_id,
        "INVROLE_EXPERT_ROLE_ID": expert_role_id,
        "INVROLE_REVIEW_ROLE_ID": review_role_id,
        "INVROLE_ADMIN_ROLE_ID": admin_role_id,
        "INVROLE_LOG_CHANNEL_ID": log_channel_id,
    }.items() if v is None]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    expert_invite_codes = _parse_codes_csv(os.getenv("INVROLE_EXPERT_INVITE_CODES", ""))
    if not expert_invite_codes:
        raise RuntimeError("INVROLE_EXPERT_INVITE_CODES is empty. Put 14 invite codes (comma-separated).")

    general_invite_code_raw = os.getenv("INVROLE_GENERAL_INVITE_CODE", "").strip()
    general_invite_code = _normalize_invite_code(general_invite_code_raw) if general_invite_code_raw else None

    debug = os.getenv("INVROLE_DEBUG", "").strip() in {"1", "true", "True", "yes", "YES"}

    return InviteRoleAssignerConfig(
        guild_id=guild_id,
        general_role_id=general_role_id,
        expert_role_id=expert_role_id,
        review_role_id=review_role_id,
        admin_role_id=admin_role_id,
        log_channel_id=log_channel_id,
        expert_invite_codes=expert_invite_codes,
        general_invite_code=general_invite_code,
        debug=debug,
    )
```

## `src/agents/invite_role_assigner/logic.py`

```
import asyncio
from typing import Dict, Optional, Tuple, List

import discord

from src.core.agent_base import BaseAgent
from .config import load_config, InviteRoleAssignerConfig


class InviteRoleAssignerAgent(BaseAgent):
    """
    招待リンク（invite）の uses 差分から「どの招待で入ったか」を推測してロール付与するエージェント。

    仕様:
    - 専門家招待コード(14本)  -> agi-lab + agi-lab*
    - 一般招待コード(1本)     -> agi-lab
    - 判定失敗(複数変化/変化なし/取得失敗) -> agi-lab + 要確認 + 管理者ロールに通知
    """

    def __init__(self) -> None:
        self.cfg: InviteRoleAssignerConfig = load_config()
        self._client: Optional[discord.Client] = None
        self._guild: Optional[discord.Guild] = None

        self._invite_uses: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._listener_registered = False

    @property
    def name(self) -> str:
        return "InviteRoleAssignerAgent"

    async def on_ready(self, client: discord.Client) -> None:
        self._client = client
        guild = client.get_guild(self.cfg.guild_id)
        if guild is None:
            # botがまだguild情報をキャッシュできてない等もあるので fetch も試す
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

        # BaseAgentのディスパッチに on_member_join が無いケースでも確実に動くよう、リスナー登録
        if not self._listener_registered:
            try:
                client.add_listener(self._on_member_join, "on_member_join")
                self._listener_registered = True
            except Exception as e:
                print(f"[{self.name}] WARN: failed to add_listener(on_member_join): {e!r}")

        await self._log(f"✅ {self.name} ready. expert_codes={len(self.cfg.expert_invite_codes)}")

    async def on_message(self, message: discord.Message) -> None:
        # 今回はメッセージ処理は不要（ログ/管理は#testで十分）
        return

    async def _refresh_invites_baseline(self, reason: str) -> None:
        guild = self._guild
        if guild is None:
            return
        try:
            invites = await guild.invites()  # requires MANAGE_GUILD
            self._invite_uses = {inv.code: (inv.uses or 0) for inv in invites}
            if self.cfg.debug:
                await self._log(f"🔄 Baseline refreshed ({reason}). invites={len(self._invite_uses)}")
        except discord.Forbidden:
            await self._log(
                "❌ 招待一覧の取得に失敗しました（Forbidden）。Botに **Manage Server(Manage Guild)** 権限が必要です。"
            )
        except Exception as e:
            await self._log(f"❌ 招待一覧の取得に失敗: {type(e).__name__}: {e}")

    async def _on_member_join(self, member: discord.Member) -> None:
        # 目的のguildのみ
        if member.guild.id != self.cfg.guild_id:
            return

        async with self._lock:
            used_code, debug_detail = await self._detect_used_invite_code(member.guild)

            # 付与ロール決定
            roles_to_add, notify_admins, note = self._decide_roles(member.guild, used_code)

            # 実際に付与
            try:
                if roles_to_add:
                    await member.add_roles(*roles_to_add, reason=f"Auto role by invite. code={used_code or 'UNKNOWN'}")
            except discord.Forbidden:
                notify_admins = True
                note = "❌ ロール付与に失敗（Forbidden）。Botのロール階層/Manage Roles権限を確認してください。"
            except Exception as e:
                notify_admins = True
                note = f"❌ ロール付与に失敗: {type(e).__name__}: {e}"

            # ログ
            roles_str = ", ".join([r.name for r in roles_to_add]) if roles_to_add else "(none)"
            code_str = used_code if used_code else "UNKNOWN"

            msg = (
                f"👤 Join: {member.mention} (`{member.id}`)\n"
                f"• invite: `{code_str}`\n"
                f"• roles: {roles_str}\n"
            )
            if note:
                msg += f"• note: {note}\n"
            if self.cfg.debug and debug_detail:
                msg += f"• debug: {debug_detail}\n"

            if notify_admins:
                admin_role = member.guild.get_role(self.cfg.admin_role_id)
                admin_mention = admin_role.mention if admin_role else f"<@&{self.cfg.admin_role_id}>"
                msg = f"{admin_mention}\n" + msg

            await self._log(msg)

            # 最後にベースライン更新（次のjoinに備える）
            await self._refresh_invites_baseline(reason="after_join")

    async def _detect_used_invite_code(self, guild: discord.Guild) -> Tuple[Optional[str], str]:
        """
        直前ベースライン(self._invite_uses)と現在の招待 uses を比較し、
        使われた可能性が高い invite code を返す。

        戻り値: (used_code or None, debug_detail)
        """
        try:
            invites = await guild.invites()
            current = {inv.code: (inv.uses or 0) for inv in invites}
        except discord.Forbidden:
            return None, "Forbidden while fetching invites"
        except Exception as e:
            return None, f"Error while fetching invites: {type(e).__name__}: {e}"

        prev = self._invite_uses or {}
        changes: List[Tuple[str, int, int]] = []  # (code, prev_uses, curr_uses)
        for code, curr_uses in current.items():
            prev_uses = prev.get(code, 0)
            if curr_uses > prev_uses:
                changes.append((code, prev_uses, curr_uses))

        # デバッグ情報
        debug_detail = ""
        if changes:
            debug_detail = "changes=" + ", ".join([f"{c}:{p}->{n}" for c, p, n in changes])
        else:
            debug_detail = "changes=none"

        # 判定ロジック（安全側）
        if len(changes) == 1 and (changes[0][2] - changes[0][1]) == 1:
            return changes[0][0], debug_detail

        # 同時joinが多いと複数増える/変化0が起きることがある → 不確実扱い
        return None, debug_detail

    def _decide_roles(
        self, guild: discord.Guild, used_code: Optional[str]
    ) -> Tuple[List[discord.Role], bool, str]:
        """
        付与するロール一覧、管理者通知するか、注釈
        """
        general = guild.get_role(self.cfg.general_role_id)
        expert = guild.get_role(self.cfg.expert_role_id)
        review = guild.get_role(self.cfg.review_role_id)

        roles: List[discord.Role] = []
        notify_admins = False
        note = ""

        # 最低でも一般ロールは付与（あなたの要件）
        if general:
            roles.append(general)
        else:
            notify_admins = True
            note += "general_role_not_found; "

        if used_code is None:
            # 判定失敗
            if review:
                roles.append(review)
            else:
                notify_admins = True
                note += "review_role_not_found; "
            notify_admins = True
            note += "invite_detection_failed"
            return roles, notify_admins, note.strip()

        # general invite code を設定している場合は、未知コードを要確認に倒す
        if self.cfg.general_invite_code and used_code == self.cfg.general_invite_code:
            # 一般
            return roles, notify_admins, note.strip()

        if used_code in self.cfg.expert_invite_codes:
            # 専門家
            if expert:
                roles.append(expert)
            else:
                notify_admins = True
                note += "expert_role_not_found; "
            return roles, notify_admins, note.strip()

        # ここに来るのは「expertでもgeneralでもないコード」
        # - general_invite_code未設定なら「一般扱い」でOK（ノイズ減らす）
        # - general_invite_code設定済みなら「未知」なので要確認
        if self.cfg.general_invite_code:
            if review:
                roles.append(review)
            notify_admins = True
            note += f"unknown_invite_code:{used_code}"
        return roles, notify_admins, note.strip()

    async def _log(self, content: str) -> None:
        if not self._client:
            print(f"[{self.name}] {content}")
            return
        ch = self._client.get_channel(self.cfg.log_channel_id)
        if ch is None:
            # fetchも試す
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
```

## `src/agents/invite_role_assigner/__init__.py`

```
# empty
```

---

# `main.py` への登録

あなたのガイド通りに登録します：

```
from src.agents.invite_role_assigner.logic import InviteRoleAssignerAgent

# ... inside main() ...
client.register_agent(InviteRoleAssignerAgent())
```

ーーーーーーーーー

## 次のステップ（おすすめ順）

### 1) Dockerでの“本番同等”起動確認

**チェックポイントは4つだけ：**
- **.envがコンテナに入っている**（env_file or environment）
- Discord Developer Portalで **Server Members Intent** ON（かつコード側でも members intent を有効）
- Bot権限：**Manage Server / Manage Roles** + ロール階層OK
- **再起動しても復帰**する（restart policy）

docker-compose例（最小）

```
services:
  lab-agent:
    build: .
    env_file:
      - .env
    command: ["python", "-u", "main.py"]
    restart: unless-stopped
```

> `-u` を付けるとログが遅延せずに出るので、当日トラブル時に効きます。

---

### 2) “同時参加が多い”前提の堅牢化（UNKNOWN/要確認を減らす）

ログにもある通り、JOIN直後に `changes=none` が出るのはよくあります。原因は主に2つで、どっちも**小さい修正で改善**できます。

改善A：招待usesが反映されるまで軽くリトライする

JOINイベントが先に来て、招待の `uses` がまだ増えてないことがあります。
→ **0.8秒→1.6秒**くらいで2回リトライするだけでUNKNOWNが減ります。

改善B：max_uses=1 で “使用後にinviteが消える”ケースを拾う

あなたの実装ログの通り、`max_uses=1` の招待が使用後に一覧から消えることがある。
→ 「前は存在したが、今は消えたコード」が **1つだけ**なら、それを“使われた”と推定できます。

**差分検知のおすすめパッチ方針（擬似コード）**
- currentで増えた招待が **(1件かつ +1)** → それを採用
- それが取れない場合に
  - prevにあってcurrentにないコードが **1件だけ** → それを採用
- それでもダメなら UNKNOWN（要確認）

この2つを入れると、イベントの「3時間に集中」でも、要確認の割合がかなり下がります。

---

### 3) 生成コマンドの設定が“本番要件”になってるか確認

ログだと `!invrole generate` が **無期限・無制限**で作ってますが、あなたの要件は
- 専門家：**max_uses=1 / expire=7days**
- 一般：無制限（期限なしでOK）

なので、`generate` は **expire/max_uses を要件に合わせる**のがおすすめです（カード運用だとここ大事）。

---

### 4) 当日運用の“最短手順”を決める

#testに通知が来る設計は完璧。次はこれだけ決めればOK：
- **要確認が出たら**：運営が `agi-lab*` 付けて `要確認` 外す（その場で30秒）
- **招待リンクはイベント用だけ**にする（他の古い招待が残るとUNKNOWN増える）

---

## いまの実装ログから見た「ここだけ注意」

- `client.add_listener()` が使えない件を **core側で on_member_join ディスパッチ追加**で解決してるのは正解。Dockerでも同じく動きます。
- `max_uses=1` の招待が “使用後に消える” → **差分検知ロジック側で拾えるようにする**のが本番向き（今のままだとUNKNOWN増えやすい）。
