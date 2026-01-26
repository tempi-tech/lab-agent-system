# Invite Role Assigner Agent

Discord招待リンクに基づいて新規メンバーに自動でロールを付与するエージェント。

## 機能概要

新規メンバーがどの招待リンクを使って参加したかを検出し、フェーズ（Wave）に応じて適切なロールを付与します。

| 招待コードの種類 | 付与されるロール | ログ表記 |
|-----------------|-----------------|---------|
| フェーズ登録済みコード | `agi-lab` | 招待枠 (フェーズ名) |
| 未登録コード | `agi-lab` | 一般枠 |
| 検出失敗時 | `agi-lab` + `要確認` | 検出失敗 |

## コマンド

| コマンド | 説明 |
|---------|------|
| `!invrole generate` | 招待枠・一般枠の招待URL各1本を生成 |
| `!invrole test` | テスト用招待リンクを1本生成 |
| `!invrole status` | システム状態の総合チェック |
| `!invrole phases` | フェーズ一覧と参加統計を表示 |
| `!invrole delete_all` | Bot作成の招待リンクを全削除 |
| `!invrole sync preview <csv>` | CSV照合プレビュー |
| `!invrole sync execute <csv>` | CSV未掲載者のロール削除 |
| `!invrole help` | ヘルプ表示 |

## 設定

### 設定ファイル（推奨）

`config.json` で設定を管理します。

```json
{
  "version": "1.0",
  "guild_id": 123456789012345678,
  "log_channel_id": 123456789012345678,
  "roles": {
    "general_role_id": 123456789012345678,
    "review_role_id": 123456789012345678,
    "admin_role_id": 123456789012345678
  },
  "phases": {
    "2025-01-wave1": {
      "description": "2025年1月 第1弾",
      "invite_codes": ["abc123"],
      "active": true
    },
    "2025-01-wave2": {
      "description": "2025年1月 第2弾 フォローアップ",
      "invite_codes": ["def456"],
      "active": true
    }
  },
  "debug": false,
  "data_dir": "data/invite_role_assigner"
}
```

### 環境変数

| 変数名 | 説明 |
|--------|------|
| `INVROLE_CONFIG` | 設定ファイルのパス（**重要: 下記参照**） |

#### ローカル開発時
```
INVROLE_CONFIG=data/invite_role_assigner/config.json
```

#### GCP VM 上
```
INVROLE_CONFIG=/app/data/invite_role_assigner/config.json
```

> **注意**: VM上の実際のファイルパスは `/var/lib/lab-agent/data/invite_role_assigner/config.json` です。
> Docker ボリュームマウントにより `/app/data/` としてコンテナ内からアクセスされます。

### レガシー環境変数（後方互換）

`INVROLE_CONFIG` が未設定の場合、以下の環境変数が使用されます：

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `INVROLE_GUILD_ID` | Yes | Discord サーバーID |
| `INVROLE_GENERAL_ROLE_ID` | Yes | agi-lab ロールID |
| `INVROLE_REVIEW_ROLE_ID` | Yes | 要確認ロールID |
| `INVROLE_ADMIN_ROLE_ID` | Yes | 管理者ロールID |
| `INVROLE_LOG_CHANNEL_ID` | Yes | ログチャンネルID |
| `INVROLE_INVITED_CODES` | No | 招待枠コード（カンマ区切り） |
| `INVROLE_DEBUG` | No | デバッグモード（1/true/yes） |

## ファイル構成

```
src/agents/invite_role_assigner/
├── __init__.py         # get_agent() エクスポート
├── config.py           # 設定読み込み・Phase dataclass
├── logic.py            # InviteRoleAssignerAgent 本体
├── storage.py          # CSV永続化ストレージ
├── config.example.json # 設定ファイル例
└── README.md           # このファイル
```

## 出力ファイル

`data/invite_role_assigner/` に以下が保存されます：

- `config.json` - 設定ファイル
- `member_log.csv` - メンバー参加ログ（永続）

### member_log.csv の構造

| カラム | 説明 |
|--------|------|
| `timestamp` | 参加日時（ISO 8601 UTC） |
| `discord_id` | Discord ユーザーID |
| `username` | Discord ユーザー名 |
| `invite_code` | 使用された招待コード |
| `phase` | フェーズ名（空白の場合は一般枠） |
| `role_assigned` | 付与されたロール名 |
| `detection_method` | 検出方法（uses_diff/vanished/detection_failed） |

## 招待コード検出アルゴリズム

1. Bot起動時に全招待コードの使用回数をベースラインとして記録
2. メンバー参加時に現在の使用回数と比較
3. 使用回数が増えたコードを特定（最大3回リトライ）
4. フェーズに登録されているコードかどうかを判定
5. ロール付与 → CSV記録 → ログ送信

## 新規フェーズ（Wave）の追加手順

1. **招待リンクを生成**
   ```
   !invrole generate
   ```
   → 招待枠URLのコード（例: `abc123`）をメモ

2. **config.json を更新**
   ```json
   "phases": {
     "2025-01-wave2": {
       "description": "2025年1月 第2弾 フォローアップ",
       "invite_codes": ["abc123"],
       "active": true
     }
   }
   ```

3. **GCP VM にデプロイ**（→ [GCP運用ガイド](../../docs/gcp_operations.md) 参照）
   ```bash
   gcloud compute scp \
     data/invite_role_assigner/config.json \
     kai@lab-agent-vm:/var/lib/lab-agent/data/invite_role_assigner/config.json \
     --zone=asia-northeast1-c

   gcloud compute ssh lab-agent-vm --zone=asia-northeast1-c \
     --command="sudo systemctl restart lab-agent"
   ```

4. **動作確認**
   - 招待リンクでテスト参加
   - ログに `招待枠 (フェーズ名)` が表示されることを確認

## 関連エージェント

- **membership_checker**: note会員CSVとの照合・同期
  - `invite_role_assigner` の `config.json` からギルドID・ロールIDを参照

## トラブルシューティング

### 新しいフェーズが反映されない

**症状**: config.json を更新したが「一般枠」として記録される

**原因**: GCP VM 上の設定ファイルが更新されていない、または間違ったパスにコピーしている

**解決**:
1. `.env` の `INVROLE_CONFIG` パスを確認
2. 正しいパス（`/var/lib/lab-agent/data/invite_role_assigner/config.json`）にコピー
3. サービス再起動

詳細は [GCP運用ガイド](../../docs/gcp_operations.md) を参照。

### 招待コードが検出されない

**症状**: 「検出失敗」として記録され、`要確認` ロールが付与される

**考えられる原因**:
- Discord API のタイミング問題（リトライで解決することが多い）
- 招待コードの有効期限切れ
- Bot の権限不足（Manage Guild が必要）

**確認コマンド**:
```
!invrole status
```
→ 招待枠コードがサーバーに存在するか確認
