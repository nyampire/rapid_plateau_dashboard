# OSM 建物の入れ替えを地域単位にする 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 地域ごとの OSM 建物の読み込みが、他の地域が入れた行を消さないようにする。

**Architecture:** `dash_osm_buildings` の各行に、どの地域の抽出から来たかを `source_region` として持たせる。
読み込みの削除を「入力に現れた市区町村コード」から「この地域から来た行」に変える。
県境で 2 つの抽出に現れる同じ建物は `(osm_type, osm_id)` の一意索引で 1 行にまとめる。

**Tech Stack:** Python 3 / psycopg2 / PostgreSQL 16 + PostGIS 3.4 / pytest / bash

## Global Constraints

- 設計は `docs/superpowers/specs/2026-09-11-osm-load-region-scope-design.md` にある。
- 列の名前は `source_region` にする。`region` にしない。`dash_city_master.region` は別の区分（10 区分）で、これは Geofabrik の 8 区分である。
- `--region` の値は Geofabrik の地域名（`hokkaido` `tohoku` `kanto` `chubu` `kansai` `chugoku` `shikoku` `kyushu`）。
- `sql/schema.sql` は何度流しても同じ結果になる形（`IF NOT EXISTS`）を保つ。
- DB を使う試験は `DASH_TEST_DATABASE_URL` を指定したときだけ走る。未指定なら skip する。
- 公開リポジトリに出すファイル、コミットメッセージ、Issue と PR に、サーバ名、IP、内部の絶対パス、接続情報を書かない。
- コードコメントとコミットメッセージは一文ごとに改行する。1 行に 2 つの文を入れない。

---

### Task 1: テーブルの定義と入れ替えの関数

**Files:**
- Modify: `sql/schema.sql:32-41`
- Modify: `ingest/load_osm_buildings.py`（`decode_select_sql` の下に関数を足す）
- Test: `tests/test_load_osm_buildings.py`（末尾に 3 件足す）

**Interfaces:**
- Consumes: なし
- Produces: `load_osm_buildings.replace_region_rows(cur, region, decoded="_decoded") -> int`
  `cur` は psycopg2 のカーソル、`region` は地域名の文字列、`decoded` は
  `city_code` / `osm_type` / `osm_id` / `geom` を持つ表の名前。
  戻り値は削除した行数。

- [ ] **Step 1: テーブルの定義に列と索引を足す**

`sql/schema.sql` の 41 行目（`dash_osm_buildings_city_idx` の行）の直後に次を足します。

```sql

-- 行がどの地域の抽出から来たかを記録する。
-- dash_city_master.region とは別の区分なので、同じ名前にしない。
-- 読み込みはこの列で削除の範囲を決める。
ALTER TABLE dash_osm_buildings ADD COLUMN IF NOT EXISTS source_region TEXT;

-- 県境の建物は隣り合う 2 つの抽出に現れる。
-- 同じ建物を 1 行に保つための一意索引である。
CREATE UNIQUE INDEX IF NOT EXISTS dash_osm_buildings_osm_uidx
  ON dash_osm_buildings (osm_type, osm_id);
CREATE INDEX IF NOT EXISTS dash_osm_buildings_source_region_idx
  ON dash_osm_buildings (source_region);
```

- [ ] **Step 2: 失敗する試験を 3 件書く**

`tests/test_load_osm_buildings.py` の末尾に足します。

```python
def _staging(cur, name):
    """_decoded と同じ形の一時表を作る。"""
    cur.execute(f"CREATE TEMP TABLE {name}("
                "city_code text, osm_type char(1), osm_id bigint, "
                "geom geometry(Geometry,4326))")


def _add(cur, name, city, osm_type, osm_id, offset_deg):
    """一時表に建物を 1 件足す。offset_deg で位置をずらす。"""
    cur.execute(
        f"INSERT INTO {name}(city_code, osm_type, osm_id, geom) "
        "SELECT %s, %s, %s, ST_Translate("
        "  ST_GeomFromText('POLYGON((0 0,0 0.0005,0.0005 0.0005,0.0005 0,0 0))',4326),"
        "  %s, 0)",
        (city, osm_type, osm_id, offset_deg))


def test_other_regions_rows_survive(db):
    """後から読む地域の抽出に 1 件はみ出していても、先の地域が入れた行は残る。

    これが 2026-09-11 に見つかった不具合の再現である。
    いわき市は東北の抽出で 14 万件入ったあと、関東の抽出に南端の数百件が
    含まれていたため、市区町村コード単位の削除で 14 万件が消えていた。
    """
    with db.cursor() as cur:
        _staging(cur, "d_tohoku")
        for i in range(1, 101):
            _add(cur, "d_tohoku", "A", "w", i, i * 0.001)
        lo.replace_region_rows(cur, "tohoku", "d_tohoku")

        _staging(cur, "d_kanto")
        _add(cur, "d_kanto", "A", "w", 9001, 0.5)
        lo.replace_region_rows(cur, "kanto", "d_kanto")

        cur.execute("SELECT count(*) FROM dash_osm_buildings WHERE city_code='A'")
        assert cur.fetchone()[0] == 101


def test_border_building_is_stored_once(db):
    """2 つの地域の抽出に同じ建物が現れても 1 行になる。

    所属は後から読んだ地域に移る。
    翌週にその地域が消して入れ直すので、行は毎週更新される。
    """
    with db.cursor() as cur:
        _staging(cur, "d_a")
        _add(cur, "d_a", "A", "w", 42, 0.0)
        lo.replace_region_rows(cur, "kanto", "d_a")

        _staging(cur, "d_b")
        _add(cur, "d_b", "A", "w", 42, 0.0)
        lo.replace_region_rows(cur, "chubu", "d_b")

        cur.execute("SELECT count(*), min(source_region) FROM dash_osm_buildings")
        assert cur.fetchone() == (1, "chubu")


def test_region_reload_drops_vanished_buildings(db):
    """同じ地域を読み直すと、抽出から消えた建物は残らない。"""
    with db.cursor() as cur:
        _staging(cur, "d_first")
        _add(cur, "d_first", "A", "w", 1, 0.0)
        _add(cur, "d_first", "A", "w", 2, 0.01)
        lo.replace_region_rows(cur, "kanto", "d_first")

        _staging(cur, "d_second")
        _add(cur, "d_second", "A", "w", 1, 0.0)
        lo.replace_region_rows(cur, "kanto", "d_second")

        cur.execute("SELECT osm_id FROM dash_osm_buildings ORDER BY osm_id")
        assert cur.fetchall() == [(1,)]
```

- [ ] **Step 3: 試験を走らせて失敗を確かめる**

```bash
cd ~/git/rapid_plateau_dashboard && DASH_TEST_DATABASE_URL=postgresql:///dash_test python3 -m pytest tests/test_load_osm_buildings.py -v
```

期待する結果: 新しい 3 件が `AttributeError: module 'load_osm_buildings' has no attribute 'replace_region_rows'` で失敗します。
既存の 2 件は通ります。

- [ ] **Step 4: `replace_region_rows` を実装する**

`ingest/load_osm_buildings.py` の `decode_select_sql` の定義の直後（80 行目の `"""` の次）に、空行 2 つを挟んで足します。

```python
def replace_region_rows(cur, region, decoded="_decoded"):
    """この地域の抽出から来た行を、新しい抽出の内容で入れ替える。

    削除の範囲は「この地域から来た行」であって、「入力に現れた市区町村」ではない。
    地域の抽出は境界の外へ少しはみ出す。
    市区町村で削除すると、隣の地域が入れたその市の行まで消えてしまう。
    2026-09-11 には、これでいわき市の 14 万件が 386 件になっていた。

    県境の建物は隣り合う 2 つの抽出に現れる。
    一意索引で 1 行にまとめ、所属を後から読んだ地域に移す。
    翌週にその地域が消して入れ直すため、行は毎週更新される。

    削除した行数を返す。
    """
    cur.execute("DELETE FROM dash_osm_buildings WHERE source_region = %s;", (region,))
    deleted = cur.rowcount
    cur.execute(
        "INSERT INTO dash_osm_buildings (city_code, osm_type, osm_id, geom, source_region) "
        f"SELECT city_code, osm_type, osm_id, geom, %s FROM {decoded} "
        "ON CONFLICT (osm_type, osm_id) DO UPDATE SET "
        "  city_code = EXCLUDED.city_code, "
        "  geom = EXCLUDED.geom, "
        "  source_region = EXCLUDED.source_region, "
        "  fetched_at = now();",
        (region,))
    return deleted
```

- [ ] **Step 5: 試験を走らせて通ることを確かめる**

```bash
cd ~/git/rapid_plateau_dashboard && DASH_TEST_DATABASE_URL=postgresql:///dash_test python3 -m pytest tests/test_load_osm_buildings.py -v
```

期待する結果: 5 件すべて PASS です。

- [ ] **Step 6: 試験を全部走らせる**

```bash
cd ~/git/rapid_plateau_dashboard && DASH_TEST_DATABASE_URL=postgresql:///dash_test python3 -m pytest -v
```

期待する結果: 失敗 0 件です。

- [ ] **Step 7: コミットする**

```bash
cd ~/git/rapid_plateau_dashboard && git add sql/schema.sql ingest/load_osm_buildings.py tests/test_load_osm_buildings.py && git commit -F - <<'MSG'
fix: OSM 建物の入れ替えを市区町村単位から地域単位に変える

地域の抽出は境界の外へ少しはみ出す。
入力に現れた市区町村コードで削除していたため、隣の地域の抽出に数件はみ出した
都市は、先に入れた本体を失っていた。
行に source_region を持たせ、その地域から来た行だけを入れ替える。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 2: 読み込みの入り口と週次バッチを繋ぐ

**Files:**
- Modify: `ingest/load_osm_buildings.py:83-126`（`main`）
- Modify: `ingest/load_osm_buildings.py:1-16`（先頭の説明）
- Modify: `run_batch.sh:51`

**Interfaces:**
- Consumes: `load_osm_buildings.replace_region_rows(cur, region, decoded="_decoded") -> int`（Task 1）
- Produces: `load_osm_buildings.py <geojsonseq> --postgres-url <url> --region <地域名>`

- [ ] **Step 1: `main` に `--region` を足し、新しい関数を呼ぶ**

`ingest/load_osm_buildings.py` の 86 行目の直後に引数を足します。

```python
    ap.add_argument("--region", required=True,
                    help="Geofabrik の地域名（hokkaido / tohoku / kanto / chubu / "
                         "kansai / chugoku / shikoku / kyushu）。"
                         "この名前で入れ替える範囲が決まる。")
```

続いて、`main` の中の次の 8 行を置き換えます。
行番号は引数を足したぶんずれるので、内容で探してください。

```python
            t2 = time.time()
            cur.execute("DELETE FROM dash_osm_buildings WHERE city_code IN "
                        "(SELECT DISTINCT city_code FROM _decoded);")
            deleted = cur.rowcount
            cur.execute("INSERT INTO dash_osm_buildings (city_code, osm_type, osm_id, geom) "
                        "SELECT city_code, osm_type, osm_id, geom FROM _decoded;")
            cur.execute(f"DROP TABLE IF EXISTS {STAGING};")
            print(f"[time] delete+insert: {time.time() - t2:.1f}s")
```

置き換えた結果は次のとおりです。

```python
            t2 = time.time()
            deleted = replace_region_rows(cur, args.region)
            cur.execute(f"DROP TABLE IF EXISTS {STAGING};")
            print(f"[time] delete+insert: {time.time() - t2:.1f}s")
```

最後の表示の 2 行

```python
        print(f"loaded {n_rows} OSM buildings across {n_cities} cities "
              f"(replaced {deleted} existing rows)")
```

を次で置き換えます。

```python
        print(f"loaded {n_rows} OSM buildings across {n_cities} cities "
              f"as region {args.region} (replaced {deleted} rows of that region)")
```

- [ ] **Step 2: 先頭の説明を直す**

`ingest/load_osm_buildings.py` の 10 行目から 11 行目の 2 文

```
outside every coverage polygon are dropped. Idempotent: reloads replace the affected
cities' rows.
```

を次で置き換えます。

```
outside every coverage polygon are dropped.
Idempotent: a reload replaces the rows this region's extract produced last time,
identified by source_region. Region extracts overlap at the border, so a building
that appears in two regions is kept as one row by the (osm_type, osm_id) unique index.
```

15 行目の使い方の例を次で置き換えます。

```
  python3 load_osm_buildings.py buildings.geojsonseq --postgres-url "$DATABASE_URL" --region kanto
```

- [ ] **Step 3: 地域名を渡さないと止まることを確かめる**

```bash
cd ~/git/rapid_plateau_dashboard && python3 ingest/load_osm_buildings.py /dev/null --postgres-url "postgresql:///dash_test"; echo "終了コード: $?"
```

期待する結果: `error: the following arguments are required: --region` が出て、終了コードは 2 です。

- [ ] **Step 4: 週次バッチから地域名を渡す**

`run_batch.sh` の 51 行目を次で置き換えます。

```bash
  python3 "$HERE/ingest/load_osm_buildings.py" "$GJ" --postgres-url "$PGURL" --region "$r"
```

- [ ] **Step 5: 試験を全部走らせる**

```bash
cd ~/git/rapid_plateau_dashboard && DASH_TEST_DATABASE_URL=postgresql:///dash_test python3 -m pytest -v
```

期待する結果: 失敗 0 件です。

- [ ] **Step 6: コミットする**

```bash
cd ~/git/rapid_plateau_dashboard && git add ingest/load_osm_buildings.py run_batch.sh && git commit -F - <<'MSG'
fix: 読み込みと週次バッチで地域名を受け渡す

load_osm_buildings.py に必須の --region を足した。
run_batch.sh は取得中の地域名をそのまま渡す。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 3: 本番への反映と確認

**Files:**
- 変更なし（実行のみ）

**Interfaces:**
- Consumes: Task 2 まででマージ済みのコード

Task 1 と Task 2 を Pull Request としてマージし、サーバ側の作業用ディレクトリを最新にしてから実行します。
日曜 01:00 の定期実行とぶつけないこと、06:00 から 06:35 の間を跨がないことを守ります。

- [ ] **Step 1: 実行前の値を控える**

```sql
SELECT s.city_code, m.city_name, s.plateau_count, s.osm_count,
       s.intersecting_count, s.import_rate
FROM dash_city_stats s JOIN dash_city_master m ON m.city_code = s.city_code
WHERE s.city_code IN ('07204','07205','11207','13308','14150','14382')
ORDER BY s.city_code;

SELECT total_plateau, total_intersecting, overall_rate, computed_at
FROM dash_progress_history WHERE region = '__overall__'
ORDER BY computed_at DESC LIMIT 1;
```

- [ ] **Step 2: 一意索引を作れることを確かめる**

```sql
SELECT count(*) FROM (
  SELECT osm_type, osm_id FROM dash_osm_buildings
  GROUP BY osm_type, osm_id HAVING count(*) > 1
) d;
```

期待する結果: `0` です。
0 でなければ、索引を作る前に次を実行して重複を消します。

```sql
DELETE FROM dash_osm_buildings a USING dash_osm_buildings b
WHERE a.osm_type = b.osm_type AND a.osm_id = b.osm_id AND a.id > b.id;
```

- [ ] **Step 3: テーブルの定義を適用する**

```bash
psql "$DATABASE_URL" -f sql/schema.sql
```

900 万行への一意索引の作成で数分かかります。

- [ ] **Step 4: 3 地域を取得して読み込む**

この順で実行します。
順番を入れ替えないでください。

```bash
./osmium/fetch_region_buildings.sh tohoku /tmp/tohoku.geojsonseq /tmp
python3 ingest/load_osm_buildings.py /tmp/tohoku.geojsonseq --postgres-url "$DATABASE_URL" --region tohoku
rm -f /tmp/tohoku.geojsonseq
```

```bash
./osmium/fetch_region_buildings.sh kanto /tmp/kanto.geojsonseq /tmp
python3 ingest/load_osm_buildings.py /tmp/kanto.geojsonseq --postgres-url "$DATABASE_URL" --region kanto
rm -f /tmp/kanto.geojsonseq
```

```bash
./osmium/fetch_region_buildings.sh chubu /tmp/chubu.geojsonseq /tmp
python3 ingest/load_osm_buildings.py /tmp/chubu.geojsonseq --postgres-url "$DATABASE_URL" --region chubu
rm -f /tmp/chubu.geojsonseq
```

合わせて 1 時間から 1 時間半の見込みです。
`kanto` が支配的です。

- [ ] **Step 5: 集計をやり直す**

```bash
python3 ingest/compute_stats.py --postgres-url "$DATABASE_URL"
```

- [ ] **Step 6: 6 都市の値を確かめる**

Step 1 と同じ問い合わせを実行します。

期待する結果: 6 都市の `osm_count` が 4 桁から 6 桁になり、`import_rate` が数十パーセントになります。
いわき市は `osm_count` が 386 から 10 万件台に増えます。

配信中の API でも確かめます。

```bash
curl -s https://rapid.nyampire.info/api/dashboard/cities | python3 -c "import sys,json;[print(r['city_code'],r['city_name'],r['osm_count'],r['import_rate']) for r in json.load(sys.stdin) if r['city_code'] in ('07204','07205','11207','13308','14150','14382')]"
```

---

## 後で行うこと（この計画には含めない）

次の日曜の定期実行で 8 地域を 1 周したあと、どの抽出にも現れなかった古い行を 1 回だけ片付けます。

```sql
DELETE FROM dash_osm_buildings WHERE source_region IS NULL;
```

`run_batch.sh` には入れません。
一部の地域だけを指定して走らせたときに、読まなかった地域の行まで消えるからです。
