# Handover: qwen3.5:9b で MiroFish を安定・並列実行する

> **絶対ルール（死守）は `AGENTS.md` を参照。要約:**
> 1. **段階別 ctx**: バルク(NER/profile/simulation)=小ctx 8192 / ontology・config・**report(最終PDF)**=大ctx 32768。
>    1つの大ctxで全部回すな（`extra_body.num_ctx` は無視される → 派生モデルを分ける: `LLM_MODEL_NAME` / `LLM_MODEL_NAME_LARGE`）。
> 2. **走らせる前に実測**: runner `-c`/`-np` を確認し、2–3分計測で **完了レート** を実測（eval tok/s だけで判断しない。`n_gen` はプロンプト+出力）。
> 3. **検証**: `scripts/verify_outputs.py` を各段階で実行し証拠を出す。
> 4. **検証済み構成で1回だけ実行**。闇雲な再起動/スイープ禁止。単一 ollama/backend。書込みはアトミック。

引き継ぎ用メモ。gemma4:e4b では全工程（graph build → prepare → simulation(1 round) → report）を
完走済み。次は **qwen3.5:9b（weight が大きい）** で同様に安定・並列実行するための見積りと手順。

最終更新: 2026-09-13

---

## 0. 現在の状態

- 完了済み sim: `sim_39b482b9c3c9`（project `proj_b44cd4c0e1ff`）
- 成果物: `backend/uploads/reports/report_1ccb2059ee5d/`（full_report.md / .html / .pdf）
- プロセスは **停止済み**（PC を閉じるため `scripts/stop_all.sh` 実行）

## 1. 既知の良好構成（gemma4:e4b 実績）

`.env`（gemma4 時）:
```
LLM_MODEL_NAME=gemma4-32k
OLLAMA_NUM_PARALLEL=16
OLLAMA_MAX_LOADED_MODELS=2
OLLAMA_MAX_QUEUE=256
OLLAMA_KEEP_ALIVE=30m
OLLAMA_CONTEXT_LENGTH=4096
OLLAMA_NUM_CTX=4096
OLLAMA_NUM_CTX_ONTOLOGY=65536
OLLAMA_NUM_CTX_CONFIG=32768
CONFIG_BATCH_PARALLEL=8
GRAPH_BUILD_PARALLEL=4
LLM_TIMEOUT=300
LLM_MAX_ATTEMPTS=5
LLM_TIMEOUT_CONFIG=900
```

- 派生モデル `gemma4-32k`: `PARAMETER num_ctx 32768`（Ollama の OpenAI 互換 endpoint は
  `extra_body.options.num_ctx` を無視するため、**モデル既定の num_ctx を派生モデルで固定**する）
- 実測（gemma4-4k, num_ctx 4096）: NP=64 → ~305 tok/s / NP=128 → ~270 tok/s。
  gemma4 は **sliding-window KV** のためメモリは NP にほぼ依存せず ~16-17 GiB。
- ハード上限: **`OLLAMA_NUM_PARALLEL <= 256`**（llama.cpp の `n_seq_max <= 256`。512 は失敗）
- 方式: prepare の profile 生成は **16並列**、config の agent バッチは **8並列**、graph build は **4並列**

## 2. qwen3.5:9b は gemma4 と何が変わるか

- **weight が大きい** → モデル本体の常駐メモリが増える
- KV 方式が **フルアテンション寄り**（sliding-window でない）可能性が高い
  → **メモリ = weights + KV/token × num_ctx × NUM_PARALLEL** が支配的になる
  （gemma4 のように「NP を増やしてもメモリ一定」とは限らない）
- 速度は aggregate が小さくなる可能性 → **タイムアウトを再計算**する必要がある

### 2.1 最初に測る（必須）

```bash
ollama pull qwen3.5:9b
ollama show qwen3.5:9b          # architecture / parameters / context / quantization
ollama show --modelfile qwen3.5:9b > /tmp/q.modelfile
grep -n num_ctx /tmp/q.modelfile
```

GGUF メタデータから KV パラメータを取得（block_count / head_count_kv / key_length）:
```bash
python3 - <<'PY'
import glob,os,struct
b=sorted(glob.glob(os.path.expanduser('~/.ollama/models/blobs/sha256-*')),key=os.path.getsize,reverse=True)
# 対象モデルの blob を選ぶ（サイズで概ね特定）
path=next(x for x in b if 4_000_000_000 < os.path.getsize(x) < 20_000_000_000)
f=open(path,'rb'); assert f.read(4)==b'GGUF'
struct.unpack('<I',f.read(4)); struct.unpack('<Q',f.read(8)); kvc=struct.unpack('<Q',f.read(8))[0]
SZ={0:1,1:1,2:2,3:2,4:4,5:4,6:4,7:1,10:8,11:8,12:8}; FM={0:'<B',1:'<b',2:'<H',3:'<h',4:'<I',5:'<i',6:'<f',7:'<?',10:'<Q',11:'<q',12:'<d'}
def rs():
    n=struct.unpack('<Q',f.read(8))[0]; return f.read(n).decode('utf-8','replace')
for _ in range(kvc):
    k=rs(); t=struct.unpack('<I',f.read(4))[0]
    if t==8: v=rs()
    elif t==9:
        if k.startswith('tokenizer'): break
        et=struct.unpack('<I',f.read(4))[0]; n=struct.unpack('<Q',f.read(8))[0]
        if et==8: break
        for _2 in range(n): f.read(SZ[et])
        v=f'array[{n}]'
    else: v=struct.unpack(FM[t],f.read(SZ[t]))[0]
    if any(w in k for w in ('block_count','head_count','key_length','value_length','embedding_length','context_length','architecture')):
        print(f"  {k} = {v}")
PY
```

`KV_bytes_per_token = 2 × block_count × head_count_kv × key_length × 2`

## 3. メモリ見積り（128 GB / M3 Max）

予算: Docker VM ~19G + OS/アプリ。**KV に回せるのは概ね 80〜90G、余裕（unused）は 25G 以上を維持**。

```
KV_total = KV_bytes_per_token × num_ctx × NUM_PARALLEL
総メモリ ≈ weights + KV_total + scratch
```

例（フルアテンションで 144 KiB/token と仮定した場合の目安）:

| NUM_PARALLEL | num_ctx | KV_total | 目安 |
|---|---|---|---|
| 8 | 8192 | ~9 GiB | 安全な開始点 |
| 16 | 8192 | ~18 GiB | 推奨開始点 |
| 16 | 16384 | ~36 GiB | 余裕あり（weights 次第）|
| 32 | 16384 | ~72 GiB | 上限付近（要実測）|

※ qwen3.5:9b の実 KV/token は §2.1 で必ず実測して置き換えること。
※ **gemma4 と違い NP 増加でメモリが増える**前提で計算する。

## 4. タイムアウトと所要時間（並列時の計算式）

```
per-request rate ≈ aggregate_throughput / NUM_PARALLEL
per-unit time    ≈ tokens_per_unit × NUM_PARALLEL / aggregate_throughput
timeout          > per-unit time × margin(1.5〜2x)
総時間           ≈ total_tokens / aggregate_throughput
```

- **並列化すると1件は遅くなる**。concurrency 1 で足りた timeout は N で不足する。
- 変更は **1ノブずつ**、前後を測定して記録。
- config の 1 バッチは 15 エージェント分の大きな JSON（~2,000〜3,000 tok）。
  qwen が遅い場合は `LLM_TIMEOUT_CONFIG` を大きめ（1800 など）に。

## 5. 推奨開始点（保守的）

`.env`:
```
LLM_MODEL_NAME=qwen3.5-8k        # 派生モデル（下記）
OLLAMA_NUM_PARALLEL=8
OLLAMA_MAX_LOADED_MODELS=2
OLLAMA_MAX_QUEUE=256
OLLAMA_KEEP_ALIVE=30m
OLLAMA_CONTEXT_LENGTH=8192
OLLAMA_NUM_CTX=8192
OLLAMA_NUM_CTX_ONTOLOGY=65536
OLLAMA_NUM_CTX_CONFIG=32768
CONFIG_BATCH_PARALLEL=4
GRAPH_BUILD_PARALLEL=4
LLM_TIMEOUT=600
LLM_MAX_ATTEMPTS=5
LLM_TIMEOUT_CONFIG=1800
```

派生モデル作成:
```bash
ollama show --modelfile qwen3.5:9b > /tmp/q.modelfile
printf '\nPARAMETER num_ctx 8192\n' >> /tmp/q.modelfile
ollama create qwen3.5-8k -f /tmp/q.modelfile
```

計測して問題なければ NP を 16 → 32 へ段階的に上げる（`ollama ps` で 100% GPU、
`vm.swapusage` の used が増えないことを確認しながら）。

## 6. 監視・復旧（そのまま使える）

```bash
# 実行（GUIなし・1ターミナル）
./scripts/run_cli.sh resume --project-id <proj> --run --max-rounds 1 --parallel-profiles 8

# 監視（多信号, STALL=600s, LLM活動シグナル付き）
./scripts/supervise.sh
./scripts/monitor.sh 60            # /tmp/mirofish-monitor.log
tail -f /tmp/mirofish-supervise.log
```

- **途中再開（後戻りなし）**:
  - profiles: `reddit_profiles.json` を `user_id` で再利用
  - config: `simulation_config.partial.json` にアトミック保存 → 完了分をスキップ
- **停止**: `./scripts/stop_all.sh`（`--ollama` / `--neo4j` / `--all` も可）
- 既知の注意: macOS の Ollama.app/launchd が `-np 1` で競合 → ランチャーが bootout 済み。

## 7. 失敗ログ/教訓（必読）

- skill: `~/.config/opencode/skills/operational-guardrails/SKILL.md`
  （§9 監視 / §10 ETAと異常対応 / §11 失敗20件 / §12 並列化 / §13 並列時の時間計算）
- README: `Operational Tooling & Skills`、`Operations failure log`、`Parallelization audit`

主要な落とし穴（再掲）:
- 単一カウンタでの停滞判定（誤再起動）
- `extra_body.options.num_ctx` が OpenAI 互換 endpoint で無視される（モデル派生で対処）
- partial の非シリアライズ/非アトミック書込
- stall window < 最長ステップ（並列だと1件が長い）
- 蓋を閉じる=スリープ、スワップでスループット崩壊
- 重複/孤立プロセスの放置

## 8. 最短の実行手順（新セッション用）

```bash
cd /Users/petadimensionlab/workspace/research/MiroFish-Offline

# 1) モデル取得と構成把握
ollama pull qwen3.5:9b
ollama show qwen3.5:9b

# 2) 派生モデル（num_ctx を固定）
ollama show --modelfile qwen3.5:9b > /tmp/q.modelfile
printf '\nPARAMETER num_ctx 8192\n' >> /tmp/q.modelfile
ollama create qwen3.5-8k -f /tmp/q.modelfile

# 3) .env を §5 の内容に更新（LLM_MODEL_NAME=qwen3.5-8k 等）

# 4) 既存 sim の続き or 新規 pipeline
./scripts/run_cli.sh resume --project-id proj_b44cd4c0e1ff --run --max-rounds 1 --parallel-profiles 8
#   新規: ./scripts/run_cli.sh pipeline --requirement "..." --file paper.pdf --max-rounds 1

# 5) 監視（別ターミナル）
./scripts/supervise.sh
```

### 8.1 12b 優先の起動手順（手動・単一構成）
`run_cli.sh` は Ollama を再起動し Ollama.app と二重化することがあるため、**手動で単一起動**する:

```bash
# Ollama.app を確実に落とす
osascript -e 'quit app "Ollama"'; pkill -f Ollama.app; pkill -f "ollama serve"; pkill -f llama-server
sleep 5
# 12b 用に単一サーバ起動（NP=16）
set -a; . ./.env; set +a
OLLAMA_NUM_PARALLEL=16 OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_MAX_LOADED_MODELS=2 \
  OLLAMA_KEEP_ALIVE=30m nohup ollama serve > /tmp/mirofish-ollama.log 2>&1 &
# backend（1つだけ）
( cd backend && exec env FLASK_DEBUG=false .venv/bin/python run.py >/tmp/mirofish-backend.log 2>&1 ) &
# resume（C=8: これ以上は競合で悪化）
nohup python3 scripts/mirofish_cli.py resume --project-id proj_827ece137595 --run \
  --max-rounds 1 --parallel-profiles 8 --max-wait 43200 > /tmp/mirofish-gemma12b-run.log 2>&1 &
# 確認: backends は必ず 1、runner は "-c 524288 -np 16"
pgrep -f run.py | wc -l; ps aux | grep llama-server | grep -v grep | grep -oE "\-c [0-9]+ -np [0-9]+"
```

### 8.2 12b の実測スループット（2026-09-13）
- `profile 生成 ≈ 0.3 件/分`（C=8）、C=12 では **0.16 件/分に悪化**（競合）。
  したがって **C=8 が上限、それ以上は逆効果**。
- 1 profile ≈ **2500 トークン**（e4b の ~877 の約2.8倍長い）。これが遅さの主因。
- prepare 157件の完走 ETA ≈ **5〜8時間**。simulate(1 round)→report は別途。
- 参考: 集約 throughput は qwen3.5≈48, e4b≈208, 12b≈19–40 tok/s。

---

## 9. qwen3.5:9b 実行結果（2026-09-13 実測）— **結論: 全工程には不向き**

### 9.0 用語（この文書で使う記号）
- **NP** = `OLLAMA_NUM_PARALLEL`（サーバ側の同時スロット数）。Ollama は
  runner を `llama-server -c <num_ctx × NP> -np NP` で起動する。メモリは `num_ctx × NP` に比例。
- **C** = クライアント側の同時実行数（`--parallel-profiles` / `CONFIG_BATCH_PARALLEL` /
  `GRAPH_BUILD_PARALLEL` / `OASIS_SEMAPHORE`）。`C ≤ NP` のときだけ効果がある。
- **集約 (aggregate) tok/s** = 総生成トークン ÷ 実時間。機械＋モデルで決まる上限で、
  並列を増やしても飽和以上にはならない。
- **per-request rate** ≈ 集約 ÷ C。C を上げると**1件は遅くなる**（ジョブは速くならない）。

§2〜§5 の想定を実測で置き換える。**qwen3.5:9b は Ollama 0.34.0 で並列実行できない**ため、
§1〜§5 の NP チューニングは qwen には無効。

### 9.1 モデル特性（実測）
- `architecture=qwen35`, 9.7B, Q4_K_M, context 262144。**hybrid attention**
  （`full_attention_interval=4`、KV ヘッドは 4層ごとに 4、他は 0）。
- `KV_bytes_per_token = 2 × Σ head_count_kv × key_length × 2 = 2×32×256×2 = 32 KiB/token`
  （§2.1 の 144 KiB/token 仮定より遥かに小さい）。メモリは問題ではない。
- **thinking モデル**。`response_format=json_object` でも `content` が空になり
  `reasoning` に全トークンを消費する（`finish_reason=length`）。

### 9.2 必須修正
1. **thinking 無効化**: Ollama の OpenAI 互換 endpoint では `reasoning_effort: "none"` のみ有効
   （`think:false` / `options.think` は無効）。`reasoning_effort` は **3 系統すべて**に必要:
   - `backend/app/utils/llm_client.py`（`reasoning_kwargs()` を追加、Config.LLM_REASONING_EFFORT）
   - `backend/scripts/run_{reddit,twitter,parallel}_simulation.py`（camel `ModelFactory.create(..., model_config_dict={"reasoning_effort": ...})`）
   - `.env: LLM_REASONING_EFFORT=none`
2. **`extra_body.options.num_ctx` は無効**（§7 の再確認）。有効なのは**派生モデルの num_ctx のみ**。
   実測: `qwen3.5-8k`(8192) に対し `num_ctx=65536` を要求しても `ollama ps` は 8192 のまま。
3. **NER プロンプト**: qwen は「ontology の型のみ・be precise」を厳格に守りすぎ、参照リストの
   著者名すら 0 件になり得る。`backend/app/storage/ner_extractor.py` の `_SYSTEM_PROMPT` を
   recall 優先（"Extract every explicit named entity … personal names, including authors in
   reference lists, count"）に変更して 178 entities を抽出できるようにした。

### 9.3 並列性（最重要）
- Ollama: `WARN model architecture does not currently support parallel requests; architecture=qwen35`
  → runner は常に `-np 1`、`llama_context: n_seq_max = 1`、スロットは 1 本。
  クライアントが 20 並列で投げてもサーバで直列化。実測 ~48 tok/s（concurrency 非依存）。
- **N=8 プロセス別ポート + ラウンドロビン proxy は不成立**（実測）:
  | N | 集約 | p50 |
  |---|---|---|
  | 1 | 21.4 tok/s | 2.1s |
  | 2 | 24.6 | 4.3s |
  | 4 | 25.6 | 7.9s |
  → Metal GPU がボトルネックで**時間分割にしかならず +20% 程度**、latency は N 倍。
  単一サーバ単体（~48 tok/s）より**悪化**。N=8 では swap 17.6GB のスラッシング。

**結論**: qwen3.5:9b で全工程は完走可能だが、並列化できず極端に遅い。
速度が必要な本番は gemma4 を使う。qwen は精度検証・単発用途に限定。

## 10. **第一候補モデル = gemma4:12b（優先）** — 最適仕様（2026-09-13 実測）

> **方針（優先順位）: 本パイプラインの第一候補は gemma4:12b（精度優先）。**
> qwen3.5:9b は並列不可のため本番非推奨、gemma4:e4b は高速な代替（速度優先のときのみ）。
> モデルを迷ったら **12b を選ぶ**。e4b は「速度が要る検証/暫定」用途に限定する。

単発ベンチ（24req×400tok, 同時実行数=NP）:

| model @NP | 集約 tok/s | req/min | p50 | 備考 |
|---|---|---|---|---|
| gemma4:e4b @64 | **208.2** | 31.2 | 45.9s | 14GB |
| gemma4:12b @16 | 41.3 | 6.19 | 77.9s | |
| gemma4:12b @32 | 99.4 | 14.9 | 96.3s | |
| gemma4:12b @64 | **152.4** | 22.9 | 62.7s | 44GB |
| gemma4:12b @128 | 87.7 | 13.2 | 109.2s | 82GB / 25%CPU offload / swap33GB |

ただし**実ワークロード（30 facts・39 nodes の長文脈 + profile ~900–2700 tok）では NP=64 は
RSS 70GB / swap 46GB のスラッシング**で進行停止。メモリ安全域での最適は:

```
LLM_MODEL_NAME=gemma4-12b-32k     # ollama show --modelfile gemma4:12b + PARAMETER num_ctx 32768
OLLAMA_NUM_PARALLEL=16            # 12b loaded ~25GB / RSS ~38GB
--parallel-profiles 8             # per-req を timeout 内に収める
LLM_TIMEOUT=1800
CONFIG_BATCH_PARALLEL=4
GRAPH_BUILD_PARALLEL=1
```

実測スループット: profile 生成 **~0.4–0.8 件/分** → prepare 157件で **3–7時間**。
e4b 比較: 同 prepare が **~30–45分**（集約 208 tok/s）。

### 10.1 macOS の落とし穴（再発）
- **Ollama.app は殺しても復活し、`-np 1`/`-np 4` で port 11434 を奪う**。
  `ollama` CLI のサーバと二重 LISTEN になり、`extra_body` どおり `-np 4` で応答してしまう。
  対策: `osascript -e 'quit app "Ollama"'` → `pkill -f Ollama.app` → `pkill -f "ollama serve"` の後、
  単一サーバを手動起動し `ollama ps` と runner 引数で **必ず確認**。

### 10.2 破損インシデント（必読）
- `reddit_profiles.json` は**非アトミック**に毎回全件書込みされる。生成中に backend を kill すると
  **buffered write が途中で切れて JSON が破損**する（実測: 93件 → 13件に消失）。
- 対策: 停止前に「書込み中でない」ことを確認、または stop は graceful に。破損時は
  `json.JSONDecoder().raw_decode` を繰り返して**先頭から完全なオブジェクトだけ救出**できる
  （`raw_decode` ベースのサルベージ）。
- 恒久対策案: `simulation_config.partial.json` と同様、reddit_profiles.json も temp+os.replace の
  アトミック書込みにする（未実装）。
