"""Install the pinned ComfyUI artifacts using the same verifier as Model Manager."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.backend.core import init
from services.backend.providers.models import CATALOG, install


class Terminal:
    def __init__(self, identifier):
        self.identifier = identifier
        self.last_stage = ""
        self.last_progress = 0.0

    def check(self):
        pass

    def stage(self, stage, progress=None, detail=None):
        if stage != self.last_stage or (progress is not None and progress - self.last_progress >= 0.05):
            percent = f" ({progress:.0%})" if progress is not None else ""
            print(f"[{self.identifier}] {stage}{percent}", flush=True)
            self.last_stage = stage
            self.last_progress = progress or 0.0


def main():
    parser = argparse.ArgumentParser()
    choices = [item["id"] for item in CATALOG.values() if item["provider"] == "comfy" and not item.get("bundled_with")]
    parser.add_argument("model", choices=choices, nargs="+")
    args = parser.parse_args()
    init()
    identifiers = list(dict.fromkeys(args.model))
    failed = False
    for identifier in identifiers:
        try:
            result = install(CATALOG[identifier], Terminal(identifier))
            location = result.get("installation_path", "existing model location")
            print(f"[{identifier}] Verified installation in {location}", flush=True)
        except Exception as error:
            failed = True
            print(f"[{identifier}] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

