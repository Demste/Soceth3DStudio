"""Query public advisories for the pinned application packages."""

import json
import urllib.request
from pathlib import Path

pins = [tuple(line.split("==", 1)) for path in (Path("requirements.lock.txt"), Path("shared/local3d-requirements.txt"))
        for line in path.read_text().splitlines() if line and not line.startswith("#") and "==" in line]
queries = [{"package": {"ecosystem": "PyPI", "name": package}, "version": version} for package, version in pins]
request = urllib.request.Request("https://api.osv.dev/v1/querybatch",
    data=json.dumps({"queries": queries}).encode(), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=30) as response:
    results = json.load(response)["results"]
findings = [(name, version, advisory["id"]) for (name, version), row in zip(pins, results) for advisory in row.get("vulns", [])]
for name, version, advisory in findings:
    print(name, version, advisory)
if findings:
    raise SystemExit("Pinned dependencies have published advisories.")
print(f"Checked {len(pins)} locked packages against OSV.")
