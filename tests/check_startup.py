"""Verify the installed Windows launch and stop entry points, using a separate port and data directory."""
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
qa = root / "runtime" / "qa" / "startup" / uuid4().hex[:8]
qa.mkdir(parents=True)
powershell = shutil.which("powershell.exe")
with_providers = "--providers" in sys.argv
port = os.getenv("SOCETH_TEST_PORT", "17869")
env = {**os.environ, "SOCETH_PORT": port, "SOCETH_DATA": str(qa), "PYTHONUTF8": "1"}
if with_providers:
    env.update(SOCETH_COMFY_PORT="18190", SOCETH_OLLAMA_PORT="11438")
options = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
with (qa / "startup.log").open("wb") as log, subprocess.Popen(
    [*options, str(root / "start.ps1"), "-BackendOnly", *([] if with_providers else ["-NoProviders"])],
    cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NO_WINDOW,
) as launcher:
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=18) as client:
            for _ in range(140):
                if launcher.poll() is not None:
                    raise RuntimeError("start.ps1 exited before the service was healthy.")
                try:
                    if client.get("/api/health").is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.35)
            else:
                raise RuntimeError("Backend startup timed out.")
            assert client.get("/").is_success
            assert client.get("/api/status").status_code == 401
            client.headers["Authorization"] = "Bearer " + (qa / "local.token").read_text(encoding="ascii")
            status = client.get("/api/status").raise_for_status().json()
            print("Detected profile", status["profile"], "GPU", status["gpus"], flush=True)
            if with_providers:
                registry = Path(os.getenv("SOCETH_DATA", root / "user_data")) / "studio.sqlite3"
                with sqlite3.connect(registry) as source, sqlite3.connect(qa / "studio.sqlite3") as dest:
                    rows = source.execute("SELECT id,body,created FROM installations").fetchall()
                    dest.executemany("INSERT OR REPLACE INTO installations VALUES (?,?,?)", rows)
                until = time.monotonic() + 150
                while time.monotonic() < until:
                    status = client.get("/api/status").raise_for_status().json()
                    if status["comfy"] and status["ollama"]:
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("The managed ComfyUI and Ollama providers did not both start.")
                models = client.get("/api/models").raise_for_status().json()
                assert {"qwen-coder","pixal3d","flux-klein"} <= {item["id"] for item in models if item["installed"]}
                state = client.get("/api/model-configuration").raise_for_status().json()
                assert state["hardware"]["runtime"]["status"] == "verified"
                assert all(state["dependencies"][key] for key in ("comfy","cuda","pixal3d_nodes","flux_nodes","ollama"))
                from services.backend.providers.models import CATALOG
                from services.backend.providers.workflows import flux_graph, pixal_graph, validate_graph
                with httpx.Client(base_url=f"http://127.0.0.1:{env['SOCETH_COMFY_PORT']}", trust_env=False, timeout=12) as comfy:
                    nodes = comfy.get("/object_info").raise_for_status().json()
                for name in ("flux-klein", "flux-klein-bf16", "pixal3d", "pixal3d-bf16"):
                    row = CATALOG[name]
                    for settings in row["configs"]:
                        profile = settings["profile"]
                        graph, _ = (flux_graph(nodes, row, "QA concept", 42, profile, "qa") if row["operation"] == "concept"
                                    else pixal_graph(nodes, row, "qa.png", 42, profile, "qa", 1024))
                        validate_graph(nodes, graph, require_artifacts=row["verification"] == "measured")
                print("Managed providers, CUDA kernel, and native workflow schemas ready. PyTorch", status["pytorch"], flush=True)
    finally:
        subprocess.run([*options, str(root / "stop.ps1")], cwd=root, env=env, timeout=20, check=True)
        launcher.wait(timeout=20)
    assert not (root / "runtime" / f"studio-{port}.json").exists()
print("I · Windows start.ps1, local API, desktop bundle, and stop.ps1 passed.", flush=True)

