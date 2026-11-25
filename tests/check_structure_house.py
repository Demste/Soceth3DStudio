"""Exercise structure optimization on the saved house without new inference."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("house", type=Path)
parser.add_argument("qa", type=Path)
parser.add_argument("--qwen", action="store_true")
args = parser.parse_args()
QA = args.qa.resolve()
QA.mkdir(parents=True, exist_ok=True)
os.environ["SOCETH_DATA"] = str(QA)
sys.path.insert(0, str(ROOT))

from services.backend.core import BASE, TOKEN_FILE, blender_path, project_file
from services.backend.main import app
from services.backend.providers.models import OLLAMA


def wait(predicate, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if value := predicate():
                return value
        except httpx.TransportError:
            pass
        time.sleep(.35)
    raise TimeoutError("The house regression did not reach the expected condition.")


def run_blender(script, *values):
    path = QA / "fixture_worker.py"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([str(blender_path()), "--background", "--factory-startup", "--disable-autoexec",
        "--python-exit-code", "1", "--python", str(path), "--", *map(str, values)],
        capture_output=True, env={**os.environ, "PYTHONUTF8": "1"}, encoding="utf-8", errors="replace", timeout=90)
    (QA / "fixture_worker.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    proc.check_returncode()


def run():
    results = {}
    ollama = None
    try:
        with TestClient(app, base_url=BASE) as client:
            client.headers["Authorization"] = "Bearer " + TOKEN_FILE.read_text(encoding="ascii")

            def ok(method, path, **kwargs):
                response = client.request(method, path, **kwargs)
                if not response.is_success:
                    raise RuntimeError(response.text)
                return response.json()

            project = ok("POST", "/api/projects", json={"name": "House structure QA"})["id"]
            endpoint = f"/api/projects/{project}"
            bible = ok("GET", endpoint + "/bible")["body"]
            bible["color_policy"] = "reference"
            bible["export_presets"]["Unity 6 URP"].update(lod_ratios=[], collider="capsule")
            ok("PUT", endpoint + "/bible", json=bible)

            def job(kind, **values):
                identifier = ok("POST", endpoint + "/jobs", json=dict(kind=kind, **values))["id"]
                def terminal():
                    row = ok("GET", "/api/jobs/" + identifier)
                    return row if row["status"] in {"completed", "failed", "cancelled"} else None
                row = wait(terminal)
                assert row["status"] == "completed", row["error"]
                return row

            def imported(path, name):
                with path.open("rb") as stream:
                    item = ok("POST", endpoint + "/files", files={"file": (path.name, stream)})
                return job("import", name=name, file=item["id"])["result"]

            source = imported(args.house, "Pixal3D House Structure Test")
            asset = source["asset"]
            asset_path = "/api/assets/" + asset
            assert source["report"]["triangles"] > 50000
            results.update(asset=asset, source=source["revision"])
            main = job("optimize", asset=asset)["result"]
            quality = main["report"]["structural_quality"]
            assert quality["target_met"] and 4750 <= quality["final_triangles"] <= 5000, quality
            assert quality["rating"] == "usable" and quality["silhouette_overlap"] >= .985
            assert quality["uv_preserved"] and quality["materials_preserved"]
            results["5000"] = quality
            ok("POST", asset_path + "/restore/" + source["revision"])
            main = job("optimize", asset=asset, profile="aggressive", triangles=4000)["result"]
            quality = main["report"]["structural_quality"]
            assert quality["target_met"] and 3800 <= quality["final_triangles"] <= 4000, quality
            assert quality["rating"] == "usable" and quality["uv_preserved"] and quality["materials_preserved"]
            assert quality["measurements"][0]["major_parts_lost"] == 0
            results["4000"] = quality
            primary = main["revision"]

            low = job("optimize", asset=asset, triangles=1000)["result"]
            assert ok("GET", asset_path)["current"] == primary
            low_quality = low["report"]["structural_quality"]
            assert not low_quality["target_met"] and low_quality["recommended_target"] > 1000
            assert low["warning"]
            results["1000_main_attempt"] = low_quality

            saved_lods = {}
            for size in (2000, 1000):
                lod = job("optimize", asset=asset, triangles=size, create_lod=True)["result"]
                assert ok("GET", asset_path)["current"] == primary
                assert lod["variant"] == "lod", lod["report"]["structural_quality"]
                lod_quality = lod["report"]["structural_quality"]
                assert lod_quality["rating"] != "failed" and lod_quality["recommended_usage"] == "lod"
                assert lod_quality["silhouette_overlap"] >= .925 and lod_quality["final_triangles"] < quality["final_triangles"]
                blocked = client.post(asset_path + "/restore/" + lod["revision"])
                assert blocked.status_code == 422
                saved_lods[lod["revision"]] = lod_quality["final_triangles"]
                results[f"lod_{size}"] = lod_quality

            state = ok("GET", asset_path)
            original = next(item for item in state["revisions"] if item["id"] == source["revision"])
            raw = client.get("/api/files/" + original["body"]["source"]).raise_for_status().content
            assert raw == args.house.read_bytes()
            original_blend, _ = project_file(project, original["body"]["blend"], {"blend"})
            dirty_mesh = QA / "house-with-qa-fragments.glb"
            run_blender("""
import bmesh
import bpy
import sys
source, destination = sys.argv[-2:]
bpy.ops.wm.open_mainfile(filepath=source, load_ui=False, use_scripts=False)
obj = next(item for item in bpy.context.scene.objects if item.type == 'MESH')
geom = bmesh.new()
geom.from_mesh(obj.data)
points = sorted(list(geom.verts), key=lambda vert: vert.co.z, reverse=True)
for i in range(4):
    p = points[i * 17].co.copy()
    p.z += 0.001
    letters = [geom.verts.new((p.x+x,p.y+y,p.z)) for x,y in [(0.,0.),(.0008,0.),(0.,.0008)]]
    geom.faces.new(letters)
geom.to_mesh(obj.data)
geom.free()
bpy.ops.export_scene.gltf(filepath=destination, export_format='GLB')
""", original_blend, dirty_mesh)
            noisy = imported(dirty_mesh, "House cleanup fixture")
            if args.qwen:
                with httpx.Client(trust_env=False, timeout=2) as transport:
                    def connected():
                        return transport.get(OLLAMA + "/api/version").is_success
                    try:
                        online = connected()
                    except httpx.TransportError:
                        online = False
                    if not online:
                        with (QA / "ollama.log").open("wb") as log:
                            ollama = subprocess.Popen(["ollama", "serve"], stdout=log, stderr=subprocess.STDOUT,
                                env={**os.environ, "OLLAMA_HOST": OLLAMA.removeprefix("http://"), "OLLAMA_NO_CLOUD": "1",
                                     "OLLAMA_MODELS": str(ROOT / "runtime" / "ollama-models")})
                        wait(connected)
                draft = job("interpret", asset=noisy["asset"], prompt=
                    "Clean up the mesh for crisp low-poly structure. Remove tiny floating fragments and flatten near-planar noise. Preserve windows, door openings and UVs.")
                operations = draft["result"]["proposal"]["operations"]
                assert operations[0]["type"] == "cleanup_mesh", operations
                cleaned = job("edit", asset=noisy["asset"], proposal=draft["id"], confirmed=True)["result"]
                results["qwen_cleanup"] = operations
            else:
                cleaned = job("cleanup", asset=noisy["asset"], profile="preserve_structure")["result"]
            inspected = cleaned["report"]["structural_quality"]
            assert cleaned["revision"] != noisy["revision"] and inspected["rating"] == "usable", inspected
            assert sum(item["removed_fragments"] for item in inspected["cleanup"]) >= 4, inspected
            assert inspected["measurements"][0]["major_parts_lost"] == 0 and inspected["silhouette_overlap"] > .98
            results["cleanup"] = inspected

            ok("POST", asset_path + "/restore/" + source["revision"])
            assert ok("GET", asset_path)["current"] == source["revision"]
            ok("POST", asset_path + "/restore/" + primary)
            job("approve", asset=asset, review_notes="Reviewed structure, PBR and source comparison in the isolated house QA project.")
            export = job("export", asset=asset, preset="Unity 6 URP")["result"]
            manifest = export["manifest"]
            assert manifest["lods"][0]["triangles"] == quality["final_triangles"]
            assert {row["revision"]: row["triangles"] for row in manifest["lods"][1:]} == saved_lods, manifest
            (QA / "house-structure-unity.zip").write_bytes(
                client.get("/api/files/" + export["file"]).raise_for_status().content)
            results.update(main_revision=primary, saved_lods=saved_lods, unity=manifest)
    finally:
        if ollama and ollama.poll() is None:
            ollama.terminate()
            ollama.wait(timeout=15)
        (QA / "structure-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Structure regression passed: {QA}")


if __name__ == "__main__":
    run()
