# 引き継ぎ資料 — oTree × MiroFish-Offline

最終更新: 2026-09-28 / セッション ID: `0401b95c-b37f-4df5-a8a2-5abf184bdc4d`（前: `a40518ee-394f-47b1-8d54-a5b1675b8939`）

## これは何の作業か

oTree（行動実験フレームワーク）の経済ゲームを、MiroFish-Offline の LLM エージェントにプレイさせる。
ゲームのラウンド間に「議論フェーズ」（エージェントが模擬 SNS で論争する）を挟み、
**言説 → 行動 → 言説**の双方向フィードバックを作るのが狙い。

設計の全体像は [plan.md](plan.md)、タスク表は [tasks.md](tasks.md)。
**作業中に気づいた異常・懸念は [NOTES.md](NOTES.md) に番号付きで記録している。着手前に必ず一読。**

## 現在地

**Phase 0〜4 完了。閉ループ（決定 → 結果注入 → 議論 → 次の決定）が実 LLM で動いている。Phase 5 に着手。**

| フェーズ | 状態 | ドキュメント |
|---|---|---|
| 0 検証と足場 | 完了 | [phase0-otree.md](phase0-otree.md), [phase0-mirofish.md](phase0-mirofish.md) |
| R1 ランナーのリファクタ | 完了 | 本書「構成」 |
| 1 oTree アプリ `pd_debate` | 完了 | [phase1-otree.md](phase1-otree.md) |
| 2 bridge（ダミー方針） | 完了 | [phase2-bridge.md](phase2-bridge.md) |
| 3 LLM 意思決定 | 完了（8体×10期） | [phase3-llm-decisions.md](phase3-llm-decisions.md) |
| 4 議論の交互配置と結果注入 | 完了（8体×10期）。議論の空回り対策（NOTES #31）を再検証中 | 同上 |
| 5 実験デザインと妥当性検証 | 着手。ラベル入れ替え・理解テスト・信念調査・話題の再掲・自分の投稿の除外を実装。ラベル偏りを発見（要判断） | phase3-llm-decisions.md, tasks.md |

## 決定事項（2026-09-28 ユーザー承認）

1. **ゲーム** — 反復囚人のジレンマ、2人固定ペア
2. **参加者** — エージェントのみ
3. **規模** — 48体 = 24ペア × 10期（**検証は 8体で実施**。理由は下記「ユーザー判断待ち」）
4. **LLM** — qwen3:4b（`.env` の `LLM_MODEL_NAME` 変更済み）
5. oTree 側を独立した git リポジトリにする — 実施済み

## ユーザー判断（2026-09-28 夜）と実施状況

1. **ラベル偏り** → 3つとも実施: 記号ラベル（△□○◇）、ランダム化（既定はセッション単位。エージェント単位 `label_unit='agent'` も実装）、
   選択肢ごとの利得の言い直し
2. **実行時間** → `OLLAMA_NUM_PARALLEL=8` で Ollama 再起動、**gemma4:e4b** に変更。8体×10期で 11分（議論なし）〜24分（議論あり）。
   `launchctl setenv` は OS 再起動で消える（NOTES #42）
3. **議論の話題** → 一般人ペルソナのシミュレーションを生成（`backend/scripts/experiment/make_general_sim.py`、
   `backend/uploads/simulations/sim_general_s1_n48`、48体、seed=1）

## 新たに判断が必要なこと

1. **gemma4:e4b の行動の妥当性（NOTES #45, #46）** — 協力率が低く（0〜0.6）、条件付き協力が**逆向き**（相手が協力した後ほど裏切る）。
   理解テストは毎回 8/8 正解。人間の規則性を再現しないので、モデル比較（gemma4:12b 等）をするか
2. **議論の話題誘導の強さ（NOTES #44, #46）** — 話題の毎期再掲で反応は増えたが一部だけ。議論ラウンド中の行動プロンプトに
   話題を入れる（誘導が強くなる）かどうか
3. **ラベルのランダム化の単位（NOTES #43）** — セッション単位だと1セッション内で全員が同じ記号の印象を受ける。エージェント単位にするか
4. 本番規模（48体）はまだ回していない

## 構成

**MiroFish 側**（このリポジトリ、AGPL-3.0、ブランチ `feat/otree-step-server`）:

```
backend/scripts/run_parallel_simulation.py  ← R1: setup_platform_env / publish_initial_posts / step_round を抽出
backend/scripts/run_experiment_env.py       ← step-server（議論0ラウンドで起動、IPC でラウンドを進める）
backend/scripts/experiment/
  ipc_protocol.py        ← run_rounds / inject_post / get_state / game_interview の定義
  round_state.py         ← ラウンド状態・RUN_ROUNDS ロック
  step_server_handler.py ← コマンドハンドラ（ParallelIPCHandler を継承）
  analyze_session.py     ← 1セッションの集計（協力率・条件付き協力・LLM・言説）
backend/app/api/experiment.py              ← /api/experiment/{configure,decide,round_complete,state}
backend/app/services/experiment_bridge.py  ← 方策（ダミー / llm）、先取り、議論フェーズ、突き合わせ
backend/app/services/game_decision.py      ← プロンプト生成・応答の解釈・ラベル入れ替え
backend/app/prompts/game_decision.j2       ← 意思決定プロンプト
backend/app/services/simulation_ipc.py     ← 新コマンドのクライアント（send_game_interview 等）
```

**oTree 側**（`/Users/petadimensionlab/workspace/research/MiroFish-oTree/`、MIT、独立 git リポジトリ）:

```
.venv/                              ← otree 6.0.15 / Python 3.12.12
mirofish_otree_test/
  settings.py                       ← pd_debate / pd_debate_faults / pd_debate_llm / pd_debate_llm_debate ほか
  pd_debate/__init__.py             ← ゲーム本体、creating_session、DebateBarrier、custom_export
  pd_debate/bridge_client.py        ← bridge 呼び出し（例外を投げない）
  pd_debate/tests.py                ← bot（MF_BRIDGE_URL があれば bridge、なければ固定戦略）
```

## 実行手順（LLM ラン）

[phase3-llm-decisions.md](phase3-llm-decisions.md) の「起動手順」を参照。要点:

- step-server は `--start-hour 9`、bridge は **`FLASK_DEBUG=false`**（リローダーが状態を消す — NOTES #22）
- 再起動の前に**旧プロセスの消滅を確認**してから DB を消す（旧 step-server がコマンドを横取りする — NOTES #26）。
  step-server は pid ファイルで二重起動を拒否する
- oTree 側は `MF_BRIDGE_TIMEOUT=1800`（`/decide` が1期ぶんの LLM バッチを待つため）
- ラン中にプロンプトや `backend/` のコードを編集しない（NOTES #22, #33）

## 検証済みの結果（要約、すべて 8体×10期・各1ラン）

| ラン | 冒頭投稿 | 期間の議論 | 結果注入 | ラベル | 協力率 第1期 → 第10期 |
|---|---|---|---|---|---|
| okxlo3bf | なし | なし | あり | 通常 | 1.00 → 0.875 |
| 2yzfogyz | あり | 2R（ほぼ空） | あり | 通常 | 1.00 → 0.25 |
| kaoh1px4 | あり | 2R | あり | 通常 | 1.00 → 0.00 |
| （opening） | あり | なし | なし | 通常 | 1.00 → 0.875 |
| ur6otsyx | なし | 2R | なし | 通常 | 1.00 → 0.00 |
| 4zj9mo7g | なし | 2R | なし | **入れ替え** | 0.12 → 0.25（第7〜9期 0.00） |

**最重要（NOTES #39, #40）**: 第1期の選択は**文字 A への偏り**で決まる（入れ替えると A=裏切りを 7/8 が選ぶ）。
利得表の理解テストは 8/8 正解なので読めてはいるが、決定では「A = 協力的」の連想が優先する。
第2期以降は履歴に反応し、議論があると意味の上での相互裏切りに収束する。協力率の水準はラベル依存で、そのまま人間と比べられない。

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

## 既知の不具合・詰まりどころ（抜粋。全件は NOTES.md）

- `otree resetdb` 直後の `otree test` 失敗（PRAGMA user_version）— phase0-otree.md に回避策
- OASIS の時計は `env.step()` ごとに進み、外部の期と一致しない。期の突き合わせは `actions.jsonl` の `round` と step-server の `round_num` を正とする（NOTES #8）
- フィードは毎回2件だけ（NOTES #30）、`active_hours=[9, 17]` の解釈（NOTES #24）、フィードの話題が意思決定を誘導する交絡（NOTES #28）
- 既存の `_get_interview_result()` は面接失敗時に前回の回答を返しうる（UI の面接機能は未修正、NOTES #19）

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
