"""Create a source-audited, versioned Windows ZIP with the compiled desktop UI."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from audit_release import ROOT, audit

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--source", action="store_true", help="Package the allowlisted tree when preparing an initial commit.")
args = parser.parse_args()
paths = audit(source=args.source)
version = json.loads((ROOT / "shared/release.json").read_text())["version"]
prefix = f"Soceth3DStudio-{version}"
desktop = ROOT / "apps/desktop"
build = desktop / "dist"
if not (build / "index.html").is_file():
    raise SystemExit("Build the desktop UI first.")
sources = [*list((desktop / "src").rglob("*")), *(desktop / name for name in
           ("package-lock.json", "package.json", "index.html", "tsconfig.json"))]
combined = "".join(hashlib.sha256(item.read_bytes()).hexdigest().upper()
                   for item in sorted(sources, key=lambda item: str(item).casefold()) if item.is_file())
receipt = hashlib.sha256(combined.encode()).hexdigest().upper()
args.output.mkdir(parents=True, exist_ok=True)
destination = args.output / f"{prefix}-Windows.zip"
with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
    for name in paths:
        archive.write(ROOT / name, prefix + "/" + name)
    for file in build.rglob("*"):
        if file.is_file() and (file.suffix in {".html", ".css", ".js", ".woff", ".woff2"}):
            archive.write(file, prefix + "/" + file.relative_to(ROOT).as_posix())
    archive.writestr(prefix + "/apps/desktop/dist/.soceth-build", receipt + "\n")
digest = hashlib.sha256(destination.read_bytes()).hexdigest()
destination.with_suffix(".zip.sha256").write_text(f"{digest}  {destination.name}\n", encoding="ascii")
print(destination)
