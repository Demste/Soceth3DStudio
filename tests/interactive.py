"""Loaded in a fresh Blender QA window without modifying Blender preferences."""
import os
import sys
from pathlib import Path

import bpy

path = Path(__file__).resolve()
package = os.getenv("SOCETH_QA_ADDON_ZIP")
if package:
    assert bpy.ops.preferences.addon_install(filepath=package, overwrite=True) == {"FINISHED"}
    module = "soceth_blender"
else:
    sys.path.insert(0, str(path.parents[1] / "integrations"))
    import blender_addon
    module = "blender_addon"

bpy.ops.preferences.addon_enable(module=module)
prefs = bpy.context.preferences.addons[module].preferences
prefs.port = int(os.environ["SOCETH_PORT"])
prefs.token_file = str(Path(os.environ["SOCETH_DATA"]) / "local.token")
bpy.ops.soceth.connect()

