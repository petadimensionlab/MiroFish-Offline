# Phase 2 — Bridge をダミー方針で貫通

実施日: 2026-09-28。LLM を使わずに oTree bot → HTTP → MiroFish bridge → oTree の往復を通した。

## 構成

| 側 | ファイル | 役割 |
|---|---|---|
| MiroFish (AGPL) | `backend/app/services/experiment_bridge.py` | セッションごとの状態、ダミー方策、べき等性、in-flight 待ち合わせ、突き合わせ、JSONL ログ |
| MiroFish (AGPL) | `backend/app/api/experiment.py` | Blueprint `/api/experiment` |
| oTree (MIT) | `pd_debate/bridge_client.py` | タイムアウト・リトライ・デフォルト値フォールバック。**例外を投げない** |
| oTree (MIT) | `pd_debate/__init__.py` | `creating_session` で `/configure`、`DebateBarrier` で `/round_complete` |
| oTree (MIT) | `pd_debate/tests.py` | `MF_BRIDGE_URL` があれば bridge、なければ固定戦略 |

## API（`/api/experiment`、応答は `{"success", "data"}`）

| メソッド | パス | 入力 | 出力 |
|---|---|---|---|
| POST | `/configure` | `session_code`, `policy` (`random`/`allc`/`alld`/`tft`), `seed`, `inject_delay_sec`, `inject_error_rate` | 設定値 |
| POST | `/decide` | `session_code`, `round_number`, `agent_id`, `history` [{`round_number`,`own`,`partner`,`payoff`}] | `choice` (A/B), `reason`, `source`, `latency_sec`, `missing`, `cached` |
| POST | `/round_complete` | `session_code`, `round_number`, `n_players`, `cooperation_rate`, `outcomes` [{`agent_id`,`choice`,`source`,`missing`}] | `accepted`, `duplicate`, `decided_by_bridge`, `n_outcomes`, `n_mismatches` |
| GET | `/state/<session_code>` | — | ラウンド別決定数、完了ラウンド、注入エラー数、リトライ数 |

エラー: 入力不備 400（クライアントはリトライしない）、注入障害 503、その他 500。
ログ: `backend/uploads/experiments/<session_code>/bridge_log.jsonl`（gitignore 済み）。

## 設計上の約束

- **`/decide` はべき等**: 同じ `(session, round, agent)` には同じ決定を返す（`cached: true`）
- **in-flight 待ち合わせ**: 計算中に同じキーのリトライが来たら、計算せずに先行の結果を待つ（NOTES #15）
- **`/round_complete` はべき等**: 期ごとに1回だけ受理。Phase 4 はここで議論を起動する
- **正は oTree**: `outcomes` を bridge の決定と突き合わせ、食い違いを `mismatches` に記録（NOTES #14）
- **oTree 側は落ちない**: bridge 不達・タイムアウト・不正応答はすべて `decision_source=default`, `decision_missing=1` で続行

## クライアント設定（環境変数）

`MF_BRIDGE_URL`（例 `http://127.0.0.1:5001/api/experiment`、未設定なら bridge を使わない）、
`MF_BRIDGE_TIMEOUT`（既定 10 秒）、`MF_BRIDGE_ATTEMPTS`（既定 3）、`MF_BRIDGE_BACKOFF`（既定 0.5 秒 × 試行回数）。
セッション設定: `bridge_policy`, `bridge_seed`, `bridge_inject_delay_sec`, `bridge_inject_error_rate`, `bridge_default_choice`（既定 A）。

## 検証結果（48体 × 10期、Flask を port 5055 で起動）

| ケース | 結果 |
|---|---|
| `pd_debate`（random 方策） | 20秒で完走。480/480 が bridge 由来、欠測 0。バリア10回、各期 48件を突き合わせて食い違い 0 |
| `pd_debate_faults`（tft、障害注入 30%、3回試行） | 35秒で完走。欠測 17/480（理論値 0.3³ × 480 ≈ 13）、残りはリトライで成功。食い違い 0 |
| 計算 1.5 秒・タイムアウト 0.5 秒・3回試行 | 3回目の試行が先行計算の結果（B）を受け取る。計算1回・ログ1行 |
| 同上・2回試行 | oTree はデフォルト A（欠測）、bridge は B。`round_complete` で `mismatches` 1件を検出 |
| bridge 不達（ポート閉） | `decide` はデフォルト値＋欠測、`notify_round_complete` はエラーを返すだけで例外なし |
| bridge 無効（`MF_BRIDGE_URL` なし） | 固定戦略で従来どおり完走（回帰確認） |

tasks.md の「N=4×10グループ、3期」は、PD の決定（2人ペア）に合わせて本番規模の 48体 × 10期で代替した。

## 未対応（次フェーズ）

- 状態がメモリのみ（NOTES #17）
- 並行性は `otree test` の構造上シリアルでしか検証できていない（phase0-otree.md の「テストギャップ」）。bridge 側はロックで保護しているが、並行負荷テストは未実施
