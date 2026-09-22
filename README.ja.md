# MiroFish-Offline — oMLX バックエンドと実測比較

**本 fork（petadimensionlab derivative）** は、MiroFish のマルチエージェント社会シミュレーションを
Ollama ではなくローカルの **oMLX / MLX** 推論サーバーで実行し、**段階ごとにモデルを使い分け**
ます（速度 vs 品質）。

言語: [English](README.md) | **日本語**

---

## TL;DR — 計測から言えること

1. **同一ホストなら oMLX が優位。** M1 Ultra 上、同一モデル・同一形状 n=8 で
   **oMLX 120.6 tok/s に対し Ollama 58–77 tok/s**（Ollama の過去最高値 208–305 tok/s は
   *別マシン* で、専用に派生させた小ctxモデルを使った計測です）。
2. **スループットは並列度で飽和する。** gemma-4-e4b は**実 profile 形状で n≈16**
   （164.5 tok/s）で飽和。スロットを増やしてもレイテンシが増えるだけ。27B は ~23 tok/s で
   飽和し、並列化の効果は**ゼロ**。
3. **oMLX は Ollama が拒否するモデル系統も並列化できる。** Ollama は `qwen35` で `-np 1`
   を強制（直列、~48 tok/s 固定）。oMLX は同系統を受理（`peak_active = 16`）し ~75 tok/s。
   結果、qwen の prepare が「3〜7時間」→「**約31分**」に。
4. **品質重視段階に小型モデルは不足。** 同一 ontology タスクで `gemma-4-e4b` は
   社会的主体ではなく*技術的アーティファクト*（`Classifier_Model`, `Network_Topology` 等）を
   生成しました。`gemma-4-12B` と `Qwen3.8-27B` は正しく社会的主体を生成 → **段階別モデル
   分割**を採用。
5. **oMLX 切替に伴う退行が2件、いずれも修正・再検証済み** — `content: null`（native
   tool_calls）による report クラッシュと、クライアント timeout 不足。パイプラインは
   エンドツーエンドで完走し `verify_outputs.py` は **18/18 PASS**。

---

## 実験環境

| 項目 | 値 |
|---|---|
| 推論ホスト | Mac Studio — **M1 Ultra / 128GB / 64 GPUコア**（tailnet 経由） |
| サーバー | **oMLX 0.6.4**（MLX 0.32.0 / MLX-LM 0.31.3）、OpenAI 互換 `:8010` |
| クライアント | MacBook Pro (M3 Max)、Neo4j 5.18 は Docker |
| プロンプト形状 | 特に断りが無ければ **約2,093トークン入力 / 900トークン出力**（実 profile 形状） |
| リクエスト | `reasoning_effort: "none"`, `response_format: {"type":"json_object"}` |
| 指標 | **aggregate = 生成トークン ÷ 実時間**（`eval tok/s` ではない） |

oMLX のモデルIDはリポジトリ名の `/` を `--` に置換したもの、または配信されるID
（例: `mlx-community/gemma-4-e4b-it-4bit` → `gemma-4-e4b-it-4bit`）。

---

## 1. oMLX vs Ollama — 同一ホスト・同一モデル・同一形状

`gemma4:e4b`（Ollama, GGUF）と `gemma-4-e4b-it-4bit`（oMLX, MLX）、実形状 n=8:

| バックエンド | 構成 | aggregate |
|---|---|---|
| **oMLX** | 既定（ctx をリクエスト単位で確保） | **120.6 tok/s** |
| Ollama | native API, `num_ctx=4096` 指定 | 58.0 tok/s（HTTP 500 ×2） |
| Ollama | OpenAI API, 自動 `num_ctx=65536` | 77.0 tok/s |

→ **同一ハードウェアで oMLX が 1.6〜2.1倍**。しかも ctx の調整は一切不要です。

**なぜ Ollama は手当てが必要で oMLX は不要か。** Ollama は `num_ctx × NUM_PARALLEL` を
事前確保し `llama-server -c <total> -np <N>` で起動します。自動選択された `num_ctx=65536`
だけで per-request 速度が崩壊するため、本プロジェクトは歴史的に段階別の派生モデル
（`-4k` / `-8k` / `-32k`）を作る必要がありました。oMLX は ctx をリクエスト単位で確保するため、
**派生モデルは不要**です。

*参考（別マシン = M3 Max、チューニング済み・派生 `gemma4-4k`）*: Ollama は NP=16 → 140.8、
NP=32 → 247.7、NP=64 → 305.4、NP=128 → 270.3 tok/s（64トークン生成）、24req×400tok で
208.2 tok/s。ただしこれらは `OLLAMA_NUM_PARALLEL=64/128` の専用サーバーが前提で、
**既定構成では再現しません**（それが上記の同一ホスト比較です）。

---

## 2. oMLX の並列スケーリング

### gemma-4-e4b-it-4bit — 短文（42 tok 入力 / 512 tok 出力）

| n | 1 | 2 | 4 | **8** | 24 | 32 |
|---|---|---|---|---|---|---|
| aggregate tok/s | 76.0 | 115.6 | 146.4 | **174.1** | ~159 | 174.0 |
| per-request tok/s | 76.0 | 57.8 | 36.6 | 21.8 | ~6.6 | 5.4 |

### 実 profile 形状（約2,093 tok 入力 / 900 tok 出力）

| モデル | n=1 | n=8 | **n=16** | n=32 |
|---|---|---|---|---|
| **`gemma-4-e4b-it-4bit`** | 76.0\* | 120.6 | **164.5** | 165.0 |
| `Qwen3.5-9B-MLX-4bit` | 50.7 | 75.0 | 75.3 | — |
| `Qwen3.8-27B-oQ4e-mtp` | 20.0 | 22.9 | — | — |
| `Ternary-Bonsai-2-27B-MLX-4bit` | 21.3 | 23.8 | — | — |

\* gemma の単一ストリーム値は短いプロンプトでの計測です。行をまたいで直接比較できるのは
n=8 / n=16 の列のみです。

**読み方:** 小型モデルはスケールし（n=1→8 で約2.3倍）、**n≈16 で飽和**。27B は帯域律速で
並列化の効果が**ありません**（1.1〜1.15倍）。飽和点を超えてスロットを増やすとレイテンシのみ
増加します（n=32 で p50 86秒 vs n=16 で 57.5秒）。

---

## 3. qwen: oMLX では並列化可能、Ollama では直列化

| ランタイム | モデル | n=1 | n=8 | n=16 | 並列受理 |
|---|---|---|---|---|---|
| Ollama 0.34.2 | `qwen3.5:9b` | ~48 tok/s | ~48 | ~48 | **不可** — `-np 1`, `n_seq_max=1` |
| **oMLX 0.6.4** | **`Qwen3.5-9B-MLX-4bit`** | 50.7 | **75.0** | **75.3** | **可** — `peak_active` 8 / 16 |

Ollama は `model architecture does not currently support parallel requests;
architecture=qwen35` を出し、クライアント並列度に関係なく直列化します。oMLX は同系統
（`config_model_type: qwen3_5`）を受理しますが、スケールは悪く ~75 tok/s で飽和
（gemma-4-e4b は 164.5）。157エージェント prepare（約141k出力トークン）での実効差は
**3〜7時間（Ollama）→ 約31分（oMLX）**です。

> 同ホストの 27B はさらに遅く（~23 tok/s）、しかも **8並列で oMLX がスタック**しました —
> `active=8` のまま約9分間、prompt/completion カウンタ・メモリ・ログすべて停止。クライアント
> 切断で即復帰します。詳細は §7。

---

## 4. モデル互換性（oMLX 0.6.4、実測）

| MLX リポジトリ | `config.json` の `model_type` | ロード | 実形状 aggregate |
|---|---|---|---|
| `mlx-community/gemma-4-e4b-it-4bit` | `gemma4` | **可** | 76.0 → 120.6 → **164.5** tok/s（n=1/8/16） |
| `mlx-community/gemma-4-12B-it-qat-4bit` | `gemma4_unified` | **可** | ontology/report 検証済み（§5） |
| `Jundot/Qwen3.8-27B-oQ4e-mtp` | `qwen3_5`（`mtp_compatible`） | **可** | 20.0 → 22.9 tok/s（n=8、ほぼスケールせず） |
| `mlx-community/Qwen3.5-9B-MLX-4bit` | `qwen3_5` | 可、ただし **JSON 段階では使用不可** | 50.7 → 75.0 tok/s |
| `pipenetwork/Ternary-Bonsai-2-27B-MLX-4bit` | `qwen3_5`（4-bit affine） | **可** | 21.3 → 23.8 tok/s |
| `prism-ml/Ternary-Bonsai-2-27B-mlx-2bit` | `prism_hadamard_qwen35` | **不可** | ロード拒否（HTTP 409） |
| `inductiveML/Ternary-Bonsai-2-27B-mlx-lossless-1.75bpw` | `ternel_hadamard_qwen35` | **不可** | ロード拒否 |
| `gemma4:e4b`（Ollama GGUF） | — | **不可** | oMLX は MLX safetensors が必要 |

- **Bonsai 2 27B（公式 ternary 2-bit ビルド、2026-09-16 公開）はロード不可**:
  `Model type prism_hadamard_qwen35 not supported. Error: No module named
  'mlx_vlm.speculative.drafters.prism_hadamard_qwen35'`。`--with-custom-kernel` での
  再ビルドでも**解決しません**（そのカーネルは旧 Bonsai 系向け）。上流では issue
  [#3768](https://github.com/jundot/omlx/issues/3768) /
  [#3727](https://github.com/jundot/omlx/issues/3727)、PR
  [#3734](https://github.com/jundot/omlx/pull/3734) /
  [#3782](https://github.com/jundot/omlx/pull/3782)（未マージ）。
  回避策は `config.json` が素の `qwen3_5` を宣言する変換版（ロード可だが低速）。
- **Qwen3.5-9B は JSON 段階では失格**: `content` に `Thinking Process:` の思考トレースを
  出し、4096トークンを使い切って ontology が `Invalid JSON format from LLM` で失敗します。
- **ダウンロード前に必ず `config.json` の `model_type` を確認**してください。`gemma4` と
  `qwen3_5` はロード可、`*_hadamard_*` の ternary パックは現状ロード不可です。

**既知の制約（致命的ではない）:** oMLX は
`response_format requested but grammar-constrained decoding is unavailable; output will
not be schema-enforced (falling back to prompt injection)` を記録します — つまり JSON は
文法制約ではなくプロンプト誘導です。`--with-grammar`（xgrammar）付きビルドで改善できます。

---

## 5. 段階別 A/B: 品質重視段階にはどのモデルか

`LLM_MODEL_NAME_LARGE` は **ontology / simulation_config / report**
（`ontology_generator.py`, `simulation_config_generator.py`, `report_agent.py`）が使用し、
`LLM_MODEL_NAME` はバルク段階（NER / profiles / simulation）が使用します。同一入力で実測:

### ontology（同一 45,326文字の文書・同一要件）

| 変種 | 時間 | entity | edge | 生成された entity_types |
|---|---|---|---|---|
| `gemma-4-e4b` | **37.5秒** | 10 | 6 | ❌ `Model_Developer, Network_Topology, Classifier_Model, Generosity_Campaign` — **技術的アーティファクト** |
| `gemma-4-12B` | 103.1秒 | 10 | 8 | ✅ `Conservationist, SustainabilityOfficer, PolicyMaker, MediaOutlet, NGO, TechDeveloper, Netizen` |
| `Qwen3.8-27B` | 218.4秒 | 10 | **9** | ✅ `ConservationNGO, GovernmentAgency, MediaOutlet, SustainabilityCompany, SocialMediaPlatform, AcademicInstitution, EnvironmentalAdvocate` |

edge_types も同様: e4b は `SIMULATES, TESTS_HYPOTHESIS, IS_MODELED_BY`（ML中心）、
12B/27B は `REPORTS_ON, ADVOCATES_FOR, REGULATES, OPPOSES, FUNDS, ENGAGES_WITH`（社会的相互作用）。

> 補足: 既存の本番グラフ（以前 `gemma4:12b` で生成）も社会的主体を含んでいました。
> つまり劣化は **小さな e4b に固有**で、gemma 系一般の問題ではありません。

### report（同一シミュレーション・同一要件）

| 変種 | 時間 | セクション | 総文字数 | tool calls | 本文の質（サンプル評価） |
|---|---|---|---|---|---|
| `gemma-4-e4b` | **99.2秒** | 3 | 7,607 | 8 | ❌ グラフ事実を引用の羅列、合成がほぼ無い |
| `gemma-4-12B` | 344.9秒 | 4 | 18,476 | 12 | ✅ 分析的な散文（テーゼ→メカニズム→含意） |
| `Qwen3.8-27B` | **1025.8秒** | 4 | **38,529** | 10 | ✅✅ 構造的分析（共著チェーン・共有venue・統一領域） |

**結論:** 品質重視段階には **12B 以上**が必要。`Qwen3.8-27B` が最も豊か（e4b の約5倍の本文）で
時間は約10倍。`gemma-4-12B` は中間（e4b の 3.5倍時間で 2.4倍の本文）。

---

## 6. 最終構成でのエンドツーエンド実測

`sim_b18e332db6b7` — 157エージェント、M1 Ultra 上で全工程（新構成は §8）:

| 段階 | モデル | 実測 | 備考 |
|---|---|---|---|
| ontology（本番経路） | 27B | **3分40秒** | 10 entity / 8 edge |
| profiles ×157（`--parallel-profiles 16`） | e4b | **12.5分** | 12.03 profiles/min, `active=16`/`waiting=8` |
| simulation_config（11バッチ） | **27B**（C=1） | **26.9分** | 約2.1分/バッチ、約25 tok/s aggregate |
| simulation（1ラウンド） | e4b | 4秒 | 初期投稿のみ（`Published 7 initial posts`）。LLM行動は round≥2 |
| report | **27B** | **約19分** | 4セクション、35,026文字 |
| `scripts/verify_outputs.py` | — | **18/18 PASS** | FAIL 0 |
| **合計** | | **約62分** | |

バルク段階の実測はマイクロベンチと一致（12.03 profiles/min ≒ n=16 で実測した 12.1〜12.3）。
つまり **実走 aggregate ≒ マイクロベンチの 88%** — 残り約12%は形状ばらつき・prefill 混在・
リトライ分として見込んでください。

⚠️ 以前 `CONFIG_BATCH_PARALLEL=8` で試した際は**スタック**し約27分を浪費しました（§7）。
上表は `CONFIG_BATCH_PARALLEL=1` の数値です。

---

## 7. oMLX で発見した退行・障害（すべて修正済み）

| # | 症状 | 根本原因 | 修正 |
|---|---|---|---|
| 1 | `report-generate` が**全モデルで** FAILED: `expected string or bytes-like object, got 'NoneType'`（Ollama 時代からの退行） | oMLX はツール形式を説明するプロンプトに対し **native `tool_calls` を返し `content: null`** になる（`finish_reason: tool_calls`）。`llm_client.chat()` が文字列前提だった | `llm_client.chat()` が native `tool_calls` を `<tool_call>{"name":…,"parameters":…}</tool_call>` テキストへ変換（`report_agent._parse_tool_calls()` が期待する形式）。再検証で3モデル全て COMPLETED |
| 2 | ontology / report が 27B で timeout し得る | これらのクライアントが **300秒固定**だった（ontology 実測 218秒、report 単発最大 ~153秒） | `LLM_TIMEOUT_LARGE`（既定1800）を導入し `ontology_generator.py` / `report_agent.py` が参照。`LLM_TIMEOUT_CONFIG=1800` |
| 3 | `simulation_config` が**ハング**: 8リクエスト発行後 `active=8` のまま約9分、カウンタ/メモリ/ログすべて停止 | **27B × 8並列**で oMLX がスタック（クライアント切断で即復帰） | `CONFIG_BATCH_PARALLEL=1`。そもそも 27B は並列で伸びない（§2） |

記録した教訓: OpenAI 互換サーバーの **`content` は `null` になり得る**。文字列前提で扱わないこと。

---

## 8. 本番構成

```bash
# --- oMLX（OpenAI 互換） ---
LLM_API_KEY=<oMLX API キー>
LLM_BASE_URL=http://<omlx-host>:8010/v1
OPENAI_API_BASE_URL=http://<omlx-host>:8010/v1

LLM_MODEL_NAME=gemma-4-e4b-it-4bit           # bulk: NER / profiles / simulation
LLM_MODEL_NAME_LARGE=Qwen3.8-27B-oQ4e-mtp    # 品質: ontology / config / report
LLM_TIMEOUT_LARGE=1800                       # 27B の per-call レイテンシ
LLM_TIMEOUT_CONFIG=1800
LLM_TIMEOUT=600
LLM_MAX_ATTEMPTS=5
LLM_REASONING_EFFORT=none

CONFIG_BATCH_PARALLEL=1     # 27B は >1 で oMLX がスタック（§7）
GRAPH_BUILD_PARALLEL=4
OASIS_SEMAPHORE=16

# --- 埋め込みは Ollama を継続利用（oMLX は埋め込みモデルを持たない） ---
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BASE_URL=http://<ollama-host>:11434
EMBEDDING_API_STYLE=ollama
EMBEDDING_API_KEY=ollama
```

oMLX 側: `scheduler.max_concurrent_requests = 32`（余裕枠。n≈16 超はレイテンシのみ増加）、
`server.host = 0.0.0.0`、管理APIキーは `POST /admin/api/setup-api-key` で設定（未設定の間は
推論APIが無認証になります）。

バルクの並列度は `scripts/mirofish_cli.py sim-prepare --parallel-profiles 16` を推奨します。

### モデルの追加・互換性確認

```bash
# oMLX 管理APIで直接ダウンロード（SSH 不要）
curl -X POST -H "Authorization: Bearer $OMLX_API_KEY" -H 'Content-Type: application/json' \
     -d '{"repo_id":"mlx-community/gemma-4-e4b-it-4bit"}' \
     http://<omlx-host>:8010/admin/api/hf/download      # 5.18 GB、約100秒

# ダウンロード前に互換性確認
curl -s https://huggingface.co/<repo>/raw/main/config.json | grep model_type
```

---

## クイックスタート

```bash
cp .env.example .env     # oMLX のホスト/キーを記入（§8）
docker compose up -d neo4j        # MiroFish 専用 Neo4j（ポート 7475/7688）
npm run backend                   # cd backend && uv run python run.py

# CLI でパイプラインを実行
python scripts/mirofish_cli.py ontology   --requirement "..." --file doc.pdf
python scripts/mirofish_cli.py build      --project-id proj_xxxx
python scripts/mirofish_cli.py sim-create --project-id proj_xxxx
python scripts/mirofish_cli.py sim-prepare --simulation-id sim_xxxx --parallel-profiles 16
python scripts/mirofish_cli.py sim-start  --simulation-id sim_xxxx --platform parallel --max-rounds 1
python scripts/mirofish_cli.py sim-close  --simulation-id sim_xxxx     # report の前に必須
python scripts/mirofish_cli.py report-generate --simulation-id sim_xxxx

# 検証（成功を主張する前に実行）
python scripts/verify_outputs.py --simulation-id sim_xxxx
```

注意: シミュレーションはラウンド終了後に *wait mode* に入るため、report 生成の前に
`sim-close` が必要です。また `--max-rounds 1` では設定済みの初期投稿しか出ません
（LLM 駆動のエージェント行動は round ≥ 2 から）。

---

## 運用ルール

運用ガードレール・既知の障害モード・必須設定は [`AGENTS.md`](AGENTS.md) にあります
（R13 に oMLX バックエンド、実測並列度、段階別モデル分割、必須の
`CONFIG_BATCH_PARALLEL=1` を記載）。長く失敗しやすい実行のために、ヘルパースクリプト
（`supervise.sh`, `monitor.sh`, `verify_outputs.py`）と `mirofish-output-verification`
スキルを同梱しています。

---

## ライセンス

AGPL-3.0 — オリジナルの MiroFish プロジェクトと同じです。[LICENSE](./LICENSE) を参照。

## クレジットと帰属

本リポジトリは [666ghj](https://github.com/666ghj) による
[MiroFish](https://github.com/666ghj/MiroFish) の改変 fork です（当初
[Shanda Group](https://www.shanda.com/) の支援）。シミュレーションエンジンは CAMEL-AI チームの
[OASIS](https://github.com/camel-ai/oasis) を使用しています。

本 derivative での変更:
- バックエンドを Ollama から **oMLX (MLX)** on Apple Silicon へ切替、実測に基づく段階別
  モデル分割（bulk = `gemma-4-e4b` / 品質 = `Qwen3.8-27B`）
- OpenAI 互換サーバーでの report 生成退行を修正（`content: null`）
- LARGE 段階の timeout を設定可能に。モデル互換性・並列度のドキュメント化
- Zep Cloud → ローカル Neo4j CE 5.18、UI の英語化、MiroFish-Offline への改名
