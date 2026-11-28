"""Exercise installed local AI models through real job entry points in the QA project."""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("qa", type=Path)
parser.add_argument("stage", choices=("interpret", "generate", "text3d", "text3d-output", "graph"))
parser.add_argument("--model", default="pixal3d", help="Installed image-to-3D variant for the generate check.")
parser.add_argument("--texture-size", type=int, choices=(256, 512, 1024, 2048))
args = parser.parse_args()
registry = Path(os.getenv("SOCETH_DATA", ROOT / "user_data")) / "studio.sqlite3"
qa = args.qa.resolve()
os.environ["SOCETH_DATA"] = str(qa)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from PIL import Image
from services.backend.core import TOKEN_FILE, db, project_file, query
from services.backend.main import app
from services.backend.media import validate_glb, validate_textured_glb
from services.backend.providers.models import CATALOG, COMFY, is_ready, local
from services.backend.providers.registry import TEXTURED
from services.backend.providers.workflows import flux_graph, pixal_graph, validate_graph


def response(value):
    if not value.is_success:
        raise RuntimeError(value.text)
    return value.json()


def run(client):
    permit = client.post("/api/session", headers={"Authorization": "Bearer " + TOKEN_FILE.read_text(encoding="ascii")})
    assert permit.status_code == 204
    projects = response(client.get("/api/projects"))
    project = next(row["id"] for row in projects if row["name"] == "Acceptance test")
    path = f"/api/projects/{project}"
    if registry.is_file():
        with sqlite3.connect(registry) as source, db() as dest:
            installations = source.execute("SELECT id,body,created FROM installations").fetchall()
            dest.executemany("INSERT OR REPLACE INTO installations VALUES (?,?,?)", installations)

    def job(kind, expires, **payload):
        identifier = response(client.post(path + "/jobs", json={"kind":kind, **payload}))["id"]
        print(f"{kind} queued {identifier}", flush=True)
        deadline = time.monotonic() + expires
        last = None
        while time.monotonic() < deadline:
            row = response(client.get(f"/api/jobs/{identifier}"))
            stages = (row["status"], row["stage"])
            if stages != last:
                print(f"{identifier} · {row['stage']}", flush=True)
                last = stages
            if row["status"] in {"completed","failed","cancelled"}:
                if row["status"] == "completed" and kind in {"interpret", "generate", "concept"} and not row["result"].get("metrics"):
                    time.sleep(1)
                    continue
                if row["status"] != "completed":
                    raise RuntimeError(json.dumps({"error":row["error"],"result":row["result"]}, ensure_ascii=False))
                return row
            time.sleep(1)
        client.post(f"/api/jobs/{identifier}/cancel")
        raise TimeoutError(f"{kind} timed out after {expires}s.")

    if args.stage == "text3d-output":
        inference = json.loads((qa / "ai-text3d.json").read_text(encoding="utf-8"))
        assert inference["status"] == "passed"
        asset = response(client.get("/api/assets/" + inference["result"]["asset"]))
        assert asset["project"] == project
        original = next(revision for revision in asset["revisions"] if revision["parent"] is None)
        provenance = original["body"]["provenance"]
        assert provenance["model"]["id"] == "pixal3d"
        concept_id = asset["body"]["reference"]
        assert provenance["input_file"] == concept_id
        concept, record = project_file(project, concept_id, {"concept"})
        metadata = record["body"]
        assert metadata["model"]["id"] == "flux-klein" and metadata["composed_prompt"]
        with Image.open(concept) as image:
            assert image.format == "PNG"
            size = list(image.size)
            image.verify()
        preview, _ = project_file(project, original["body"]["preview"], {"preview"})
        document = validate_glb(preview)
        images = document.get("images", [])
        assert len(images) == len(inference["result"]["textures"]) and all("bufferView" in image for image in images)
        assert document.get("textures") and document.get("materials")
        concept_job = next(row for row in query(
            "SELECT result FROM jobs WHERE project=? AND kind='concept' AND status='completed'", (project,), True)
            if row["result"]["file"] == concept_id)

        budget = 2500
        current = next(revision for revision in asset["revisions"] if revision["id"] == asset["current"])
        if not current["parent"] or current["body"]["report"]["triangles"] > budget:
            job("optimize", 300, asset=asset["id"], triangles=budget)
        report = job("validate", 300, asset=asset["id"])["result"]["report"]
        assert report["valid"] and 0 < report["triangles"] <= budget and report["textures_present"]
        job("approve", 300, asset=asset["id"], review_notes="Generated fixture reviewed for acceptance testing.")
        export = job("export", 300, asset=asset["id"], preset="Unity 6 URP")["result"]
        package = qa / "text3d-unity-export.zip"
        package.write_bytes(client.get("/api/files/" + export["file"]).raise_for_status().content)
        with ZipFile(package) as bundle:
            assert bundle.testzip() is None
            assert all(any(name.endswith(suffix) for name in bundle.namelist())
                       for suffix in (".fbx", ".glb", "/soceth-manifest.json"))
        levels = [lod["triangles"] for lod in export["manifest"]["lods"]]
        assert levels[0] == report["triangles"] and levels[0] > levels[1] > levels[2] > 0
        return {"asset": asset["id"], "original_revision": original["id"], "concept": str(concept),
                "concept_size": size, "concept_metrics": concept_job["result"]["metrics"],
                "preview": str(preview), "lod_triangles": levels, "unity_package": str(package)}

    if args.stage == "graph":
        with local(10) as comfy:
            info = comfy.get(COMFY + "/object_info").raise_for_status().json()
        for model in ("flux-klein", "pixal3d", "pixal3d-bf16", "trellis2-int8", "trellis2-bf16"):
            item = CATALOG[model]
            for execution in item["configs"]:
                profile = execution["profile"]
                if model == "flux-klein":
                    graph, sink = flux_graph(info, item, "Test concept", 42, profile, "qa")
                else:
                    graph, sink = pixal_graph(info, item, "soceth/qa.png", 42, profile, "qa", 1024)
                missing = validate_graph(info, graph, require_artifacts=False)
                if item["workflow"] == "trellis2":
                    kinds = {node["class_type"] for node in graph.values()}
                    assert {"Trellis2Conditioning", "UnwrapMesh", "BakeTextureFromVoxel",
                            "BakeNormalMapFromMesh", "BakeAmbientOcclusion"} <= kinds
                    assert not {"Pixal3DConditioning", "LoadMoGeModel"} & kinds
                    expected = Path(item["files"][0]["path"]).name
                    assert [node["inputs"]["unet_name"] for node in graph.values()
                            if node["class_type"] == "UNETLoader"] == [expected]
                    assert graph[sink]["class_type"] == "Save3DAdvanced"
                print(model, profile, sink, len(graph), "missing files", missing)
        return {"native_node_schemas": "passed", "inference": "not run by this schema check"}

    if args.stage == "interpret":
        assert is_ready(CATALOG["qwen-coder"]), "Pinned model is absent from local Ollama."
        asset = next(row for row in response(client.get("/api/assets", params={"project":project})) if row["name"] == "QA Stone")
        details = response(client.get("/api/assets/" + asset["id"]))
        revision = next(row for row in details["revisions"] if row["id"] == asset["current"])
        initial = revision["body"]["report"]["objects"][0]["dimensions"][0]
        row = job("interpret", 360, asset=asset["id"],
                  prompt="Make only the object named QA_Stone 20 percent wider on X. Leave Y and Z unchanged.")
        operations = row["result"]["proposal"]["operations"]
        assert len(operations) == 1, operations
        edit = job("edit", 180, asset=asset["id"], proposal=row["id"], confirmed=True)
        final = edit["result"]["report"]["objects"][0]["dimensions"][0]
        assert abs(final - initial * 1.2) < .05, [initial, final, operations]
        response(client.post(f"/api/assets/{asset['id']}/restore/{revision['id']}"))
        return {"operations": operations, "width_before": initial, "width_after": final, "metrics": row["result"].get("metrics")}

    if args.stage == "generate":
        model = CATALOG[args.model]
        assert model["operation"] == "generate" and is_ready(model), "Select an installed image-to-3D weight set."
        file = qa / "reference.png"
        with file.open("rb") as image:
            reference = response(client.post(path + "/files", files={"file":(file.name, image)}))["id"]
    else:
        assert is_ready(CATALOG["flux-klein"]), "Pinned FLUX.2 weight set is missing."
        assert is_ready(CATALOG["pixal3d"]), "Pinned Pixal3D weight set is missing."
        concept = job("concept", 2400, prompt="A stylized squat wooden chest with copper corners", seed=172410)
        reference = concept["result"]["file"]
        assert client.get("/api/files/" + reference).content.startswith(b"\x89PNG")
        print("FLUX.2 concept verified", reference, concept["result"].get("metrics"), flush=True)
    explicit = {"model": args.model, "source_texture_size": args.texture_size,
                "allow_limited_3d": model["output_capability"] not in TEXTURED} if args.stage == "generate" else {}
    result = job("generate", 3600, file=reference, name="AI QA Chest", seed=172409, **explicit)["result"]
    assert result["report"]["triangles"] > 0, result
    if args.stage != "generate" or model["output_capability"] in TEXTURED:
        assert result["report"]["textures_present"], result
    asset = response(client.get("/api/assets/" + result["asset"]))
    revision = next(row for row in asset["revisions"] if row["id"] == result["revision"])
    preview = client.get("/api/files/" + revision["body"]["preview"])
    assert preview.is_success and preview.content.startswith(b"glTF")
    evidence = {"asset": asset["id"], "triangles": result["report"]["triangles"],
                "textures": result["report"]["textures"], "metrics": result.get("metrics")}
    if args.stage == "generate":
        original = next(row for row in asset["revisions"] if row["parent"] is None)
        provenance = original["body"]["provenance"]
        assert provenance["model"]["id"] == args.model
        evidence["model"] = provenance["model"]["id"]
        if model["output_capability"] in TEXTURED:
            source, _ = project_file(project, original["body"]["source"], {"source_mesh"})
            evidence["source_textures"] = validate_textured_glb(source, model["output_capability"])
            if args.texture_size:
                assert evidence["source_textures"]["base_color_sizes"] == [(args.texture_size, args.texture_size)]
    if args.stage == "text3d":
        evidence.update(concept=reference, concept_metrics=concept["result"]["metrics"])
    return evidence


try:
    with TestClient(app, base_url="http://127.0.0.1:17863") as client:
        result = {"status":"passed", "result":run(client)}
except Exception as error:
    result = {"status":"failed", "error":repr(error)}
(qa / f"ai-{args.stage}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
raise SystemExit(1 if result["status"] == "failed" else 0)

