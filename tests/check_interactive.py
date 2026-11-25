"""Open Blender in an isolated QA session and exercise the add-on against a real server."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
QA = Path(sys.argv[1]).resolve()
structure = "--structure" in sys.argv[2:]
os.environ.update(SOCETH_DATA=str(QA), SOCETH_PORT="17868")
sys.path.insert(0, str(ROOT))
from services.backend.core import BASE, PORT, TOKEN_FILE, blender_path, db, now, uid
from services.backend.main import app

server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", access_log=False))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
process = None


def wait(check, seconds=45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if result := check():
                return result
        except httpx.HTTPError:
            pass
        time.sleep(0.35)
    raise TimeoutError("The backend or Blender add-on did not respond.")


def ok(response):
    response.raise_for_status()
    return response.json()


try:
    with httpx.Client(base_url=BASE, timeout=15, trust_env=False) as client:
        wait(lambda: client.get("/api/health").is_success)
        client.headers["Authorization"] = "Bearer " + TOKEN_FILE.read_text(encoding="ascii")
        if structure:
            qa_results = json.loads((QA / "structure-results.json").read_text(encoding="utf-8"))
            asset = ok(client.get("/api/assets/" + qa_results["asset"]))
            project = asset["project"]
        else:
            projects = ok(client.get("/api/projects"))
            project = next(row["id"] for row in projects if row["name"] == "Acceptance test")
            asset = ok(client.get("/api/assets", params={"project": project}))[0]
        detail = ok(client.get("/api/assets/" + asset["id"]))
        base = next(rev for rev in detail["revisions"] if rev["id"] == asset["current"])
        obj = base["body"]["report"]["objects"][0]
        old = obj["dimensions"][0]
        env = {**os.environ, "PYTHONUTF8": "1"}
        if structure:
            env.update(BLENDER_USER_RESOURCES=str(QA / "blender-profile"),
                       SOCETH_QA_ADDON_ZIP=str(ROOT / "runtime" / "Soceth-Blender-Addon.zip"))
        with (QA / "interactive-blender.log").open("wb") as log:
            process = subprocess.Popen([str(blender_path()), "--factory-startup", "--disable-autoexec",
                "--python", str(ROOT / "tests/interactive.py")], env=env,
                stdout=log, stderr=subprocess.STDOUT)
        wait(lambda: ok(client.get("/api/status"))["blender_structure" if structure else "blender_connected"])
        submit = f"/api/projects/{project}/jobs"
        def run(kind, failure=None, **params):
            data = {"kind": kind, "asset": asset["id"], **params}
            identifier = ok(client.post(submit, json=data))["id"]
            def completed():
                row = ok(client.get("/api/jobs/" + identifier))
                return row if row["status"] in {"completed","failed","cancelled"} else None
            row = wait(completed, 100)
            if failure:
                assert row["status"] == "failed" and failure in row["error"], row
                return row
            if row["status"] != "completed":
                raise RuntimeError(row["error"])
            return row["result"]
        if structure:
            source = qa_results["source"]
            lod = min(qa_results["saved_lods"], key=qa_results["saved_lods"].get)
            assert run("open", revision=lod)["opened"]
            run("capture", revision=base["id"], failure="current main revision")
            ok(client.post(f"/api/assets/{asset['id']}/restore/{source}"))
            assert run("open", revision=source)["opened"]
            ok(client.post(f"/api/assets/{asset['id']}/restore/{base['id']}"))
            assert run("open", revision=base["id"])["opened"]
            with db() as conn:
                cleanup = conn.execute("""SELECT id,result FROM jobs WHERE project=? AND
                    kind='interpret' AND status='completed' ORDER BY created LIMIT 1""", (project,)).fetchone()
            if cleanup:
                proposal = json.loads(cleanup["result"])
                noisy_asset, noisy_base = proposal["asset"], proposal["base"]
                ok(client.post(f"/api/assets/{noisy_asset}/restore/{noisy_base}"))
                assert run("open", asset=noisy_asset, revision=noisy_base)["opened"]
                result = run("edit", asset=noisy_asset, revision=noisy_base, proposal=cleanup["id"],
                             confirmed=True, mode="interactive")
                assert result["report"]["structural_quality"]["rating"] == "usable"
                assert result["revision"] != noisy_base
            assert run("open", revision=base["id"])["opened"]
            print("Packaged Blender add-on verified: LOD open, capture guard, restore, and interactive cleanup.", flush=True)
            sys.exit(0)
        run("open", revision=base["id"])
        proposal_id = uid()
        proposal = {"intent":"edit_mesh","explanation":"QA dimensions and material",
                    "operations":[{"type":"dimensions","targets":[obj["id"]],"multipliers":[1.1,1,1]},
                                  {"type":"material","targets":[obj["id"]],"color":"#447788"},
                                  {"type":"handles","targets":[obj["id"]],"axis":"X"}]}
        result = {"asset":asset["id"],"base":base["id"],"proposal":proposal,"request":"Interactive QA"}
        with db() as conn:
            conn.execute("""INSERT INTO jobs(id,project,kind,status,stage,body,result,created,updated)
                VALUES (?,?,'interpret','completed','Completed','{}',?,?,?)""",
                (proposal_id, project, json.dumps(result), now(), now()))
        result = run("edit", revision=base["id"], proposal=proposal_id, mode="interactive")
        assert abs(result["report"]["objects"][0]["dimensions"][0] - old * 1.1) < .03
        assert result["report"]["object_count"] == base["body"]["report"]["object_count"] + 6
        run("open", revision=result["revision"])
        captured = run("capture", revision=result["revision"])
        assert captured["report"]["triangles"] == result["report"]["triangles"]
        ok(client.post(f"/api/assets/{asset['id']}/restore/{base['id']}"))
        run("open", revision=base["id"])
        print("C · real interactive Blender connection, edit, detail geometry, capture, reopen, and revision restore passed.", flush=True)
finally:
    if process and process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
    server.should_exit = True
    thread.join(8)

