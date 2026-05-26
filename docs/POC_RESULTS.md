# ダッシュボード PoC 実施結果 (2026-05-23)

DASHBOARD_DESIGN.md §10 Phase 1（PoC）の実施記録。
「公開 extract → osmium → PostGIS → 交差率算出」を 3 都市で通し、数値妥当性・osmium のメモリ/時間を実測した。

スクリプト・成果物に機微情報（接続情報等）は含めない。

---

## 結論（要約）

- **パイプラインは end-to-end で成立**。公開 Geofabrik extract のみで完結（作業者スコープ外＝user メタ不要を裏付け）。
- **交差率指標は妥当に弁別する**: wiki 完了+検証済の新座=89.9%、未完了山間の奥多摩=25.2%、OSM トレース途上の高知=36.5%。
- **part 除外は正しい**: 高知で率はほぼ同じだが棟数は outline 78,384 → part込み 119,001（+52%）。「棟数」基準としては outline のみが正。
- **要検討（重要）**:
  1. 判定基準がやや厳しい（検証済都市でも近傍 OSM 建物の取りこぼしあり）→ キャリブレーション要。
  2. 交差判定 SQL が重い（高知 119k 棟で約 7m49s）→ 全国規模で最適化必須。
  3. **【本番機実測で確定】osmium `tags-filter` は ≈1GB 機で危険**（Shikoku ですら MemAvailable 1.8MB / swap +1.5GB の OOM 寸前）→ **`osmium export`(disk index) | `grep building` に変更**。最大 region(Kanto)でも MemAvailable 482MB を維持し安全（§6）。
  4. 本番サーバ に osmium-tool / gdal-bin を導入済み（2026-05-23）。

---

## 対象都市

| code | 都市 | PLATEAU outline | PLATEAU total(part込) | OSM wiki 状態 |
|---|---|---|---|---|
| 11230 | 埼玉県新座市 | 58,804 | 62,311 | 完了 + 検証済 (2025-05-07) |
| 13308 | 東京都奥多摩町 | 8,505 | 8,768 | 未記載（未完了・山間） |
| 39201 | 高知県高知市 | 78,384 | 119,001 | 未記載（PLATEAU 再import済だが OSM トレース途上） |

OSM extract: Geofabrik `kanto-latest.osm.pbf`(445MB, 新座+奥多摩) / `shikoku-latest.osm.pbf`(83MB, 高知)。

---

## 1. パイプライン

実行環境: osmium は **ローカル環境**（osmium 1.19.0）、交差率算出は **本番サーバ の PostGIS**（plateau_buildings が常駐）。
OSM 建物は ローカルから セキュアな経路経由で ogr2ogr → 本番サーバ `dash_osm_buildings` に投入。

各都市の処理（`run_city_osmium.sh`）:
```
osmium extract -b <bbox> <region>.osm.pbf -o clip.osm.pbf          # PoCで1都市に絞るため（本番フローには不要）
osmium tags-filter clip.osm.pbf nwr/building -o buildings.osm.pbf
osmium export buildings.osm.pbf --add-unique-id=type_id \
  --index-type=sparse_file_array --geometry-types=polygon -f geojsonseq -o buildings.geojsonseq
```
- bbox は 本番サーバ の `ST_Extent(plateau_buildings.geom)`（city_code 別）から取得。
- `--add-unique-id=type_id` で feature id が `a<num>` 形式（num 偶数=way / 奇数=relation; osm_id=num/2 等）。投入時にデコードして osm_type/osm_id を復元。
- ogr2ogr で staging table へ → `INSERT ... SELECT` で `dash_osm_buildings(city_code, osm_type, osm_id, geom)` に整形投入。

---

## 2. osmium メモリ/時間 実測（Mac, /usr/bin/time -l の peak RSS）

> ⚠️ macOS の "maximum resident set size" は **mmap した入力 pbf のページを含む**ため、Linux で必須となる実メモリより過大に出る可能性が高い。本番サーバ(Linux, ≈1GB) での確定値は別途要計測。

### 本番フロー（region 全体を直接 tags-filter → export。bbox extract なし）— 最大 region の Kanto 445MB

| 工程 | 時間 | peak RSS | 出力 |
|---|---|---|---|
| tags-filter nwr/building | 7.59s | **1.85 GB** | buildings.osm.pbf 186MB |
| export (sparse_file_array) | 24.80s | **639 MB** | geojsonseq 1.7GB / **5,983,467 features** |

- **export は disk-backed index で region 規模(約600万棟)でも 639MB で完走** → sparse_file_array 採用は妥当。
- **tags-filter が 1.85GB** が唯一の懸念。referenced node の id-set 構築が主因とみられる。≈1GB 本番サーバ では swap に入る可能性 → ①本番サーバ 実測 ②県別など小さい extract に分割 ③許容して swap 運用、のいずれか。

### PoC フロー（1都市 bbox clip。本番には無い工程）参考値

| 都市 | extract peak | filter peak | export peak | features |
|---|---|---|---|---|
| 11230 新座 | 2.72 GB | 1.21 GB | 67 MB | 96,613 |
| 13308 奥多摩 | 2.53 GB | 528 MB | 40 MB | 3,034 |
| 39201 高知 | 1.63 GB | 1.09 GB | 54 MB | 36,891 |

- `osmium extract`(bbox clip) が 1.6–2.7GB と高いが、これは **PoC で1都市に絞るための工程で本番フローには含まれない**（本番は region を直接 filter）。

---

## 3. 交差率（§2.1: 代表点 ST_PointOnSurface in OSM ポリゴン OR 面積重複>30%、分母=outline のみ）

| code | 都市 | outline | 交差 | **率** | wiki 状態 |
|---|---|---|---|---|---|
| 11230 | 新座 | 58,804 | 52,883 | **89.93%** | 完了+検証済 |
| 13308 | 奥多摩 | 8,505 | 2,143 | **25.20%** | 未完了 |
| 39201 | 高知 | 78,384 | 28,585 | **36.47%** | OSMトレース途上 |

→ 完了+検証済が ~90%、未完了が 25–36% で **指標は方向性として妥当**。
（解釈注記: 本指標は「OSM に建物が存在する率」であり「PLATEAU からの取込率」ではない。PLATEAU 提供以前から OSM にある建物も真になる。設計通り。）

### 新座 89.93% の 10% ギャップ内訳（5,921 棟）

| 内訳 | 件数 | 割合 | 意味 |
|---|---|---|---|
| 近傍に OSM 建物あり・判定漏れ | 3,892 | 65.7% | **形状ズレ**（点 in も 面積>30% も満たさず） |
| 近傍に OSM 建物なし | 2,029 | 34.3% | **OSM 未存在**（真の未取込） |

→ 検証済完了都市でも、近傍に OSM 建物があるケースの 2/3 を取りこぼす＝**判定基準がやや厳しい**。
要検討: 面積閾値の引き下げ / 距離トレランス（代表点が OSM 建物から N m 以内）/ 逆方向（OSM→PLATEAU）併用。

### 高知 part 除外の検証（outline のみ vs part込み）

| scope | PLATEAU 行数 | 交差 | 率 |
|---|---|---|---|
| outline のみ | 78,384 | 28,585 | 36.47% |
| part 込み | 119,001 | 43,120 | 36.23% |

→ 率はほぼ同じ（part は親 outline の判定を継承し空間的に同居）だが、**棟数は +52% 膨張**。
「棟数」セマンティクスでは outline 除外が正しいことを確認（設計の判断を裏付け）。

---

## 4. 性能（交差判定 SQL, 本番サーバ PostGIS 16 + PostGIS 3.4）

| 対象 | 棟数 | 時間 | 備考 |
|---|---|---|---|
| 新座+奥多摩 | 67k | 180s | EXISTS を 2 回評価する非効率クエリ |
| 高知(part込) | 119k | **468s (7m49s)** | 単一パス。matched を全行で計算 |

- スループット約 250 棟/s。**全国 outline 約 1,200 万棟なら単純外挿で約 13 時間**。週次バッチなら許容範囲だが要最適化。
- ボトルネックは `ST_Area(ST_Intersection(p.geom,o.geom))`。改善案:
  - 先に `ST_Contains(o.geom, ST_PointOnSurface(p.geom))` で安価に大半を確定し、残りのみ面積計算（OR の評価順を制御 / LATERAL で段階化）。
  - 面積比は度単位の平面近似で計算中（4326）。厳密には planar CRS or geography が望ましい（PoC では比なので近似許容）。
  - 都市単位の空間分割・並列、または事前計算（MatView）。

---

## 5. 本番化に向けた要対応（本番サーバ 環境）

- ✅ **osmium-tool 1.16.0 / gdal-bin(ogr2ogr) 3.8.4 を導入済み**（2026-05-23）。
- venv に psycopg2 は既存（shapely / GDAL python binding は無し）。OSM 建物投入は ogr2ogr 経路で可。
- 本 PoC の交差率算出時は ローカルで osmium 実行 → セキュアな経路 + ローカルの ogr2ogr で 本番サーバ に投入したが、本番バッチは 本番サーバ 上で osmium + ogr2ogr 完結が可能になった。

---

## 6. 本番機(≈1GB / swap)メモリ実測（2026-05-23）

本番サーバ に osmium 導入後、`/usr/bin/time -v` の Max RSS と `/proc/meminfo` 1s サンプリング（peak swap / min MemAvailable）で実測。計測スクリプトは tags-filter 版と export|grep 版を用意。

| region | 手法 | Max RSS | swap delta | min MemAvailable | elapsed | building | 判定 |
|---|---|---|---|---|---|---|---|
| Shikoku 83MB | `osmium tags-filter nwr/building` | 742MB | **+1.5GB** | **1.8MB** | 0:30 | 593,338 | ❌ OOM寸前 |
| Shikoku 83MB | `osmium export \| grep '"building":'` | 332MB | +11MB | 382MB | 0:08 | 592,958 | ✅ 安全 |
| Kanto 446MB(最大) | `osmium export \| grep '"building":'` | 701MB | +258MB | 482MB | 2:55 | 5,967,905 | ✅ 安全 |

### 結論

- **`tags-filter` は不採用**。参照ノード解決のためのメモリ確保で、最小 region でも ≈1GB 機の RAM+swap をほぼ使い切り、稼働中の postgres/API を OOM で巻き込む危険。
- **本番フロー = `osmium export`(sparse_file_array disk index) を stdout に流し `grep '"building":'` で building 行のみ抽出**。
  ```bash
  osmium export region.osm.pbf --add-unique-id=type_id \
    --index-type=sparse_file_array --geometry-types=polygon -f geojsonseq -o - \
    | grep '"building":' > buildings.geojsonseq
  ```
  - export の Max RSS は region サイズにほぼ依存しない（座標 index がディスク）。grep はストリーミングで追加 RAM ほぼゼロ。
  - `grep '"building":'` は key `building`（OSM 建物 outline）を拾い、`building:part` 単独 way を除外 → 意図通り。building 件数は tags-filter とほぼ一致（差 ~0.06%）。
- 処理単位は Geofabrik 地域別 region（全国 8 本）で十分。県別細分は不要。
- 実行時の安全策（実装済の計測スクリプトに踏襲）: `/proc/meminfo` を監視し、稼働サービス保護のため MemAvailable 閾値割れで中断できるようにする。一時 pbf/geojsonseq は trap で削除。

⚠️ 実測中、tags-filter 版は実際に本番機を OOM 寸前まで追い込んだ（min MemAvailable 1.8MB）。本番バッチでは tags-filter を絶対に使わないこと。

---

## 付録: 成果物

- `run_city_osmium.sh` — 1 都市分の osmium パイプライン（再利用可）
- `work_<city>/` — 各都市の中間 pbf / geojsonseq / time ログ
- `kanto-latest.osm.pbf` / `shikoku-latest.osm.pbf` — Geofabrik extract（再利用可、不要なら削除可）
- `kanto-buildings.osm.pbf` / `.geojsonseq` — region 一括 filter/export の成果（メモリ計測用、削除可）
- 本番サーバ `dash_osm_buildings` テーブル: PoC 3 都市分(11230/13308/39201)を投入済み。staging table は削除済み。
