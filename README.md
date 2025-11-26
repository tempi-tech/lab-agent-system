# Lab Agent System – Discord Community Agents

このリポジトリは、コミュニティ（特にDiscord）向けの **AIエージェント基盤** です。

AGIラボ / ChatGPT研究所 のコミュニティ運営で実際に使っているボットをそのまま公開しており、  
1つの Discord Bot プロセス上で複数のエージェントを動かせるようになっています。

---

## アーキテクチャ概要

- **`src/core/`**  
  Discord 接続、共通設定、ロギング、ユーティリティなどの基盤コード。

- **`src/agents/`**  
  プラグイン式のエージェント群。  
  各サブディレクトリが 1 つのエージェント機能を表し、共通のインターフェースでボット本体にフックします。

---

## 現在動いているエージェント

### 1. Daily Reporter（ラボちゃん）

- **役割**  
  1日のチャット履歴をもとに、コミュニティ向けの日報を自動投稿します。
  - その日に盛り上がった **トピック**
  - 印象的だった会話や出来事などの **ハイライト**
  - 見落としがちなURLや参考資料への **隠れたお宝リンク**

- **実装場所**  
  `src/agents/daily_reporter/`

<img width="989" height="497" alt="image" src="https://github.com/user-attachments/assets/23ee8dd0-92d8-4523-8442-b8774c406067" />


---

## 必要要件

- Python 3.10 以上
- Discord Bot Token
- Google Gemini API キー

---

## セットアップ

### A. 通常のセットアップ (Python環境)

1. このリポジトリをクローンします。

   ```bash
   git clone <repository-url>
   cd lab-agent-system
   ```

2. 依存ライブラリをインストールします。
   （`pyproject.toml` を使用します）

   ```bash
   pip install .
   ```

3. 環境変数を設定します。
   `.env.example` をコピーして `.env` を作成し、各値を入力してください。

   ```bash
   cp .env.example .env
   ```
   * **Discord Bot Token** や **APIキー** は絶対にコミットしないよう注意してください。

4. ボットを起動します。

   ```bash
   python main.py
   ```

### B. Dockerでのセットアップ (推奨)

Docker環境があれば、コマンド一発で起動できます。

1. `.env` ファイルを作成します（上記手順3と同じ）。

2. Docker Composeで起動します。

   ```bash
### C. GitHub Actionsでの自動実行 (推奨)

サーバーを用意せず、GitHub上で毎日自動実行させることができます。

1. GitHubリポジトリの **Settings > Secrets and variables > Actions** を開きます。
2. 以下の「Repository secrets」を追加します。
   - `DISCORD_TOKEN`: ボットのトークン
   - `DISCORD_CHANNEL_ID`: 投稿先のチャンネルID
   - `SOURCE_CHANNEL_IDS`: 監視対象のチャンネルID（カンマ区切りで複数指定可）
   - `GOOGLE_API_KEY`: GeminiのAPIキー
3. これで毎日 21:00 (JST) に自動的にレポートが投稿されます。
   （`Actions` タブから手動実行も可能です）

---

## 自分用のエージェントを追加するには

このプロジェクトは、まずは AGIラボ内の運用を主目的としてメンテナンスしています。
コードを参考にしたり、フォークして自分の環境向けにエージェントを追加したりする使い方を特に想定しています。

このリポジトリに直接エージェントを追加したい場合の基本パターンは以下です。

1. `src/agents/` 配下に新しいディレクトリを作成します（例: `my_awesome_agent/`）。

2. その中に `__init__.py` を作成し、`get_agent()` 関数でエージェントクラスのインスタンスを返すようにします。

3. エージェントクラスでは、以下のメソッドを任意で実装できます（ダックタイピング）:

   * `async def on_ready(self, client: discord.Client)`
     ボット起動時に1度呼ばれます。

   * `async def on_message(self, message: discord.Message)`
     メッセージ受信時に呼ばれます。

4. `main.py` で新しいエージェントを読み込むように登録します。
   （将来的には自動読み込みにする可能性があります。）

---

## Contributing / コントリビュートについて

このリポジトリは、ChatGPT研究所で使っているボット実装を
「こんな感じで動かしています」という形で公開しているものです。
コードを読んだり、フォークして好きに改造してもらえると嬉しいです。

Issue や Pull Request も歓迎です！

大きめの変更提案の場合は、先に Discord や Issue などで
「こういうことをやりたいです」と提案いただけると嬉しいです！

---

## License

本リポジトリは [MIT License](LICENSE) のもとで公開されています。
