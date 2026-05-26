# PLATEAU インポート進捗ダッシュボード — 設計ドキュメント（レビュー用ドラフト）

ステータス: **設計レビュー中**（実装未着手）
最終更新: 2026-05-23

このドキュメントは、PLATEAU 建物データの OSM へのインポート進捗を可視化するダッシュボードの設計案です。
レビューと合意の後に実装フェーズへ移行します。

---

## 1. 目的とスコープ

PLATEAU 建物データの OSM へのトレース／インポート進捗を、運用者・コミュニティが一目で把握できるダッシュボードを構築する。

対象サーバ: 既存の PLATEAU 配信 API が稼働している 本番サーバ（PostGIS + FastAPI が既設、disk 増設済み、RAM ≈1GB）。

### 表示要件

**初期スコープ（3 指標）**
1. **インポート率** — PLATEAU 建物のうち、交差する OSM 建物が存在する割合（PLATEAU を 100 とした充足率）
2. **対象市町村一覧** — PLATEAU 提供都市の一覧と各都市のインポート率・ステータス（OSM wiki の「インポート完了」状態を含む）
3. **全体進捗率** — 全国 PLATEAU 都市を母集団とした全体の充足率（時系列トレンド含む）

**スコープ外（将来拡張）**
- **主なインポート作業者一覧** — 当面はスコープ外。将来的に対応したい。
  - 重要な波及効果: 作業者をスコープ外にしたことで **OSM 建物データに user メタ情報が不要**になり、公開 extract で完結する（後述 §3.2 の OAuth 問題が消滅）。
  - 将来実装時の軽量ソース候補: OSM wiki `imports_list` の「実施者」列（都市/メッシュ単位の担当者。建物単位ではないが取得が容易）。建物単位の厳密な作業者集計が必要なら OSM メタ込み extract や履歴 API を別途検討。

### リポジトリ構成（確定: 別リポジトリ）

ダッシュボードは **`rapid_plateau_api` とは別リポジトリ**で実装する（例: `rapid_plateau_dashboard`）。

- 理由: ダッシュボードは PLATEAU データ／OSM extract に対する**読み取り専用の分析・可視化レイヤ**であり、データパイプライン本体（importer / purge / 配信 API）とは関心が分離している。別リポジトリにすることで本体の肥大化を防ぎ、依存・デプロイ・テストを独立させられる。
- 連携点: 同一 本番サーバ の PostgreSQL を参照（`plateau_*` テーブルは read-only、`dash_*` テーブルは本ダッシュボードが所有）。配信は既存 nginx に `/dashboard/` パスを追加する形でも、別ポートでも可。
- 本設計ドキュメントは新リポジトリ作成時にそちらへ移設する（暫定的に `rapid_plateau_api` 内に置いている）。

---

## 2. 指標定義（確定事項）

### 2.1 インポート率

```
インポート率 = (交差する OSM 建物が存在する PLATEAU 建物数) / (PLATEAU 建物総数) × 100
```

- **分母**: PLATEAU の建物 outline のみ。`building:part`（部分）は **除外**する。
  - 理由: part を含めると 1 棟が複数行になり率が歪むため。outline = 実質的な「建物棟数」。
- **交差判定（確定）**: 次のいずれかを満たせば「OSM に存在する」と判定:
  1. PLATEAU 建物の代表点が OSM 建物ポリゴン内にある、**または**
  2. PLATEAU 建物と OSM 建物の面積重複率が 30% を超える
  - 代表点は `ST_PointOnSurface()` を用いる（凹形状で重心がポリゴン外に出るのを避けるため、`ST_Centroid` ではなく `ST_PointOnSurface`）。
- **解釈上の注意**: この率は厳密な「PLATEAU からの取り込み率」ではなく「**OSM に建物が表現されている率**」である。PLATEAU 提供以前から OSM に存在した建物も交差判定で真になる。指標としては要件通りだが、ダッシュボード上にこの注記を表示する。

判定 SQL（概念）:
```sql
-- city 単位。p = PLATEAU outline, o = OSM building
SELECT
  COUNT(*) FILTER (WHERE EXISTS (
    SELECT 1 FROM dash_osm_buildings o
    WHERE o.city_code = p.city_code
      AND o.geom && p.geom                      -- bbox 事前絞り込み（GiST index）
      AND (
        ST_Contains(o.geom, ST_PointOnSurface(p.geom))
        OR ST_Area(ST_Intersection(p.geom, o.geom)) / NULLIF(ST_Area(p.geom),0) > 0.30
      )
  )) AS intersecting,
  COUNT(*) AS plateau_total
FROM plateau_buildings p
WHERE p.city_code = :city AND p.building_part IS NULL;   -- outline のみ
```

### 2.2 全体進捗率の母集団（確定・出典確定）

**PLATEAU 提供の全国都市**を母集団とする。

- 母集団 = 国交省 PLATEAU が 3D 都市モデル（建築物データ）を提供している全国の市区町村。
- **権威ある出典（確定）**: 国交省 PLATEAU の公式「整備都市の属性リスト」Excel（`attributedata_2025`）。
  - 入手元: PLATEAU ポータル（https://front.geospatial.jp/plateau_portal_site/ ）→ G空間情報センター（CKAN）。
  - 直リンク例: `https://gic-plateau.s3.ap-northeast-1.amazonaws.com/doc/attributedata_2025.zip`
  - 構造: `V3/V4/V5建築物` シート（仕様版別）。**転置レイアウト**で都市が列、行は属性。
    - row3 = 市区町村コード（5桁）、row4 = 地方、row5 = 都道府県、row6 = 市区町村名、row7-10 = 建築物 LOD1〜4 作成範囲（面積・棟数）。
  - 抽出結果（2025 年度時点）: **ユニーク 306 都市**（V4=201, V5=105, V3=1, 重複 1）。地方別: 関東133 / 中部60 / 近畿28 / 九州27 / 中国20 / 東北12 / 北陸11 / 四国11 / 北海道3 / 沖縄1。
  - 抽出スクリプトと CSV: `~/git/plateau_attributedata/`（`plateau_city_master_2025.csv`）。
- **自前 DB との突合（2026-05-23 時点）**:
  - 全国 PLATEAU 都市: 306 / 自前 DB 取込済: 144 / **未取込: 162** / DB にあってマスタに無い: 0（完全整合）。
  - → 都市カバレッジ進捗 ≒ **144 / 306 ≈ 47%**。
- 各都市のステータス（2 軸で管理）:
  - **取込状態**（自前 DB 基準）: `未取込` / `取込済`
  - **OSM インポート状態**（OSM wiki `imports_list` 基準、後述 §3.4）: `未着手` / `作業中` / `完了`（+ `検証済`）
  - 加えて定量指標として **インポート率**（§2.1）を併記。
- 全体進捗率は複数を併記:
  - **建物加重**: Σ(交差 PLATEAU 建物) / Σ(全 PLATEAU 建物)
  - **都市加重（取込）**: 取込済都市数 / 306
  - **都市加重（OSM完了）**: wiki で「完了」の都市数 / 306

---

## 3. データソース

| データ | 出典 | 状態 |
|---|---|---|
| PLATEAU 建物 | 自前 PostGIS（`plateau_buildings` 他） | ✅ 既存 |
| PLATEAU 対応エリア（凸包） | `plateau_coverage`（MatView） | ✅ 既存 |
| OSM 建物（ジオメトリ） | **県別/地域 extract を osmium でフィルタ**（確定。公開 extract で可） | 🆕 要構築 |
| 全国 PLATEAU 都市マスタ | **`attributedata_2025` Excel（PLATEAU 公式）→ 306 都市**（確定） | ✅ 抽出済 |
| OSM インポート完了ステータス | **OSM wiki `imports_list`**（完了日・実施者・検証済） | 🆕 要構築 |
| city_code → 名称/都道府県 | 上記マスタ Excel（row3/5/6）で取得済 | ✅ 生成済 |
| OSM 作業者メタ（user/uid） | （スコープ外・将来） | ⏸ 将来 |

### 3.1 OSM データ取得方式（確定: 県別 extract + osmium）

RAM ≈1GB の制約上、全国 OSM を osm2pgsql で常時保持するのは非現実的。
**osmium はストリーミング処理で低メモリ**なので、extract から建物だけを抽出する方式を採る。

フロー（**PoC で本番機実測のうえ確定。§11 参照**）:
```
1. Geofabrik 等から日本の地域別/県別 .osm.pbf を取得
2. osmium export でポリゴン化しつつ building 行のみ grep 抽出（低RAM）
     osmium export region.osm.pbf --add-unique-id=type_id \
       --index-type=sparse_file_array --geometry-types=polygon -f geojsonseq -o - \
       | grep '"building":' > buildings.geojsonseq
3. ogr2ogr / COPY で PostGIS の dash_osm_buildings へロード
     （feature id は 'a<num>' 形式: num 偶数=way, 奇数=relation。osm_type/osm_id にデコード）
4. 行政界 or coverage ポリゴンとの空間結合で city_code を付与
```

> ⚠️ **`osmium tags-filter nwr/building` は本番機(≈1GB)で使用しないこと**。PoC 実測(§11)で、最小 region(Shikoku)ですら tags-filter は MemAvailable を 1.8MB まで枯渇させ swap を +1.5GB 消費し OOM 寸前になった（稼働中の postgres/API を巻き込むリスク）。代わりに上記の `osmium export`（ディスク index）→ `grep` で building 行を抽出する方式が安全（最大 region の Kanto でも MemAvailable 482MB を維持、§11）。

メモリ対策:
- `osmium export --index-type=sparse_file_array`（ノード座標をディスクに保持、RAM 数百MB で完了。本番機で実測 RSS 332–701MB）。
- building 抽出は **tags-filter ではなく export 出力の `grep '"building":'`**（ストリーミングで追加 RAM ほぼゼロ）。`grep` は key `building` を拾い `building:part` 単独 way は除外する（= OSM 建物 outline。意図通り）。
- 一時 pbf / geojsonseq は処理後に削除（disk に十分な空きあり）。

> **作業者メタ不要に**: 作業者一覧を初期スコープ外にしたため、OSM 建物に user/uid は不要。
> **公開 extract（GDPR でメタ除去済）で十分**。OAuth 認証（Geofabrik internal）も Overpass 補完も不要になり、取得が大幅に簡素化された。

### 3.2 全国 PLATEAU 都市マスタ（確定・抽出済）

§2.2 参照。`attributedata_2025` Excel の `V3/V4/V5建築物` シートから **306 都市**を抽出済み。
- 取得カラム: city_code・都道府県・地方・市区町村名・建築物 LOD・仕様版。
- 週次バッチで Excel（zip）を再取得して差分更新する（年度更新・追加都市に追従）。
- `dash_city_master` テーブルへ upsert（§4.1）。

### 3.3 OSM wiki によるインポート完了ステータス（新規）

OSM wiki の 2 ページを参照し、各都市の「公式なインポート完了」状態を取り込む。

- **`JA:MLIT_PLATEAU/imports_list`**（https://wiki.openstreetmap.org/wiki/JA:MLIT_PLATEAU/imports_list ）
  - 構造: 市区町村ごとのセクション（見出しに「全メッシュ YYYY-MM-DD にインポート完了」等）+ 表（3次メッシュコード / インポート実施日 / 実施者 / 備考）。
  - ステータス: 見出しテキスト（完了日・妥当性検査終了）+ 備考列（`検証済`/`追加+更新`/`追加のみ`）。
  - 現状 **約 27 市区町村**が記載（完了/作業中）。
  - ⚠️ **city_code を持たない**（市区町村名 + メッシュコード）→ マスタの名称で name→code マッピングが必要。メッシュコードから空間的に city を引くことも可能（補助）。
  - パース: MediaWiki マークアップ。MediaWiki API（`action=parse`）or wikitext 取得で取得し、見出し（都市名・完了日）と表（実施者・備考）を抽出。
- **`JA:MLIT_PLATEAU/imports_outline`**（ワークフロー定義）
  - 「**完了**」の定義 = 「進捗管理テーブル（imports_list）に完了日が記載される」時点（公式慣行）。
  - インポート対象タグ: `building` / `height` / `building:levels` / `ele` / `addr:full`（PLATEAU 側優先）。インポート率や交差判定の解釈の裏付けになる。
  - → ダッシュボードの「完了」ステータスは **wiki の完了日**を権威ソースとし、計算上の **インポート率**は定量補助指標として併記する。

> 補足: imports_list の「実施者」列は将来の「作業者一覧」（スコープ外）の軽量ソースになりうる（都市/メッシュ単位）。

---

## 4. データモデル

集計は**週次バッチで実行し、ダッシュボードは集計済みテーブルを読むだけ**にする（重い空間結合をリクエスト毎に走らせない）。

```sql
-- 4.1 全国 PLATEAU 都市マスタ（attributedata_2025 Excel + OSM wiki から構築）
CREATE TABLE dash_city_master (
  city_code           TEXT PRIMARY KEY,         -- Excel row3（5桁）
  city_name           TEXT,                     -- Excel row6
  prefecture          TEXT,                     -- Excel row5
  region              TEXT,                     -- Excel row4（地方）
  building_lods       TEXT,                     -- 例 '1+2+3'（Excel row7-10）
  spec_versions       TEXT,                     -- 'V4' / 'V5' 等
  plateau_provided    BOOLEAN DEFAULT TRUE,     -- Excel に建築物データあり
  in_local_db         BOOLEAN,                  -- 自前 PostGIS に取込済みか
  -- OSM wiki imports_list 由来
  osm_import_status   TEXT,                     -- not_started / in_progress / done
  osm_import_date     DATE,                     -- wiki の完了日
  osm_validated       BOOLEAN,                  -- 備考『検証済』
  boundary_geom       GEOMETRY(MultiPolygon, 4326),  -- 行政界（§9参照。当面は coverage 凸包で代替可）
  updated_at          TIMESTAMPTZ DEFAULT now()
);

-- 4.2 OSM 建物キャッシュ（公開 extract から投入。対応エリア内に限定）
--     ※ 作業者一覧スコープ外のため user/uid は保持しない（将来拡張時に追加）
CREATE TABLE dash_osm_buildings (
  id            BIGSERIAL PRIMARY KEY,
  city_code     TEXT,
  osm_type      CHAR(1),            -- 'w' / 'r'
  osm_id        BIGINT,
  geom          GEOMETRY(Geometry, 4326),
  fetched_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON dash_osm_buildings USING GIST (geom);
CREATE INDEX ON dash_osm_buildings (city_code);

-- 4.3 都市別 統計スナップショット
CREATE TABLE dash_city_stats (
  city_code          TEXT PRIMARY KEY REFERENCES dash_city_master(city_code),
  plateau_count      INTEGER,        -- outline のみ
  osm_count          INTEGER,        -- 対応エリア内 OSM 建物数
  intersecting_count INTEGER,        -- 交差判定で真の PLATEAU 建物数
  import_rate        NUMERIC(5,2),   -- intersecting / plateau × 100
  computed_at        TIMESTAMPTZ
);

-- 4.4 全体進捗の時系列（トレンドグラフ用）
CREATE TABLE dash_progress_history (
  computed_at        TIMESTAMPTZ PRIMARY KEY,
  total_plateau      BIGINT,
  total_intersecting BIGINT,
  overall_rate       NUMERIC(5,2),   -- 建物加重
  cities_total       INTEGER,        -- 母集団（全国 PLATEAU 都市 = 306）
  cities_in_db       INTEGER,        -- 取込済
  cities_osm_done    INTEGER         -- wiki で『完了』の都市数
);

-- 4.5 作業者統計（★スコープ外・将来拡張。実装時に有効化）
-- CREATE TABLE dash_contributor_stats (
--   computed_at TIMESTAMPTZ, city_code TEXT, osm_user TEXT, osm_uid BIGINT,
--   building_count INTEGER, PRIMARY KEY (computed_at, city_code, osm_uid)
-- );
```

---

## 5. 集計パイプライン（バッチ）

**週次** cron もしくは手動起動の Python スクリプト（既存 CLI ツール群と同じスタイル）。

```
Phase 0: 都市マスタ更新
  - attributedata_2025 zip 取得 → V3/V4/V5建築物 から 306 都市抽出 → dash_city_master upsert
  - 自前 DB の city_code 突合で in_local_db を更新
Phase 1: OSM wiki ステータス取得（§3.3）
  - imports_list を MediaWiki API で取得 → 都市名・完了日・検証済を抽出
  - name→code マッピングで dash_city_master.osm_import_* を更新
Phase 2: OSM extract 取得・建物抽出（§3.1）
  - 公開 県/地域 pbf → osmium filter（building）→ geojsonseq
Phase 3: PostGIS ロード
  - dash_osm_buildings へ COPY、行政界/coverage で city_code 付与
Phase 4: 都市別 統計
  - §2.1 の交差判定 SQL を city 単位で実行 → dash_city_stats
Phase 5: 全体ロールアップ
  - 母集団 = dash_city_master 全 306 → dash_progress_history に追記
Phase 6: （任意）ダッシュ用 MatView リフレッシュ
（※ 作業者集計はスコープ外。将来 Phase として追加）
```

実行頻度（**確定: 週 1 回**）: OSM extract の鮮度と負荷のバランスで週次。`dash_progress_history` は実行毎に 1 行追加されトレンドになる。

冪等性・安全性:
- 既存の CLI ツール（importer/purge）と同様、`advisory_lock` で多重起動を防止
- 一時ファイルは `trap` で確実に削除（disk 保護。再 import バッチの教訓を踏襲）
- 本番 DB への書き込みは dash_* テーブルのみ（PLATEAU 本体テーブルは読み取り専用）

---

## 6. API（既存 FastAPI を拡張）

既存 `osmfj_plateau_api.py` にダッシュボード用エンドポイントを追加。dash_* テーブルを返すだけなので軽量・高速。

| エンドポイント | 返却 |
|---|---|
| `GET /api/dashboard/summary` | 全体進捗（最新 + トレンド、306母集団）|
| `GET /api/dashboard/cities` | 対象市町村一覧（率・取込状態・OSM完了状態込み）|
| `GET /api/dashboard/cities/{city_code}` | 単一都市詳細 |
| `GET /api/dashboard/cities.geojson` | 地図描画用（都市ポリゴン + 率で色分け）|
| `GET /api/dashboard/contributors` | （スコープ外・将来）|

---

## 7. フロントエンド

- **静的 HTML + JS** を既存 nginx 配信に追加（`/dashboard/`）。
  - 地図: Leaflet で都市ポリゴンをインポート率で色分け（コロプレス）
  - グラフ: Chart.js で全体進捗トレンド + 作業者ランキング棒グラフ
  - 表: 対象市町村一覧（ソート・フィルタ）
- 既に静的 + API を同一オリジンで配信しているため CORS 不要・親和性高い。
- Grafana は別プロセス + RAM を要し ≈1GB では非推奨。

---

## 8. 運用・リソース上の考慮

| 項目 | 方針 |
|---|---|
| RAM ≈1GB | 全国 osm2pgsql は不採用。osmium ストリーミング（ディスク index）で回避 |
| disk に十分な空き | extract 一時ファイル + dash_osm_buildings を吸収可能。処理後に一時 pbf 削除 |
| バッチ多重起動 | advisory_lock で防止 |
| 一時ファイル | trap で確実削除（再 import バッチの disk 事故を踏まえる）|
| extract 更新 | 週次想定。差分更新（osmupdate）は将来検討 |
| 本体 DB 保護 | dash_* のみ書き込み、PLATEAU 本体は read-only |

---

## 9. 決定事項と残課題

### 決定済み
- ✅ OSM データ: **公開の県別/地域 extract + osmium**（作業者スコープ外のため user メタ不要）。
- ✅ 交差判定: **代表点 in ポリゴン or 面積重複 >30%**（`ST_PointOnSurface`）。
- ✅ 全国都市マスタ: **`attributedata_2025` Excel → 306 都市**（抽出済）。
- ✅ 進捗母集団: **全国 PLATEAU 都市 306**。
- ✅ 完了ステータス: **OSM wiki `imports_list` の完了日**を権威ソースに採用。
- ✅ バッチ頻度: **週次**。
- ✅ 作業者一覧: **初期スコープ外**（将来拡張）。

### 残課題（レビュー/実装で詰める）
1. **対応エリアジオメトリ（行政界）**: ユーザ希望は**行政界ポリゴン**。出典候補 = 国土数値情報「行政区域データ（N03）」（city_code で join 可）。導入まで暫定で coverage 凸包を代替に使い、後で差し替える。→ N03 取得・整形のタスク化が必要。
2. **「完了」判定**: wiki 完了日を primary とする。wiki 未記載だが率が高い都市の表示ラベルをどうするか（例: `OSM完了(wiki)` と `率ベース` を分けて表示）。
3. **wiki name→city_code マッピング**: imports_list は名称ベース。マスタ名称との表記揺れ（市/区/町村、旧字体）への対処。メッシュコード経由の空間引きを補助に。
4. **extract の単位と更新**: 差分更新（osmupdate）は将来。処理単位は **Geofabrik の地域別 region（全国 8 本）で確定** — PoC 本番機実測(§11)で、最大 region(Kanto)でも export→grep なら MemAvailable 482MB を維持し安全に処理できたため、県別への細分化は不要。`japan-latest` 1 本でも export 自体は回る可能性が高いが、region 別の方が再実行・並列・障害分離に有利。
5. **part 充足の別指標**: outline 率で確定済み。part 単位の充足を見たい需要があれば別指標として追加。
6. **交差判定のキャリブレーション**（PoC §11-5 で判明）: 現行基準（代表点 in ポリゴン OR 面積>30%）は検証済完了都市でも形状ズレ分を取りこぼす（新座で 10% ギャップの 2/3）。**面積閾値の引き下げ / 代表点の距離トレランス / 逆方向(OSM→PLATEAU)併用**を Phase 4 実装時に評価する。

---

## 10. 実装フェーズ計画（合意後）

1. **Phase 1 — PoC** ✅ 実施済 (2026-05-23): 3 都市で「公開 extract→osmium→PostGIS→交差率算出」を通し、数値妥当性・osmium のメモリ/時間を実測（結果は下記）。
2. **Phase 2 — マスタ & データモデル**: `attributedata_2025` 取込スクリプト + wiki パーサ + dash_* テーブル作成。
3. **Phase 3 — 集計バッチ** ✅ 実装済 (2026-05-23): `osmium/fetch_region_buildings.sh`（region pbf → export\|grep）、`ingest/load_osm_buildings.py`（geojsonseq → ogr2ogr staging → coverage で city_code 付与 → dash_osm_buildings、冪等）、`ingest/compute_stats.py`（最適化交差判定 → dash_city_stats + dash_progress_history、advisory_lock）、`run_batch.sh`（flock + trap で全 Phase を統括）。Shikoku region で実機 end-to-end 検証済み（§11）。
4. **Phase 4 — API**: FastAPI にダッシュボードエンドポイント追加 + テスト。
5. **Phase 5 — フロントエンド**: 静的ダッシュボード（地図コロプレス・トレンド・一覧表）。
6. **Phase 6 — 運用化**: 週次バッチのスケジューリング、監視。行政界(N03)への差し替え。

---

## 11. Phase 1 PoC 実施結果 (2026-05-23)

3 都市で end-to-end を検証。osmium はローカルで実行し、OSM 建物を PostGIS `dash_osm_buildings` に投入して交差率を算出。

### 検証した交差率

| 都市 | outline | 交差 | 率 | OSM wiki 状態 |
|---|---|---|---|---|
| 埼玉県新座市 (11230) | 58,804 | 52,883 | **89.93%** | 完了+検証済 |
| 東京都奥多摩町 (13308) | 8,505 | 2,143 | **25.20%** | 未完了（山間） |
| 高知県高知市 (39201) | 78,384 | 28,585 | **36.47%** | OSMトレース途上 |

→ 完了+検証済 ~90% / 未完了 25–36% で**指標は妥当に弁別**。`ST_PointOnSurface` + 面積>30% の §2.1 定義で実装可能と確認。

### part 除外の確認（高知）

outline のみ 78,384 棟(率36.47%) に対し part 込みは 119,001 行(率36.23%)。率はほぼ同じ（part は親 outline の判定を継承）だが**棟数は +52% 膨張**。「棟数」基準は **outline 除外が正**（§2.1 の判断を裏付け）。

### 確定した知見・設計反映事項

1. **公開 extract で完結**: Geofabrik 県/地域 pbf のみで交差率を算出できた。作業者スコープ外＝user メタ不要の前提を実証。
2. **osmium export は低メモリ**: `--index-type=sparse_file_array` で最大 region(Kanto 445MB, OSM建物約600万) を peak ~639MB / 25s で処理。region 規模で採用妥当。
3. **osmium tags-filter は本番機で危険、export→grep に変更（本番機実測で確定）**: 本番機(≈1GB)で実測した結果、`tags-filter nwr/building` は最小 region(Shikoku)ですら MemAvailable を 1.8MB まで枯渇させ swap +1.5GB を消費（OOM 寸前）。一方 `osmium export`(disk index)→`grep '"building":'` は最大 region(Kanto, 建物約600万)でも MemAvailable 482MB を維持し安全。**本番フロー(§3.1)を export→grep に変更済み**。詳細は下記「本番機メモリ実測」。
4. **交差判定 SQL の最適化 — ✅ 適用済（劇的改善）**: `ST_Area(ST_Intersection())` を避け、**代表点 in ポリゴンを先に安価判定（EXISTS）→ false の行だけ OR 短絡で面積判定**する段階化を `compute_stats.py` に実装。高知 outline 78,384 棟が **約7m49s → 3.1s**（PoC 数値と完全一致）。全国 outline 約1,200万棟でも単純外挿 ~8-9 分で週次バッチに十分。
5. **判定基準のキャリブレーション（要検討）**: 検証済完了の新座でも 10% ギャップがあり、その 2/3 は「近傍に OSM 建物はあるが点 in も 面積>30% も満たさない」形状ズレだった。**面積閾値の引き下げ / 距離トレランス / 逆方向(OSM→PLATEAU)併用**を §9 残課題に追加。
6. **本番サーバ のツール導入済み**: `osmium-tool 1.16.0` と `gdal-bin(ogr2ogr) 3.8.4` を導入済み（2026-05-23）。venv に psycopg2 既存（shapely/GDAL python binding は無し）。OSM 建物の投入は ogr2ogr 経路で可。

### 本番機メモリ実測（2026-05-23, ≈1GB / swap）

`/usr/bin/time -v` の Max RSS と、`/proc/meminfo` を 1s サンプリングした peak swap / min MemAvailable。

| region | 手法 | Max RSS | swap delta | min MemAvailable | elapsed | building features | 判定 |
|---|---|---|---|---|---|---|---|
| Shikoku(83MB) | `tags-filter nwr/building` | 742MB | **+1.5GB** | **1.8MB** | 0:30 | 593,338 | ❌ OOM寸前 |
| Shikoku(83MB) | `export \| grep building` | 332MB | +11MB | 382MB | 0:08 | 592,958 | ✅ 安全 |
| Kanto(446MB, 最大) | `export \| grep building` | 701MB | +258MB | 482MB | 2:55 | 5,967,905 | ✅ 安全 |

→ **tags-filter は不採用、export→grep を本番フローに採用**（§3.1 更新済み）。export の Max RSS は region サイズにあまり依存しない（disk index のため）。building 件数は両手法でほぼ一致（grep は key `building` を拾い `building:part` 単独は除外）。

### PoC 詳細記録

数値・コマンド・メモリログの全量は [`POC_RESULTS.md`](POC_RESULTS.md) に保存。`dash_osm_buildings` には PoC 3 都市分を投入済み（staging は削除済み）。

## 12. Phase 3 集計バッチ 実機検証 (2026-05-23)

`run_batch.sh --regions shikoku` を本番機で end-to-end 実行（fetch → export\|grep → coverage で city_code 付与 → dash_osm_buildings 投入 → compute_stats）。

- Shikoku region: OSM 建物 592,956 抽出 → coverage 内 165,527 を自前 DB の 4 都市に投入。
- 交差率（dash_city_stats）:

| city_code | 都市 | plateau(outline) | osm | rate | 検証 |
|---|---|---|---|---|---|
| 37206 | さぬき市 | 49,926 | 50,280 | **99.89%** | wiki 完了 → ~100%。**指標を独立検証** |
| 36201 | 徳島市 | 169,664 | 79,947 | 42.36% | |
| 39201 | 高知市 | 78,384 | 34,465 | **36.46%** | PoC(36.47%) と一致 |
| 39386 | （高知県町村）| 14,610 | 835 | 5.07% | OSM 建物希少 |

- **city_code 付与は coverage(凸包)で実用上十分**: 高知は bbox 法(36.47%)と全 region coverage 法(36.46%)が一致。N03 行政界への差し替えは将来（§9-1）。
- **性能**: 6 都市の compute_stats 合計 ~12.5s（最適化済交差判定）。
- 全体ロールアップ（dash_progress_history）も生成。全国母集団 306・取込済 144・wiki 完了 25 を併記。
- 残課題: 全 8 region の本番一括投入（dash_osm_buildings を全国分で満たす）と、§9-6 の判定キャリブレーション評価。

---

## 13. 全国一括投入 実績 (2026-05-24)

`run_batch.sh`（全 8 region）を本番機でデタッチ実行し、全国データを投入。

### 結果
- **建物カバレッジ率（全国・建物加重）= 49.08%**（6,289,593 / 12,814,582 棟、計測 144 都市）。
- `dash_osm_buildings`: 144 都市 / 7,558,125 棟。`dash_progress_history` に全国スナップショット 1 件。
- region 別投入: 東北 44万(5都市)・関東 267万(67)・中部 249万(50)・近畿 133万(13)・中国 44万(8)・四国 16.5万(4)・九州 15.6万(6)。北海道・沖縄は取込都市0。
- 妥当性: wiki 完了都市が高率（さぬき 99.9・備前 99.7・松浦 99.6・波佐見 99.9・玉名 80.6）、高知 36.5%（PoC 一致）。

### Disk 実測（週次バッチの所要容量 — 運用の指標）
| 項目 | 値 |
|---|---|
| 一時ピーク使用（1 region の geojsonseq＋ogr2ogr staging） | ~5 GB |
| 永続増加（`dash_osm_buildings`） | +2.1 GB（7.56M 棟）|
| **必要空き目安** | **永続 ~2.1GB ＋ 一時 ~5GB → 8-10GB 確保推奨** |

`run_batch.sh` は region ごとに `[disk] ...` 行を出力し、毎回自己記録する。

### 所要時間
- 投入（全 region）: 約 **2時間20分**。ボトルネックは `load_osm_buildings` の coverage 空間結合（建物ごとの `ST_Contains` LATERAL）。大 region（関東 約600万棟）が支配的。
- 統計（`compute_stats`）: 約 10–15 分。大都市が重い（広島市 345,776 棟で 142s）。
- → 週次バッチ全体 **約3時間弱**。無人実行（nohup デタッチ）前提。**06:00–06:35 JST（apt-daily-upgrade による postgres 再起動）を跨がない時刻に起動**すること。

### 判明したバグと修正
- **不正ジオメトリ**（OSM 建物の自己交差等）で `ST_Intersection` が GEOS TopologyException を投げ、`compute_stats` が異常終了（初回）。
- 修正: ①`compute_stats` の面積判定で両ジオメトリを `ST_MakeValid`、②都市単位 try/except でスキップ継続、③`load_osm_buildings` は投入時に `ST_MakeValid` で valid 格納。→ 再実行でスキップ 0・全 144 都市完了。

### 高速化（計測と適用 2026-05-24）
- 各工程に所要時間ログを追加（`fetch_region_buildings.sh`: download / export|grep、`load_osm_buildings.py`: ogr2ogr / coverage-join / insert）。
- 計測（Kanto 597万棟）: download 63s ・ export|grep 225s ・ **ogr2ogr 全列投入 820s（staging 2,676MB・1,377列!）**。真のボトルネックは coverage 結合より **ogr2ogr の幅広 staging 投入**だった（OSM 建物に 1,377 種のタグ列）。
- **対策（適用済）**: `ogr2ogr -select id`（id＋geometry のみ、1,377→3列）。→ **820s→277s（約3倍速）、staging 2,676→1,535MB（一時 disk ほぼ半減）**。残 277s はジオメトリ投入の下限。
- 見込み: 投入全体で概ね **1.4–1.6倍速 ＋ 一時 disk 半減**。

### 残課題
- さらなる高速化: coverage 結合の index 化 / 差分更新（osmupdate で変更分のみ反映）。
- `compute_stats` の大都市（>30万棟）面積判定の最適化。

---

## 付録 A: 既存資産との関係

- PLATEAU 本体テーブル（`plateau_buildings` / `plateau_building_nodes` / `plateau_coverage`）は本ダッシュボードの PLATEAU 側ソース。
- `plateau_buildings.building_part` で outline / part を判別（再 import で導入済み）。
- city_code は全テーブルで共通キー。
- 配信 API・静的配信・nginx は既存構成を再利用。

## 付録 B: 外部データソース一覧

| ソース | URL | 用途 |
|---|---|---|
| PLATEAU ポータル | https://front.geospatial.jp/plateau_portal_site/ | データカタログの起点 |
| 整備都市の属性リスト | `https://gic-plateau.s3.ap-northeast-1.amazonaws.com/doc/attributedata_2025.zip` | **全国都市マスタ（306）** |
| G空間情報センター CKAN | https://www.geospatial.jp/ckan/dataset/ | データセット API |
| OSM wiki imports_list | https://wiki.openstreetmap.org/wiki/JA:MLIT_PLATEAU/imports_list | **インポート完了ステータス** |
| OSM wiki imports_outline | https://wiki.openstreetmap.org/wiki/JA:MLIT_PLATEAU/imports_outline | 「完了」定義・対象タグ |
| OSM 建物 extract | Geofabrik 等（公開 pbf） | OSM 建物ジオメトリ |
| 行政区域データ N03 | 国土数値情報（要取得） | 都市境界ポリゴン（将来差替） |

## 付録 C: 都市マスタ抽出メモ（2025 年度）

- `attributedata_2025_v3/v4/v5.xlsx` の `V*建築物` シートは転置構造（都市=列）。
  - row3=city_code, row4=地方, row5=都道府県, row6=市区町村名, row7-10=建築物LOD1-4 作成範囲。
- 抽出結果: ユニーク 306 都市（V4=201 / V5=105 / V3=1 / 重複1=美濃加茂市21211）。
- 自前 DB 突合（2026-05-23）: マスタ306 / 取込済144 / 未取込162 / マスタ外0。
- 生成物: `~/git/plateau_attributedata/plateau_city_master_2025.csv`（ローカル）。
