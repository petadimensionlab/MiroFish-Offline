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

## R14. 禁止事項（2026-10-11 確定・全遵守）

1. **禁対象データの利用禁止**
   - `workspace/HOIKORO/dataAnalysis` の調査データ・コード・派生成果物は利用禁止（許可なき限り）。
     当該パイプラインの呼称も禁止。以後「禁対象データ」と書く。
   - persona・出力に**個人を特定できる情報（実名・ID・連絡先等）を残さない**。
     個人特定不能のマスキング済み状態でのみ利用可。
2. **persona は変数ホワイトリストのみで作る**
   - 使用変数は**57件**。除外13件（旧個社系11 + 部門の焦点系2）を persona に含めない。
   - 除外13件: `org_ident_legacy` / `comm_legacy_*`(6) / `infl_legacy_*`(4) /
     `dept_prevention_focus` / `dept_promotion_focus`。
3. **会社特有語の出力禁止**
   - 旧社グループ名・旧個社・買収・合併・業界語（ハム等）を生成物に含めない。
     マスキング監査（禁止語スキャン0件）を必ず通過させる。
4. **生成後検証の必須化**
   - 生成物は スクリプト `scripts/verify_outputs.py` とマスキング監査を通してから報告する。
5. **リテラル破壊の禁止（R9#12 と同旨）**
   - tool 引数やコードにバッククオート・実改行などの制御文字を書くな。`chr()` で構築する。
   - 破損したら edit 再入力でなく python ヘルパーでパッチし `ast.parse` で検証する。
   - 停止を引き起こす並びは**絶対に出力・エコーしない**。長い自己分析ではなく具体修正1回を実行。
6. **実行規律（R2/R4）**
   - 本実行前に小サンプルで実測（レート・audit）し、本実行は検証済み構成で1回だけ。

### なぜこれらが禁止なのか（理由）

1. **禁対象データ**: 実在する調査回答者のデータと、特定企業の内情。無断利用・再識別は調査対象者と企業に害を及ぼす。元から匿名化されていても、属性の組み合わせで個人や企業が推定され得るため「呼称すら出さない」運用にする。
2. **変数ホワイトリスト**: persona が M&A・旧社特有の概念を帯びると、その調査特有の文脈（合併・旧社間の力学）がシミュレーション全体に刷り込まれ、一般化できない結果になる。Persona は「誰にでも通用する人の傾向」だけで作る。
3. **会社特有語**: 旧社グループ名や業界語が生成物に残ると、それが手掛かりになって企業が特定される。マスキング監査でヒット0件を義務化するのはこのため。
4. **生成後検証**: 「生成した=完了」と報告すると実体と乖離し信用を失う。ファイル形式・一意性・監査を機械的に確認してから報告する。
5. **リテラル破壊**: 私の入力操作で制御文字が実体として混入し、1つの構文エラーを数ターンかけて繰り返す停滞を起こした。同じことを防ぐため、「文字列は書かない」をデフォルトにする。
6. **実行規律**: 長い無音の本実行を繰り返すと、待たされる側の確認手段がなくなる（途中停止・再走が頻発）。小サンプル実測でレートと監査を先に見せ、本実行は検証済み設定で1回に絞る。

### 具体事例（2026-10-11 当日）→ 次はこうする

| 何が起きたか | 結果 | 次回の守り方 |
|---|---|---|
| 禁対象データから persona を生成 | 利用禁止→呼称禁止→全削除・出戻り | R14-1: 使わないか、個人特定不能にマスクした形のみ、呼称も出さない |
| persona に旧個社・合併概念を混入 | 「会社の特殊事情が入ってる」「また間違ってる」 | R14-2: 使用57変数のみ。統合(問題解決)などの紛争処理尺度と合併「統合」を区別 |
| 生成文に旧社名・ハム等が残る恐れ | SCAN で0件確認して初めて提示 | R14-3: 禁止語スキャン0件を必須 |
| 完了報告が検証前 | 「信用できない」 | R14-4: verify_outputs + 監査を先に通す |
| 正規表現に実改行を書いて構文エラー、3回以上停滞 | 大量の言い直しで作業停滞 | R14-5: tool 引数に制御文字を書かず、`chr()`/`join` で構築、直後に `ast.parse` |
| 100体生成を無音で開始、途中でabort | 無駄な時間、再起動 | R14-6: 短い本実行前に10体等の小サンプルでレートと audit を提示 |

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



## R15. 日本語レポート文体規準（natural-japanese 準拠・2026-10-12）

- 出典: https://github.com/coji/natural-japanese（MIT）。文体憲法12条に従う。
- **前置き禁止**: 見出しと第一文で結論を渡す。要約の反復で結ばず、So What まで書く。
- **見出しに結論を埋める**: ラベルだけの見出しは書かない。同じ鋳型を3回続けない。
- **省略形・自作の造語禁止**: 禁止語リストの略称・run番号・strong/weak 等の独自略語はレポート本文で使わない。
  専門・学術用語の略（OASIS, LLM, SNS, Reddit 等）とカタカナ語はそのまま認める。
- **機械翻訳調を避ける**: 「〜を持つこと」「〜することが可能」等の直訳構文ではなく
  自然な日本語で書く。和訳を無理にせずカタカナ表記を用いる。
- **検証**: レポート提出前に `uv run scripts/natural-japanese/scripts/lint.py <file> --genre tech`
  を実行し、検出0件を確認する（検出は機械、判断は人間の方針）。
- スキル本体: https://github.com/coji/natural-japanese（手動配置で取り込み済み・
  `uv run .../lint.py .../outline.py .../terms.py` が単体で使える）

## R16. TensorFold 0.6.6 バックエンド（本机 M1 Ultra / 127.0.0.1:11510・11511）— 2026-10-25 実測

- 起動: `scripts/start_tensorfold.sh`（停止 `--stop`）。TensorFold は **ashhart/TensorFold**。
  **native 1.1.4 は M1 Ultra を refuse**（`GemmaNeedsTensorUnits` / `UnqualifiedQwenShape`、
  M5 系 Tensor Units 必須）。**Python lane engine の 0.6.6** を使う:
  `brew install ashhart/tensorfold/tensorfold@0.6`（kag-only → `/opt/homebrew/opt/tensorfold@0.6/bin/tensorfold`）。
- **Native 1.1.4 は e4b/27B を refuse、0.6.6 lane engine は e4b(26B MoE) のみ OK**:
  27B は native で `MCPFTV2KVDescriptorMismatch` で落ちる → **0.6.6 lane engine で serve** する
  （両方 0.6.6 で動かすのが最も安定。起動は `scripts/start_tensorfold.sh`）。
- サーバ構成（port 固定・単一 key `~/.tensorfold/api_key`）:
  - bulk `:11510` = `mirofish` = `mlx-community/gemma-4-26b-a4b-it-4bit`（MoE, --no-thinking, --parallel 8, ctx 16384）
  - large `:11511` = `mirofish-27b` = `TensorFold/Qwen3.8-27B-MLX-4bit`（tested checkpoint,
    **--reasoning-effort low + --decode-share 100**, --parallel 16, ctx 8192）
- **Flash Next は不採用（ユーザー明示禁止・2026-10-26）**。再提案・再 pull しないこと。
- 実測（200tok prompt / 512 out、集約 tok/s）:

  | 形状 | n=1 | n=4 | n=8 | n=16 | n=32 | n=64 |
  |---|---|---|---|---|---|---|
  | 26B MoE bulk (no thinking) | 74 | 71 | ~65 | — | — | — |
  | **27B low thinking (ds=100)** | ~52 | 64 | 81 | **84** | 91 | 94 |

  → **27B は TensorFold で並列可**（Ollama qwen35 の `-np 1` 直列・oMLX 27B の C=1 必須と違い、
  **同一ポートで n=16 まで実効並列**）。n>16 は集約ほぼ横ばいでレイテンシのみ増（n=64 p-max 347s）。
  **`--decode-share 100` が鍵**: 既定(~50%)では n=8 で 40 tok/s しか出なかった（大バルク prefill に
  時間を取り分られる）。M1 Ultra には Tensor Units が無いので lane decoder は 128-row shared round
  でも 1-row と同コスト（ログの "ms by rows" 表がそれを示す）→ prefill に時間を取り分るほど decode が餓える。
- 27B は思考モデル: `reasoning_effort: "low"` で thinking 量を抑制しつつ answer を返す（oMLX 時代と
  同様に **`content` が空で `reasoning_content` に全消費** になり得る → `max_tokens` は 1024+ を置く。
  512 指定では finish_reason=length で content が空になった実測あり）。
- lmstudio-community/Qwen3.5-9B-MLX-4bit は 0.6.6 でも **UnqualifiedQwenShape** で不可
  （9B 系は TensorFold kernels 非対応）。e4b/12B dense Gemma も不可（MoE のみ）。
- **Qwen3.8-Flash-Next**: 0.6.6 で serve 可能（`--ssd-experts 20` で weights 25GiB常駐+expert を
  SSD stream、ロード 5分）だが実測 n=8 で集約 13 tok/s と遅い（本番不採用。oMLX 27B のまま）。
  **ユーザーが明示禁止（2026-10-26）** — 再提案・再 pull しない。
- 比較（oMLX e4b 実測 145.6 tok/s @n=16）: TensorFold 26B bulk n=8 ≈ 65-72 tok/s と見劣りするが、
  **27B low thinking が並列で使える**のが利点（oMLX では 27B は C=1 必須・stall 経験あり）。
- **xgrammar 未導入（0.6.6・本机）**: `response_format:{"type":"json_object"}` は **HTTP 400**
  （"structured output needs xgrammar on the server"）。oMLX 時代のような無音フォールバックは
  起きない。→ `llm_client.chat()` に 400 検知フォールバック（prompt-only JSON 誘導へ切替、
  サーバごとに一度だけ検知・キャッシュ、以後 response_format を送らない）を実装済み・2回検証 PASS。
  **xgrammar 導入済み・稼働中（2026-10-26 確定）**: TensorFold 0.6.6 は pure python の
  xgrammar を import する（`engine/grammar.py:112,125` — `import xgrammar as xgr` と
  `from transformers import PreTrainedTokenizerFast`）。`pip install xgrammar` で
  transformers も共に入るので、libexec 側（`/opt/homebrew/Cellar/tensorfold@0.6/0.6.6/libexec/bin/python`）に
  導入→サーバ重启すれば `response_format:{"type":"json_object"}` が **400 ではなく制約付き生成で通る**
  （実測: 初回 compile ~2s、以降 cached、コードフェンスなしの素の JSON が返る）。
  backend/.venv 側の導入は不要（フォールバックは保険として残置）。
  `response_format: json_schema`（strict）も検証済み: schema 準拠の JSON が返り型一致。
  grammar 制約下でも per-call レートは実測維持（profile 10体@n=8 = 72s、8.3体/分）。
- **large ctx=8192 の制約**: TensorFold は prompt+max_tokens が ctx を超えると 400 で拒否
  （Ollama のような暗黙 truncation はしない）。ontology は prompt ≤4k 文字＋max_tokens 4096 で
  実測 OK（147s、entity_types=10）。大きな文書は前処理で切るか `TF_LARGE_CTX` を上げる。
  **report planning も同じ制約**: planning プロンプト ~4925tok + max_tokens 4096 = 9021tok で
  400 を実測（2026-10-27 23:57）。対策: planning/生成の max_tokens を 3000 に下げ、レポートを
  数セクションに分けて合成する（`TF_LARGE_CTX` を 16384 に上げる手もあるが model_config の
  content_length を超えるため再起動が要る）。`report_agent.py` の planning/生成 calls に
  max_tokens 指定が必要。
- **simulation env は fresh 起動が必須**（run_reddit_simulation.py:590-592 が起動時に
  `reddit_simulation.db` を **os.remove して再生成**する）。前回の DB を再利用すると
  OASIS/camel が history 復元で無限 loop に入り、server 応答は正常でも無限に固まる
  （396 体分 ~450 件の API 呼び出しが `Load Agent Profile...` で空振りした実測あり）。
  再起動なら `kill -STOP` で確認後、DB をリネームして fresh 起動すること。
  **修正済み**: `os.remove(db_path)` を **`os.rename(db_path, db_path+".prev_<ts>")`** に変更
  （run_reddit_simulation.py:592 付近）。これで OASIS は毎回 fresh DB を作り、残留 DB による
  history 復元ハングが再発しない。旧データは `.prev_*` で残る。
- **シミュレーション実行の待受ハング（2026-10-27 確定）**: run_reddit_simulation の
  `_shutdown_event` は `main()` で `global` 宣言されず `setup_signal_handlers()` の
  **ローカル変数**に束縛される（run_reddit_simulation.py:744 付近）。そのため SIGTERM に対して
  ハンドラは `global _shutdown_event` で **module 側の None（truthy 判定をすり抜け `await
  wait_for(None.wait())` → TypeError）** を参照し、環境 loop は終了しない。server 応答正常でも
  `Simulation already running` が残り、**round が一切進まない**（`round 0/2` 固定）。
  回避策: 親を kill して module の待受を閉じ、`fresh_state()` で PID 生死判定→stale を消し、
  DB をリネームしてから start すること（`/tmp/sim_run_foreground.py` に実装）。
  **修正済み**: `main()` に `global _shutdown_event` を追加（:720）し、IPC 待受ループの
  `wait_for(_shutdown_event.wait(), 0.5)` の `except TimeoutError: break` を **`continue`** に修正
  （:698 付近）。timeout で抜けていたため待受が1回で終了し `Environment closed` →
  COMPLETED 扱いになっていた。これで round 完遂後の interview 待受が正しく動く。
  **追加修正（2026-10-27 確定）**: `setup_signal_handlers()` を `asyncio.run(main())` の
  **前**に呼ぶよう修正（スクリプト末尾の `if __name__` で既に呼ばれていたが、main() 内の
  `_shutdown_event = asyncio.Event()` が **モジュール側 None を上書き**し、ハンドラは
  古い None を参照していた）。これで SIGTERM がイベントを set → env loop が break →
  環境が正常 close する。同時に **シグナルハンドラは `return` で抜ける**（`sys.exit(1)` は
  asyncio.run の cleanup をスキップして env loop が孤児化→无限ハングの原因）。
  残課題: OASIS 側 env の round ループが `posts` を増やさない（`Load Agent Profile...`
  後、profile 596体の LLM 生成が始まらず停止。原因は camel の agent_graph
  `process_agent(i)` で `await asyncio.gather(*tasks)` の 596 並列が bulk server
  の prompt cache 上限で絞まる可能性。  `wait_for(_shutdown_event.wait())` を `continue` で
  抜けるよう修正済みだが、round loop が進行するか別途要調査）。
- **シミュレーション実行の待受ハング（2026-10-27 確定）**: run_reddit_simulation の
  `_shutdown_event` は `main()` で `global` 宣言されず `setup_signal_handlers()` の
  **ローカル変数**に束縛される（run_reddit_simulation.py:744 付近）。そのため SIGTERM に対して
  ハンドラは `global _shutdown_event` で **module 側の None（truthy 判定をすり抜け `await
  wait_for(None.wait())` → TypeError）** を参照し、環境 loop は終了しない。server 応答正常でも
  `Simulation already running` が残り、**round が一切進まない**（`round 0/2` 固定）。
  回避策: 親を kill して module の待受を閉じ、`fresh_state()` で PID 生死判定→stale を消し、
  DB をリネームしてから start すること（`/tmp/sim_run_foreground.py` に実装）。
  **修正済み**: `main()` に `global _shutdown_event` を追加（:720）し、IPC 待受ループの
  `wait_for(_shutdown_event.wait(), 0.5)` の `except TimeoutError: break` を **`continue`** に修正
  （:698 付近）。timeout で抜けていたため待受が1回で終了し `Environment closed` →
  COMPLETED 扱いになっていた。これで round 完遂後の interview 待受が正しく動く。
  **追加修正（2026-10-27 確定）**: `setup_signal_handlers()` を `asyncio.run(main())` の
  **前**に呼ぶよう修正（スクリプト末尾の `if __name__` で既に呼ばれていたが、main() 内の
  `_shutdown_event = asyncio.Event()` が **モジュール側 None を上書き**し、ハンドラは
  古い None を参照していた）。これで SIGTERM がイベントを set → env loop が break →
  環境が正常 close する。同時に **シグナルハンドラは `return` で抜ける**（`sys.exit(1)` は
  asyncio.run の cleanup をスキップして env loop が孤児化→无限ハングの原因）。
  **追加修正（2026-10-27 確定）**: `setup_signal_handlers()` を `asyncio.run(main())` の
  **前**に呼ぶよう修正（スクリプト末尾の `if __name__` で既に呼ばれていたが、main() 内の
  `_shutdown_event = asyncio.Event()` が **モジュール側 None を上書き**し、ハンドラは
  古い None を参照していた）。これで SIGTERM がイベントを set → env loop が break →
  環境が正常 close する。同時に **シグナルハンドラは `return` で抜ける**（`sys.exit(1)` は
  asyncio.run の cleanup をスキップして env loop が孤児化→无限ハングの原因）。
  **SIM_NO_WAIT**: simulation_runner.py に `SIM_NO_WAIT=1` で child に `--no-wait` を
  渡す分岐を追加。Flask サーバなしの CLI 実行では round 完遂後の interview 待受が
  永久に空振りするため、env を閉じて exit させる（round 完遂→COMPLETED を実測確認）。
  **round loop の進行を実測（2026-10-27 21:12）**: `SIM_NO_WAIT=1` + DB リネーム +
  fresh `run_state.json` で 12 rounds が完遂（posts=7, comments=79, acts=249,
  COMPLETED）。前回の「posts が増えない」は **env 待受ハングと残留 DB が原因**で、
  OASIS 側は正常。`agents_per_hour_max` は 5-60、activity_level の平均 0.6 × 596体
  で action/round ≈ 359 となり、72 rounds なら 2.6 万アクション（bulk 集約 65-72 tok/s
  で約 10 時間）。検証は 12 rounds 相当のスケール（~30 分）で回せる。
  **残留ハングの再発防止**: run_state.json の `runner_status` を `stopped` にし、
  DB を `.test_<ts>` でリネーム退避してから `start_simulation` を呼ぶこと。
  **再現実測（22:35）**: 同じ手順で 12 rounds = 700s（posts=7, comments=76, acts=196,
  COMPLETED、verify_outputs 15/15 PASS）。`attempt to write a readonly database` は
  リネーム退避済みの DB を後から読んだときのみで、実行中の DB では発生しない。
  手順は `/tmp/sim_run_bounded.py`（PID 生死判定→stale run_state クリア→DB リネーム→
  SIM_NO_WAIT=1 で start→20秒間隔で posts/cmts/acts を監視）。
  **残課題（2026-10-27 23:31 再発）**: 3度目の再実行（`/tmp/sim_run_forever.py` +
  監視スクリプトの二重起動）では round 10 付近でまた `attempt to write a readonly
  database` で FAILED。**原因は実行中に reddit_simulation.db を別プロセスが
  os.remove/os.rename したこと**（run_reddit_simulation.py は env.reset() 前に
  os.remove する設計＋私の監視スクリプトが run 中に DB を触った）。
  → **教訓**: run 中に DB を削除/リネームしない。再実行手順を固定:
  `kill -9` 残留プロセス確認 → DB 退避（run 開始**前**のみ）→ run →
  `tail -F simulation.log` で `Simulation process exited` を待つ。
  **修正済み**: `os.remove(db_path)` を **`os.rename(db_path, db_path+".prev_<ts>")`** に変更
  （run_reddit_simulation.py:592 付近）。これで OASIS は毎回 fresh DB を作り、残留 DB による
  history 復元ハングが再発しない。旧データは `.prev_*` で残る。
  **残課題（2026-10-27 23:31 再発）**: 3度目の再実行（`/tmp/sim_run_forever.py` +
  監視スクリプトの二重起動）では round 10 付近でまた `attempt to write a readonly
  database` で FAILED。**原因は実行中に reddit_simulation.db を別プロセスが
  os.remove/os.rename したこと**（run_reddit_simulation.py は env.reset() 前に
  os.remove する設計＋私の監視スクリプトが run 中に DB を触った）。
  → **教訓**: run 中に DB を削除/リネームしない。再実行手順を固定:
  `kill -9` 残留プロセス確認 → DB 退避（run 開始**前**のみ）→ run →
  `tail -F simulation.log` で `Simulation process exited` を待つ。
  **修正済み**: `os.remove(db_path)` を **`os.rename(db_path, db_path+".prev_<ts>")`** に変更
  （run_reddit_simulation.py:592 付近）。これで OASIS は毎回 fresh DB を作り、残留 DB による
  history 復元ハングが再発しない。旧データは `.prev_*` で残る。
  **再現実測（22:35）**: 同じ手順で 12 rounds = 700s（posts=7, comments=76, acts=196,
  COMPLETED、verify_outputs 15/15 PASS）。`attempt to write a readonly database` は
  リネーム退避済みの DB を後から読んだときのみで、実行中の DB では発生しない。
  手順は `/tmp/sim_run_bounded.py`（PID 生死判定→stale run_state クリア→DB リネーム→
  SIM_NO_WAIT=1 で start→20秒間隔で posts/cmts/acts を監視）。
  **残課題（2026-10-27 23:31 再発）**: 3度目の再実行（`/tmp/sim_run_forever.py` +
  監視スクリプトの二重起動）では round 10 付近でまた `attempt to write a readonly
  database` で FAILED。**原因は実行中に reddit_simulation.db を別プロセスが
  os.remove/os.rename したこと**（run_reddit_simulation.py は env.reset() 前に
  os.remove する設計＋私の監視スクリプトが run 中に DB を触った）。
  → **教訓**: run 中に DB を削除/リネームしない。再実行手順を固定:
  `kill -9` 残留プロセス確認 → DB 退避（run 開始**前**のみ）→ run →
  `tail -F simulation.log` で `Simulation process exited` を待つ。
  **修正済み**: `os.remove(db_path)` を **`os.rename(db_path, db_path+".prev_<ts>")`** に変更
  （run_reddit_simulation.py:592 付近）。これで OASIS は毎回 fresh DB を作り、残留 DB による
  history 復元ハングが再発しない。旧データは `.prev_*` で残る。
  **再現実測（22:35）**: 同じ手順で 12 rounds = 700s（posts=7, comments=76, acts=196,
  COMPLETED、verify_outputs 15/15 PASS）。`attempt to write a readonly database` は
  リネーム退避済みの DB を後から読んだときのみで、実行中の DB では発生しない。
  手順は `/tmp/sim_run_bounded.py`（PID 生死判定→stale run_state クリア→DB リネーム→
  SIM_NO_WAIT=1 で start→20秒間隔で posts/cmts/acts を監視）。
  残課題: OASIS 側 env の round ループが `posts` を増やさない（`Load Agent Profile...`
  後、profile 596体の LLM 生成が始まらず停止。原因は camel の agent_graph
  `process_agent(i)` で `await asyncio.gather(*tasks)` の 596 並列が bulk server
  の prompt cache 上限で絞まる可能性。  `wait_for(_shutdown_event.wait())` を `continue` で
  抜けるよう修正済みだが、round loop が進行するか別途要調査）。
- **シグナルハンドラ二重束縛の修正（2026-10-27 確定）**: `main()` に `global _shutdown_event`
  を追加（:743）、`setup_signal_handlers()` のハンドラも `global _cleanup_done, _shutdown_event`
  を宣言（:768）。これで SIGTERM が module の event を set し env loop が break する。
  ハンドラは最後に `return`（`sys.exit(1)` は asyncio.run の task cancel をスキップして
  env loop を孤児化させ无限ハングの原因）。`wait_for(...)` は `TimeoutError: continue`、
  `break` はイベントが**実際に set されたときだけ**通る。
- Ollama は **embedding 専用**（:11434 nomic-embed-text）に残す。LLM 経路の切替は .env の
  LLM_BASE_URL / OPENAI_API_BASE_URL / LLM_API_KEY / OPENAI_API_KEY を 11510 と api_key の値へ。
- **ディスク注意**: HF cache に未使用の大容量 checkpoint を pull すると swap スラッシング
  （本机で 45GiB swap 使用・全サーバ応答不能化）に直結する。pull は `tensorfold plan`/`info` で
  必要を確認してから。不要な dump は即削除すること。
