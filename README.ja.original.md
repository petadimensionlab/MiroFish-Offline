> **Archived** — this is the pre-rewrite README, kept for reference. Current documentation: [README.md](README.md) (English, measurement-focused) · [README.ja.md](README.ja.md) (日本語).

[English](README.md) | **日本語**

<div align="center">

<img src="./static/image/mirofish-offline-banner.png" alt="MiroFish Offline" width="100%"/>

# MiroFish-Offline

**[MiroFish](https://github.com/666ghj/MiroFish) の完全ローカルフォーク。クラウド API は不要です。英語 UI。**

*世論、市場センチメント、社会的ダイナミクスをシミュレートするマルチエージェント・スウォームインテリジェンスエンジン。すべてあなたのハードウェア上で動作します。*

[![GitHub Stars](https://img.shields.io/github/stars/nikmcfly/MiroFish-Offline?style=flat-square&color=DAA520)](https://github.com/nikmcfly/MiroFish-Offline/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/nikmcfly/MiroFish-Offline?style=flat-square)](https://github.com/nikmcfly/MiroFish-Offline/network)
[![Docker](https://img.shields.io/badge/Docker-Build-2496ED?style=flat-square&logo=docker&logoColor=white)](https://hub.docker.com/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue?style=flat-square)](./LICENSE)

</div>

## これは何か？

MiroFish はマルチエージェント・シミュレーションエンジンです。任意の文書（プレスリリース、政策草案、財務レポート）をアップロードすると、独自の個性を持つ数百体の AI エージェントを生成し、SNS 上の世間の反応をシミュレートします。投稿、論争、意見の変化を、1時間ごとに再現します。

[オリジナルの MiroFish](https://github.com/666ghj/MiroFish) は中国市場向けに作られました（中国語 UI、ナレッジグラフに Zep Cloud、DashScope API）。このフォークはそれを**完全ローカルかつ完全英語**にします:

| オリジナル MiroFish | MiroFish-Offline |
|---|---|
| 中国語 UI | **英語 UI**（1,000 以上の文字列を翻訳） |
| Zep Cloud（グラフメモリ） | **Neo4j Community Edition 5.15** |
| DashScope / OpenAI API（LLM） | **Ollama**（qwen2.5、llama3 など） |
| Zep Cloud のエンベディング | **nomic-embed-text**（Ollama 経由） |
| クラウド API キーが必要 | **クラウド依存ゼロ** |

## ワークフロー

1. **グラフ構築** — 文書からエンティティ（人物、企業、イベント）と関係を抽出します。Neo4j を介して、個人メモリとグループメモリを備えたナレッジグラフを構築します。
2. **環境設定** — 数百体のエージェントペルソナを生成します。それぞれが独自の個性、意見バイアス、反応速度、影響力レベル、過去のイベントの記憶を持ちます。
3. **シミュレーション** — エージェントが模擬ソーシャルプラットフォーム上で相互作用します。投稿、返信、論争、意見の変化。システムはセンチメントの推移、トピックの伝播、影響力のダイナミクスをリアルタイムで追跡します。
4. **レポート** — ReportAgent がシミュレーション後の環境を分析し、エージェントのフォーカスグループにインタビューし、証拠のためにナレッジグラフを検索し、構造化された分析を生成します。
5. **対話** — シミュレートされた世界の任意のエージェントとチャットできます。なぜその投稿をしたのか尋ねてみてください。記憶と個性はすべて保持されます。

## スクリーンショット

<div align="center">
<img src="./static/image/mirofish-offline-screenshot.jpg" alt="MiroFish Offline の英語 UI" width="100%"/>
</div>

## クイックスタート

### 前提条件

- Docker & Docker Compose（推奨）、**または**
- Python 3.11+、Node.js 18+、Neo4j 5.15+、Ollama

### オプション A: Docker（最も簡単）

```bash
git clone https://github.com/nikmcfly/MiroFish-Offline.git
cd MiroFish-Offline
cp .env.example .env

# Start all services (Neo4j, Ollama, MiroFish)
docker compose up -d

# Pull the required models into Ollama
docker exec mirofish-ollama ollama pull qwen2.5:32b
docker exec mirofish-ollama ollama pull nomic-embed-text
```

`http://localhost:3000` を開くだけです。

### オプション B: 手動

**1. Neo4j を起動**

```bash
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/mirofish \
  neo4j:5.15-community
```

**2. Ollama を起動してモデルを取得**

```bash
ollama serve &
ollama pull qwen2.5:32b      # LLM (or qwen2.5:14b for less VRAM)
ollama pull nomic-embed-text  # Embeddings (768d)
```

**3. バックエンドを設定して実行**

```bash
cp .env.example .env
# Edit .env if your Neo4j/Ollama are on non-default ports

cd backend
pip install -r requirements.txt
python run.py
```

**4. フロントエンドを実行**

```bash
cd frontend
npm install
npm run dev
```

`http://localhost:3000` を開きます。

## 設定

すべての設定は `.env` にあります（`.env.example` からコピー）:

```bash
# LLM — oMLX（Apple Silicon 向け MLX 推論サーバー、OpenAI 互換）
LLM_API_KEY=<oMLX の API キー>
LLM_BASE_URL=http://<omlx-host>:8010/v1
LLM_MODEL_NAME=gemma-4-e4b-it-4bit           # bulk: NER / profiles / simulation
LLM_MODEL_NAME_LARGE=Qwen3.8-27B-oQ4e-mtp    # 品質: ontology / config / report
LLM_TIMEOUT_LARGE=1800                       # 27B の LARGE 段階は約10倍遅い

# Neo4j — MiroFish 専用コンテナ（mirofish-neo4j）
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=mirofish
NEO4J_BOLT_PORT=7687
NEO4J_HTTP_PORT=7474

# Embeddings — Ollama を継続利用（oMLX は既定で埋め込みモデルを持たない）
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BASE_URL=http://<ollama-host>:11434
EMBEDDING_API_STYLE=ollama
EMBEDDING_API_KEY=ollama
```

**この fork の並列段階では oMLX が必須バックエンドです。** 単なる好みではなく、パイプラインのスループットと、複数のモデル系統を並列で動かせるかどうかが oMLX に依存します:

- **コンテキストはリクエスト単位で確保。** Ollama は `num_ctx × NUM_PARALLEL` を事前確保（`llama-server -c …`）するため高並列で per-request 速度が崩壊し、本プロジェクトは `-4k`/`-8k`/`-32k` の派生モデルを作る必要がありました。oMLX にはこの事前確保が無く、**派生 `-4k`/`-8k`/`-32k` モデルは不要**です。ただし `LLM_MODEL_NAME*` は**モデル能力での段階分け**として引き続き使用します（バルク = `gemma-4-e4b`、品質重視段階 = より大きなモデル）。
- **並列化できるモデルが多い。** Ollama は `qwen35` アーキテクチャの並列化を拒否（`-np 1`）しますが、oMLX は受理します → [oMLX での qwen 並列化（実測）](#omlx-での-qwen-並列化実測)。
- **同一ホストでの実測**（Mac Studio M1 Ultra / 128 GB、実 profile 形状、n=8〜16）: oMLX **120.6–164.5 tok/s** に対し Ollama **58–77 tok/s**。

Ollama は**埋め込み専用**（`nomic-embed-text`、768次元）として残しています。任意の OpenAI 互換チャット API にも `LLM_BASE_URL` / `LLM_API_KEY` の変更で差し替え可能です。エンベディングにはさらに `EMBEDDING_API_STYLE` が必要です（後述）。

### oMLX バックエンド（必須）

oMLX は Apple Silicon 向けの MLX 推論サーバーです。API は FastAPI なので `GET /openapi.json` で全エンドポイントを列挙でき、管理 UI は `/admin` にあります。

```bash
# 1. インストール（Homebrew tap）。--with-custom-kernel は Bonsai / GLM-5.2 /
#    MiniMax M3 / Qwen3.5-3.6 のネイティブカーネル（任意）
brew tap jundot/omlx && brew install omlx
brew reinstall omlx --with-custom-kernel     # これらのモデルが必要な場合のみ

# 2. ネットワークに bind（既定は 127.0.0.1 で他ホストから見えない）
#    ~/.omlx/settings.json:  "host": "0.0.0.0",  "port": 8010
omlx restart            # または: omlx serve --host 0.0.0.0 --port 8010

# 3. 管理 API キーを設定（未設定の間は推論 API が無認証になる）
curl -X POST -H 'Content-Type: application/json' \
     -d '{"api_key":"<key>","api_key_confirm":"<key>"}' \
     http://<omlx-host>:8010/admin/api/setup-api-key

# 4. 管理 API 経由で MLX モデルを取得（SSH 不要）
curl -X POST -H "Authorization: Bearer <key>" -H 'Content-Type: application/json' \
     -d '{"repo_id":"mlx-community/gemma-4-e4b-it-4bit"}' \
     http://<omlx-host>:8010/admin/api/hf/download     # 5.18 GB、約100秒
```

主なエンドポイント: `GET /api/status`（公開カウンタ、`active_requests`）、`GET /v1/models/status`、`GET /admin/api/models`、`GET /admin/api/global-settings`（`max_concurrent_requests`）、`POST /admin/api/server/restart`。

> **oMLX は Ollama ではありません。** `gemma4:e4b` は **GGUF** タグなので oMLX では読み込めません。MLX safetensors が必要で、同じモデルの MLX 版 `mlx-community/gemma-4-e4b-it-4bit` を使います。モデルIDはリポジトリ名の `/` が `--` に置換されます（例: `mlx-community/gemma-4-e4b-it-4bit` → `gemma-4-e4b-it-4bit`）。

#### モデル互換性（oMLX 0.6.4、Mac Studio M1 Ultra / 128 GB で実測）

| MLX モデル | `config.json` の `model_type` | ロード | aggregate（実形状） |
|---|---|---|---|
| `mlx-community/gemma-4-e4b-it-4bit` | `gemma4` | **可** | 76.0 → 120.6 → **164.5** tok/s（n=1/8/16） |
| `mlx-community/Qwen3.5-9B-MLX-4bit` | `qwen3_5` | **可** | 50.7 → 75.0 → 75.3 tok/s |
| `pipenetwork/Ternary-Bonsai-2-27B-MLX-4bit` | `qwen3_5`（4-bit affine） | **可** | 21.3 → 23.8 tok/s（n=8、ほぼスケールせず） |
| `Jundot/Qwen3.8-27B-oQ4e-mtp` | `qwen3_5`（`mtp_compatible`） | **可** | 18.5–20.0 → 22.9 tok/s（n=8、ほぼスケールせず） |
| `prism-ml/Ternary-Bonsai-2-27B-mlx-2bit`（公式 ternary） | `prism_hadamard_qwen35` | **不可** | ロード拒否 |
| `inductiveML/Ternary-Bonsai-2-27B-mlx-lossless-1.75bpw` | `ternel_hadamard_qwen35` | **不可** | ロード拒否 |

- 公式の **Bonsai 2 27B ternary 2-bit** ビルド（2026-09-16 公開）は oMLX 0.6.4 では**ロードできません**:
  `Model type prism_hadamard_qwen35 not supported. Error: No module named 'mlx_vlm.speculative.drafters.prism_hadamard_qwen35'`（HTTP 409）。
- **カスタムカーネルを再ビルドしても解決しません。** `--with-custom-kernel` は旧 Bonsai 系
  （Qwen3.6 ベースの `Ternary-Bonsai-27B` / `Bonsai-8B`）向けで、Hadamard パックには
  `prism_hadamard_qwen35` のローダが必要ですが、oMLX 0.6.4 はそれをどこにも登録していません
  （ソース中に文字列が存在しません）。上流では issue
  [#3768](https://github.com/jundot/omlx/issues/3768) /
  [#3727](https://github.com/jundot/omlx/issues/3727)、PR
  [#3734](https://github.com/jundot/omlx/pull/3734) /
  [#3782](https://github.com/jundot/omlx/pull/3782)（メンテナ作、2026-09-21）が**未マージ**です。
  なお本インストールには任意のカスタムカーネルも入っていません（`custom_kernels.bonsai.available = false`）が、
  これは別の問題でブロッカーではありません。oMLX が対応している Bonsai モデルでは、カーネルは
  **速度のみ**の経路です（純 MLX フォールバックは正しく動きますが大幅に遅くなります）。
- **動作する回避策:** `config.json` が標準の `qwen3_5` を宣言する MLX 変換版
  （例: `pipenetwork/Ternary-Bonsai-2-27B-MLX-4bit`、16.07 GB）はロード・生成できます。
  ただし aggregate は約 24 tok/s で、同一ワークロードで `gemma-4-e4b-it-4bit` の約7倍遅いです。
- **実務ルール:** ダウンロード前に `config.json` の `model_type` を確認してください。
  `gemma4` と `qwen3_5` はロード可、`*_hadamard_*` の ternary 形式は現状ロード不可です。
  （`gemma4:e4b` のような Ollama/GGUF モデルは原理的に不可。oMLX は MLX safetensors が必要です。）
- **27B クラスは帯域律速で、並列が効きません。** 上の 27B ビルドはすべて aggregate 約 19–24 tok/s で、
  n=8 でもほとんど伸びず（1.1–1.15倍）、**`gemma-4-e4b-it-4bit` の約7〜8倍遅い**という結果です。
  `mtp_compatible` は**効きませんでした**: `mtp_enabled` / `vlm_mtp_enabled` / `mtp_num_draft_tokens` を
  有効化しても速度は不変（19.0 → 18.5 tok/s）で、MTP 関連のログ出力もありませんでした。
  157エージェントの prepare は **約1時間40分**（`gemma-4-e4b-it-4bit` は約13分）に相当します。
  → バルク段階（profiles / simulation）は小型モデル、精度重視段階
  （ontology / config / report = `LLM_MODEL_NAME_LARGE`）に 27B を割り当てる使い分けを推奨します。

### 専用の Neo4j

MiroFish には専用の Neo4j インスタンスが必要です。Neo4j Community Edition は単一のデータベースしかサポートしないため、他のプロジェクトと共有できません。同梱のコンテナを起動します:

```bash
docker compose up -d neo4j
```

ポート `7474`/`7687` が別の Neo4j で既に使われている場合は、ホストポートを再マッピングし、`NEO4J_URI` を合わせて更新します:

```bash
NEO4J_HTTP_PORT=7475
NEO4J_BOLT_PORT=7688
NEO4J_URI=bolt://localhost:7688
```

完全 Docker モード（`docker compose up -d`）では、`docker-compose.yml` がバックエンドを名前で `neo4j`/`ollama` サービスに向けます。上記のホストポート値は、バックエンドがホスト上で動作する場合（`npm run backend`）にのみ適用されます。

## パフォーマンスと並列性

**oMLX が本番バックエンドです。** サーバー側の並列度は `scheduler.max_concurrent_requests`、クライアント側は下記の段階別ノブで制御します。コンテキストはリクエスト単位で確保されるため `-c` の崩壊も派生 `-4k`/`-8k`/`-32k` モデルも不要です。`LLM_MODEL_NAME`（バルク）と `LLM_MODEL_NAME_LARGE`（ontology / config / report）は**モデル能力での段階分け**に使います。

| 設定 | 場所 | 本番値 |
|---|---|---|
| `max_concurrent_requests` | oMLX `global-settings` | **32**（余裕枠。飽和点を超えるとレイテンシのみ増加） |
| `--parallel-profiles` | クライアント（`sim-prepare`） | **16**（長出力の実測飽和点） |
| `CONFIG_BATCH_PARALLEL` | `.env` | 8 |
| `GRAPH_BUILD_PARALLEL` | `.env` | 4 |
| `OASIS_SEMAPHORE` | `.env` | 16 |

Mac Studio M1 Ultra / 128 GB、`gemma-4-e4b-it-4bit`（約 2.1k トークン prompt / 900 トークン出力）での実測: **n=1 76.0 · n=8 120.6 · n=16 164.5 · n=32 165.0 tok/s** — aggregate は **n≈16 で飽和**（それ以上はレイテンシのみ増加）。157エージェントの `sim-prepare --parallel-profiles 16` は **16分21秒**（12.03 profiles/min、`active=16` / `waiting=8` で宣言どおり）、`scripts/verify_outputs.py` は **17/18 PASS**（唯一の FAIL は未実行の `simulation.db`）。

### 旧: Ollama バックエンド

Ollama はチャット用バックエンドではなくなりました（**埋め込み**エンドポイント用とフォールバックとして残しています）。並列推論をサポートしますが、既定では `OLLAMA_NUM_PARALLEL=1` で、リクエストが直列化されます。シミュレーションは多数のエージェント呼び出しを同時に発行するため、この値を引き上げてください。128 GB の Apple Silicon マシン向けの推奨既定値（[ベンチマーク結果](#benchmark-results-apple-m3-max-128-gb) を参照）:

```bash
OLLAMA_NUM_PARALLEL=128       # parallel slots per model (llama.cpp hard ceiling: 256)
OLLAMA_MAX_LOADED_MODELS=2    # keep chat + embedding models resident (do not set 1)
OLLAMA_MAX_QUEUE=256          # queue depth before requests are rejected with HTTP 503
OLLAMA_KEEP_ALIVE=30m         # keep the model resident across the run
OLLAMA_CONTEXT_LENGTH=4096    # per-request context for the bulk of the steps
OLLAMA_NUM_CTX=4096           # per-request context used by the LLM client
OLLAMA_NUM_CTX_ONTOLOGY=65536 # ontology generation only (may feed up to 50,000 chars)
```

Ollama は `num_ctx × NUM_PARALLEL` トークンを合計で割り当て、ランナーを `-c <total> -np <NUM_PARALLEL>` として起動します。メモリコストはモデルのアテンション方式によって異なります。スライディングウィンドウ型のモデル（例: Gemma 4）はほぼ一定（`NUM_PARALLEL=128` でも約 17 GiB）ですが、フルアテンション型のモデル（例: Bonsai）は `num_ctx` に比例して増加します。

- **Docker:** これらの値は `docker-compose.yml`（`ollama` サービス）によって `.env` から読み込まれます。
- **ローカル（macOS）:** Ollama のメニューバーアプリはシェル環境変数を無視し、その launchd エージェントが `-np 1` でサーバーを再生成します。`scripts/start_all.sh` と `scripts/run_cli.sh` は自動的にそれを終了/停止し（`launchctl bootout`）、上記の値を尊重するサーバーを起動します。単独で行うには `scripts/start_ollama_parallel.sh` を実行します。
- **Apple Silicon:** GGUF モデルを使用し、`*-mlx` タグは**使わないでください**。Ollama の MLX エンジンはリクエストを1つずつ処理するため、`OLLAMA_NUM_PARALLEL` は効果がありません。一部の MoE アーキテクチャも `parallel=1` を強制します。スループットが改善しない場合は Ollama サーバーログを確認してください。

### ベンチマーク結果（Apple M3 Max、128 GB）

Apple M3 Max / 128 GB 上で、64 トークン生成を N 並列で発行し、集約スループット、中央値レイテンシ、ロード済みサイズを記録して測定しました。

**1-bit Bonsai（`digitsflow/bonsai-8b`、`num_ctx` 8192）**

| NUM_PARALLEL | 集約 | p50 レイテンシ |
|---|---|---|
| 4 | 39.5 tok/s | 4.8 s |
| 8 | 41.6 tok/s | 8.8 s |

Bonsai は約 40 tok/s で頭打ちになり、スロットを増やしてもレイテンシが増えるだけです。

**Gemma 4（`gemma4:e4b`、派生 `gemma4-4k`、`num_ctx` 4096）**

| NUM_PARALLEL | 集約 | p50 レイテンシ | ロード済み |
|---|---|---|---|
| 2 | 34.6 tok/s | 3.7 s | 9.03 GiB |
| 4 | 41.5 tok/s | 6.2 s | 9.19 GiB |
| 8 | 50.5 tok/s | 10.1 s | 9.54 GiB |
| 16 | 140.8 tok/s | 7.3 s | 11.02 GiB |
| 32 | 247.7 tok/s | 8.2 s | 13.09 GiB |
| 64 | 305.4 tok/s | 13.4 s | 17.23 GiB |
| 128 | 270.3 tok/s | 29.7 s | 16.07 GiB |
| 256 | 292.6 tok/s | 53.9 s | 17.30 GiB |
| 512 | **失敗** | — | — |

**所見**

- スループットは **約 300 tok/s** で飽和します。約 128 スロットを超えると伸びはわずかになり、リクエストごとのレイテンシが増加します。総作業量がメモリ帯域に律速されるためです。**`NUM_PARALLEL=128` が推奨既定値です。**
- **256 が絶対的な上限です。** 512 は常に失敗します。llama.cpp が `n_seq_max must be <= 256` で拒否します。
- **モデルの選択が最も重要です。** このマシンでは、高い並列度で 1-bit Bonsai は Q4_K_M Gemma 4（約 300 tok/s）よりも*遅く*（約 40 tok/s）なります。1-bit カーネルは Metal 上ではまだ成熟していません。
- メモリ: Gemma 4 は NP=8 から NP=256 まで約 16〜17 GiB にとどまります（スライディングウィンドウ KV）。Bonsai のフルアテンション KV は `num_ctx` に比例して増加するため（例: `num_ctx=65536` × NP=8 → 約 75 GiB）、高い並列度では `num_ctx` を小さく保ってください。
- **Prepare の並列度**は `OLLAMA_NUM_PARALLEL` ではなく `parallel_profile_count`（既定 5）によって制限されます。スロットを実際に使うには引き上げてください: `resume`/`sim-prepare`/`pipeline` で `--parallel-profiles 128`。

**スロットごとのコンテキストを制御する。** Ollama はモデルの `num_ctx` をスロットごとのコンテキストとして使用します。モデルを派生させて固定します:

```bash
ollama show --modelfile gemma4:e4b > /tmp/m.modelfile
printf '\nPARAMETER num_ctx 4096\n' >> /tmp/m.modelfile
ollama create gemma4-4k -f /tmp/m.modelfile
```

### vLLM（およびその他の OpenAI 互換バックエンド）

vLLM は高い並列度で大幅に優れたスループットを提供しますが、**ネイティブの macOS/Apple Silicon バックエンドがありません**。Linux 上で NVIDIA（CUDA）または AMD（ROCm）で動作します。そのようなマシンがあれば、アプリは設定だけでチャットに、追加の設定1つでエンベディングに使用できます:

```bash
LLM_BASE_URL=http://gpu-host:8000/v1
LLM_MODEL_NAME=<served-model-name>
OPENAI_API_BASE_URL=http://gpu-host:8000/v1

EMBEDDING_BASE_URL=http://gpu-host:8000/v1
EMBEDDING_API_STYLE=openai
EMBEDDING_MODEL=<served-embedding-model>
```

`EMBEDDING_API_STYLE=auto`（既定）は、`EMBEDDING_BASE_URL` がポート 11434 を指す場合は Ollama ネイティブの `/api/embed` を使用し、それ以外の場合は OpenAI 互換の `/v1/embeddings` を使用します。エンベディングモデルは Neo4j のベクトルインデックスに合わせて **768 次元**のベクトルを生成する必要があります。別の次元を使う場合は `neo4j_schema.py` と `embedding_service.py` の `VECTOR_DIMENSION` を更新し、グラフを再構築する必要があります。GPU サーバーがない場合、Apple Silicon では `OLLAMA_NUM_PARALLEL` のチューニングが現実的な手段です。

## コマンドラインインターフェース

Web UI は REST API 上の薄いクライアントなので、ワークフロー全体をターミナルから `scripts/mirofish_cli.py` で操作できます（Python 3.11 以降、標準ライブラリのみ）。

**Web UI なしで、単一のターミナルから**実行するには `scripts/run_cli.sh` を使用します。Neo4j が起動していることを確認し、並列設定で Ollama を再起動し、バックエンドを起動し、渡した CLI コマンドを実行し、終了時にバックエンドを停止します（Ollama は実行したままになります）:

```bash
./scripts/run_cli.sh pipeline --requirement "..." --file press_release.pdf --max-rounds 50 --report-out report.md
./scripts/run_cli.sh sim-status --simulation-id sim_xxxx
./scripts/run_cli.sh --help
```

代わりに GUI を使う場合は `scripts/start_all.sh` を使用します（http://localhost:3000 で Web アプリを開きます）。

バックエンドがすでに実行中の場合（`npm run backend`）は、CLI を直接呼び出します:

```bash
python scripts/mirofish_cli.py projects                      # newest first, readable table
python scripts/mirofish_cli.py project --project-id proj_xxxx
python scripts/mirofish_cli.py resume --index 3              # show the next step for row #3
python scripts/mirofish_cli.py resume --index 3 --run        # run remaining steps until done
python scripts/mirofish_cli.py resume --index 3 --run --once # run just one step
```

新しいプロジェクトは文書名とタイムスタンプから自動命名されるため（例: `paper (2026-09-13 09:56)`）、後で見つけやすくなっています。`resume` はプロジェクト、そのシミュレーション、レポートを調べ、次のコマンドを表示します。`--run` を付けると、レポートが完成するまで残りのパイプラインステップ（build → prepare → simulate → report）を実行します。

```bash
# End-to-end: ontology -> graph build -> prepare -> simulate -> report
python scripts/mirofish_cli.py pipeline \
  --requirement "How will the public react to this policy?" \
  --file press_release.pdf --max-rounds 50 \
  --report-out report.md

# Individual steps
python scripts/mirofish_cli.py ontology --requirement "..." --file doc.pdf
python scripts/mirofish_cli.py build --project-id proj_xxxx
python scripts/mirofish_cli.py sim-create --project-id proj_xxxx
python scripts/mirofish_cli.py sim-prepare --simulation-id sim_xxxx
python scripts/mirofish_cli.py sim-start --simulation-id sim_xxxx --follow
python scripts/mirofish_cli.py report-generate --simulation-id sim_xxxx
python scripts/mirofish_cli.py interview --simulation-id sim_xxxx --agent-id 0 --prompt "Why?"
python scripts/mirofish_cli.py report-chat --simulation-id sim_xxxx --message "Summarize the sentiment"
```

`--base-url`（または `MIROFISH_API_BASE`）はバックエンドを指します。`--json` は生の JSON を出力します。`--help` はすべてのコマンドを一覧表示します。`--timeout` は HTTP リクエストのタイムアウト、`--max-wait` はステップごとのポーリング上限（既定 7200 秒）です。遅いローカルモデルでは `--max-wait` を引き上げてください。

## 運用方針（まず読む。すべてのルールより、このシンプルな方針を優先する）

このプロジェクトの失敗は、細かいルールを欠いていたことによるものではほぼありません。**このシンプルな方針を適用しなかったこと**が原因です。迷ったときは、以下の長いリストではなく、この方針に従ってください。

1. **モデル: まず gemma4:12b**（精度）。e4b は速度が目的のときだけ。
2. **コンテキスト: 既定では小さく保つ。** 必要なコンテキストはステージごとに異なるため、*概ね十分*な小さめの `num_ctx` を選びます（バルク = 8192。NER、プロファイル、シミュレーションをカバー）。本当に大きくする必要があるステージ（ontology、simulation_config、そして**最終レポート/PDF**）だけ、大きい値（32768）を使います。パイプライン全体を1つの大きなコンテキストで回さないでください。
3. **並列で実行する。** 小さいコンテキストなら、`NP`（サーバースロット）と `C`（クライアント並列度）を実際に効かせ、ランナー引数を確認してください。
4. **失敗から回復できるようにする。** 保存済みの成果物から再開し、進捗ファイルはアトミックに書き、サーバーとバックエンドはそれぞれ1つだけにします。失敗によって完了済みの作業を失ったり、最初からやり直しになったりしてはなりません。
5. **ルールを増やすより、このシンプルな方針を優先する。** ルールを追加したくなったら、まずこのシンプルな方針がすでにそれをカバーしているか確認してください。

### 特性: エージェントはこのシンプルな方針を理解しない

繰り返し観測されています。エージェントは詳細なルールを読んでも、1つの大きなコンテキストで実行し、並列性を潰し、失敗で作業を失い、計測されたレートなしに何時間も盲目的に走らせます。これが本当の欠陥です。**シンプルな方針を知らない / 適用していない**のであり、エージェントの*システム特性*であって、一度きりのバグではありません。

- 症状: すべてを1つの大きな ctx で実行 / 並列性が無効化される / クラッシュで全損 / 計測されたレートなしの数時間ラン。
- 行動する前に確認する: *次の行動は（小さい ctx）+（並列）+（回復可能）を満たすか？* 満たさないなら、まずそれを直してください。
- 以下の詳細な失敗ログは、この方針を例示するためのものであり、置き換えるためのものではありません。

### 特性: エージェントは問題に気づかず、放置する

繰り返し現れるもう1つのエージェントの欠陥です。実際の異常が目の前にあっても気づかず、対処しないため、壊れている/遅い実行が「問題なし」と見なされ、その上に積み重ねられます。

実際に観測された例:
- モデルはプロファイルごとに約 **1,810 トークン**を生成したのに、保存されたのは約 **850** だけでした（約50%が thinking で浪費）。`n_gen`/`completion_tokens` と保存された出力長を比較した者は誰もいませんでした。
- `NP=16` と宣言したのにランナーは `-np 4` でした（さらにアプリサーバーがポートを奪っていました）。宣言値と実測値を比較した者は誰もいませんでした。
- 観測されたトークンレートから示唆される値よりはるかに大きい、リクエストごとのレイテンシ。この不整合は調査されませんでした。

**必要な行動:** 実行のたびに*期待値 vs 実測値*を能動的に比較してください。生成トークン vs 保存トークン、宣言した並列度 vs ランナー引数、観測レイテンシ vs `tokens / rate`。そして、続行する**前に**あらゆるズレを調査してください。「妙に見える数値」は背景ノイズではなく、説明すべき欠陥として扱ってください。

### 特性: エージェントは素過程のレベルで失敗する（基本が雑）

ここでの失敗は戦略の失敗では**ありません**。**素過程**の不注意な実行でした: コンテキストのサイジング、ポートの衛生、書込みのアトミック性、正しい指標の計測、ランナー引数の確認、異常への気づき。基本が雑だったため、どれほど高度な計画も実行を救いませんでした。

- すべての素過程について、一度だけ書かれたトレースが [`docs/process-trace-2026-09-14.md`](docs/process-trace-2026-09-14.md) に保管されています。これを読み、最新に保ってください: **書き残されていない素過程は、必ず間違って繰り返されます。**
- 必須: 各素過程を第一級の成果物として扱い、厳密に実行し、（期待値 vs 実測値で）検証し、記録してください。

## 運用ツールとスキル

MiroFish をローカルで実行するのは、長時間かつ失敗しやすい作業です（LLM のタイムアウト、切り詰められた JSON、停滞）。リポジトリにはヘルパースクリプトが同梱されており、**監視、エラー検出、レポート**のためにいくつかのエージェントスキルに依存しています。

### ヘルパースクリプト

| スクリプト | 用途 |
|---|---|
| `scripts/run_cli.sh` | 1つのターミナル、GUI なし: Neo4j を確認し、調整済み環境で Ollama を再起動し、バックエンドを起動し、CLI コマンドを実行し、終了時にバックエンドを停止します。 |
| `scripts/start_all.sh` | Web UI を使う1つのターミナル（Neo4j + Ollama + バックエンド + フロントエンド）。 |
| `scripts/stop_all.sh [--ollama] [--neo4j] [--all]` | バックエンド/フロントエンドを停止します（任意で Ollama や Neo4j も）。 |
| `scripts/start_ollama_parallel.sh` | 並列設定で Ollama をフォアグラウンドで再起動します。 |
| `scripts/lib_ollama.sh` | ランチャーが Ollama を正しく（再）起動するために使う共有ヘルパー（macOS アプリ/エージェントを終了し、調整用の環境変数をエクスポート）。 |
| `scripts/monitor.sh [interval]` | ステータススナップショット（プロファイル数、ランナーの `-c/-np`、メモリ、CLI 進捗行）を N 秒ごと（既定 60）に `/tmp/mirofish-monitor.log` に追記します。 |
| `scripts/supervise.sh` | ステージ対応スーパーバイザー: **複数**の進捗シグナル（プロファイル数、設定生成の進捗、成果物、CLI 行）を監視し、生成中の通常のエラー/リトライを許容し、**真の停滞時のみ再起動**し（完了済みの作業を再利用）、prepare が完了すると停止します。 |
| `scripts/verify_outputs.py` | シミュレーション向けの固定された出力検証チェックリスト: 成果物の存在、JSON の妥当性、必須/一意/連続の id、config↔profile の id 一致、切り詰め/フォールバック/タイムアウトの件数、リクエストごとの速度。FAIL 時は非ゼロで終了します。 |

### 出力検証チェックリスト（成功を主張する前に実行する）

プロセスの終了は証拠になりません。`python scripts/verify_outputs.py --simulation-id sim_xxxx` を実行し、PASS/FAIL のリストを示してください。完全なチェックリスト（`mirofish-output-verification` というスキルでもあります）:

| 領域 | チェック |
|---|---|
| A. 成果物 | profiles / config / simulation の `.db` が存在しパースできる。`.corrupt.bak` が残っていない |
| B. profiles | 有効な JSON。`user_id` が一意で**かつ `0..N-1` で連続**。必須フィールドが空でない。名前が一意。ペルソナ長の中央値が妥当 |
| C. config | `agent_configs` の件数 == profiles。`time_config`/`event_config`/`llm_model` が存在。**`{agent_id}` == `{profile user_id}`** |
| D. 品質 | `LLM output truncated` が少ない。`using rule-based generation` ≈ 0。`Request timed out` == 0 |
| E. 速度 | リクエストごとの tok/s（Ollama ログの `eval time` から）× **アクティブスロット** = 集約。観測された完了レートと照合する。ETA = `tokens_per_unit × N / aggregate` |

**ルール:** 常に*宣言した並列度* vs *アクティブスロット* vs *観測レート*を照合してください。リクエストごとのレート × 並列度が観測レートを大きく上回るなら、別の何か（prefill / キューイング / リトライ / スワップ）が支配しています。報告する前に診断してください。FAIL の修正には（測定値という）証拠が必要であり、「問題なさそう」では不十分です。

### 監視 / エラー検出 / レポート用のスキル

| スキル | 用途 |
|---|---|
| `operational-guardrails` | 長時間作業のための行動ルール: 一方的な停止/再起動を行わない、指示に文字通り従う、行動前に証拠に基づいて診断する、複数シグナルによる停滞検出、非破壊的な復旧、簡潔なマイルストーン報告。 |
| `debugging` | 仮説駆動のランタイムデバッグ（3つ以上の仮説、並行調査、根本原因の確認、失敗するテストでの固定、最小限の修正、システムを使った検証）。 |
| `verification-planning` | 非自明なシステムを変更する前に、主張に対するプロジェクト固有の証拠経路を構築します。 |
| `reflect` | 最近の作業を振り返り、繰り返し発生する摩擦（例: 監視ミス）を再利用可能なスキルや設定に変えます。 |
| `oh-my-opencode-slim` | ワークフローの摩擦が繰り返される場合に、エージェント/モデル/プロンプトを調整します。 |

### 典型的な監視付き実行

```bash
# terminal 1: supervisor (non-destructive restart only on a real stall)
./scripts/supervise.sh
# terminal 2 (optional): periodic snapshots
./scripts/monitor.sh 60
tail -f /tmp/mirofish-supervise.log    # stall / restart events
tail -f /tmp/mirofish-resume.log       # job progress
```

ツールが従うルール: `LLM output truncated`、`JSON parsing failed (attempt N)`、`Request timed out`、`using rule-based generation` といった生成中のエラーは**正常**であり、停滞とは見なしません。停滞とは、停滞ウィンドウの間、**いずれの進捗シグナルにも変化がない**ことを指します。復旧は、完了済みの成果物を再生成せずに再利用します。

### ステップの所要時間、ETA、異常に基づく修正

ただ待つだけにしないでください。各ステップを計測し、遅いものに対処します:

1. **ステップごとに計測する** — 連続する進捗行のタイムスタンプ（例: `[3/13] -> [4/13]`）を取り、差分を計算します。
2. **完了を推定する** — 残りステップ数 × 観測された平均（ステップ種別ごと）。
3. **異常を検出する** — 中央値の約2倍以上（またはハード上限を超える）かかるステップは、単に遅いのではなく疑わしいです。
4. **すぐに調査する** — そのステップのログ行（出力の切り詰め、JSON パース失敗、タイムアウト、リトライ、ルールベースへのフォールバック、接続エラー）を読み、CPU/メモリ/スワップを確認し、根本原因を見つけます。
5. **修正して検証する** — 修正（設定またはコード）を適用し、非破壊的に再実行し（完了済みの成果物を再利用）、ステップの差分が改善したことを確認します。

この方法で得られた既知の運用上の教訓:

- **設定生成の切り詰め。** `simulation_config_generator` は当初、リクエストごとのコンテキストを送信していなかったため、Ollama はモデルの既定値（4096）を使用し、大規模なマルチエージェント JSON 出力が切り詰められ（`finish_reason=length`）、パースに失敗し、リトライの嵐を引き起こしました。修正: `OLLAMA_NUM_CTX_CONFIG`（既定 16384）によるリクエストごとのコンテキストと `LLM_TIMEOUT`。
- **リクエストごとのタイムアウトと並列度。** 高い並列度では、単一のプロファイルがクライアントのタイムアウトを超えることがあります。短めのタイムアウトとリトライ、および適度な `--parallel-profiles` を使うことで、バッチ全体がブロックされるのを避けられます。
- **ノート PC の蓋を閉じる = スリープ。** ノート PC を閉じるとジョブが中断されます（スワップを引き起こすこともあります）。`caffeinate` は蓋を閉じることによるスリープを防ぎません。
- **スーパーバイザーの誤検知。** 単一のカウンタ（保存済みプロファイル）に依存するスーパーバイザーは、設定フェーズ中に誤って再起動しました。複数の進捗シグナルを使用し、ガード対象フェーズが完了したら監視を停止してください。
- **プロセスの衛生。** 各チェック時に、必要なプロセス（`ollama serve`、`llama-server`、バックエンド、CLI 実行、スーパーバイザー、モニター）を数え、重複、孤立、残存プロセスを直ちに停止します。プロセスごとのメモリを監視し、大きなランナーがマシンをスワップに追い込まないようにしてください。

### 運用障害ログ（繰り返さないこと）

| 症状 | 根本原因 | 必要な行動 |
|---|---|---|
| 再起動で進捗を失った | 一方的な停止/再起動 | 明示的な承認を得る。成果物を再利用する。コストを明示する |
| 「再開」/「残り N から」が「最初から」になった | 指示の再解釈 | 指示に文字通り従う。曖昧なら一度だけ確認する |
| NUM_PARALLEL が効かず直列化された | macOS の Ollama アプリ/launchd が `-np 1` で `ollama serve` を再生成 | アプリを終了/bootout する。サーバーは1つ。ランナーの `-np` を確認する |
| 4096 でも切り詰められる | Ollama の OpenAI エンドポイントが `extra_body options.num_ctx` を無視 | モデルの `num_ctx` を設定する（派生モデル）。ランナーの `-c` を確認する |
| 数分かかる設定ステップ、リトライの嵐 | 出力の切り詰め + JSON パース失敗 | 実効コンテキストを引き上げる。予算超過のステップを直ちに修正する |
| 設定中にスーパーバイザーが再起動した | 単一カウンタによる停滞検出 | 複数シグナルの進捗。ガード対象フェーズ後に監視を停止する |
| 再起動ですべてのプロファイルが再生成された | 生成に再開機能がなかった | 保存済みプロファイルを再利用する（`user_id` による） |
| 負荷時に完了しない | 並列度とクライアントタイムアウトの競合 | 短いタイムアウト + リトライ。適度な並列度 |
| 実行中にジョブが一時停止する | ノート PC の蓋を閉じた（スリープ）。スワップを引き起こすことがある | 蓋を開けたままにする / 電源+ディスプレイを接続する。`caffeinate` は蓋を閉じることによるスリープを防がない |
| スループットの崩壊 | スワップスラッシング（未使用 RAM がほぼ 0） | スワップをほぼ 0 に保ち余裕を持たせる。他の消費者を解放する。`vm.swapusage` を監視する |
| 並列度 256 超で失敗する | llama.cpp の `n_seq_max <= 256` | 256 を上限とする。実用的な既定値は 128 |
| メモリの浪費 / 競合 | 重複または孤立プロセス | 各サイクルで数を監査する。重複/孤立プロセスを kill する |
| 停止スクリプトがバックエンドを捕捉できなかった | プロセス名の不一致 | 実際のコマンドラインに一致させる。停止後にポートを確認する |
| 証拠なしに修正を主張した | 未検証の仮定 | 実際のランナー引数/成果物を確認する |
| アイドル状態の待機 | 長時間のブロッキングスリープ | 頻繁に確認して行動する |
| 再起動で完了済みステップをやり直した | 部分永続化がない | ステップ/バッチごとの進捗を永続化する（`simulation_config.partial.json`）。未完了の項目だけを再生成する |
| 再開状態が失敗/破損した | dataclass がシリアライズ不可。非アトミック書込み | `asdict` + アトミック書込み（temp + `os.replace`）。partial ファイルがパースできることを検証する |
| 長時間の実行中にスーパーバイザーが再起動した | 停滞ウィンドウがステップより短い。活動シグナルがない | ウィンドウを最長ステップより上に設定する。LLM サーバーログ/CPU 活動を進捗シグナルに含める |
| 確認せずに待った | 受動的なスリープ、ステータス未確認 | 待つたびにプロセス + ステータスAPI + 最新の成果物/ログを確認する。進捗が確認できなければ調査する |
| 特定モデルで NUM_PARALLEL が効かなかった | Ollama: `architecture does not support parallel requests`（qwen35）→ ランナーが `-np 1` を強制 | NP を調整する**前に**この警告がないか Ollama ログを確認する。本番では並列対応モデル（gemma4）を使う |
| JSON モードが空の内容を返した | thinking モデルがすべてを `reasoning` に入れた | **すべての**クライアント（`llm_client.py` + camel の `run_*_simulation.py`）で `reasoning_effort:"none"` を設定する。`think:false` は OpenAI エンドポイントでは効かない |
| マルチプロセスの「並列」で速度が上がらなかった | 単一 GPU が N プロセスをタイムスライスする | 1つの GPU で N 個の `ollama serve` + ラウンドロビン proxy を使わない。実測: N=4 で +20%、レイテンシは N×、N=8 でスワップスラッシング |
| `reddit_profiles.json` が破損し、93 → 13 プロファイルを失った | 非アトミックな全ファイル書換え + 書込み途中での kill | アトミックに書く（temp + `os.replace` + `fsync`）。完全なプレフィックスを救済して復旧する（`json.JSONDecoder().raw_decode` ループ） |
| 進捗が知らぬ間に上書きされ / 件数が減少した | 2つのバックエンドが同じファイルに対して prepare を実行 | バックエンドを**正確に1つ**に保つ。各サイクルで `pgrep -f run.py` を監査する。プロファイル件数の不一致がその兆候 |
| 検証されていない長時間ランで何時間も浪費した | 設定を計測せず推測した | 2〜3分の代表的なスライスで集約スループットを計測し、それから1回だけ実行する |

### 失敗の分類（網羅的。これまでに観測したすべての失敗）

見落としがないよう分類しています。各項目 = 症状 → 原因 → 必要な行動。

**A. プロセス / ライフサイクル**
1. 再起動後に進捗を失った → 一方的な停止/再起動 → 明示的な承認を得る。コストを明示する。成果物を再利用する。
2. 「再開」/「残り N から」が知らぬ間に「最初から」になった → 指示の再解釈 → 指示に文字通り従う。曖昧なら短い質問を一度だけする。
3. 重複 / 孤立プロセス（`ollama serve`、`llama-server`、バックエンド、CLI、スーパーバイザー、モニター）→ メモリの浪費と競合 → 各サイクルで数を監査する。それぞれ1つだけに保つ。
4. 2つのバックエンドが同じファイルに対して prepare を実行した → 進捗が知らぬ間に上書きされ / 件数が減少（89 → 8）→ バックエンドを正確に1つに保つ。プロファイル件数の不一致がその兆候。
4b. **2つの設定生成が `simulation_config.partial.json` を奪い合った**（バッチ件数が 5 → 3 に減少）。バックエンドは prepare を**CLI より長生きするバックグラウンドタスク**として実行するため、CLI を再起動すると最初の prepare がまだ動いている間に*2つ目*が始まる。→ **prepare の起動を1回だけ**にする。`grep -c "Starting intelligent simulation configuration generation"` == 1 を確認してから進捗を信頼する。再起動時は再実行せず、partial から再開する。
4c. **シミュレーション後にパイプラインが停止し、レポートが自動生成されない。** シミュレーション（platform `parallel`）は環境を**待機モード**に保つため、`sim-status` が「終了」を報告しない。CLI はこれを**20ステップの上限**までポーリングし、`error: resume did not finish after 20 steps` で終了する。`reddit_completed=True` なのに `twitter_running=True` で、レポートは生成されない。→ 実行完了後に、まず**環境を閉じ**（`sim-close --simulation-id …`）、シミュレーションプロセスを終了させてから、`report-generate --simulation-id …` を実行する。（そうすれば検証は 19/19 で通る。）
5. 停止ヘルパーがバックエンドを捕捉できなかった → プロセス名の不一致 → 実際のコマンドラインに一致させる。停止後にポート/プロセスを確認する。
6. macOS の Ollama.app が `-np 1/4` で `ollama serve` を再生成し、ポート 11434 を奪った → 二重 LISTEN、直列化 → アプリを終了/`pkill` する。サーバーは1つ。`-np` を確認する。
7. アイドル状態の待機（長時間のブロッキングスリープ）と、確認せずに待つこと → ジョブが停滞/停止している可能性がある → 待つたびにプロセス + ステータス/成果物を確認する。進捗が確認できなければ調査する。
8. スーパーバイザーの誤再起動 → 単一カウンタによる停滞検出、または最長ステップより短い停滞ウィンドウ → 複数シグナルの進捗。ウィンドウ > 最長ステップ。LLM の活動を含める。ガード対象フェーズが完了したら監視を停止する。
9. ノート PC の蓋を閉じる = スリープ = ジョブが凍結（スワップを引き起こすことがある）→ 蓋を開けたまま / 電源 + ディスプレイ。`caffeinate` は蓋を閉じることによるスリープを防がない。

**B. コンテキスト / 設定**
10. より多くを要求したのに 4096 で出力が切り詰められた → **Ollama の OpenAI エンドポイントは `extra_body.options.num_ctx` を無視する** → 派生モデルの `num_ctx` を設定する。ランナーの `-c` を確認する。
11. すべてのステージで1つの大きな `num_ctx` を使う → `-c = num_ctx × NP` が爆発し、並列時のリクエストごとの速度が崩壊（15 → 1.5 tok/s）→ **ステージごとにモデルを分ける**（バルクは小さい `LLM_MODEL_NAME`、ontology/config/report は `LLM_MODEL_NAME_LARGE`）。
12. 設定の意図 ≠ ランナーの現実 → `-c`/`-np` を一度も確認していない → 設定を信頼する前に、常にランナーの実引数（およびモデル blob）を読む。
13. Ollama が**黙って NP を下げた**（NP=16 を要求、ランナーは `-np 4`）→ `-np` を確認する。
14. 切り詰め + JSON パース失敗 → リトライの嵐と数分かかるステップ → 実効（モデル）コンテキストを引き上げる。予算超過のステップは疑わしいものとして扱い、直ちに修正する。
15. 並列度 256 超は不可能（`n_seq_max must be <= 256`）→ 256 を上限とする（実用的な既定は 128）。

**C. 並列度 / パフォーマンス**
16. NUM_PARALLEL が効かない / 直列化されたまま → Ollama のアーキテクチャ制限（`model architecture does not support parallel requests`、例: qwen35）→ NP を調整する**前に**この警告がないか Ollama ログを確認する。並列化できるモデルを使う。
17. N 個の `ollama serve` + ラウンドロビン proxy で速度が上がらなかった → 1つの GPU が N プロセスをタイムスライスする → これをやってはいけない（実測: N=4 で +20%、N× のレイテンシ、N=8 でスワップスラッシング）。
18. 負荷時に完了しない → 高い並列度でのリクエストごとのレイテンシよりクライアントタイムアウトが短い → `timeout > 2 × per-request`。ステージごとに適度な並列度。
19. 速度を誤判定 → `eval tok/s` だけで判断し、`n_gen`/`n_tokens` を出力長と誤読（これらは**プロンプト + 出力**）→ **エンドツーエンドの完了レート**（件/分）を計測する。
20. 広範な NP スイープ（2..128）を何時間も実行 → 短い標本計測がなかった → 2〜3分のスライスを計測して選ぶ。1回だけ実行する。
21. スループットの崩壊 → スワップスラッシング（未使用 RAM がほぼ 0）→ スワップをほぼ 0 に保ち、実質的な余裕を持たせる。並列度を RAM に合わせる。`vm.swapusage` を監視する。
22. 検証されていない長時間ランで何時間も浪費 → 設定を計測せず推測した → 代表的なスライスを計測し、それから1回の実行に踏み切る。

**D. データ整合性 / 永続化**
23. `reddit_profiles.json` が破損（93 → 13 プロファイルを喪失）→ 非アトミックな全ファイル書換え + 書込み途中での kill → アトミック書込み（temp + `os.replace` + `fsync`）。`json.JSONDecoder().raw_decode` ループで完全なプレフィックスを救済する。
24. 再開状態が失敗/破損 → dataclass がシリアライズ不可。非アトミック書込み → `asdict` + アトミック書込み。依存する前にファイルがパースできることを検証する。
25. 再起動で完了済みステップをやり直し → 部分永続化がない → ステップ/バッチごとに永続化する（例: `simulation_config.partial.json`）。未完了の項目だけを再生成する。
26. 再起動ですべてのプロファイルが再生成された → 生成に再開機能がなかった → 保存済みプロファイルを再利用する（`user_id` で照合）。

**E. モデル固有**
27. JSON モードが**空の `content`** を返した → thinking モデルが予算を別の `reasoning` フィールドで使い切った → **すべての**クライアントで `reasoning_effort:"none"` を設定する（`llm_client.py` と camel の `run_*_simulation.py` の両方を `model_config_dict` 経由で）。`think:false` は OpenAI エンドポイントでは効かない。
28. NER が**0 エンティティ**を抽出 → プロンプトが「オントロジーの型のみ / 正確に」と言い過ぎていた → 再現率を許容する（参照リストに著者を含めるなど）。

**F. エージェントの行動（運用規律）**
29. エンドツーエンドの完了レートを計測せずに長時間ランを開始した。
30. 明示的な指示（バルク段階は小さい ctx）を無視した。
31. 再起動チャーンで状態を破損し、作業を失った。
32. プロジェクト固有のルールを**グローバル**設定に書いた（スコープ違反）。
33. 方法を変えずに、既知の悪手（Nプロセス proxy）に投資し続けた。
34. 報告がスパムと過少の間で振れた。マイルストーンで簡潔に報告する。
35. マシンが処理できないとき、能動的にスコープを縮めなかった。
36. 証拠なしに修正を主張した → 常に実際のランナー引数/成果物を確認する。

### 失敗 → 教訓 → 一般化されたルール（これらのガードレールがどう導かれたか）

ここでの失敗のほとんどは、指示に従わなかったことによるものでは**ありません**。*文字通り*の指示には従いつつ、**その目的/意図を理解していなかった**ため、努力が間違った対象に最適化されました。このセクションはその過程を記録し、ルールをこのプロジェクトの外にも一般化できるようにするものです。

| 失敗（実際に起きたこと） | 見落とされた目的/意図 | 追加すべき一般化されたルール |
|---|---|---|
| すべてのステージを1つの大きな `num_ctx` で実行した | ctx の要点はステージごと。並列性を効かせるためバルクは小さく保ち、大きいのは ontology/config/report のみ | **異種のステージに1つの一律な設定を適用しない。** 各ステージの要件を特定し、ステージごとに設定する |
| 完了レートを計測せずに数時間のランを開始した | 成功 = *開始したジョブ*ではなく*完了した出力*。`eval tok/s` ≠ スループット | **成功を完了として定義する。** 長時間ランの前に、短いスライスでエンドツーエンドのレート（件/分）を計測する |
| `n_gen`/`n_tokens` を出力長と誤読した | 指標には厳密な意味がある。定義を検証せずに行動するのは推測 | **定義をドキュメント/ログで検証していない指標に基づいて行動しない** |
| 再起動を繰り返し、93→13 プロファイルを失った | 「再開/続行」は*保存して続ける*ことであり、*再起動*ではない | **明示的な承認 + コスト計算なしに停止/再起動/再設定しない。成果物を再利用する** |
| プロジェクトのルールをグローバル設定に書いた | 意図は*このプロジェクトのため*に教訓を記録することだった | **スコープの境界を守る。プロジェクト固有の内容を共有/グローバル設定に決して入れない** |
| 1つの GPU で Nプロセス + ラウンドロビン proxy を追求した | 単一 GPU はタイムスライスする。前提（独立した並列ハードウェア）が偽だった | **根本的な前提（ハードウェア/アーキテクチャ）が未検証のアイデアに投資しない** |
| NP=2..128 のスイープを何時間も実行した | 目標は*決定*であり、網羅的なデータではなかった | **網羅的なスイープを実行しない。** 対象を絞った2〜3分の計測を1回行い、決定する |
| ランナーの実引数より設定の意図を信頼した | 重要なのは意図した設定ではなく観測された挙動 | **実際のランナー引数/成果物を読むまで、設定が適用されたと主張しない** |
| 報告がスパムから沈黙へ振れた | ユーザーは安定した簡潔なマイルストーン更新を必要とする | **簡潔なマイルストーン更新を報告する。スパムも沈黙もしない** |
| マシンが完了できないのにフルスケールを維持した | 意図は*実行可能な*エンドツーエンドの結果。スケールは交渉可能 | **実行不可能な計画を黙って走らせない。** 制約を明らかにし、トレードオフを提案する |
| 証拠なしに修正を主張した | 意図は*検証済みの*修正 | **証拠（測定値/成果物）なしに成功を主張しない** |

### 一般化された禁止事項（あらゆるプロジェクトに適用する）

1. **指示の文字面を最適化するな。その目的を最適化せよ。** 高コストな行動の前に、目的と成功基準を述べ、文字通りの要求が本当にそれらに資するか確認する。もし両者が衝突するなら（例: 「実行しろ」だが設定は許容できる時間/リソースで完了できない）、立ち止まって調和させよ。ただ走らせるな。
2. **エンドツーエンドのレートと終了条件を予測できるまで、長時間/高コストのジョブを開始するな。** 「開始した」は「完了する」ではない。
3. **意図した設定を信頼するな。実際の挙動（ランナー引数、成果物）を検証せよ。**
4. **未検証の指標や仮定に基づいて行動するな。** 定義と値を検証せよ。
5. **明示的な承認と明示したコストなしに、実行中のジョブを停止/再起動/変更するな。**
6. **パラメータをスイープするな。** 1つの仮説、1つの短い計測、それから決定せよ。
7. **スコープを超えるな**（プロジェクト vs グローバル、タスク vs より広範な書き直し）。
8. **ユーザーを推測させたままにするな。** 簡潔なマイルストーン、証拠を伴う失敗、次の意思決定ポイントを報告せよ。
9. **失敗した行動をそのまま繰り返すな。** 診断し、1つの変数を変え、計測せよ。
10. **教訓は学んだその瞬間に書き残せ**（スキル/README）、そして一般化せよ。1つのケースだけを直すルールは、次のケースでまた破られる。
11. **良い案が尽きたら暴走するな。ユーザーに一度相談せよ。** アプローチが機能しておらず、*より良い*案を出せないなら、バリエーションを試すのをやめて尋ねよ: 現状、見えている選択肢、推奨案を述べよ。盲目的な再試行は、1つの失敗を多くの失敗に変える。（短く的を射た相談は安く、もう1回の無駄な数時間ランは高くつく。）

### システムの特性（これらを前提に設計する）

- Ollama の OpenAI エンドポイントでは **`extra_body.options.num_ctx` が無視されます** → 実効のスロットごとのコンテキストは**派生モデルの `num_ctx`** だけです。
- ランナー = `llama-server -c <num_ctx × NP> -np NP`。**`-c` が大きいと並列時のリクエストごとの速度が崩壊します**（8192 単発 = 15 tok/s vs 32768×NP8 ≈ 1.5）。
- **NP は Ollama によって黙って下げられることがあります**（16 を要求して 4 になる）。常に `-np` を読んでください。
- **並列性はアーキテクチャが決めます**: gemma4（スライディングウィンドウ）は並列化します。qwen35（ハイブリッドアテンション）は**しません**（`-np 1` を強制、`n_seq_max = 1`）。
- **Ollama.app は再生成し**、不正な `-np` でポート 11434 を奪うことがあります（二重 LISTEN）。**修正: 調整済みサーバーを専用ポートで動かします**（`OLLAMA_HOST=127.0.0.1:11500`）。そして `LLM_BASE_URL` / `OPENAI_API_BASE_URL` / `EMBEDDING_BASE_URL` をそこに向けます（`EMBEDDING_API_STYLE=ollama`）。そうすればアプリは 11434 で自由に出入りしても無害です。`scripts/lib_ollama.sh` は既定でこれを行います。
- `n_gen` / `n_tokens` = **プロンプト + 出力**（実際のプロファイル出力は約 **850 トークン**ですが、`n_gen` は約 2,500 を示します）。
- **GPU は1基**: N プロセスはタイムスライスします。マルチプロセスの「並列」≠ スループット。
- **スワップスラッシング**（未使用 RAM ≈ 0）はスループットを崩壊させます。
- llama.cpp のハード上限: `n_seq_max ≤ 256`。
- **バックエンドは正確に1つ**。2つのバックエンドは同じ進捗ファイルを上書きします。
- 進捗ファイルは**アトミック**でなければなりません（temp + `os.replace` + `fsync`）。

### 並列化の監査

パイプライン内の独立した項目ごとの作業は並行して実行すべきです（`OLLAMA_NUM_PARALLEL` によって制限されるため、余分なコンテキスト割り当てなしに空きスロットを使用します）。

| 対象 | 環境変数ノブ | 既定値 |
|---|---|---|
| エージェント設定バッチ（`simulation_config_generator`） | `CONFIG_BATCH_PARALLEL` | 8 |
| グラフ構築のチャンク処理（`graph_builder.add_text_batches`） | `GRAPH_BUILD_PARALLEL` | 4 |
| プロファイル生成（`oasis_profile_generator`） | `--parallel-profiles` | 16 |
| エンベディングリクエスト | すでにバッチ化済み（`embed_batch`） | — |
| レポートセクション | 直列（依存コンテキスト） | — |

新しい項目ごとのループを追加するときは、それらを並列化し、ここにノブを追加してください。

**並列化するときの時間予算ルール:** 集約スループットは飽和するため、`per-request rate ≈ aggregate / concurrency` かつ `per-unit time ≈ tokens × concurrency / aggregate` です。並列度1で機能していたリクエストごとのタイムアウトは、並列度 N では短すぎます。並列度のノブを上げる前に（余裕を見て）再計算し、総実時間はユニット数や直列時のユニット時間ではなく `total tokens / aggregate` から見積もってください。

### モデル選択と実ワークロードでの並列度（実測）

**用語**（定義なしの略記は使わない）:
- **NP** = `OLLAMA_NUM_PARALLEL`。1モデルに対するサーバー側スロット。Ollama はランナーを `llama-server -c <num_ctx × NP> -np NP` として起動します。
- **C** = 1ステージに対するクライアント側の並列度: `--parallel-profiles`、`CONFIG_BATCH_PARALLEL`、`GRAPH_BUILD_PARALLEL`、`OASIS_SEMAPHORE`。
- **aggregate** = ジョブ全体の生成トークン ÷ 実時間（マシンの実際の上限。並列度を上げても飽和を超えては上がりません）。

| モデル | 並列? | 約 aggregate | 実ワークロードでの挙動 |
|---|---|---|---|
| **gemma4:12b**（第一選択。精度） | 可 | 152 tok/s ピーク（NP=64、短いプロンプト） | プロファイル約 2,500 tok。C=8 → 約 14 tok/s、C=12 → 約 6.7（競合）。**NP=16、C=8、`LLM_TIMEOUT=1800`** を使う |
| **gemma4:e4b**（速度） | 可 | 208 tok/s（NP=64） | プロファイル約 880 tok。最速のエンドツーエンド（prepare で約 30〜45 分） |
| **qwen3.5:9b** | **Ollama: 不可 / oMLX: 可（ただし低速）** | 約 48 tok/s（Ollama, 固定）/ 約 75 tok/s（oMLX, 飽和） | Ollama は `-np 1` を強制（`architecture does not support parallel`）。**oMLX は並列受理するが早期に飽和** → [oMLX での qwen 並列化（実測）](#omlx-での-qwen-並列化実測)。thinking モデルは `reasoning_effort=none` が必要 |

苦労して得たルール:
- **NP を調整する前に、Ollama ログで `does not support parallel requests` を確認する。** これがあれば `-np` は 1 に強制され、NP の調整は無駄になる。
- **直列モデルを Nプロセス + ラウンドロビン proxy で並列化しない。** 単一 GPU はタイムスライスする（実測: N=4 で aggregate は +20% のみ、レイテンシは N×）。
- **ステージごとに並列度を調整する**: 短い出力（NER チャンク、sim アクション）は高く、長い出力（プロファイル、設定バッチ）は低く。`timeout > 2 × per-request time` を守る。
- **2〜3分の計測で探り、それから1回だけ実行する。** 「動くか見るため」に数時間のランを開始しない。実際のプロンプト/出力形状でベンチマークする。

### oMLX での qwen 並列化（実測）

Ollama は `qwen35` アーキテクチャの並列化を拒否するため、`qwen3.5:9b` はスループット目的では使えませんでした（常に `-np 1`、`n_seq_max = 1`、直列化）。**oMLX（Apple Silicon 上の MLX）はこの制限を取り除きます。** 同一形状（約 2,093 トークン prompt / 900 トークン出力、`reasoning_effort:none`）での実測:

| ランタイム | モデル | n=1 | n=8 | n=16 | 並列受理 |
|---|---|---|---|---|---|
| Ollama 0.34.2 | `qwen3.5:9b` | 約48 tok/s | 約48 tok/s | 約48 tok/s | **不可** — `-np 1`, `n_seq_max=1`, 直列化 |
| **oMLX 0.6.4** | **`Qwen3.5-9B-MLX-4bit`** | 50.7 tok/s | **75.0 tok/s** | **75.3 tok/s** | **可** — `peak_active_requests` = 8 / 16 |
| oMLX 0.6.4 | `gemma-4-e4b-it-4bit`（参考） | 76.0\* | 120.6 tok/s | 164.5 tok/s | 可 |

\* gemma の単一ストリーム 76.0 tok/s は短いプロンプトでの計測です。n=8 / n=16 の列は qwen と同じ実形状なので、直接比較できるのはこの2列のみです。

計測ホスト: oMLX = Mac Studio M1 Ultra / 128 GB。Ollama の `qwen3.5:9b` は以前の引き継ぎ資料（M3 Max）の値ですが、直列化は**アーキテクチャ**の制約なので、並列度やホストによらず一定です。

結論:

- **oMLX は qwen3.5 を並列実行できる。** `Qwen3.5-9B-MLX-4bit` は `config_model_type = qwen3_5`（Ollama が拒否したまさにその系統）でありながら、16 リクエストが実際に同時実行され（`peak_active = 16`）、アーキテクチャ拒否は発生しません。Ollama の `-np 1` 直列化は起きません。
- **ただし qwen3.5 のスケールは悪い。** aggregate は約 75 tok/s（単一 50.7 tok/s の 1.5 倍、n=8 で頭打ち）で、同一ホスト・同一形状の gemma-4-e4b（120.6 → 164.5 tok/s）に大きく劣ります。原因はメモリ（10.85 GB / 107.5 GB 使用）でもスケジューリング（n=16 が active）でもなく、**ハイブリッド注意の decode が MLX でバッチ効率良く動かない**ためです。
- **実用効果。** 157エージェントの prepare（約 141k 出力トークン）は **3〜7時間（Ollama, 直列）→ 約31分（oMLX, `total_tokens / 75`）**。qwen が実用圏に入ります（gemma-4-e4b の約13分には及びません）。
- **推奨。** スループットは gemma-4 を維持。qwen は精度が必要なときだけ選び、ETA は `total_tokens / 75` で見積もります。
- **未計測。** Qwen の MoE 系（例: `Qwen3.6-35B-A3B`）はより良くバッチする可能性があり、別途計測が必要です。

oMLX に qwen MLX モデルを追加（SSH 不要。管理 API キーが必要）:

```bash
curl -s -X POST -H "Authorization: Bearer $OMLX_API_KEY" \
     -H 'Content-Type: application/json' \
     -d '{"repo_id":"mlx-community/Qwen3.5-9B-MLX-4bit"}' \
     http://<omlx-host>:8010/admin/api/hf/download      # 約6GB、約100秒
# 呼び出す際のモデルID: Qwen3.5-9B-MLX-4bit
```

> **oMLX で派生モデルが不要な理由。** Ollama は `num_ctx × NUM_PARALLEL`（`-c`）を事前確保するため、本プロジェクトは段階別に `gemma4-4k` / `-8k` / `-32k` を派生させていました。oMLX はコンテキストをリクエスト単位で確保するので、8k/32k の分割は不要で `-c` の崩壊も起こりません。

### 教訓の記録（必須）

このシステムは臨機応変な対応が苦手なエージェントが運用します。したがって:

- あらゆる失敗（停滞、タイムアウト、再開状態の破損、誤再起動、指示の誤解釈）は、
  その場の修正で終わらせず、**必ずスキル**
  (`~/.config/opencode/skills/operational-guardrails/SKILL.md`) と上記の失敗ログに記録する。
- 教訓の抽出・一般化には学習/デバッグ系のスキルを活用する:
  `reflect`（繰り返す摩擦を再利用可能なスキル/設定へ）、`debugging`（根本原因）、
  `verification-planning`（証拠経路）。
- ヒューリスティックが誤動作したら修正して記録し、次のエージェントに同じ罠を残さない。


> モデル切替の引き継ぎ資料: [`docs/handover-qwen3.5-9b.md`](docs/handover-qwen3.5-9b.md)

## アーキテクチャ

このフォークは、アプリケーションとグラフデータベースの間にクリーンな抽象化レイヤーを導入します:

```
┌─────────────────────────────────────────┐
│              Flask API                   │
│  graph.py  simulation.py  report.py     │
└──────────────┬──────────────────────────┘
               │ app.extensions['neo4j_storage']
┌──────────────▼──────────────────────────┐
│           Service Layer                  │
│  EntityReader  GraphToolsService         │
│  GraphMemoryUpdater  ReportAgent         │
└──────────────┬──────────────────────────┘
               │ storage: GraphStorage
┌──────────────▼──────────────────────────┐
│         GraphStorage (abstract)          │
│              │                            │
│    ┌─────────▼─────────┐                │
│    │   Neo4jStorage     │                │
│    │  ┌───────────────┐ │                │
│    │  │ EmbeddingService│ ← Ollama       │
│    │  │ NERExtractor   │ ← Ollama LLM   │
│    │  │ SearchService  │ ← Hybrid search │
│    │  └───────────────┘ │                │
│    └───────────────────┘                │
└─────────────────────────────────────────┘
               │
        ┌──────▼──────┐
        │  Neo4j CE   │
        │  5.15       │
        └─────────────┘
```

**主要な設計上の決定:**

- `GraphStorage` は抽象インターフェースです。1つのクラスを実装するだけで Neo4j を任意の他のグラフ DB に差し替えられます
- Flask `app.extensions` による依存性注入。グローバルなシングルトンなし
- ハイブリッド検索: 0.7 × ベクトル類似度 + 0.3 × BM25 キーワード検索
- ローカル LLM による同期 NER/RE 抽出（Zep の非同期エピソードを置き換え）
- 元のすべてのデータクラスと LLM ツール（InsightForge、Panorama、Agent Interviews）を保持

## ハードウェア要件

| コンポーネント | 最小 | 推奨 |
|---|---|---|
| RAM | 16 GB | 32 GB |
| VRAM（GPU） | 10 GB（14b モデル） | 24 GB（32b モデル） |
| ディスク | 20 GB | 50 GB |
| CPU | 4 コア | 8+ コア |

CPU のみのモードも動作しますが、LLM 推論は大幅に遅くなります。より軽量な構成では `qwen2.5:14b` または `qwen2.5:7b` を使用してください。

## ユースケース

- **PR クライシスのテスト**: 公開前にプレスリリースに対する世間の反応をシミュレートする
- **トレーディングシグナルの生成**: 金融ニュースを投入し、シミュレートされた市場センチメントを観察する
- **政策の影響分析**: 規制草案をシミュレートされた世間の反応に対してテストする
- **創造的な実験**: 誰かが結末の失われた中国の古典小説を投入したところ、エージェントたちが物語的に整合した結末を書いた

## ライセンス

AGPL-3.0。オリジナルの MiroFish プロジェクトと同じです。[LICENSE](./LICENSE) を参照してください。

## クレジットと帰属

これは [666ghj](https://github.com/666ghj) による [MiroFish](https://github.com/666ghj/MiroFish) の改変フォークであり、当初は [Shanda Group](https://www.shanda.com/) に支援されていました。シミュレーションエンジンは CAMEL-AI チームの [OASIS](https://github.com/camel-ai/oasis) によって提供されています。

**このフォークにおける変更:**
- バックエンドを Zep Cloud からローカルの Neo4j CE 5.15 + Ollama に移行
- フロントエンド全体を中国語から英語に翻訳（20 ファイル、1,000 以上の文字列）
- UI 全体で Zep の参照をすべて Neo4j に置き換え
- MiroFish Offline にリブランド
