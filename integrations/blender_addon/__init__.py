bl_info = {
    "name": "Soceth 3D Studio",
    "author": "Soceth",
    "version": (1, 2, 0),
    "blender": (4, 3, 0),
    "location": "3D View > Sidebar > Soceth",
    "category": "Import-Export",
}

import json
import os
import queue
import shutil
import threading
import urllib.request
from pathlib import Path

import bpy

from .engine import run_to_file
from .scene import import_scene

_pending = queue.Queue(maxsize=1)
_results = queue.Queue()
_stop = threading.Event()
_thread = None
_status = "Disconnected"


def call(base, token, route, body):
    req = urllib.request.Request(f"{base}/api/blender/{route}", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=4) as response:
        return json.load(response)


def network_worker(base, token):
    global _status
    current, completed = None, None
    while not _stop.is_set():
        try:
            if completed is None:
                try:
                    completed = _results.get_nowait()
                except queue.Empty:
                    pass
            if completed:
                call(base, token, "finish", {"id": completed})
                completed, current = None, None
            response = call(base, token, "poll", {"busy": bool(current), "capabilities": ["production_v1", "structure_v1"]})
            if response.get("command") and not current:
                current = response["id"]
                _pending.put_nowait((current, response["command"]))
            _status = "Processing" if current else "Connected"
        except (OSError, ValueError) as error:
            _status = str(error)
        _stop.wait(0.7)


def timer():
    if _stop.is_set():
        return None
    try:
        identifier, command = _pending.get_nowait()
    except queue.Empty:
        return 0.3
    try:
        kind = command["kind"]
        if bpy.data.is_dirty and kind != "capture":
            raise ValueError("Save unsaved changes or use Save Blender scene before opening another revision.")
        previous = Path(bpy.data.filepath) if bpy.data.filepath else None
        if kind == "open":
            live = Path(command["output"]) / "live.blend"
            shutil.copyfile(command["source"], live)
            command["source"] = str(live)
        result = run_to_file(command)
        if (not result["ok"] or result.get("unchanged") or result.get("variant") == "trial") and kind != "capture":
            if previous and previous.is_file():
                import_scene(previous)
            return 0.3
        elif result["ok"] and kind in {"edit", "palette_apply", "palette_restore", "capture"}:
            live = Path(command["output"]) / "live.blend"
            shutil.copyfile(Path(command["output"]) / "working.blend", live)
            import_scene(live)
        if result["ok"]:
            active = bpy.context.scene
            active["soceth_live_asset"] = command["asset"]
            active["soceth_live_project"] = command["project"]
            active["soceth_live_variant"] = command.get("variant", "main")
            bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath, compress=True)
    except Exception as error:
        path = Path(command["output"]) / "blender-result.json"
        path.write_text(json.dumps({"ok": False, "error": str(error)}), encoding="utf-8")
    finally:
        _results.put(identifier)
    return 0.3


class SOCETH_OT_connect(bpy.types.Operator):
    bl_idname = "soceth.connect"
    bl_label = "Connect to Soceth"

    def execute(self, context):
        global _thread
        if _thread and _thread.is_alive():
            return {"FINISHED"}
        settings = context.preferences.addons[__package__].preferences
        try:
            token = Path(bpy.path.abspath(settings.token_file)).read_text(encoding="ascii").strip()
            if not token:
                raise ValueError("Select the Soceth local.token file in the add-on preferences.")
        except (OSError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        _stop.clear()
        _thread = threading.Thread(target=network_worker,
            args=(f"http://127.0.0.1:{settings.port}", token), daemon=True)
        _thread.start()
        if not bpy.app.timers.is_registered(timer):
            bpy.app.timers.register(timer, persistent=True)
        return {"FINISHED"}


class SOCETH_OT_disconnect(bpy.types.Operator):
    bl_idname = "soceth.disconnect"
    bl_label = "Disconnect"

    def execute(self, context):
        global _status
        _stop.set()
        _status = "Disconnected"
        return {"FINISHED"}


class SOCETH_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    token_file: bpy.props.StringProperty(name="Local token file", subtype="FILE_PATH",
        default=str(Path(os.environ["SOCETH_DATA"]) / "local.token") if os.getenv("SOCETH_DATA") else "")
    port: bpy.props.IntProperty(name="Backend port", default=17863, min=1024, max=65535)

    def draw(self, context):
        self.layout.prop(self, "token_file")
        self.layout.prop(self, "port")


class SOCETH_PT_panel(bpy.types.Panel):
    bl_label = "Soceth 3D Studio"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Soceth"

    def draw(self, context):
        self.layout.label(text=_status)
        self.layout.operator("soceth.connect")
        self.layout.operator("soceth.disconnect")


CLASSES = (SOCETH_Preferences, SOCETH_OT_connect, SOCETH_OT_disconnect, SOCETH_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    _stop.set()
    if bpy.app.timers.is_registered(timer):
        bpy.app.timers.unregister(timer)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

