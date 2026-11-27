"""Provision and run isolated upstream single-image reconstructions."""

import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from ..core import ROOT, RUNTIME, write_json
from ..hardware import FLAGS, inspect

WORKER = ROOT / "integrations/local3d_worker.py"
REQUIREMENTS = ROOT / "shared/local3d-requirements.txt"
SOURCES = {
    "triposr": ("TripoSR", "107cefdc244c39106fa830359024f6a2f1c78871", "tsr", 31282920,
                "a7b3871a1b4377bef96cbfb99bdc952bdd4f5df9f65fea4208d978a343fd1162"),
    "triposg": ("TripoSG", "fc5c40990181e2a756c4e0b1c2f4d6b5202faf8c", "triposg", 16411924,
                "cbb21f7dbb9f6759b84f00b5bcdce5c75a5130ae3a63d012646cd274fd945062"),
}


def package_path(python):
    executable = Path(python)
    identity = hashlib.sha256(f"{executable}:{executable.stat().st_mtime_ns}".encode() +
                              REQUIREMENTS.read_bytes()).hexdigest()[:20]
    return RUNTIME / "local3d" / ("packages-" + identity)


def source_path(workflow):
    return RUNTIME / "local3d" / SOURCES[workflow][1]


def ready(workflow, python):
    if not python or not Path(python).is_file():
        return False
    packages = package_path(python)
    return packages.is_dir() and (source_path(workflow) / (".soceth-ready-" + packages.name)).is_file()


def environment():
    env = {key: value for key, value in os.environ.items() if key not in {
        "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "PYTHONPATH", "PYTHONHOME", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"
    }}
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
               PYTHONUTF8="1", PIP_CONFIG_FILE=os.devnull)
    return env


def process(args, ctx, log_name, timeout):
    path = ctx.out / log_name
    with path.open("wb") as log, subprocess.Popen(args, env=environment(), cwd=ctx.out,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS) as child:
        until = time.monotonic() + timeout
        try:
            while child.poll() is None:
                ctx.check()
                if time.monotonic() >= until:
                    raise TimeoutError(f"Local 3D process timed out. See {log_name}.")
                time.sleep(.3)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    if child.returncode:
        with path.open("rb") as log:
            log.seek(max(0, path.stat().st_size - 2200))
            tail = log.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Local 3D process failed ({child.returncode}). {tail.strip()}")


def adapt_source(workflow, source):
    def replace(file, old, new):
        path = source / file
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise ValueError(f"Unexpected upstream source in {file}.")
        path.write_text(text.replace(old, new), encoding="utf-8")

    if workflow == "triposr":
        replace("tsr/utils.py", "import rembg\n", "")
        replace("tsr/utils.py", "image = rembg.remove(", "import rembg\n        image = rembg.remove(")
        replace("tsr/models/isosurface.py", "from torchmcubes import marching_cubes",
                "from local3d_worker import marching_cubes")
        replace("tsr/models/tokenizers/image.py",
                'hf_hub_download(\n                    repo_id=self.cfg.pretrained_model_name_or_path,\n                    filename="config.json",\n                )',
                "self.cfg.pretrained_model_name_or_path, local_files_only=True")
    else:
        replace("triposg/inference_utils.py", "from diso import DiffDMC\n", "")
        replace("triposg/inference_utils.py", "dmc = DiffDMC(", "from diso import DiffDMC\n        dmc = DiffDMC(")


def install_source(workflow, ctx):
    from .models import download

    name, revision, package, size, digest = SOURCES[workflow]
    target = source_path(workflow)
    if target.is_dir():
        return target
    archive = target.parent / (revision + ".zip")
    download({"size": size, "sha256": digest,
              "source": f"https://codeload.github.com/VAST-AI-Research/{name}/zip/{revision}"}, archive, ctx, 0, size)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        staged = Path(temp) / "source"
        with ZipFile(archive) as bundle:
            for item in bundle.infolist():
                relative = PurePosixPath(item.filename)
                if relative.is_absolute() or any(part in {".", ".."} or ":" in part or "\\" in part for part in relative.parts):
                    raise ValueError("Invalid upstream source archive path.")
                parts = relative.parts[1:]
                if (not parts or item.is_dir() or stat.S_ISLNK(item.external_attr >> 16)
                        or not (parts[0] == package or parts == ("LICENSE",))):
                    continue
                path = staged.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bundle.read(item))
        adapt_source(workflow, staged)
        staged.replace(target)
    return target


def provision(model, ctx):
    workflow = model["workflow"]
    runtime = inspect(probe=True)["runtime"]
    python = runtime.get("python")
    if not python or runtime["status"] != "verified":
        raise ValueError("Set up a local ComfyUI PyTorch interpreter with verified CUDA before installing this model.")
    if ready(workflow, python):
        return
    source = install_source(workflow, ctx)
    packages = package_path(python)
    if not packages.is_dir():
        ctx.stage("Preparing isolated 3D dependencies")
        with tempfile.TemporaryDirectory(dir=source.parent) as temp:
            staged = Path(temp) / "packages"
            process([sys.executable, "-m", "pip", "--python", python, "install", "--no-deps",
                     "--disable-pip-version-check", "--index-url", "https://pypi.org/simple",
                     "--target", str(staged), "-r", str(REQUIREMENTS)], ctx, "packages.log", 900)
            staged.replace(packages)
    ctx.stage("Verifying local 3D runtime")
    process([python, "-s", "-B", str(WORKER), workflow, str(source), str(packages)], ctx, "provider-check.log", 180)
    (source / (".soceth-ready-" + packages.name)).touch()


def model_files(model, registered):
    from .models import file_location

    files = {entry["path"].split("/", 1)[1]: file_location(entry, registered) for entry in model["files"]}
    if model["workflow"] == "triposg":
        # Diffusers fetches named config and checkpoint files from the same component folder.
        for name in ("image_encoder_dinov2", "transformer", "vae"):
            entries = {key: path.absolute() for key, path in files.items() if key.startswith(name + "/")}
            if len({path.parent for path in entries.values()}) != 1 or any(
                    path.name != PurePosixPath(key).name for key, path in entries.items()):
                raise ValueError("Use the same official TripoSG snapshot directory for each component's config and weights.")
    return files


class Local3D:
    def run(self, ctx, model, **inputs):
        from .models import installation_for, is_ready

        if not is_ready(model):
            raise ValueError(f"Install {model['name']} in Model Manager.")
        host = inspect(probe=True)
        runtime = host["runtime"]
        python = runtime.get("python")
        workflow = model["workflow"]
        if not ready(workflow, python) or runtime["status"] != "verified":
            raise ValueError("Prepare this model's local 3D runtime again in Model Manager.")
        registered = installation_for(model)["body"].get("external_files", {})
        files = {key: str(path) for key, path in model_files(model, registered).items()}
        execution = inputs.get("execution") or model["configs"][0]
        candidates = []
        for entry in runtime["devices"]:
            if not entry["ok"]:
                continue
            if any(card["name"] == entry["name"] and card["capability"] == entry["capability"] and
                   card["total"] >= execution["total_vram"] - 256 and card["free"] >= execution["free_vram"]
                   for card in host["gpus"]):
                candidates.append(entry)
        if not candidates:
            raise ValueError(f"This local 3D profile needs {execution['free_vram']} MiB of free GPU memory.")
        device = max(candidates, key=lambda row: row["total"])
        spec = ctx.out / "local3d.json"
        write_json(spec, {"files": files, "image": str(inputs["image"]), "target": str(inputs["target"]),
                          "seed": inputs["seed"], "device": f"cuda:{device['index']}"})
        ctx.stage("Reconstructing with " + model["name"])
        process([python, "-s", "-B", str(WORKER), workflow, str(source_path(workflow)), str(package_path(python)),
                 "--job", str(spec)], ctx, "reconstruction.log", 10800)
        return {"model": model, "seed": inputs["seed"], "execution": execution,
                "runtime": {"torch": runtime["torch"], "cuda": runtime["cuda"], "device": device["name"],
                            "source_revision": SOURCES[workflow][1]}}
