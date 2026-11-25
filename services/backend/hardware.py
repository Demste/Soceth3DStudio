"""Read physical devices and prove CUDA execution with the selected interpreter."""

import csv
import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from .core import ROOT, RUNTIME, blender_path, setting, set_setting, write_json
from .locations import DATA, configured

MIB = 1024 ** 2
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def command(args, timeout=8):
    try:
        return subprocess.run(args, text=True, encoding="utf-8", errors="replace", capture_output=True,
                              timeout=timeout, check=True, creationflags=FLAGS).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def gpus():
    output = command(["nvidia-smi", "--query-gpu=index,uuid,name,compute_cap,memory.total,memory.free,driver_version",
                      "--format=csv,noheader,nounits"])
    rows = []
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        try:
            index, uuid, name, capability, total, free, driver = row
            total, free = int(total), int(free)
            rows.append(dict(index=int(index), uuid=uuid, name=name, capability=capability, total=total,
                             free=free, used=total-free, driver=driver))
        except ValueError:
            continue
    return rows


def memory():
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (field, ctypes.c_ulonglong) for field in ("total", "available", "swap", "swap_free", "vm", "vm_free", "extended")]
        stats = Status()
        stats.length = ctypes.sizeof(stats)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stats)):
            return {"total": stats.total // MIB, "available": stats.available // MIB}
    elif hasattr(os, "sysconf"):
        size = os.sysconf("SC_PAGE_SIZE")
        return {"total": os.sysconf("SC_PHYS_PAGES") * size // MIB,
                "available": os.sysconf("SC_AVPHYS_PAGES") * size // MIB}
    return {"total": 0, "available": 0}


def compute(card):
    return tuple(int(part) for part in card["capability"].split(".")) if re.fullmatch(r"\d+\.\d+", card["capability"]) else (0, 0)


def runtime_archive(cards):
    # Preserve the measured cu126 path on pre-Blackwell. CUDA 13 needs R580+.
    card = best_gpu(cards)
    if not card:
        return None
    driver = int(card["driver"].split(".")[0]) if card["driver"].split(".")[0].isdigit() else 0
    if compute(card) >= (12, 0) or compute(card)[0] in (10, 11):
        return "cu130" if driver >= 580 else None
    return "cu126" if compute(card) >= (7, 5) and driver >= 560 else None


def best_gpu(cards):
    return max(cards, key=lambda row: (row["total"], compute(row), row["free"]), default=None)


def inventory():
    user = Path.home()
    configured_home = configured("comfy")
    roots = [Path(configured_home)] if configured_home else []
    roots.extend([RUNTIME / "ComfyUI-cu130/ComfyUI_windows_portable", RUNTIME / "ComfyUI-cu126/ComfyUI_windows_portable",
                  RUNTIME / "ComfyUI_windows_portable", RUNTIME / "comfy-source/pinned",
                  user / "ComfyUI", user / "ComfyUI_windows_portable"])
    installs = []
    for root in dict.fromkeys(roots):
        home = root if (root / "main.py").is_file() else root / "ComfyUI"
        if not (home / "main.py").is_file():
            continue
        declared = configured("comfy_python") if configured_home and root == Path(configured_home) else None
        candidates = ([Path(declared)] if declared else []) + [
            root / "python_embeded/python.exe", home / ".venv/Scripts/python.exe", home / "venv/Scripts/python.exe"]
        if root == RUNTIME / "comfy-source/pinned":
            candidates.append(RUNTIME / "comfy-venv/Scripts/python.exe")
        python = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
        installs.append({"home": str(home), "python": python, "models": str(home / "models")})
    return {"comfy": installs, "python": shutil.which("python"), "node": shutil.which("node"),
            "ollama": shutil.which("ollama"), "blender": str(blender_path() or ""),
            "huggingface": str(Path(os.getenv("HF_HUB_CACHE", Path(os.getenv("HF_HOME", user / ".cache/huggingface")) / "hub"))),
            "ollama_models": configured("ollama_models") or os.getenv("OLLAMA_MODELS") or str(user / ".ollama/models")}


def cuda_probe(python, cards, *, refresh=False):
    if not python or not Path(python).is_file():
        return {"status": "missing", "devices": []}
    path = Path(python)
    stat = path.stat()
    torch_info = []
    for packages in (path.parent / "Lib/site-packages", path.parent.parent / "Lib/site-packages"):
        torch_info.extend((entry.name, entry.stat().st_mtime_ns) for entry in sorted(packages.glob("torch-*.dist-info")))
    identity = hashlib.sha256(json.dumps([str(path), stat.st_mtime_ns, torch_info,
        [(card["uuid"], card["driver"], card["capability"]) for card in cards]]).encode()).hexdigest()
    cache = RUNTIME / ("cuda-" + identity[:24] + ".json")
    if cache.is_file() and not refresh:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("status") == "verified":
            return saved
    output = command([python, str(ROOT / "scripts/probe_cuda.py")], timeout=65)
    try:
        value = json.loads(output.strip().splitlines()[-1])
    except (ValueError, IndexError):
        value = {"status": "failed", "devices": [], "error": "CUDA probe produced no result."}
    if value["status"] == "verified":
        write_json(cache, value)
    return value


def inspect(*, probe=False):
    cards, software = gpus(), inventory()
    disk = DATA if DATA.exists() else DATA.parent
    while not disk.exists() and disk != disk.parent:
        disk = disk.parent
    runtime = {"status": "unchecked", "devices": []}
    selected = configured("active_comfy_python")
    if probe:
        candidates = list(dict.fromkeys([selected, *(item["python"] for item in software["comfy"])]))
        for python in filter(None, candidates):
            runtime = cuda_probe(python, cards)
            if runtime["status"] == "verified":
                runtime["python"] = python
                break
    return {"gpus": cards, "ram": memory(), "cpu": os.getenv("PROCESSOR_IDENTIFIER") or platform.processor(),
            "cores": os.cpu_count(), "free_disk": shutil.disk_usage(disk).free, "software": software,
            "runtime": runtime, "runtime_archive": runtime_archive(cards)}


def track(hardware):
    signature = sorted(f'{card["uuid"]}:{card["total"]}:{card["capability"]}' for card in hardware["gpus"])
    stored = setting("hardware_signature")
    if signature and stored != signature:
        if stored:
            set_setting("hardware_changed", True)
        set_setting("hardware_signature", signature)
    return bool(setting("hardware_changed", False))
