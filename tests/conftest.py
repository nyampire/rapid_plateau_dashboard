import pathlib
import sys

# Make the ingest/ modules importable without packaging them.
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
