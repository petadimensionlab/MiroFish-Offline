# AGENTS.md — MiroFish-Offline 絶対ルール（全エージェント必ず遵守・死守）

このプロジェクトで作業する全エージェントは、以下を**毎回・例外なく**守る。
過去にこれらを守らず、長時間計算の浪費・進捗破壊・ユーザーの強い不満を招いた。

## R0. 最優先: シンプルな方針（個別ルールより、これを守ることを優先する）

1. **モデルは 12b 優先**（精度）。
2. **ctx は基本小さめ**。段階ごとに必要な長さは違うので、概ね大丈夫なよう
   **小さい num_ctx を基本**にし（バルク=8192: NER/profile/simulation）、
   本当に必要な段階（ontology / simulation_config / **report=最終PDF**）だけ大きく（32768）。
   1つの大 ctx で全部回すな。
3. **並列で走らせる**（小さめ ctx で NP/C を実際に効かせ、runner 引数で確認）。
4. **失敗しても回復できるようにする**（途中再開・アトミック書込み・単一サーバ/backend）。
   失敗で完了分を失う/最初からやり直しになる状態にするな。
5. **困ったら方針に戻る**。ルールを増やすより、この方針を適用しているかを確認する。

> 特性: エージェントはこの「シンプルな方針」自体を理解できていないことが多い
> （大ctxで全部回す/並列を潰す/失敗で全損/長時間空振り）。行動前に
> 「小ctx・並列・回復可能」を満たすか自問する。

## R1. 段階ごとにコンテキスト(num_ctx)を使い分ける（最優先）

**1つの大きな num_ctx で全工程を回すな。**

- バルク段階（グラフ構築の NER / **profile生成** / simulation のエージェント行動）は **小さい num_ctx（8192）**。
- 大きい ctx が必要なのは **ontology / simulation_config / report（最終PDF）** のみ。
- 理由: Ollama の runner は `llama-server -c <num_ctx × NP> -np NP` で起動され、
  `-c` が大きいと**並列時の per-request 速度が崩壊**する（実測: num_ctx 32768×NP16 → 1.5 tok/s、
  num_ctx 8192 単発 → 15 tok/s）。
- `extra_body.options.num_ctx` は Ollama の OpenAI 互換 endpoint では**無視**される。
  したがって段階別に**派生モデルを分ける**（`LLM_MODEL_NAME`=小ctx / `LLM_MODEL_NAME_LARGE`=大ctx）。

## R2. 走らせる前に「実測」で確かめる（推測で走らせるな）

- 実行前に **runner 実引数** `-c`/`-np` をログで確認し、意図した num_ctx・NP になっているか照合する。
- **短い計測（2–3分）**で per-request と **完了レート（件/分）** を実測してから本実行する。
- **eval tok/s だけで判断するな。** `n_gen`/`n_tokens` は「プロンプト+出力」であり出力長ではない。
  必ず「完了件数 ÷ 実時間」で評価する。

## R3. 検証を先に行い、証拠を出す

- 各段階の完了時に `python scripts/verify_outputs.py --simulation-id <sim>` を実行し、PASS/FAIL を提示する。
- 宣言した並列数 vs 実スロット vs 実レートを照合する。食い違ったら原因を特定してから報告する。

## R4. 長時間ランは「検証済み構成で1回だけ」

- 闇雲な再起動・NP のスイープ・複数回の長時間試行を禁止する。
- 構成を確定したら1回だけ通し、無駄な電気と時間を使わない。

## R5. 単一性・ポート分離

- `ollama serve` と backend は**各1本**。起動前に `pgrep` で確認する。
- Ollama.app は復活して `-np 1/4` で port 11434 を奪う。**専用ポートに分離**して衝突を断つ:
  `OLLAMA_HOST=127.0.0.1:11500` で起動し、`.env` の `LLM_BASE_URL` /
  `OPENAI_API_BASE_URL` / `EMBEDDING_BASE_URL` を 11500 に向ける
  （`EMBEDDING_API_STYLE=ollama`）。`scripts/lib_ollama.sh` は既定でこのポートを使う。

## R6. 進捗ファイルはアトミック書込み

- temp + `os.replace` + `fsync`。生成中に kill して破損させるな（過去に93→13件消失）。

## R7. 並列の使い分け

- 短出力は並列、長出力・競合時は低並列。`timeout > 2 × per-request`。
- 1 GPU 上で N プロセス + proxy による擬似並列は効かない。

## R8. 学びは即座に書き残す（記憶に頼るな）

- 失敗・知見はこの `AGENTS.md`、`.opencode/skills/mirofish-output-verification/SKILL.md`、
  `README.md`、`docs/handover-qwen3.5-9b.md` にその場で追記する。
- **プロジェクト固有の事項をグローバル設定（`~/.config/opencode/`）に書かない。**

---

## R9. エージェントの失敗パターン（実観測・再発防止 / 「できが悪い」典型）

過去に実際にやらかした。同じことをするな。

1. **長時間ランを end-to-end で測らずに開始**した。`eval tok/s` や中間カウンタだけで
   判断し、**完了レート(件/分)** を実測しなかった → 実は 0.3件/分で 8h コースだった。
2. **ユーザーの指示（バルクは小 ctx）を無視**し、全工程を 1 つの大 ctx で回した
   → `-c` 巨大化で並列が崩壊（15→1.5 tok/s）。
3. **`n_gen`/`n_tokens` を出力長と誤読**（実際はプロンプト+出力）。誤った結論を報告した。
4. **再起動チャーン**で状態を破壊（progress 破損、93→13 件消失）。
5. **スコープ違反**: プロジェクト固有ルールをグローバル設定に書いた。
6. **既知の悪手に時間を費やした**（1 GPU で Nプロセス+proxy 疑似並列）。
7. **宣言と実体の乖離を放置**（config 意図 ≠ runner 実引数）。`-c`/`-np` を確認しなかった。
8. **無目的な網羅スイープ**（NP=2..128 を全部）を長時間実行。短い標本計測で足りた。
9. **報告が両極端**（連投スパム ↔ 出すぎない）。マイルストーンで簡潔に出す。
10. **スコープを自分で縮める判断の欠如**: 機械に無理な規模をそのまま走らせ続けた。
11. **問題点に気づかず放置した**（最重要の悪癖）。実測値の異常 —
    - 生成 `n_gen` ~1810 に対し保存は ~850（**約半分は thinking で無駄**）、
    - 宣言 NP=16 に対し runner は `-np 4`、
    - 観測 latency が `tokens / rate` と不整合 —
    を放置し、指摘されるまで気づかなかった。
    → **毎回「期待値 vs 実測値」を比較**する（生成トークン vs 保存出力、宣言NP vs
    runner実引数、latency vs tokens/rate）。ズレは雑音ではなく**欠陥**として調査する。

## R10. システム特性・観察事項（実測 / 設計時に必ず考慮）

- **Ollama は OpenAI 互換 endpoint の `extra_body.options.num_ctx` を無視する**。
  実効 ctx は**派生モデルの num_ctx だけ**で決まる。→ 段階別に派生モデルを分ける。
- runner は `llama-server -c <num_ctx × NP> -np NP` で起動。**`-c` が大きいと
  並列時に per-request が崩壊**する（8192単発=15 tok/s vs 32768×NP8≈1.5 tok/s）。
- **NP は Ollama が自動で下げることがある**（NP=16 を指定しても runner が `-np 4`）。
  必ず runner 実引数を確認する。
- **アーキテクチャ次第で並列不可**: qwen35 は
  `model architecture does not support parallel requests` → 常に `-np 1`。
- **Ollama.app は復活して port 11434 を `-np 1/4` で奪う**。二重 LISTEN になる。
- **実 profile 長は ~850 トークン**（`n_gen` の ~2500 はプロンプト込み）。誤読注意。
- `reddit_profiles.json` の保存は**アトミック化済み**（temp+`os.replace`+`fsync`）。
- backend 二重起動は同一 progress ファイルを**上書き**する（必ず単一化）。

## R12. 行き詰まったら「一度だけ相談」する（闇雲に試すな）

- アプローチが機能せず、**より良い案を自分で出せない**ときは、バリエーションを
  試し続けず**一度停止して相談**する。出すもの: 現状 / 見えている選択肢 / 推奨案。
- ブラインド再試行は「1回の失敗を何回もの失敗」に変える。短い相談は安く、
  無駄な長時間ランは高い。
- 発想が浅いまま走らせるな。目的に対して筋の良い案を出せないと確信したら相談。

## R11. 段階別モデル（実装済み）

- `LLM_MODEL_NAME` = 小 ctx（バルク: NER / profile / simulation）。
- `LLM_MODEL_NAME_LARGE` = 大 ctx（ontology / simulation_config / **report(最終PDF)**）。
- 実装: `config.py` の `LLM_MODEL_NAME_LARGE`、`llm_client.py` の `use_large`、
  `ontology_generator`/`report_agent` は `use_large=True`、
  `simulation_config_generator` は `LLM_MODEL_NAME_LARGE`。

## R13. oMLX バックエンド（Mac Studio / 100.127.45.60:8010）— 2026-09-21 実測

バックエンドを「ローカル Ollama」から **oMLX 0.6.4 (MLX) on Mac Studio** へ切替。

- ホスト: `shinjimac-studio` = **M1 Ultra / 128GB / 64 GPUコア**（tailnet `100.127.45.60`）。
- **oMLX は既定で `127.0.0.1` のみ bind → tailnet からは見えない。** `server.host=0.0.0.0` が必須。
  ポートは **8010**（Mac Studio の 8000 は別サービス `ds4.c` が使用中）。
- モデル: `gemma-4-e4b-it-4bit`（HF `mlx-community/gemma-4-e4b-it-4bit`, 5.18GB）。
  **Ollama の `gemma4:e4b` は GGUF なので oMLX では使えない**（MLX safetensors が必要）。
- 管理API (`/admin/api/*`) は **API キー必須**。`POST /admin/api/login` が 400
  （"No API key configured"）なら未設定 → `POST /admin/api/setup-api-key` で設定する。
  **キー未設定の間は推論APIが無認証**になるので、設定は事実上の保護にもなる。
- モデル取得（SSH不要・HTTPのみ）:
  `POST /admin/api/hf/download {"repo_id":"mlx-community/gemma-4-e4b-it-4bit"}`
  （`Authorization: Bearer <key>` 付与。5.18GB を約100秒）。
- oMLX の API は `8010/openapi.json` で全列挙できる（FastAPI）。`/admin` に Web UI あり。

### 並列の実測（最重要）

oMLX は `scheduler.max_concurrent_requests`（既定 8）で同時実行数を制御。
**ctx はリクエスト長に応じて動的確保**されるため、Ollama の `num_ctx × NP` 問題は無く、
`8k/32k` の派生モデル分割（R1/R11）は**不要**。ただし `LLM_MODEL_NAME` /
`LLM_MODEL_NAME_LARGE` は**モデル能力での段階分け**として引き続き使用する
（bulk = `gemma-4-e4b-it-4bit` / LARGE = `Qwen3.8-27B-oQ4e-mtp`。下記 `.env` 設定を参照）。

実測 aggregate（M1 Ultra, gemma-4-e4b-it-4bit）:

| 形状 | n=1 | n=8 | n=16 | n=32 |
|---|---|---|---|---|
| 短文 (~40 tok prompt / 512 out) | 76.0 | **174.1** | — | 174.0 |
| profile 相当 (2093 tok prompt / 900 out) | — | 120.6 | **164.5** | 165.0 |

- **短出力は n≈8 で飽和、長プロンプト込みの実形状は n≈16 で飽和**。
- 飽和後は aggregate 横ばいで**レイテンシのみ増加**（実形状 n=32 で p50 86s、n=16 で 57.5s）。
- よって client 並列は「長出力≈16 / 短出力≈4–8」、サーバ側 `max_concurrent_requests` は 32 で余裕を持たせる。

### MiroFish 側の設定（`.env`）

- `LLM_BASE_URL` / `OPENAI_API_BASE_URL` = `http://100.127.45.60:8010/v1`
- `LLM_API_KEY` = oMLX 管理APIキー（推論APIも同じキーを要求）
- `LLM_MODEL_NAME` = `gemma-4-e4b-it-4bit`（bulk: NER / profiles / simulation）
- `LLM_MODEL_NAME_LARGE` = `Qwen3.8-27B-oQ4e-mtp`（ontology / config / report）
  ＋ `LLM_TIMEOUT_LARGE=1800`（27B は per-call が長い: ontology 実測 218s、report 最大 ~153s。
  ontology_generator / report_agent がこの env を読む）。2026-09-21 の A/B で
  **e4b は ontology の entity を技術用語化（誤り）**、12B/27B は社会的主体を生成したことを確認。
- 埋め込みは Mac Studio の Ollama を継続利用:
  `EMBEDDING_BASE_URL=http://100.127.45.60:11434`, `EMBEDDING_API_STYLE=ollama`,
  `EMBEDDING_MODEL=nomic-embed-text`（768次元、`VECTOR_DIMENSION` と一致）。
- `llm_client._is_ollama()` は base_url に `11434` を含む時だけ `extra_body.num_ctx` を送る。
  oMLX (8010) 経路では送られない（oMLX はこのパラメータを無視するので正しい挙動）。
- `reasoning_effort:"none"` と `response_format:{"type":"json_object"}` は oMLX で**受理**され、
  `content` に正常な JSON が返る（reasoning フィールドへ逃げない）。
- 検証: `uv run python` で `LLMClient.chat_json` / `chat`、`EmbeddingService.embed`(768) /
  `embed_batch` / `health_check` の全スモークが PASS。

### oMLX 固有の退行と修正: report 段階が `content: null` で落ちる（2026-09-21）

- 症状: `report-generate` が `status=FAILED`、`expected string or bytes-like object, got 'NoneType'`。
  **モデルに依らず発生**（gemma-4-e4b でも失敗）。Ollama 時代（9/14）のレポートは成功していた
  → **oMLX 切替による退行**。
- 根本原因（実測）: ReACT のシステムプロンプトがツール呼び出し形式を説明しているため、
  oMLX は **native `tool_calls` を返し `content` が `null`** になる
  （`finish_reason: tool_calls`, `content: None`, `tool_calls: [...]`）。
  `llm_client.py` の `re.sub(r'<think>...</think>', '', content)` が `None` で TypeError。
- 修正: `LLMClient.chat()` で `content` が空のとき native `tool_calls` を
  `<tool_call>{"name":...,"parameters":{...}}</tool_call>` テキストへ変換する
  （`report_agent._parse_tool_calls` がまさにこの形式を期待している）。
  修正後、report は 3モデルすべてで COMPLETED を確認。
- 教訓: **OpenAI 互換サーバの `content` は `null` になり得る**。`content` を無条件に
  文字列前提で扱うコードは oMLX で壊れる。`content is None` を常に想定する。

### LARGE=27B 運用の実測と必須条件（2026-09-21 通し実行）

`LLM_MODEL_NAME_LARGE=Qwen3.8-27B-oQ4e-mtp` で全工程を通した結果:

| 段階 | モデル | 実測 |
|---|---|---|
| ontology | 27B | 220s |
| profiles 157（C=16） | e4b | 12.5分 |
| simulation_config（11バッチ） | **27B** | **26.9分**（e4b の 3m03s の約8.8倍） |
| simulation（1 round） | e4b | 4秒（初期投稿のみ。LLM行動は round≥2 で発生） |
| report | **27B** | 約19分 |
| `verify_outputs.py` | — | **18/18 PASS** |

- **必須: `CONFIG_BATCH_PARALLEL=1`。** 27B を 8並列で投げると **oMLX がスタック**する
  （実測: 発行後 `active=8` のまま約9分間、prompt/completion/メモリ/ログすべて停止。
  クライアントを切ると `active=0` に復帰）。27B は並列で aggregate が伸びない
  （n=1:20.0 / n=8:22.9 tok/s）ので C=1 が最適でもある。
- **`LLM_TIMEOUT_LARGE`（既定1800）**: ontology / report の LLMClient は元々 300s 固定で、
  27B では余裕が無い（ontology 実測 218s、report 単発最大 ~153s）。
- 無害だが記録すべき警告: `response_format requested but grammar-constrained decoding is
  unavailable; output will not be schema-enforced (falling back to prompt injection)`。
  === **JSON は文法制約されない**（`--with-grammar`/xgrammar 未導入のため prompt 誘導のみ）。
  実測では parse_fail=0 だったが、**構造化出力の堅牢性は下がる**点に注意。



### 実パイプライン実測（2026-09-21, `sim_0ceefc296227`, 157エージェント）

`sim-prepare --parallel-profiles 16` を実走した結果:

| 項目 | 実測値 |
|---|---|
| prepare 全体 | **16m21s**（profile 13m03s + config 3m03s） |
| profile 生成レート | **12.03 profiles/min** |
| aggregate（prepare 全体） | **145.6 tok/s** |
| oMLX 呼び出し / completion | 174 calls / 142,874 tokens |
| 平均出力 | 910 tokens/profile |
| 宣言 vs 実スロット | `--parallel-profiles 16` → **active=16, waiting=8**（一致・乖離なし） |
| `verify_outputs.py` | **17/18 PASS**（FAIL は未実行の simulation.db のみ＝想定内） |
| 品質 | truncation 0 / rule-based 0 / client timeout 0 / parse-retry 2 |

- マイクロベンチ（同形状 n=16）の 164.5 tok/s に対し実走は **145.6 tok/s ≈ 88%**。
  形状のばらつき・prefill 混在・retry 分だけ低い。**実走 ETA はこの係数を見込む**。
- profile 1件 ≈ 910 completion tokens。16並列で約 5 秒/件 → 157件で約13分。
- config 生成は 11 バッチ（`CONFIG_BATCH_PARALLEL=8`）で約3分。
- 実測から: **`--parallel-profiles 16` は妥当**（飽和点一致）。これ以上上げても aggregate は伸びない。


