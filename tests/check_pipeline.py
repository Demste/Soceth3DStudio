"""Consolidated backend and real Blender acceptance checks."""
import io
import json
import os
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / "runtime" / "qa" / uuid.uuid4().hex
os.environ["SOCETH_DATA"] = str(QA)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pydantic import ValidationError

from services.backend.core import TOKEN_FILE, PROJECTS, blender_path, db, now, safe, uid
from services.backend.main import app
from services.backend.media import validate_glb
from services.backend.schemas import Proposal
from services.backend.providers.models import CATALOG, is_ready

results = {}


def run(label, test):
    try:
        result = test()
        results[label] = {"status": "passed", "result": result}
    except RuntimeError as error:
        results[label] = {"status": "failed", "error": str(error)}
    except Exception as error:
        results[label] = {"status": "failed", "error": repr(error)}
    print(label, results[label], flush=True)


def blocked(label, reason):
    results[label] = {"status": "blocked", "error": reason}
    print(label, results[label], flush=True)


def fixture():
    path = QA / "source.glb"
    proc = subprocess.run([str(blender_path()), "--background", "--factory-startup", "--python-exit-code",
        "1", "--python", str(ROOT / "tests/fixture.py"), "--", str(path)], capture_output=True, text=True, timeout=90)
    if proc.returncode or not path.is_file():
        raise RuntimeError(proc.stdout + proc.stderr)
    validate_glb(path)
    return path


def authenticated(client):
    reply = client.post("/api/session", headers={"Authorization": "Bearer " + TOKEN_FILE.read_text(encoding="ascii")})
    assert reply.status_code == 204, reply.text
    return client


def body(response):
    if not response.is_success:
        raise RuntimeError(f"{response.status_code} {response.text}")
    return response.json()


def terminal(client, job, wait=180):
    end = time.monotonic() + wait
    while time.monotonic() < end:
        row = body(client.get("/api/jobs/" + job))
        if row["status"] not in {"queued", "running"}:
            return row
        time.sleep(0.5)
    client.post(f"/api/jobs/{job}/cancel")
    raise TimeoutError("Job exceeded the acceptance test deadline.")


def main():
    live_ai = "--live-ai" in sys.argv
    with TestClient(app, base_url="http://127.0.0.1:17863") as client:
        assert client.get("/api/projects").status_code == 401
        authenticated(client)
        p = body(client.post("/api/projects", json={"name": "Acceptance test", "platforms": ["Windows", "Linux"]}))
        alternate = body(client.post("/api/projects", json={"name": "Independent test project"}))
        project = p["id"]
        base = f"/api/projects/{project}"
        bible = body(client.get(base + "/bible"))["body"]
        bible["palette"] = ["#334455", "#DDAA44"]
        bible["texture_size"] = 256
        bible["triangle_budgets"]["Prop"] = 2500
        bible["export_presets"]["Unity 6 URP"]["collider"] = "capsule"
        assert body(client.put(base + "/bible", json=bible))["version"] == 2
        assert body(client.get(f"/api/projects/{alternate['id']}/bible"))["version"] == 1
        token_path = TOKEN_FILE
    with TestClient(app, base_url="http://127.0.0.1:17863") as client:
        authenticated(client)
        run("A · project and Art Bible persistence", lambda: (
            body(client.get(base + "/bible"))["body"]["palette"],
            len(body(client.get("/api/projects")))
        ))
        assert body(client.get(base + "/bible"))["body"]["palette"] == ["#334455", "#DDAA44"]
        def job(kind, fail=None, seconds=180, **kwargs):
            identifier = body(client.post(base + "/jobs", json={"kind":kind, **kwargs}))["id"]
            result = terminal(client, identifier, seconds)
            if fail:
                assert result["status"] == "failed" and fail.lower() in result["error"].lower(), result
            elif result["status"] != "completed":
                raise RuntimeError(result["error"] or str(result))
            return result

        def uploaded(path):
            with path.open("rb") as file:
                return body(client.post(base + "/files", files={"file": (path.name, file)}))["id"]

        source = fixture()
        imported = job("import", file=uploaded(source), name="QA Stone")["result"]
        asset, first = imported["asset"], imported["revision"]
        detail = lambda: body(client.get("/api/assets/" + asset))
        def preview():
            record = detail()["revisions"][0]["body"]
            data = client.get("/api/files/" + record["preview"])
            assert data.content.startswith(b"glTF") and data.is_success
            assert record["report"]["triangles"] > 1000 and record["report"]["material_count"] == 1
            return [record["report"]["triangles"], len(data.content)]
        run("B · real mesh and GLB preview", preview)

        def proposal(operations):
            identifier = uid()
            val = Proposal.model_validate({"intent":"edit_mesh","explanation":"Acceptance check","operations":operations})
            result = {"asset":asset,"base":first,"request":"Acceptance check","proposal":val.model_dump()}
            with db() as conn:
                conn.execute("""INSERT INTO jobs(id,project,kind,status,stage,body,result,created,updated)
                    VALUES (?,?,'interpret','completed','Completed','{}',?,?,?)""",
                    (identifier, project, json.dumps(result), now(), now()))
            return identifier

        target = imported["report"]["objects"][0]["id"]
        def deterministic():
            plan = proposal([
                {"type":"dimensions","targets":[target],"multipliers":[1.2,1,1]},
                {"type":"material","targets":[target],"color":"#193755","roughness":0.7,"metallic":0.35},
                {"type":"handles","targets":[target],"axis":"X"},
            ])
            value = job("edit", asset=asset, revision=first, proposal=plan)["result"]
            assert value["report"]["object_count"] == 7
            assert value["report"]["objects"][0]["dimensions"][0] > 2.35
            assert any(abs(mat.get("roughness", 0) - .7) < .001 for mat in value["report"]["materials"])
            assert len(detail()["revisions"]) == 2
            body(client.post(f"/api/assets/{asset}/restore/{first}"))
            assert detail()["current"] == first
            failure = proposal([{"type":"material","targets":[target],"match":"ABSENT","color":"#193755"}])
            job("edit", fail="No material matches", asset=asset, revision=first, proposal=failure)
            assert detail()["current"] == first and len(detail()["revisions"]) == 2
            return f"Saved {value['revision']} and restored {first}; failed edit preserved history"
        run("C · background Blender editing, revisions and rollback", deterministic)
        blocked("C · interactive Blender add-on", "Covered by the separate check_interactive.py test.")
        if is_ready(CATALOG["qwen-coder"]) and live_ai:
            def llm():
                base_revision = detail()["current"]
                row = job("interpret", asset=asset, prompt="Make QA_Stone 20 percent wider on X and leave Y and Z unchanged.", seconds=300)
                ops = row["result"]["proposal"]["operations"]
                assert len(ops) == 1, ops
                changed = job("edit", asset=asset, revision=base_revision, proposal=row["id"], confirmed=True)
                assert abs(changed["result"]["report"]["objects"][0]["dimensions"][0] - 2.4) < .05, changed
                body(client.post(f"/api/assets/{asset}/restore/{first}"))
                return row["result"]
            run("D · Ollama interpretation and Blender apply", llm)
        else:
            blocked("D · Ollama interpretation and Blender apply", "Pinned Qwen model unavailable or --live-ai omitted.")

        path = QA / "reference.png"
        image = Image.new("RGB", (512, 512), (241, 241, 237))
        draw = ImageDraw.Draw(image)
        draw.polygon([(135,193),(257,132),(373,194),(257,245)], fill=(182,109,59))
        draw.polygon([(135,193),(257,245),(257,380),(135,326)], fill=(107,57,30))
        draw.polygon([(257,245),(373,194),(373,327),(257,380)], fill=(145,77,40))
        image.save(path)
        image_id = uploaded(path)
        def reconstruct():
            result = job("generate", seconds=1800, file=image_id, seed=172409, name="AI QA Crate")["result"]
            assert result["report"]["triangles"] > 0
            return result["metrics"] if "metrics" in result else result["report"]["triangles"]
        if is_ready(CATALOG["pixal3d"]) and live_ai:
            run("E · real Pixal3D", reconstruct)
        else:
            blocked("E · real Pixal3D", "Pinned Pixal3D bundle unavailable or --live-ai omitted.")
        def text3d():
            concept = job("concept", prompt="A stylized squat wooden crate with copper corners", seed=172410, seconds=1200)["result"]
            assert client.get("/api/files/" + concept["file"]).content.startswith(b"\x89PNG")
            return job("generate", file=concept["file"], name="AI QA Concept", seed=172411, seconds=1800)["result"]["report"]["triangles"]
        if live_ai and all(is_ready(CATALOG[model]) for model in ("flux-klein", "pixal3d")):
            run("F · text to image to 3D", text3d)
        else:
            blocked("F · text to image to 3D", "Pinned FLUX.2 or Pixal3D bundle unavailable or --live-ai omitted.")

        def production():
            result = job("optimize", asset=asset, triangles=700, bake=True, seconds=360)["result"]
            report = result["report"]
            assert report["triangles"] <= 700 and report["uv_present"] and report["textures_present"], report
            assert report["triangles"] < imported["report"]["triangles"]
            assert job("validate", asset=asset)["result"]["report"]["valid"]
            job("approve", asset=asset, review_notes="Acceptance fixture reviewed.")
            export = job("export", asset=asset, preset="Unity 6 URP")["result"]
            levels = [item["triangles"] for item in export["manifest"]["lods"]]
            assert levels[0] > levels[1] > levels[2]
            memory = client.get("/api/files/" + export["file"]).content
            (QA / "unity-export.zip").write_bytes(memory)
            with zipfile.ZipFile(io.BytesIO(memory)) as package:
                paths = package.namelist()
                for ext in (".fbx", ".glb", "validation.json", "soceth-manifest.json", ".cs", ".png"):
                    assert any(name.endswith(ext) for name in paths), paths
                glb = next(name for name in paths if name.endswith(".glb"))
                target = QA / "export.glb"
                target.write_bytes(package.read(glb))
                validate_glb(target)
            assert export["manifest"]["colliders"][0]["shape"] == "capsule"
            return {"lod_triangles": levels, "package": str(QA / "unity-export.zip")}
        run("G · Blender optimization, Cycles bake, validation, LOD, FBX and GLB", production)

        def recovery():
            assert client.post(base + "/files", files={"file": ("bad.png", b"malformed")}).status_code == 422
            assert client.post(base + "/jobs", headers={"Origin":"https://example.test"}, json={"kind":"export"}).status_code == 403
            job("open", fail="disconnected", asset=asset)
            if not is_ready(CATALOG["pixal3d"]):
                job("generate", fail="missing or unverified", file=image_id)
            try:
                Proposal.model_validate({"intent":"edit_mesh","explanation":"","operations":[{"type":"python","code":"print(1)"}]})
                raise AssertionError("Unsafe operation accepted")
            except ValidationError:
                pass
            identifier = uid()
            with db() as conn:
                conn.execute("""INSERT INTO jobs(id,project,kind,status,stage,body,created,updated)
                   VALUES (?,?,'validate','running','Preparing','{}',?,?)""", (identifier, project, now(), now()))
            return identifier
        run("H · invalid input, origin restriction, disconnected Blender and schema rejection", recovery)
        interrupted = results["H · invalid input, origin restriction, disconnected Blender and schema rejection"].get("result")

    if interrupted:
        with TestClient(app, base_url="http://127.0.0.1:17863") as client:
            authenticated(client)
            record = body(client.get("/api/jobs/" + interrupted))
            assert record["status"] == "failed" and record["stage"] == "Interrupted", record
            results["H · interrupted job recovery"] = {"status":"passed"}

    (QA / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("RESULTS:", QA / "results.json")
    if any(item["status"] == "failed" for item in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

