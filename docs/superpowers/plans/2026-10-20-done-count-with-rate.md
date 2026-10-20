# 取り込み率が90%以上の都市も完了に数える 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** wikiで完了の都市と取り込み率90%以上の都市を合わせて完了に数え、画面、API、週次の記録で同じ数を出す。

**Architecture:** 境目の値をAPIと集計の両方に定数で置き、SQLに埋め込んで数える。
APIは`cities_done`（新しい定義）と`cities_osm_done`（wikiの完了）を並べて返し、都市ごとに`done_source`を返す。
画面は境目を持たず、APIの値だけで描く。

**Tech Stack:** Python 3.12、FastAPI、psycopg2、PostgreSQL 16 + PostGIS、pytest、素のJavaScript。

**Spec:** `docs/superpowers/specs/2026-10-20-done-count-with-rate-design.md`

## Global Constraints

- 境目は90。率は`dash_city_stats.import_rate`で判定し、`import_rate >= 90`を完了に含む（90.00ちょうどは完了、89.99は完了でない）。
- 率が`null`の都市は、wikiで完了でない限り完了でない。
- wikiで完了の都市は、率に関係なく完了。両方に当てはまる都市の`done_source`は`"wiki"`。
- 名前は全応答で`cities_done`（新しい定義）と`cities_osm_done`（wikiの完了）にそろえる。
- `dash_progress_history.cities_osm_done`の意味は変えない。過去の行の`cities_done`は`null`のまま。
- 画面から「ほぼ完了」という言葉を無くす。完了（wiki）と完了（率）は同じ完了の色。
- 画面の文は、和文と英数字の間に空白を入れない（既存の文の書き方に合わせる箇所は除く）。
- DBを使うテストは`DASH_TEST_DATABASE_URL=postgresql:///dash_test pytest`で走らせる。手元に`dash_test`がある。
- `git add -A`は使わない。

## Review Focus

- wikiで作業中かつ率90%以上の都市：一覧と詳細の欄で「完了（率）」と出て、絞り込みの「完了（率）」と「完了（すべて）」に入る。Task 3のテストで`done_source`が`"rate"`になることを確かめる。
- 率が未計測でwikiで完了の都市：`done_source`が`"wiki"`、地域ごとの`cities_done`にも入る。Task 3のテストで確かめる。
- `boundary_geom`が無い都市：`/summary`の`cities_done`に入らない。Task 3のテストで確かめる。
- APIが読めず`data.js`で描く場合：カード、地域、一覧が新しい名前で描ける。Task 4で`data.js`を作り直し、APIを止めた状態でも画面を確かめる。
- 都市名で並べ替えた一覧の「インポート作業」の列：完了（wiki）、完了（率）、作業中、未着手の順に並ぶ。Task 4の画面の確認に入れる。

---

### Task 0: 既存のテストの準備データに境界を入れる

mainの時点で、DBを使うテストが9件失敗する（2026-10-20に確認、19件成功）。
rapid_plateau_dashboard#27で都市の数を`boundary_geom IS NOT NULL`に絞ったが、テストの準備データの`dash_city_master`に境界を入れていないため、都市が0件と数えられる。
GitHubのテストはDBを使うテストを飛ばすので、この失敗は表に出ていなかった。

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_compute_stats.py`（`dash_city_master`に行を入れる箇所すべて）
- Modify: `tests/test_dashboard_api.py`（同上）

**Interfaces:**
- Produces: `conftest.BOUNDARY_SQL = "ST_Multi(ST_MakeEnvelope(139, 35, 139.1, 35.1, 4326))"`（`dash_city_master.boundary_geom`に入れるSQLの式）。以降のTaskの準備データはこれを使う。

- [ ] **Step 1: 失敗を確かめる**

Run: `DASH_TEST_DATABASE_URL=postgresql:///dash_test pytest -q`
Expected: `9 failed, 19 passed`

- [ ] **Step 2: `BOUNDARY_SQL`を`tests/conftest.py`に置き、準備データの`INSERT`に`boundary_geom`を足す**

`dash_city_master`に行を入れるすべての`INSERT`に、列`boundary_geom`と値`BOUNDARY_SQL`を足す。
`test_load_osm_buildings.py`のように境界の形そのものを試しているテストは変えない。

- [ ] **Step 3: 通ることを確かめる**

Run: `DASH_TEST_DATABASE_URL=postgresql:///dash_test pytest -q`
Expected: `28 passed`

- [ ] **Step 4: コミット**

```bash
git add tests/conftest.py tests/test_compute_stats.py tests/test_dashboard_api.py
git commit -m "test: 準備データの市区町村に境界を入れ、#27の絞り込みに合わせる"
```

---

### Task 1: 境目の定数と、週次の記録の`cities_done`

**Files:**
- Modify: `sql/schema.sql`（`dash_progress_history`の定義のあと）
- Modify: `ingest/compute_stats.py:100-125`（`PROGRESS_HISTORY_INSERT_SQL`）、`ingest/compute_stats.py:302-314`（ログ）
- Test: `tests/test_compute_stats.py`

**Interfaces:**
- Produces: `compute_stats.RATE_DONE_THRESHOLD = 90`（int）。`dash_progress_history.cities_done INTEGER`の列。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_compute_stats.py`に足す。
既存の`test_progress_history_insert_emits_per_region_and_overall`と同じ形で、`dash_city_master`（`boundary_geom`は`BOUNDARY_SQL`）と`dash_city_stats`に次の4都市を入れ、`PROGRESS_HISTORY_INSERT_SQL`を実行する。

| city_code | region | osm_import_status | import_rate |
|---|---|---|---|
| 13104 | 関東 | done | 80.00 |
| 13115 | 関東 | not_started | 90.00 |
| 13116 | 関東 | in_progress | 89.99 |
| 23100 | 中部 | done | （stats行なし） |

```python
def test_progress_history_cities_done_is_union_of_wiki_and_rate(db):
    ...
    cur.execute("SELECT region, cities_osm_done, cities_done FROM dash_progress_history ORDER BY region")
    rows = {r[0]: r[1:] for r in cur.fetchall()}
    assert rows["関東"] == (1, 2)        # 13104(wiki) + 13115(90.00)。13116(89.99)は入らない
    assert rows["中部"] == (1, 1)        # 率なしでもwikiの完了は入る
    assert rows["__overall__"] == (2, 3)
```

既存の2つのテストが`cities_osm_done`まで読んでいる行の比較は、そのまま通ることを確かめる（`cities_done`は読まないので変えない）。

- [ ] **Step 2: 失敗を確かめる**

Run: `DASH_TEST_DATABASE_URL=postgresql:///dash_test pytest tests/test_compute_stats.py -v`
Expected: 新しいテストが`column "cities_done" does not exist`で失敗する。

- [ ] **Step 3: 列と数え方を足す**

`sql/schema.sql`に`ALTER TABLE dash_progress_history ADD COLUMN IF NOT EXISTS cities_done INTEGER;`を足し、列の説明に「wikiの完了か率90%以上の都市数。2026-10以前の行は空」と書く。
`ingest/compute_stats.py`の定数の並び（`LOCK_KEY`の近く）に`RATE_DONE_THRESHOLD = 90`を置き、仕様の日付つきの説明を1行添える。
`PROGRESS_HISTORY_INSERT_SQL`の`cb`に`s.import_rate`を足し、列の並びと`SELECT`の最後に`count(*) FILTER (WHERE cb.osm_import_status = 'done' OR cb.import_rate >= {RATE_DONE_THRESHOLD})`を足す。
定数はf文字列でSQLに埋める（利用者の入力ではないため）。

- [ ] **Step 4: 通ることを確かめる**

Run: `DASH_TEST_DATABASE_URL=postgresql:///dash_test pytest tests/test_compute_stats.py -v`
Expected: すべて成功。

- [ ] **Step 5: ログの1行に新しい数を足す**

`main()`の記録を読む`SELECT`に`cities_done`を足し、出力を`cities total={ct} in_db={cdb} osm_done={cod} done={cd}`にする。
`python3 -c "import ast,sys; ast.parse(open('ingest/compute_stats.py').read())"`で構文を確かめる。

- [ ] **Step 6: コミット**

```bash
git add sql/schema.sql ingest/compute_stats.py tests/test_compute_stats.py
git commit -m "feat(ingest): 週次の記録に、率90%以上も含めた完了の数を残す"
```

---

### Task 2: APIの境目の定数と、集計と一致することのテスト

**Files:**
- Modify: `api/dashboard_api.py`（`_db_url`の前）
- Test: `tests/test_rate_done_threshold.py`（新規、DB不要）

**Interfaces:**
- Consumes: `compute_stats.RATE_DONE_THRESHOLD`
- Produces: `dashboard_api.RATE_DONE_THRESHOLD = 90`（int）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_threshold_is_same_in_api_and_ingest():
    import compute_stats, dashboard_api
    assert dashboard_api.RATE_DONE_THRESHOLD == compute_stats.RATE_DONE_THRESHOLD == 90
```

- [ ] **Step 2: 失敗を確かめる**

Run: `pytest tests/test_rate_done_threshold.py -v`
Expected: `AttributeError: module 'dashboard_api' has no attribute 'RATE_DONE_THRESHOLD'`

- [ ] **Step 3: `api/dashboard_api.py`に`RATE_DONE_THRESHOLD = 90`を置く**

本番では別の場所に置かれるため、`compute_stats`をimportせず、値を写す理由を1行の説明に書く。

- [ ] **Step 4: 通ることを確かめる**

Run: `pytest tests/test_rate_done_threshold.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add api/dashboard_api.py tests/test_rate_done_threshold.py
git commit -m "feat(api): 完了とみなす率の境目を定数で持つ"
```

---

### Task 3: APIの応答に`cities_done`、`done_source`、`rate_done_threshold`を足す

**Files:**
- Modify: `api/dashboard_api.py:59-150`（`SUMMARY_SQL`、`PROGRESS_SQL`、`REGIONS_SQL`、`CITY_COLS`）
- Test: `tests/test_dashboard_api.py`

**Interfaces:**
- Consumes: `dashboard_api.RATE_DONE_THRESHOLD`、`dash_progress_history.cities_done`（Task 1）
- Produces（JSON）:
  - `/summary`：`cities_done`（int）、`cities_osm_done`（int、今と同じ）、`rate_done_threshold`（int、90）
  - `/regions`の各行：`cities_done`（int、新しい定義に変わる）、`cities_osm_done`（int、新規）
  - `/progress`：`cities_done`（int または null）
  - `/cities`と`/cities/{code}`の各都市：`done_source`（`"wiki"`、`"rate"`、null）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_dashboard_api.py`に、既存の`client`とは別のfixture`client_done`を足す。
`dash_city_master`に次の6都市を入れる（`boundary_geom`は13999以外に`BOUNDARY_SQL`を入れ、13999は`NULL`にする）。
`dash_progress_history`には`region='__overall__'`、`overall_rate`あり、`cities_done=4`の行を1つ入れる（`/progress`は`overall_rate`が無いと404を返すため）。

| city_code | region | osm_import_status | import_rate | boundary_geom |
|---|---|---|---|---|
| 11230 | 関東 | done | 89.90 | あり |
| 13229 | 関東 | not_started | 97.20 | あり |
| 13116 | 関東 | in_progress | 90.00 | あり |
| 23100 | 中部 | done | （stats行なし） | あり |
| 13308 | 関東 | not_started | 25.20 | あり |
| 13999 | 関東 | not_started | 99.00 | なし |

```python
def test_summary_counts_union_and_threshold(client_done):
    j = client_done.get("/api/dashboard/summary").json()
    assert j["cities_osm_done"] == 2      # 11230, 23100
    assert j["cities_done"] == 4          # + 13229, 13116。13999は境界なしで除く
    assert j["rate_done_threshold"] == 90

def test_regions_split_done_and_osm_done(client_done):
    by = {r["region"]: r for r in client_done.get("/api/dashboard/regions").json()}
    assert (by["関東"]["cities_osm_done"], by["関東"]["cities_done"]) == (1, 3)
    assert (by["中部"]["cities_osm_done"], by["中部"]["cities_done"]) == (1, 1)

def test_cities_done_source(client_done):
    by = {c["city_code"]: c["done_source"] for c in client_done.get("/api/dashboard/cities").json()}
    assert by["11230"] == "wiki"   # wikiで完了、率は90未満
    assert by["13229"] == "rate"
    assert by["13116"] == "rate"   # wikiで作業中でも率90.00なら完了（率）
    assert by["23100"] == "wiki"   # 率なし
    assert by["13308"] is None
    assert client_done.get("/api/dashboard/cities/13229").json()["done_source"] == "rate"

def test_progress_returns_cities_done(client_done):
    assert client_done.get("/api/dashboard/progress").json()["cities_done"] == 4
```

`/regions`は`boundary_geom IS NOT NULL`で絞っているので、13999は地域の数にも入らない。
既存の`test_summary`（`cities_osm_done == 1`）はそのまま通ることを確かめる。

- [ ] **Step 2: 失敗を確かめる**

Run: `DASH_TEST_DATABASE_URL=postgresql:///dash_test pytest tests/test_dashboard_api.py -v`
Expected: 新しい4つが`KeyError`か値の不一致で失敗する。

- [ ] **Step 3: SQLを直す**

`SUMMARY_SQL`に`'cities_done'`と`'rate_done_threshold'`を足す。
`cities_done`は`dash_city_master`と`dash_city_stats`を`LEFT JOIN`し、`boundary_geom IS NOT NULL AND (osm_import_status='done' OR import_rate >= 境目)`で数える。
`REGIONS_SQL`の`cities_done`を同じ条件に変え、wikiだけの数を`cities_osm_done`として足す。
`PROGRESS_SQL`に`cities_done`を記録から読む行を足す。
`CITY_COLS`に`CASE WHEN m.osm_import_status='done' THEN 'wiki' WHEN s.import_rate >= 境目 THEN 'rate' END AS done_source`を足す。
境目はTask 1と同じくf文字列で埋める。
SQLの中に`%`の文字を足さないこと（`PROGRESS_SQL`と`CITIES_SQL`はpsycopg2の引数を使うため、`%`が書式として解釈される）。

- [ ] **Step 4: 通ることを確かめる**

Run: `DASH_TEST_DATABASE_URL=postgresql:///dash_test pytest -v`
Expected: すべて成功。

- [ ] **Step 5: コミット**

```bash
git add api/dashboard_api.py tests/test_dashboard_api.py
git commit -m "feat(api): 完了の数に率90%以上の都市を含め、都市ごとに完了の出どころを返す"
```

---

### Task 4: 画面と模擬データ

**Files:**
- Modify: `frontend/gen_mock_data.py`（APIと同じSQLの写し。Task 3の変更を同じように反映する）
- Modify: `frontend/data.js`（作り直す）
- Modify: `frontend/app.js:18-46`（カード）、`:85`（地域）、`:105-131`（一覧の印と絞り込み）、`:138-145`（並べ替え）、`:218`と`:257`（詳細の欄）
- Modify: `frontend/index.html`（カード`:44-49`、用語の説明の表`:66-89`、絞り込み`:120-121`、列の見出し`:136`、`?v=`の印）
- Modify: `frontend/style.css`（`badge ratedone`を消し、完了（率）は`badge done`を使う）

**Interfaces:**
- Consumes: Task 3のJSON（`cities_done`、`cities_osm_done`、`rate_done_threshold`、`done_source`）

- [ ] **Step 1: 模擬データを作り直す**

`gen_mock_data.py`の使い方（ファイル冒頭の説明）に従って`data.js`を作り直す。
本番のデータベースから作る場合は、読み取りの接続で実行する。
`grep -c done_source frontend/data.js`が0でないこと、`grep -o '"rate_done_threshold": *90' frontend/data.js`が1件出ることを確かめる。

- [ ] **Step 2: カードを直す**

`RATE_DONE`と`isRateDone`を消し、境目は`s.rate_done_threshold`から読む。
`#kpi-done`は`${s.cities_done} / ${s.cities_total}`、割合は`pct(s.cities_done, s.cities_total)`。
`#f-done`の内訳は`うちwikiに完了と記載 ${s.cities_osm_done} 都市`の1行だけにし、「＋ ほぼ完了」の行を消す（数と単位の間の空白は既存のカードの書き方に合わせる）。
`index.html`の見出しを「OSMインポート完了都市数」、説明文を「OSM wikiで完了と記載された都市と、取り込み率が<span id="done-th">90</span>%以上の都市の合計です。」にし、`#done-th`を`s.rate_done_threshold`で埋める。

- [ ] **Step 3: 地域のカード、一覧、詳細の欄を直す**

地域のカードの`r.cities_done`はそのまま使う（APIの意味が変わるため、コードの変更は要らない）。
`statusBadge(c)`を次の順で判定する：`done_source === "wiki"`なら`完了（wiki）`（検証済みなら今の「✓検証」を添える）、`"rate"`なら`完了（率）`、`osm_import_status === "in_progress"`なら`作業中`、それ以外は`未着手`。
完了（wiki）と完了（率）は、どちらも`badge done`のクラスを使う。
`rateCell`と詳細の欄の率の横にある「ほぼ完了」の印を消す。
詳細の欄の行の見出し「OSMインポート(wiki)」を「インポート作業」にし、完了（wiki）のときだけ`osm_import_date`を添える。
一覧の見出しの補足を「OSM wikiの記録と取り込み率」にする。

- [ ] **Step 4: 絞り込みと並べ替えを直す**

絞り込みの選択肢を、値`done_all`「完了（すべて）」、`done_wiki`「完了（wiki）」、`done_rate`「完了（率）」に置き換える。
`passFilter`は、`done_all`なら`done_source`がnullでない都市、`done_wiki`なら`"wiki"`、`done_rate`なら`"rate"`を残す。
「インポート作業」の列で並べ替えるときの順番は、完了（wiki）、完了（率）、作業中、未着手。
列の`data-sort`の値と、`sortKey`から並べ替えの値を出す処理を、この順の数を返す形にする。

- [ ] **Step 5: 用語の説明の表を直す**

「OSMインポート完了都市数（OSM wiki）」の行を「OSMインポート完了都市数」にし、意味の欄に「wikiで完了と記載された都市と、取り込み率が90%以上の都市の合計」を書く。
wikiの`imports_list`へのリンクと、wikiで進捗を管理しているという説明は残す。
計算方法の欄は「（wikiで完了 または 率≥90%の都市数）/ Plateau公開都市数（306）」にする。
「ほぼ完了（率ベース）」の行は「完了（率）」に改め、「wikiに記載がなくても率が90%以上なら完了に数える。率が90%を割ると外れる」と書く。
率が100%に届かない理由の注記は残す。
`grep -n "ほぼ完了" frontend/`で0件になることを確かめる。

- [ ] **Step 6: 手元で画面を確かめる**

`python3 frontend/stamp_cache.py`を実行し、`python3 frontend/stamp_cache.py --check`が成功することを確かめる。
手元のAPI（`DATABASE_URL=postgresql:///dash_test uvicorn dashboard_api:app --port 8000`を`api/`で起動）か`data.js`で画面を開き、次を確かめる。

- カードの数が`cities_done / cities_total`で、内訳の1行が`cities_osm_done`と一致する。
- 地域のカードの完了の数の合計が、カードの数と一致する。
- 一覧で「完了（wiki）」「完了（率）」が同じ色で出る。
- 3つの絞り込みで残る都市の数が、`done_source`ごとの数と一致する。
- 「インポート作業」の列で並べ替えると、完了（wiki）、完了（率）、作業中、未着手の順になる。
- APIを止めて開き直しても、`data.js`で同じ画面が出る。
- コンソールにエラーが出ない。

- [ ] **Step 7: コミット**

```bash
git add frontend/app.js frontend/index.html frontend/style.css frontend/gen_mock_data.py frontend/data.js
git commit -m "feat(frontend): 完了の数に率90%以上の都市を含め、完了（wiki）と完了（率）を出す"
```

---

### Task 5: 設計文書の更新と仕上げ

**Files:**
- Modify: `docs/DESIGN.md`（3.3節、9節）
- Modify: `docs/UI_DESIGN.md`（補助の指標の表`:87`、APIの表`:132-133`、9節の未決事項`:154`）

- [ ] **Step 1: 文書を直す**

`docs/DESIGN.md`の3.3節に、2026-10-20に完了の定義を「wikiで完了、または率90%以上」に改めたことと、wikiの完了の数は`cities_osm_done`として残すことを書く。
9節の「wiki未記載だが率が高い都市の表示ラベル」の未決事項を、決まった内容（完了（率））で閉じる。
`docs/UI_DESIGN.md`の表の`cities_osm_done`を`cities_done`に直し、`/regions`の説明に`cities_osm_done`を足す。
設計文書（`docs/superpowers/specs/2026-10-20-done-count-with-rate-design.md`）へのリンクを両方に置く。

- [ ] **Step 2: 全体のテストを走らせる**

Run: `DASH_TEST_DATABASE_URL=postgresql:///dash_test pytest -v`と`pytest -v`（DBなし）と`python3 frontend/stamp_cache.py --check`
Expected: DBありはすべて成功、DBなしはDBのテストがskipされてほかは成功、`--check`は成功。

- [ ] **Step 3: 機微情報を確かめる**

`git diff main`を読み、本番のサーバのファイルの置き場所、IPアドレス、管理者の権限で行う操作の手順が入っていないことを確かめる。

- [ ] **Step 4: コミット**

```bash
git add docs/DESIGN.md docs/UI_DESIGN.md
git commit -m "docs: 完了の定義を改めたことを設計文書に書く"
```

---

## このあと（計画の外、利用者の承認を取ってから行う）

Pull Requestを作る前に、本文とコミットメッセージにも機微情報が無いことを確かめる。
本番への反映は、設計文書の「本番への反映の順番」に従う。
APIの再起動はsudoが要るので、利用者に依頼する。
日曜01:00からの週次の集計の時間帯には反映しない。
