# Phase 0-A: oTree 6.0.15 実機検証（Sonnet 委譲分）

作成日: 2026-09-28
検証環境: `/Users/petadimensionlab/workspace/research/MiroFish-oTree/.venv`（otree **6.0.15**, Python **3.12.12**）
プロジェクト: `/Users/petadimensionlab/workspace/research/MiroFish-oTree/mirofish_otree_test/`

本ドキュメントは `plan.md` / `tasks.md` の Phase 0 で Sonnet に委譲された残り5項目（`participant.label`、REST API、`live_method` + bot、非同期フック、未捕捉例外時の挙動）を**実機実行で検証**し、既に確立済みの F1–F6 と合わせてまとめたもの。**docs 以下では本ファイルのみを変更しており、`plan.md` / `tasks.md` / `phase0-mirofish.md` には一切触れていない。**

## 0. サマリーテーブル

| # | 項目 | 判定 | 一言 |
|---|------|------|------|
| F1 | Headless bot runner CLI (`otree test ... --export`) | **verified**（既存） | ブラウザなしで完走、CSV export も動作 |
| F2 | ラウンドごとのバリア (`WaitPage.after_all_players_arrive`) | **verified**（既存） | グループ全員提出後に1回だけ発火、`group.get_players()`で全員の値が読める |
| F3 | `play_round` 内でのブロッキング HTTP | **verified**（既存、ログは上書き済みなので設問文から引用のみ） | 8回とも成功、サーバの返り値が実際に提出される |
| F4 | bot runner は完全にシリアル（並行性なし） | **verified**（既存） | 全グループ・全参加者がページ単位でラウンドロビン、リクエストは重複しない |
| F5 | HTTP タイムアウトでもラン全体は落ちない（ただし bot 側の try/except に依存） | **verified**（既存） | `ReadTimeout` は bot が自分で catch → `contribution=50` にフォールバック → ラン完走 |
| F6 | CSV export の列定義。`participant.label` は空 | **verified**（既存） | `mf_group.csv` で label 列が空だった原因は本ドキュメントの 1. で特定 |
| 1(a) | REST `POST /api/sessions/` にラベルリストを渡す | **DIFFERENT from expectation** | `create_session()` にそのようなキーワード引数は存在せず、403 Forbidden で拒否される |
| 1(b) | rooms + `participant_label_file` | **DIFFERENT from expectation**（ソースコード読解による、実行未検証。理由は本文参照） | `otree test` は `create_session()` を `room_name` なしで直接呼ぶため、rooms 機構自体に到達しない |
| 1(c) | `creating_session()` でラベル付与 | **DIFFERENT from expectation → 最終的に verified** | `Subsession` クラスの**メソッドとして**定義すると黙って無視される（is_noself 仕様）。`__init__.py` の**モジュールレベル関数**として定義すると動作する |
| 2 | REST API 6エンドポイント + 認証仕様 | **verified** | 全エンドポイント成功。認証ヘッダ名は `otree-rest-key`、`OTREE_AUTH_LEVEL` が `DEMO`/`STUDY` の時のみ強制される（デフォルトは無認証） |
| 3 | `live_method` + `otree test` | **DIFFERENT from expectation → verified** | bot がライブページを yield するだけでは `live_method` は一切呼ばれない。`tests.py` に `call_live_method` フックを明示的に定義し、かつ返ってくる async generator を自前で drive しないと動かない |
| 4 | `async def live_method` サポート | **verified** | async **generator**（`async def` + `yield`）はサポートされ実行できる。プレーンな coroutine（`async def` で `yield` なし）は `LiveMethodBadReturnValue` で明示的に拒否される |
| 4b | `live_method` 以外のページライフサイクルフックの非同期対応 | **verified（否定的結果）** | `async def vars_for_template` を試したところ、コルーチンが await されずテンプレートに渡り `Exception: vars_for_template did not return a dict` で全体が exit 1。`live_method` 以外は非同期非対応 |
| 5 | `play_round` 内の未捕捉例外 | **verified** | プロセス全体が traceback を出して exit code 1 で異常終了する（F5 のタイムアウト+catch とは対照的） |
| — | （副産物）`otree resetdb` 直後に `otree test`/`devserver` が失敗する | **verified** | `resetdb` はディスク上の sqlite ファイルに `PRAGMA user_version` を書き込まないため、次のプロセス起動時に「oTree has been updated」で exit 1 になる。回避策あり（本文参照） |

---

## 1. `participant.label` の検証

### 1(a) REST `POST /api/sessions/` にラベルリストを渡す — 失敗（403）

`otree/views/rest.py` の `RESTSessions.post()` は `otree.session.create_session(**kwargs)` をそのまま呼ぶだけで、`create_session()` のシグネチャ（`otree/session.py:240`）を読むと

```python
def create_session(
    session_config_name, *, num_participants,
    progress_message_fxn=..., label='', room_name=None,
    is_mturk=False, is_demo=False, modified_session_config_fields=None,
) -> Session:
```

`participant_labels` や `participant_label_file` に相当する引数が存在しない。実機で確認:

```
curl -s -i -X POST -H "otree-rest-key: testkey123" -H "Content-Type: application/json" \
  -d '{"session_config_name":"label_test","num_participants":3,"participant_labels":["mf_7","mf_8","mf_9"]}' \
  http://127.0.0.1:8123/api/sessions
```

```
HTTP/1.1 403 Forbidden
content-length: 72

create_session() got an unexpected keyword argument 'participant_labels'
```

`otree/views/cbv.py` の `inner_dispatch()` は `TypeError` を捕まえて 403 に変換する実装になっているため、「未対応のキーワード引数を渡す」と一律 403 になる。**REST 経由でセッション作成時に participant label を指定する方法は 6.0.15 には存在しない。**

### 1(b) rooms + `participant_label_file` — 構造的に到達不可能（ソース読解、未実行）

`otree/bots/runner.py` の `run_all_bots_for_session_config()` を読むと:

```python
session = otree.session.create_session(
    session_config_name=config_name,
    num_participants=(num_participants or config['num_demo_participants']),
)
```

`room_name` を一切渡していない。一方 `otree/room.py` の `LabelRoom.get_participant_labels()` はブラウザが room の URL に `?participant_label=xxx` 付きでアクセスした時にラベルを割り当てる仕組み（`get_participant_urls()` 参照）であり、そもそも `otree test` の headless CLI bot ランナー（`otree/bots/bot.py` の `ParticipantBot.open_start_url()` は `participant_start_url()` を直接叩く。room 経由の入室 URL ではない）はこの経路を通らない。

**これは実行して確認した事実ではなく、`run_all_bots_for_session_config` と `room.py` のソースコードを読んで導いた結論であることを明記する（未実行 = unverified by execution、ただしコードパスが機械的に存在しないことは確認済み）。** rooms は「人間参加者がブラウザで入室する」シナリオ専用の仕組みであり、bot 駆動の headless run には使えない、という設計上の帰結。

### 1(c) `creating_session()` でラベル付与 — 動くが「クラスメソッドでは黙って無視される」

最初、`label_test/__init__.py` は次のように書かれていた（前任エージェントが用意したスケルトン）:

```python
class Subsession(BaseSubsession):
    def creating_session(subsession):
        if subsession.round_number == 1:
            for i, p in enumerate(subsession.get_players()):
                p.participant.label = f"mf_{i}"
```

これで `otree test label_test 3 --export ./export_label` を実行すると、**エラーなく完走するが CSV の `participant.label` 列が空のまま**だった:

```
participant.id_in_session,participant.code,participant.label,...
1,dh5m9pd9,,1,...
2,afveiui2,,1,...
3,rw1fk3b0,,1,...
```

原因を `otree/database.py` で特定: `AnyModel` の `is_noself = True`（`database.py:645`）がデフォルトであり、`get_user_defined_target()`（`database.py:706`）は

```python
@classmethod
def get_user_defined_target(cls):
    if cls.is_noself:
        app_name = cls.get_folder_name()
        return get_models_module(app_name)
    return cls
```

`is_noself` が True の場合、ターゲットは「`Subsession` クラス自身」ではなく**`__init__.py` モジュール**になる。そして `otree/session.py` の `run_creating_session_functions()` は

```python
target = models_module.Subsession.get_user_defined_target()  # → モジュール
func = getattr(target, 'creating_session', None)              # モジュールレベルの関数を探す
```

つまり `creating_session` は **`__init__.py` のトップレベル関数**として定義しないと `getattr` で見つからず、**エラーも警告も出さずに黙ってスキップされる**。`Subsession` クラスの中にメソッドとして書く（多くの oTree チュートリアルでよく見る書き方だが、それは旧来の self-style API）と 6.0.15 の noself API では効かない。

デバッグプリントを仕込んで実証（`label_test/__init__.py` を module-level 関数に修正後）:

```python
class Subsession(BaseSubsession):
    pass

def creating_session(subsession):
    ...
    for i, p in enumerate(players):
        p.participant.label = f"mf_{i}"
```

```
$ otree test label_test 3 --export ./export_label3
Creating 'label_test' session (test case 0)
DEBUG creating_session round=1 num_players=3
DEBUG set label mf_0 on participant code=i8u8ddhu readback=mf_0
DEBUG set label mf_1 on participant code=qmucbmad readback=mf_1
DEBUG set label mf_2 on participant code=vfj8zp74 readback=mf_2
...
Bots completed session
Exported CSV to folder "./export_label3"
```

```
$ cat export_label3/label_test.csv
participant.id_in_session,participant.code,participant.label,...
1,i8u8ddhu,mf_0,1,...
2,qmucbmad,mf_1,1,...
3,vfj8zp74,mf_2,1,...
```

**label が CSV に乗ることを確認**（`/Users/petadimensionlab/workspace/research/MiroFish-oTree/mirofish_otree_test/export_label3/label_test.csv`）。

さらに REST 経由でセッションを作った場合にも同じ `creating_session` フックが自動的に走ることを確認（`GET /api/sessions/{code}` のレスポンス、2章参照）— `participants[].label` が `mf_0`/`mf_1`/`mf_2` になっていた。

**結論: `mf_{agent_id}` のような join key を付けたいなら、(c) の module-level `creating_session()` 一択。REST も rooms もこの用途には使えない。**

---

## 2. REST API の実機検証

起動コマンド:

```
export OTREE_REST_KEY="testkey123"
export OTREE_AUTH_LEVEL="DEMO"
otree devserver 8123
```

### 認証仕様（`otree/views/cbv.py:189-212`, 実機確認）

- ヘッダ名は環境変数名 `OTREE_REST_KEY` ではなく **`otree-rest-key`**（小文字・ハイフン区切り）。
- `settings.AUTH_LEVEL`（環境変数 `OTREE_AUTH_LEVEL`）が `'DEMO'` か `'STUDY'` の時だけ検証が走る。**デフォルト（未設定）では REST API は無認証で誰でも叩ける。**

```
$ curl -s -i http://127.0.0.1:8123/api/otree_version
HTTP/1.1 403 Forbidden
HTTP Request Header otree-rest-key is missing
```

（`OTREE_AUTH_LEVEL=DEMO` を設定した状態でヘッダなしアクセスした結果）

### 各エンドポイント（`otree-rest-key: testkey123` 付き）

**`GET /api/otree_version`**
```
$ curl -s -i -H "otree-rest-key: testkey123" http://127.0.0.1:8123/api/otree_version
HTTP/1.1 200 OK
{"version":"6.0.15"}
```
（末尾スラッシュ付き `/api/otree_version/` は 307 リダイレクトになるので注意）

**`GET /api/session_configs`**
```
$ curl -s -H "otree-rest-key: testkey123" http://127.0.0.1:8123/api/session_configs
[{"...","name": "mf_group",...},{"...","name": "label_test",...},{"...","name": "live_test",...}]
```
（`SESSION_CONFIGS` に登録した 3 つの config が全部返る）

**`POST /api/sessions`（正常系）**
```
$ curl -s -i -X POST -H "otree-rest-key: testkey123" -H "Content-Type: application/json" \
  -d '{"session_config_name":"label_test","num_participants":3}' http://127.0.0.1:8123/api/sessions
HTTP/1.1 200 OK
{"code":"5ju6yqrr","session_wide_url":"http://127.0.0.1:8123/join/vemoreke","admin_url":"http://127.0.0.1:8123/SessionStartLinks/5ju6yqrr"}
```

**`GET /api/sessions/{code}`**（POST semantics だが `GET` で `request.body` を渡す実装）
```
$ curl -s -X GET -H "otree-rest-key: testkey123" -H "Content-Type: application/json" -d '{}' \
  http://127.0.0.1:8123/api/sessions/5ju6yqrr
{
  "code": "5ju6yqrr", "num_participants": 3, "config_name": "label_test",
  "participants": [
    {"id_in_session": 1, "code": "c4773ofm", "label": "mf_0", ...},
    {"id_in_session": 2, "code": "k92jy135", "label": "mf_1", ...},
    {"id_in_session": 3, "code": "b2u7lb4o", "label": "mf_2", ...}
  ]
}
```
（1(c) の module-level `creating_session` が REST 経由でも自動的に発火し、label が最初から付いていることを確認）

**`POST /api/session_vars/{code}`**
```
$ curl -s -i -X POST -H "otree-rest-key: testkey123" -H "Content-Type: application/json" \
  -d '{"vars":{"debate_topic":"public_goods_round1"}}' http://127.0.0.1:8123/api/session_vars/5ju6yqrr
HTTP/1.1 200 OK
{}
```

**`POST /api/participant_vars/{code}`**
```
$ curl -s -i -X POST -H "otree-rest-key: testkey123" -H "Content-Type: application/json" \
  -d '{"vars":{"mirofish_agent_id":"agent_042"}}' http://127.0.0.1:8123/api/participant_vars/c4773ofm
HTTP/1.1 200 OK
{}
```

書き込み確認（`GET /api/sessions/{code}` に `session_vars`/`participant_vars` フィルタを渡す）:
```
$ curl -s -X GET -H "otree-rest-key: testkey123" -d '{"session_vars":["debate_topic"],"participant_vars":["mirofish_agent_id"]}' \
  http://127.0.0.1:8123/api/sessions/5ju6yqrr
...
"mirofish_agent_id": "agent_042"
...
"debate_topic": "public_goods_round1"
```

両方とも書き込み・読み出しともに正常動作。これは Phase 2/3 の bridge から「セッション単位の議論トピック」「参加者単位のエージェントID・ステータス」を出し入れするチャネルとして使える。

devserver は検証終了後に確実に kill 済み（`kill -9`、`lsof -i :8123` で空を確認）。

---

## 3. `live_method` + bot の検証

`live_test` を `SESSION_CONFIGS` に登録し、`otree test live_test 2` を実行。

### 3-1. 何もしないと呼ばれない

素の `tests.py`（`yield LivePage` するだけ、`call_live_method` フックなし）で実行すると:

```
$ otree test live_test 2 --export ./export_live1
Submit /p/1y1oi7gu/live_test/LivePage/1
Submit /p/m3n0eoli/live_test/LivePage/1
Bots completed session
```

CSV:
```
player.live_method_called,player.got_value
0,
0,
```

**bot がライブページを `yield` するだけでは `live_method` は一度も呼ばれない。**

### 3-2. `call_live_method` フックを追加しても素朴な実装は無効

`otree/bots/bot.py` の `live_method_stuff()` を読むと、`tests.py` に `call_live_method(method, **kwargs)` というモジュールレベル関数が定義されている場合だけ、その関数に `method` コールバックを渡して呼び出す:

```python
method_calls_fn = getattr(bots_module, 'call_live_method', None)
if method_calls_fn:
    ...
    method_calls_fn(method=method, case=..., round_number=..., page_class=PageClass, group=...)
```

素朴に `method(player.id_in_group, {"value": 99})` を同期的に呼ぶだけの実装を書いて試したところ、`live_method_called` は **やはり 0 のまま**だった。原因: `method` の実体（`bot.py`内）は

```python
def method(id_in_group, data):
    return call_live_method_compat(live_method, players[id_in_group], data)
```

であり、`call_live_method_compat`（`otree/live.py:132`）は**常に async generator 関数**（内部で `else:` 分岐でも `yield result` している）なので、`method(...)` を呼んだだけでは async generator オブジェクトが生成されるだけで中身は一切実行されない。`asyncio.run()` + `async for` で明示的に drive して初めて実際に走る:

```python
def call_live_method(method, *, page_class, group, **kwargs):
    import asyncio
    async def drive():
        for player in group.get_players():
            async for _ in method(player.id_in_group, {"value": 99}):
                pass
    asyncio.run(drive())
```

これで:
```
$ otree test live_test 2 --export ./export_live2  # (naive, no asyncio.run) → still 0
$ otree test live_test 2 --export ./export_live3  # (fixed, asyncio.run + async for)
player.live_method_called,player.got_value
1,99
1,99
```

**結論: `otree test` で `live_method` を検証したいなら、(1) `tests.py` に `call_live_method` を定義し、(2) 渡される `method` の戻り値が async generator であることを理解した上で自前で drive する必要がある。何もしなければ `live_method` は完全にスキップされる（エラーも警告も出ない）。**

証跡: `/Users/petadimensionlab/workspace/research/MiroFish-oTree/mirofish_otree_test/export_live1/live_test.csv`（0のケース）、`export_live3/live_test.csv`（1のケース、`live_test/tests.py` に実装あり）。

---

## 4. 非同期フックの検証

`live_test` に `AsyncGenLivePage` / `AsyncDefLivePage` を追加し、`Player.asyncgen_live_method_called` / `asyncdef_live_method_attempted` フィールドで検証。

### 4-1. `async def` + `yield`（async generator）— サポートされる

```python
class AsyncGenLivePage(Page):
    @staticmethod
    async def live_method(player: Player, data):
        player.asyncgen_live_method_called = True
        yield {player.id_in_group: dict(echo=data)}
```

3-2 と同じ drive パターンで呼び出したところ、正常に動作:
```
Submit .../live_test/AsyncGenLivePage/2
```
CSV: `player.asyncgen_live_method_called = 1`

### 4-2. プレーンな `async def`（coroutine、`yield` なし）— 明示的に拒否される

```python
class AsyncDefLivePage(Page):
    @staticmethod
    async def live_method(player: Player, data):
        player.asyncdef_live_method_attempted = True
        return {player.id_in_group: dict(echo=data)}
```

`otree/live.py` の `call_live_method_compat` を読むと:
```python
elif inspect.iscoroutinefunction(live_method):
    raise LiveMethodBadReturnValue(ASYNC_OR_YIELD_ISSUE_MSG)
```
実行結果:
```
DEBUG AsyncDefLivePage raised: LiveMethodBadReturnValue('live_method can only be (a) a regular function that returns a value, or (b) an async generator function.')
```
CSV: `player.asyncdef_live_method_attempted = 0`（関数の中身が一度も実行されずに拒否されたことが分かる）。

**結論: `live_method` の非同期対応は「async generator（`async def` + `yield`）」のみ。プレーンな `async def`（`await` して `return` するだけの一般的なコルーチン関数）は明示的にエラーになる。**

証跡: `/Users/petadimensionlab/workspace/research/MiroFish-oTree/mirofish_otree_test/export_live4/live_test.csv`、`live_test/__init__.py`、`live_test/tests.py`。

### 4-3. `live_method` 以外のページライフサイクルフックは非同期非対応

`otree/views/abstract.py` の `dispatch()` を確認すると:
```python
response = await run_in_threadpool(self.inner_dispatch, request)
```
`inner_dispatch`（`vars_for_template` / `before_next_page` / `error_message` などを呼ぶ本体）はスレッドプール上で**同期的に**呼ばれる。実機で `LivePage.vars_for_template` を `async def` にして試したところ:

```python
@staticmethod
async def vars_for_template(player: Player):
    return dict(probe="async_vars_for_template_ran")
```

```
File ".../otree/views/abstract.py", line 199, in get_context_data
    raise Exception('vars_for_template did not return a dict')
Exception: vars_for_template did not return a dict
sys:1: RuntimeWarning: coroutine 'LivePage.vars_for_template' was never awaited
EXIT: 1
```

コルーチンオブジェクトがそのまま `dict` 期待の箇所に渡されて即座に例外、プロセス全体が exit 1 で落ちる。このプローブは検証後に元に戻し、現在の `live_test/__init__.py` には残っていない（ログのみ本ドキュメントに保存）。

**結論: oTree 6.0.15 で非同期にできる場所は `live_method`（async generator限定）だけ。それ以外（`vars_for_template`, `before_next_page`, `error_message`, `get_form_fields` 等）はすべて同期関数として扱われ、ブロッキング HTTP 呼び出しをそこに書くとスレッドプールのワーカースレッドをブロックするだけ（F3 の bot 内呼び出しと同じ扱い）。非同期化による並行実行のメリットは得られない。**

---

## 5. `play_round` 内の未捕捉例外（F5 caveatのクローズ）

`mf_group/tests.py` に環境変数 `MF_TEST_RAISE_UNCAUGHT=1` でゲートした故意の例外を追加し検証:

```python
if RAISE_UNCAUGHT and self.player.id_in_group == 1 and self.round_number == 1:
    raise RuntimeError("MF_TEST deliberate uncaught exception in play_round")
```

```
$ MF_TEST_RAISE_UNCAUGHT=1 otree test mf_group 4
Creating 'mf_group' session (test case 0)
Traceback (most recent call last):
  ...
  File ".../mirofish_otree_test/mf_group/tests.py", line 26, in play_round
    raise RuntimeError("MF_TEST deliberate uncaught exception in play_round")
RuntimeError: MF_TEST deliberate uncaught exception in play_round
EXIT: 1
```

`"Bots completed session"` は出力されず、プロセス全体が exit code 1 で終了した。

**結論: F5 のキャッチ済みタイムアウト（bot 側 try/except で `contribution=50` にフォールバックしてラン継続）とは対照的に、未捕捉例外は `otree test` プロセス全体を即座に異常終了させる。** Bridge 呼び出しを bot 側に実装する際は、**必ず try/except で囲み、パース失敗やタイムアウトに対してデフォルト値＋欠測フラグを返す**設計（`tasks.md` Phase 3 にある方針そのもの）にしないと、1エージェントの1回の失敗が実験セッション全体を巻き込んで停止させる。

---

## 6. 副産物: `otree resetdb` 直後に `otree test`/`devserver` が失敗する

検証中に踏んだ運用上の落とし穴。`otree/database.py` の `load_in_memory_db()`（`database.py:92`）は、ディスク上の sqlite ファイルの `PRAGMA user_version` と現在の otree バージョン由来の数値（`version_for_pragma()`, 例: `6.0.15` → `6015`）を比較し、不一致なら

```python
sys.exit(f'oTree has been updated. Please delete your database ({DB_FILE})')
```

で即終了する。ところが `otree/cli/resetdb.py` の `resetdb` コマンドは SQLAlchemy の `engine` 経由でテーブルを再作成するだけで、**`PRAGMA user_version` をディスクファイルに書き込む処理を一切行わない**（`database.py:174` の `sqlite_mem_conn.backup(sqlite_disk_conn)` はメモリ上DBの内容をバックアップする別経路で、`resetdb` コマンドはこれを通らない）。そのため:

```
$ rm -f db.sqlite3 && otree resetdb --noinput
Database engine: sqlite
Created new tables and columns.
$ otree test label_test 3 --export ./export_label
oTree has been updated. Please delete your database (db.sqlite3)
EXIT: 1
```

`sqlite3 db.sqlite3 "PRAGMA user_version;"` は `0` のまま。回避策として確認したワークアラウンド:

```
$ sqlite3 db.sqlite3 "PRAGMA user_version = 6015;"   # 6.0.15 → digits only
$ otree test label_test 3 --export ./export_label
Creating 'label_test' session (test case 0)
...
Bots completed session
```

これで正常に動くようになった。**Phase 1 以降で CI や自動テストスクリプトに `otree resetdb && otree test` を組み込む場合、このワークアラウンド（もしくは `db.sqlite3` を都度物理削除して新規作成させる — 新規作成直後は `Path(DB_FILE).stat().st_size > 0` の条件が false になるケースがあるか要確認）を入れないと初回実行で必ず失敗する。**

---

## 7. 設計上の含意（Design Implications）

### 7-1. レイテンシ予算: 48エージェント × 10ラウンド、1決定あたり約5秒

F4（bot runner は完全にシリアル）が確定している以上、LLM のレイテンシは**単純加算**される。素朴な実装（各ラウンドで各エージェントが `play_round` の中で個別に LLM を呼ぶ）の場合:

```
48 agents × 10 rounds × 5s/decision = 2400s = 40分
```

これはゲーム部分の意思決定だけの数字で、議論フェーズ（デベート）のラウンドや、ページ遷移・HTTPオーバーヘッドは含まない。10ラウンド × 議論フェーズを挟む設計（`plan.md` の「1サイクル」）だと、単純計算で優に1時間を超える。これは対話的な実験運用・イテレーションには非現実的な待ち時間であり、**単純な「bot が個別に LLM を呼ぶ」設計は却下すべき**という結論になる。

**Decision-prefetch design（提案・支持する）:**

F2（`after_all_players_arrive` はグループ全員の提出値にアクセスできる、グループ単位で確実に1回発火する）と、MiroFish 側の `send_batch_interview`（`plan.md` 1章の「使える部品」に記載）を組み合わせ、**グループのバリア到達時点でそのグループの次ラウンドの意思決定を1回のバッチ呼び出しでまとめて計算し、bridge 側にキャッシュしておく**設計を提案する。

- 48エージェントを例えば4人グループ×12グループに分けた場合、素朴設計では 48 回の個別 LLM 呼び出しが必要なラウンドが、prefetch 設計では **12回のバッチ呼び出し**に減る。
- 各 bot の `play_round` は「もう計算済みの値をキャッシュから読むだけ」になるので、F3で確認したブロッキングHTTP呼び出し自体は残るが、レイテンシはキャッシュヒットの応答時間（ミリ秒オーダー）まで縮む。
- バッチ呼び出し自体のレイテンシが「1体だけ聞く」場合と大差ない（LLM プロバイダ側でバッチ内は並列処理される、または `send_batch_interview` が内部で並列化している、という前提）なら、理論上の下限は `groups × rounds × batch_latency` = `12 × 10 × ~6-8s ≈ 720-960s`（12-16分)。48体を1体ずつ呼ぶ場合の40分から3倍前後の改善が見込める。
- **ただし例外: 最初のラウンド（round 1）には「前ラウンドのバリア」が存在しない。** round 1 の決定は `creating_session()`（1(c) で動作確認済みの module-level フック）の中で、セッション作成時に先読みして埋めておく必要がある。これも bridge への1回のバッチ呼び出しで足りるはず。
- 5章の教訓（未捕捉例外はプロセス全体を落とす）と合わせると、prefetch のバッチ呼び出しも当然 try/except で保護し、パース失敗・タイムアウト時は「そのグループだけデフォルト値＋欠測フラグでキャッシュしておく」実装にしないと、1バッチの失敗が後続の bot 呼び出し全部をブロックする。

### 7-2. デベートフェーズのトリガーはどこに置くべきか

F2 と F4 を踏まえると、**`WaitPage.after_all_players_arrive` を使うべきで、bridge 側に別途「提出カウンタ」を実装するのは冗長**という結論になる。理由:

- `after_all_players_arrive` は oTree 側が既にグループ・ラウンド単位の「全員揃った」判定を正しく管理している（F2 で実証済み）。bridge 側で同じことを再実装すると、oTree の内部状態（誰がどのグループ・どのラウンドにいるか）と bridge 側のカウンタの間に二重管理・ズレのリスクが生まれる。
- ただし、**headless bot モード（`otree test`）で確認できたのは「シリアル実行時の挙動」だけ**である（F4）。この検証環境では、あるグループのバリア発火は、別グループのバリア発火と時間的に重ならない（そもそも bot runner 自体が1プロセス1スレッドでラウンドロビンしているため）。
- 一方、実際のブラウザ参加者による live セッションでは事情が異なる。`otree/views/abstract.py` の `dispatch()` は各リクエストを `run_in_threadpool(self.inner_dispatch, request)` で処理しており（4-3で実機確認した箇所と同じコードパス）、複数の参加者・複数のグループが同時にページを送信すれば、**複数グループの `after_all_players_arrive` が実際に並行して（別スレッドで）実行され得る**。これはソースコード読解によるアーキテクチャ上の帰結であり、本検証では headless bot 実行しか行っていないため「複数グループの `after_all_players_arrive` が実際に同時に走ること」自体は実行検証していない（**unverified**、ただしコード上そう設計されている）。

**含意:** デベートフェーズのトリガーを `after_all_players_arrive` に置くのは正しい設計判断だが、そのフック内から bridge を呼び出すコード（HTTP クライアント、キャッシュ書き込みなど)は、**複数グループから同時に叩かれても安全なように**（bridge 側のグループ別キャッシュへの書き込みがアトミックであること、同じグループ・同じラウンドに対する議論注入が二重に走らないようにベキ等性を持たせること）実装しておく必要がある。**`otree test` による headless テストはこの並行性のバグを一切検出できない**（F4 により構造的にシリアルだから）。Phase 1〜2 の CI では `otree test` で機能検証はできても、並行アクセス時の安全性を検証するには別途ブラウザベースの負荷テスト（`browser_bots` コマンド、もしくは複数プロセスから同時に REST API/`live_method` を叩く専用テスト）を用意する必要がある、というのが Phase 0 で確定した最も重要な「テストギャップ」。

---

## 8. ブロッカー・未解決事項

1. **rooms + `participant_label_file` は実行未検証**（1(b)）。ソースコード上明らかに `otree test` からは到達不可能だが、もし将来 rooms を使う設計（人間参加者との混在セッションなど）を検討するなら、ブラウザ経由での実地検証が別途必要。
2. **複数グループの `after_all_players_arrive` が実際に並行実行されること自体は未実行検証**（7-2）。`run_in_threadpool` の存在はソースで確認したが、live セッションでの実地負荷テストは Phase 0 の範囲外だった。Phase 1〜2 で `browser_bots` か同等の並行テストを組み込むべき。
3. **`otree resetdb` の PRAGMA 未書き込み問題**（6章）は、CI パイプラインを組む前に対処方針（ワークアラウンド script 化 or 物理削除フロー）を決めておく必要がある。
4. **バッチ呼び出しの実レイテンシは未計測。** 7-1 の試算は「バッチ呼び出しが単体呼び出しとほぼ同じ時間で完了する」という MiroFish 側 `send_batch_interview` の性能特性を仮定した机上計算であり、実測していない（MiroFish 側の検証は並行して走っている `phase0-mirofish.md` の担当）。両ドキュメントの数字を突き合わせて Phase 2 の予算を再計算する必要がある。
5. **live_method を実運用でどう使うか自体が未決。** 3〜4章の検証結果（yield するだけでは呼ばれない、非同期化は `live_method` の async-generator 限定）を踏まえると、`tasks.md` Phase 1 に既に書かれている「live_method は使わない（bot が通らないため）」という判断は、より正確には「**live_method は bot からは自動的に呼ばれないが、`call_live_method` フックを書けば bot からも検証できる。ただし非同期ストリーミング的な使い方をする実利は薄く、通常の Page + form field 構成の方が bot 実装がシンプル**」と補足すべき。方針自体（live_method を使わない）は妥当だが、理由の精度を上げた。

---

## 付録: 変更・追加したファイル一覧（`MiroFish-oTree` リポジトリ側、参照用）

- `mirofish_otree_test/settings.py` — `label_test` / `live_test` を `SESSION_CONFIGS` に登録
- `mirofish_otree_test/label_test/__init__.py` — `creating_session` をモジュールレベル関数に修正（1(c) の実証）
- `mirofish_otree_test/label_test/Intro.html` — 新規作成（テンプレート欠落で最初落ちたため）
- `mirofish_otree_test/live_test/__init__.py` — `AsyncGenLivePage` / `AsyncDefLivePage` を追加（4章の実証用）
- `mirofish_otree_test/live_test/LivePage.html` — `{{ next_button }}` 追加（bot が submit ボタンを検出できず落ちたため）
- `mirofish_otree_test/live_test/AsyncGenLivePage.html`, `AsyncDefLivePage.html` — 新規作成
- `mirofish_otree_test/live_test/tests.py` — `call_live_method` フック実装（3〜4章の実証）
- `mirofish_otree_test/mf_group/tests.py` — `MF_TEST_RAISE_UNCAUGHT` env var ゲート付きの故意の例外を追加（5章の実証、デフォルト無効なので既存の F1〜F5 の動作に影響なし）
- `mirofish_otree_test/export_label/`, `export_label2/`, `export_label3/`, `export_live1〜5/` — 各検証の CSV エクスポート（証跡）

いずれも `MiroFish-oTree` リポジトリ内であり、`MiroFish-Offline` リポジトリには一切変更を加えていない。git 操作（add/commit/push等）は一切行っていない。
