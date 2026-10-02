# oTree × MiroFish-Offline — 技術仕様

**English: [README.md](README.md)** ・ 実装と結果のレポート: [REPORT.md](REPORT.md) ／ [report.html](report.html)、第2報 [REPORT_2026-10.md](REPORT_2026-10.md) ／ [report_2026-10.html](report_2026-10.html) ・ 観察ログ: [NOTES.md](NOTES.md) ・ 音声版: [この仕様](audio/readme_ja.mp3)、[レポート](audio/report_ja.mp3)（読み上げ原稿は [narration/](narration/)）

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
3. 全ペアの提出後、`/round_complete` で oTree の記録を送る。bridge は裏で「結果の注入 → 議論 `debate_rounds` ラウンド →（`chat_turns > 0` なら）ペア内チャット → 次期の先取り」を行う。次期の計算中の印は `/round_complete` の応答前に確保する
4. 最終期の後: 必要なら事後の信念調査

先取りが必要なのは、oTree の bot 実行が完全に直列だから。`/decide` のたびに LLM を呼ぶと、所要時間が人数倍になる。

### PD 以外のゲーム（`game`、`backend/app/services/games.py`）

| `game` | oTree アプリ | 構成 | ゲーム理論の予測（有限回の繰り返し） | 人間の典型 |
|---|---|---|---|---|
| `pgg` | `pgg` | 4人固定×10期、持ち点20、倍率1.6（MPCR 0.4） | 全期 拠出0 | 40〜60%から減衰、最終期に低下 |
| `beauty` | `beauty` | 4人固定×10期、0〜100、平均の2/3に最も近い人が20点 | 全員0 | 第1期 平均≈35、期ごとに0へ |
| `trust` | `trust` | 2人固定・役割固定×10期、両者10点、送った額は3倍 | 送らない・返さない | 約半分を送り、ほぼ送った分が返る |
| `ultimatum` | `ultimatum` | 2人固定・役割固定×10期、20点を分ける | 最小の提案・必ず受諾 | 提案40〜50%、2割未満は拒否されやすい |

- bridge の設定は `game`、`game_params`（`games.py` の既定値を上書き）、`agents=[{agent_id, group_agent_ids, role}]`（`role=2` は後手）
- **後手のあるゲーム（trust, ultimatum）**: 期の先取りは先手だけ。oTree は先手が全員決めたところ（`StageBarrier`）で `/stage_complete` を送り、bridge は先手の決定をプロンプトに入れて後手を先取りする。送金0のときの返金など、決める余地がない決定は LLM を呼ばず `source='auto'`
- 理解テストは役割ごとに期待値が違う（`comprehension.jsonl` の `expected`）
- チャットと `inject_results='summary'` は非対応（`/configure` が拒否）
- 集計: `analyze_pgg.py`（公共財）、`analyze_games.py --game beauty|trust|ultimatum`
- セッション設定: 固定戦略 bot 用 `pgg` / `beauty` / `trust` / `ultimatum`、LLM 用 `*_llm`（チャット・議論・フィード・結果注入なし）

### 職場ペルソナ（`make_workplace_sim.py`）

架空の企業グループ（Kestrel Harbour Group）の従業員 48 人。4社×12部署で1セル1人なので、**全員が同じグループだが会社か部署が違う**。
LLM が書くのは仕事・気質・職場で大事にしていること・社内 SNS での書き方で、関係性は全員に同じ固定文を付ける:
「他部署・他社の人は名前やグループ行事・社内 SNS で知っている程度で、目標・予算・指揮系統は共有しておらず、わざわざ手を貸す義理もない」。
ゲームの主題の語（trust, cooperation など）は一般人ペルソナと同じく除外。`personas_meta.json` の `agent_order` を oTree の `MF_AGENT_IDS`（JSON 配列）に渡すと、
ペア（1–2, 3–4…）と4人グループが**同じ会社の別部署**になる。

```sh
backend/.venv/bin/python backend/scripts/experiment/make_workplace_sim.py --n 48 --seed 1
MF_AGENT_IDS='[3,4,6,14,...]' ... otree test pd_debate_llm_nochat 16
```

### ペア内チャット（`chat_turns`）

> ⚠️ **本実験は必ず「チャットなし」（`chat_turns=0`、例 `pd_debate_llm_nochat` / `pd_debate_llm_debate*`）で行う。** この研究の意図は「SNS 上の言説 → ゲームの行動」の効果を見ることで、ペア内チャットで相手と直接打ち合わせられると、行動がチャットの合意で決まり（NOTES #50: 協力率 1.00 固定）、言説の効果を測る余地がなくなる。チャットは、エージェントがコミュニケーションを行動に反映できるかの**検証・参照条件としてのみ**使う（NOTES #51）

SNS 上の議論ラウンドでは、エージェントはペルソナの関心ごとを投稿するだけで、ゲームの相手と話すことがなかった（NOTES #34, #44, #46, #47）。
そこで各期の決定の直前に、**ペアの2人だけが見える非公開チャット**を行う（実験経済学でいう cheap talk: 発言に拘束力はない）。

- 1期あたり `chat_turns` 通。1通ごとに全ペアの話し手をまとめて `game_interview` で1回の面接にする。話し手は交互で、先に話す側は期ごとに入れ替わる
- チャットのプロンプトには利得表・これまでの結果・過去 `chat_memory_rounds` 期ぶんのチャット・今期のここまでの発言が入り、「このゲームについて、自分の口調で1〜3文」で話すよう指示する（`game_chat.j2`）。ペルソナは面接のシステムプロンプトから入る
- その期のチャットは両者の意思決定プロンプトに入る（過去のチャットも同じ期数だけ）。約束と実際の選択の食い違いを相手が次の期に指摘できる
- 両者が同じ記号で選択肢を呼べるよう、`label_unit` は `session` か `pair`（ペアごとに共通・ペア間でランダム）。`agent` との組み合わせは `/configure` が拒否する
- 読めない応答は1回だけ再質問し、それでも駄目ならその1通は飛ばす（チャットの失敗で決定は止めない）

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
| `label_unit` | `session` | `session`、`pair`、`agent`: 同じ割り当てを共有する単位 |
| `swap_labels` | `false` | 文字のみ。固定で入れ替える（A を裏切りとして見せる） |
| `debate_rounds` | `0` | 期間の議論ラウンド数（0＝議論なし） |
| `debate_players_only` | `true` | 議論で動けるのはゲーム参加者だけ |
| `debate_ignore_hours` | `true` | 議論では時刻による活性化を無視 |
| `debate_min_active` | `2` | 議論1ラウンドあたりの最低活性人数 |
| `inject_results` | `each` | `each`（各自が結果を投稿）、`summary`（要約1件）、`none` |
| `opening_post` ／ `repeat_opening` | `""` ／ `false` | 第1期前の話題投稿 ／ それを毎回の議論前に再掲 |
| `chat_turns` | `0` | 各期の決定前のペア内チャットの通数（0＝チャットなし） |
| `chat_memory_rounds` | `3` | プロンプトに入れる過去のチャットの期数（-1＝全部） |
| `chat_max_chars` | `400` | 1通の上限文字数（超えたら切る） |
| `comprehension_check` | `false` | 第1期前に利得の質問2問 |
| `belief_survey` | `false` | 7件法の質問をゲームの前後に |
| `default_choice` | `A` | 応答が使えない時の内部値（欠測フラグ付き） |
| `no_think` | `auto` | qwen3 の `/no_think` を付ける（auto＝qwen3 のときだけ） |
| `payoffs`、`num_rounds` | oTree から | `creating_session` が送る |
| `seed`、`inject_delay_sec`、`inject_error_rate` | `0` | 再現性、テスト用の障害注入 |

oTree アプリはこれらを `bridge_<設定名>` のセッション設定として渡す（例 `bridge_debate_rounds=2`）。登録済みのセッション設定: `pd_debate`、`pd_debate_faults`、`pd_debate_llm`、`pd_debate_llm_debate`、`pd_debate_llm_debate_topic`、`pd_debate_llm_opening`、`pd_debate_llm_debate_noinject`、`pd_debate_llm_debate_noinject_swap`、`pd_debate_llm_chat`（ペア内チャット4通、フィード・議論・結果注入なし、ペア単位ラベル）、`pd_debate_llm_chat_debate`（チャット＋議論＋話題の再掲＋結果注入）、`pd_debate_llm_nochat`（`pd_debate_llm_chat` の対照: チャットだけ無し）。

### チャネル適合ダイアドと忘却つき記憶（NOTES #55）

ネットワーク会話（`net_topology`、#54）に、ペルソナのチャネル（#53）から決まる「ペアごとの相性」と、過去の会話を年齢に応じて忘れる記憶を足す。**既定はすべて #54 と同じ挙動**（オフ経路のプロンプト・接触・ログはバイト一致）。oTree からは `bridge_net_<名前>` で渡す。

| 設定 | 既定 | 意味 |
|---|---|---|
| `net_channels` | `false` | 全ペアの適合度 A を計算しダイアド台帳（`dyads.json` / `dyads.jsonl`）を持つ。チャネルの無いシミュレーションは ValueError（セッション削除） |
| `net_channel_topic_weight` | `0.5` | A = w·話題 + (1-w)·媒体、0〜1 |
| `net_channel_beta` | `0.0` | 近隣の選択確率に exp(β·z)。0＝一様。β>0 は `net_topology`≠none と `net_channels` が必要 |
| `net_reply_model` | `always` | `reach`: 会話ごとに媒体と返信を引き、返信なしなら開始者だけが1通書く（相手には見えない） |
| `net_channel_prompt` | `none` | `medium`: 「You are now talking with X by email」のように媒体だけを入れる |
| `net_pair_chat_model` | `always` | `compat`: ペアチャットを確率 A で実行（**参照用、本実験では使わない**、#51） |
| `net_dyad_hooks` | `""` | `dyads.DYAD_HOOKS` の名前（カンマ区切り）。いまは空（将来 betrayal / reputation） |
| `net_memory_mode` | `window` | `decay`: 会話の記憶が w=2^(-Δ/(h·s)) で 原文 → 最初の一文 → 1行要旨 → 相手ごとの集約 と薄れる。`net_topology`≠none が必要 |
| `net_memory_half_life` | `2.0` | h（期）。h=2 で原文の範囲は従来の窓（Δ0〜2）と同じ |
| `net_memory_budget_chars` | `3200` | 記憶ブロックの上限（400以上）。超えたら最低重みの項目から降格、集約行は古い順に削除、今期は降格しない |
| `net_memory_summary` | `extract` | `llm` は未実装（configure が拒否） |

推奨条件は β=1.0 と `reach`（会話の約2割が未返信）。ログ: `dyads.json`（configure 時に1回）、`dyads.jsonl`（ラウンドごと）、`memory_shown.jsonl`（decay のみ、プロンプトごと）、`network.json` の `channels` / `edge_attrs`。事前確認（LLM なし）:

```bash
cd backend && python scripts/experiment/preview_channel_contacts.py \
    --sim-dir uploads/simulations/sim_workplace_ch_s1_n48 --topology ba --beta 1.0 --reply reach --seeds 20
```

oTree のセッション設定案は `otree_settings_dyads_memory.txt`（`pd_net_off_ch`、`pd_net_ba_ch0`、`pd_net_ba_ch`、`pd_net_ba_ch_mem`、pgg 版など）。解析は `analyze_network.py`（dyads 節・memory 節）、`dashboard.py`（辺の着色、適合度ヒストグラム、記憶チャート）。

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
| `pd_debate_custom.csv`（oTree の `--export`） | 1行＝1体×1期: `session_code, participant_code, participant_label, agent_id, round_number, pair_id, id_in_pair, partner_agent_id, choice, cooperated, partner_choice, payoff, decision_source, decision_missing, decision_latency_sec, decision_reason, chat_transcript`（chat_transcript＝その期のペア内チャット、JSON） |
| `<sim_dir>/game/<session>/bridge_log.jsonl` | configure（ラベル含む）、decide、round_complete（食い違い）、debate_phase、調査、失敗 |
| `<sim_dir>/game/<session>/llm_answers.jsonl` | LLM の全回答: プロンプト、見たフィード、生の応答、解釈エラー |
| `<sim_dir>/game/<session>/beliefs.jsonl`、`comprehension.jsonl` | 信念調査と理解テストの回答 |
| `<sim_dir>/game/<session>/chat.jsonl` | ペア内チャットの全発言: 期、通番、話し手、相手、本文、プロンプト、生の応答、解釈エラー |
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

## 11. 技術的な変更（2026-09-29〜10-02）

MiroFish-Offline の PR #3・#4 のマージ後、ブランチ `feat/network-chat-dashboard-channels` で入った変更の一覧。詳細は既存の節（§3 ペア内チャットとゲーム、§5 設定、チャネル適合ダイアドと記憶）にあり、ここでは**変更の索引と、まだどこにも書いていない設定**だけを置く。経緯は [NOTES.md](NOTES.md) #47〜#56、結果は [REPORT_2026-10.md](REPORT_2026-10.md) ／ [report_2026-10.html](report_2026-10.html)。

| 構成要素 | 追加・変更 | 要点（既定値） | 参照 |
|---|---|---|---|
| ペア内チャット | `chat_turns`（既定 0）、`label_unit=pair`、`prompts/game_chat.j2`。**参照用のみ**（本実験は `chat_turns=0`） | `chat_memory_rounds=3`、`chat_max_chars=400`。oTree 設定 `pd_debate_llm_chat` / `_chat_debate` / `_nochat` | §3、NOTES #47, #51 |
| プロンプト v2 | 決定・チャットのプロンプトに「SNS の投稿ではない」「点数は実際の報酬」「記号に意味はない」「選択肢と理由を言う」を追加 | `prompts/_game_header.j2`、`_game_footer.j2`、`game_*.j2`（ゲーム別）、`comprehension_*.j2`。ラン中はテンプレートを編集しない | NOTES #48 |
| モデルと環境 | 主力モデルを gemma4:26b（MoE、思考あり）に | `.env`: `LLM_MODEL_NAME=gemma4:26b`、`MODEL_TIMEOUT=600`。`LLM_REASONING_EFFORT=none` で思考を切れるが、12b は思考なしだと先頭の選択肢を選ぶだけ（NOTES #49）なので 12b 用の設定にとどめる | `backend/app/config.py`、NOTES #49 |
| ゲーム登録表 | `backend/app/services/games.py`: `pgg`、`beauty`、`trust`、`ultimatum`。後手のあるゲームは `/stage_complete` | `game_params` で既定値を上書き（pgg: 持ち点 20・倍率 1.6・4人、beauty: p=2/3・0〜100・賞金 20、trust: 10・3倍、ultimatum: 20） | §3 |
| 職場ペルソナ | `make_workplace_sim.py`（48 人）、`personas_meta.json` の `agent_order` | oTree の環境変数 `MF_AGENT_IDS`（JSON 配列）で座席を指定。`agent_order` 先頭16体: `[3,4,6,14,1,7,8,17,5,9,10,13,0,2,11,12]` | §3、NOTES #53 |
| ペルソナのチャネル | `add_channels.py`（LLM なし、約2秒）、`workplace_channels.json`（分類表） | 全チャネルに無視/流し読み/読む/対応する、全媒体に習慣を割り当て、`channels.json` に記録。`--src`、`--out`（新規ディレクトリ）、`--taxonomy`、`--seed`、`--max-chars 420`、`--verify-only` | NOTES #53, #55 |
| ネットワーク会話 | `net_*` 設定（下表）、`app/services/network_chat.py`、`prompts/game_network_chat.j2` | 相手以外の近隣と1対1。ゲームの相手・pgg の組員への辺は除外 | NOTES #54 |
| チャネル適合ダイアド | `net_channels`、`net_channel_beta`、`net_reply_model`、`dyads.json` / `dyads.jsonl`、`DYAD_HOOKS` | β 既定 0、`always`。`DYAD_HOOKS` は空 | §5（チャネル適合ダイアド…）、NOTES #55 |
| 記憶 | `net_memory_mode`、`net_memory_half_life`、`net_memory_budget_chars`、`memory_shown.jsonl`、`prompts/_memory.j2` | `window`、2.0、3200 | §5、NOTES #55 |
| 解析・ダッシュボード | `analyze_pgg.py`、`analyze_games.py`、`analyze_network.py`、`collect_results.py`、`dashboard.py`、`dashboard_compare.py` | 下記「解析」 | NOTES #52〜#56 |
| oTree 側 | `MF_BRIDGE_SEED`（`bridge_seed`）、`network_transcript` 列、`pd_net_*` ほかの設定 | [MiroFish-oTree の README](https://github.com/petadimensionlab/MiroFish-oTree) | 同左 |
| テスト | `backend/tests/` | 下記「テスト」 | NOTES #54, #55 |

### ネットワーク会話の設定（`bridge_net_<名前>`、NOTES #54）

§5 のダイアド・記憶の表にない、基本の設定。既定では `net_topology=none`（会話なし、導入前と**バイト一致**）。

| 設定 | 既定 | 意味 |
|---|---|---|
| `net_topology` | `none` | `none`、`er`、`ba`、`ws`、`ring`。PD と pgg のみ |
| `net_mean_degree` | `4.0` | er は辺数 round(N·k/2) 固定、ba は m=max(1, round(k/2))、ws・ring は偶数 k（2 以上） |
| `net_ws_p` | `0.1` | ws の張り替え確率 |
| `net_seed` | `-1` | -1 なら `seed`。グラフと λ はセッションコードに依存しない（条件間で同じ） |
| `net_exclude_partners` | `true` | ゲームの相手・pgg の組員との辺を除く（NOTES #51） |
| `net_contact_mean` ／ `net_contact_dispersion` | `1.0` ／ `0.5` | μ ／ r。λ_i ~ Gamma(r, μ)、開始数 k_i ~ Poisson(λ_i)（負の二項）。r≤0 は λ=μ |
| `net_lambda_assign` | `random` | `degree` は大きい λ を次数の高い人に |
| `net_max_initiate` ／ `net_max_load` | `3` ／ `4` | 1期に始める会話数の上限 ／ 始める＋受ける数の上限 |
| `net_turns` | `2` | 1会話の通数（ウェーブ単位で進める） |
| `net_memory_rounds` ／ `net_max_convs_in_prompt` | `2` ／ `8` | `window` 記憶で決定プロンプトに入れる過去の期数 ／ 会話数 |
| `net_identity` | `profile` | `anon` は「Participant 7」のように名前を伏せる |
| `label_order_per_agent` | `false` | 記号の割り当てはセッション共通のまま、選択肢を並べる順だけエージェントごとにランダム（位置バイアス対策、NOTES #49）。ネットワーク・チャネルのランでは対照を含め `true` |

出力（`<sim_dir>/game/<session>/`）: `network.json`（グラフ・λ・レイアウト。チャネル有効時は `channels` / `edge_attrs` / `channel_engagement`）、`network_contacts.jsonl`（期ごとの k_drawn / k_realized と会話。medium / reply_draw / replied）、`network_chat.jsonl`（全メッセージ試行とプロンプト）、`dyads.json` / `dyads.jsonl`、`memory_shown.jsonl`、`bridge_log.jsonl` の `network_built` / `network_phase` / `network_failed`。oTree の CSV には `network_transcript` 列（pd_debate）。

### 解析（`backend/scripts/experiment/`）

| スクリプト | 内容 |
|---|---|
| `analyze_session.py` | PD の1セッション（協力率、条件付き協力、先頭の選択肢の割合、ペアチャット） |
| `analyze_pgg.py` | 公共財: decline、end_game_drop、trend_per_round、個人内の conditional_slope、nash_share |
| `analyze_games.py --game beauty\|trust\|ultimatum` | 理論値との比較 |
| `analyze_network.py --otree-csv … --sim-dir … [--session] [--json]` | ネットワーク統計、Spearman、負の二項の確認、露出の表、辺の一致（置換 1000 回）、会話の質、dyads 節、memory 節 |
| `collect_results.py [--manifest results_manifest.json] [--out-dir docs/otree-integration/results]` | 上の解析をライブラリとして呼び、`results/results.json` と `results_rounds.csv` を作る。LLM・サーバー不要 |
| `dashboard.py --otree-csv … [--sim-dir] [--session] [--baseline-csv] [--out] [--title]` | 1ランの静的 HTML（Altair / Vega-Lite。vega・vega-lite・vega-embed は jsdelivr から読み込む） |
| `dashboard_compare.py --run LABEL=CSV[:SIMDIR][@SESSION] … [--out] [--title]` | 複数条件の比較ページ |
| `preview_channel_contacts.py` | 相性と接触の事前確認（LLM なし） |

### oTree 側の追加（要約）

`MF_BRIDGE_SEED`（既定 0）は oTree のセッション設定 `bridge_seed` に入り、bridge の `seed`（ラベル、グラフ、接触率、接触の抽選）になる。LLM 自体のサンプリングには seed がないので、同じ seed でも結果は一致しない（反復ラン用）。詳細は MiroFish-oTree の README。

### テスト

LLM もサーバーも使わない（偽クライアント）。

```sh
cd backend && .venv/bin/python -m pytest tests -q
```

`test_network_chat.py`（ネットワークの接触・プロンプト。off のとき導入前と一致するゴールデン `golden_*.json`）、`test_channel_dyads.py`、`test_memory.py`、`test_add_channels.py`、`test_dashboard.py`、`test_dashboard_compare.py`。実ランのシミュレーションディレクトリが無い環境では、それを使うテストはスキップされる（NOTES #55 の時点で全 115 件、うち 9 件がスキップ）。
