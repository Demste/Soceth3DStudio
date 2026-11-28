"""Rank compatible execution configurations separately from model quality evidence."""

from .registry import TASKS, TEXTURED
from ..hardware import compute

ORDER = {"LOW_VRAM": 0, "BALANCED": 1, "HIGH_QUALITY": 2, "CUSTOM": 3}
PRIMARY = ("generate", "concept", "interpret")
DEFAULT_POLICY = {"verification": 8, "output_quality": 5, "compatibility": 5, "stability": 3, "installed": 3,
                  "commercial": 2, "features": 1, "headroom": 1, "performance": 1, "provenance": 3}


def evaluate(model, hardware, dependencies, installed=False, requested="AUTO"):
    missing = [name for name in model["dependencies"] if not dependencies.get(name)]
    cards = hardware["gpus"]
    supported = [card for card in cards if compute(card) >= tuple(map(int, model["min_compute"].split(".")))]
    runtime = hardware["runtime"]
    cuda_provider = model["provider"] in {"comfy", "local3d"}
    if cuda_provider and runtime["status"] == "verified":
        verified = {(row["name"], row["capability"]) for row in runtime["devices"] if row["ok"]}
        supported = [card for card in supported if (card["name"], card["capability"]) in verified]
    ram_ok = not hardware["ram"]["total"] or hardware["ram"]["total"] >= model["min_ram"] - 256
    reasons, options = [], []
    ceiling = ORDER.get("HIGH_QUALITY" if requested == "QUALITY" else requested, ORDER["CUSTOM"])
    for config in model["configs"]:
        if ORDER[config["profile"]] > ceiling:
            continue
        total, free = config["total_vram"], config["free_vram"]
        card = next((card for card in sorted(supported, key=lambda x: x["free"], reverse=True)
                     if card["total"] >= total - 256 and card["free"] >= free), None)
        if card or total == 0:
            options.append((config, card, False))
    if (not options and ram_ok and model["provider"] == "ollama" and model["cpu_offload"]
            and model["verification"] in {"measured", "local"}):
        config = model["configs"][0]
        options.append((config, None, True))
    if not ram_ok:
        reasons.append("Insufficient system memory for this configuration.")
    elif not options:
        reasons.append("GPU architecture, total VRAM, or currently free VRAM falls below the catalog allowance.")
    if cuda_provider and runtime["status"] == "failed":
        reasons.append("The selected PyTorch runtime failed the CUDA kernel check.")
    if missing:
        reasons.append("Requires: " + ", ".join(missing))
    if model["commercial"] in {"restricted", "unknown"}:
        reasons.append("Review the model terms before commercial use.")
    execution, card, offload = max(options, key=lambda row: ORDER[row[0]["profile"]], default=(None, None, False))
    possible = ram_ok and bool(options)
    runtime_failed = cuda_provider and runtime["status"] == "failed"
    compatible = possible and not missing and not runtime_failed
    status = "compatible" if compatible else "setup_required" if possible and missing and not runtime_failed else "incompatible"
    confidence = execution["evidence"] if execution else model["verification"]
    if confidence == "measured" and card and (card["capability"] != "8.6" or card["total"] != 8192) and model["provider"] != "blender":
        confidence = "documented"
    if offload:
        reasons.append("CPU offload can be slower. GPU memory was insufficient for the pinned allowance.")
    if execution:
        reasons.append(execution["note"])
    measured = model.get("measurement")
    relevant_measurement = bool(execution and measured and card and measured["profile"] == execution["profile"] and
                                measured["compute"] == card["capability"] and abs(measured["total_vram"] - card["total"]) <= 256)
    return {"status": status, "execution": execution, "gpu": card["index"] if card else None, "offload": offload,
            "confidence": confidence, "reasons": reasons, "installed": installed,
            "headroom": max(0, card["free"] - execution["free_vram"]) if card and execution else 0,
            "measurement": measured if relevant_measurement else None}


def recommend(models, hardware, dependencies, installed, requested="AUTO", policy=None):
    weights = {**DEFAULT_POLICY, **(policy or {})}
    states = {model["id"]: evaluate(model, hardware, dependencies, model["id"] in installed, requested)
              for model in models.values()}
    recommendations = {}
    for operation in TASKS:
        candidates = [model for model in models.values() if model["operation"] == operation]
        eligible = [model for model in candidates if model["verification"] == "measured" and not model.get("manual_only")
                    and model["commercial"] in {"permissive", "conditional"}
                    and (operation != "generate" or model.get("output_capability") in TEXTURED)
                    and states[model["id"]]["status"] != "incompatible"]

        def rank(model):
            state = states[model["id"]]
            signals = {"verification": state["confidence"] == "measured",
                       "output_quality": model["verification"] == "measured",
                       "stability": bool(state["measurement"]),
                       "compatibility": state["status"] == "compatible",
                       "installed": state["installed"], "commercial": model["commercial"] == "permissive",
                       "features": ORDER[state["execution"]["profile"]] if state["execution"] else 0,
                       "headroom": min(state["headroom"] / 4096, 1),
                       "performance": 1 / max(1, state["measurement"]["seconds"]) if state["measurement"] else 0,
                       "provenance": bool(model.get("revision") or model["files"])}
            return sum(weights[key] * value for key, value in signals.items())

        best = max(eligible, key=rank, default=None)
        recommendations[operation] = {
            "model": best["id"] if best else None,
            "candidates": [model["id"] for model in candidates if model["verification"] in {"documented", "experimental"}
                           and states[model["id"]]["status"] != "incompatible"],
        }
    recommendations["text_to_3d"] = {"model": None, "stages": ["concept", "generate"], "candidates": []}
    levels = [states[item["model"]]["execution"]["profile"] for key, item in recommendations.items()
              if key in PRIMARY and item["model"] and states[item["model"]]["execution"]]
    tier = max(levels, key=ORDER.__getitem__, default="LOW_VRAM")
    return {"models": states, "recommendations": recommendations,
            "tier": "CUSTOM" if requested == "CUSTOM" else tier}
