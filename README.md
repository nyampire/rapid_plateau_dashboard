# rapid_plateau_dashboard

PLATEAU 建物データの OSM へのインポート進捗を可視化するダッシュボード。
PLATEAU 配信 API（PostGIS + FastAPI）と同一の DB を **読み取り専用**で参照し、
集計結果を `dash_*` テーブルに書き込む分析・可視化レイヤ。本体パイプラインとは関心分離。

設計の詳細は [`docs/DESIGN.md`](docs/DESIGN.md)、PoC 実測結果は [`docs/POC_RESULTS.md`](docs/POC_RESULTS.md)。

## 指標（初期スコープ）

1. インポート率 — PLATEAU 建物のうち交差する OSM 建物が存在する割合（outline のみ）
2. 対象市町村一覧 — 各都市の率・取込状態・OSM wiki 完了状態
3. 全体進捗率 — 全国 PLATEAU 都市（306）を母集団とした充足率の時系列

## 構成

```
sql/schema.sql                     dash_* テーブル定義（冪等）
run_batch.sh                       週次バッチ統括（Phase 0-5 / flock + trap）

ingest/extract_city_master.py      attributedata_2025 Excel -> 都市マスタ CSV（306都市）
ingest/load_city_master.py         CSV -> dash_city_master（in_local_db を突合）
ingest/parse_wiki_imports.py       OSM wiki imports_list -> dash_city_master.osm_import_*
ingest/load_osm_buildings.py       geojsonseq -> ogr2ogr staging -> 行政界(N03)で city_code 付与（coverage フォールバック）-> dash_osm_buildings
ingest/load_n03_boundaries.py      国土数値情報 N03 -> 政令市ward 集約 -> dash_city_master.boundary_geom（年次・任意）
ingest/compute_stats.py            交差率算出 -> dash_city_stats + dash_progress_history（advisory_lock）

osmium/fetch_region_buildings.sh   Geofabrik region pbf -> osmium export|grep building -> geojsonseq（本番フロー）
osmium/run_city_osmium.sh          1 都市分の OSM 建物抽出（PoC 用）
osmium/measure*.sh                 osmium のメモリ実測スクリプト

api/dashboard_api.py               読み取り専用 FastAPI（/api/dashboard/*。router 相乗り or standalone）
frontend/                          静的ダッシュボード（index.html / app.js / style.css。API 直読 + data.js フォールバック）
frontend/stamp_cache.py            ?v= キャッシュ無効化を内容ハッシュで自動書換（デプロイ前に実行）

data/plateau_city_master_2025.csv  抽出済み都市マスタ
docs/DESIGN.md, docs/POC_RESULTS.md  設計・PoC 実測記録
```

## 使い方

依存: `osmium-tool`, `gdal-bin`(ogr2ogr), Python 3（`openpyxl` は extractor のみ、他は `psycopg2`）。

```bash
# 1. スキーマ適用
psql "$DATABASE_URL" -f sql/schema.sql

# 2. 都市マスタ（Excel から再抽出する場合）
python3 ingest/extract_city_master.py --xlsx-dir <attributedata dir> -o data/plateau_city_master_2025.csv
python3 ingest/load_city_master.py data/plateau_city_master_2025.csv --postgres-url "$DATABASE_URL"

# 3. OSM wiki 完了ステータス
python3 ingest/parse_wiki_imports.py --postgres-url "$DATABASE_URL"   # --dry-run で確認可

# 4. OSM 建物抽出（本番フロー: 低メモリ。詳細 docs/DESIGN.md §3.1）
osmium export region.osm.pbf --add-unique-id=type_id \
  --index-type=sparse_file_array --geometry-types=polygon -f geojsonseq -o - \
  | grep '"building":' > buildings.geojsonseq
```

> ⚠️ OSM 建物抽出に `osmium tags-filter` は使わないこと（低メモリ機（RAM ≈1GB）で OOM 寸前になる。docs/DESIGN.md §11 参照）。

> 任意（差分ダウンロード）: `DASH_PBF_CACHE=<dir>` を設定し `osmupdate`（osmctools）を導入すると、region pbf をそのディレクトリにキャッシュし replication 差分だけ取得して更新する（未設定／未導入なら従来どおりフルダウンロード）。高速化は**ダウンロード段のみ**で、export+load は毎回 region 全体を処理する。docs/DESIGN.md §9-4。

## 開発・テスト

```bash
pip install -r requirements-dev.txt

# 単体テスト（DB 不要）: wiki パーサ / name→code 解決 / DB 接続文字列の組み立て
pytest

# 統合テスト（PostGIS 必須）: compute_stats 交差判定 / load_osm の coverage 付与 / API エンドポイント
createdb dash_test && psql -d dash_test -c "CREATE EXTENSION postgis"
DASH_TEST_DATABASE_URL=postgresql:///dash_test pytest
```

`DASH_TEST_DATABASE_URL` 未設定時は DB 依存テストが自動 skip される（CI でも緑）。

## 週次自動化（デプロイ）

`run_batch.sh` を週次実行する systemd timer のテンプレートを `deploy/` に同梱:

- `deploy/run_weekly.sh` — env 駆動の薄いラッパ（venv 有効化 → `run_batch.sh`）
- `deploy/rapid-plateau-dashboard.service` / `.timer` — oneshot service + 週次 timer（`Persistent=true`）
- `deploy/dashboard-batch.env.example` — `DATABASE_URL` ほか設定例（実値は repo 外に置く）

```bash
# 1. 設定ファイル（DB パスワードを含むので repo 外・600）
sudo cp deploy/dashboard-batch.env.example /etc/rapid-plateau-dashboard.env
sudo chmod 600 /etc/rapid-plateau-dashboard.env   # DATABASE_URL 等を記入
# 2. unit の WorkingDirectory / ExecStart / User を環境に合わせて編集
sudo cp deploy/rapid-plateau-dashboard.service deploy/rapid-plateau-dashboard.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rapid-plateau-dashboard.timer
systemctl list-timers rapid-plateau-dashboard.timer   # 次回実行時刻を確認
journalctl -u rapid-plateau-dashboard.service -f      # ログ
```

> バッチは数時間かかるため、開始時刻は日次メンテナンス（例: パッケージ更新によるサービス再起動）の時間帯を跨がないこと。ホストのタイムゾーンを確認のうえ `OnCalendar` を調整する。単発確認は `sudo systemctl start rapid-plateau-dashboard.service`。

## フロントエンドのデプロイ

静的フロント（`frontend/`）を配信先（nginx の `/dashboard/` など）へ配置する。**配置前に**キャッシュ無効化ハッシュを更新する:

```bash
python3 frontend/stamp_cache.py          # index.html の ?v= を各アセットの内容ハッシュに更新
rsync -av frontend/ <web>/dashboard/     # 静的ファイル配置（--delete は使わない: 親を巻き込まないため）
```

フロントは API（`/api/dashboard/*`）を直読し、不通時のみ同梱 `data.js` にフォールバックする。補助地図は CARTO のダークベースマップを利用。

## データソース

| ソース | 用途 |
|---|---|
| 整備都市の属性リスト（attributedata_2025, PLATEAU 公式） | 全国都市マスタ 306 |
| OSM wiki `JA:MLIT_PLATEAU/imports_list` | インポート完了ステータス |
| OSM 建物 extract（Geofabrik 公開 pbf） | OSM 建物ジオメトリ |
| 国土数値情報 行政区域データ（N03, 国土交通省） | 市区町村の行政界ポリゴン（city_code 付与・地図表示） |
| 自前 PostGIS `plateau_*`（読み取り専用） | PLATEAU 建物・対応エリア（行政界が無い都市のフォールバック） |

## API の DB 接続（読み取り専用）

`api/dashboard_api.py` は参照専用。接続は psycopg2 の read-only セッション（`set_session(readonly=True)`）で、万一にも書き込みできない。接続先は `DASH_DATABASE_URL`（あれば）→ `DATABASE_URL` の順で解決する。

防御を強めるなら、専用の read-only ロールを作成し `DASH_DATABASE_URL` に設定する（[`sql/readonly_role.sql`](sql/readonly_role.sql)。ロール作成は DB 管理者権限が必要）。

## ライセンスと帰属

- **コード**: [MIT License](LICENSE)。
- **OSM 建物データ**: © OpenStreetMap contributors（[ODbL](https://www.openstreetmap.org/copyright)）。
- **地図タイル**: © [CARTO](https://carto.com/attributions) ／ © OpenStreetMap contributors。
- **PLATEAU 建物データ・整備都市マスタ（attributedata_2025）**: 国土交通省 [Project PLATEAU](https://www.mlit.go.jp/plateau/)（各データの利用規約に従う）。
- **インポート完了ステータス**: OSM wiki [`JA:MLIT_PLATEAU/imports_list`](https://wiki.openstreetmap.org/wiki/JA:MLIT_PLATEAU/imports_list)。
- **行政界**: [国土数値情報（行政区域データ N03）](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2025.html) 国土交通省（[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.ja)）。
