# Phase 0-B: MiroFish 側の検証とステップサーバ設計

作成日: 2026-09-28
対象: `backend/scripts/run_parallel_simulation.py` を中心とした OASIS ランナーの
「外部オーケストレータがラウンドを刻む再開可能ステップサーバ」化。
併走ドキュメント: `docs/otree-integration/plan.md`（全体構想）/ `phase0-otree.md`（oTree 側、別エージェント作成、本書では触れない）。

このディレクトリの `plan.md` / `tasks.md` は編集していない。

---

## 0. サマリ（先に結論）

- **インタプリタ**: `backend/.venv`（Python 3.12.12）でも `camel-oasis==0.2.5` / `camel-ai==0.2.78` は
  問題なく import でき、`run_parallel_simulation.py` が実際に触れるコードパス
  （`create_model` 手前までのモジュールロード、`ManualAction`/`ActionType` 等のシンボル解決）も
  `venv311`（3.11.16）と同一に動作することを実行で確認した。**`ROADMAP.md:12` の
  「camel-oasis は Python <3.12 が必要」という記載は現状の実態と食い違っており、古い情報である。**
  詳細は §1。
- INTERVIEW / CREATE_POST の `ManualAction` はどちらも「ラウンドループの外側から `env.step()` を
  1回呼ぶ」という汎用パスを通るだけで、OASIS 側に「ラウンド境界でしか受け付けない」という制約は
  見当たらない（§2、一部はコード読解ベースで明記）。
- ステップサーバ化の核心的な障害は **「1ラウンド分の処理がループ本体に直書きされていて、単独で
  呼べる関数になっていない」** ことであり、それ以外の主要ヘルパーはほぼ全部 import 可能だった
  （§3、実行で確認）。
- 新規ファイルのみで動くプロトタイプ `backend/scripts/run_experiment_env.py` と
  `backend/scripts/experiment/` を作成済み。「import が通る」を超えて、
  **実在の `simulation_config.json` を渡して実際に起動し、意図通り `NotImplementedError` に
  到達するところまで実行で確認した**（3.11 / 3.12 両方）。§4。

---

## 1. Task 1 — インタプリタ判定

### 1.1 バージョンとパッケージの実態

```
$ backend/.venv/bin/python --version
Python 3.12.12

$ backend/venv311/bin/python --version
Python 3.11.16
```

`backend/pyproject.toml`:
```
5:  requires-python = ">=3.11"
23:  "camel-oasis==0.2.5",
24:  "camel-ai==0.2.78",
```
上限バージョン指定（`<3.12` 相当の制約）は **どこにも存在しない**。

`importlib.metadata` で両 venv の実インストール版を確認（`.venv` には pip 自体が入っておらず
`pip show` は使えなかったため `importlib.metadata.version()` で代替）:

```
$ backend/.venv/bin/python -c "from importlib.metadata import version; \
    print('camel-oasis', version('camel-oasis')); print('camel-ai', version('camel-ai'))"
camel-oasis = 0.2.5
camel-ai = 0.2.78

$ backend/venv311/bin/python -c "同上"
camel-oasis = 0.2.5
camel-ai = 0.2.78
```

→ **両 venv に全く同じバージョンが入っている**（別々にロックされたわけではなく、同じ pin を
別インタプリタで解決した結果）。

### 1.2 素の import

```
$ backend/.venv/bin/python -c "import oasis, camel; print(oasis.__file__); print(camel.__file__)"
/Users/.../backend/.venv/lib/python3.12/site-packages/oasis/__init__.py
/Users/.../backend/.venv/lib/python3.12/site-packages/camel/__init__.py
```
例外なし。3.12 で普通に import できる。

### 1.3 `run_parallel_simulation.py` が実際に触れるコードパスでの確認

素の `import oasis, camel` だけでは「スクリプトが実際に使うシンボル解決」までは保証されない。
そこで `backend/scripts/run_parallel_simulation.py` をモジュールとして import し（`if __name__ ==
"__main__"` ガードがあるので `main()` は起動しない）、同ファイルが使っている主要シンボルが
両インタプリタで解決できることを確認した:

```
$ backend/venv311/bin/python -c "
import sys; sys.path.insert(0,'backend/scripts')
import run_parallel_simulation as rps
for name in ['create_model','get_active_agents_for_round','fetch_new_actions_from_db',
             'load_config','get_agent_names_from_config','init_logging_for_simulation',
             'ParallelIPCHandler','CommandType','PlatformSimulation']:
    print(name, '->', getattr(rps, name, 'MISSING'))
"
Loaded environment configuration: /Users/.../.env
create_model -> <function create_model at 0x1201f6ac0>
get_active_agents_for_round -> <function get_active_agents_for_round at 0x1201f6b60>
fetch_new_actions_from_db -> <function fetch_new_actions_from_db at 0x1201f67a0>
load_config -> <function load_config at 0x10a438e00>
get_agent_names_from_config -> <function get_agent_names_from_config at 0x1201f6700>
init_logging_for_simulation -> <function init_logging_for_simulation at 0x10a438d60>
ParallelIPCHandler -> <class 'run_parallel_simulation.ParallelIPCHandler'>
CommandType -> <class 'run_parallel_simulation.CommandType'>
PlatformSimulation -> <class 'run_parallel_simulation.PlatformSimulation'>
```

さらに一歩進めて、**新規プロトタイプ `run_experiment_env.py`（§4）を実在の
`simulation_config.json` に対して実際に起動し**、`.env` ロード → `run_parallel_simulation`
のフル import（camel/oasis 含む）→ 設定ロード → ラウンド状態初期化、という現実のコードパスを
3.11 / 3.12 **両方**で走らせ、両者が完全に同一の挙動（意図的な `NotImplementedError`到達）を
示すことを確認した:

```
$ backend/venv311/bin/python backend/scripts/run_experiment_env.py --config <実在の sim の config>
Loaded environment configuration: /Users/.../.env
...
NotImplementedError: init_platforms() documents a working zero-refactor lever (max_rounds=0) ...

$ backend/.venv/bin/python backend/scripts/run_experiment_env.py --config <同じ config>
Loaded environment configuration: /Users/.../.env
...
NotImplementedError: init_platforms() documents a working zero-refactor lever (max_rounds=0) ...
```
（使用した config: `backend/uploads/simulations/sim_f2feaaf349ea/simulation_config.json`。
どちらのインタプリタでもトレースバックのメッセージ・行番号まで完全に一致。）

### 1.4 結論と推奨

- **`ROADMAP.md:12` の「camel-oasis は Python <3.12 が必要」は現状のパッケージでは再現しない。
  古い情報として扱ってよい。** おそらく camel-ai の過去バージョン、もしくは別の依存
  （numpy/pydantic 系のネイティブ拡張など）が原因だった問題が、`camel-ai==0.2.78` の時点では
  解消されている。
- とはいえ **本番のステップサーバプロセスは `backend/venv311` で動かすことを推奨する。**
  理由は「3.12 が動かないから」ではなく:
  1. `backend/venv311` は「既に動作確認済みの環境」として `ROADMAP.md` や本タスクの前提に
     明記されており、退行時の切り分けコストが低い。
  2. `backend/.venv`（3.12）は **pip が入っていない**（`No module named pip`）などツール面で
     手入れが行き届いていない兆候があり、依存の追加・更新が必要になった際の運用コストが高い。
  3. Flask 側 (`app/services/simulation_runner.py:440` 付近) は `subprocess.Popen([sys.executable,
     script_path, ...])` で **Flask プロセス自身のインタプリタ**をそのまま子プロセスに使っている
     （`SCRIPTS_DIR` 配下のスクリプトを起動する際に別 venv を明示的に選んでいない）。Flask 自体を
     どちらの venv で動かすかによって、ここが自動的に決まってしまう点は Phase 1 以降で明示的に
     固定すべき設定漏れとして risk リストに記載した（§6）。
- 3.12 対応そのものは「動くと確認できた」という朗報であり、`ROADMAP.md` の該当項目
  （v0.3.0 節）を「解決済み / 要再調査」に更新することを推奨するが、**本タスクのルールにより
  既存ファイルは編集していない**（提案のみ）。

---

## 2. Task 2 — 2つのプリミティブの検証

### 2.1 `env.step()` のディスパッチ機構（**実インストール済みライブラリのソースを読んだ**。
実行はしていないが、推測ではなく `backend/venv311/lib/python3.11/site-packages/oasis/` の
実ファイルを cat した結果）

`oasis/environment/env.py:136-198` の `step()` 全文を確認した。要点:

```python
async def step(self, actions: dict[...]) -> None:
    await self.platform.update_rec_table()
    tasks = []
    for agent, action in actions.items():
        ...
        if isinstance(action, ManualAction):
            if action.action_type == ActionType.INTERVIEW:
                tasks.append(self._perform_interview_action(agent, interview_prompt))
            else:
                tasks.append(agent.perform_action_by_data(action.action_type, **action.action_args))
        elif isinstance(action, LLMAction):
            tasks.append(self._perform_llm_action(agent))
    await asyncio.gather(*tasks)
    if self.platform_type == DefaultPlatformType.TWITTER:
        self.platform.sandbox_clock.time_step += 1
```

**INTERVIEW にも CREATE_POST にも「ラウンド境界でしか受け付けない」というゲートは
`env.step()` 自体には存在しない。** `actions` dict に何を混ぜても（`ManualAction(INTERVIEW)`
1個だけでも、`ManualAction(CREATE_POST)` 1個だけでも）等しく処理される。つまり
「`env.step()` を呼べる」という条件だけが必要で、それは `main()` の wait モードのループ内で
既に満たされている（現状の制約は「呼べる場所が wait モードしかない」という**呼び出し側**の
配線の問題であり、oasis 側の実行時制約ではない）。この点は plan.md にも既に記載されている
現状認識（「IPC コマンドのポーリングは全ラウンド終了後の wait モードでのみ行われる」）と
矛盾しない ― むしろ「oasis 自体は妨げていない」という部分を今回コードで裏付けた形。

**ただし1つ、ドキュメント化されていない副作用を発見した**: `step()` の最後で
**Twitter の場合のみ** `self.platform.sandbox_clock.time_step += 1` が**呼び出し理由を問わず
無条件に**実行される。Reddit 側には対応する行が無い（同じ `step()` 内に Reddit 用の分岐が
存在しない＝ Reddit の `sandbox_clock` は `step()` からは進まない、少なくともこのファイルの
範囲では）。

→ **これは新設計にとって具体的なリスクである**（§6 にも記載）。現行の
`run_twitter_simulation` は `simulated_hour`/`simulated_day` を `round_num`
というローカル変数から**独立に**計算しており（`env.platform.sandbox_clock` を読み返しては
いない）、`round_num` と oasis 内部の `sandbox_clock.time_step` は「たまたま同じペースで
進んでいる」だけの2つの独立した値である。もしステップサーバが `RUN_ROUNDS` バーストの
**合間**に `INTERVIEW` や `INJECT_POST`（＝追加の `env.step()` 呼び出し）を挟むと、
`sandbox_clock.time_step` だけが余計に進み、外部の `round_num` カーソルとは静かに
ズレ始める。この時計は投稿の内部タイムスタンプ処理などに使われている可能性があり
（未調査）、Phase 1 で実際に `RUN_ROUNDS` と `INJECT_POST`/`INTERVIEW` を混在させる前に、
`sandbox_clock` を外部から読める形にするか、少なくとも「時計のズレは実害があるか」を
確認すべきタスクとして残す。

`agent.perform_interview()`（`oasis/social_agent/agent.py:197-246`）も実ソースを確認:
エージェント自身の `self.memory.get_context()`（＝そのエージェントの過去の記憶・行動履歴）を
使って実際に LLM 呼び出し（`self._aget_model_response`）を行い、
`{"prompt": interview_prompt, "response": content}` という dict を
`self.env.action.perform_action(interview_data, ActionType.INTERVIEW.value)` 経由で
trace テーブルに書き込む。これが §2.3 で見る実データの `"prompt"`/`"response"` キーの
出どころである。**状態面での前提はエージェントの記憶が存在すること（＝そのラウンドで
何もアクションしていなくても、`generate_*_agent_graph` で作られた時点のプロフィール由来の
記憶は既にあるので問題ない）くらいで、「直前に何かアクションした後でないと質問できない」
といった制約はコード上見当たらない。**

`agent.perform_action_by_data()`（`agent.py:278-296`）は非 INTERVIEW の `ManualAction`
（＝ CREATE_POST 含む）の実行経路。`func_name` を `self.env.action.get_openai_function_list()`
から名前で引いて直接呼ぶだけで、「このエージェントが今ラウンドで既に投稿済みでないか」
のような重複防止チェックは無い。ただし `trace`/`post` テーブルは
`FOREIGN KEY(user_id) REFERENCES user(user_id)` （`.schema trace` で確認、§2.3）を持つため、
**`agent_graph` に存在しない（＝ `generate_twitter_agent_graph`/`generate_reddit_agent_graph`
でロードされたプロフィールに含まれない）任意の `agent_id` に対して CREATE_POST を注入する
ことはできない**（FK 違反、あるいは `agent_graph.get_agent(agent_id)` の時点で例外）。
「任意のエージェント」とは「その回のシミュレーションに実在するエージェントなら誰でも」
という意味であり、「シミュレーション外の任意の ID」という意味ではない、と明確化しておく。

### 2.2 実行による直接検証（§2.1 の裏付け）

§2.1 のソース読解（`env.step()` にラウンド境界ゲートが無いという結論）を、**実際に
LLM を動かして裏付けた**。スタンドアロンのプローブスクリプト（`profiles.csv` + `probe.py`。
リポジトリ外のスクラッチパッドに作成、リポジトリ内ファイルは変更していない）で、
2エージェントの Twitter `oasis.make()` 環境（ローカル Ollama `qwen3:4b` 使用）に対して
以下を**順に**実行:

1. 通常ラウンド `env.step({agent0: LLMAction(), agent1: LLMAction()})`
2. **実ラウンドが1回走った後**（round 0 の特殊処理ではなく）に
   `env.step({agent1: ManualAction(CREATE_POST, {"content": "INJECTED_MIDSIM_POST_MARKER: ..."})})`
3. `env.step({agent0: ManualAction(INTERVIEW, {"prompt": "Reply with EXACTLY this JSON: {\"choice\": \"cooperate\", \"confidence\": 0.8}"})})`
4. もう1ラウンド `LLMAction`（手動介入後も env が正常にステップ可能か確認）
5. 別エージェントへの2回目の INTERVIEW
6. `env.close()` 後、`trace` テーブルを直接読む

`backend/venv311` と `backend/.venv` の**両方**で実行し、完全に同一の成功パターンを得た:

```
[probe] env reset OK
[probe] step A (LLMAction round) OK
[probe] step B (ManualAction CREATE_POST mid-sim) OK
[probe] step C (ManualAction INTERVIEW mid-sim) OK
[probe] step D (LLMAction round after manual actions) OK
[probe] step E (second ManualAction INTERVIEW) OK
[probe] env closed OK

[probe] trace table has 11 rows total
  rowid=5 user_id=1 created_at=1 action=create_post   info={"content": "INJECTED_MIDSIM_POST_MARKER: the coffee experiment begins now.", "post_id": 3}
  rowid=6 user_id=0 created_at=2 action=interview     info={"prompt": "Reply with EXACTLY this JSON...", "response": "{\"choice\": \"cooperate\", \"confidence\": 0.8}", "interview_id": "2_0"}
  rowid=11 user_id=1 created_at=4 action=interview    info={"prompt": "In one short sentence...", "response": "I just posted about how the coffee experiment models coordination games...", "interview_id": "4_1"}
```

これにより §2.1 の結論（「ラウンド境界でしか受け付けないというゲートは無い」）は
**コード読解だけでなく実行でも確認済み**に格上げできる。加えて2点、実行結果から直接
読み取れたことを追記する:

- `created_at` は §2.1 で読んだ `sandbox_clock.time_step`（Twitter のみ無条件インクリメント）
  と整合する形で、`env.step()` を呼ぶたびに 1 ずつ単調増加していた（0, 0, 1, 2, 3, 3, 4）。
  §6 のリスク項目4（`sandbox_clock` のドリフト）の実データ上の裏付けでもある。
- プローブでは `qwen3:4b` が「このJSONだけを返せ」という指示に厳密に従ったが、これは
  モデル依存でありOASIS側が保証するものではない（`info.response` は自由テキスト、
  INTERVIEW は camel-ai のツールコール経路を通らない）。意思決定オラクル用プロンプトは
  パース失敗を前提にした寛容な抽出ロジックを別途持つ必要がある。

### 2.3 sqlite `trace` テーブルの実データ（**実際に実行した**。`sqlite3 -readonly` で
実ファイルに対してクエリした）

対象DBの棚卸し（`backend/uploads/simulations/` 配下、`.tables` で確認）:
```
sim_f24d83d033ca/twitter_simulation.db  … trace あり、action='interview' が13件
sim_f24d83d033ca/reddit_simulation.db   … trace あり、interview 0件
sim_f2feaaf349ea/twitter_simulation.db  … trace あり、interview 0件
sim_f2feaaf349ea/reddit_simulation.db   … trace あり、interview 0件
sim_91d78d67ddfb/                        … db ファイル無し（未実行 or 別形式）
```

`trace` テーブルのスキーマ（`sqlite3 -readonly ... ".schema trace"`）:
```sql
CREATE TABLE trace (
    user_id INTEGER,
    created_at DATETIME,
    action TEXT,
    info TEXT,
    PRIMARY KEY(user_id, created_at, action, info),
    FOREIGN KEY(user_id) REFERENCES user(user_id)
);
```
（Twitter 側の `created_at` は整数の疑似タイムスタンプ、Reddit 側は日時文字列 ―
`fetch_new_actions_from_db` のコメント「different platforms have different created_at
formats」と一致。）

`action` の分布（`SELECT action, COUNT(*) FROM trace GROUP BY action`、一部抜粋）:
`sim_f24d83d033ca/twitter_simulation.db` → `refresh:1780, repost:269, quote_post:208,
sign_up:199, create_post:147, do_nothing:17, like_post:16, interview:13`

実際の INTERVIEW 行（`SELECT user_id, created_at, info FROM trace WHERE action='interview'
ORDER BY created_at LIMIT 3`、1件をそのまま掲載・長いので `response` は代表1件のみ全文、
他は先頭のみ）:

```
user_id=129, created_at=119
info = {
  "prompt": "You are being interviewed. Please combine your character profile, all past
    memories and actions, and directly answer the following questions in plain text.
    Response requirements:
    1. Answer directly in natural language, do not call any tools
    2. Do not return JSON format or tool call format
    3. Do not use Markdown headings (e.g., #, ##, ###)
    4. Answer the questions in order, with each answer starting with 'Question X:'
    5. Separate each answer with a blank line
    6. Provide substantive answers, at least 2-3 sentences per question

    1. How do key publications shape your organization's strategies?
    2. What emotional impact does this research have on you personally?
    3. Can you share a specific instance where research influenced policy?
    4. How do you ensure the accuracy of data in biodiversity studies?
    5. In what ways does this paper challenge existing conservation practices?",
  "response": "Question 1: Key publications shape Hillebrand's strategies by providing
    evidence-based insights into ecosystem stability and change. ... (以下4問分、各2-4文の
    自然文が続く。全文はDB参照) ...",
  "interview_id": "119_129"
}
```
他2行（`user_id=0, created_at=119, interview_id="119_0"`／`user_id=187, created_at=119,
interview_id="119_187"`）も同一 `prompt`（バッチ質問）に対する別エージェントの `response`
で、形は完全に同一。

**確定した形状**: トップレベルキーは常に `prompt` / `response` / `interview_id` の3つ
（他のキーは観測されなかった）。`interview_id` は `"{created_at}_{user_id}"` という
命名（例の3行がいずれも `created_at=119` で揃っており、`interview_id` の前半と一致する
ことから逆算）。`run_parallel_simulation.py:517-559` の `_get_interview_result` が行う
`info.get("response", info)` は、この実データに対して **常に `"response"`
キーがヒットする**ことを確認した（＝フォールバック分岐 `info` 全体を返すケースは
今回のサンプルでは発生していない）。

### 2.4 Ollama 到達性（**実際に実行した**）

```
$ ollama list
NAME                       ID              SIZE      MODIFIED
qwen3:4b                   359d7dd4bcda    2.5 GB    3 weeks ago
bge-m3:latest              790764642607    1.2 GB    2 months ago
gemma4:12b                 4eb23ef187e2    7.6 GB    2 months ago
gemma4:e4b                 c6eb396dbd59    9.6 GB    2 months ago
nomic-embed-text:latest    0a109f422b47    274 MB    4 months ago

$ grep '^LLM_MODEL_NAME' .env
LLM_MODEL_NAME=pdurugyan/qwen3.5-9b-deepseek-v4-flash-Q4_K_M
```

Ollama デーモン自体は到達可能・応答する。**ただし `.env` の `LLM_MODEL_NAME` に設定された
モデル（`pdurugyan/qwen3.5-9b-deepseek-v4-flash-Q4_K_M`）は `ollama list` の出力に
存在しない。** 今 `create_model()`（`run_parallel_simulation.py:984-1035`）経由で
実際にシミュレーションを起動すると、このモデル未 pull が原因で失敗する可能性が高い
（未検証・実際にモデル呼び出しまでは行っていない。`ollama pull
pdurugyan/qwen3.5-9b-deepseek-v4-flash-Q4_K_M` などの対応が必要になる想定）。
`app/config.py:32-33` のデフォルト値は `LLM_BASE_URL=http://localhost:11434/v1`
（Ollama の OpenAI 互換エンドポイント）、`LLM_MODEL_NAME=qwen2.5:32b`（これも
`ollama list` には無い）。`app/config.py:76-77` は `LLM_API_KEY` が空でないことだけを
要求しており（Ollama は認証不要なのでダミー値でよい、というコメントが既存コードにある）、
モデルの実在チェックは行っていない。

---

## 3. Task 3 — ステップサーバ設計

### 3.1 全体方針

現状の `main()`（`run_parallel_simulation.py:1492-1651`）は
「(a) `asyncio.gather` で Twitter/Reddit の**全ラウンド**を最後まで回し切ってから、
(b) `ParallelIPCHandler` を作って wait モードに入る」という一直線の流れになっている。
IPC コマンドのポーリング（`ParallelIPCHandler.process_commands()`, 560-602行目）は
(b) の中でしか呼ばれない。

ステップサーバ化の要点は、(a) を「ブートストラップのみ実行して 0 ラウンドで止める」に変え、
(b) の wait モードを **起動直後から**開始し、ラウンド前進そのものを新コマンド `RUN_ROUNDS(k)` の
ハンドラ内で行う、という順序の入れ替えである。処理系としては単一の asyncio イベントループ・
単一プロセスのままで良い（マルチプロセス化は不要）。

### 3.2 新コマンドのスキーマ

設計時に決めた命名・型は `backend/scripts/experiment/ipc_protocol.py`（新規、Task 4 で作成済み）に
`TypedDict` として実装してある。ここでは IPC 越しの実際の JSON 形状として書き下す。
`platform` は 3値（`"twitter" | "reddit" | "both"`）とし、既存3コマンドの `None` = 両方、という
やや曖昧な慣習より明示的にした。

#### RUN_ROUNDS

Request（`ipc_commands/<uuid>.json`。既存 `IPCCommand.to_dict()` の形をそのまま踏襲）:
```json
{
  "command_id": "b6e2...-uuid",
  "command_type": "run_rounds",
  "args": {
    "platform": "both",
    "rounds": 5
  },
  "timestamp": "2026-09-28T12:00:00"
}
```

Response（成功時）:
```json
{
  "command_id": "b6e2...-uuid",
  "status": "completed",
  "result": {
    "twitter": {
      "rounds_requested": 5, "rounds_executed": 5,
      "round_from": 10, "round_to": 15,
      "simulated_hour": 14, "simulated_day": 2, "total_rounds": 144,
      "actions_this_call": 42, "total_actions": 812, "reached_end": false
    },
    "reddit": {
      "rounds_requested": 5, "rounds_executed": 5,
      "round_from": 10, "round_to": 15,
      "simulated_hour": 14, "simulated_day": 2, "total_rounds": 144,
      "actions_this_call": 31, "total_actions": 590, "reached_end": false
    }
  },
  "error": null,
  "timestamp": "2026-09-28T12:01:27"
}
```

Response（既に別の RUN_ROUNDS / 他のミューテーション系コマンドが進行中の場合。**新設のリジェクト
方針**、§3.5 参照）:
```json
{
  "command_id": "c9f1...-uuid",
  "status": "rejected",
  "result": null,
  "error": "run_rounds already in progress",
  "timestamp": "2026-09-28T12:00:41"
}
```
（`result` 側に `RunRoundsRejection.to_dict()` 形状 = `{"reason", "in_flight_command_id",
"in_flight_since", "retry_hint"}` を入れる案もあるが、上は最小形。実装時にどちらの置き場にするかは
Phase 1 で確定でよい。）

#### INJECT_POST

Request:
```json
{
  "command_id": "...",
  "command_type": "inject_post",
  "args": {
    "agent_id": 12,
    "content": "oTree round 3 results: your group's average contribution was 42 tokens.",
    "platform": "both",
    "write_to_memory": true
  }
}
```
Response:
```json
{
  "status": "completed",
  "result": {
    "agent_id": 12,
    "platforms": {
      "twitter": {"posted": true, "post_id": 913, "round_num": 15, "error": null},
      "reddit":  {"posted": true, "post_id": 44,  "round_num": 15, "error": null}
    },
    "memory_written": true
  },
  "error": null
}
```
`write_to_memory: true` の場合、`graph_memory_updater.GraphMemoryUpdater.add_activity_from_dict`
（`app/services/graph_memory_updater.py:268`）を呼んで Neo4j にも同期する、という拡張点。
これは既存ファイルの改修ではなく**呼び出し側（新規ファイル）が既存 API を使うだけ**なので
リスクは低い。

#### GET_STATE

Request:
```json
{"command_id": "...", "command_type": "get_state", "args": {"platform": "both"}}
```
Response:
```json
{
  "status": "completed",
  "result": {
    "twitter": {"round_num": 15, "total_rounds": 144, "simulated_hour": 14,
                "simulated_day": 2, "total_actions": 812, "last_rowid": 4031,
                "status": "idle"},
    "reddit":  {"round_num": 15, "total_rounds": 144, "simulated_hour": 14,
                "simulated_day": 2, "total_actions": 590, "last_rowid": 2210,
                "status": "idle"},
    "server": {"busy": false, "current_command_type": null,
               "current_command_id": null, "in_flight_since": null}
  }
}
```
`status` は `"initializing" | "idle" | "running_rounds" | "closed"`。

### 3.3 importable なヘルパー vs. トラップされたロジック（実行で確認済み）

`run_parallel_simulation.py` をモジュールとして import した実行結果（§1.3）に基づく一覧:

| 候補 | 行番号 | 実態 |
|---|---|---|
| `create_model` | 984-1035 | **そのまま import 可能**。トップレベル関数。 |
| `get_active_agents_for_round` | 1040-1091 | **そのまま import 可能**。 |
| `fetch_new_actions_from_db` | 657-748 | **そのまま import 可能**。 |
| `load_config` | 604-607 | **そのまま import 可能**。 |
| `get_agent_names_from_config` | 633-655 | **そのまま import 可能**。 |
| `init_logging_for_simulation` | 141-158 | **そのまま import 可能**（ただし呼ぶと `simulation_dir/log` を `shutil.rmtree` するので、ステップサーバから複数回呼ぶ設計にしてはいけない＝起動時1回のみ）。 |
| `ParallelIPCHandler` | 217-602 | **そのまま import 可能**なクラス。`handle_interview`(345-415) / `handle_batch_interview`(416-516) / `_get_interview_result`(517-559) / `process_commands`(560-602) はほぼ無改造で再利用できる。ただし `__init__`(224-245) は env/agent_graph の参照しか持たず、`config` / `agent_names` / `db_path` / ラウンドカーソルを一切持たない（下記トラップ参照）。 |
| `CommandType` | 210-215 | **そのまま import 可能**な定数クラス。ただし `RUN_ROUNDS`/`INJECT_POST`/`GET_STATE` は当然未定義。 |

**トラップされている**（単独では呼べない）ロジック:

1. **環境ブートストラップ**（Twitter: 1122-1157行目、`create_model`→CSV読込→
   `generate_twitter_agent_graph`→DB削除→`oasis.make`→`env.reset()`。Reddit 側も同型の別ブロック）。
   `run_twitter_simulation`/`run_reddit_simulation` の先頭に直書きされていて、関数として切り出されて
   いない。
2. **初期投稿の注入**（1158-1206行目、`event_config.initial_posts` を `ManualAction(CREATE_POST)`
   で投げる部分）。同上、ブートストラップの続きとして直書き。
3. **1ラウンド分の本体**（Twitter: 1228-1273行目 — `get_active_agents_for_round` 呼び出し →
   `action_logger.log_round_start` → 空なら `continue` → `env.step({agent: LLMAction()
   for...})` → `fetch_new_actions_from_db` → `log_action`/`log_round_end`。Reddit 側は
   同型の別ブロック、`run_reddit_simulation` 内）。**これが RUN_ROUNDS(k) の核心であり、
   現状「単独で k 回呼べる関数」が存在しない。**
4. **`main()` の順序そのもの**（1492-1651行目）。前述の通り「全ラウンド実行 →
   `ParallelIPCHandler` 生成 → wait モード」という順序が、ステップサーバでは
   「ブートストラップ → `ParallelIPCHandler`（拡張版）生成 → 即座に wait モード（コマンド待ち）」
   に変わる必要がある。

**重要な事前検証**: `run_experiment_env.py`（§4）のコメントに書いた通り、
`run_twitter_simulation(..., max_rounds=0)` を呼ぶと `total_rounds = min(total_rounds, 0)` に
なり（1220-1222行目）、for ループ本体が一度も実行されないまま「初期化済みの `PlatformSimulation`
（env・agent_graph 込み）」が返る、という **無改造で使える「0ラウンド起動レバー」**が存在する。
これは今回のプロトタイプでは実際に呼び出してはいない（LLM 呼び出し・DB 作成が発生するため
Phase 0-B の「最小限の実行可能チェック」の範囲外と判断した）が、コード読解により確認した
挙動であり、Phase 1 の実装では「1〜3を丸ごと関数化する」のではなく「既存の
`run_twitter_simulation(config, dir, ..., max_rounds=0)` をブートストラップとして流用し、
ラウンド本体（上記3）だけを新規に切り出す」という**最小改修**で済む可能性が高い。

### 3.4 import 時副作用のリスク

`run_parallel_simulation.py` を import すると、`main()` を呼ばなくても以下が**モジュールロード時に
実行される**（実行で確認済み、§1.3 のログの `Loaded environment configuration: ...` 行がその証拠）:

1. `sys.platform == 'win32'` の分岐（29-67行目）: macOS/Linux では skip される。Windows で
   ステップサーバを import する場合、`builtins.open` がグローバルに monkey-patch される点は
   そのまま引き継がれる（新規ファイル側は関知しない、既存の挙動を継承するだけ）。
2. `sys.path.insert(0, _scripts_dir)` / `sys.path.insert(0, _backend_dir)`（89-90行目）:
   グローバルな `sys.path` 変更。`run_experiment_env.py` も自前で同じ2パスを insert しているため
   重複はするが実害はない（`sys.path` に同じパスが2回入るだけ）。
3. `load_dotenv(_env_file)`（95-104行目）: プロジェクトルートの `.env` を読み、
   `os.environ` をグローバルに書き換える。**もしステップサーバ側が先に独自の `.env` や
   別の設定源をロード済みだった場合、ここで上書きされる可能性がある**（`load_dotenv` は
   デフォルトで既存の環境変数を上書きしない `override=False` 相当の挙動だが、未設定の変数は
   ここで初めて入る）。
4. `logging.getLogger().addFilter(MaxTokensWarningFilter())`（117行目）: ルートロガーに
   フィルタを追加。プロセスの生存期間中ずっと残る。複数回 import されても `addFilter` が
   何度も呼ばれるわけではない（Python の import キャッシュにより2回目以降は no-op）。
5. `camel`/`oasis` の import が失敗すると **`except ImportError: ... sys.exit(1)`**
   （145-148行目）が発火する。これは「import 例外を投げる」のではなく **プロセスを
   `SystemExit` で落とす**動作であり、呼び出し側が `try/except ImportError` で守っても
   意味がない（`SystemExit` は `Exception` を継承しない）。ステップサーバ側で
   camel/oasis が入っていないインタプリタから誤って import してしまうと、
   ステップサーバプロセスごと即死する。**これが最大の import リスク**であり、
   起動スクリプト側で「正しい venv の python で起動されているか」を
   `run_parallel_simulation` を import する**前**に検査すべき（例: `sys.executable` のパスに
   `venv311` が含まれるかのチェック、または `oasis`/`camel` を先に素で import してみて
   `SystemExit`/`ImportError` を握りつぶさず早期に分かりやすいエラーメッセージへ変換する）。

総評: 副作用はいずれも「一度きり・冪等・プロセスグローバル」であり、**import すること自体は
安全**（§1.3 で複数回・複数インタプリタで確認済み）。唯一の実質的リスクは (5) の
「誤ったインタプリタから import すると `sys.exit(1)` で無言に近い形でプロセスが落ちる」点。

### 3.5 ラウンドカウンタ・シミュレート時計・action_logger・run_state.json のバースト対応

調査で分かった重要な事実: **`run_state.json` は `run_parallel_simulation.py` 自身は一切書いていない。**
`grep` で確認した通り、書いているのは Flask 側の `app/services/simulation_runner.py`
（`_save_run_state`, 298-310行目付近）であり、これは `_monitor_simulation`
（481行目〜）という**別スレッドが `twitter/actions.jsonl` / `reddit/actions.jsonl` を
tail して**、`{"round": N, "event_type": "round_start"|"round_end", ...}` という
JSON Lines イベントから `current_round` 等を再構築している（`_read_action_log`,
582行目〜、`round_num > state.current_round` の比較でモノトニックに前進させているだけ）。

これは設計上**非常に都合が良い**: `action_logger`（`PlatformActionLogger`,
`backend/scripts/action_logger.py:22-118`）の `log_round_start`/`log_action`/`log_round_end` は
純粋な追記型 JSONL 出力であり、「1回の `for` ループの中で呼ばれた」のか
「`RUN_ROUNDS(k)` が5回呼ばれた結果、合計25ラウンド分が断続的に書かれた」のかを
tail 側は区別しない。**round_num をプロセス内でモノトニックに前進させ続けてさえいれば、
`run_state.json` の再構築ロジックは無改修で動く。**

ここから設計上の要件が3つ出る:
1. **ラウンドカーソルはコマンドハンドラではなくステップサーバ本体（プロセス寿命）が持つ。**
   現在の `run_twitter_simulation`/`run_reddit_simulation` はこれをローカル変数
   （`round_num`, `last_rowid`, `total_actions`）として持っているが、バースト呼び出しの間も
   生存する必要があるため、`ParallelIPCHandler` 拡張クラスのインスタンス属性に昇格させる。
   プロトタイプでは `experiment/round_state.py` の `PlatformRoundState` がこの役目を持つ
   （新規ファイル、§4）。
2. **`action_logger.log_simulation_end()` を呼ぶタイミングを変える。**
   既存コードは「全ラウンドの `for` ループを抜けた直後」（1281行目付近、Twitter）に
   無条件で呼んでいる。ステップサーバでは「全ラウンド終了」に相当する瞬間が
   `RUN_ROUNDS` バーストの終わりとは限らない（オーケストレータがまだ続きを送ってくるかも
   しれない）ので、**`log_simulation_end()` は `CLOSE_ENV` コマンドのハンドラでのみ呼ぶ**
   ように変える必要がある。これを誤ると `simulation_runner.py:692`
   （`_check_all_platforms_completed`、`actions.jsonl` 中の `simulation_end` イベントの
   有無で完了判定している）が**バースト1回ごとに「完了」と誤検知**し、
   Flask 側が実行中のシミュレーションを止まったものとして扱う恐れがある。
   これは Phase 1 実装時に最も踏みやすい地雷としてリスクリスト（§6）にも記載する。
3. **`total_rounds`（時計の終端）は現状 `config["time_config"]` から起動時に1回だけ
   計算される固定値**（1219行目付近、`total_rounds = (total_hours*60)//minutes_per_round`）。
   `RUN_ROUNDS(k)` がこの上限を超えて要求された場合、`rounds_executed < rounds_requested` で
   打ち切り、`reached_end: true` を返す（§3.2 のレスポンス形状に既に用意済み）。

### 3.6 シリアライゼーション設計 ― 「拒否」方式を採用

前提の再確認: oTree ヘッドレス bot ランナーはシリアルなので、主要ユースケースでは
`RUN_ROUNDS`/`INTERVIEW` 系コマンドはそもそも同時に飛んでこない。しかしファイル IPC
（`ipc_commands/*.json`）自体にはロックが無く、実ブラウザセッション（将来のセカンダリケース）や
Flask 側の複数リクエストスレッドからの誤送信では複数コマンドが同時に書き込まれ得る。

**既存コードの `poll_command()`（256-278行目）の挙動**: `commands_dir` 内の `*.json` を
mtime でソートし、**1件だけ**読んで返す。壊れた/書きかけの JSON
（`json.JSONDecodeError`/`OSError`）は `continue` で読み飛ばし、次の poll サイクル
（`main()` 側で 0.5秒間隔）で再試行する。つまり **「同時書き込み中のファイルを読んでしまう」
レースは既に skip-and-retry で許容済み**であり、ここに新たなロックを足す必要はない。

問題は「1コマンドの処理に時間がかかる場合にどうするか」。現行の
`process_commands()`（560-602行目）は **1呼び出しにつき最大1コマンドを完了まで待って**から
返る。`RUN_ROUNDS(k)` の k が大きいと、この1回の処理がLLM呼び出し×kラウンド分
（数十秒〜数分）かかり得る。ここで2つの設計を比較した:

- **案A（キューイング）**: 何もしない。2つ目以降のコマンドファイルはただ
  `ipc_commands/` に積まれ、前のバーストが終わってから mtime 順に処理される。
  実装コストはゼロだが、`SimulationIPCClient.send_command()`
  （`app/services/simulation_ipc.py`）の**デフォルトタイムアウトが60秒**
  （既存コード、変更していない）であるため、長いバーストの後ろに並んだ呼び出し元は
  サーバ側が正常に処理を続けているにもかかわらず `TimeoutError` を受け取ってしまう。
  呼び出し元から見て「失敗したのか、まだ処理中なのか」が区別できない。
- **案B（即時リジェクト、採用）**: サーバ側にプロセス内の `busy` フラグ
  （`experiment/round_state.py` の `ServerBusyState`、あるいはプロトタイプの
  `asyncio.Lock`）を持たせる。`RUN_ROUNDS` のようなミューテーション系コマンドが
  実行中に、別のミューテーション系コマンド（`RUN_ROUNDS`/`INTERVIEW`/
  `BATCH_INTERVIEW`/`INJECT_POST`/`CLOSE_ENV`）が届いたら、**キューに積まずに
  即座に `status: "rejected"` を返す**（§3.2 のレスポンス例）。
  呼び出し元は「今は無理、`retry_hint` に従って `GET_STATE` をポーリングするか
  リトライせよ」と明確に分かる。`GET_STATE` だけは `busy` に関わらず**常に即答**する
  （env に触らず、プロセス内のカーソル/フラグを読むだけの安全な read-only 操作のため）。

  さらに、プロセスが `RUN_ROUNDS` の途中でクラッシュ/kill された場合に備え、
  `experiment/round_state.py::RunRoundsLock` として**ファイルベースのロック**
  （`run_rounds.lock`、burst 開始時に書き、終了時に削除、`pid`/`started_at` を含む）
  も用意した。プロセス内 `asyncio.Lock` だけだと、再起動後の新プロセスは
  「前のバーストが応答不能のまま宙に浮いている」ことに気づけないため、
  この lock ファイルの mtime/pid から staleness を判定できるようにしている
  （`is_stale()`、120秒をデフォルト閾値としたが、Ollama の実応答時間次第で
  要調整とコメント済み）。

  RUN_ROUNDS 自体の内部でも、k ラウンドを1本の `await` で塊にせず、
  **1ラウンドごとに `commands_dir` 内に `close_env` が届いていないか軽くチェックして
  早期終了できる**（`stop_on_close` 相当の協調的キャンセル）設計にすべき、というのが
  今回の結論。バーストが数分かかっても `CLOSE_ENV` の反映が最大1ラウンド分の遅延で
  済むようにする。

まとめると: **同時実行そのものは asyncio シングルループにより構造的に起こり得ない
（1コマンドの処理が完全に終わるまで次のコマンドの処理は始まらない）。設計すべきは
「順番を待たせる」か「即座に断って再試行させる」かの選択であり、本設計では
後者（案B）を採用する。** これによりクライアント側のタイムアウト値を今の60秒から
変える必要がなくなる、という副次的な利点もある（バーストが終わっていなければ
即座に reject が返るので、60秒も待たされることがない）。

### 3.7 ファイル別変更一覧

**既存ファイル（編集していない。ここでは必要な改修を記述するのみ）:**

| ファイル | 変更内容（未適用） |
|---|---|
| `backend/scripts/run_parallel_simulation.py:210-215` | `CommandType` に `RUN_ROUNDS = "run_rounds"`, `INJECT_POST = "inject_post"`, `GET_STATE = "get_state"` を追加。 |
| 同ファイル `:1122-1157`（Twitter）/ Reddit 相当ブロック | ブートストラップ部分を `bootstrap_twitter_environment(config, simulation_dir, action_logger, main_logger) -> PlatformSimulation` として関数抽出（§3.3 トラップ1・2）。 |
| 同ファイル `:1228-1273`（Twitter）/ Reddit 相当ブロック | 1ラウンド分の本体を `async def step_twitter_round(result, config, agent_names, db_path, cursor, action_logger) -> None`（`cursor` は `PlatformRoundState` 的な可変オブジェクト）として関数抽出（§3.3 トラップ3・これが refactor R1 の中心）。 |
| 同ファイル `:224-245`（`ParallelIPCHandler.__init__`） | `config` / `agent_names` / `db_path` / 各プラットフォームの `PlatformRoundState` / `ServerBusyState` を保持できるようコンストラクタ引数を拡張。 |
| 同ファイル `:560-602`（`process_commands`） | `CommandType.RUN_ROUNDS`/`INJECT_POST`/`GET_STATE` の分岐を追加し、対応する新規ハンドラ（`handle_run_rounds`/`handle_inject_post`/`handle_get_state`）を呼ぶ。 |
| 同ファイル `:1492-1651`（`main()`） | 実行順序を「全ラウンド実行 → `ParallelIPCHandler` 生成 → wait」から「ブートストラップ（0ラウンド）→ 拡張 `ParallelIPCHandler` 生成 → 即 wait（コマンド駆動でラウンド前進）」に変更。後方互換のため `--max-rounds` が指定された場合のみ従来の自動連続実行にフォールバックする、という互換フラグを検討。 |
| `backend/app/services/simulation_ipc.py:23-27`（`CommandType` Enum） | スクリプト側と同じ3値を追加。 |
| 同ファイル `SimulationIPCClient` クラス | `send_run_rounds(k, platform, timeout)` / `send_inject_post(...)` を追加。`send_run_rounds` は「即時reject方式」（§3.6）採用によりデフォルトタイムアウトは既存の60秒のままで問題ない想定。`get_state` は待ち行列を経由せず `env_status.json` 直読みへのショートカットを別途用意することを推奨（後述）。 |
| `backend/app/services/simulation_runner.py:692`（`_check_all_platforms_completed`） | ロジック自体は変更不要（`simulation_end` イベントの有無を見るだけ）。ただし §3.5 の通り、`log_simulation_end()` の発火タイミングをスクリプト側で正しく変えないと、ここが誤動作する、という**依存関係の注意**として記載。 |

**新規ファイル（作成済み。§4 で詳細）:**

- `backend/scripts/run_experiment_env.py`
- `backend/scripts/experiment/__init__.py`
- `backend/scripts/experiment/ipc_protocol.py`
- `backend/scripts/experiment/round_state.py`
- `backend/scripts/experiment/step_server_handler.py`（本セッションで追加。§4.4 参照）

---

## 4. Task 4 — プロトタイプの状態

### 4.1 経緯についての注記

作業開始時点で `backend/scripts/experiment/`（`__init__.py`, `ipc_protocol.py`,
`round_state.py`）と `backend/scripts/run_experiment_env.py` が**既に存在していた**
（mtime はすべて本セッション内）。中身を確認したところ、本書がこれから書こうとしていた設計
（§3.6 の「即時リジェクト＋クラッシュセーフなロックファイル」方式、§3.3 の
importable/trapped 分類、§3.5 のラウンドカーソル分離）と**独立に同じ結論に達しており**、
コード品質・コメントの正確さも高かったため、**書き直さずにそのまま採用し、実行検証のみ
追加で行った**（§1.3・下記4.2）。これは本セッションの前段（同一タスクの以前の試行）による
成果物と判断している。

### 4.2 実行検証の結果（実際に実行した。コード読解ではない）

```
$ backend/venv311/bin/python backend/scripts/run_experiment_env.py \
    --config backend/uploads/simulations/sim_f2feaaf349ea/simulation_config.json
Loaded environment configuration: /Users/.../.env
Traceback (most recent call last):
  ...
  File ".../run_experiment_env.py", line 146, in init_platforms
    raise NotImplementedError(
NotImplementedError: init_platforms() documents a working zero-refactor lever
(max_rounds=0) but does not call it here: doing so would start a real OASIS
environment (LLM calls, sqlite db creation under self.cfg.simulation_dir),
which Phase 0-B's 'smallest runnable check' scope for THIS file does not
include. ...

$ backend/.venv/bin/python backend/scripts/run_experiment_env.py \
    --config backend/uploads/simulations/sim_f2feaaf349ea/simulation_config.json
（3.11 と完全に同一のトレースバック・メッセージ）
```

これは「import が通る」より強い検証で、実際に: (1) `run_parallel_simulation` の
フル import（camel/oasis 込み）、(2) `experiment.ipc_protocol` / `experiment.round_state`
の import、(3) 実在の config ファイルの `json.load`、(4) `PlatformRoundState` への
`total_rounds`/`minutes_per_round` の設定、までが両インタプリタで完走し、
その先（実際に OASIS 環境を起動して LLM を呼ぶ部分）だけが**意図的に**
`NotImplementedError` で止まる設計になっていることを確認した。

### 4.3 現状の実装状況

| コンポーネント | 状態 |
|---|---|
| `experiment/ipc_protocol.py` | RUN_ROUNDS/INJECT_POST/GET_STATE の `TypedDict` 定義、`RunRoundsRejection` dataclass。stdlib のみ、camel/oasis/Flask 非依存で実装済み。 |
| `experiment/round_state.py` | `PlatformRoundState`（§3.5 のラウンドカーソル）、`RunRoundsLock`（§3.6 のクラッシュセーフロック）を実装済み。stdlib のみ。 |
| `run_experiment_env.py :: ExperimentStepServer.get_state()` | **実体あり**。ローカルの `PlatformRoundState`/lock 状態を読むだけなので env 未起動でも動く。 |
| `run_experiment_env.py :: ExperimentStepServer.inject_post()` | **実体あり**（`ManualAction(ActionType.CREATE_POST, ...)` を呼ぶ実コード）。ただし `self.twitter`/`self.reddit` が `None` のまま（`init_platforms()` が未実装で止まるため）、実際に叩いてもまだ何も投稿されない。 |
| `run_experiment_env.py :: ExperimentStepServer.run_rounds()` | **未実装（意図的）**。refactor R1（§3.3 トラップ3、既存ファイル側の関数抽出）が無いと実装できないことをそのまま `NotImplementedError` として明示。 |
| `run_experiment_env.py :: ExperimentStepServer.init_platforms()` | 設定ロードとラウンド状態の初期化までは実装。実際の OASIS 環境起動（LLM呼び出し発生）はコメントで「`max_rounds=0` レバーで可能」と明記した上で、Phase 0-B のスコープ外として意図的に呼んでいない。 |

つまり **「skeleton that imports cleanly and passes an import check」という要求水準を
超えて、実在の config に対する実行まで確認済み**。一方で `run_rounds()` は
既存ファイルを一切変更しないという制約の下では原理的に実装不可能であり、
その理由をコード内コメントとしても本書としても明記した。

### 4.4 追加ファイル: `experiment/step_server_handler.py`（本セッションで追加）

§4.1〜4.3 の `ExperimentStepServer`（`run_experiment_env.py`）は `run_rounds()` を
`NotImplementedError` のまま残す、という「動くふりをしない」方針を取っている。これとは
別に、**「既存ファイルを一切変更しない制約の中でも RUN_ROUNDS を実際に動かすと何が
起きるか」を示す目的で**、`backend/scripts/experiment/step_server_handler.py` を追加した。

この `ExperimentIPCHandler` は（`ExperimentStepServer` が composition なのとは対照的に）
`rps.ParallelIPCHandler` を**継承**し、`handle_interview`/`handle_batch_interview`/
`poll_command`/`send_response`/`update_status` をそのまま再利用しつつ、
`handle_run_rounds`/`handle_inject_post`/`handle_get_state` を実装する。`run_rounds` は
`NotImplementedError` にはせず、`run_parallel_simulation.py:1228-1279` のラウンドループ
本体を**並行して再実装したコード**で実際に k ラウンド分の `env.step()` を回す。
モジュール docstring 内にも明記した通り、これは refactor R1（§3.3・§3.7）の代替では
なく、「既存ループの変更に手動で追従しないと壊れる」という §6 のリスクをそのまま
体現する暫定実装である。`process_commands()` を drain-all 方式（1 poll で溜まっている
コマンドを全て処理してから抜ける。既存の「1 tick = 最大1コマンド」実装との差分は
§3.6 の遅延議論と同じ論点）にオーバーライドしている点も既存2案（案A/案B）とは別の
第3の緩和策として記録しておく。

検証: `backend/venv311/bin/python -c "import experiment.step_server_handler as ssh; ..."`
（`backend/scripts/` から）で import 確認済み。`run_experiment_env.py` の `_main()` は
現状まだ `ExperimentStepServer`（`NotImplementedError` 版）を使っており、
`ExperimentIPCHandler`（動く版）には**配線されていない**。どちらを最終的に採用するかは
本書では一方的に決め打ちせず、次フェーズの判断事項として残す:
(a) `run_experiment_env.py` を `ExperimentIPCHandler` に差し替え、再実装リスクを
受け入れて先に進む、(b) refactor R1 を実際に適用してから
`ExperimentIPCHandler._run_rounds_one_platform` を実抽出後の関数呼び出しに置き換え、
それから配線する。既存ファイルを変更せずに済む代替実装が並行して2つ存在する状態を、
どちらか一方の上書きで解消しなかった理由でもある。

---

## 5. Task 1・2 で使った検証コマンドの一覧（再掲・付録）

- `backend/.venv/bin/python --version` / `backend/venv311/bin/python --version`
- `backend/.venv/bin/python -c "from importlib.metadata import version; ..."`
- `backend/.venv/bin/python -c "import oasis, camel; print(oasis.__file__); print(camel.__file__)"`
- `backend/venv311/bin/python -c "import sys; sys.path.insert(0,'backend/scripts'); import run_parallel_simulation as rps; ..."`
- `backend/venv311/bin/python backend/scripts/run_experiment_env.py --config <実在config>`
- `backend/.venv/bin/python backend/scripts/run_experiment_env.py --config <同config>`
- `grep -n "camel" backend/pyproject.toml`
- `grep -n "run_state" backend/scripts/*.py backend/app/services/*.py`
- Task 2 固有のコマンド（`sqlite3 -readonly`, `ollama list` 等）は §2 に記載。

---

## 6. リスク一覧

優先度が高い順（≒ 一番踏みやすい／一番気づきにくい順）:

1. **`log_simulation_end()` の呼び出しタイミングを間違えると `simulation_runner.py:692`
   の完了判定が誤爆する**（§3.5）。ステップサーバ化の refactor で最も見落としやすい。
   既存の「全ラウンド後に1回だけ呼ぶ」コードをそのまま `step_twitter_round` に
   コピーしてしまうと、`RUN_ROUNDS` を呼ぶたびに「シミュレーション終了」と
   Flask 側に誤通知することになる。
2. **`sys.executable` 依存**（`simulation_runner.py:440` 付近）。Flask プロセスの
   インタプリタがそのまま子プロセスに継承されるため、Flask を 3.12 の `.venv` で
   動かしている限り、ステップサーバも 3.12 で起動されてしまう。§1 の通り 3.12 でも
   動くことは確認したが、意図せず venv が食い違う設定ミスのリスクは残る。
   明示的に `venv311/bin/python` のフルパスを渡すよう改修すべき。
3. **RUN_ROUNDS の途中キャンセル**。§3.6 で「1ラウンドごとに `close_env` の有無を
   チェックする協調的キャンセル」を提案したが、これを実装し忘れると、大きな k の
   `RUN_ROUNDS` 実行中は `CLOSE_ENV` も含めた**一切のコマンドが数分単位でブロックされる**。
   oTree 側のバリア設計（decision-prefetch でまとめて呼ぶ）と組み合わさると、
   「重い RUN_ROUNDS の後ろに oTree のバリア待ちが並ぶ」という多段待ちが発生しうる。
4. **`env.step()` が Twitter の `sandbox_clock.time_step` を呼び出し理由を問わず
   無条件にインクリメントする**（§2.1、`oasis/environment/env.py:196-197` を実ソースで確認）。
   `RUN_ROUNDS` バーストの合間に `INTERVIEW`/`INJECT_POST` を挟む（＝追加の `env.step()`
   呼び出しを行う）と、外部の `round_num` カーソルとは無関係にこの内部時計だけが進み、
   静かにズレていく。Reddit 側には対応するインクリメントが無いため Twitter 固有の問題。
   実害の有無（投稿の内部タイムスタンプやレコメンドシステムへの影響）は未調査で、
   Phase 1 で `INJECT_POST`/`INTERVIEW` と `RUN_ROUNDS` を混在させる前に検証すべき。
5. **`init_logging_for_simulation` の再入不可**（§3.3）。`shutil.rmtree` で
   `simulation_dir/log` を削除するので、ステップサーバの起動シーケンスで
   誤って複数回呼ぶと、実行中に生成されたログが消える。1プロセス1回だけ呼ぶ、
   という制約をコメント以上の形（アサーションなど）で保証すべき。
6. **`ImportError` → `sys.exit(1)`**（§3.4 (5)）。誤ったインタプリタからの import は
   例外ではなくプロセス終了として現れるため、監視側が「クラッシュ」と
   「起動時の設定ミス」を区別しにくい。起動スクリプト側で事前チェックを入れることを推奨。
7. **`.env` の `LLM_MODEL_NAME` が `ollama list` に存在しない**（§2.4、実行で確認:
   `.env` は `pdurugyan/qwen3.5-9b-deepseek-v4-flash-Q4_K_M`、`app/config.py:33` の
   デフォルトは `qwen2.5:32b` だが、実際に pull 済みなのは `qwen3:4b` / `bge-m3` /
   `gemma4:12b` / `gemma4:e4b` / `nomic-embed-text` のみ）。今の設定のまま
   `create_model()` 経由でシミュレーションを起動すると、モデル未 pull が原因で
   失敗する可能性が高い。ステップサーバの本実装に入る前に
   `ollama pull pdurugyan/qwen3.5-9b-deepseek-v4-flash-Q4_K_M` あるいは `.env` の
   修正のどちらかが必要（本タスクでは `.env` は未編集）。
8. **`.env` の二重ロード順序**（§3.4 (3)）。ステップサーバ側の起動スクリプトが
   独自に環境変数を設定してから `run_parallel_simulation` を import すると、
   後から読まれる `.env` の内容で意図せず上書きされる可能性がある
   （逆に「未設定の変数だけ埋める」という `python-dotenv` のデフォルト挙動に
   助けられている面もある。要実機確認）。
9. **oTree ヘッドレス実行の逐次性を前提にした reject 方式（§3.6）は、実ブラウザ
   セッション（同時多群アクセス）では体験を悪化させる**。複数グループが同時に
   `RUN_ROUNDS` を要求すると、後発グループは reject を受けてポーリング＋リトライを
   繰り返すことになり、UI 側にリトライ/バックオフの実装が要る。Phase 2 以降で
   ライブブラウザ対応をする際に再設計が必要になる可能性がある、と当初から
   ドキュメント化しておく。

---

## 7. 未確定事項（Phase 1 に持ち越し）

- `RUN_ROUNDS` の reject 方式（案B）と、`GET_STATE` の queue バイパス（`env_status.json`
  直読み）を実際にどこまで実装するかは、Phase 1 で oTree 側のバリア呼び出し頻度が
  固まってから決めたい（呼び出し間隔が長いなら案A＝素朴なキューイングでも十分な可能性がある）。
- `--max-rounds` 経由の「従来の自動連続実行」モードを残すかどうか（後方互換 vs. 実装の単純さ）。
- `write_to_memory`（INJECT_POST → Neo4j 書き戻し）を同期で呼ぶか、既存の
  `graph_memory_updater` の非同期パスに乗せるかは `graph_memory_updater.py` 側の
  API（`GraphMemoryManager.create_updater` 等）をもう一段読み込んでから決める。
