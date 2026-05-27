#!/usr/bin/env python3
"""Rewrite the ?v=<hash> cache-busting query in index.html to each asset's content
hash, so a changed file (and only a changed file) gets a fresh URL. No build tool
required; run this once before deploying the static frontend.

Idempotent: re-running with unchanged assets leaves index.html unchanged.

Usage:
  python3 stamp_cache.py [frontend_dir]   # default: this script's directory
"""
import hashlib
import pathlib
import re
import sys

# Local assets referenced from index.html that should be cache-busted.
ASSETS = ["style.css", "app.js", "data.js"]


def short_hash(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def main() -> None:
    arg = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else pathlib.Path(__file__).resolve()
    base = arg if arg.is_dir() else arg.parent
    index = base / "index.html"
    if not index.exists():
        sys.exit(f"index.html not found in {base}")

    html = index.read_text(encoding="utf-8")
    changed = []
    for asset in ASSETS:
        path = base / asset
        if not path.exists():
            print(f"skip {asset}: not found")
            continue
        h = short_hash(path)
        # match "<asset>" optionally followed by an existing ?v=...
        pat = re.compile(re.escape(asset) + r"(?:\?v=[0-9a-zA-Z]+)?")
        html, n = pat.subn(f"{asset}?v={h}", html)
        if n:
            changed.append(f"{asset}?v={h} ({n}x)")

    index.write_text(html, encoding="utf-8")
    print("stamped:", "; ".join(changed) if changed else "(no assets matched)")


if __name__ == "__main__":
    main()
