"""Run the compiled frontend in the real Windows WebView2 shell against a QA database."""
import argparse
import base64
import json
import os
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("qa", nargs="?", type=Path)
parser.add_argument("--asset", help="Open a specific saved asset and verify its embedded texture in the viewport.")
parser.add_argument("--production", action="store_true", help="Check the current production revision and native Inspector scrolling.")
parser.add_argument("--structure", action="store_true", help="Check the approved main and saved distance LOD in the native app.")
parser.add_argument("--models", action="store_true", help="Check hardware recommendations and persisted UI modes.")
args = parser.parse_args()
QA = args.qa.resolve() if args.qa else ROOT / "runtime" / "qa" / uuid4().hex
QA.mkdir(parents=True, exist_ok=True)
os.environ["SOCETH_DATA"] = str(QA)
os.environ["SOCETH_PORT"] = "17867"
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn
import webview

from services.backend.core import BASE, PORT, TOKEN_FILE
from services.backend.main import app

results = {}
server = None


def wait(predicate, timeout=20, description=None):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if result := predicate():
            return result
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {description or predicate.__name__}.")


def health():
    with httpx.Client(timeout=2, trust_env=False) as client:
        try:
            return client.get(BASE + "/api/health").is_success
        except httpx.HTTPError:
            return False


def start_backend():
    global server
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, access_log=False, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait(health)
    return thread


def check_asset_viewport(window, asset):
    ev = window.evaluate_js
    picker = """document.querySelector('[aria-label="Active project"]')"""
    project_id = json.dumps(asset["project"])
    name = wait(lambda: ev(f"Array.from({picker}?.options || []).find(option => option.value === {project_id})?.text"),
                description="the saved asset's project to appear")
    if ev(f"{picker}.value") != asset["project"]:
        ev(f"""(() => {{
            const select = {picker};
            select.value = {project_id};
            select.dispatchEvent(new Event('change', {{bubbles:true}}));
        }})()""")
        wait(lambda: ev(f"document.querySelector('.topbar strong')?.textContent === {json.dumps(name)}"),
             description="the saved asset's project to activate")
    ev("Array.from(document.querySelectorAll('nav button')).find(item => item.textContent.trim()==='Library').click()")
    label = json.dumps(asset["name"])
    selector = f"""Array.from(document.querySelectorAll('.asset-tile')).find(tile =>
        tile.querySelector('.asset-caption strong')?.textContent === {label})"""
    wait(lambda: ev(f"Boolean({selector})"), description="the saved asset's library tile")
    ev(f"({selector}).click()")
    original = next(item for item in asset["revisions"] if item["parent"] is None)
    selected = next(item for item in asset["revisions"] if item["id"] == asset["current"]) if args.production or args.structure else original
    if asset["current"] != selected["id"]:
        wait(lambda: ev("document.querySelector('.revision-row:last-child button') !== null"),
             description="the revisions to appear")
        ev("document.querySelector('.revision-row:last-child button').click()")
    blend = json.dumps("/api/files/" + selected["body"]["blend"] + "?download=true")
    wait(lambda: ev(f"document.querySelector('a[title=\"Download editable Blender scene\"]')?.getAttribute('href') === {blend}"),
         description="the selected Blender revision to open")
    textures = wait(lambda: ev("""(() => {
        const canvas = document.querySelector('.viewport canvas');
        if (!canvas || document.querySelector('.viewport-message')) return false;
        if (!canvas.getContext('webgl2') || canvas.width <= 100 || canvas.height <= 100) return false;
        return Number(canvas.dataset.baseColorTextures);
    })()"""), description="the selected textured GLB to render in WebGL 2")
    assert textures > 0, "The asset loaded without its embedded base-color texture."
    results.update(status="passed", asset=asset["id"], revision=selected["id"], base_color_textures=textures)
    if args.production or args.structure:
        check_inspector(window, selected)
    if args.structure:
        check_lod_view(window, asset, selected)


def devtools(window, method, data):
    from System import Action

    pending = []
    def call():
        pending.append(window.native.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(method, json.dumps(data)))
    window.native.Invoke(Action(call))
    assert pending[0].Wait(10000), f"WebView2 did not respond to {method}"
    return json.loads(str(pending[0].Result))


def check_inspector(window, revision):
    ev = window.evaluate_js
    ev("""Array.from(document.querySelectorAll('.generation .segmented button'))
        .find(item => item.textContent === 'Text').click()""")
    assert ev("""Array.from(document.querySelectorAll('.generation button'))
        .find(item => item.textContent.includes('Approve concept & generate 3D')).disabled""")
    ev("""Array.from(document.querySelectorAll('.generation .segmented button'))
        .find(item => item.textContent === 'Image').click()""")
    expected = revision["body"]["report"]["triangles"]
    assert ev("Number(document.querySelector('.stats-grid strong').textContent.replace(/[^0-9]/g, ''))") == expected
    assert ev("document.body.innerText.includes('Approved Game Asset')"), "Approved state was not displayed."
    inspector = """document.querySelector('[aria-label="Asset Inspector"]')"""
    checks = []
    for width, height in [(1400, 880), (940, 610)]:
        window.resize(width, height)
        dimensions = ev(f"""({{
            viewport: [innerWidth, innerHeight], inspector: [{inspector}.clientHeight, {inspector}.scrollHeight],
            workspace: document.querySelector('.workspace').clientHeight
        }})""")
        assert wait(lambda: ev(f"{inspector}.clientHeight > 100 && {inspector}.scrollHeight > {inspector}.clientHeight"),
                    timeout=5, description=f"scrollable Inspector at {width}x{height}, layout {dimensions}")
        ev(f"{inspector}.scrollTop = 0")
        x, y = ev(f"""(() => {{
            const bounds = {inspector}.getBoundingClientRect();
            return [bounds.left + bounds.width / 2, bounds.top + bounds.height / 2];
        }})()""")
        devtools(window, "Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": 450})
        wait(lambda: ev(f"{inspector}.scrollTop > 0"), description="an actual WebView2 wheel event to scroll the Inspector")
        assert ev("document.querySelector('.workspace-content').scrollTop === 0")
        for label in ("Interpret command", "Optimize to Project Budget", "Validate Against Art Bible"):
            js = f"""Array.from({inspector}.querySelectorAll('button')).find(item => item.textContent.trim() === {json.dumps(label)})"""
            ev(f"({js}).scrollIntoView({{block:'center'}})")
            assert ev(f"""(() => {{
                const outer = {inspector}.getBoundingClientRect(), inner = ({js}).getBoundingClientRect();
                return inner.top >= outer.top - 1 && inner.bottom <= outer.bottom + 1 &&
                    document.elementFromPoint(inner.left + inner.width / 2, inner.top + inner.height / 2)?.closest('button') === ({js});
            }})()"""), f"{label} is clipped at {width}×{height}."
        checks.append(f"{width}x{height}")
    assert ev("document.querySelector('.viewport canvas').dataset.baseColorTextures > 0")
    ev("document.querySelector('[aria-label=\"Wireframe\"]').click()")
    assert ev("document.querySelector('[aria-label=\"Wireframe\"]').getAttribute('aria-pressed') === 'true'")
    ev("document.querySelector('[aria-label=\"Wireframe\"]').click()")
    (QA / "desktop-production.png").write_bytes(base64.b64decode(
        devtools(window, "Page.captureScreenshot", {"format": "png"})["data"]))
    results.update(inspector_wheel=checks, measured_triangles=expected, viewport="styled GLB and controls")
    if args.structure:
        return
    window.resize(1400, 880)
    ev("""Array.from(document.querySelectorAll('summary'))
       .find(item => item.textContent.trim() === 'Assign material or paint texture regions').click()""")
    brush = """document.querySelector('.mask-canvas')"""
    wait(lambda: ev(f"Boolean({brush}?.width > 300)"), description="the source atlas to load on the brush canvas")
    ev(f"{brush}.scrollIntoView({{block:'center'}})")
    x, y = ev(f"""(() => {{
        const bounds = {brush}.getBoundingClientRect();
        return [bounds.left + bounds.width * .72, bounds.top + bounds.height * .6];
    }})()""")
    devtools(window, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1})
    devtools(window, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x + 8, "y": y, "button": "left", "buttons": 1})
    devtools(window, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x + 8, "y": y, "button": "left", "clickCount": 1})
    wait(lambda: ev(f"{inspector}.querySelectorAll('button[aria-label^=\"Remove mapping\"]').length === 3"),
         description="the painted region mapping to be added")
    ev("""Array.from(document.querySelectorAll('button'))
       .find(item => item.textContent.trim() === 'Preview Palette Changes').click()""")
    wait(lambda: ev("document.querySelector('.dialog.compare h2')?.textContent === 'Palette preview'"),
         timeout=45, description="the Blender-generated palette preview")
    wait(lambda: ev("""Array.from(document.querySelectorAll('.compare canvas'))
        .filter(canvas => canvas.dataset.baseColorTextures > 0).length === 2"""),
         description="both before and after textures to load in the desktop preview")
    assert ev("document.querySelector('.dialog.compare').textContent.includes('Art Bible Compliance')")
    results["region_preview"] = "painted region and rendered both GLBs without committing a revision"


def check_lod_view(window, asset, main):
    ev = window.evaluate_js
    options = ev("""Array.from(document.querySelector('[aria-label="Optimization profile"]').options).map(option => option.text)""")
    assert options == ["Preserve Structure", "Balanced", "Aggressive", "LOD / Distance"], options
    def compare(label, image, description):
        ev(f"""Array.from(document.querySelectorAll('.workspace-right button'))
            .find(item => item.textContent.trim() === {json.dumps(label)}).click()""")
        wait(lambda: ev("""Array.from(document.querySelectorAll('.compare canvas'))
            .filter(canvas => canvas.dataset.baseColorTextures > 0).length === 2"""), description=description)
        (QA / image).write_bytes(base64.b64decode(
            devtools(window, "Page.captureScreenshot", {"format": "png"})["data"]))
        ev("document.querySelector('[aria-label=\"Close comparison\"]').click()")
    compare("Compare Source / Result", "source-vs-main.png", "the source and optimized main to render side by side")
    lod = min((row for row in asset["revisions"] if row["body"]["report"].get("lifecycle") == "lod"),
              key=lambda row: row["body"]["report"]["triangles"])
    expected = lod["body"]["report"]["triangles"]
    index = asset["revisions"].index(lod)
    ev(f"document.querySelectorAll('.revision-row')[{index}].querySelector('button').click()")
    wait(lambda: ev("document.querySelector('.workspace-right').innerText.includes('LOD Candidate')"))
    assert ev("Number(document.querySelector('.stats-grid strong').textContent.replace(/[^0-9]/g, ''))") == expected
    assert ev("""Array.from(document.querySelectorAll('.workspace-right button'))
        .find(item => item.textContent.trim() === 'Export package').disabled""")
    compare("Compare with Main Revision", "main-vs-lod.png", "the saved LOD and main to render side by side")
    results.update(main_triangles=main["body"]["report"]["triangles"], lod_triangles=expected,
                   lod_viewport="main revision and persisted distance LOD rendered together")


def check(window):
    try:
        ev = window.evaluate_js
        token = TOKEN_FILE.read_text(encoding="ascii")
        if args.models:
            check_model_manager(window)
            return
        asset_detail = None
        with httpx.Client(base_url=BASE, headers={"Authorization": "Bearer " + token}, trust_env=False) as client:
            if args.asset:
                asset_detail = client.get(f"/api/assets/{args.asset}").raise_for_status().json()
                check_asset_viewport(window, asset_detail)
                return
            wait(lambda: ev("document.querySelector('button') && document.body.innerText.includes('Asset production studio')"))
            projects = client.get("/api/projects").json()
            first = next((row for row in projects if row["name"] == "Acceptance test"), None)
            if first:
                assets = client.get("/api/assets", params={"project": first["id"]}).json()
                if assets:
                    asset_detail = client.get(f"/api/assets/{assets[0]['id']}").raise_for_status().json()
                client.post(f"/api/projects/{first['id']}/activate").raise_for_status()
            else:
                assets = []
        ev("Array.from(document.querySelectorAll('button')).find(item => item.textContent.trim()==='New project').click()")
        wait(lambda: ev("document.querySelector('.dialog input') !== null"))
        ev("""(() => {
            const input = document.querySelector('.dialog input');
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'Desktop QA project');
            input.dispatchEvent(new Event('input', {bubbles:true}));
        })()""")
        ev("document.querySelector('.dialog button.primary').click()")
        wait(lambda: ev("document.querySelector('h1')?.textContent === 'Art Bible' && !document.querySelector('.dialog')"))
        ev("document.querySelector('form.stack button.primary').click()")
        wait(lambda: ev("document.body.innerText.includes('Version 2')"))
        ev("location.reload()")
        wait(lambda: ev("document.querySelector('.topbar strong')?.textContent==='Desktop QA project'"))
        ev("Array.from(document.querySelectorAll('nav button')).find(item => item.textContent.trim()==='Art Bible').click()")
        wait(lambda: ev("document.querySelector('h1')?.textContent === 'Art Bible' && document.body.innerText.includes('Version 2')"))
        results["A · WebView2 project, Art Bible and reload"] = "passed"
        if first and assets:
            ev(f"""(() => {{
                const picker = document.querySelector('[aria-label="Active project"]');
                picker.value = {json.dumps(first["id"])};
                picker.dispatchEvent(new Event('change', {{bubbles:true}}));
            }})()""")
            wait(lambda: ev("document.querySelector('.topbar strong')?.textContent === 'Acceptance test'"))
            ev("Array.from(document.querySelectorAll('nav button')).find(item => item.textContent.trim()==='Library').click()")
            wait(lambda: ev("document.querySelector('.asset-tile') !== null"))
            ev("document.querySelector('.asset-tile').click()")
            wait(lambda: ev("document.querySelector('canvas') && !document.querySelector('.viewport-message')"))
            has_gl = ev("""(() => {
                const canvas = document.querySelector('canvas');
                const gl = canvas.getContext('webgl2');
                return !!gl && canvas.width > 100 && canvas.height > 100;
            })()""")
            assert has_gl, "Canvas failed to initialize WebGL 2."
            if asset_detail is not None:
                active = next(item for item in asset_detail["revisions"] if item["id"] == asset_detail["current"])
                if active["body"]["report"]["textures_present"]:
                    assert int(ev("document.querySelector('canvas').dataset.baseColorTextures")) > 0
                    results["B · Embedded PBR base color loaded"] = "passed"
            ev("document.querySelector('[aria-label=\"Frame model\"]').click()")
            ev("document.querySelector('[aria-label=\"Wireframe\"]').click()")
            assert ev("document.querySelector('[aria-label=\"Wireframe\"]').getAttribute('aria-pressed')") == "true"
            ev("document.querySelector('.viewport-top button').click()")
            wait(lambda: ev("document.querySelector('.viewport-top button').textContent.includes('Orthographic')"))
            results["B · Native 3D viewport and camera controls"] = "passed"
        else:
            results["B · Native 3D viewport and camera controls"] = "blocked: run the Blender QA check first"
    except Exception as exc:
        results["error"] = repr(exc)
    finally:
        print(json.dumps(results, indent=2, ensure_ascii=False), flush=True)
        report = "desktop-model-results.json" if args.models else "desktop-asset-results.json" if args.asset else "desktop-results.json"
        (QA / report).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        window.destroy()


def check_model_manager(window):
    ev = window.evaluate_js
    mode = """Array.from(document.querySelectorAll('.topbar button')).find(button => /^(Basic|Advanced) mode$/.test(button.textContent.trim()))"""
    wait(lambda: ev(f"Boolean({mode})"), description="the persisted mode control")
    if ev(f"({mode}).getAttribute('aria-pressed')") == "true":
        ev(f"({mode}).click()")
    ev("Array.from(document.querySelectorAll('nav button')).find(button => button.textContent.trim() === 'Models').click()")
    wait(lambda: ev("document.body.innerText.includes('Recommended for Your Hardware')"), timeout=70,
         description="the measured hardware configuration to load")
    assert ev("document.body.innerText.includes('Pixal3D INT8')")
    assert ev("document.body.innerText.includes('FLUX.2 Klein 4B FP8')")
    assert ev("document.body.innerText.includes('Qwen2.5-Coder 7B Q4_K_M')")
    assert ev("!document.body.innerText.includes('Local providers')")
    ev("""Array.from(document.querySelectorAll('button')).find(button =>
        button.textContent.trim() === 'Apply Recommended Configuration').click()""")
    wait(lambda: ev("document.querySelector('[aria-label=\"Download plan\"]') !== null"),
         description="the plan before any model installation")
    ev(f"({mode}).click()")
    wait(lambda: ev("document.body.innerText.includes('Local providers')"), description="Advanced mode")
    ev("location.reload()")
    wait(lambda: ev(f"({mode})?.getAttribute('aria-pressed') === 'true'"), description="advanced preference after reload")
    ev(f"({mode}).click()")
    wait(lambda: ev(f"({mode})?.getAttribute('aria-pressed') === 'false'"))
    results.update(status="passed", hardware="model recommendations in WebView2", download_plan="review before installation",
                   modes="Basic and Advanced with persistence")


def main():
    backend = start_backend()
    try:
        window = webview.create_window("Soceth 3D Studio — QA", BASE + "/#session=" + TOKEN_FILE.read_text(encoding="ascii"),
                                       width=1400, height=880, background_color="#161917")
        webview.start(check, window, gui="edgechromium", private_mode=False, storage_path=str(QA / "webview"))
    finally:
        server.should_exit = True
        backend.join(7)
    if "error" in results or not results:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

