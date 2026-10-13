# ハンドオフ記録: マスキング済みペルソナ生成（100体）とシミュレーション

日付: 2026-10-11 / 引継元: 低格エージェント（ルール整備目的で投入） / 引継先: 前任の高能力エージェント

## 目的

禁対象データ（HOIKORO/dataAnalysis、以後このデータを指す際は「禁対象データ」）の調査回答者から、
**個人特定不能**かつ**会社特有情報を除去した**ペルソナ 100 体を作り、MiroFish でシミュレーション実行可能にする。

## 現在の状態（引継時点）

- **100 体生成が継続中**: PID 59464（nohup、workers=8、gemma4:e4b/ollama）。
  開始 16:33 頃、約 5.6 分経過。**約 11 分で完了見込み**。
  完了確認: `/tmp/maskgen.log` に `wrote <dir>: 100 personas (0 fallback) ... audit clean` が出力される。
  出力先: `backend/uploads/simulations/sim_masked_s1_n100/`
- **ジェネレータ**: `backend/scripts/experiment/make_masked_personas_sim.py`（57 変数版、EXCLUDED 13 件反映済み）。
- **AGENTS.md に R14 追加済み**（禁止事項 6 項目 + 理由 + 具体事例、未コミット）。

## 決定事項（必ず守る）

1. **変数ホワイトリスト**: persona に使うのは **57 変数のみ**。
   除外 13 件: `org_ident_legacy` / `comm_legacy_*`(6) / `infl_legacy_*`(4) /
   `dept_prevention_focus` / `dept_promotion_focus`。
2. **罠**: `conf_integrating`（対立対処：統合（問題解決））は**一般的な紛争処理尺度のため除外しない**。
   会社合併の「統合」と混同して文字列一致で弾くな。
3. **会社特有語の出力禁止**: 旧社グループ名・旧個社・買収・合併・業界語（ハム等）を生成物に残さない。
   マスキング監査（SCRUB 語ヒット 0 件）を強制する。
4. **個人特定情報を残さない**: 実名・ID・連絡先・ソースの回答者 ID を出力ファイル（reddit/twitter/meta/config）に書かない。
5. **呼称禁止**: 禁対象データのパイプライン名（旧称）を、コード・呼称・出力・この文書にも書かない。
6. **生成後検証必須**: `scripts/verify_outputs.py` とマスキング監査を通してから報告する。
7. **実行規律**: 小サンプル実測 → 検証済み構成で本実行は 1 回だけ。

## ジェネレータの使い方

```
backend/.venv/bin/python backend/scripts/experiment/make_masked_personas_sim.py \
    --n 100 --seed 1 --workers 8 \
    --source-dir <anon のあるディレクトリ> \
    --meta-root <pipeline ルート（data/meta/variables.csv を持つ親）> \
    [--out-dir ...]
```

- LLM は `.env` の `LLM_MODEL_NAME`（現在 `gemma4:e4b`）、`LLM_BASE_URL`（ollama 11434）。
- 事前に単一の `ollama serve` を確認（R5）。
- 期待する構成比: 部長 5 / 課長 23 / 一般社員 72。
- 生成は約 6.7 秒/人（workers=8 実測）。100 体で約 9〜11 分。

## 次のステップ（引継ぎ先が行う）

1. 生成完了を待つ（`/tmp/maskgen.log` の `wrote` 行、または `reddit_profiles.json` の出現）。
2. `uv run python scripts/verify_outputs.py --simulation-id sim_masked_s1_n100` → **15/15 PASS を目標**。
3. マスキング監査（SCRUB 語 0 件）を reddit/twitter/meta/config に対して再実行。
4. スモーク実行: `backend/.venv/bin/python scripts/run_parallel_simulation.py \
   --config <sim>/simulation_config.json --reddit-only --max-rounds 1 --no-wait`
5. 報告を作る（件数・構成比・検証・レート）。

## 検証済みの事項

- 10 体サンプルで生成・マスキング監査 0 件を確認済み（改変前の旧版 8-10 体でも 0 件）。
- 検証 FAIL 対策は対応済み: mbti はシードで割当（非空）、重複名は連番サフィックスで一意化。
- 停止時は `os.makedirs` 済みの空 sim dir が残る。再実行前に `rm -rf` してから実行。

## 学びと再発防止（詳細は AGENTS.md R9#12 / R14）

- tool 引数にバッククオート・実改行など制御文字を書かない。正規表現は `chr()`/`join` で組み立てる。
- 破損したら edit 再入力でなく python パッチ → 直後に `ast.parse` で検証。
- 停止を引き起こす並びは出力・エコーしない。長い自己分析ではなく具体修正 1 回を実行。
- 「見てから決める」相手へは、生成物・変数一覧を**先に見せて**から次へ進む。