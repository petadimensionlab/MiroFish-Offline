# 実装タスク一覧

## Phase 0 — 検証と足場（1〜2日）

- [x] oTree 6.0.15 を別 venv に導入、`otree startproject otree_social`。6系での bot CLI・REST API・`participant_label` の挙動を実機確認（推測で進めない）（Sonnet に委譲済み / 2026-09-28）
- [x] `camel-oasis` を Python 3.11 venv に固定し、既存シミュレーションが回ることを確認（Sonnet に委譲済み / 2026-09-28）

成果物: `docs/otree-integration/phase0-findings.md`

## Refactor R1（2026-09-28 完了）

- [x] `run_parallel_simulation.py` から `setup_platform_env` / `publish_initial_posts` / `step_round` を抽出
- [x] `run_experiment_env.py` を `ExperimentIPCHandler` ベースで実装、実 LLM で IPC end-to-end 確認

## Phase 1 — oTree アプリ単体（2026-09-28 完了、[phase1-otree.md](phase1-otree.md)）

- [x] `pd_debate/__init__.py`: 反復囚人のジレンマ（`PLAYERS_PER_GROUP=2`、固定ペア、`NUM_ROUNDS=10`）、`set_payoffs`、全グループのラウンドバリア `DebateBarrier`
- [x] live_method は使わない。通常の Page + form field 構成
- [x] `tests.py` に固定戦略 bot（tft/alld/allc/grim）、`otree test pd_debate 48` で完走・理論値と一致
- [x] CSV エクスポートの列定義を確定（`custom_export`）

成果物: bot で完走する実験アプリ + CSV エクスポートの列定義確定

## Phase 2 — Bridge をダミー方針で貫通（2〜3日）

- [ ] `backend/app/api/experiment.py` を新設
- [ ] `backend/app/services/experiment_bridge.py` を新設
- [ ] `POST /decide` をランダム／固定戦略を返す実装（LLM 抜き）
- [ ] `otree_social/bridge_client.py` から bot が呼び出せるように実装
- [ ] N=4×10グループ、3期でのテスト実行
- [ ] タイムアウト、リトライ、バリア（全員提出検出）の動作確認

成果物: LLM ゼロで end-to-end が通る状態

## Phase 3 — 意思決定を MiroFish に接続（3〜5日）

- [x] `run_experiment_env.py` を新設（`run_parallel_simulation.py` の helper を import して再利用）
- [x] `RUN_ROUNDS` / `INJECT_POST` / `GET_STATE` を追加（`experiment/ipc_protocol.py`。`CommandType` 本体への追加と `SimulationIPCClient` 側は未）
- [ ] `/decide` を `send_batch_interview` 実装に差し替え
- [ ] `llm_client.chat_json` と `oasis_profile_generator._try_fix_json` のパターンを流用して構造化出力の堅牢化
- [ ] パース失敗時は再問→デフォルト値＋欠測フラグの処理を実装
- [ ] プロンプトテンプレートを `prompts/game_decision.j2` として外出し

成果物: エージェントが実際に oTree のゲームをプレイする

## Phase 4 — 議論の交互配置と結果注入（3〜5日）

- [~] `run_rounds(k)` と `inject_post` を接続（step-server 側は実装・確認済み。bridge からの呼び出しは未）
- [ ] 上記1サイクルを完成
- [ ] 議論フェーズのトピック誘導: initial post としてゲーム争点を投入
- [ ] ゲーム連動ログの `sim_xxx/game/` 配下への格納確認

成果物: 閉ループ動作 + `sim_xxx/game/` 配下にゲーム連動ログ

## Phase 5 — 実験デザインと妥当性検証（5〜7日）

- [ ] 議論あり／なし の切り替え実装（`run_rounds(k)` vs `k=0`）
- [ ] 匿名／顕名 の切り替え実装（プロンプトに実名・フォロワー数の含有切替）
- [ ] 罰あり／なし の実装（oTree 側に punishment ラウンドを追加）
- [ ] ネットワーク構造の制御（エコーチェンバー vs 混合）
- [ ] 影響力の非対称性の実装（`influence_weight` 上位をオピニオンリーダーとして配置）
- [ ] 行動データの収集・検証（oTree CSV の拠出額・利得・期別推移）
- [ ] 言説データの収集・検証（`sim_xxx/twitter/actions.jsonl` の投稿・賛否・伝播）
- [ ] 内的状態の測定（ゲーム前後の Likert 面接で信念変化）
- [ ] 派生指標の計算（協力率減衰、stance ドリフト、同調度等）
- [ ] LLM エージェント固有の課題対応（選択肢順序ランダム化、プロンプト・アブレーション）
- [ ] 既知の人間の規則性の再現性確認（拠出減衰、条件付き協力者型分布等）
- [ ] `ReportAgent` による定性分析の追加（ゲーム結果を踏まえた世論分析）

成果物: 完全な実験データセット + 妥当性検証レポート

## Phase 6 — UI 統合（見積未定）

- [ ] フロントの5ステップウィザード（Graph → Env → Simulation → Report → Interaction）に「Game」ステップを追加

成果物: UI 統合完了

## 決定事項（2026-09-28 ユーザー承認）

- [x] 最初のゲーム: **反復囚人のジレンマ**（2人固定ペア。公共財ゲームは後回し）
- [x] 参加者: **エージェントのみ**（bot 経路だけ。人間用 DebatePage は作らない）
- [x] 規模: **48体 = 2人固定ペア × 24組、10期**
- [x] LLM: **qwen3:4b**（`.env` の `LLM_MODEL_NAME` を変更済み）
