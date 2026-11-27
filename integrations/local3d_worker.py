"""Offline, single-image adapters executed in a separate PyTorch process."""

import argparse
import json
import sys
from pathlib import Path


def marching_cubes(level, threshold):
    import numpy as np
    import torch
    from skimage.measure import marching_cubes as extract

    vertices, faces, _, _ = extract(level.float().cpu().numpy(), threshold,
                                    gradient_direction="ascent", allow_degenerate=False)
    # The TripoSR caller applies the axis correction expected by torchmcubes.
    return torch.from_numpy(vertices[:, ::-1].copy()), torch.from_numpy(np.ascontiguousarray(faces))


def prepare_image(path, background):
    from PIL import Image

    with Image.open(path) as source:
        image = source.convert("RGBA")
    alpha = image.getchannel("A")
    bounds = alpha.getbbox()
    if not bounds or alpha.getextrema()[0] == 255:
        raise ValueError("Provide a PNG with the background removed and transparent alpha.")
    image = image.crop(bounds)
    size = max(2, round(max(image.size) / 0.85))
    canvas = Image.new("RGBA", (size, size), background)
    canvas.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
    return canvas.convert("RGB")


def triposr_model():
    from tsr.system import TSR
    return TSR


def triposg_pipeline():
    from torch import nn
    from diffusers.models import normalization

    # The upstream decoder imports the old Diffusers alias for torch LayerNorm.
    if not hasattr(normalization, "LayerNorm"):
        normalization.LayerNorm = nn.LayerNorm
    from triposg.models.autoencoders import TripoSGVAEModel
    from triposg.models.transformers import TripoSGDiTModel
    from triposg.pipelines.pipeline_triposg import TripoSGPipeline
    from triposg.schedulers.scheduling_rectified_flow import RectifiedFlowScheduler
    from transformers import BitImageProcessor, Dinov2Model
    return TripoSGPipeline, TripoSGVAEModel, TripoSGDiTModel, RectifiedFlowScheduler, BitImageProcessor, Dinov2Model


def triposr_state_dict(state):
    """Port the pinned checkpoint's nested ViT keys to Transformers 5."""
    prefix = "image_tokenizer.model.encoder.layer."
    renamed = {}
    fields = {
        "attention.attention.query": "attention.q_proj",
        "attention.attention.key": "attention.k_proj",
        "attention.attention.value": "attention.v_proj",
        "attention.output.dense": "attention.o_proj",
        "intermediate.dense": "mlp.fc1",
        "output.dense": "mlp.fc2",
    }
    for name, tensor in state.items():
        if name.startswith(prefix):
            layer, parameter = name[len(prefix):].split(".", 1)
            field, suffix = parameter.rsplit(".", 1)
            name = f"image_tokenizer.model.layers.{layer}.{fields.get(field, field)}.{suffix}"
        if name in renamed:
            raise ValueError("Duplicate key in the TripoSR checkpoint.")
        renamed[name] = tensor
    return renamed


def triposr(files, image, options):
    import torch
    from omegaconf import OmegaConf

    config = OmegaConf.load(files["config.yaml"])
    config.image_tokenizer.pretrained_model_name_or_path = str(files["dino-config.json"])
    OmegaConf.resolve(config)
    model = triposr_model()(config)
    state = torch.load(files["model.ckpt"], map_location="cpu", weights_only=True)
    model.load_state_dict(triposr_state_dict(state), assign=True)
    del state
    model.eval().to(options["device"])
    model.renderer.set_chunk_size(8192)
    scene = model(image, device=options["device"])
    return model.extract_mesh(scene, has_vertex_color=True, resolution=256)[0]


def triposg(files, image, options):
    import torch

    pipeline, vae, transformer, scheduler, processor, encoder = triposg_pipeline()
    local = {"local_files_only": True}
    weights = {"use_safetensors": True, "torch_dtype": torch.float16, **local}
    pipe = pipeline(
        vae.from_pretrained(files["vae/config.json"].parent, **weights),
        transformer.from_pretrained(files["transformer/config.json"].parent, **weights),
        scheduler.from_config(scheduler.load_config(str(files["scheduler/scheduler_config.json"]))),
        encoder.from_pretrained(files["image_encoder_dinov2/config.json"].parent, **weights),
        processor.from_pretrained(files["feature_extractor_dinov2/preprocessor_config.json"].parent, **local),
    )
    pipe.model_cpu_offload_seq = "image_encoder_dinov2->transformer->vae"
    pipe.enable_model_cpu_offload(device=options["device"])
    result = pipe(image=image, guidance_scale=7.0, num_inference_steps=50, use_flash_decoder=False,
                  generator=torch.Generator(device=options["device"]).manual_seed(options["seed"]))
    return result.meshes[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workflow", choices=("triposr", "triposg"))
    parser.add_argument("source", type=Path)
    parser.add_argument("packages", type=Path)
    parser.add_argument("--job", type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(args.source), str(args.packages)]
    # The patched upstream TripoSR module imports this name.
    sys.modules["local3d_worker"] = sys.modules[__name__]

    import torch
    import trimesh

    if args.workflow == "triposr":
        triposr_model()
    else:
        triposg_pipeline()
    if args.job is None:
        if not torch.cuda.is_available():
            raise RuntimeError("The local 3D interpreter cannot initialize CUDA.")
        print("Soceth local 3D imports verified", flush=True)
        return

    job = json.loads(args.job.read_text(encoding="utf-8"))
    image = prepare_image(job["image"], (128, 128, 128, 255) if args.workflow == "triposr" else (255, 255, 255, 255))
    files = {key: Path(value) for key, value in job["files"].items()}
    torch.cuda.set_device(job["device"])
    torch.manual_seed(job["seed"])
    with torch.inference_mode():
        mesh = (triposr if args.workflow == "triposr" else triposg)(files, image, job)
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.vertices) < 3 or len(mesh.faces) == 0:
        raise ValueError("The reconstructor produced no usable surface.")
    mesh.export(job["target"], file_type="glb")


if __name__ == "__main__":
    main()
