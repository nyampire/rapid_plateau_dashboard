# rapid_plateau_dashboard

公開URL: <https://rapid.nyampire.info/dashboard/>

PLATEAUの建物データが、OpenStreetMap（OSM）の建物とどれだけ重なっているかを、都市ごとに集計して表示するダッシュボードです。
PLATEAUの建物をRapidエディタに配信している[rapid_plateau_api](https://github.com/nyampire/rapid_plateau_api)と同じデータベースを使います。
このリポジトリは集計と表示だけを受け持ち、配信のコードには手を入れません。

設計は[`docs/DESIGN.md`](docs/DESIGN.md)、試作時の実測結果は[`docs/POC_RESULTS.md`](docs/POC_RESULTS.md)、画面の設計は[`docs/UI_DESIGN.md`](docs/UI_DESIGN.md)にあります。

## 画面で見られる指標

画面の上部に、全国の値を3つ表示します。

| 指標 | 意味 |
|---|---|
| 建物カバレッジ率 | PLATEAUの建物のうち、OSMの建物と重なるものの割合。前回の集計との差も表示します |
| 都市カバレッジ | PLATEAUが建物データを公開している306都市のうち、データベースに取り込まれていて、建物カバレッジ率を計算できる都市の割合 |
| OSMインポート完了都市数 | OSM wikiで完了と記録された都市と、建物カバレッジ率が90%以上の都市の合計 |

その下に並ぶのは、地方別の集計、市区町村の一覧、建物カバレッジ率で色分けした地図です。
市区町村の一覧は、地方、状態、都市名で絞り込めます。
政令指定都市は、区ごとの建物カバレッジ率も見られます。

建物カバレッジ率の計算では、PLATEAUの建物1棟ごとに、重なるOSMの建物があるかを調べます。
PLATEAUの建物の内側に置いた代表点がOSMの建物の内側にあるか、交差する面積がPLATEAUの建物の30%を超えると、重なりとして数えます。
建物は外形1つを1件として数え、`building:part`に当たる部分は対象外です。
OSMの建物がいつ描かれたかは問わないので、PLATEAUの公開前からOSMにあった建物も重なりに含みます。
指標の詳しい定義の置き場は、画面の「指標の意味と計算方法」と`docs/DESIGN.md`の2章です。

## 仕組み

週に一度、バッチが外部のデータとPLATEAUの建物を突き合わせて建物カバレッジ率を計算し、`dash_*`テーブルに書き込みます。
画面に出るのは、APIを通して読んだ`dash_*`テーブルの値です。

```
 PLATEAU 整備都市の属性リスト ── extract_city_master.py ─┐
 (attributedata_2025)             load_city_master.py  ─┤
                                                         │
 OSM wiki imports_list ───────── parse_wiki_imports.py ──┤
                                                         ├──▶ dash_* テーブル
 国土数値情報 行政区域 (N03) ─── load_n03_boundaries.py ─┤        │
                                                         │        │ 読み取り専用
 Geofabrik の地域別 pbf ── fetch_region_buildings.sh     │        ▼
                           (osmium export | grep)        │   dashboard_api.py
                        ── load_osm_buildings.py ────────┤   (/api/dashboard/*)
                                                         │        │
 PLATEAU の建物 (plateau_* テーブル) ─ compute_stats.py ─┘        ▼
   rapid_plateau_api が取り込んだもの                         frontend/
```

`dash_*`テーブルに書き込むのはバッチだけです。
APIは読み取り専用の接続を使い、`plateau_*`テーブルにも`dash_*`テーブルにも書き込みません。
接続の詳細は「APIのデータベース接続」の節に書きました。

週次のバッチ（`run_batch.sh`）は次の順に処理します。

1. `--csv`を指定したときだけ、都市マスタを読み込み直します。
2. OSM wikiから、インポートが完了した都市を読み込みます。
3. Geofabrikの8地域ごとにpbfを取得し、建物を取り出して`dash_osm_buildings`に入れます。
4. 都市ごと、区ごと、地方ごと、全国の建物カバレッジ率と完了都市数を計算し、履歴に記録します。

バッチは`flock`で二重に起動しないようにしてあり、建物カバレッジ率の計算ではデータベースのadvisory lockも取ります。
作業用のpbfとgeojsonseqは、バッチの終了時に削除する作りです。

## ファイル構成

```
run_batch.sh                       週次バッチの本体
sql/schema.sql                     dash_* テーブルの定義 (何度実行してもよい)
sql/readonly_role.sql              API 用の読み取り専用ロールの作成例

ingest/extract_city_master.py      属性リストの Excel から都市マスタの CSV を作る
ingest/load_city_master.py         都市マスタの CSV を dash_city_master に入れる
ingest/parse_wiki_imports.py       OSM wiki の imports_list から完了状況を読む
ingest/load_osm_buildings.py       OSM の建物を dash_osm_buildings に入れ、行政界で city_code を付ける
ingest/load_n03_boundaries.py      N03 の行政界を dash_city_master に入れる (年に一度、任意)
ingest/compute_stats.py            建物カバレッジ率を計算し、dash_city_stats と dash_progress_history に書く

osmium/fetch_region_buildings.sh   Geofabrik の地域別 pbf から建物を取り出す (バッチで使う)
osmium/run_city_osmium.sh          1 都市分の建物を取り出す (試作用)
osmium/measure*.sh                 osmium のメモリ使用量を測る

api/dashboard_api.py               読み取り専用の API (/api/dashboard/*)
frontend/                          画面 (index.html, app.js, style.css, data.js)
frontend/stamp_cache.py            index.html のキャッシュ無効化用ハッシュを書き換える
frontend/gen_mock_data.py          dash_* テーブルから data.js を作る

data/plateau_city_master_2025.csv  抽出済みの都市マスタ
data/plateau_city_master_extra.csv 属性リストに載っていない都市 (抽出時に追加する)

deploy/                            週次バッチの systemd ユニットのひな形
docs/                              設計と実測の記録
```

都市マスタのCSVは308都市分あります。
都市カバレッジの母数の306は、そのうち行政界のポリゴンを持つ都市の数です。

`plateau_city_master_extra.csv`には、PLATEAUがCityGMLを配布しているのに属性リストに載っていない都市（守口市、門真市）を入れています。
`extract_city_master.py`は抽出の最後にこのファイルの行を足します。
あとで属性リストに載った都市では、属性リストの値が優先です。

## 手で実行する

必要なものは`osmium-tool`、`gdal-bin`（`ogr2ogr`）、Python 3です。
Pythonのパッケージは`psycopg2`が要り、`extract_city_master.py`だけ`openpyxl`も使います。

```bash
# 1. dash_* テーブルを作る
psql "$DATABASE_URL" -f sql/schema.sql

# 2. 都市マスタを入れる (Excel から抽出し直すときだけ extract_city_master.py を実行する)
python3 ingest/extract_city_master.py --xlsx-dir <属性リストのディレクトリ> -o data/plateau_city_master_2025.csv
python3 ingest/load_city_master.py data/plateau_city_master_2025.csv --postgres-url "$DATABASE_URL"

# 3. 行政界を入れる (年に一度。N03 の geojson / shp / gml を渡す)
python3 ingest/load_n03_boundaries.py <N03 のファイル> --postgres-url "$DATABASE_URL"

# 4. 週次の処理をまとめて実行する
./run_batch.sh --postgres-url "$DATABASE_URL"
./run_batch.sh --postgres-url "$DATABASE_URL" --regions "shikoku kyushu"   # 地域を絞る
```

`parse_wiki_imports.py`は`--dry-run`を付けると、データベースに書かずに読み取り結果だけを表示します。
`compute_stats.py`の`--city`は、試験のために1都市だけを計算するオプションです。

OSMの建物を取り出すときに`osmium tags-filter`を使わないでください。
メモリが1GBほどのサーバでは、メモリ不足で落ちる寸前まで使います（`docs/DESIGN.md`の11章）。
`fetch_region_buildings.sh`は、ディスク上の索引を使う`osmium export`の出力を`grep`で絞る方法を取っています。

`DASH_PBF_CACHE`にディレクトリを指定し、`osmupdate`（osmctools）を入れておくと、地域別のpbfをそのディレクトリに残し、次回からは差分だけを取得します。
どちらかが欠けているときの取得は、毎回pbfの全体です。
速くなるのは取得の段階だけで、建物の取り出しと読み込みで毎回地域全体を処理するのは変わりません(`docs/DESIGN.md`の9-4)。

## 週次の自動実行

`deploy/`に、`run_batch.sh`を週に一度動かすsystemdユニットのひな形があります。

| ファイル | 役割 |
|---|---|
| `deploy/run_weekly.sh` | 環境変数を読んでvenvを有効にし、`run_batch.sh`を起動する |
| `deploy/rapid-plateau-dashboard.service` | バッチを1回動かすoneshotのサービス |
| `deploy/rapid-plateau-dashboard.timer` | 日曜01:00にサービスを起動するタイマー（`Persistent=true`） |
| `deploy/dashboard-batch.env.example` | `DATABASE_URL`などの設定例 |

```bash
# 1. 設定ファイルを作る (データベースのパスワードを含むので、リポジトリの外に置き、権限を 600 にする)
sudo cp deploy/dashboard-batch.env.example /etc/rapid-plateau-dashboard.env
sudo chmod 600 /etc/rapid-plateau-dashboard.env   # DATABASE_URL などを書き込む

# 2. サービスの WorkingDirectory、ExecStart、User を環境に合わせて書き換えてから置く
sudo cp deploy/rapid-plateau-dashboard.service deploy/rapid-plateau-dashboard.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rapid-plateau-dashboard.timer

systemctl list-timers rapid-plateau-dashboard.timer   # 次に動く時刻
journalctl -u rapid-plateau-dashboard.service -f      # ログ
```

バッチは数時間かかります。
パッケージの自動更新でサービスが再起動する時間帯と重ならないよう、ホストのタイムゾーンを確かめて`OnCalendar`を決めてください。
すぐに1回だけ動かすときは`sudo systemctl start rapid-plateau-dashboard.service`を使います。

## 画面の配置

`frontend/`の中身を、Webサーバの配信先（nginxの`/dashboard/`など）に置きます。

```bash
rsync -av frontend/ <配信先>/dashboard/
```

親のディレクトリのファイルを消さないよう、`--delete`は付けません。

`style.css`、`app.js`、`data.js`のどれかを変えたときは、コミットの前に`index.html`のハッシュを書き換えて、同じコミットに含めます。

```bash
python3 frontend/stamp_cache.py   # index.html の ?v= を各ファイルの内容のハッシュにする
git add frontend/index.html
```

CIは`python3 frontend/stamp_cache.py --check`でハッシュが最新かを確かめ、古ければ失敗します。

画面のデータの取得先は`/api/dashboard/*`です。
APIに届かないときは、同梱の`data.js`を代わりに表示します。
地図の背景は、OSMFJのタイルサーバが配信するToner（`maptiler-toner-ja`）です。

## APIのデータベース接続

`api/dashboard_api.py`は、psycopg2の読み取り専用セッション（`set_session(readonly=True)`）で接続するので、書き込み権限のあるロールで接続しても書き込みません。
接続先は`DASH_DATABASE_URL`を優先し、なければ`DATABASE_URL`を使います。

さらに守りを固めるなら、読み取り専用のロールを作り、`DASH_DATABASE_URL`に指定します。
作成例は[`sql/readonly_role.sql`](sql/readonly_role.sql)で、実行にはデータベースの管理者権限が必要です。

APIはrapid_plateau_apiのアプリに組み込んで動かすほか、単独でも起動できます。

```bash
DATABASE_URL=... uvicorn dashboard_api:app --port 8000   # api/ で実行する
```

## 開発とテスト

```bash
pip install -r requirements-dev.txt

# データベースを使わないテスト
pytest

# PostGIS を使うテスト
createdb dash_test && psql -d dash_test -c "CREATE EXTENSION postgis"
DASH_TEST_DATABASE_URL=postgresql:///dash_test pytest
```

`DASH_TEST_DATABASE_URL`を設定しなければ、データベースを使うテストは飛ばされます。
CIで動くのもこの設定です。

## データソースとライセンス

コードは[MIT License](LICENSE)です。

| データ | 使い道 | 出典とライセンス |
|---|---|---|
| PLATEAU整備都市の属性リスト（attributedata_2025） | 全国の都市マスタ | 国土交通省[Project PLATEAU](https://www.mlit.go.jp/plateau/)（各データの利用規約に従う） |
| PLATEAUの建物 | 建物カバレッジ率で、1棟ずつ重なりを調べる建物。行政界のない都市では、OSMの建物がどの都市にあるかを、PLATEAUの建物の範囲で決める | 同上。rapid_plateau_apiが取り込んだもの |
| OSMの建物 | PLATEAUの建物と重なるかを調べる相手 | © OpenStreetMap contributors（[ODbL](https://www.openstreetmap.org/copyright)）。[Geofabrik](https://download.geofabrik.de/)の地域別pbfから取得 |
| OSM wiki [`JA:MLIT_PLATEAU/imports_list`](https://wiki.openstreetmap.org/wiki/JA:MLIT_PLATEAU/imports_list) | インポートの完了状況 | OSM wiki |
| 国土数値情報 行政区域データ（N03） | 市区町村の行政界（city_codeの付与、地図） | [国土交通省](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2025.html)（[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.ja)） |
| 地図タイル | 地図の背景 | © [OpenMapTiles](https://www.openmaptiles.org/) © OpenStreetMap contributors（[OSMFJタイルサーバ](https://tile.openstreetmap.jp/)が配信） |
