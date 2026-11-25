"""Check public release metadata and keep installation changes explicit."""
import json
import re
import time

import httpx

from .core import ROOT, setting, set_setting


def version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    return tuple(int(item) for item in match.groups()) if match else ()


def check(force=False):
    release = json.loads((ROOT / "shared/release.json").read_text())
    current, repository = release["version"], release["repository"]
    cached = setting("app_update", {})
    if not force and cached.get("current") == current and time.time() - cached.get("checked", 0) < 86400:
        return cached
    result = {"current": current, "checked": time.time(), "available": False}
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        return result
    try:
        response = httpx.get(f"https://api.github.com/repos/{repository}/releases/latest", timeout=5)
        response.raise_for_status()
        tag = response.json()["tag_name"]
        if version(tag) > version(current):
            result.update(available=True, tag=tag, url=f"https://github.com/{repository}/releases/tag/{tag}")
    except (httpx.HTTPError, ValueError, KeyError):
        result["reachable"] = False
    set_setting("app_update", result)
    return result
