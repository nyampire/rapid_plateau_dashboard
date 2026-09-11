# 設計: OSM 建物の入れ替えを地域単位にする

作成日: 2026-09-11

## 解く問題

一部の都市で、ダッシュボードの建物カバレッジ率が実態とかけ離れて低く出ます。
いわき市は 0.05 パーセント、秩父市は 0.00 パーセントと表示されています。

原因は集計の計算ではなく、集計が読む `dash_osm_buildings` から建物が消えていることです。

## 計測した事実

いわき市の PLATEAU 建物（外形のみ）は 231,508 件あります。
これに対して `dash_osm_buildings` のいわき市の行は 386 件です。

同じ日の日本全体の PBF を osmium で読むと、いわき市の外接矩形に 139,765 件、
中心部の約 4 km 四方だけでも 19,654 件の建物があります。
OSM に建物が無いのではなく、集計用のテーブルから失われています。

残っている行の位置に偏りがあります。
いわき市の 386 件は市域の南の縁から最大 744 m の帯に収まっています。
市域の面積の平方根は 35 km です。

同じ測り方を率の低い 35 都市に当てると、6 都市だけがはっきり分かれます。

| 都市 | 率 | 残っている行 | 縁からの最大距離 | 市域の一辺相当 | 比率 |
|---|---|---|---|---|---|
| 秩父市 | 0.00 | 8 | 293 m | 24,038 m | 1.2 % |
| いわき市 | 0.05 | 386 | 744 m | 35,097 m | 2.1 % |
| 白河市 | 0.05 | 73 | 804 m | 17,474 m | 4.6 % |
| 奥多摩町 | 0.51 | 74 | 816 m | 15,018 m | 5.4 % |
| 箱根町 | 1.36 | 187 | 1,147 m | 9,636 m | 11.9 % |
| 相模原市 | 0.61 | 3,579 | 3,274 m | 18,136 m | 18.1 % |

残る 29 都市はこの比率が 22 パーセントから 43 パーセントで、行が市域全体に散らばっています。
そちらは OSM に建物が少ないだけで、この不具合ではありません。

## 原因

`run_batch.sh` は地域ごとの OSM 抽出を次の順で読み込みます。

```
hokkaido tohoku kanto chubu kansai chugoku shikoku kyushu
```

`ingest/load_osm_buildings.py` は読み込みのたびに、今回の入力に現れた市区町村コードの行を
すべて削除してから入れ直します。

```sql
DELETE FROM dash_osm_buildings WHERE city_code IN (SELECT DISTINCT city_code FROM _decoded);
```

地域の抽出は境界の外側へ少しはみ出します。
関東の抽出には、いわき市の南端にある建物が数百件だけ含まれます。
その数百件が「いわき市の入力」と見なされ、直前に東北の抽出から入れた 14 万件が削除されます。

影響を受けた 6 都市は、すべて自分より後に読み込む地域の県と接しています。

| 都市 | 自分の地域（読む順） | 接している県の地域（読む順） |
|---|---|---|
| いわき市 | tohoku (2) | 茨城県 = kanto (3) |
| 白河市 | tohoku (2) | 栃木県 = kanto (3) |
| 秩父市 | kanto (3) | 山梨県・長野県 = chubu (4) |
| 奥多摩町 | kanto (3) | 山梨県 = chubu (4) |
| 相模原市 | kanto (3) | 山梨県 = chubu (4) |
| 箱根町 | kanto (3) | 静岡県 = chubu (4) |

## 直し方

入れ替えの単位を、市区町村から「どの地域の抽出から来たか」に変えます。

### テーブル

`dash_osm_buildings` に `source_region` 列を足します。
`dash_city_master.region` は東北や北陸といった 10 区分で、Geofabrik の 8 区分とは別物です。
読み違えを避けるため、同じ名前にしません。

```sql
ALTER TABLE dash_osm_buildings ADD COLUMN IF NOT EXISTS source_region TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS dash_osm_buildings_osm_uidx
  ON dash_osm_buildings (osm_type, osm_id);
CREATE INDEX IF NOT EXISTS dash_osm_buildings_source_region_idx
  ON dash_osm_buildings (source_region);
```

一意の索引を作る前に、既存の行に `(osm_type, osm_id)` の重複がないことを確認します。
重複があれば、`id` の小さいほうを残して消してから索引を作ります。

### 読み込み

`load_osm_buildings.py` に必須の引数 `--region` を足します。
値は Geofabrik の地域名（`tohoku`、`kanto` など）で、`source_region` にそのまま入ります。
削除と挿入を関数に切り出し、試験から呼べるようにします。

```sql
DELETE FROM dash_osm_buildings WHERE source_region = %s;

INSERT INTO dash_osm_buildings (city_code, osm_type, osm_id, geom, source_region)
SELECT city_code, osm_type, osm_id, geom, %s FROM _decoded
ON CONFLICT (osm_type, osm_id) DO UPDATE SET
  city_code = EXCLUDED.city_code,
  geom = EXCLUDED.geom,
  source_region = EXCLUDED.source_region,
  fetched_at = now();
```

関東の読み込みは、関東から来た行だけを消します。
東北が入れたいわき市の行は残ります。

県境の建物は両方の抽出に現れます。
`osm_type` と `osm_id` が同じなので 1 行にまとまり、所属は後から読んだ地域に移ります。
どちらの地域が持っていても、翌週にその地域が消して入れ直すため、行は毎週更新されます。

`run_batch.sh` は地域名を `--region "$r"` で渡します。

### 既存の行の移行

既存の行は `source_region` が空です。
初回の読み込みで `ON CONFLICT ... DO UPDATE` により地域名が入ります。

8 地域を 1 周したあとも空のまま残る行は、どの抽出にも現れなかった古い行です。
これは次の 1 文で 1 回だけ片付けます。

```sql
DELETE FROM dash_osm_buildings WHERE source_region IS NULL;
```

`run_batch.sh` には入れません。
一部の地域だけを指定して走らせたときに、読まなかった地域の行まで消えるからです。

## 試験

`tests/test_load_osm_buildings.py` に 3 件足します。
いずれも先に書いて失敗することを確かめてから、実装を入れます。

1. 地域 1 で A 市の建物を 100 件入れ、地域 2 の入力に A 市の建物を 1 件だけ含める。
   読み込み後も A 市の行が 100 件あること。これが今回の不具合の再現です。
2. 同じ `osm_type` と `osm_id` の建物を 2 つの地域の入力に入れ、行が 1 つになること。
3. 同じ地域を 2 回読み、1 回目にあって 2 回目にない建物が残らないこと。

手元の PostGIS に `DASH_TEST_DATABASE_URL` を向けて実行します。
既存の 6 件が通ることも確かめます。

## 反映の手順

1. 実行前に、6 都市の値と全国の率を控える
2. `(osm_type, osm_id)` の重複がないことを確認する
3. `sql/schema.sql` を適用する
4. tohoku、kanto、chubu をこの順で取得して読み込む
5. `compute_stats.py` を走らせ、集計と履歴を更新する
6. 6 都市の値と全国の率を確認する

4 の所要時間は kanto が支配的で、合わせて 1 時間から 1 時間半の見込みです。
既存の設計文書に、日曜 01:00 の定期実行とぶつけないことと、
06:00 から 06:35 の間を跨がないことが書かれています。
この 2 つを外した時刻に始めます。

5 を走らせるまで表示中の数字は変わらないため、作業中に画面が壊れて見えることはありません。
途中で失敗しても、次の日曜の定期実行が全地域を読み直すため、元に戻す手順は要りません。

## この設計で直らないもの

`compute_stats.py` の `target_cities` は `dash_osm_buildings` に行がある都市だけを対象にします。
OSM の建物が 1 件も無い都市は集計の対象から外れ、前回の値が残ります。
これは今回の不具合とは別で、ここでは触りません。

率が 1 パーセントから 5 パーセントの 29 都市は、OSM に建物が少ないだけです。
この修正では数字は変わりません。
