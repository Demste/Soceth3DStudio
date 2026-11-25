import json

from ..core import ROOT

TEMPLATE = ROOT / "integrations/comfyui_workflows/3d_pixal3d_trellis2_image_to_model.json"

PROFILES = {
    "LOW_VRAM": dict(concept=512, crop=512, shape=512, remesh=256, triangles=60000, texture=512),
    "BALANCED": dict(concept=768, crop=768, shape=1024, remesh=384, triangles=120000, texture=1024),
    "HIGH_QUALITY": dict(concept=1024, crop=1024, shape=1536, remesh=512, triangles=300000, texture=2048),
}
PROFILES["QUALITY"] = PROFILES["HIGH_QUALITY"]


def node(kind, **inputs):
    return {"class_type": kind, "inputs": inputs}


def ordered_inputs(schema):
    for group in ("required", "optional"):
        definitions = schema["input"].get(group, {})
        order = schema.get("input_order", {}).get(group, definitions.keys())
        for name in order:
            yield name, group, definitions[name]


def selected_option(options, selected):
    return next((item for item in options.get("options", []) if item["key"] == selected), None)


def flux_graph(info, model, prompt, seed, profile, prefix, sampling=None):
    files = {entry["path"].split("/")[0]: entry["path"].split("/")[-1] for entry in model["files"]}
    sampling = sampling or {}
    ceiling = PROFILES[profile]["concept"]
    size = sampling.get("concept_size", ceiling)
    if size > ceiling:
        raise ValueError(f"The selected execution profile supports concept previews up to {ceiling}px.")
    return {
        "unet": node("UNETLoader", unet_name=files["diffusion_models"], weight_dtype="default"),
        "text": node("CLIPLoader", clip_name=files["text_encoders"], type="flux2",
                     device="cpu" if profile == "LOW_VRAM" or model["id"] == "flux-klein-bf16" else "default"),
        "vae": node("VAELoader", vae_name=files["vae"]),
        "positive": node("CLIPTextEncode", text=prompt, clip=["text", 0]),
        "negative": node("ConditioningZeroOut", conditioning=["positive", 0]),
        "guide": node("CFGGuider", model=["unet", 0], positive=["positive", 0], negative=["negative", 0], cfg=1),
        "schedule": node("Flux2Scheduler", steps=sampling.get("concept_steps", 4), width=size, height=size),
        "sampler": node("KSamplerSelect", sampler_name="euler"),
        "noise": node("RandomNoise", noise_seed=seed),
        "latent": node("EmptyFlux2LatentImage", width=size, height=size, batch_size=1),
        "sample": node("SamplerCustomAdvanced", noise=["noise", 0], guider=["guide", 0],
                       sampler=["sampler", 0], sigmas=["schedule", 0], latent_image=["latent", 0]),
        "decode": node("VAEDecode", samples=["sample", 0], vae=["vae", 0]),
        "save": node("SaveImage", images=["decode", 0], filename_prefix=prefix),
    }, "save"


def pixal_graph(info, model, image, seed, profile, prefix, texture_limit):
    source = json.loads(TEMPLATE.read_text(encoding="utf-8-sig"))
    inputs = {item["id"]: item for item in source["nodes"]}
    links = {link[0]: (link[1], link[2]) for link in source["links"]}
    chosen = PROFILES[profile]
    resolution = min(chosen["texture"], texture_limit)
    graph = {}

    def connections(item):
        return {pin["name"]: links[pin["link"]] for pin in item.get("inputs", []) if pin.get("link") in links}

    upsample = next(item for item in inputs.values() if item["type"] == "Trellis2UpsampleStage")
    cascade_source = connections(upsample)["shape_latent"]

    def resolve(identifier, slot):
        item = inputs[identifier]
        kind = item["type"]
        connected = connections(item)
        if chosen["shape"] == 512:
            # Native Trellis2ShapeStage produces a complete 512 latent. The cascade node
            # only accepts 1024 and above, so use the first pass directly.
            if kind == "KSampler" and connected.get("latent_image", (None,))[0] == upsample["id"]:
                return resolve(*cascade_source)
            if identifier == upsample["id"] and slot in (0, 1):
                return resolve(*connected[("positive", "negative")[slot]])
        if kind == "ComfySwitchNode":
            switch = connected.get("switch")
            flag = inputs[switch[0]]["widgets_values"][0] if switch else item["widgets_values"][0]
            return resolve(*connected["on_true" if flag else "on_false"])
        if kind in {"PreviewImage", "MaskPreview", "GetMeshInfo"}:
            return resolve(*next(iter(connected.values())))
        key = str(identifier)
        if key in graph:
            return [key, slot]
        schema = info.get(kind)
        if not schema or schema.get("python_module", "").startswith("custom_nodes"):
            raise ValueError(f"Unsupported ComfyUI release: native {kind} is unavailable.")
        values = iter(item.get("widgets_values") or [])
        arguments = {}
        for name, group, definition in ordered_inputs(schema):
            typ, *config = definition
            options = config[0] if config else {}
            if typ == "COMFY_DYNAMICCOMBO_V3":
                selected = next(values, None)
                option = selected_option(options, selected)
                if option is None:
                    raise ValueError(f"Unknown dynamic setting for {kind}.{name}: {selected}.")
                arguments[name] = selected
                for field, _, field_type in ordered_inputs({"input": option["inputs"]}):
                    default = field_type[1].get("default") if len(field_type) > 1 else None
                    arguments[f"{name}.{field}"] = next(values, default)
                continue
            widget = (isinstance(typ, list) or typ in {"COMBO", "INT", "FLOAT", "BOOLEAN", "STRING", "COLOR"}
                      or name == "viewport_state") and not options.get("forceInput")
            supplied = next(values, None) if widget else None
            if widget and options.get("control_after_generate"):
                next(values, None)
            if name in connected:
                arguments[name] = resolve(*connected[name])
            elif supplied is not None:
                arguments[name] = supplied
            elif group == "required":
                if "default" not in options:
                    raise ValueError(f"Workflow template and ComfyUI schema differ for {kind}.{name}.")
                arguments[name] = options["default"]
        if kind == "LoadImage":
            arguments["image"] = image
        if kind == "KSampler":
            arguments["seed"] = (seed + identifier) % (2**53 - 1)
        if kind == "Save3DAdvanced":
            arguments.update(filename_prefix=prefix, viewport_state={})
        if kind == "Trellis2UpsampleStage":
            arguments["target_resolution"] = chosen["shape"]
        if kind in {"BakeNormalMapFromMesh", "BakeAmbientOcclusion", "PrimitiveInt", "ImageCropToMask", "DecimateMesh", "RemeshMesh"}:
            overrides = {2048: resolution, 1024: resolution, 4096: resolution, 700000: chosen["triangles"], 768: chosen["remesh"]}
            if kind == "ImageCropToMask":
                overrides = {1024: chosen["crop"]}
            for name, value in list(arguments.items()):
                if type(value) is int and value in overrides:
                    arguments[name] = overrides[value]
        graph[key] = node(kind, **arguments)
        return [key, slot]

    output = next(item["id"] for item in inputs.values() if item["type"] == "Save3DAdvanced")
    resolve(output, 0)
    loader = next(item for item in graph.values() if item["class_type"] == "UNETLoader")
    loader["inputs"]["unet_name"] = next(item["path"].split("/")[-1] for item in model["files"] if item["path"].startswith("diffusion_models/"))
    return graph, str(output)


def validate_graph(info, graph, *, require_artifacts=True):
    required_artifacts = {
        ("UNETLoader", "unet_name"), ("VAELoader", "vae_name"),
        ("CLIPVisionLoader", "clip_name"), ("CLIPLoader", "clip_name"),
        ("LoadMoGeModel", "model_name"), ("LoadBackgroundRemovalModel", "model_name"),
    }
    missing = []
    for item in graph.values():
        kind, inputs = item["class_type"], item["inputs"]
        schema = info.get(kind)
        if not schema or schema.get("python_module", "").startswith("custom_nodes"):
            raise ValueError(f"ComfyUI is missing the native node {kind}. Use the pinned ComfyUI release.")
        definitions = {name: definition for name, _, definition in ordered_inputs(schema)}
        for name, definition in list(definitions.items()):
            typ, *config = definition
            if typ == "COMFY_DYNAMICCOMBO_V3" and name in inputs:
                option = selected_option(config[0], inputs[name])
                if option is None:
                    raise ValueError(f"Unknown dynamic setting {kind}.{name}.")
                definitions.update({f"{name}.{field}": spec
                                    for field, _, spec in ordered_inputs({"input": option["inputs"]})})
        if set(inputs) - definitions.keys():
            names = ", ".join(set(inputs) - definitions.keys())
            raise ValueError(f"Incompatible ComfyUI API for {kind}: {names}.")
        for name, value in inputs.items():
            # ComfyUI's LoadImage dropdown only lists top-level images. The node's
            # VALIDATE_INPUTS checks the actual path returned by the upload route.
            if kind == "LoadImage" and name == "image":
                continue
            choices, *config = definitions[name]
            bounds = config[0] if config else {}
            if type(value) in (int, float) and (value < bounds.get("min", value) or value > bounds.get("max", value)):
                raise ValueError(f"{kind}.{name} = {value} is outside the ComfyUI input range.")
            if choices == "COMBO":
                choices = bounds.get("options", [])
            if isinstance(choices, list) and not isinstance(value, list) and value not in choices:
                if (kind, name) in required_artifacts or kind in {"LoadMoGeModel", "LoadBackgroundRemovalModel"}:
                    missing.append(f"{kind}.{name} → {value}")
                else:
                    raise ValueError(f"{kind}.{name} does not offer {value!r}. Check the ComfyUI release.")
    if missing and require_artifacts:
        raise ValueError("ComfyUI cannot see these model or image files: " + ", ".join(missing))
    return missing

