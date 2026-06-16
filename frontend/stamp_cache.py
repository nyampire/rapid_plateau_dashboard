#!/usr/bin/env python3
"""Rewrite the ?v=<hash> cache-busting query in index.html to each asset's content
hash, so a changed file (and only a changed file) gets a fresh URL. No build tool
required; run this once before deploying the static frontend.

Idempotent: re-running with unchanged assets leaves index.html unchanged.

Usage:
  python3 stamp_cache.py [frontend_dir]              # default: this script's directory
  python3 stamp_cache.py --check [frontend_dir]      # CI mode: don't write; exit 1 if stale
"""
import argparse
import hashlib
import pathlib
import sys

# Local assets referenced from index.html that should be cache-busted.
ASSETS = ["style.css", "app.js", "data.js"]


def short_hash(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def restamp(html: str, base: pathlib.Path) -> tuple[str, list[str]]:
    """Return (rewritten html, list of human-readable change descriptions)."""
    import re
    changed: list[str] = []
    for asset in ASSETS:
        path = base / asset
        if not path.exists():
            print(f"skip {asset}: not found")
            continue
        h = short_hash(path)
        # match "<asset>" optionally followed by an existing ?v=...
        pat = re.compile(re.escape(asset) + r"(?:\?v=[0-9a-zA-Z]+)?")
        new_html, n = pat.subn(f"{asset}?v={h}", html)
        if n and new_html != html:
            changed.append(f"{asset}?v={h} ({n}x)")
        html = new_html
    return html, changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "frontend_dir", nargs="?",
        help="frontend dir containing index.html (default: this script's dir)",
    )
    ap.add_argument(
        "--check", action="store_true",
        help="don't write; exit 1 if index.html is missing a current asset hash. "
             "Use in CI to catch PRs that updated an asset without re-stamping.",
    )
    args = ap.parse_args()

    arg = pathlib.Path(args.frontend_dir).resolve() if args.frontend_dir \
        else pathlib.Path(__file__).resolve()
    base = arg if arg.is_dir() else arg.parent
    index = base / "index.html"
    if not index.exists():
        sys.exit(f"index.html not found in {base}")

    html = index.read_text(encoding="utf-8")
    new_html, changed = restamp(html, base)

    if args.check:
        if new_html != html:
            sys.stderr.write(
                "stamp_cache: index.html is out of date for: "
                + "; ".join(changed) + "\n"
                "Run `python3 frontend/stamp_cache.py` and commit the result.\n"
            )
            sys.exit(1)
        print("stamp_cache: index.html hashes are up to date.")
        return

    index.write_text(new_html, encoding="utf-8")
    print("stamped:", "; ".join(changed) if changed else "(no changes)")


if __name__ == "__main__":
    main()
