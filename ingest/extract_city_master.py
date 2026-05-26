#!/usr/bin/env python3
"""Extract the national PLATEAU city master from attributedata_2025 Excel files.

Source: PLATEAU "整備都市の属性リスト" (attributedata_2025_v3/v4/v5.xlsx).
Each file has a 'V<n>建築物' sheet, TRANSPOSED (one column per city), with:
  row3 = city_code (5-digit), row4 = region(地方), row5 = prefecture(都道府県),
  row6 = city_name, rows 7-10 = building LOD1-4 ranges.
City columns start where row3 first holds a 5-digit code.

building_lods uses positional mapping: rows 7-10 -> LOD 1,2,3,4 when the cell is
non-empty (matches the established master; does not re-read the LOD digit from text).
Output CSV: city_code,prefecture,region,city_name,building_lods,spec_versions
Cities deduped across files; spec_versions lists every version a city appears in
(e.g. 'V3+V4'); row fields are taken from the highest version.

Usage:
  python3 extract_city_master.py --xlsx-dir <attributedata dir> -o plateau_city_master_2025.csv
"""
import argparse
import csv
import os
import re

import openpyxl

CODE_RE = re.compile(r"^\d{5}$")
FILES =[("attributedata_2025_v3.xlsx", "V3建築物", "V3"),
         ("attributedata_2025_v4.xlsx", "V4建築物", "V4"),
         ("attributedata_2025_v5.xlsx", "V5建築物", "V5")]
VER_RANK = {"V3": 3, "V4": 4, "V5": 5}


def cell(rows, r, ci):
    v = rows[r][ci]
    return str(v).strip() if v is not None else ""


def extract_sheet(path, sheet, version):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    rows = {}
    for ri, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), 1):
        rows[ri] = row
    wb.close()
    width = max(len(rows[r]) for r in rows)
    out = []
    for ci in range(width):
        code = rows[3][ci] if ci < len(rows[3]) else None
        code = str(code).strip() if code is not None else ""
        if not CODE_RE.match(code):
            continue
        region = cell(rows, 4, ci)
        pref = cell(rows, 5, ci)
        name = cell(rows, 6, ci)
        # Positional: rows 7,8,9,10 -> LOD 1,2,3,4 when non-empty.
        lods = [str(i) for i, r in enumerate((7, 8, 9, 10), 1) if cell(rows, r, ci)]
        out.append({"city_code": code, "prefecture": pref, "region": region,
                    "city_name": name, "building_lods": "+".join(lods),
                    "spec_versions": version})
    return out


def main():
    ap = argparse.ArgumentParser(description="Extract PLATEAU city master from attributedata_2025 Excel.")
    ap.add_argument("--xlsx-dir", required=True)
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    merged = {}    # code -> rec (row fields from highest version)
    versions = {}  # code -> set of versions
    for fname, sheet, ver in FILES:
        path = os.path.join(os.path.expanduser(args.xlsx_dir), fname)
        if not os.path.exists(path):
            print(f"skip (missing): {path}")
            continue
        recs = extract_sheet(path, sheet, ver)
        print(f"{fname}: {len(recs)} cities")
        for rec in recs:
            code = rec["city_code"]
            versions.setdefault(code, set()).add(ver)
            cur = merged.get(code)
            if cur is None or VER_RANK[ver] > VER_RANK[cur["spec_versions"]]:
                merged[code] = rec

    for code, rec in merged.items():
        rec["spec_versions"] = "+".join(v for v in ("V3", "V4", "V5") if v in versions[code])
    rows = sorted(merged.values(), key=lambda r: r["city_code"])
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["city_code", "prefecture", "region",
                                          "city_name", "building_lods", "spec_versions"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} unique cities -> {args.out}")


if __name__ == "__main__":
    main()
