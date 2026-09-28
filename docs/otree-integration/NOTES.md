# 観察ログ — oTree × MiroFish-Offline

作業中に気づいた「おかしな点」を記録する。止めずに監視しながら先へ進むための控え。
状態: **監視中** / **解決** / **仕様**（意図どおりと判明）

| # | 日付 | 現象 | 影響 | 状態 |
|---|---|---|---|---|
| 1 | 2026-09-28 | `--max-rounds 1` のスモークで第1ラウンドの行動が0件、所要 0.0 秒。LLM が一度も呼ばれていない | R1 の `step_round()` の LLM 経路が未検証のまま | **解決**: round 0 = 模擬時刻 0時で、既定 `active_hours=range(8,23)` の外だった。hour 9 起点で2ラウンド×両プラットフォームを実 LLM（qwen3:4b）で実行し、CREATE_POST/REPOST/QUOTE_POST/CREATE_COMMENT が記録されることを確認（Twitter 104秒、Reddit 83秒）。step-server に `--start-hour` を追加 |
| 2 | 2026-09-28 | `simulation_config.json` の `time_config` に `morning_hours` / `work_hours` と各 multiplier があるが、`get_active_agents_for_round()` は `peak_hours` / `off_peak_hours` しか見ていない | 朝・日中の活動量が設定どおりにならない（既存挙動。R1 とは無関係） | 監視中。実験用 config では参照されない前提で書く |
| 3 | 2026-09-28 | 既存 config の `off_peak_activity_multiplier=0.05` だと `int(uniform(5,30)*0.05)` が 0〜1 になり、深夜はほぼ誰も動かない | 議論フェーズを深夜帯の round_num で回すと空回りする | 監視中。step-server 側で議論フェーズの開始時刻を活動時間帯に合わせる必要あり |
| 4 | 2026-09-28 | 既存 Twitter の初期投稿は同一エージェントの複数投稿を上書き（最後の1件のみ）、Reddit は全件投稿 | Twitter の初期投稿が黙って欠落しうる | 監視中。R1 では挙動を保存（`allow_multiple_per_agent` で分岐） |
| 5 | 2026-09-28 | `ExperimentIPCHandler.process_commands()`（drain-all 版）は `while True` で `poll_command()` を回すが、`send_response()` が消すのは `<command_id>.json` だけ。ファイル名と `command_id` が食い違うコマンドは消えずに無限ループ。さらにハンドラの例外は捕捉されずサーバごと落ちる | step-server がハング／クラッシュ | **解決**: 同一 drain 内で再出現した `command_id` で打ち切り、例外は `failed` 応答に変換。既存 `ParallelIPCHandler` 側（1 tick 1件）でも同じファイル名の食い違いは毎 tick 再処理になる — 既存挙動として監視中 |
| 6 | 2026-09-28 | 初期投稿（round 0）が最初のアクティブなラウンドの `actions.jsonl` に**重複記録**される。`last_rowid=0` のまま初期投稿後に進めていないため、次の `fetch_new_actions_from_db` が初期投稿の trace 行を拾い直す。既存ランナーにもあった不具合（R1 前から） | 投稿数・伝播指標が初期投稿ぶん水増しされる（Phase 5 の言説データに影響） | **解決**: `publish_initial_posts()` が初期投稿後の rowid を返し、ラウンドはそこから開始。既存ランナー・step-server の両方に適用 |
| 7 | 2026-09-28 | hour 9 起点で2ラウンド実行した時、Twitter と Reddit の `last_rowid` がどちらも 214、`total_actions` もどちらも 9 と一致 | 偶然か、DB の取り違えか | 監視中。db_path は別ファイルと確認済み（twitter_simulation.db / reddit_simulation.db）。trace の内訳は sign_up 199 + create_post 6 + refresh 6 + repost 2 + quote 1 = 214 で整合。偶然の可能性が高い — 次回の実行で再確認 |
| 8 | 2026-09-28 | step-server 経由の INTERVIEW 応答の `timestamp` が 3、外部の `round_num` は 10。OASIS の `sandbox_clock.time_step` は `env.step()` 1回ごとに進むだけで、`--start-hour` や INJECT_POST の回数とは連動しない | 投稿・面接の `created_at` を oTree の期と突き合わせられない | 監視中（HANDOVER の既知事項 4 を実測で確認）。突き合わせは `actions.jsonl` の `round` と step-server の `round_num` を正とし、DB の時刻は使わない方針で Phase 4 に持ち越し |
| 9 | 2026-09-28 | INJECT_POST の投稿が `actions.jsonl` に記録されず、`last_rowid` も進まない。次の RUN_ROUNDS で「エージェントの自発投稿」として記録されてしまう | ゲーム結果の注入と自発的言説が区別できなくなる（分析の根幹） | **解決**: 注入直後に trace を読み進め、`action_args.injected=true` を付けて記録 |
| 10 | 2026-09-28 | INJECT_POST と RUN_ROUNDS の後に agent 0 へ INTERVIEW したところ、理由欄に "No posts were provided for analysis" と返答。INTERVIEW のプロンプトにタイムライン（議論の内容）が入っていない可能性 | Phase 3 で「議論を踏まえた意思決定」が成立しない恐れ | 監視中。Phase 3 着手時に INTERVIEW が参照する文脈（記憶・TL）を確認。入らないなら decision プロンプトに直近の議論を明示的に埋め込む |
| 11 | 2026-09-28 | `otree test pd_debate 48 --export ./export_pd` の出力フォルダに、今回走らせていない `label_test.csv` も出る | 実害なし（エクスポート対象の決め方が「今回のセッション」ではない） | 監視中。分析では `pd_debate_custom.csv` だけを読む |
| 12 | 2026-09-28 | `MiroFish-oTree/` が git リポジトリではない。`pd_debate` を含む oTree 側コードがバージョン管理されていない | 変更履歴が残らない・消失リスク | **解決**: ユーザー承認のうえ独立リポジトリとして `git init`（venv・DB・エクスポート・ログは除外） |
| 13 | 2026-09-28 | bridge の障害注入を `(session, round, agent)` だけで seed していたため、同じリクエストのリトライが**必ず同じ結果で失敗**し、リトライ経路が検証できなかった | テストの意味がなくなる | **解決**: 失敗判定の乱数だけ試行回数込みで seed。方策の乱数は従来どおり（選択の再現性は保持） |
| 14 | 2026-09-28 | クライアントがタイムアウトしてデフォルト値（A・欠測）を採用しても、bridge 側は処理を続けて別の決定（B）をキャッシュ・記録する。bridge ログと oTree CSV が食い違う | bridge ログを正として分析・議論注入すると誤る | **解決**: `round_complete` で oTree が実際に記録した選択（outcomes）を送り、bridge はそれを正として食い違いを `mismatches` に記録。**分析の正は oTree CSV** |
| 15 | 2026-09-28 | リトライが先行リクエストの計算中に届くと、同じ決定を二重に計算し、ログも2行出ていた | Phase 3 では LLM 呼び出しが二重になり、遅いほどさらに増える | **解決**: キーごとの in-flight 待ち合わせ。実測でタイムアウト 0.5 秒 × 3回試行・計算 1.5 秒のとき、計算1回・ログ1行で、3回目の試行がその結果を受け取った |
| 16 | 2026-09-28 | bridge 失敗時のデフォルトは A（協力）。TFT 方策＋障害注入 30% の実行では欠測 17/480 件が全部 A で埋まり、協力率は全期 1.0 のまま — 欠測が行動データ上で見えない | 欠測が協力側に偏る。LLM 導入後は協力率を押し上げるバイアスになりうる | 監視中。分析では `decision_missing=1` を除外 or 感度分析。セッション設定 `bridge_default_choice` で変更可。Phase 5 で方針を決める |
| 17 | 2026-09-28 | bridge の状態はメモリ上だけ。Flask を再起動するとキャッシュ・完了ラウンドが消え、同じセッションの再開時に別の決定が返りうる（random 方策は seed 固定なので同じになるが、LLM では異なる） | 実験の途中で bridge が落ちると再現性が崩れる | 監視中。Phase 3 で `bridge_log.jsonl` からの復元を検討 |
