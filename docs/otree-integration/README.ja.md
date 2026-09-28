# oTree × MiroFish-Offline — 技術仕様

**English: [README.md](README.md)** ・ 実装と結果のレポート: [REPORT.md](REPORT.md) ／ [report.html](report.html) ・ 観察ログ: [NOTES.md](NOTES.md) ・ 音声版: [この仕様](audio/readme_ja.mp3)、[レポート](audio/report_ja.mp3)（読み上げ原稿は [narration/](narration/)）

MiroFish の LLM エージェントに oTree の反復囚人のジレンマをプレイさせる。ゲームの期と期の間に、エージェントは模擬 SNS（OASIS）で議論し、言説と行動が互いに影響し合う。

## 1. 構成要素

| 構成要素 | 場所 | ライセンス | 役割 |
|---|---|---|---|
| oTree アプリ `pd_debate` | [`MiroFish-oTree`](https://github.com/petadimensionlab/MiroFish-oTree)（別の公開リポジトリ）の `mirofish_otree_test/pd_debate/` | MIT | ゲーム本体（ペア・利得・記録）。bot が bridge に決定を問い合わせる |
| bridge | `backend/app/api/experiment.py`、`backend/app/services/experiment_bridge.py` | AGPL-3.0 | 意思決定の方策、先取り、議論フェーズ、突き合わせ、ログ |
| 意思決定プロンプト | `backend/app/services/game_decision.py`、`backend/app/prompts/*.j2` | AGPL-3.0 | ラベル、プロンプト生成、応答の解釈 |
| step-server | `backend/scripts/run_experiment_env.py`、`backend/scripts/experiment/` | AGPL-3.0 | OASIS 環境をファイル IPC で1ラウンドずつ進める |
| ランナーのリファクタ（R1） | `backend/scripts/run_parallel_simulation.py` | AGPL-3.0 | `setup_platform_env`、`publish_initial_posts`、`step_round` |
| ツール | `backend/scripts/experiment/analyze_session.py`、`make_general_sim.py` | AGPL-3.0 | セッションの集計、一般人ペルソナの生成 |

```
oTree bot ──HTTP──▶ Flask bridge ──ファイル IPC──▶ step-server（OASIS）──▶ Ollama
          ◀────────              ◀────────────────
```

行動と利得の正典は oTree。bridge が oTree の記録を上書きすることはない。

## 2. ゲーム

- 反復囚人のジレンマ、2人固定ペア、`NUM_ROUNDS = 10`
- 利得（セッション設定 `pd_payoffs`、既定）: R=30（両者協力）、T=50（協力者を裏切る）、S=0（裏切られる）、P=10（両者裏切り）
- 内部の選択値は常に `A`＝協力、`B`＝裏切り。エージェントに見せるラベルは bridge の設定で決まる
- ページ順: `Decide` → `PairWaitPage`（利得計算）→ `Results` → `DebateBarrier`（`wait_for_all_groups=True`、期ごとに1回 `/round_complete`）
- `participant.label = "agent_<agent_id>"`。`agent_id` は MiroFish のエージェントに対応（セッション設定 `agent_ids`、既定は `0..N-1`）

## 3. 1期の流れ（llm 方策）

1. `creating_session` → `/configure`（ペア表と設定）。bridge は第1期の全員の決定を計算し始める（必要なら先に理解テスト、信念調査、冒頭投稿、冒頭の議論）
2. 各 bot が `/decide` → bridge は先取り済みの決定を返す。計算中なら待つ
3. 全ペアの提出後、`/round_complete` で oTree の記録を送る。bridge は裏で「結果の注入 → 議論 `debate_rounds` ラウンド → 次期の先取り」を行う。次期の計算中の印は `/round_complete` の応答前に確保する
4. 最終期の後: 必要なら事後の信念調査

先取りが必要なのは、oTree の bot 実行が完全に直列だから。`/decide` のたびに LLM を呼ぶと、所要時間が人数倍になる。

## 4. bridge の HTTP API（`/api/experiment`）

応答は `{"success": bool, "data": ...}`。

| メソッド | パス | 入力 | 出力 |
|---|---|---|---|
| POST | `/configure` | `session_code`、設定（§5）、`agents: [{agent_id, partner_agent_id}]` | 有効な設定 |
| POST | `/decide` | `session_code`、`round_number`、`agent_id`、`history: [{round_number, own, partner, payoff}]` | `choice`（内部値 A/B）、`reason`、`source`、`latency_sec`、`missing`、`cached` |
| POST | `/round_complete` | `session_code`、`round_number`、`n_players`、`cooperation_rate`、`outcomes: [{agent_id, choice, payoff, partner_agent_id, source, missing}]` | `accepted`、`duplicate`、`n_mismatches`、`prefetched_next` |
| GET | `/state/<session_code>` | — | 期ごとの決定数・欠測数、計算中の数 |

エラー: 400 入力不備、409 未設定のセッション（bridge の再起動など）、503 障害注入、500 その他。

保証: `/decide` は（セッション, 期, エージェント）ごとにべき等で、同時のリトライは二重に計算しない。`/round_complete` は期ごとにべき等。bridge の決定と oTree の記録の食い違いはログに残る。

## 5. bridge の設定（`/configure`、または oTree のセッション設定 `bridge_<名前>`）

| 設定 | 既定 | 意味 |
|---|---|---|
| `policy` | `random` | `random`、`allc`、`alld`、`tft`、`llm` |
| `simulation_id` ／ `simulation_dir` | — | step-server が扱うシミュレーション（llm） |
| `platform` | `twitter` | 面接と議論に使うプラットフォーム |
| `include_feed` | `true` | 今見ているフィードを意思決定プロンプトに入れる |
| `feed_exclude_own` | `false` | そのフィードから自分の投稿を除く |
| `label_scheme` | `symbols` | `letters`（A/B）か `symbols`（△□○◇） |
| `label_randomize` | `true` | ラベルの対応と並び順をランダムに決める |
| `label_unit` | `session` | `session` か `agent`: 同じ割り当てを共有する単位 |
| `swap_labels` | `false` | 文字のみ。固定で入れ替える（A を裏切りとして見せる） |
| `debate_rounds` | `0` | 期間の議論ラウンド数（0＝議論なし） |
| `debate_players_only` | `true` | 議論で動けるのはゲーム参加者だけ |
| `debate_ignore_hours` | `true` | 議論では時刻による活性化を無視 |
| `debate_min_active` | `2` | 議論1ラウンドあたりの最低活性人数 |
| `inject_results` | `each` | `each`（各自が結果を投稿）、`summary`（要約1件）、`none` |
| `opening_post` ／ `repeat_opening` | `""` ／ `false` | 第1期前の話題投稿 ／ それを毎回の議論前に再掲 |
| `comprehension_check` | `false` | 第1期前に利得の質問2問 |
| `belief_survey` | `false` | 7件法の質問をゲームの前後に |
| `default_choice` | `A` | 応答が使えない時の内部値（欠測フラグ付き） |
| `no_think` | `auto` | qwen3 の `/no_think` を付ける（auto＝qwen3 のときだけ） |
| `payoffs`、`num_rounds` | oTree から | `creating_session` が送る |
| `seed`、`inject_delay_sec`、`inject_error_rate` | `0` | 再現性、テスト用の障害注入 |

oTree アプリはこれらを `bridge_<設定名>` のセッション設定として渡す（例 `bridge_debate_rounds=2`）。登録済みのセッション設定: `pd_debate`、`pd_debate_faults`、`pd_debate_llm`、`pd_debate_llm_debate`、`pd_debate_llm_debate_topic`、`pd_debate_llm_opening`、`pd_debate_llm_debate_noinject`、`pd_debate_llm_debate_noinject_swap`。

## 6. step-server の IPC コマンド

コマンドは `<sim_dir>/ipc_commands/`、応答は `<sim_dir>/ipc_responses/`（定義は `experiment/ipc_protocol.py`）。

| コマンド | 引数 | 動作 |
|---|---|---|
| `run_rounds` | `rounds`、`platform`、`agent_ids`、`ignore_active_hours`、`min_active` | `step_round` で議論を k ラウンド進める |
| `inject_post` | `posts: [{agent_id, content}]`、`platform` | 1回の `env.step` で全投稿を公開。`injected: true` 付きで記録 |
| `game_interview` | `interviews: [{agent_id, prompt}]`、`platform`、`exclude_own_posts` | 一括面接。`{{FEED}}` を各自の今のフィードに置き換え、応答と見たフィードを返す |
| `get_state` | `platform` | ラウンド数、処理中かどうか |
| `interview`、`batch_interview`、`close_env` | — | `ParallelIPCHandler` から継承 |

step-server は `<sim_dir>/step_server.pid` を取り、生きている別のサーバーがあれば起動しない。

## 7. 意思決定プロンプトと解釈

- 利得は選択肢ごとに言い直す（「△ を選んだ場合: 相手が △ なら…、□ なら…」）
- 期、見せるラベルでのこれまでの履歴、エージェントのフィード、JSON での回答指示 `{"choice": ..., "reason": ...}` を含む
- 解釈は `<think>` とコードフェンスを除き、有効なラベルを含む最初の JSON を読む。駄目なら `"choice": X` の形を探す。使えない応答は厳格版で1回だけ再質問し、それでも駄目なら `default_choice`＋`decision_missing=1`

## 8. 出力

| ファイル | 内容 |
|---|---|
| `pd_debate_custom.csv`（oTree の `--export`） | 1行＝1体×1期: `session_code, participant_code, participant_label, agent_id, round_number, pair_id, id_in_pair, partner_agent_id, choice, cooperated, partner_choice, payoff, decision_source, decision_missing, decision_latency_sec, decision_reason` |
| `<sim_dir>/game/<session>/bridge_log.jsonl` | configure（ラベル含む）、decide、round_complete（食い違い）、debate_phase、調査、失敗 |
| `<sim_dir>/game/<session>/llm_answers.jsonl` | LLM の全回答: プロンプト、見たフィード、生の応答、解釈エラー |
| `<sim_dir>/game/<session>/beliefs.jsonl`、`comprehension.jsonl` | 信念調査と理解テストの回答 |
| `<sim_dir>/twitter/actions.jsonl` | 議論中の行動。注入投稿は `action_args.injected=true` |

1セッションの集計:

```sh
python backend/scripts/experiment/analyze_session.py \
    --otree-csv <export>/pd_debate_custom.csv --sim-dir <sim_dir>
```

## 9. LLM セッションの実行

```sh
# 0. Ollama を並列スロット付きで（OS 再起動で消える）
launchctl setenv OLLAMA_NUM_PARALLEL 8   # その後 Ollama アプリを再起動
# .env: LLM_MODEL_NAME=gemma4:e4b

# 1.（任意）一般人ペルソナの集団を生成
backend/.venv/bin/python backend/scripts/experiment/make_general_sim.py --n 48 --seed 1

# 2. step-server: 起動時の議論は0回、最初の議論は9時から
backend/venv311/bin/python backend/scripts/run_experiment_env.py \
    --config <sim_dir>/simulation_config.json --twitter-only --start-hour 9

# 3. bridge（FLASK_DEBUG=false が必須。リローダーが bridge の状態を消す）
cd backend && FLASK_DEBUG=false FLASK_PORT=5055 .venv/bin/python run.py

# 4. oTree の bot（git clone https://github.com/petadimensionlab/MiroFish-oTree; python -m venv .venv; pip install "otree==6.0.15" requests）
cd MiroFish-oTree/mirofish_otree_test && source ../.venv/bin/activate
MF_BRIDGE_URL=http://127.0.0.1:5055/api/experiment MF_BRIDGE_TIMEOUT=1800 \
MF_SIMULATION_DIR=<sim_dir> otree test pd_debate_llm_debate 8 --export ./export
```

oTree クライアントの環境変数: `MF_BRIDGE_URL`（未設定なら固定戦略の bot、bridge なし）、`MF_BRIDGE_TIMEOUT`（既定 10秒、llm では 1800）、`MF_BRIDGE_ATTEMPTS`（3）、`MF_BRIDGE_BACKOFF`（0.5秒×試行回数）。

## 10. 運用上の決まり

- シミュレーションの DB を消す前に、古いプロセスを止めて消えたことを確かめる（止めた step-server は実行中のコマンドを続け、次のランのコマンドを横取りしうる）
- ラン中に `backend/` のコードやプロンプトのテンプレートを編集しない
- step-server は起動時に `<sim_dir>/twitter_simulation.db` を消して作り直す。実験はシミュレーションディレクトリのコピーで行う
- oTree の bot 実行は直列なので、並行アクセス時の安全性は負荷試験していない
