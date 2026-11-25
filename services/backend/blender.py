import json
import os
import subprocess
import threading
import time
from pathlib import Path

from .core import LOGS, ROOT, blender_path, write_json

SCRIPT = ROOT / "integrations/blender_scripts/worker.py"


class Bridge:
    def __init__(self):
        self.lock = threading.Lock()
        self.seen = 0
        self.capabilities = set()
        self.waiting = None

    def connected(self):
        with self.lock:
            return time.monotonic() - self.seen < 5

    def supports(self, feature):
        with self.lock:
            return time.monotonic() - self.seen < 5 and feature in self.capabilities

    def poll(self, busy, capabilities=()):
        with self.lock:
            self.seen = time.monotonic()
            self.capabilities = set(capabilities)
            if (self.waiting and not busy and not self.waiting["claimed"] and
                (not self.waiting["capability"] or self.waiting["capability"] in self.capabilities)):
                self.waiting["claimed"] = True
                return {"id": self.waiting["id"], "command": self.waiting["command"]}
            return {}

    def finish(self, identifier):
        with self.lock:
            if self.waiting and self.waiting["id"] == identifier:
                self.waiting["complete"].set()

    def run(self, ctx, command):
        structural = (command.get("variant") in {"lod", "trial"} or any(
            op["type"] in {"decimate_mesh", "cleanup_mesh"} for op in command.get("operations", [])))
        capability = ("structure_v1" if structural else
                      "production_v1" if command["kind"] in {"palette_apply", "palette_restore"} else None)
        item = {"id": ctx.id, "command": command, "capability": capability,
                "claimed": False, "complete": threading.Event()}
        with self.lock:
            if self.waiting or time.monotonic() - self.seen >= 5:
                raise ValueError("Blender add-on is disconnected or already processing a command.")
            if capability and capability not in self.capabilities:
                raise ValueError("Install the current Soceth Blender add-on to run this command interactively, or use background mode.")
            self.waiting = item
        try:
            deadline = time.monotonic() + 300
            while not item["complete"].wait(0.4):
                ctx.check()
                if time.monotonic() > deadline or not self.connected():
                    raise TimeoutError("The interactive Blender connection stopped responding.")
        finally:
            with self.lock:
                self.waiting = None


bridge = Bridge()


def execute(ctx, command, mode="background"):
    ctx.stage("Blender processing")
    directory = Path(command["output"])
    directory.mkdir(parents=True, exist_ok=True)
    command_file = directory / "blender-command.json"
    write_json(command_file, command)
    if mode == "interactive":
        bridge.run(ctx, command)
    else:
        blender = blender_path()
        if not blender:
            raise ValueError("Blender was not found. Set SOCETH_BLENDER or install Blender 4.3 or later.")
        argv = [str(blender), "--background", "--factory-startup", "--disable-autoexec",
                "--python-exit-code", "1", "--python", str(SCRIPT), "--", str(command_file)]
        env = {**os.environ, "PYTHONUTF8": "1"}
        log_file = LOGS / f"{ctx.id}-blender.log"
        with log_file.open("ab") as log, subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as worker:
            limit = time.monotonic() + 1800
            try:
                while worker.poll() is None:
                    ctx.check()
                    if time.monotonic() > limit:
                        raise TimeoutError("Blender exceeded the 30 minute processing timeout.")
                    time.sleep(0.25)
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait()
    result = Path(command["output"]) / "blender-result.json"
    if not result.is_file():
        raise ValueError(f"Blender produced no result. Check job log {ctx.id}-blender.log.")
    output = json.loads(result.read_text(encoding="utf-8"))
    if not output.get("ok"):
        raise ValueError(output.get("error", "Blender operation failed."))
    return output

