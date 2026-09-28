# Phase 3〜4 — LLM による意思決定と議論フェーズ

実施日: 2026-09-28。LLM: qwen3:4b（Ollama）。

## 起動手順

```sh
# 1. step-server（議論ラウンドは0回で起動。hour 9 から開始 — NOTES #1）
backend/venv311/bin/python backend/scripts/run_experiment_env.py \
    --config <sim_dir>/simulation_config.json --twitter-only --start-hour 9

# 2. bridge（必ず FLASK_DEBUG=false — リローダーが bridge の状態を消す。NOTES #22）
cd backend && FLASK_DEBUG=false FLASK_PORT=5055 .venv/bin/python run.py

# 3. oTree（bot の /decide は round 全体の LLM バッチを待つのでタイムアウトは長く）
cd MiroFish-oTree/mirofish_otree_test
MF_BRIDGE_URL=http://127.0.0.1:5055/api/experiment MF_BRIDGE_TIMEOUT=1800 \
MF_SIMULATION_DIR=<sim_dir> otree test pd_debate_llm 48 --export ./export_pd_llm
```

セッション設定: `pd_debate_llm`（議論なし）、`pd_debate_llm_debate`（期間に議論2ラウンド＋結果注入＋冒頭の話題投稿）。

## 仕組み

1. `creating_session` → `/configure`（ペア表・利得・LLM 設定）。bridge は第1期の全48体ぶんの決定を**先取り**（prefetch）し始める。
   議論ありの場合は先に冒頭投稿と議論ラウンドを実行する
2. bot の `/decide` は先取り中の結果を待つ（in-flight 待ち合わせ）。oTree の bot 実行は完全に直列なので、
   1体ずつ LLM を呼ぶと 48倍の時間がかかる。先取りで1期あたり1回のバッチ面接にまとめる
3. 全ペアの提出後、`DebateBarrier` → `/round_complete`（oTree が記録した結果が正）。bridge は
   ① 結果を各エージェント自身の投稿として注入 → ② 議論 `run_rounds(k)`（候補はゲーム参加者のみ）→
   ③ 次期の決定を先取り、をバックグラウンドで実行。次期の in-flight は `/round_complete` の応答前に確保する
4. 面接は step-server の新コマンド `game_interview` で一括実行。プロンプトの `{{FEED}}` は、
   各エージェントがその時点で見ている投稿（rec テーブルを更新してから refresh — NOTES #20）に置き換わる

## 意思決定プロンプト

`backend/app/prompts/game_decision.j2`。中立ラベル（Option A / B）、利得表、期、これまでの履歴、
フィード、JSON での回答指示。

## 応答の解釈と欠測

`backend/app/services/game_decision.py::parse_decision`:
`<think>` ブロックとコードフェンスを除去 → 最初に読める JSON の `choice` → 壊れた JSON でも `"choice": "A"` を拾う。
読めなければ **厳格版プロンプトで1回だけ再質問**（`source=llm:<model>:strict`）、それでも駄目なら
`default_choice`（既定 A）＋ `decision_missing=1`（`source=llm_default`）。

## ログ

`<sim_dir>/game/<session_code>/`:
- `bridge_log.jsonl` — configure / decide / round_complete（食い違い）/ debate_phase / prefetch
- `llm_answers.jsonl` — 全回答（プロンプト本文、フィード件数、生の応答、解釈エラー）

議論中の行動は従来どおり `<sim_dir>/twitter/actions.jsonl`。注入投稿は `action_args.injected=true`。

## 検証結果

（実行後に追記）
