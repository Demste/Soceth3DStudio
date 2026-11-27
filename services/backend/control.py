import asyncio
import json
import os
import platform
import shutil
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from typing import Literal
from pathlib import Path
from pydantic import Field

from .blender import bridge
from .core import DATA, LOGS, TOKEN_FILE, blender_path, db, now, profile, query, set_setting, setting, write_json
from .hardware import gpus
from .jobs import enqueue
from .locations import CONFIG, preferences
from .providers.discovery import configuration, discover, download_plan
from .providers.inference import random_seed
from .providers.models import CATALOG, COMFY, OLLAMA, local, model_for, registry, removal_plan, status
from .providers.recommendations import DEFAULT_POLICY, PRIMARY
from .routes import active_bible, asset_row, found, project_row
from .schemas import Bible, ID, JobRequest, Strict, Text
from .updates import check as check_updates

router = APIRouter(prefix="/api")


@router.get("/app-update")
def app_update(refresh: bool = False):
    return check_updates(refresh)


class Configuration(Strict):
    profile: Literal["AUTO", "LOW_VRAM", "BALANCED", "QUALITY", "HIGH_QUALITY", "CUSTOM"]
    advanced: bool | None = None


class ModelSelection(Strict):
    model: Text | None = None
    operation: Literal["generate", "concept", "interpret"] | None = None
    project: ID | None = None


class ModelPlan(Strict):
    models: list[Text] = Field(max_length=12)


class LocalRegistration(Strict):
    model: Text | None = None
    roots: list[str] = Field(default_factory=list, max_length=12)
    tag: Text | None = None
    gguf: str | None = Field(default=None, max_length=2048)
    license: str = Field(default="", max_length=256)


class ProviderConfiguration(Strict):
    comfy_port: int = Field(ge=1024, le=65535)
    ollama_port: int = Field(ge=1024, le=65535)
    comfy: str = Field(default="", max_length=2048)
    comfy_python: str = Field(default="", max_length=2048)


class RecommendationPolicy(Strict):
    weights: dict[Literal["verification", "output_quality", "compatibility", "stability", "installed",
                          "commercial", "features", "headroom", "performance", "provenance"], int]


class Comparison(Strict):
    project: ID
    first: ID
    second: ID


class Finish(Strict):
    id: ID


class Poll(Strict):
    busy: bool
    capabilities: list[Literal["production_v1", "structure_v1"]] = []


def probe(url):
    try:
        with local(1.5) as client:
            reply = client.get(url)
            return reply.json() if reply.is_success else {}
    except (httpx.HTTPError, ValueError):
        return {}


@router.get("/status")
def system():
    with ThreadPoolExecutor(max_workers=3) as pool:
        comfy_task = pool.submit(probe, COMFY + "/system_stats")
        ollama_task = pool.submit(probe, OLLAMA + "/api/version")
        gpu_task = pool.submit(gpus)
        comfy, ollama, cards = comfy_task.result(), ollama_task.result(), gpu_task.result()
    return {"app": "Soceth 3D Studio", "profile": profile(cards), "profile_setting": setting("profile", "AUTO"),
            "gpus": cards, "cpu": os.getenv("PROCESSOR_IDENTIFIER") or platform.processor(),
            "cores": os.cpu_count(), "disk_free": shutil.disk_usage(DATA).free,
            "blender": str(blender_path() or ""), "blender_connected": bridge.connected(),
            "blender_production": bridge.supports("production_v1"),
            "blender_structure": bridge.supports("structure_v1"),
            "comfy": bool(comfy), "ollama": bool(ollama), "ollama_version": ollama.get("version"),
            "pytorch": comfy.get("system", {}).get("pytorch_version"), "comfy_devices": comfy.get("devices", []),
            "last_project": setting("last_project"), "token_file": str(TOKEN_FILE),
            "advanced": setting("advanced", False)}


@router.put("/settings")
def settings(body: Configuration):
    set_setting("profile", body.profile)
    if body.advanced is not None:
        set_setting("advanced", body.advanced)
    return system()


@router.get("/models")
def models(project: ID | None = None):
    if project:
        project_row(project)
    return status(project)


@router.get("/model-configuration")
def model_configuration(project: ID | None = None):
    if project:
        project_row(project)
    state = configuration(project)
    tags = state["tags"]
    rows = status(project, state)
    existing = discover(state["hardware"], tags)
    del state["tags"]
    return {**state, "catalog": rows, "existing": existing,
            "providers": {key: preferences().get(key, default) for key, default in
                          (("comfy_port", 8189), ("ollama_port", 11434), ("comfy", ""), ("comfy_python", ""))},
            "policy": {**DEFAULT_POLICY, **(setting("recommendation_policy") or {})}}


@router.put("/model-configuration/policy")
def recommendation_policy(body: RecommendationPolicy):
    if any(not 0 <= value <= 20 for value in body.weights.values()):
        raise HTTPException(422, "Use preference weights between 0 and 20.")
    set_setting("recommendation_policy", body.weights)
    return body.weights


@router.post("/model-configuration/comparison")
def compare_models(body: Comparison):
    records = [found(query("""SELECT * FROM jobs WHERE id=? AND project=? AND status='completed'
              AND kind IN ('generate','concept','interpret')""", (key, body.project)), "Completed project run not found.")
               for key in (body.first, body.second)]
    first, second = records
    comparable = ("file", "prompt", "asset", "revision", "category", "seed", "preset", "sampling", "bible_version")
    if body.first == body.second or first["kind"] != second["kind"] or any(
        first["body"].get(key) != second["body"].get(key) for key in comparable
    ):
        raise HTTPException(422, "Compare two runs with the same source, settings, seed and Art Bible.")
    output = []
    for record in records:
        result, payload = record["result"], record["body"]
        revision, asset = result.get("revision"), result.get("asset")
        saved = query("SELECT body FROM revisions WHERE id=? AND asset=?", (revision, asset)) if revision else None
        report = result.get("report", {})
        output.append({"id": record["id"], "model": payload["model"],
                       "profile": (payload.get("execution") or {}).get("profile"),
                       "file": result.get("file") or (saved["body"]["preview"] if saved else None),
                       "triangles": report.get("triangles"), "seconds": result.get("metrics", {}).get("seconds"),
                       "operations": len(result["proposal"]["operations"]) if result.get("proposal") else None})
    return [{key: value for key, value in row.items() if value is not None} for row in output]


@router.get("/diagnostics/{name}")
def recent_log(name: Literal["studio", "comfyui", "ollama"]):
    filename = {"studio": "studio.jsonl", "comfyui": "comfyui.log", "ollama": "ollama.log"}[name]
    path = LOGS / filename
    if not path.is_file():
        return {"text": ""}
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 24 * 1024))
        lines = stream.read().decode("utf-8", errors="replace").splitlines()
    return {"text": "\n".join(lines[-36:])}


@router.post("/model-configuration/dismiss")
def dismiss_hardware_change():
    set_setting("hardware_changed", False)
    return {"ok": True}


@router.put("/model-configuration/providers")
def configure_providers(body: ProviderConfiguration):
    if len({body.comfy_port, body.ollama_port}) < 2 or any(
        value == int(os.getenv("SOCETH_PORT", "17863")) for value in (body.comfy_port, body.ollama_port)
    ):
        raise HTTPException(422, "Use different loopback ports for both providers and Studio.")
    for path, directory in ((body.comfy, True), (body.comfy_python, False)):
        if path and (not Path(path).is_absolute() or not (Path(path).is_dir() if directory else Path(path).is_file())):
            raise HTTPException(422, "Select a valid absolute local provider path.")
    write_json(CONFIG, {**preferences(), **body.model_dump()})
    return {"restart": True}


@router.put("/models/selection")
def select_model(body: ModelSelection):
    item = found(registry().get(body.model), "Select a registered AI model.") if body.model else None
    operation = item["operation"] if item else body.operation
    if operation not in PRIMARY or (body.operation and body.operation != operation):
        raise HTTPException(422, "Select the matching generation or editing capability.")
    if body.project:
        project_row(body.project)
    prefix = f"{body.project}:" if body.project else ""
    set_setting(prefix + "model:" + operation, body.model)
    return {"model": body.model}


@router.post("/models/plan")
def model_download_plan(body: ModelPlan):
    for key in body.models:
        found(registry().get(key), "Model not found.")
    return download_plan(body.models)


@router.post("/models/install-plan", status_code=202)
def install_plan(body: ModelPlan):
    for key in body.models:
        model = found(CATALOG.get(key), "Managed model not found.")
        if model["operation"] not in PRIMARY:
            raise HTTPException(422, "Choose an installable model variant.")
    plan = download_plan(body.models)
    jobs = [enqueue(None, "install", {"model": row["id"]})["id"] for row in plan["models"] if not row["installed"]]
    return {"jobs": jobs, "plan": plan}


@router.post("/models/register", status_code=202)
def register_model(body: LocalRegistration):
    if bool(body.model) == bool(body.tag):
        raise HTTPException(422, "Select a ComfyUI catalog variant or provide a local Ollama tag.")
    if body.model:
        found(CATALOG.get(body.model), "Catalog variant not found.")
    return enqueue(None, "register", body.model_dump())


@router.post("/models/{model}/install", status_code=202)
def install_model(model: str):
    found(CATALOG.get(model), "Model not found.")
    return enqueue(None, "install", {"model": model})


@router.delete("/models/{model}", status_code=202)
def remove_model(model: str):
    item = found(CATALOG.get(model), "Model not found.")
    removal_plan(item)
    return enqueue(None, "remove", {"model": model})


@router.get("/models/{model}/removal-plan")
def model_removal_plan(model: str):
    return removal_plan(found(CATALOG.get(model), "Model not found."))


def seed(body):
    return body.seed if body.seed is not None else random_seed()


@router.post("/projects/{project}/jobs", status_code=202)
def submit(project: ID, body: JobRequest):
    project_row(project)
    payload = body.model_dump()
    kind = body.kind
    if kind == "cleanup" and "profile" not in body.model_fields_set:
        payload["profile"] = "preserve_structure"
    if kind in {"import", "generate"} and not body.file:
        raise HTTPException(422, "Select a source file first.")
    if kind in {"concept", "interpret"} and not body.prompt.strip():
        raise HTTPException(422, "Enter a description or edit instruction.")
    if kind not in {"import", "generate", "concept"}:
        asset = asset_row(body.asset)
        if asset["project"] != project:
            raise HTTPException(404, "Asset does not belong to this project.")
        payload["revision"] = body.revision or asset["current"]
        payload["category"] = asset["category"]
    if kind in {"concept", "interpret", "generate"}:
        state = configuration(project)
        item = model_for(kind, body.model, project, state)
        candidate = state["models"][item["id"]]
        payload["model"] = item["id"]
        payload["execution"] = candidate["execution"]
        payload["model_readiness"] = candidate["status"]
        payload["model_reasons"] = candidate["reasons"]
    if kind in {"concept", "generate"}:
        payload["seed"] = seed(body)
    bible = active_bible(project)
    bible_config = Bible.model_validate(bible["body"])
    if body.regions is not None:
        if kind not in {"palette_preview", "palette_apply"}:
            raise HTTPException(422, "Region masks are used by palette preview and application.")
        if any(region.category not in bible_config.semantic_materials for region in body.regions):
            raise HTTPException(422, "Select a material category defined in the current Art Bible.")
    payload.update(bible=bible_config.model_dump(), bible_version=bible["version"])
    return enqueue(project, kind, payload)


PUBLIC_FIELDS = """id,project,kind,status,stage,progress,error,cancel,provider_id,result,created,updated,
    json_extract(body, '$.asset') AS target_asset, json_extract(body, '$.revision') AS target_revision"""


def job_list():
    return query(f"SELECT {PUBLIC_FIELDS} FROM jobs ORDER BY created DESC LIMIT 100", many=True)


@router.get("/jobs")
def list_jobs():
    return job_list()


@router.get("/jobs/{job}")
def get(job: ID):
    return found(query("SELECT * FROM jobs WHERE id=?", (job,)), "Job was not found.")


@router.post("/jobs/{job}/cancel")
def cancel(job: ID):
    get(job)
    with db() as conn:
        conn.execute("""UPDATE jobs SET cancel=1,
            status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,
            stage=CASE WHEN status='queued' THEN 'Cancelled' ELSE stage END,updated=?
            WHERE id=? AND status IN ('queued','running')""", (now(), job))
    return get(job)


@router.post("/jobs/{job}/retry", status_code=202)
def retry(job: ID):
    row = get(job)
    if row["status"] not in {"failed", "cancelled"}:
        raise HTTPException(409, "Only failed or cancelled jobs can be retried.")
    return enqueue(row["project"], row["kind"], row["body"])


@router.websocket("/events")
async def events(socket: WebSocket):
    from .main import authorized, valid_origin
    if not authorized(socket) or not valid_origin(socket):
        await socket.close(code=1008)
        return
    await socket.accept()
    prior = None
    try:
        while True:
            rows = await asyncio.to_thread(job_list)
            encoded = json.dumps(rows)
            if prior != encoded:
                await socket.send_text(encoded)
                prior = encoded
            try:
                await asyncio.wait_for(socket.receive_text(), timeout=0.8)
            except asyncio.TimeoutError:
                pass
    except (WebSocketDisconnect, RuntimeError):
        pass


@router.post("/blender/poll")
def blend_poll(body: Poll):
    return bridge.poll(body.busy, body.capabilities)


@router.post("/blender/finish")
def blend_finish(body: Finish):
    bridge.finish(body.id)
    return {"received": True}

