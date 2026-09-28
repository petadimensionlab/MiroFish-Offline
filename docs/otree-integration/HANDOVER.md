# 引き継ぎ資料 — oTree × MiroFish-Offline

最終更新: 2026-09-28 / 前セッション ID: `a40518ee-394f-47b1-8d54-a5b1675b8939`

## これは何の作業か

oTree（行動実験フレームワーク）の経済ゲームを、MiroFish-Offline の LLM エージェントにプレイさせる。
ゲームのラウンド間に「議論フェーズ」（エージェントが模擬 SNS で論争する）を挟み、
**言説 → 行動 → 言説**の双方向フィードバックを作るのが狙い。

設計の全体像は [plan.md](plan.md)、タスク表は [tasks.md](tasks.md)。

## 現在地

**Phase 0 完了、refactor R1 完了、step-server は実 LLM で end-to-end 動作確認済み。Phase 1（oTree `pd_debate`）は未着手。**

作業中に気づいた異常・懸念は [NOTES.md](NOTES.md) の観察ログに記録している（着手前に一読）。

| ドキュメント | 行数 | 内容 |
|---|---|---|
| [plan.md](plan.md) | 174 | 全体設計。**一部に訂正が必要**（下記「訂正が必要な箇所」参照） |
| [tasks.md](tasks.md) | 77 | Phase 0〜6 のチェックリスト |
| [phase0-otree.md](phase0-otree.md) | 510 | oTree 6.0.15 の実機検証結果 |
| [phase0-mirofish.md](phase0-mirofish.md) | 871 | MiroFish 側の検証結果と step-server 設計 |

## ファイルの置き場所

**MiroFish 側**（このリポジトリ、AGPL-3.0）— すべて未コミットの新規ファイル:

```
docs/otree-integration/          ← ドキュメント一式
backend/scripts/run_experiment_env.py    ← step-server の骨格（未完成）
backend/scripts/experiment/
  __init__.py
  ipc_protocol.py                ← 新 IPC コマンドの定義
  round_state.py                 ← ラウンド状態管理
  step_server_handler.py         ← コマンドハンドラ
backend/venv311/                 ← Python 3.11.16（gitignore 済み）
```

`run_experiment_env.py` は import は通るが `run_rounds()` / `init_platforms()` が
意図的に `NotImplementedError` のまま。既存ファイルへのリファクタ（下記 R1）が前提。

**oTree 側**（別ディレクトリ、MIT。ライセンス分離のため MiroFish リポジトリ外）:

```
/Users/petadimensionlab/workspace/research/MiroFish-oTree/
  .venv/                         ← otree 6.0.15 / Python 3.12.12
  mirofish_otree_test/
    settings.py                  ← mf_group, label_test, live_test を登録済み
    mf_group/                    ← 2人グループ・2ラウンドの検証用アプリ
    label_test/                  ← participant.label の検証用
    live_test/                   ← live_method の検証用
    network_test_server.py       ← bot からの HTTP 呼び出し検証用サーバ
    export_*/                    ← 各検証の CSV 出力
```

## Phase 0 で確定したこと（実測済み）

意思決定チャネルの成立に関わる要点だけ。詳細は各 phase0 ドキュメント。

1. **bot から外部 HTTP を叩ける。** `otree test` の `PlayerBot.play_round` 内でブロッキング
   `requests.post` が動き、返り値が実際に提出フォーム値になる。→ 「oTree bot → bridge →
   MiroFish INTERVIEW」経路は成立する。
2. **ラウンドごとのバリアが張れる。** `WaitPage.after_all_players_arrive(group)` が
   グループ全員の提出値が読める状態で1回だけ発火する。議論フェーズの起動点にできる。
3. **bot runner は完全にシリアル。** 並行性ゼロ。LLM レイテンシが単純加算される。
   48体 × 10期で素朴な実装だと約40分、バリア起点の decision-prefetch 方式で約12〜16分。
4. **未捕捉例外はラン全体を落とす**（exit 1）。bridge 呼び出しは必ず try/except で囲む。
   catch すればランは完走する。
5. **`participant.label` の付与は `creating_session()` のみ。** REST では渡せない（403）。
   かつ `Subsession` の**メソッドとして書くと黙って無視される** — `__init__.py` の
   モジュールレベル関数として書くこと。これを踏むとラベル列が空のまま原因不明になる。
6. **REST 認証ヘッダ名は `otree-rest-key`**（環境変数名 `OTREE_REST_KEY` とは別）。
   デフォルトは無認証。`OTREE_AUTH_LEVEL` が `DEMO`/`STUDY` の時だけ強制される。
7. **`live_method` は bot では呼ばれない。** `tests.py` に `call_live_method` フックを
   明示し、渡される async generator を自前で drive すれば動く。async generator 形式の
   `live_method` はサポートされるが、素の coroutine は拒否される。
8. **INTERVIEW の応答形状**（sqlite `trace.info`）:
   `{"prompt": ..., "response": ..., "interview_id": "<created_at>_<user_id>"}`
9. **`ManualAction` にラウンド境界の制約はない。** INTERVIEW / CREATE_POST は
   シミュレーション途中でも `env.step()` 経由で発行できる。

## 訂正が必要な箇所

`plan.md` に、検証前の推測を断定で書いた箇所が残っている。**Phase 1 に入る前に直すこと。**

| 箇所 | 現状の記述 | 実際 |
|---|---|---|
| plan.md:37, 152 | 「camel-oasis は Python <3.12 前提」 | **誤り。** `pyproject.toml` は `>=3.11` のみで上限なし。3.12 で実動作確認済み。`ROADMAP.md:12` の記載も古い |
| plan.md:58 | 構成図が Python 3.11 を前提 | 3.11 推奨の理由は「`.venv` に pip が入っていない」という運用上の都合のみ。技術的制約ではない |
| plan.md:96 | 「live_method は使わない（bot が通らないため）」 | 方向は正しいが理由が不正確。`call_live_method` フックを書けば呼べる |

## 既知の不具合・詰まりどころ

1. **`.env` の `LLM_MODEL_NAME` が存在しないモデルを指している。**
   設定値は `pdurugyan/qwen3.5-9b-deepseek-v4-flash-Q4_K_M` だが、`ollama list` にあるのは
   qwen3:4b / bge-m3 / gemma4:12b / gemma4:e4b / nomic-embed-text の5つ。**このままでは動かない。**
   モデルを pull するか `.env` を実在するモデル名に変える必要がある。
2. **`otree resetdb` の直後に `otree test` / `devserver` が失敗する。**
   `resetdb` が sqlite ファイルに `PRAGMA user_version` を書き込まないため、
   次のプロセス起動で "oTree has been updated. Please delete your database" で exit 1。
   回避策は phase0-otree.md に記載（sqlite3 CLI で pragma を手動設定）。
3. **`sys.executable` の venv 曖昧性。** `simulation_runner.py:440` の
   `subprocess.Popen([sys.executable, ...])` は Flask を動かしている venv を黙って使う。
   step-server を venv311 で動かすなら明示的に指定すること。
4. **`env.step()` が Twitter の `sandbox_clock.time_step` を毎回インクリメントする。**
   INTERVIEW / INJECT_POST を RUN_ROUNDS の合間に挟むと、外部で管理する `round_num` と
   ズレる。設計上の注意点。

## R1 と step-server（2026-09-28 完了）

- `run_parallel_simulation.py` から `setup_platform_env()` / `publish_initial_posts()` /
  `step_round()` を抽出。`run_twitter_simulation` / `run_reddit_simulation` はこれらを呼ぶだけ。
  既存ランナーは `--max-rounds 1 --no-wait` で回帰確認済み
- `run_experiment_env.py` を実装し直し、`ExperimentStepServer`（NotImplementedError 版）は廃止。
  起動時に議論ラウンドを0回実行し、`ExperimentIPCHandler` で
  run_rounds / inject_post / get_state / interview / batch_interview / close_env を受ける。
  `--start-hour` で最初の議論ラウンドの模擬時刻を指定（既定の 0 時だと誰も動かない）
- 実測: qwen3:4b・199体・1時間あたり3体で 1ラウンド約 50〜60 秒（Twitter のみ）
- ついでに直した既存不具合: 初期投稿の二重記録（NOTES #6）、注入投稿が自発投稿として
  記録される問題（NOTES #9）、drain ループの無限ループ／例外でのクラッシュ（NOTES #5）

## 次にやること

1. Phase 1: `MiroFish-oTree/mirofish_otree_test/` 側に `pd_debate` アプリ（2人固定ペア×10期）と
   固定戦略 bot を作り、`otree test` で完走させる。CSV 列定義を確定
2. Phase 2: bridge（`backend/app/api/experiment.py` ほか）をダミー方針で貫通
3. NOTES #8（時刻のズレ）と #10（INTERVIEW に議論の文脈が入らない疑い）は Phase 3〜4 の設計に効く

## 決定事項（2026-09-28 ユーザー承認）

1. **ゲーム** — 反復囚人のジレンマ、2人固定ペア
2. **参加者** — エージェントのみ
3. **規模** — 48体 = 24ペア × 10期
4. **LLM** — qwen3:4b（`.env` 変更済み。既知の不具合 1 は解消）

`plan.md` の訂正3か所は反映済み。

## 保留中の課題（作業とは別）

Claude Code の課金先を個人 Max から Team 組織へ切り替える件が未解決。

- 現在のログイン: `admin@petadimension.org` / 組織 `06452b5a-3ddf-4685-a46a-4dde82e04a36`
  （`organizationType: claude_max`、`seatTier: null`）
- Team 組織の UUID: `d30a8a5c-f949-4cfc-ad21-96bd8653b1f7`
- `/login` を実行しても個人 Max の組織に解決される
- `forceLoginOrgUUID` は**managed settings（`/Library/Application Support/ClaudeCode/managed-settings.json`、sudo 必須）からのみ強制される**。ユーザー設定に書いても効かない
- ただしこの設定は**アカウントを切り替えるものではなく、指定組織以外のログインを拒否する**もの。
  seat が実際に無い場合 **Claude Code が起動しなくなる**（復旧は sudo でファイル削除）
- 未実施。実行するかはユーザー判断待ち
