import hashlib
import json
import os
import shutil
from pathlib import Path

import httpx

from ..core import RUNTIME, ROOT, db, now, query, safe, setting, write_json
from ..locations import configured, location
from .registry import load
from .recommendations import PRIMARY, recommend

COMFY_DATA = location("comfy_data", RUNTIME / "comfy-data")
MODELS = location("models", COMFY_DATA / "models")
CATALOG = load()
OLLAMA = "http://127.0.0.1:" + str(configured("ollama_port") or 11434)
COMFY = "http://127.0.0.1:" + str(configured("comfy_port") or 8189)


def registry():
    return {**CATALOG, **setting("custom_models", {})}


def selection(operation, project=None):
    key = "model:" + operation
    if project:
        identifier = setting(f"{project}:{key}")
        if identifier:
            return identifier, "project"
    identifier = setting(key)
    return (identifier, "global") if identifier else (None, "automatic")


def local(timeout=10):
    return httpx.Client(timeout=timeout, trust_env=False)


def model_for(operation, identifier=None, project=None, configuration=None):
    preferred, _ = selection(operation, project)
    identifier = identifier or preferred
    all_models = registry()
    if not identifier and configuration:
        identifier = configuration["recommendations"].get(operation, {}).get("model")
    model = all_models.get(identifier) if identifier else next((candidate for candidate in all_models.values()
                          if candidate["operation"] == operation and candidate["verification"] == "measured"
                          and not candidate.get("manual_only")), None)
    if not model or model["operation"] != operation:
        raise ValueError("Select a registered model for this operation. A saved choice may be missing from the catalog.")
    return model


def ollama_tags():
    try:
        with local(3) as client:
            response = client.get(OLLAMA + "/api/tags")
            response.raise_for_status()
        return {row["name"]: row for row in response.json()["models"]}
    except (httpx.HTTPError, KeyError, ValueError):
        return offline_ollama_tags()


def ollama_model_home():
    legacy = RUNTIME / "ollama-models"
    return Path(configured("ollama_models") or os.getenv("OLLAMA_MODELS") or
                (legacy if legacy.is_dir() else Path.home() / ".ollama/models"))


def offline_ollama_tags():
    home = ollama_model_home()
    tags = {}
    for layout in ("manifests", "manifests-v2"):
        for path in (home / layout).glob("*/*/*/*"):
            try:
                if not path.is_file() or path.stat().st_size > 1024 ** 2:
                    continue
                raw = path.read_bytes()
                manifest = json.loads(raw)
                layers = manifest["layers"]
                if not any(item["mediaType"] == "application/vnd.ollama.image.model" for item in layers):
                    continue
                pinned = hashlib.sha256(raw).hexdigest()
                for blob in [manifest["config"], *layers]:
                    key = blob["digest"].removeprefix("sha256:")
                    if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
                        raise ValueError("Invalid blob digest.")
                    file = home / "blobs" / ("sha256-" + key)
                    if file.stat().st_size != blob["size"]:
                        raise ValueError("Truncated blob.")
                    if blob["mediaType"] == "application/vnd.docker.distribution.manifest.v2+json" and blob["size"] < 1024 ** 2:
                        if hashlib.sha256(file.read_bytes()).hexdigest() == key:
                            pinned = key
                scope, name, tag = path.parts[-3:]
                model = f"{name}:{tag}" if scope == "library" else f"{scope}/{name}:{tag}"
                tags[model] = {"name": model, "digest": pinned, "size": sum(item["size"] for item in layers)}
            except (OSError, KeyError, TypeError, ValueError):
                continue
    return tags


def installation_for(model):
    return query("SELECT body,created FROM installations WHERE id=?", (model["id"],))


def artifacts(model):
    return {item["path"]: (item["size"], item["sha256"]) for item in model["files"]}


def file_location(entry, registered):
    return Path(registered[entry["path"]]) if entry["path"] in registered else safe(MODELS, entry["path"])


def reusable_external_files(model):
    """Reuse pinned files already verified by a local registration."""
    paths = {}
    expected = artifacts(model)
    for record in query("SELECT body FROM installations", many=True):
        body = record["body"]
        verified = body.get("verified_files", {})
        pinned = artifacts(body.get("model", {"files": []}))
        for name, file in body.get("external_files", {}).items():
            if name not in expected or expected[name] != pinned.get(name) or (MODELS / name).exists():
                continue
            path = Path(file)
            try:
                stats = path.stat()
            except OSError:
                continue
            if stats.st_size == expected[name][0] and stats.st_mtime_ns == verified.get(name):
                paths.setdefault(name, str(path))
    return paths


def is_ready(model, tags=None):
    if model.get("bundled_with"):
        return is_ready(CATALOG[model["bundled_with"]], tags)
    if model["provider"] == "blender":
        from ..core import blender_path
        return bool(blender_path())
    if model["provider"] == "ollama":
        tags = ollama_tags() if tags is None else tags
        return tags.get(model["tag"], {}).get("digest", "").removeprefix("sha256:") == model["revision"]
    installation = installation_for(model)
    if not installation:
        return False
    body = installation["body"]
    if artifacts(body.get("model", {"files": []})) != artifacts(model):
        return False
    registered, verified = body.get("external_files", {}), body.get("verified_files", {})
    try:
        return all((stats := file_location(entry, registered).stat()).st_size == entry["size"]
                   and verified.get(entry["path"]) == stats.st_mtime_ns for entry in model["files"])
    except OSError:
        return False


def status(project=None, configuration=None):
    tags = ollama_tags() if configuration is None else configuration["tags"]
    rows = []
    for model in registry().values():
        old = installation_for(model)
        installed = is_ready(model, tags)
        chosen, scope = selection(model["operation"], project)
        if not chosen and configuration:
            chosen = configuration["recommendations"].get(model["operation"], {}).get("model")
        source = ("Custom" if model.get("custom") else "Existing Local"
                  if installed and (not old or not old["body"].get("owned")) else "Managed")
        rows.append({**model, "installed": installed, "selected": chosen == model["id"],
                     "selection_scope": scope if chosen == model["id"] else None, "source_type": source,
                     "size": model["disk_bytes"], "installation": ({"created": old["created"],
                        "managed": bool(old["body"].get("owned")),
                        "removable": bool(old["body"].get("owned")) or model["provider"] in {"comfy", "local3d"}} if old else None),
                     "compatibility": configuration["models"][model["id"]] if configuration else None})
    return rows


def digest(path, ctx):
    sha = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(2**20):
            ctx.check()
            sha.update(block)
    return sha.hexdigest()


def download(entry, path, ctx, prior, total):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        if path.stat().st_size == entry["size"] and digest(path, ctx) == entry["sha256"]:
            return False
        raise ValueError(f"Existing model file differs from the pinned hash: {path.name}. Move it before retrying.")
    partial = path.with_name(path.name + ".partial")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > entry["size"]:
        partial.unlink()
        offset = 0
    if offset < entry["size"]:
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            with client.stream("GET", entry["source"], headers=headers) as response:
                response.raise_for_status()
                if response.status_code == 200:
                    offset = 0
                elif not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ValueError("The download server returned an unexpected byte range.")
                with partial.open("ab" if offset else "wb") as stream:
                    threshold = offset
                    for chunk in response.iter_bytes(2**20):
                        ctx.check()
                        offset += len(chunk)
                        if offset > entry["size"]:
                            raise ValueError("Downloaded data exceeded the file size in the catalog.")
                        stream.write(chunk)
                        if offset >= threshold:
                            ctx.stage("Downloading " + path.name, (prior + offset) / total)
                            threshold = offset + 16 * 2**20
    ctx.stage("Verifying " + path.name)
    if partial.stat().st_size != entry["size"] or digest(partial, ctx) != entry["sha256"]:
        partial.unlink()
        raise ValueError(f"Size or SHA256 mismatch for {path.name}. Retry to fetch a fresh copy.")
    os.replace(partial, path)
    return True


def install(model, ctx):
    if model["provider"] == "ollama":
        existed = is_ready(model)
        if existed:
            return {"model": model, "reused": True}
        if model.get("custom"):
            raise ValueError("Custom Ollama models must already be installed locally.")
        previous = query("SELECT body FROM installations WHERE id=?", (model["id"],))
        with local(180) as client, client.stream("POST", OLLAMA + "/api/pull",
                json={"model": model["tag"], "stream": True}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                ctx.check()
                item = json.loads(line)
                if item.get("error"):
                    raise ValueError(item["error"])
                total = item.get("total")
                ctx.stage(item.get("status", "Downloading"), item.get("completed", 0) / total if total else None)
        tags = ollama_tags()
        if not is_ready(model, tags):
            raise ValueError("Ollama returned a revision other than the pinned model tag.")
        provenance = {"model": model, "digest": tags[model["tag"]]["digest"], "owned": not existed or bool(previous and previous["body"]["owned"]),
                      "installation_path": os.getenv("SOCETH_OLLAMA_MODELS", str(Path.home() / ".ollama/models"))}
    elif model["provider"] in {"comfy", "local3d"} and not model.get("bundled_with"):
        if model["provider"] == "local3d":
            from .local3d import provision
            provision(model, ctx)
        if is_ready(model):
            return {"model": model, "reused": True}
        MODELS.mkdir(parents=True, exist_ok=True)
        external = reusable_external_files(model)
        local_files = [item for item in model["files"] if item["path"] not in external]
        needed = 0
        for item in local_files:
            path = safe(MODELS, item["path"])
            partial = path.with_name(path.name + ".partial")
            if not path.is_file():
                needed += max(0, item["size"] - (partial.stat().st_size if partial.exists() else 0))
        if shutil.disk_usage(MODELS).free < needed + 1024**3:
            raise ValueError("Free disk space is insufficient for this model and its working files.")
        total = sum(item["size"] for item in local_files)
        old = query("SELECT body FROM installations WHERE id=?", (model["id"],))
        previous = old["body"] if old else {}
        same_root = Path(previous.get("installation_path", MODELS)).resolve() == MODELS.resolve()
        previous_pins = artifacts(previous.get("model", {"files": []}))
        previous_verified = previous.get("verified_files", {})
        owned = {item["path"] for item in local_files if same_root and item["path"] in previous.get("owned", [])
                 and previous_pins.get(item["path"]) == (item["size"], item["sha256"])}
        verified = {}
        prior = 0
        for item in local_files:
            name = item["path"]
            path = safe(MODELS, name)
            downloaded = download(item, path, ctx, prior, total)
            verified[name] = path.stat().st_mtime_ns
            if downloaded:
                owned.add(name)
                record_installation(model["id"], {"model": model, "owned": sorted(owned),
                                    "installation_path": str(MODELS), "verified_files": verified})
            elif previous_verified.get(name) != verified[name]:
                owned.discard(name)
            prior += item["size"]
        verified.update({name: Path(file).stat().st_mtime_ns for name, file in external.items()})
        if model["provider"] == "local3d":
            from .local3d import model_files
            model_files(model, external)
        provenance = {"model": model, "owned": sorted(owned), "installation_path": str(MODELS), "verified_files": verified}
        if external:
            provenance["external_files"] = external
    else:
        raise ValueError("This component comes from its parent workflow or application installation.")
    record_installation(model["id"], provenance)
    return provenance


def record_installation(identifier, provenance):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO installations VALUES (?,?,?)",
                     (identifier, json.dumps(provenance), now()))


def managed_removal(model, installation, other_installations):
    root = MODELS.resolve()
    if Path(installation.get("installation_path", MODELS)).resolve() != root:
        return [], {}
    external_paths = {Path(path).resolve() for row in [installation, *other_installations.values()]
                      for path in row.get("external_files", {}).values()}
    pinned = artifacts(model)
    paths, transfers = [], {}
    for name in set(installation.get("owned", [])) & pinned.keys():
        path = root / name
        if path.resolve() != path or path in external_paths or not path.is_file():
            continue
        size, _ = pinned[name]
        stats = path.stat()
        if stats.st_size != size or installation.get("verified_files", {}).get(name) != stats.st_mtime_ns:
            continue
        users = [key for key, row in other_installations.items()
                 if name in artifacts(row.get("model", {"files": []}))
                 and name not in row.get("external_files", {})
                 and row.get("model", {}).get("provider") in {"comfy", "local3d"}
                 and Path(row.get("installation_path", MODELS)).resolve() == root]
        if users:
            transfers.setdefault(users[0], set()).add(name)
        else:
            paths.append(path)
    return paths, transfers


def removal_plan(model):
    old = installation_for(model)
    if not old:
        raise ValueError("This model has no Studio installation or registration to remove.")
    body = old["body"]
    if model["provider"] == "ollama" and body.get("owned"):
        if not is_ready(model):
            raise ValueError("The current Ollama tag differs from the managed revision or is unavailable.")
        return {"mode": "managed", "recoverable_bytes": None}
    if model["provider"] not in {"comfy", "local3d"}:
        raise ValueError("This local Ollama model is managed outside Studio.")
    others = {row["id"]: row["body"] for row in query("SELECT id,body FROM installations WHERE id<>?",
              (model["id"],), many=True)}
    paths, _ = managed_removal(model, body, others)
    plan = {"mode": "managed" if body.get("owned") else "unregister",
            "recoverable_bytes": sum(path.stat().st_size for path in paths)}
    if body.get("external_files") and model["provider"] == "comfy":
        plan["restart"] = True
    return plan


def remove(model):
    key = model["id"]
    plan = removal_plan(model)
    if model["provider"] == "ollama":
        with local() as client:
            client.request("DELETE", OLLAMA + "/api/delete", json={"model": model["tag"]}).raise_for_status()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if model["provider"] in {"comfy", "local3d"}:
            rows = {row["id"]: json.loads(row["body"]) for row in conn.execute("SELECT id,body FROM installations")}
            installation = rows.pop(key)
            paths, transfers = managed_removal(model, installation, rows)
            for identifier, files in transfers.items():
                other = rows[identifier]
                other["owned"] = sorted(set(other.get("owned", [])) | files)
                conn.execute("UPDATE installations SET body=? WHERE id=?", (json.dumps(other), identifier))
            for path in paths:
                path.unlink(missing_ok=True)
        conn.execute("DELETE FROM installations WHERE id=?", (key,))
    return {"removed": key, "restart": plan.get("restart", False)}

