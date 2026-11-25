import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from services.backend.locations import RUNTIME

addon = root / "integrations/blender_addon"
out = RUNTIME / "Soceth-Blender-Addon.zip"
out.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as bundle:
    for file in sorted(addon.glob("*.py")):
        bundle.write(file, f"soceth_blender/{file.name}")
    if (addon / "LICENSE").is_file():
        bundle.write(addon / "LICENSE", "soceth_blender/LICENSE")
print(out)

