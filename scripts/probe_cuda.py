"""Run in the inference interpreter, never the Studio frontend environment."""

import json

result = {"status": "failed", "devices": []}
try:
    import torch

    result.update(torch=torch.__version__, cuda=torch.version.cuda)
    if not torch.cuda.is_available():
        raise RuntimeError("PyTorch cannot initialize a CUDA device.")
    result["compiled_arches"] = torch.cuda.get_arch_list()
    for index in range(torch.cuda.device_count()):
        device = torch.device("cuda", index)
        major, minor = torch.cuda.get_device_capability(device)
        props = torch.cuda.get_device_properties(device)
        row = {"index": index, "name": props.name, "capability": f"{major}.{minor}",
               "total": props.total_memory // 1024 ** 2, "ok": False}
        try:
            a = torch.arange(16, device=device, dtype=torch.float32)
            value = (a * 2 + 1).sum().item()
            torch.cuda.synchronize(device)
            row["ok"] = value == 256.0
        except Exception as exc:
            row["error"] = str(exc)[:600]
        result["devices"].append(row)
    if any(row["ok"] for row in result["devices"]):
        result["status"] = "verified"
except Exception as exc:
    result["error"] = str(exc)[:600]
print(json.dumps(result))
