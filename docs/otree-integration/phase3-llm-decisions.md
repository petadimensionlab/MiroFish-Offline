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

規模: **8体（4ペア）× 10期**。48体では qwen3:4b（思考専用）× Ollama 直列処理で1期約14分、10期で約2.3時間かかるため（NOTES #27）、
検証は8体で行った。コードは規模に依存しない。

### Phase 3 — LLM 意思決定（session okxlo3bf、`pd_debate_llm`、議論ラウンドなし）

26分17秒で完走（1期あたり約140秒）。

| 指標 | 値 |
|---|---|
| 意思決定の出所 | 80/80 が `llm:qwen3:4b` |
| 解釈失敗 / 厳格版再質問 / 欠測 | 0 / 0 / 0 |
| bridge と oTree の食い違い | 0 |
| フィード件数（1回答あたり） | 常に 2（NOTES #30） |
| 協力率 | 第1〜8期 1.00、第9・10期 0.875（1体が終盤に B へ） |
| 相手が前期に A だった時の協力率 | 0.972 |

理由欄はほぼ全員がフィードの投稿やペルソナ（研究者）を根拠に挙げていた（NOTES #28）。
注: このランは結果注入が既定の `each` のまま（NOTES #29）。以後 `pd_debate_llm` は注入なしの純粋な対照にした。

分析: `backend/scripts/experiment/analyze_session.py --otree-csv <export>/pd_debate_custom.csv --sim-dir <sim_dir>`

### Phase 4 — 閉ループ（session 2yzfogyz、`pd_debate_llm_debate`）

冒頭の話題投稿＋議論2ラウンド → 各期: 決定 → 結果を各自の投稿として注入 → 議論2ラウンド → 次期の決定。
33分03秒で完走。

| 期 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| 協力率 | 1.00 | 1.00 | 0.62 | 0.62 | 0.50 | 0.62 | 0.62 | 0.38 | 0.25 | 0.25 |
| 平均利得 | 30.0 | 30.0 | 23.75 | 23.75 | 20.0 | 23.75 | 23.75 | 18.75 | 15.0 | 17.5 |

| 指標 | 値 |
|---|---|
| 意思決定の出所 / 解釈失敗 / 欠測 / 食い違い | 80/80 LLM / 0 / 0 / 0 |
| 条件付き協力（相手が前期 A → 自分 A / 相手が前期 B → 自分 A） | 0.778 / 0.148 |
| 注入投稿 | 73（冒頭1 + 8体×9期） |
| 議論中の自発的行動 | 7（投稿4・リポスト2・引用1）、すべてゲーム参加者 |
| 議論フェーズの所要 | 計 142 秒（10回） |

- 閉ループは設計どおり動いた: 結果注入 → 議論 → 次期の決定、の順序が守られ、先取りが議論の完了を待った（食い違い 0）
- ただし**議論はほぼ空回り**（第1期以降の議論9回中8回が行動0）。模擬時計が1期2時間ずつ進み、昼〜深夜に活動時間帯の
  エージェントがいなくなるため（NOTES #31）。対処として `debate_ignore_hours=True`（時刻を無視）と
  `debate_min_active=2`（最低活性人数）を追加し、既定で有効にした
- 協力率の低下と相互主義（相手が B の後はほぼ B）は観察されたが、議論の行動はわずかなので、主因は**結果注入と
  フィードの2件抽出**と考えるのが自然。n=8・1ランなので因果は主張しない（NOTES #32）

議論なし（Phase 3、注入あり）との比較: 第1〜8期 1.00 対 1.00→0.38。条件の違いは議論2ラウンドの有無だけだが、議論の行動は
7件しかなく、差の原因は特定できない。
