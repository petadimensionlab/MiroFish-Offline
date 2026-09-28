# oTree × MiroFish-Offline 社会エージェントシミュレーション 統合計画

作成日: 2026-09-28

ステータス: Phase 0 着手中（ゲーム種別・人間参加・規模は未確定）

## 目次

- [1. 現状の把握（調査結果）](#1-現状の把握調査結果)
- [2. アーキテクチャ](#2-アーキテクチャ)
- [3. 閉ループの1サイクル（公共財ゲームを例に）](#3-閉ループの1サイクル公共財ゲームを例に)
- [4. 段階的実装計画](#4-段階的実装計画)
- [5. リスクと対策](#5-リスクと対策)
- [6. 未確定事項（要決定）](#6-未確定事項要決定)
- [参考](#参考)

## 1. 現状の把握（調査結果）

MiroFish-Offline 側にすでにある「使える部品」:

| 部品 | 場所 | 役割 |
|------|------|------|
| OASIS 環境 (`env.step`) | `backend/scripts/run_parallel_simulation.py:1155` | Twitter/Reddit 版の社会空間。ラウンド = シミュレーション時間 |
| `LLMAction()` | 同 `:1273` | 「自律行動」= 実質的なディベート（CREATE_POST / CREATE_COMMENT / LIKE / DISLIKE / REPOST / QUOTE_POST / FOLLOW / MUTE） |
| `ManualAction(ActionType.INTERVIEW, {"prompt": ...})` | 同 `:332` | 任意のエージェントに何でも質問して自由文回答を得る＝意思決定オラクル |
| `ManualAction(ActionType.CREATE_POST, ...)` | 同 `:1188` | 外部イベントを社会空間に注入するチャネル |
| IPC（ファイルベース） | `backend/app/services/simulation_ipc.py` | `send_interview` / `send_batch_interview` / `send_close_env` |
| ペルソナ生成 | `oasis_profile_generator.py` | 550体規模。`stance` / `sentiment_bias` / `influence_weight` / `activity_level` を持つ |
| エピソード記憶 | `graph_memory_updater.py` | 行動 → Neo4j の個人／集団記憶へ非同期書き戻し |
| 分析エージェント | `report_agent.py` | 事後にフォーカスグループ面接＋グラフ検索して報告書生成 |

要点: 「意思決定を訊く口（INTERVIEW）」と「世界に書き戻す口（CREATE_POST）」が既に空いているので、OASIS をフォークせずに oTree と閉ループを組める。

重要な制約2点:

1. IPC コマンドのポーリングは全ラウンド終了後の wait モードでのみ行われる（`run_parallel_simulation.py:1601` 以降）。したがって「議論ラウンド → ゲーム → 議論ラウンド」の交互実行は現状不可能で、ランナーの改造が必要。
2. `camel-oasis==0.2.5` の `pyproject.toml` は `>=3.11` のみで上限なし（3.12 で実動作確認済み。`ROADMAP.md:12` の「<3.12」記載は古い）。oTree は最新 6.0.15（MIT）。→ ライセンス分離（AGPL / MIT）と依存の独立性のため、同一プロセス同居は避け、プロセス分離 + HTTP 境界とする。

## 2. アーキテクチャ

3層・2プロセス。上から順に:

```
┌─────────────────────────────────────────────────────────┐
│ oTree 6.0.x  (別venv, MIT, PostgreSQL/SQLite)           │
│  - 実験プロトコル / 利得計算 / ラウンド管理 / 人間用UI     │
│  - Bot (tests.py) が意思決定を bridge に委譲              │
└──────────────┬──────────────────────────────────────────┘
               │ HTTP (JSON) ← 唯一の結合点
┌──────────────▼──────────────────────────────────────────┐
│ Experiment Bridge  (MiroFish backend 内, AGPL)          │
│  - decision oracle : 意思決定要求 → INTERVIEW プロンプト  │
│  - injector        : ゲーム結果 → CREATE_POST + Neo4j    │
│  - barrier         : 全員提出を待って議論フェーズを起動     │
└──────────────┬──────────────────────────────────────────┘
               │ file IPC (既存)
┌──────────────▼──────────────────────────────────────────┐
│ MiroFish / OASIS  (venv311*, step-server 化)             │
│  - ペルソナ・記憶(Neo4j)・ディベート(LLMAction)            │
│  新コマンド: run_rounds(k) / inject_post / get_state      │
└─────────────────────────────────────────────────────────┘
```

\* 3.11 は技術的制約ではなく、既存 `.venv` に pip が無いという運用上の都合。3.12 でも動作する。

キーとなる改造: OASIS ランナーを「gym 的ステップサーバ」にする。現状の「全ラウンド回す → wait モード」を、`run_rounds(k)` コマンドで k ラウンドずつ進められる再入可能ループに分解する。これで外側（実験オーケストレータ）が時間の主導権を持つ構造になり、ゲームと議論を任意に交互配置できる。

結合点は4つだけ:

| # | 方向 | 実装 |
|---|------|------|
| ① 意思決定 | oTree → MiroFish | `POST /api/experiment/decide` → `send_batch_interview` → JSON 強制パース |
| ② 結果注入 | oTree → MiroFish | `POST /api/experiment/inject` → `ManualAction(CREATE_POST)` + `GraphMemoryUpdater.add_activity_from_dict` |
| ③ 時間進行 | Bridge → MiroFish | 新 IPC コマンド `run_rounds(k)`（議論フェーズ） |
| ④ ID 対応 | 双方向 | oTree `participant_label = f"mf_{agent_id}"` ⇄ MiroFish `agent_id`。分析時の join key |

## 3. 閉ループの1サイクル（公共財ゲームを例に）

1. 議論フェーズ: `run_rounds(6)` — 6ラウンド = 6時間ぶん、エージェントが論争 → タイムラインに「協力すべきか」の言説が蓄積
2. 意思決定: oTree bot が Contribute ページに到達 → bridge → INTERVIEW:「あなたは4人グループの公共財ゲームの第3期にいる。元手100pt、拠出分は2倍されて4人に均等配分。前期はグループ合計60ptだった。いくら拠出する？ JSONで {"contribution": int, "reason": str} を返せ」→ agent は自分のペルソナ + Neo4j記憶 + 直近のTLの議論 を踏まえて回答
3. 利得計算: oTree が payoff を計算（＝実験の正典）
4. 結果注入: `inject_post` で「20pt出したのにフリーライダーに食われた」等を当該agentの投稿として社会空間に書き戻す + 記憶に追加
5. 議論フェーズ: `run_rounds(6)` — 次の議論はゲーム結果に影響される

この「言説 → 行動 → 言説」の双方向フィードバックが、oTree 単体でも MiroFish 単体でも得られない、この組み合わせ固有の価値。

## 4. 段階的実装計画

### Phase 0 — 検証と足場（1〜2日）

- oTree 6.0.15 を別 venv に導入、`otree startproject otree_social`。6系での bot CLI・REST API・`participant_label` の挙動を実機確認（推測で進めない）
- `camel-oasis` を Python 3.11 venv に固定し、既存シミュレーションが回ることを確認
- 成果物: `docs/otree-integration/phase0-otree.md`（oTree 側）、`docs/otree-integration/phase0-mirofish.md`（MiroFish 側）

### Phase 1 — oTree アプリ単体（2〜3日）

- `otree_social/public_goods_debate/__init__.py`: チュートリアルの公共財ゲームを土台に、`C.NUM_ROUNDS`、拠出フィールド、`set_payoffs`、加えて議論フェーズを表す `DebatePage`（人間用）とエージェント用のスキップ経路
- live_method は使わない。bot からは自動では呼ばれず、`tests.py` に `call_live_method` フックを書いて async generator を自前で drive する必要があり複雑になるため。通常の Page + form field 構成に統一
- `tests.py` に固定戦略 bot を書き、`otree test` で headless に完走
- 成果物: 人間でも回せる実験アプリ + CSV エクスポートの列定義確定

### Phase 2 — Bridge をダミー方針で貫通（2〜3日）

- `backend/app/api/experiment.py` + `backend/app/services/experiment_bridge.py` を新設
- `POST /decide` はまずランダム／固定戦略を返す（LLM 抜き）
- `otree_social/bridge_client.py` から bot が呼ぶ
- ここで配管を完全にテストする: N=4×10グループ、3期、タイムアウト、リトライ、バリア（全員提出検出）
- 成果物: LLM ゼロで end-to-end が通る状態

### Phase 3 — 意思決定を MiroFish に接続（3〜5日）

- `run_experiment_env.py`（`run_parallel_simulation.py` の helper を import して再利用、既存プリセットは壊さない）に `CommandType.RUN_ROUNDS` / `INJECT_POST` / `GET_STATE` を追加
- `/decide` を `send_batch_interview` 実装に差し替え
- 構造化出力の堅牢化: `llm_client.chat_json` と `oasis_profile_generator._try_fix_json` のパターンを流用。パース失敗時は再問→デフォルト値＋欠測フラグ（黙って埋めない）
- プロンプトテンプレートを `prompts/game_decision.j2` として外出し（アブレーション実験のため）
- 成果物: エージェントが実際に oTree のゲームをプレイする

### Phase 4 — 議論の交互配置と結果注入（3〜5日）

- ③ `run_rounds(k)` と ② `inject_post` を接続し、上記1サイクルを完成
- 議論フェーズのトピック誘導: initial post としてゲーム争点を投入
- 成果物: 閉ループ動作 + `sim_xxx/game/` 配下にゲーム連動ログ

### Phase 5 — 実験デザインと妥当性検証（5〜7日）

トリートメント（oTree `SESSION_CONFIGS` で切替、グループ単位でランダム化）:

| 条件 | 操作 |
|------|------|
| 議論あり／なし | `run_rounds(k)` vs `k=0` |
| 匿名／顕名 | プロンプトに実名・フォロワー数を含めるか |
| 罰あり／なし | oTree 側に punishment ラウンドを追加 |
| ネットワーク構造 | エコーチェンバー（同 stance で follow 偏重）vs 混合 |
| 影響力の非対称 | `influence_weight` 上位をオピニオンリーダーとして配置 |

測定:

- 行動データ: oTree CSV（拠出額・利得・期別推移）＝正典
- 言説データ: `sim_xxx/twitter/actions.jsonl`（投稿・賛否・伝播）
- 内的状態: ゲーム前後の Likert 面接（INTERVIEW）で信念変化
- 派生指標: 協力率の期別減衰、`stance` ドリフト（分極化）、同調度、議論露出量と拠出額の相関、`influence_weight` の因果効果
- 定性分析: `ReportAgent` に「ゲーム結果を踏まえた世論分析」を書かせる

妥当性検証（省略不可）: 既知の人間の頑健な規則性を再現できるか先に確認する — 公共財ゲームの拠出減衰、条件付き協力者の型分布（Fischbacher–Gächter–Fehr 型の分類）、罰の協力促進効果、信頼ゲームの互恵性。LLM エージェントは過剰協力・プロンプト感受性・選択肢順序バイアスが既知なので、選択肢順序のランダム化とプロンプト・アブレーションを標準手順に組み込む。

### Phase 6（任意）— UI 統合（見積未定）

フロントの5ステップウィザード（Graph → Env → Simulation → Report → Interaction）に「Game」ステップを追加。

## 5. リスクと対策

| リスク | 対策 |
|--------|------|
| 依存衝突・ライセンス混在（camel-oasis / oTree 6、AGPL / MIT） | プロセス分離が設計に内在。venv 2つ、HTTP 境界のみ |
| LLM コスト・レイテンシ（550体 × 期 × 面接） | ゲーム参加者は 40〜80体にサブサンプル、`send_batch_interview` でバッチ化、`semaphore=30`、意思決定は qwen2.5:14b／報告は 32b |
| 自由文からの意思決定抽出の失敗 | JSON 強制＋再問＋欠測フラグ。パース失敗率を必ず指標として報告 |
| 再現性（Ollama の非決定性） | モデルダイジェスト固定、temperature 記録、全プロンプト・全応答を保存、seed 管理。完全決定性は保証できない旨を明記 |
| 訓練データ汚染（ゲームを「知っている」） | 中立フレーミング、利得ラベルの改名、新規ゲームでの対照 |
| ライセンス | oTree=MIT、MiroFish=AGPL-3.0。oTree アプリは別ディレクトリ／別リポジトリに置き、bridge 側（AGPL）と HTTP で分離 |
| 人間参加者を混ぜる場合 | IRB／倫理審査、AI 相手であることの開示方針を先に決める |

## 6. 未確定事項（要決定）

> **2026-09-28 決定:** 1 = 反復囚人のジレンマ（2人固定ペア）、2 = エージェントのみ、3 = 48体（24ペア）× 10期、LLM = qwen3:4b。以下は検討時の記録。§3・Phase 1 の公共財ゲームの記述は例示として残すが、実装は PD で行う（アプリ名 `pd_debate`）。

1. **最初のゲーム** — 推奨: 公共財ゲーム（罰あり／なし）。oTree のチュートリアルアプリが土台に使え、人間ベースラインの文献が厚く、議論の効果が出やすい。代案: 信頼ゲーム、反復囚人のジレンマ、熟議型投票（MiroFish の世論ダイナミクスとの親和性は最も高い）

2. **人間参加者を混ぜるか** — エージェント専用なら bot 経路だけで済み最速。人間混在なら Phase 1 の UI 設計と Phase 5 の倫理手続きが重くなる

3. **規模** — ゲーム参加エージェント数（推奨 40〜80）と期数（推奨 10）。550体全員は初手では LLM 予算が現実的でない

## 参考

- oTree REST API: https://otree.readthedocs.io/en/latest/misc/rest_api.html
- oTree Live pages: https://otree.readthedocs.io/en/latest/live.html
- oTree Bots: https://otree.readthedocs.io/en/latest/bots.html
- Facilitating the Integration of LLMs Into Online Experiments (arXiv:2511.19123): https://arxiv.org/pdf/2511.19123
- Integrating Machine Behavior into Human Experiments (MPG Discussion Paper 2024/1): https://pure.mpg.de/rest/items/item_3558922_3/component/file_3558923/content
