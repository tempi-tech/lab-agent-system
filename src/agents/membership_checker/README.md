# Membership Checker Agent

note会員CSVとDiscordサーバーの会員状況を同期するエージェント。

## 機能

| コマンド | 説明 |
|---------|------|
| `!membership status` | 参加状況レポートを表示 |
| `!membership assign preview` | ロール未付与者のプレビュー |
| `!membership assign execute` | ロールを実際に付与 |
| `!membership followup` | 未参加者リストをCSV添付で出力 |
| `!membership sync preview` | 退会者同期のプレビュー |
| `!membership sync execute` | 退会者からロールを削除 |

## GitHub Actions 定期実行

毎週月曜 10:00 JST に `.github/workflows/membership_check.yml` が自動実行されます。

**実行内容:**
```bash
python main.py --once membership
```

これにより `run_scheduled_check()` が呼び出され、以下が実行されます：

1. 最新のnote会員CSVを読み込み
2. Discordサーバーの会員状況をチェック
3. ログチャンネルにレポートを投稿
4. 結果をJSONファイルに保存

**出力例（ログチャンネル）:**
```
📊 AGIラボ Discord会員状況レポート
生成: 2025-01-14T10:00:00
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 Discord紐付け統計:
  総数: 50名
  ├ 有効ID: 25名 (アクティブ: 23名)
  └ ユーザー名のみ: 25名 (アクティブ: 20名)

🔗 サーバー: AGIラボ
  総メンバー: 150名
  ロール所持: 40名

━━ 有効ID会員 ━━
✅ サーバー参加: 21名
   └ ロールあり: 19名 / ロールなし: 2名
❌ 未参加: 2名

━━ ユーザー名会員 ━━
✅ サーバー参加: 16名
❌ 未参加: 4名
```

## 設定

### 環境変数

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `MEMBERSHIP_CHECKER_LOG_CHANNEL_ID` | Yes | レポート投稿先チャンネルID |
| `MEMBERSHIP_CHECKER_CSV_DIR` | No | CSV格納ディレクトリ（デフォルト: `~/Develop/note-extract-auto/data/output/active`） |
| `MEMBERSHIP_CHECKER_DATA_DIR` | No | 出力先ディレクトリ（デフォルト: `data/membership_checker`） |
| `MEMBERSHIP_CHECKER_CONFIRM_USERNAMES` | No | ユーザー名マッチングを有効化（デフォルト: false） |
| `MEMBERSHIP_CHECKER_DEBUG` | No | デバッグログを有効化 |

### 依存設定

`invite_role_assigner` の `config.json` からギルドID・ロールIDを参照します：
- `INVROLE_CONFIG` 環境変数でパスを指定（デフォルト: `data/invite_role_assigner/config.json`）

## ファイル構成

```
src/agents/membership_checker/
├── __init__.py     # get_agent() エクスポート
├── config.py       # 設定読み込み
├── checker.py      # CSV解析・Discord API処理
├── logic.py        # MembershipCheckerAgent 本体
└── README.md       # このファイル
```

## 出力ファイル

`data/membership_checker/` に以下が保存されます：

- `status_YYYYMMDD_HHMMSS.json` - ステータスレポート
- `assign_YYYYMMDD_HHMMSS.json` - ロール付与結果
- `followup_YYYYMMDD_HHMMSS.json` - 未参加者リスト
- `followup_YYYYMMDD_HHMMSS.csv` - 未参加者CSV（メール連絡用）
- `sync_YYYYMMDD_HHMMSS.json` - 退会者同期結果

## 注意事項

- `!membership assign execute` と `!membership sync execute` は管理者ロール必須
- `sync execute` はロール削除を行うため、慎重に使用してください
- GitHub Actions での自動実行は `status` のみ（安全のため）
