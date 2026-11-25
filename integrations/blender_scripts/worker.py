import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blender_addon.engine import run_to_file

command = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
if not run_to_file(command)["ok"]:
    sys.exit(1)

