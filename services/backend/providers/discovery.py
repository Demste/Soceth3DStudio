"""Artifact registration and provider discovery without copying existing weights."""

import json
import re
import shutil
import struct
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from ..core import safe, setting, set_setting, write_json
from ..hardware import FLAGS, inspect, track
from . import models
from . import local3d
from .recommendations import PRIMARY, recommend

NODE_SETS = {"pixal3d_nodes": ("Trellis2ShapeStage", "Trellis2UpsampleStage", "Save3DAdvanced"),
             "trellis2_nodes": ("Trellis2Conditioning", "Trellis2ShapeStage", "Trellis2UpsampleStage",
                                "UnwrapMesh", "BakeTextureFromVoxel", "BakeNormalMapFromMesh",
                                "BakeAmbientOcclusion", "Save3DAdvanced"),
             "flux_nodes": ("Flux2Scheduler", "EmptyFlux2LatentImage", "CLIPLoader")}
GGUF_FAMILIES = {"qwen2", "qwen3", "llama", "mistral", "gemma3", "phi3"}


def supports(base, route):
    try:
        with models.local(1.5) as client:
            response = client.get(base + route)
            if response.is_success:
                return response.json()
    except (httpx.HTTPError, ValueError):
        pass
    return {}


def configuration(project=None, *, probe=True, remember=True):
    with ThreadPoolExecutor(max_workers=4) as pool:
        hardware_job = pool.submit(inspect, probe=probe)
        comfy_job = pool.submit(supports, models.COMFY, "/system_stats")
        ollama_job = pool.submit(supports, models.OLLAMA, "/api/version")
        tags_job = pool.submit(models.ollama_tags)
        hardware, comfy, ollama, tags = hardware_job.result(), comfy_job.result(), ollama_job.result(), tags_job.result()
        nodes = {}
        if comfy:
            futures = {node: pool.submit(supports, models.COMFY, "/object_info/" + node)
                       for node in {node for group in NODE_SETS.values() for node in group}}
            nodes = {node: future.result().get(node) for node, future in futures.items()}
    deps = {"comfy": bool(comfy), "ollama": bool(ollama), "blender": bool(hardware["software"]["blender"]),
            "cuda": hardware["runtime"]["status"] == "verified",
            "local3d_python": bool(hardware["runtime"].get("python"))}
    deps.update({key: all(nodes.get(node) and not nodes[node].get("python_module", "").startswith("custom_nodes")
                         for node in group) for key, group in NODE_SETS.items()})
    choices = models.registry()
    installed = {key for key, row in choices.items() if models.is_ready(row, tags)}
    state = recommend(choices, hardware, deps, installed, setting("profile", "AUTO"),
                      setting("recommendation_policy"))
    for key, row in choices.items():
        if row["provider"] == "local3d" and key in installed:
            state["models"][key]["runtime_setup"] = not local3d.ready(row["workflow"], hardware["runtime"].get("python"))
    state.update(hardware=hardware, dependencies=deps, tags=tags, hardware_changed=track(hardware) if remember else False,
                 selections={kind: models.selection(kind, project) for kind in PRIMARY})
    state["plan"] = download_plan([state["recommendations"][key]["model"] for key in PRIMARY], tags)
    return state


def check_candidate_install(model, state=None):
    if model.get("manual_only"):
        state = configuration(remember=False) if state is None else state
        readiness = state["models"][model["id"]]
        if readiness["status"] != "compatible":
            raise ValueError("This optional candidate requires a compatible local setup. " + " ".join(readiness["reasons"]))


def default_roots(hardware, entries=()):
    cache = Path(hardware["software"]["huggingface"])
    roots = [models.MODELS, *(Path(item["models"]) for item in hardware["software"]["comfy"]), *map(Path, entries)]
    for repo in {artifact["repo"] for row in models.CATALOG.values() for artifact in row["files"]}:
        snapshots = cache / ("models--" + repo.replace("/", "--")) / "snapshots"
        if snapshots.is_dir():
            roots.extend(sorted(snapshots.iterdir())[:32])
    return list(dict.fromkeys(str(root.resolve()) for root in roots if root.is_dir()))


def locate(artifact, roots, *, standalone=False):
    relative = Path(artifact["path"])
    for directory in roots:
        root = Path(directory)
        if standalone:
            source_path = Path(artifact["source"].split("/" + artifact["revision"] + "/", 1)[-1])
            local = (root / relative, root.joinpath(*relative.parts[1:]))
            if not source_path.is_absolute() and ".." not in source_path.parts:
                local += (root / source_path,)
        else:
            local = (root / relative, root / "split_files" / relative, root / relative.name)
        for path in local:
            if path.is_file() and path.stat().st_size == artifact["size"]:
                return path
    return None


def discover(hardware, tags, entries=()):
    roots = default_roots(hardware, entries)
    matching = []
    for row in models.CATALOG.values():
        if row["operation"] not in PRIMARY:
            continue
        if row["provider"] in {"comfy", "local3d"} and all(
                locate(artifact, roots, standalone=row["provider"] == "local3d") for artifact in row["files"]):
            matching.append(row["id"])
        if row["provider"] == "ollama" and models.is_ready(row, tags):
            matching.append(row["id"])
    return {"roots": roots, "matches": matching,
            "ollama": [{"tag": name, "size": row.get("size", 0), "family": row.get("details", {}).get("family")}
                       for name, row in tags.items() if not row.get("remote_host") and ":cloud" not in name]}


def register_existing(model, paths, ctx):
    if models.is_ready(model):
        return {"model": model, "reused": True}
    if model["provider"] not in {"comfy", "local3d"} or model["operation"] not in PRIMARY:
        raise ValueError("Select a supported local generation variant or register an existing Ollama tag.")
    standalone = model["provider"] == "local3d"
    roots = default_roots(inspect(), paths)
    external, verified = {}, {}
    for entry in model["files"]:
        path = locate(entry, roots, standalone=standalone)
        if not path:
            raise ValueError(f"Missing {entry['path']}. Supply compatible ComfyUI model roots or Hugging Face snapshot directories.")
        ctx.stage("Verifying " + path.name)
        if models.digest(path, ctx) != entry["sha256"]:
            raise ValueError(f"{path.name} differs from the pinned architecture and variant.")
        if path.resolve() != safe(models.MODELS, entry["path"]):
            external[entry["path"]] = str(path.absolute())
        verified[entry["path"]] = path.stat().st_mtime_ns
    if standalone:
        local3d.model_files(model, external)
        local3d.provision(model, ctx)
    result = {"model": model, "owned": [], "external_files": external, "verified_files": verified,
              "installation_path": str(models.MODELS)}
    models.record_installation(model["id"], result)
    return {"model": model, "source": "Existing Local", "restart": not standalone}


def download_plan(identifiers, tags=None):
    all_models = models.registry()
    files, rows, cumulative = set(), [], 0
    for key in dict.fromkeys(filter(None, identifiers)):
        item = all_models[key]
        installed = models.is_ready(item, tags)
        size = 0
        if not installed and item["provider"] == "ollama":
            size = item["disk_bytes"]
        elif not installed and item["provider"] in {"comfy", "local3d"}:
            external = models.reusable_external_files(item)
            for artifact in item["files"]:
                if artifact["path"] in external or artifact["sha256"] in files:
                    continue
                files.add(artifact["sha256"])
                path = models.MODELS / artifact["path"]
                if not path.is_file() or path.stat().st_size != artifact["size"]:
                    partial = path.with_name(path.name + ".partial")
                    offset = partial.stat().st_size if partial.is_file() else 0
                    size += artifact["size"] - (offset if offset <= artifact["size"] else 0)
        cumulative += size
        rows.append({"id": key, "name": item["name"], "license": item["license"], "commercial": item["commercial"],
                     "source": item["source"], "download_bytes": size, "disk_bytes": item["disk_bytes"],
                     "vram": item["vram"], "installed": installed, "provider": item["provider"],
                     "commercial_game": item.get("commercial_game"),
                     "runtime_setup": item["provider"] == "local3d"})
    return {"models": rows, "download_bytes": cumulative}


def gguf_architecture(path):
    types = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    size = path.stat().st_size
    with path.open("rb") as source:
        def number(code):
            return struct.unpack("<" + code, source.read(struct.calcsize(code)))[0]

        def skip(length):
            if length < 0 or source.tell() + length > size:
                raise ValueError("Truncated GGUF metadata.")
            source.seek(length, 1)

        def string(read):
            length = number("Q")
            if not read:
                skip(length)
                return None
            if length > 65536:
                raise ValueError("Invalid GGUF metadata length.")
            return source.read(length).decode("utf-8")

        if source.read(4) != b"GGUF" or number("I") not in (2, 3):
            raise ValueError("Select a GGUF version 2 or 3 file.")
        number("Q")
        count = number("Q")
        if count > 65536:
            raise ValueError("Excessive GGUF metadata.")
        for _ in range(count):
            key, kind = string(True), number("I")
            if key == "general.architecture" and kind == 8:
                return string(True)
            if kind == 8:
                string(False)
            elif kind == 9:
                element, amount = number("I"), number("Q")
                if element == 8 and amount <= 1_000_000:
                    for _ in range(amount):
                        string(False)
                else:
                    skip(types[element] * amount)
            else:
                skip(types[kind])
    raise ValueError("The GGUF architecture is missing.")


def register_ollama(tag, path, license_name, ctx):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_./:-]{0,155}", tag) or ":cloud" in tag or ".." in tag:
        raise ValueError("Enter a local Ollama tag, for example qwen2.5-coder:7b.")
    tags = models.ollama_tags()
    if path and tag not in tags:
        source = Path(path).expanduser().resolve()
        try:
            architecture = gguf_architecture(source)
        except (OSError, struct.error, KeyError, UnicodeError) as error:
            raise ValueError("Unable to inspect this GGUF file.") from error
        if architecture not in GGUF_FAMILIES:
            raise ValueError(f"No supported Ollama editing adapter for {architecture}.")
        binary = shutil.which("ollama")
        if not binary or not supports(models.OLLAMA, "/api/version"):
            raise ValueError("Start local Ollama before registering a GGUF.")
        script = ctx.out / "Modelfile"
        if '"' in str(source) or "\n" in str(source):
            raise ValueError("Use a GGUF path without quotes or line breaks.")
        script.write_text(f'FROM "{source.as_posix()}"\n', encoding="utf-8")
        ctx.stage("Importing GGUF into the Ollama blob store")
        import os
        import time
        with (ctx.out / "ollama-import.log").open("w", encoding="utf-8") as log, subprocess.Popen(
            [binary, "create", tag, "-f", str(script)],
            env={**os.environ, "OLLAMA_HOST": models.OLLAMA, "OLLAMA_NO_CLOUD": "1"},
            stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS
        ) as process:
            try:
                while process.poll() is None:
                    ctx.check()
                    time.sleep(.3)
            finally:
                if process.poll() is None:
                    process.terminate()
            if process.returncode:
                raise ValueError("Ollama could not import this GGUF. See the registration job log.")
        tags = models.ollama_tags()
    row = tags.get(tag)
    if not row or row.get("remote_host") or row.get("remote_model"):
        raise ValueError("Install this local Ollama model or select its GGUF file first.")
    revision = row["digest"].removeprefix("sha256:")
    with models.local() as client:
        response = client.post(models.OLLAMA + "/api/show", json={"model": tag})
        response.raise_for_status()
        info = response.json()
    arch = info.get("model_info", {}).get("general.architecture") or row.get("details", {}).get("family")
    if arch not in GGUF_FAMILIES or "completion" not in info.get("capabilities", []):
        raise ValueError(f"This provider model cannot execute the Studio editing workflow ({arch}).")
    pinned = next((entry for entry in models.CATALOG.values() if entry.get("revision") == revision and entry.get("tag") == tag), None)
    if pinned:
        return {"model": pinned, "source": "Existing Local"}
    import hashlib
    key = "local-" + hashlib.sha256(tag.encode()).hexdigest()[:16]
    disk = row.get("size", 0)
    memory = max(4096, round(disk / (1024 ** 2) * 1.45))
    model = {**models.CATALOG["qwen-coder"], "id": key, "name": tag, "family": arch, "variant": "Local GGUF",
             "tag": tag, "revision": revision, "custom": True, "verification": "local",
             "source": "Local Ollama", "commercial": "unknown", "license": license_name or
             ("Upstream terms reported by Ollama; review required" if info.get("license") else "Unknown"),
             "precision": row.get("details", {}).get("quantization_level", "GGUF"),
             "vram": "Planning estimate. Run the same editing fixture before relying on it.",
             "disk_bytes": disk, "size": disk, "min_ram": max(8192, memory * 2),
             "configs": [{"profile": "LOW_VRAM", "free_vram": memory, "total_vram": memory,
                          "features": ["Local completion"], "evidence": "local", "note": "Provider and architecture verified; output quality untested."}]}
    set_setting("custom_models", {**setting("custom_models", {}), key: model})
    return {"model": model, "source": "Custom"}


def comfy_paths():
    locations = {"soceth": {"base_path": str(models.MODELS), "is_default": True}}
    slot = 0
    for model in models.CATALOG.values():
        if model["provider"] != "comfy":
            continue
        installation = models.installation_for(model)
        if not installation:
            continue
        for relative, file in installation["body"].get("external_files", {}).items():
            category = relative.split("/")[0]
            locations[f"registered_{slot}"] = {category: str(Path(file).parent)}
            slot += 1
    path = models.COMFY_DATA / "soceth-model-paths.yaml"
    # Comfy reads YAML; JSON is a YAML 1.2 subset and safely handles Windows paths.
    write_json(path, locations)
    return path
