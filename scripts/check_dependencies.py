"""Check the installed Python resolutions against the portable lockfile."""

from importlib import metadata
from pathlib import Path

pins = dict(line.strip().split("==", 1) for line in Path("requirements.lock.txt").read_text().splitlines()
            if line.strip() and not line.startswith("#"))
for package, expected in pins.items():
    try:
        if metadata.version(package) != expected:
            raise SystemExit(1)
    except metadata.PackageNotFoundError:
        raise SystemExit(1)
