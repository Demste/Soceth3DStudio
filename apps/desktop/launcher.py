import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.backend.core import BASE, DATA, LOGS, PORT, ROOT, RUNTIME, TOKEN_FILE, init, profile, write_json
from services.backend.hardware import cuda_probe, gpus, inventory
from services.backend.locations import CACHE, configured
from services.backend.providers.discovery import comfy_paths
from services.backend.providers.models import COMFY, COMFY_DATA, OLLAMA, ollama_model_home

FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def reachable(url):
    with httpx.Client(timeout=1, trust_env=False) as client:
        with contextlib.suppress(httpx.HTTPError):
            return client.get(url).is_success
    return False


def spawn(args, role, env=None, cwd=None):
    with (LOGS / f"{role}.log").open("ab") as log:
        return subprocess.Popen(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)


def comfy_runtime():
    cards = gpus()
    for item in inventory()["comfy"]:
        python = item["python"]
        if python and cuda_probe(python, cards)["status"] == "verified":
            home = Path(item["home"])
            return home, home / "main.py", Path(python)
    return None


def providers():
    children = []
    if not reachable(OLLAMA + "/api/version") and (ollama := shutil.which("ollama")):
        model_home = ollama_model_home()
        model_home.mkdir(parents=True, exist_ok=True)
        os.environ["SOCETH_OLLAMA_MODELS"] = str(model_home)
        children.append(spawn([ollama, "serve"], "ollama", {
            **os.environ, "OLLAMA_HOST": OLLAMA.removeprefix("http://"),
            "OLLAMA_MODELS": str(model_home), "OLLAMA_NO_CLOUD": "1", "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_MAX_LOADED_MODELS": "1",
        }))
    selected = comfy_runtime() if not reachable(COMFY + "/system_stats") else None
    if selected:
        home, script, python = selected
        os.environ["SOCETH_ACTIVE_COMFY_PYTHON"] = str(python)
        COMFY_DATA.mkdir(parents=True, exist_ok=True)
        args = [str(python), "-s", str(script), "--listen", "127.0.0.1", "--port", COMFY.rsplit(":", 1)[-1],
                "--base-directory", str(COMFY_DATA), "--offline", "--disable-all-custom-nodes",
                 "--disable-auto-launch", "--disable-metadata", "--disable-product-tips", "--disable-assets",
                "--extra-model-paths-config", str(comfy_paths())]
        proof = cuda_probe(str(python), gpus())
        ready = [device for device in proof["devices"] if device["ok"]]
        if ready:
            args += ["--cuda-device", str(max(ready, key=lambda device: device["total"])["index"])]
        if profile() == "LOW_VRAM":
            args += ["--disable-dynamic-vram", "--lowvram", "--reserve-vram", "0.7"]
        children.append(spawn(args, "comfyui", {
            **os.environ, "PYTHONUTF8": "1", "HF_HOME": os.getenv("HF_HOME", str(CACHE / "hf-home")),
            "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "TRANSFORMERS_OFFLINE": "1",
        }, home))
    return children


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--no-providers", action="store_true")
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()
    if not args.backend_only and not (ROOT / "apps/desktop/dist/index.html").is_file():
        raise SystemExit("The desktop bundle is missing. Run setup.ps1.")
    already = reachable(BASE + "/api/health")
    if args.backend_only and already:
        raise SystemExit(f"Soceth is already listening on {BASE}.")
    child = []
    pid_record = RUNTIME / f"studio-{PORT}.json"
    server = None
    receiving = None
    if not already:
        init()
        if not args.no_providers:
            child = providers()
        server = uvicorn.Server(uvicorn.Config("services.backend.main:app", host="127.0.0.1", port=PORT,
                                              access_log=False, log_level="warning"))
        write_json(pid_record, {"pid": os.getpid(), "children": [item.pid for item in child], "time": time.time()})
    try:
        if args.backend_only:
            server.run()
            return
        if server:
            receiving = threading.Thread(target=server.run, daemon=True)
            receiving.start()
        until = time.monotonic() + 20
        while not reachable(BASE + "/api/health"):
            if time.monotonic() > until or (receiving and not receiving.is_alive()):
                raise RuntimeError(f"The local Studio server could not start on {BASE}.")
            time.sleep(0.25)
        if not TOKEN_FILE.is_file():
            raise RuntimeError("The running Studio instance uses another data directory.")
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        url = BASE + "/#session=" + TOKEN_FILE.read_text(encoding="ascii").strip()
        window = webview.create_window("Soceth 3D Studio", url, width=1480, height=920, min_size=(940, 610),
                                       background_color="#161917")

        def choose_path(kind):
            if kind not in {"directory", "gguf", "python"}:
                return None
            options = {"dialog_type": webview.FileDialog.FOLDER if kind == "directory" else webview.FileDialog.OPEN}
            if kind == "gguf":
                options["file_types"] = ("GGUF models (*.gguf)",)
            if kind == "python":
                options["file_types"] = ("Python interpreter (python.exe)",)
            files = window.create_file_dialog(**options)
            return files[0] if files else None

        window.expose(choose_path)
        webview.start(gui="edgechromium", debug=args.dev, private_mode=False, storage_path=str(RUNTIME / "webview"))
    finally:
        if server:
            server.should_exit = True
        if receiving:
            receiving.join(timeout=7)
        for process in child:
            if process.poll() is None:
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=4)
        if server:
            pid_record.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

