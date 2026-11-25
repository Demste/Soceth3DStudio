"""Run production regressions using an existing Pixal3D house and local Blender/Ollama."""

import argparse
import io
import json
import os
import struct
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("--qa", type=Path, default=ROOT / "runtime" / "qa" / uuid4().hex)
parser.add_argument("--live", action="store_true", help="Run installed Qwen and the packaged interactive Blender add-on.")
args = parser.parse_args()
QA = args.qa.resolve()
QA.mkdir(parents=True, exist_ok=True)
os.environ.update(SOCETH_DATA=str(QA), SOCETH_PORT="17869")
sys.path.insert(0, str(ROOT))

from services.backend.core import BASE, PORT, TOKEN_FILE, blender_path, project_file, write_json
from services.backend.main import app
from services.backend.providers.models import OLLAMA
from services.backend.schemas import Bible, Proposal


def wait(predicate, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if result := predicate():
                return result
        except httpx.TransportError:
            pass
        time.sleep(.35)
    raise TimeoutError("The local regression condition did not complete.")


def content(response):
    response.raise_for_status()
    return response.json()


def mesh_uv(glb):
    size, = struct.unpack_from("<I", glb, 12)
    graph = json.loads(glb[20:20 + size])
    binary = memoryview(glb)[28 + size:]
    result = []
    for mesh in graph["meshes"]:
        for primitive in mesh["primitives"]:
            uv = graph["accessors"][primitive["attributes"]["TEXCOORD_0"]]
            view = graph["bufferViews"][uv["bufferView"]]
            assert uv["componentType"] == 5126 and uv["type"] == "VEC2"
            start = view.get("byteOffset", 0) + uv.get("byteOffset", 0)
            stride = view.get("byteStride", 8)
            result.append(b"".join(binary[start + i * stride:start + i * stride + 8] for i in range(uv["count"])))
    return result


def blender_worker(directory, command):
    directory.mkdir(exist_ok=True)
    path = directory / "command.json"
    write_json(path, {"protocol_version": 1, "output": str(directory), **command})
    result = subprocess.run([str(blender_path()), "--background", "--factory-startup", "--disable-autoexec",
        "--python-exit-code", "1", "--python", str(ROOT / "integrations/blender_scripts/worker.py"), "--", str(path)],
        capture_output=True, env={**os.environ, "PYTHONUTF8": "1"}, text=True, encoding="utf-8", errors="replace",
        timeout=90)
    (directory / "worker.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    result.check_returncode()
    return json.loads((directory / "blender-result.json").read_text(encoding="utf-8"))


def build_addon_test():
    script = QA / "install_addon.py"
    addon = ROOT / "runtime" / "Soceth-Blender-Addon.zip"
    script.write_text(f"""
import addon_utils
import ast
import bpy
import os
from pathlib import Path
assert bpy.app.version[:2] == (5, 1), bpy.app.version
assert "FINISHED" in bpy.ops.preferences.addon_install(filepath={str(addon)!r}, overwrite=True)
module = next(m for m in addon_utils.modules(refresh=True) if m.__name__ == "soceth_blender")
assert module.bl_info["version"] >= (1, 1, 0)
tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
assert any(isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) and
           any(isinstance(target, ast.Name) and target.id == "bl_info" for target in node.targets)
           for node in tree.body)
assert "FINISHED" in bpy.ops.preferences.addon_enable(module="soceth_blender")
prefs = bpy.context.preferences.addons["soceth_blender"].preferences
print("SOCETH ADDON ENABLED", bpy.app.version_string, flush=True)
if not bpy.app.background:
    prefs.port = int(os.environ["SOCETH_PORT"])
    prefs.token_file = str(Path(os.environ["SOCETH_DATA"]) / "local.token")
    assert "FINISHED" in bpy.ops.soceth.connect()
""", encoding="utf-8")
    return script


def run():
    processes = []
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    results = {}
    try:
        with httpx.Client(base_url=BASE, timeout=15, trust_env=False) as client:
            wait(lambda: client.get("/api/health").is_success)
            client.headers["Authorization"] = "Bearer " + TOKEN_FILE.read_text(encoding="ascii")
            project = content(client.post("/api/projects", json={"name": "House production QA"}))["id"]
            base = f"/api/projects/{project}"
            bible = content(client.get(base + "/bible"))["body"]
            assert bible["color_policy"] == "guided"
            bible.update(color_policy="strict", palette=["#254270", "#6d1735"], semantic_materials={
                "Stone Walls": {"primary_color": "#254270", "texture_style": "textured"},
                "Roof": {"primary_color": "#6d1735", "texture_style": "textured"},
            })
            export_preset = bible["export_presets"]["Unity 6 URP"]
            export_preset["collider"] = "capsule"
            content(client.put(base + "/bible", json=bible))
            with args.source.open("rb") as source:
                file_id = content(client.post(base + "/files", files={"file": ("house.glb", source)}))["id"]
            asset = None

            def job(kind, failure=None, **params):
                identifier = content(client.post(base + "/jobs", json={"kind": kind, "asset": asset, **params}))["id"]
                def done():
                    row = content(client.get("/api/jobs/" + identifier))
                    return row if row["status"] in {"completed", "failed", "cancelled"} else None
                row = wait(done, 300)
                if failure:
                    assert row["status"] == "failed" and failure in row["error"], row
                elif row["status"] != "completed":
                    raise RuntimeError(f"{kind}: {row['error']} ({identifier})")
                return row

            def revisions():
                return content(client.get("/api/assets/" + asset))["revisions"]

            def restore(identifier):
                data = content(client.post(f"/api/assets/{asset}/restore/{identifier}"))
                assert data["current"] == identifier

            def file(identifier):
                response = client.get("/api/files/" + identifier)
                response.raise_for_status()
                return response.content

            initial = job("import", name="Existing Pixal3D House", file=file_id)["result"]
            asset, source_id = initial["asset"], initial["revision"]
            initial_report = initial["report"]
            assert initial_report["triangles"] > 50000, initial_report
            source_row = revisions()[0]
            assert file(source_row["body"]["source"]) == args.source.read_bytes()
            results.update(asset=asset, source=source_id, source_triangles=initial_report["triangles"])

            automatic = job("optimize")["result"]
            auto_count = automatic["report"]["optimization"][0]
            assert auto_count["target_triangles"] == 5000 and auto_count["target_met"], auto_count
            results["automatic_decimation"] = auto_count
            restore(source_id)
            # Exercise the same Blender candidate phase as generation without another Pixal3D run.
            original_blend, _ = project_file(project, source_row["body"]["blend"], {"blend"})
            automatic_configuration = Bible.model_validate({**bible, "texture_size": 256}).model_dump()
            candidate_dir = QA / "automatic-candidate"
            candidate = blender_worker(candidate_dir, {
                "kind": "candidate", "source": str(original_blend), "triangles": 5000, "target_triangles": 5000,
                "texture_size": 256, "bible_version": 2, "bible": automatic_configuration})
            candidate_report = candidate["report"]
            assert candidate_report["triangles"] <= 5000 and candidate_report["review_required"]
            atlas = candidate_report["art_bible"]["materials"][0]
            assert atlas["requires_mapping"] and atlas["image"] != atlas["original_image"], atlas
            with Image.open(candidate_dir / candidate["textures"][atlas["image"]]) as rendered:
                assert max(rendered.size) == 256, rendered.size
            assert file(source_row["body"]["source"]) == args.source.read_bytes()
            results["automatic_candidate"] = {"triangles": candidate_report["triangles"],
                "semantic_mapping": "review", "texture_resolution": 256}

            optimized = job("optimize", triangles=4000)["result"]
            optimized_scene = next(row for row in revisions() if row["id"] == optimized["revision"])
            reduction = optimized["report"]["optimization"][0]
            assert reduction["original_triangles"] == initial_report["triangles"]
            assert 3500 <= reduction["final_triangles"] <= 4000 and reduction["target_met"], reduction
            assert reduction["materials_preserved"] and reduction["uv_preserved"], reduction
            assert revisions()[0]["parent"] == source_id
            assert job("optimize")["result"]["unchanged"] and len(revisions()) == 3
            results["manual_decimation"] = reduction
            ratio = Proposal.model_validate({"intent": "edit_mesh", "explanation": "QA ratio",
                "operations": [{"type": "decimate_mesh", "targets": [initial_report["objects"][0]["id"]], "ratio": .1}]})
            assert ratio.operations[0].ratio == .1 and ratio.operations[0].preserve_uvs is True

            source_mat = initial_report["art_bible"]["materials"][0]
            assert source_mat["texture_file"] and source_mat["requires_mapping"]
            material = source_mat["name"]
            regions = [
                {"material": material, "category": "Stone Walls", "strokes": []},
                {"material": material, "category": "Roof", "strokes": [
                    {"radius": .16, "points": [[.25, .5], [.7, .5]]}]},
            ]
            preview = job("palette_preview", regions=regions)["result"]
            assert preview["preview"] and len(revisions()) == 3
            styled = job("palette_apply", regions=regions)["result"]
            styled_row = revisions()[0]
            art = styled["report"]["art_bible"]
            assert art["palette_conformity"] == "pass", art
            assert set(art["materials"][0]["categories"]) == {"Stone Walls", "Roof"}
            assert styled["report"]["triangles"] == optimized["report"]["triangles"]
            assert mesh_uv(file(optimized_scene["body"]["preview"])) == mesh_uv(file(styled_row["body"]["preview"]))
            def map_file(report, kind):
                image = report["materials"][0]["maps"][kind]["image"]
                return next(item["file"] for item in report["textures"] if item["name"] == image)
            for kind in ("normal", "roughness", "metallic"):
                assert file(map_file(initial_report, kind)) == file(map_file(styled["report"], kind)), kind
            original_pixels = Image.open(io.BytesIO(file(source_mat["texture_file"]))).convert("RGBA")
            styled_bytes = file(art["materials"][0]["texture_file"])
            styled_pixels = Image.open(io.BytesIO(styled_bytes)).convert("RGBA")
            assert styled_pixels.size == original_pixels.size
            assert ImageChops.difference(original_pixels.getchannel("A"), styled_pixels.getchannel("A")).getbbox() is None
            assert ImageChops.difference(original_pixels.convert("RGB"), styled_pixels.convert("RGB")).getbbox()
            assert len(styled_pixels.getcolors(styled_pixels.width * styled_pixels.height)) > 100
            first_mask = styled_pixels.getpixel((int(styled_pixels.width * .5), int(styled_pixels.height * .5)))
            assert first_mask[0] > first_mask[2], first_mask
            (QA / "house-styled.png").write_bytes(styled_bytes)
            (QA / "house-source.png").write_bytes(file(source_mat["texture_file"]))
            (QA / "house-styled.glb").write_bytes(file(styled_row["body"]["preview"]))
            reimport_dir = QA / "blender-reimport"
            reimport = blender_worker(reimport_dir, {
                "kind": "validate", "source": str(QA / "house-styled.glb"), "triangles": 5000,
                "texture_size": 1024, "bible_version": 2, "bible": Bible.model_validate(bible).model_dump(),
                "regions": regions})
            reap = reimport["report"]["art_bible"]["materials"][0]
            decoded = Image.open(reimport_dir / reimport["textures"][reap["image"]]).convert("RGBA")
            assert ImageChops.difference(decoded.convert("RGB"), styled_pixels.convert("RGB")).getbbox() is None
            results["blender_reimport"] = "Reimported GLB's authored atlas matches the adapted revision."
            results["palette"] = art
            restored = job("palette_restore")["result"]
            restored_texture = restored["report"]["art_bible"]["materials"][0]["texture_file"]
            assert file(restored_texture) == file(source_mat["texture_file"])
            restore(styled["revision"])
            bible["semantic_materials"]["Roof"]["primary_color"] = "#2d8243"
            content(client.put(base + "/bible", json=bible))
            reapplied = job("palette_apply")["result"]
            updated = reapplied["report"]["art_bible"]
            assert updated["materials"][0]["texture_file"] and updated["regions"] == regions
            assert updated["palette_conformity"] == "pass" and updated["bible_version"] > art["bible_version"]
            assert revisions()[0]["parent"] == styled["revision"]
            assert reapplied["report"]["triangles"] == styled["report"]["triangles"]
            updated_pixels = Image.open(io.BytesIO(file(updated["materials"][0]["texture_file"]))).convert("RGBA")
            updated_mask = updated_pixels.getpixel((int(updated_pixels.width * .5), int(updated_pixels.height * .5)))
            assert updated_mask[1] > updated_mask[0], updated_mask
            results["reapplication"] = {"revision": reapplied["revision"], "mask_pixel": updated_mask}
            job("export", failure="Approve", preset="Unity 6 URP")
            job("approve", review_notes="Reviewed the isolated house candidate and recorded any source topology warnings.")
            exported = job("export", preset="Unity 6 URP")["result"]
            pack = QA / "house-unity.zip"
            pack.write_bytes(file(exported["file"]))
            with zipfile.ZipFile(pack) as archive:
                manifest = exported["manifest"]
                base_color = next(entry["path"] for entry in manifest["unity_materials"][0]["maps"]
                                  if entry["kind"] == "base_color")
                assert archive.read(f"SocethAssets/{manifest['asset']}/{base_color}") == file(updated["materials"][0]["texture_file"])
                assert any(name.endswith(".fbx") for name in archive.namelist())
                assert any(name.endswith(".glb") for name in archive.namelist())
            results["unity_package"] = {"path": str(pack), "asset": manifest["asset"], "lods": manifest["lods"],
                                        "width": manifest["bounds"][1][0] - manifest["bounds"][0][0]}
            script = build_addon_test()
            env = {**os.environ, "BLENDER_USER_RESOURCES": str(QA / "blender-profile"), "PYTHONUTF8": "1"}
            argv = [str(blender_path()), "--factory-startup", "--disable-autoexec", "--python-exit-code", "1", "--python", str(script)]
            installation = subprocess.run([argv[0], "--background", *argv[1:]], env=env,
                                          capture_output=True, text=True, timeout=90, check=True)
            (QA / "addon-install.log").write_text(installation.stdout, encoding="utf-8")
            assert "SOCETH ADDON ENABLED" in installation.stdout
            results["addon_install"] = "Blender 5.1 discovered and enabled the packaged add-on"
            if args.live:
                if not wait(lambda: client.get("/api/status").is_success):
                    raise RuntimeError("Studio status could not be read.")
                if not content(client.get("/api/status"))["ollama"]:
                    with (QA / "ollama.log").open("wb") as log:
                        processes.append(subprocess.Popen(["ollama", "serve"], stdout=log, stderr=subprocess.STDOUT,
                            env={**env, "OLLAMA_HOST": OLLAMA.removeprefix("http://"), "OLLAMA_NO_CLOUD": "1",
                                 "OLLAMA_MODELS": str(ROOT / "runtime" / "ollama-models")}))
                    wait(lambda: content(client.get("/api/status"))["ollama"])
                with (QA / "interactive.log").open("wb") as log:
                    processes.append(subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT))
                wait(lambda: content(client.get("/api/status"))["blender_connected"], 90)
                restore(source_id)
                job("open")
                proposal = job("interpret", prompt="Reduce the active model to approximately 4000 triangles using a decimate modifier. Preserve UVs and textures.")
                operation = proposal["result"]["proposal"]["operations"][0]
                assert operation["type"] == "decimate_mesh" and operation["target_triangles"] == 4000, operation
                applied = job("edit", proposal=proposal["id"], confirmed=True, mode="interactive")["result"]
                measured = applied["report"]["optimization"][0]
                assert measured["target_met"] and measured["final_triangles"] <= 4000, measured
                results["interactive_qwen"] = {"operation": operation, "optimization": measured}
                live_palette = job("palette_apply", mode="interactive", regions=regions)["result"]
                live_report = live_palette["report"]["art_bible"]
                assert live_report["palette_conformity"] == "pass"
                assert file(live_report["materials"][0]["texture_file"]) == file(updated["materials"][0]["texture_file"])
                results["interactive_palette"] = live_palette["revision"]
                restore(reapplied["revision"])
                job("open")
            results["desktop_revision"] = reapplied["revision"]
    finally:
        (QA / "production-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
        server.should_exit = True
        thread.join(8)
    print(f"House production regression passed: {QA}")


if __name__ == "__main__":
    run()
