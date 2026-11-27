import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from PIL import Image, ImageDraw

from services.backend import core
from services.backend.media import image_data
from services.backend.providers import discovery, models
from services.backend.providers.recommendations import recommend
from tests.test_recommendations import COMPATIBLE, hardware
from integrations.local3d_worker import prepare_image


class ModelManagement(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(folder)
        self.model_dir = self.root / "models"
        self.enterContext(patch.object(core, "DATA", self.root))
        self.enterContext(patch.object(models, "MODELS", self.model_dir))
        with core.db() as conn:
            conn.executescript(core.MIGRATIONS[0])

    def file(self, root, relative):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        contents = relative.encode()
        path.write_bytes(contents)
        return path, {"path": relative, "size": len(contents), "sha256": hashlib.sha256(contents).hexdigest()}

    def record(self, identifier, files, owned=(), external=None):
        model = {"id": identifier, "provider": "comfy", "files": files}
        registered = external or {}
        models.record_installation(identifier, {
            "model": model, "owned": list(owned), "external_files": registered,
            "installation_path": str(self.model_dir),
            "verified_files": {file["path"]: models.file_location(file, registered).stat().st_mtime_ns for file in files},
        })
        return model

    def test_removal_preserves_shared_and_external_files_and_saved_selection(self):
        denoiser, pixal = self.file(self.model_dir, "diffusion_models/pixal")
        _, trellis = self.file(self.model_dir, "diffusion_models/trellis")
        shared, vae = self.file(self.model_dir, "vae/shared")
        unrelated, _ = self.file(self.model_dir, "vae/unrelated")
        external, encoder = self.file(self.root / "outside", "clip_vision/external")
        first = self.record("pixal", [pixal, vae], [pixal["path"], vae["path"]])
        second = self.record("trellis", [trellis, vae, encoder], [trellis["path"]], {encoder["path"]: str(external)})
        core.set_setting("model:generate", "pixal")

        self.assertEqual(models.removal_plan(first), {"mode": "managed", "recoverable_bytes": pixal["size"]})
        models.remove(first)
        self.assertIsNone(models.installation_for(first))
        self.assertFalse(denoiser.exists())
        self.assertTrue(shared.is_file())
        self.assertTrue(models.is_ready(second))
        self.assertEqual(core.setting("model:generate"), "pixal")

        self.assertEqual(models.removal_plan(second)["recoverable_bytes"], trellis["size"] + vae["size"])
        models.remove(second)
        self.assertFalse(shared.exists())
        self.assertTrue(external.is_file())
        self.assertTrue(unrelated.is_file())

    def test_unregister_keeps_external_file_and_reuses_verified_registration(self):
        path, encoder = self.file(self.root / "outside", "clip_vision/external")
        model = self.record("external", [encoder], external={encoder["path"]: str(path)})
        self.assertEqual(models.reusable_external_files(model), {encoder["path"]: str(path)})
        self.assertEqual(models.removal_plan(model), {"mode": "unregister", "recoverable_bytes": 0, "restart": True})
        self.assertTrue(models.remove(model)["restart"])
        self.assertTrue(path.is_file())
        self.assertFalse(models.is_ready(model))

    def test_removal_preserves_replaced_artifact(self):
        path, file = self.file(self.model_dir, "vae/replaced")
        model = self.record("model", [file], [file["path"]])
        path.write_bytes(b"replacement")
        self.assertEqual(models.removal_plan(model)["recoverable_bytes"], 0)
        models.remove(model)
        self.assertEqual(path.read_bytes(), b"replacement")

    def test_install_downloads_only_missing_artifact_and_records_provenance(self):
        external, encoder = self.file(self.root / "outside", "clip_vision/encoder")
        self.record("external", [encoder], external={encoder["path"]: str(external)})
        _, vae = self.file(self.model_dir, "vae/shared")
        weight = b"weight fixture"
        path = "diffusion_models/model"
        denoiser = {"path": path, "size": len(weight), "sha256": hashlib.sha256(weight).hexdigest(),
                    "source": "https://huggingface.co/fixture"}
        model = {"id": "candidate", "provider": "comfy", "files": [denoiser, vae, encoder]}
        ctx = SimpleNamespace(check=lambda: None, stage=lambda *_: None)
        requests = []

        def respond(request):
            requests.append(str(request.url))
            return httpx.Response(200, content=weight)

        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch.object(models.httpx, "Client", return_value=client):
            installed = models.install(model, ctx)
        self.assertEqual(requests, [denoiser["source"]])
        self.assertEqual(installed["owned"], [path])
        self.assertEqual(installed["external_files"], {encoder["path"]: str(external)})
        self.assertTrue(models.is_ready(model))
        self.assertFalse((self.model_dir / encoder["path"]).exists())

    def test_trellis_is_explicit_selection_and_shares_download(self):
        model = models.CATALOG["trellis2-int8"]
        dependencies = [self.file(self.model_dir, item["path"])[1] for item in model["files"][1:]]
        fixture = {**model, "files": [model["files"][0], *dependencies]}
        with patch.object(models, "CATALOG", {**models.CATALOG, model["id"]: fixture}):
            plan = discovery.download_plan([model["id"]], {})
            self.assertEqual(plan["download_bytes"], model["files"][0]["size"])
        deps = {**COMPATIBLE, "trellis2_nodes": True}
        host = hardware("12.0", 16384, 14500)
        decision = recommend(models.CATALOG, host, deps, set())
        self.assertEqual(decision["models"][model["id"]]["status"], "compatible")
        self.assertEqual(decision["recommendations"]["generate"]["model"], "pixal3d")
        self.assertEqual(models.model_for("generate", model["id"], configuration=decision)["id"], model["id"])
        older_host = recommend(models.CATALOG, hardware("8.6", 8192, 6500), deps, set())
        with self.assertRaisesRegex(ValueError, "requires a compatible local setup"):
            discovery.check_candidate_install(model, older_host)

    def test_standalone_models_require_a_verified_runtime_and_keep_manual_selection(self):
        deps = {**COMPATIBLE, "local3d_python": True, "trellis2_nodes": True}
        ampere = recommend(models.CATALOG, hardware("8.6", 8192, 6200), deps, set())
        blackwell = recommend(models.CATALOG, hardware("12.0", 16384, 15000), deps, set())
        self.assertEqual(ampere["models"]["triposr"]["status"], "compatible")
        self.assertEqual(ampere["models"]["triposg"]["status"], "incompatible")
        for model in ("triposr", "triposg", "trellis2-bf16"):
            self.assertEqual(blackwell["models"][model]["status"], "compatible")
            self.assertEqual(models.model_for("generate", model, configuration=blackwell)["id"], model)
        self.assertEqual(blackwell["recommendations"]["generate"]["model"], "pixal3d")
        failed = recommend(models.CATALOG, hardware("12.0", 16384, 15000, "failed"), deps, set())
        self.assertEqual(failed["models"]["triposg"]["status"], "incompatible")

    def test_transparent_palette_upload_and_standalone_preprocessing(self):
        reference = Image.new("P", (96, 64))
        reference.putpalette([0, 0, 0, 150, 90, 20] + [0] * 762)
        ImageDraw.Draw(reference).ellipse((28, 12, 66, 54), fill=1)
        buffer = io.BytesIO()
        reference.save(buffer, "PNG", transparency=0)
        buffer.seek(0)
        path = self.root / "reference.png"
        path.write_bytes(image_data(buffer))
        prepared = prepare_image(path, (255, 255, 255, 255))
        self.assertEqual(prepared.size[0], prepared.size[1])
        self.assertEqual(prepared.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(prepared.getpixel((prepared.width // 2, prepared.height // 2)), (150, 90, 20))
        Image.new("RGB", (50, 50)).save(path)
        with self.assertRaisesRegex(ValueError, "transparent alpha"):
            prepare_image(path, (255, 255, 255, 255))
