# Phase 1 — oTree アプリ `pd_debate`

実施日: 2026-09-28 / oTree 6.0.15 / Python 3.12（`MiroFish-oTree/.venv`）

## 置き場所

`/Users/petadimensionlab/workspace/research/MiroFish-oTree/mirofish_otree_test/pd_debate/`
（MIT 側。ライセンス分離のため MiroFish リポジトリ外。**git 管理されていない** — NOTES #12）

| ファイル | 内容 |
|---|---|
| `__init__.py` | ゲーム本体、`creating_session`、`set_payoffs`、ラウンドバリア、`custom_export` |
| `tests.py` | 固定戦略 bot（tft / alld / allc / grim） |
| `Decide.html` / `Results.html` | 人間が開いた場合の最小画面（エージェント専用なので確認用） |

`settings.py` に `pd_debate`（`num_demo_participants=48`）を登録済み。

## 設計

- 2人固定ペア × 10期。oTree の既定で第1期のグループが以後も維持される（実測で全員の相手が1人のみ）
- 選択肢は中立ラベル `A` / `B`（A = 協力）。訓練データ汚染対策（plan.md §5）。順序ランダム化は Phase 5
- 利得行列はセッション設定 `pd_payoffs`（既定 `R=30, T=50, S=0, P=10`）
- `participant.label = "agent_<agent_id>"`。`agent_id` はセッション設定 `agent_ids`（リスト）で MiroFish 側の ID に対応付け、未指定なら 0 起点の連番。`creating_session` はモジュールレベル関数（phase0-otree.md 所見 5）
- ページ順: `Decide` → `PairWaitPage`（ペア内バリア、`set_payoffs`）→ `Results` → `DebateBarrier`（`wait_for_all_groups=True`、全48体の提出後に1回だけ `on_round_complete` が発火）
- `on_round_complete` は現状 `barrier_log.jsonl` に期ごとの協力率を書くだけ。Phase 4 でここから議論フェーズを起動する
- 意思決定の出所は `decision_source` / `decision_reason` / `decision_latency_sec` / `decision_missing` としてフォームで受ける（bridge が埋める想定）。画面には出さないため bot は `check_html=False` で提出

## 検証結果

`otree test pd_debate 48 --export ./export_pd` — 17秒で完走、`expect`（利得・ラベル）全件パス。

| 指標 | 理論値 | 実測 |
|---|---|---|
| 第1期の協力率 | 0.75（alld のみ裏切り） | 0.75 |
| 第2〜10期の協力率 | 0.5（tft が裏切りに転じる） | 0.5 |
| 1人あたり累計利得 tft / alld / allc / grim | 90 / 140 / 300 / 300 | 90 / 140 / 300 / 300 |
| バリア発火回数 | 10（各期1回） | 10 |
| 相手の人数（固定ペア） | 1 | 1 |
| 空ラベル | 0 | 0 |

## CSV エクスポート列定義（`pd_debate_custom.csv`、1行 = 1体 × 1期）

| 列 | 型 | 内容 |
|---|---|---|
| `session_code` | str | oTree セッション |
| `participant_code` | str | oTree 参加者コード |
| `participant_label` | str | `agent_<agent_id>` |
| `agent_id` | int | MiroFish のエージェント ID |
| `round_number` | int | 期（1〜10） |
| `pair_id` | int | ペア番号（`group.id_in_subsession`、期をまたいで不変） |
| `id_in_pair` | int | 1 or 2 |
| `partner_agent_id` | int | 相手の MiroFish ID |
| `choice` | str | `A`（協力）/ `B`（裏切り） |
| `cooperated` | 0/1 | `choice == A` |
| `partner_choice` | str | 相手の選択 |
| `payoff` | float | その期の利得（oTree は `0.0` 形式で書き出す） |
| `decision_source` | str | `bot_fixed:<strategy>` / 将来 `bridge` / `default` |
| `decision_missing` | 0/1 | パース失敗などでデフォルト値を使ったか |
| `decision_latency_sec` | float | 意思決定にかかった秒数 |
| `decision_reason` | str | エージェントの理由（自由文） |

`choice` が未入力の行（途中離脱）は出力しない。
