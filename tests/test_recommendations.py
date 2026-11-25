import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.backend import hardware as analyzer
from services.backend.hardware import runtime_archive
from services.backend.providers import discovery, models
from services.backend.providers.recommendations import recommend
from services.backend.providers.registry import load


COMPATIBLE = {key: True for key in ("cuda", "comfy", "pixal3d_nodes", "flux_nodes", "ollama", "blender")}


def hardware(capability, total, free, status="verified"):
    gpu = dict(index=0, uuid="fixture", name="simulated", capability=capability, total=total, free=free, driver="617.42")
    return dict(gpus=[gpu], ram={"total": 32768}, runtime={"status": status, "devices": [{**gpu, "ok": True}]})


class Recommendations(unittest.TestCase):
    def setUp(self):
        self.catalog = load()

    def select(self, host, deps=COMPATIBLE, installed=(), requested="AUTO"):
        return recommend(self.catalog, host, deps, installed, requested)

    def test_rtx_3070(self):
        host = hardware("8.6", 8192, 6900)
        answer = self.select(host, installed=["pixal3d"])
        self.assertEqual(runtime_archive(host["gpus"]), "cu126")
        self.assertEqual(answer["tier"], "LOW_VRAM")
        for kind, model in (("generate", "pixal3d"), ("concept", "flux-klein"), ("interpret", "qwen-coder")):
            self.assertEqual(answer["recommendations"][kind]["model"], model)
            self.assertEqual(answer["models"][model]["confidence"], "measured")
        self.assertTrue(answer["models"]["pixal3d"]["installed"])

    def test_rtx_5070_ti_is_not_an_inference_claim(self):
        host = hardware("12.0", 16384, 15100)
        answer = self.select(host)
        self.assertEqual(runtime_archive(host["gpus"]), "cu130")
        self.assertEqual(answer["tier"], "HIGH_QUALITY")
        self.assertEqual(answer["models"]["pixal3d"]["execution"]["profile"], "BALANCED")
        self.assertIn("qwen-coder-14b", answer["recommendations"]["interpret"]["candidates"])
        self.assertIn("flux-klein-bf16", answer["recommendations"]["concept"]["candidates"])
        self.assertNotIn("pixal3d-bf16", answer["recommendations"]["generate"]["candidates"])
        self.assertEqual(answer["models"]["pixal3d"]["confidence"], "estimated")
        self.assertEqual(answer["recommendations"]["interpret"]["model"], "qwen-coder")

    def test_insufficient_vram_and_runtime(self):
        answer = self.select(hardware("8.6", 4096, 3600))
        self.assertIsNone(answer["recommendations"]["generate"]["model"])
        self.assertTrue(answer["models"]["qwen-coder"]["offload"])
        answer = self.select(hardware("12.0", 16384, 14800, "failed"))
        self.assertEqual(answer["models"]["pixal3d"]["status"], "incompatible")

    def test_missing_provider_and_native_nodes(self):
        answer = self.select(hardware("8.6", 8192, 6900), {**COMPATIBLE, "comfy": False, "flux_nodes": False})
        self.assertEqual(answer["models"]["flux-klein"]["status"], "setup_required")
        self.assertTrue(any("flux_nodes" in item for item in answer["models"]["flux-klein"]["reasons"]))

    def test_upgrade_keeps_user_selection(self):
        host = hardware("12.0", 16384, 15100)
        choice = self.select(host)
        project = "0" * 32
        values = {project + ":model:interpret": "qwen-coder-14b", "model:interpret": "qwen-coder"}
        with patch.object(models, "setting", side_effect=lambda key, default=None: values.get(key, default)):
            self.assertEqual(models.model_for("interpret", project=project, configuration=choice)["id"], "qwen-coder-14b")
            self.assertEqual(models.model_for("interpret", "qwen-coder", project, choice)["id"], "qwen-coder")
            self.assertEqual(models.model_for("interpret", configuration=choice)["id"], "qwen-coder")
        self.assertEqual(self.select(host, requested="LOW_VRAM")["tier"], "LOW_VRAM")

    def test_hardware_change_is_persisted_without_overriding_preferences(self):
        settings = {"model:interpret": "qwen-coder"}
        with (
            patch.object(analyzer, "setting", side_effect=lambda key, default=None: settings.get(key, default)),
            patch.object(analyzer, "set_setting", side_effect=settings.__setitem__),
        ):
            self.assertFalse(analyzer.track(hardware("8.6", 8192, 6900)))
            self.assertTrue(analyzer.track(hardware("12.0", 16303, 15100)))
            self.assertTrue(analyzer.track(hardware("12.0", 16303, 14600)))
            self.assertEqual(settings["model:interpret"], "qwen-coder")

    def test_existing_artifact_search(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "split_files/diffusion_models/model.safetensors"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"fixture")
            self.assertEqual(discovery.locate({"path": "diffusion_models/model.safetensors", "size": 7}, [folder]), path)
            self.assertIsNone(discovery.locate({"path": "diffusion_models/model.safetensors", "size": 8}, [folder]))
            self.assertEqual(len(list(root.rglob("*.safetensors"))), 1)

    def test_registry_covers_text_to_3d_and_builtin_stages(self):
        answer = self.select(hardware("8.6", 8192, 6900))
        self.assertEqual(answer["recommendations"]["text_to_3d"]["stages"], ["concept", "generate"])
        self.assertEqual(answer["recommendations"]["mesh"]["model"], "blender-worker")
        self.assertIn(answer["recommendations"]["preprocess"]["model"], {"birefnet", "moge-2"})


if __name__ == "__main__":
    unittest.main()
