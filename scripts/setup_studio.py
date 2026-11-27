"""CLI setup and verification use the same catalog and analyzer as the desktop."""

import argparse
import contextlib
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.backend.core import RUNTIME, init, setting, set_setting, uid
from services.backend.hardware import inspect
from services.backend.locations import DATA
from services.backend.providers import discovery, models
from services.backend.providers.recommendations import PRIMARY


def launcher():
    spec = importlib.util.spec_from_file_location("studio_launcher", ROOT / "apps/desktop/launcher.py")
    instance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(instance)
    return instance


class Console:
    def __init__(self):
        self.id, self.out, self.stage_name = uid(), RUNTIME / "setup-results", None
        self.out.mkdir(exist_ok=True)

    def check(self):
        pass

    def stage(self, label, progress=None):
        percentage = int(progress * 10) * 10 if progress is not None else None
        current = label, percentage
        if current != self.stage_name:
            self.stage_name = current
            print(label + (f" ({percentage}%)" if percentage is not None else ""), flush=True)


def snapshot():
    answer = discovery.configuration(remember=False)
    tags = answer.pop("tags")
    answer["existing"] = discovery.discover(answer["hardware"], tags)
    answer["catalog"] = list(models.CATALOG.values())
    return answer


def apply(mode, identifiers):
    active = launcher()
    children = active.providers()
    try:
        until = time.monotonic() + 35
        intent = set(identifiers)
        while time.monotonic() < until:
            want_comfy = any(models.CATALOG[key]["provider"] == "comfy" for key in intent if key in models.CATALOG)
            want_ollama = mode == "existing" or any(models.CATALOG[key]["provider"] == "ollama" for key in intent if key in models.CATALOG)
            comfy = active.reachable(models.COMFY + "/system_stats")
            ollama = active.reachable(models.OLLAMA + "/api/version")
            if (comfy or not want_comfy) and (ollama or not want_ollama or not children):
                break
            time.sleep(.75)
        state = snapshot()
        if mode == "recommended" and not identifiers:
            identifiers = [state["recommendations"][key]["model"] for key in PRIMARY]
        if mode == "existing":
            identifiers = identifiers or state["existing"]["matches"]
        for key in dict.fromkeys(filter(None, identifiers)):
            model = models.CATALOG.get(key)
            if not model or model["operation"] not in PRIMARY:
                raise ValueError("Unknown installable model: " + key)
            if models.is_ready(model):
                print("Reused " + model["name"])
                continue
            console = Console()
            if mode == "existing":
                if model["provider"] in {"comfy", "local3d"}:
                    discovery.register_existing(model, [], console)
                continue
            if model["provider"] == "ollama" and not state["dependencies"]["ollama"]:
                raise ValueError("Start or install local Ollama before pulling the planned Ollama model.")
            if model["provider"] == "comfy" and not all(state["dependencies"].get(dep) for dep in model["dependencies"]):
                raise ValueError("The CUDA and native ComfyUI checks must pass before downloading this variant.")
            if key in state["existing"]["matches"] and model["provider"] in {"comfy", "local3d"}:
                discovery.register_existing(model, [], console)
            else:
                discovery.check_candidate_install(model, state)
                models.install(model, console)
        print("Model configuration saved. Existing manual choices remain active.")
    finally:
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
                with contextlib.suppress(Exception):
                    process.wait(timeout=7)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "apply", "verify", "paths"))
    parser.add_argument("--mode", choices=("recommended", "custom", "existing"), default="recommended")
    parser.add_argument("--model", action="append", default=[])
    args = parser.parse_args()
    if args.action == "verify":
        value = inspect(probe=True)
        print(json.dumps(value, ensure_ascii=False))
        from services.backend.main import app
        assert app.title == "Soceth 3D Studio"
    elif args.action == "paths":
        print(json.dumps({"runtime": str(RUNTIME), "data": str(DATA), "models": str(models.MODELS)}))
    else:
        init()
        if args.action == "plan":
            print(json.dumps(snapshot(), ensure_ascii=False))
        else:
            apply(args.mode, args.model)


if __name__ == "__main__":
    main()
