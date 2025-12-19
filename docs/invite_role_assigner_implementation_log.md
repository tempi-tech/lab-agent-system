# InviteRoleAssignerAgent 実装ログ

**作成日**: 2024-12-18
**作成者**: Claude Code + kai

---

## 概要

Discord招待リンクの使用状況を追跡し、招待コードに応じて自動的にロールを付与するエージェント「InviteRoleAssignerAgent」を実装した。

### 主な機能

1. **招待コード別ロール付与**
   - 専門家招待コード → `agi-lab` + `agi-lab*` ロール
   - 一般招待コード → `agi-lab` ロール
   - 判定失敗/未知のコード → `agi-lab` + `要確認` ロール + 管理者通知

2. **管理コマンド**
   - `!invrole generate` - 専門家用招待リンク14本を一括生成
   - `!invrole test` - テスト用招待リンク1本を生成
   - `!invrole delete_all` - Bot作成の招待リンクを全削除
   - `!invrole help` - ヘルプ表示

---

## ファイル構成

```
src/agents/invite_role_assigner/
├── __init__.py      # 空ファイル
├── config.py        # 環境変数読み込み・設定クラス
└── logic.py         # InviteRoleAssignerAgent 本体
```

---

## 実装手順

### 1. 基本設計の確認

`docs/role-agent-implementation.md` に記載された設計書を基に実装を開始。

### 2. ディレクトリ・ファイル作成

```bash
mkdir -p src/agents/invite_role_assigner
```

以下のファイルを作成:
- `__init__.py` - 空ファイル
- `config.py` - 環境変数からの設定読み込み
- `logic.py` - エージェントのメインロジック

### 3. main.py への登録

```python
from src.agents.invite_role_assigner.logic import InviteRoleAssignerAgent

# Register Agents
client.register_agent(InviteRoleAssignerAgent())
```

### 4. 管理コマンドの追加

#### `!invrole generate`
専門家用招待リンク14本を一括生成し、ログチャンネルに出力する機能を追加。
- 管理者ロール保持者のみ実行可能
- 無期限・無制限の招待リンクを生成
- `.env` にコピペできる形式でコード一覧を出力

#### `!invrole delete_all`
Bot が作成した招待リンクを全削除する機能を追加。
- `invite.inviter.id` でBotが作成したものを判定

#### `!invrole test`
テスト用に1本だけ招待リンクを生成する機能を追加。
- 当初は `max_uses=1`（1回限り）だったが、差分検出の問題で `max_uses=0`（無制限）に変更

---

## 発生した問題と解決

### 問題1: on_member_join イベントが発火しない

**症状**: 招待リンクで参加してもロールが付与されない。デバッグログも出力されない。

**原因**: `CommunityBot` クラスに `on_member_join` イベントのディスパッチが実装されていなかった。

**解決**: `src/core/bot.py` に `on_member_join` メソッドを追加。

```python
async def on_member_join(self, member):
    for agent in self.agents:
        if hasattr(agent, 'on_member_join'):
            try:
                await agent.on_member_join(member)
            except Exception as e:
                print(f"Error in {agent.__class__.__name__}.on_member_join: {e}")
```

当初は `client.add_listener()` でリスナー登録を試みたが、`discord.Client` にはこのメソッドがなく（`commands.Bot` のメソッド）、`AttributeError` が発生した。

### 問題2: 招待コードの差分検出で `changes=none`

**症状**: メンバー参加時に `invite: UNKNOWN`、`changes=none` となり、招待コードを検出できない。

**原因**:
1. `!invrole test` で生成した招待が `max_uses=1`（1回限り）だったため、使用後に招待が削除され、差分検出時に存在しなくなった
2. `.env` の変更後にBotを再起動していなかったため、新しい招待コードが `expert_invite_codes` に反映されていなかった

**解決**:
1. テスト招待を `max_uses=0`（無制限）に変更
2. `.env` 変更後にBotを再起動

### 問題3: DailyReporter などの他エージェントが同時起動

**症状**: `python main.py` 実行時に、開発対象外のエージェントも起動してしまう。

**解決**: `main.py` で DailyReporter と QuizMaster の登録をコメントアウト。

```python
# from src.agents.daily_reporter.logic import DailyReporterAgent
# from src.agents.quiz_master import get_agent as get_quiz_master

# daily_reporter = DailyReporterAgent()
# client.register_agent(daily_reporter)

# quiz_master = get_quiz_master()
# client.register_agent(quiz_master)
```

---

## 環境変数

`.env` に以下を設定:

```bash
# 対象Guild
INVROLE_GUILD_ID=842347959102603274

# 付与するロール
INVROLE_GENERAL_ROLE_ID=1432555793105813595     # agi-lab
INVROLE_EXPERT_ROLE_ID=1451042072697241651      # agi-lab*
INVROLE_REVIEW_ROLE_ID=1451042236790997053      # 要確認

# 通知先
INVROLE_ADMIN_ROLE_ID=1436182674044620942       # chatgpt-lab-admin
INVROLE_LOG_CHANNEL_ID=1441302743229665422      # #test

# 専門家招待コード（カンマ区切り）
INVROLE_EXPERT_INVITE_CODES=code1,code2,...

# 一般用招待コード（任意）
INVROLE_GENERAL_INVITE_CODE=

# デバッグモード（任意）
INVROLE_DEBUG=1
```

---

## 動作確認結果

### 成功ログ

```
✅ InviteRoleAssignerAgent ready. expert_codes=1
👤 Join: @kaidebug (1451049110395293958)
• invite: J3ceV7tW5N
• roles: agi-lab, agi-lab*
• debug: changes=J3ceV7tW5N:0->1
🔄 Baseline refreshed (after_join). invites=3
```

- 招待コード `J3ceV7tW5N` を正しく検出
- `agi-lab` + `agi-lab*` ロールを付与
- 差分検出 `0->1` で使用回数の変化を確認

---

## 追加実装（本番対応）

### 1. Docker本番起動確認

`docker-compose.yml` を更新:
- `command: ["python", "-u", "main.py"]` 追加（ログ遅延防止）

### 2. 差分検知の堅牢化

`_detect_used_invite_code` を改善:

**リトライ機能**
- 初回 → 0.8秒後 → 1.6秒後の3回でリトライ
- 招待usesがDiscord側で反映されるまでの遅延に対応

**消えた招待の検出**
- `max_uses=1` の招待が使用後に消えるケースを検出
- 前回存在して今回消えた招待が1件だけなら、それを使用された招待と判定

```python
# 判定ロジック（優先順位順）
# 1. 増加が1件かつ+1 → それを採用
# 2. 増加がなく、消えた招待が1件だけ → それを採用
# 3. それ以外はリトライ継続、最終的にUNKNOWN
```

### 3. 生成コマンド設定の本番対応

`!invrole generate` の招待設定を変更:
- `max_age=604800` (7日間有効)
- `max_uses=1` (1回限り)

### 4. デバッグログの整理

すべてのデバッグ出力を `INVROLE_DEBUG=1` で制御:
- コンソールのprint文
- Discordへのベースライン更新ログ
- Join時のdebug詳細情報

本番運用時は `INVROLE_DEBUG` を未設定または空にすることで、ノイズを削減。

---

## 運用手順

### 当日の最短手順

1. **事前準備**
   - `!invrole generate` で専門家用招待リンク14本を生成
   - 生成されたコードを `.env` の `INVROLE_EXPERT_INVITE_CODES` に設定
   - Botを再起動

2. **イベント中**
   - メンバー参加時に #test にログが出力される
   - 正常: `invite: xxx` / `roles: agi-lab, agi-lab*`
   - 要確認: `invite: UNKNOWN` → 管理者が手動で確認

3. **要確認への対応**
   - `agi-lab*` ロールを手動付与
   - `要確認` ロールを外す

---

## v2: 忘年会対応リファクタリング (2025-12-18)

### 背景

忘年会イベント用に、システムを大幅に簡素化。

- **招待枠**: 専門家として招待された参加者
- **一般枠**: 一般参加の方

それぞれ別のQRコード（招待URL）で参加してもらい、内部的にどちらの枠で参加したかをログで記録する。

### 変更概要

| 項目 | 変更前 | 変更後 |
|------|--------|--------|
| 招待URL数 | 14本（専門家用） | 2本（招待枠・一般枠） |
| 招待有効期限 | 7日間 / 2回まで | **無期限・無制限** |
| ロール付与 | `agi-lab` + `agi-lab*` | `agi-lab` のみ |
| 検出失敗時 | `agi-lab` + `要確認` | `agi-lab` + `要確認`（変更なし） |
| ログチャンネル | `#test` | `#member-log`（専用チャンネル） |
| 環境変数 | `INVROLE_EXPERT_INVITE_CODES` | `INVROLE_INVITED_CODES` |

### 新しい動作仕様

| 招待URL | 付与ロール | #member-log への出力 |
|---------|-----------|---------------------|
| 招待枠 (`CNdj4dr74d`) | `agi-lab` | 「招待枠」+ ユーザーID |
| 一般枠 (`PV7TS83SUN`) | `agi-lab` | 「一般枠」+ ユーザーID |
| 検出失敗 | `agi-lab` + `要確認` | 「検出失敗」+ ユーザーID |

### 新しい環境変数

```bash
INVROLE_GUILD_ID=842347959102603274
INVROLE_GENERAL_ROLE_ID=1432555793105813595    # agi-lab
INVROLE_REVIEW_ROLE_ID=1451042236790997053     # 要確認
INVROLE_ADMIN_ROLE_ID=1436182674044620942      # chatgpt-lab-admin
INVROLE_LOG_CHANNEL_ID=842347959102603277      # #member-log
INVROLE_INVITED_CODES=CNdj4dr74d               # 招待枠のコード
INVROLE_DEBUG=1
```

**削除した環境変数:**
- `INVROLE_EXPERT_ROLE_ID` - `agi-lab*` ロールは不要になった
- `INVROLE_EXPERT_INVITE_CODES` - `INVROLE_INVITED_CODES` にリネーム
- `INVROLE_GENERAL_INVITE_CODE` - 不要（招待枠以外は全て一般枠扱い）

### コード変更

#### `config.py`

```python
# 変更前
@dataclass(frozen=True)
class InviteRoleAssignerConfig:
    guild_id: int
    general_role_id: int
    expert_role_id: int        # 削除
    review_role_id: int
    admin_role_id: int
    log_channel_id: int
    expert_invite_codes: Set[str]  # リネーム
    general_invite_code: Optional[str]  # 削除

# 変更後
@dataclass(frozen=True)
class InviteRoleAssignerConfig:
    guild_id: int
    general_role_id: int      # agi-lab
    review_role_id: int       # 要確認
    admin_role_id: int
    log_channel_id: int       # #member-log
    invited_codes: Set[str]   # 招待枠のコード
    debug: bool = False
```

#### `logic.py` - `on_member_join`

```python
# 変更後のロジック
async def on_member_join(self, member: discord.Member) -> None:
    # ... 省略 ...

    # 全員に agi-lab を付与
    roles_to_add = [general_role]

    # 招待タイプ判定
    if used_code in self.cfg.invited_codes:
        invite_type = "招待枠"
    elif used_code:
        invite_type = "一般枠"
    else:
        invite_type = "検出失敗"
        # 検出失敗時は要確認も付与
        roles_to_add.append(review_role)

    # ロール付与
    await member.add_roles(*roles_to_add)

    # ログ出力
    msg = f"👤 {member.mention} (`{member.id}`) - **{invite_type}**\n..."
    await self._log(msg)
```

#### `logic.py` - `!invrole generate`

```python
# 変更後: 2本の招待URL（無期限・無制限）
async def _cmd_generate_expert_invites(self, message):
    # 招待枠URL
    invited_invite = await channel.create_invite(
        max_age=0,       # 無期限
        max_uses=0,      # 無制限
        unique=True,
        reason="招待枠 invite"
    )
    # 一般枠URL
    general_invite = await channel.create_invite(
        max_age=0,
        max_uses=0,
        unique=True,
        reason="一般枠 invite"
    )
```

### 新機能: `!invrole status`

当日の動作確認用に、システム状態を一括チェックするコマンドを追加。

```
!invrole status
```

**チェック項目:**
1. Bot接続状態
2. Guild取得
3. `agi-lab` ロール存在確認
4. `要確認` ロール存在確認
5. 管理者ロール存在確認
6. ログチャンネル（#member-log）アクセス
7. 招待枠コード設定状況
8. 監視中の招待数
9. 招待枠コードがサーバーに存在するか
10. ログチャンネルへのテスト送信

**出力例:**
```
✅ InviteRoleAssigner ステータス
✅ Bot接続: OK
✅ Guild: AGI-Lab
✅ agi-lab ロール: agi-lab
✅ 要確認ロール: 要確認
✅ 管理者ロール: chatgpt-lab-admin
✅ ログチャンネル: #member-log
✅ 招待枠コード: 1個設定済み
   • `CNdj4dr74d`
✅ 監視中の招待: 5個
✅ 招待枠コード: サーバーに存在確認済み

📝 `#member-log` にテストログを送信しました
```

### 更新された管理コマンド一覧

| コマンド | 説明 |
|---------|------|
| `!invrole status` | **新規** システム状態を一括チェック |
| `!invrole generate` | 招待枠・一般枠の招待URL各1本を生成 |
| `!invrole test` | テスト用招待リンク1本を生成 |
| `!invrole delete_all` | Bot作成の招待リンクを全削除 |
| `!invrole help` | ヘルプを表示 |

### 忘年会当日の運用手順

#### 事前準備

1. `!invrole generate` で招待URL2本を生成
2. 出力された招待枠コードを `.env` の `INVROLE_INVITED_CODES` に設定
3. `docker compose down && docker compose up -d` で再起動
4. QRコード作成
   - 招待枠: https://discord.gg/INVITE_CODE
   - 一般枠: https://discord.gg/INVITE_CODE

#### イベント開始前

1. `!invrole status` でシステム状態確認
2. 全て ✅ になっていることを確認

#### イベント中

- 参加者がQRコードでサーバーに参加
- `#member-log` に自動でログが出力される
- 「検出失敗」の場合は管理者が手動確認

#### 要確認への対応

1. `#member-log` で「検出失敗」を確認
2. 該当メンバーに直接確認（招待枠 or 一般枠）
3. 必要に応じて `要確認` ロールを外す

---

## 変更ファイル一覧

### v1 (初期実装)

| ファイル | 変更内容 |
|---------|---------|
| `src/agents/invite_role_assigner/__init__.py` | 新規作成（空） |
| `src/agents/invite_role_assigner/config.py` | 新規作成（設定クラス） |
| `src/agents/invite_role_assigner/logic.py` | 新規作成（エージェント本体） |
| `src/core/bot.py` | `on_member_join` ディスパッチ追加 |
| `main.py` | エージェント登録追加、他エージェントを一時コメントアウト |

### v2 (忘年会対応)

| ファイル | 変更内容 |
|---------|---------|
| `src/agents/invite_role_assigner/config.py` | 環境変数簡素化、`expert_role_id` 削除、`invited_codes` にリネーム |
| `src/agents/invite_role_assigner/logic.py` | ロール付与ロジック簡素化、`!invrole status` 追加、`generate` を2本生成に変更 |
| `.env` | `INVROLE_LOG_CHANNEL_ID` を #member-log に変更、環境変数名変更 |

---

## 参考

- 設計書: `docs/role-agent-implementation.md`
- エージェント開発ガイド: `AGENT_GUIDE.md`
- プロジェクト概要: `CLAUDE.md`
- プランファイル: `/Users/kai/.claude/plans/piped-toasting-codd.md`
