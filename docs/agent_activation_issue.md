# DailyReporter 自動出力の課題整理（前提・現状・論点）

## 目的
- DailyReporter を有効化して他エージェントをテストすると、不要なレポートが #random などのターゲットチャンネルに投稿されてしまう問題を整理する。
- **「シンプル優先・過度な防御を避ける」**という方針のもとで、適切な解決策を選べるように前提と論点を明文化する。

## 背景 / 前提
- 本リポジトリは Discord 向けのマルチエージェント Bot で、`main.py` がエージェント登録の起点になっている。
- DailyReporter は「1日分のメッセージを集計して日報を投稿する」エージェント。
- 運用としては、DailyReporter を **本番で自動実行したい**が、開発・テスト時には **不要な投稿を避けたい**。

## 現状の挙動（現実のコード前提）
- `main.py` で `DailyReporterAgent` を登録すると、**起動直後の `on_ready` で即時に日報生成が走る**。
- 投稿先は `DISCORD_CHANNEL_ID`（`src/core/config.py`）で決まる。
- DailyReporter の `on_ready` は「起動直後に必ず出す」ロジックであり、**他エージェントのテストでも必ず動く**。
- 現在は `main.py` から DailyReporter の登録をコメントアウトして回避している。

関連ファイル:
- `main.py`
- `src/agents/daily_reporter/logic.py`
- `src/core/config.py`
- `src/agents/daily_reporter/config.py`

## 現在の課題
- **DailyReporter を起動すると、不要なタイミングで日報が投稿される**。
- 「QuizMaster / InviteRoleAssigner のみをテストしたい」場面で DailyReporter が混ざり、**検証チャネルが汚れる・運用事故リスクがある**。
- コメントアウトでの制御は手間がかかり、作業ミスを誘発しやすい。

## 影響
- テスト時に誤投稿が発生し、チャンネルが乱れる。
- 本番用の `DISCORD_CHANNEL_ID` を誤って使うと、運用チャンネルへの誤投稿につながる。
- エージェント構成の切替がコード変更（コメントアウト）を伴うため、レビューや運用が煩雑。

## 方針・制約
- **IMPORTANT: Do not write overly defensive code. Always prefer simplicity over pathological complexity.**
- なるべく変更点を少なくし、認知負荷が低い仕組みにしたい。
- `main.py` を「構成の一元管理（Composition Root）」として、エージェント選択を明確化するのが望ましい。

## 解決方針候補（検討中）
### 1. エージェント選択方式（推奨）
- `main.py` で「起動するエージェントの一覧」を **環境変数**で明示。
- 例: `ENABLED_AGENTS=quiz_master,invite_role_assigner` なら DailyReporter を登録しない。
- DailyReporter のコードはそのまま、**起動時に登録しない**ことで抑制。
- シンプル・誤爆防止・コード改変最小。

### 2. DailyReporter の自動実行フラグ方式
- `DAILY_REPORTER_AUTORUN=1` の時だけ `on_ready` で投稿。
- テスト時は `0` にして抑制。
- ロジックに分岐を入れる分、若干の複雑性は増える。

### 3. 専用エントリポイント分離
- `python run_daily_reporter.py` など日報専用の起動ファイルを作る。
- `main.py` は常時運用用の「常駐エージェント」だけ起動。
- 最も分離は明確だが、ファイルが増える。

## 判断基準（評価観点）
- **シンプルさ**: 誰が見ても直感的にわかるか。
- **安全性**: 誤投稿が起きない構成にできるか。
- **運用コスト**: 普段の運用が簡単か（env変更だけで済むか）。
- **変更範囲**: 既存コードへの影響が最小か。

## 現時点の考え（暫定）
- **最小の改変で効果が大きいのは「エージェント選択方式」**。
- DailyReporter 本体を弄らずに回避できるため、
  「防御的分岐を増やさない」という方針に沿いやすい。

## 未決事項 / 確認が必要な点
- `ENABLED_AGENTS` を導入した場合の **デフォルト挙動**（未設定時は全起動 or 既定のセット）。
- 既存の GitHub Actions で DailyReporter が自動実行される前提と整合するか。
- ローカル開発での標準設定（`.env` の推奨値）をどうするか。

## 次のアクション候補
- まず `ENABLED_AGENTS` 方式で POC を入れ、実運用で摩擦がないか確認。
- 問題があれば `DAILY_REPORTER_AUTORUN` のような補助フラグを追加する。

## 関連ログ
- `docs/implementation_log_2025-12-19.md`
