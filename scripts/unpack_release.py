"""Extract a verified release into a side-by-side version directory."""
import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

parser = argparse.ArgumentParser()
parser.add_argument("archive", type=Path)
parser.add_argument("destination", type=Path)
parser.add_argument("sha256")
args = parser.parse_args()
with args.archive.open("rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
if digest != args.sha256.lower():
    raise SystemExit("Release checksum did not match.")
with zipfile.ZipFile(args.archive) as bundle:
    rows = [row for row in bundle.infolist() if not row.is_dir()]
    names = [PurePosixPath(row.filename) for row in rows]
    roots = {name.parts[0] for name in names if name.parts}
    invalid = any(not name.parts or name.is_absolute() or ".." in name.parts or ":" in str(name) or "\\" in str(name)
                  or any(part.rstrip(" .") != part for part in name.parts) for name in names)
    if invalid or len(roots) != 1 or len(set(str(name).casefold() for name in names)) != len(names):
        raise SystemExit("Invalid release archive paths.")
    prefix = next(iter(roots))
    version = json.loads(bundle.read(f"{prefix}/shared/release.json"))["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or prefix != f"Soceth3DStudio-{version}":
        raise SystemExit("Unrecognized Studio release version.")
    destination = args.destination.resolve() / prefix
    destination.mkdir(parents=True)
    for row, name in zip(rows, names):
        path = destination.joinpath(*name.parts[1:]).resolve()
        if not path.is_relative_to(destination):
            raise SystemExit("Invalid release destination.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with bundle.open(row) as source, path.open("xb") as output:
            shutil.copyfileobj(source, output)
print(destination)
