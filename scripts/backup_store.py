"""Create a consistent backup of the SQLite store before application upgrades."""
import argparse
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("store", type=Path)
parser.add_argument("backup", type=Path)
args = parser.parse_args()
if args.store.is_file():
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.store.resolve().as_uri() + "?mode=ro", uri=True) as source:
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SystemExit("Studio database integrity check failed.")
        if source.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():
            raise SystemExit("Studio still has a running job. Finish it before updating.")
        with sqlite3.connect(args.backup) as destination:
            source.backup(destination)
    print(f"Database backup: {args.backup}")
