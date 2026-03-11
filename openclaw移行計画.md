# OpenClaw移行計画

作成日: 2026-03-11

## 1. 目的

`lab-agent-system` を今後の主戦場にせず、Mac mini 側の `/Users/kai/.openclaw` 系アーキテクチャへ段階的に移行する。

今回の前提:

- 現時点で本当に残したい機能は `daily_reporter` とロール付与系のみ
- その他の agent は実験色が強く、品質・保守性・設計一貫性の面で移植優先度は低い
- 移行先は「単一 Discord Bot プロセスに機能を追加していく repo」ではなく、`workspace / memory / cron / subagents / logs` を持つ OpenClaw 流の運用環境
- `https://github.com/tempi-tech/lab-agent-system` は public OSS なので、Mac mini 側エージェントの参照元として使いやすい

成功条件:

- OpenClaw 側で `daily_reporter` 相当と role assignment 相当が動く
- 旧システム停止前に、出力品質・権限・ログ・失敗時復旧が確認できる
- `lab-agent-system` は「本番の主系」ではなく「移行元 / 参照元 / 一時保守対象」に降格される

## 2. 結論

方針としては賛成。

ただし、重要なのは「コード移行」ではなく「仕様移植」で進めること。
今の `lab-agent-system` をそのまま延命するより、OpenClaw 側で再設計した方が長期的にはよい。

一方で、以下は避ける:

- GitHub 上の OSS repo を単一の真実の源にする
- 旧 runtime を先に止める
- `daily_reporter` と role assignment を「単純な Python 移植」と見なす
- experimental/slop 部分まで一緒に持っていく

## 3. 現在の棚卸し

### 移植対象にするもの

- `daily_reporter`
- `invite_role_assigner`
- role assignment に必要な membership / config 参照の一部

### 移植対象にしないもの

- `quiz_master`
- `updates_assistant`
- `claude_search`
- `lab_onboarder`
- `operator`
- `question_sla`
- `community_analytics`
- `closed_loop` 系の成果物
- one-off の運用スクリプト群

### 位置づけ変更するもの

- `lab-agent-system`
  - 今後は「移行元 repo」「仕様参照元」「必要最低限の暫定運用」のみ
- GitHub 上の OSS repo
  - 「参照元」ではある
  - ただし「唯一の真実」ではない

## 4. OpenClaw側の前提

確認できた OpenClaw 側の特徴:

- state dir: `/Users/kai/.openclaw`
- config: `/Users/kai/.openclaw/openclaw.json`
- workspace docs: `/Users/kai/.openclaw/workspace/*`
- memory sqlite: `/Users/kai/.openclaw/memory/*.sqlite`
- cron: `/Users/kai/.openclaw/cron/jobs.json`
- logs: `/Users/kai/.openclaw/logs/*`
- subagents: `/Users/kai/.openclaw/subagents/*`

また、既存の移行メモでは「legacy を read-only reference としてぶら下げる」パターンがすでに使われている。

参考:

- `/Users/kai/.openclaw/workspace/MIGRATION_OPENCLAW_FROM_CLAWDBOT.md`

このため、今回も同じ思想を採用する:

- `lab-agent-system` はいきなり捨てない
- OpenClaw workspace から read-only reference として見られる状態を先に作る

## 5. 批判的レビュー

### 5-1. GitHub経由参照の弱点

1. public repo は「見えるもの」しか持っていない

- GitHub には local-only の `.env`、運用時の暗黙知、dirty working tree、未push の修正、Discord 上の実運用知識が入っていない
- そのため、Mac mini 側エージェントが GitHub だけ読んでも「本当に動いている仕様」を完全には再現できない

2. dirty working tree と GitHub がズレている

- 現在の `main` には未コミット変更と未追跡ファイルがある
- GitHub を見せた場合、Mac mini 側はその差分を見落とす
- とくに role assignment まわりはローカル差分が実運用改善の一部になっている可能性がある

3. public repo を単一 truth にすると誤読が固定化される

- README や `.env.example` は実態より古い可能性がある
- `main.py` 登録状況とドキュメントがズレている箇所もある
- その状態で「GitHub が正」とすると、OpenClaw 側に誤仕様が移植される

4. migration docs は放置するとすぐ古くなる

- 一度書いた移行メモが更新されないと、次のセッションでそれ自体が誤情報になる
- 移行期間が長引くほど GitHub / local / OpenClaw docs の三重ドリフトが起きやすい

### 5-2. 機能選定と順序の失敗パターン

1. worth keeping の判定を広げすぎる

- 「せっかくだから他の agent も移す」が始まると失敗しやすい
- 今回は `daily_reporter` と role assignment 以外を原則除外するべき

2. `daily_reporter` を見た目だけで捉える

- 実体は「ソースチャンネル解決」「Discord履歴収集」「Gemini/ADK 要約」「Webhook投稿」「音声生成」「GitHub Actions/--once 運用」が絡む
- 単なる要約 agent として扱うと、移行後に日次運用が壊れる

3. role assignment を単純な event handler と見なす

- 実体は「招待コード」「role hierarchy」「Manage Roles 権限」「sync」「membership config 参照」「失敗時ログ」が絡む
- Discord 側の権限や role order を plan に書かないと、移行先で再発する

4. 仕様移植と言いながら behavioral fixtures を持たない

- 「どう動くべきか」をコードから読んだつもりでも、細かい失敗条件は落ちる
- 実例ログやサンプル入出力がないと、OpenClaw 側で別物になりやすい

5. 旧 runtime を止めるのが早すぎる

- 新系が一応動いた段階で切り替えると、日次ジョブや join event の抜け漏れが表面化する
- 並行運用と比較期間が必要

### 5-3. 運用移行の失敗パターン

1. scheduler の置き換え漏れ

- 現状の `daily_reporter` と membership は GitHub Actions / `--once` 前提がある
- OpenClaw 側では cron / workspace / runtime に合わせた置換が必要
- 「コードは移したが定期実行されない」は典型的な失敗

2. data migration の扱いが曖昧

- SQLite や `data/` のどこを引き継ぐか決めないと、状態が飛ぶ
- 引き継がないなら「ゼロから開始」と明文化する必要がある

3. observability が先送りされる

- 新系で失敗しても、どこで落ちたかわからないとロール付与系は事故になる
- `logs`, `cron`, `memory`, Discord 通知のどこで観測するか先に決めるべき

4. rollback が設計されていない

- 切替後に daily report や role assignment が壊れたとき、どの時点で旧系へ戻すかを決めていないと運用停止になる

## 6. GitHubの使い方

GitHub は使うべき。
ただし役割は限定する。

### GitHubの役割

- Mac mini 側エージェントが読むための公開参照元
- 現行コードの構造確認
- PR / branch / history の参照

### GitHubに期待しないこと

- ローカル未コミット差分の保持
- private knowledge の保持
- migration truth の保持
- 運用仕様の完全表現

### 推奨する参照構成

1. GitHub repo を参照元にする
2. 併せて migration bundle を用意する
3. OpenClaw workspace に read-only reference として保存する

migration bundle に含めるもの:

- `openclaw移行計画.md`
- `daily_reporter_spec.md`
- `role_assignment_spec.md`
- `runtime_inventory.md`
- 必要なら代表ログや入出力サンプル

### 推奨する source of truth

最も安全なのは、以下の3層を分けること:

1. **GitHub public repo**
   - コード参照元
   - Mac mini 側エージェントが読める公開ソース
2. **migration-source ブランチ**
   - Mac mini 側に読ませたいスナップショットを固定するブランチ
   - dirty working tree のうち、移行判断に必要な差分だけ反映する
3. **migration bundle**
   - コードでは表現しきれない運用仕様・暗黙知・サンプルを補う文書群

つまり、**GitHub は使うが、`main` をそのまま唯一の真実にはしない**。
Mac mini 側には原則として `migration-source` ブランチ + migration bundle を読ませる。

## 7. 実行方針

### Phase 0. 凍結宣言

- `lab-agent-system` は新機能開発の本流にしない
- ここでの変更は「移行補助」「暫定保守」「仕様抽出」に限定する

### Phase 1. 参照元整備

- GitHub を OpenClaw 側エージェントの参照元として使う
- ただし GitHub のみにはしない
- `migration-source` ブランチを作り、Mac mini 側に読ませたい状態を固定する
- OpenClaw workspace に read-only migration reference を作る
- ローカル dirty state がある場合は WIP branch または bundle に落とす

成果物:

- `runtime_inventory.md`
- `daily_reporter_spec.md`
- `role_assignment_spec.md`
- この `openclaw移行計画.md`
- 必要なら `dirty_state_notes.md`

### Phase 2. 仕様抽出

#### daily_reporter

抽出するべき仕様:

- 実行タイミング
- ソースチャンネル解決ルール
- メッセージ収集条件
- LLM/Gemini 利用箇所
- 投稿形式
- 音声生成の有無
- 失敗時の挙動

#### role assignment

抽出するべき仕様:

- イベント起点
- invite code 判定
- role 決定ロジック
- Discord 権限前提
- ログ出力
- membership checker との関係
- sync / allowlist / diagnostics

### Phase 3. OpenClaw側で再実装

実装順と切替順は分ける。

#### 実装順

1. `daily_reporter`
2. role assignment

理由:

- `daily_reporter` は shadow run しやすく、OpenClaw 側の cron / logs / memory / Discord 投稿基盤の検証台として使いやすい
- role assignment は失敗時の事故コストが高く、Discord 権限や event 即時性の差分を後から慎重に潰した方が安全

#### 切替順

1. `daily_reporter`
2. role assignment

理由:

- `daily_reporter` は比較運転しやすく、旧系を止めずに品質差分を見られる
- role assignment は最後に切り替えることで、Discord 権限・ログ・rollback が揃った状態で本番に入れる

方針:

- Python モジュールの丸コピーはしない
- OpenClaw の cron / memory / workspace / logs に合わせて再実装する
- 仕様一致を優先し、ファイル配置やクラス構成は合わせない

### Phase 4. 並行運用

- 旧系をまだ止めない
- まずは比較運転
- `daily_reporter` は別チャンネルまたはテスト出力先で比較する
- role assignment は限定条件で dry-run / shadow logging を使う
- role assignment は「観測専用モード」から始め、実付与は最後まで旧系を主系に残す

比較項目:

- daily report の内容差
- 実行時刻
- エラー率
- role assignment の成功率
- permission error の再発有無

### Phase 5. 切替

切替条件:

- role assignment の動作確認完了
- daily_reporter の出力品質と定期実行が安定
- 監視手段がある
- rollback 手順が書かれている

### Phase 6. 降格

- `lab-agent-system` は archive ではなく「migration-source / reference」へ降格
- 必要なら README に以下を明記:
  - long-term home is OpenClaw
  - this repo is not the primary runtime
  - only limited maintenance continues

## 8. Guardrails

必須ガードレール:

1. GitHub を唯一の真実の源にしない
2. `daily_reporter` と role assignment 以外は原則移植しない
3. 旧系停止前に並行運用期間を置く
4. Discord 権限前提を plan に明文化する
5. migration docs は OpenClaw workspace 側にも保存する
6. rollback 条件を先に書く

## 9. 最初の具体アクション

1. `lab-agent-system` の dirty state を退避する
2. `migration-source` ブランチを作る
3. `runtime_inventory.md` を作る
4. `daily_reporter_spec.md` を作る
5. `role_assignment_spec.md` を作る
6. OpenClaw workspace に migration reference を置く
7. Mac mini 側エージェントに `migration-source` + migration bundle を読ませる
8. OpenClaw 側で `daily_reporter` の shadow-run 実装から始める
9. role assignment は dry-run / diagnostics 実装後に限定切替する

## 10. いま決めてよい判断

- OpenClaw 移行方針で進める
- GitHub は参照元に使う
- ただし migration bundle を必須にする
- 旧 repo は本流に戻さない
- 最初の移植対象は `daily_reporter` と role assignment だけに限定する
