"""Validate release metadata before exposing it to installers or graph builders."""

import json
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

from ..locations import ROOT, configured

Tier = Literal["LOW_VRAM", "BALANCED", "HIGH_QUALITY", "CUSTOM"]
Evidence = Literal["measured", "documented", "estimated", "experimental", "local"]
Operation = Literal["generate", "concept", "interpret", "preprocess", "mesh"]
TASKS = {"generate": "IMAGE_TO_3D", "concept": "TEXT_TO_IMAGE", "interpret": "LOCAL_LLM",
         "preprocess": "IMAGE_PREPROCESSING", "mesh": "MESH_PROCESSING"}
WORKFLOWS = {"generate": {"pixal3d"}, "concept": {"flux-klein"}, "interpret": {"blender-edits"},
             "preprocess": {"pixal3d"}, "mesh": {"blender-worker"}}


class Artifact(BaseModel):
    source: str
    repo: str
    revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    path: str
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    license: str

    @model_validator(mode="after")
    def safe_source(self):
        parts = PurePosixPath(self.path).parts
        url = urlsplit(self.source)
        if (len(parts) != 2 or any(part in {"/", "..", "."} for part in parts)
            or "\\" in self.path or ":" in self.path or url.scheme != "https" or url.hostname != "huggingface.co"
            or url.username or url.password or not url.path.startswith(f"/{self.repo}/resolve/{self.revision}/")):
            raise ValueError("Artifacts require a safe model filename and a pinned Hugging Face HTTPS source.")
        return self


class Execution(BaseModel):
    profile: Tier
    total_vram: int = Field(ge=0)
    free_vram: int = Field(ge=0)
    features: list[str]
    evidence: Evidence
    note: str


class Measurement(BaseModel):
    seconds: float = Field(gt=0, allow_inf_nan=False)
    peak_system_vram: int = Field(gt=0)
    compute: str
    total_vram: int
    profile: Tier
    note: str


class Entry(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    name: str
    provider: Literal["comfy", "ollama", "blender"]
    operation: Operation
    workflow: str | None = None
    license: str
    vram: str
    family: str
    version: str
    variant: str
    precision: str
    capability: str
    dependencies: list[Literal["comfy", "cuda", "pixal3d_nodes", "flux_nodes", "ollama", "blender"]]
    min_ram: int = Field(ge=0)
    min_compute: str = Field(pattern=r"^\d+\.\d+$")
    configs: list[Execution] = Field(min_length=1)
    verification: Evidence
    commercial: Literal["permissive", "conditional", "unknown", "restricted"]
    source: str
    traits: list[str]
    cpu_offload: bool
    input_formats: list[str]
    output_formats: list[str]
    workflow_ids: list[str]
    attribution: str
    disk_bytes: int = Field(ge=0)
    files: list[Artifact]
    tag: str | None = None
    revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    size: int | None = None
    bundled_with: str | None = None
    measurement: Measurement | None = None

    @model_validator(mode="after")
    def supported(self):
        if self.capability != TASKS[self.operation] or set(self.workflow_ids) != WORKFLOWS[self.operation]:
            raise ValueError("Model capability and workflow do not identify a Studio adapter.")
        expected = {"generate": "comfy", "concept": "comfy", "interpret": "ollama",
                    "preprocess": "comfy", "mesh": "blender"}[self.operation]
        if self.provider != expected or (self.provider == "ollama" and not (self.tag and self.revision)):
            raise ValueError("Model provider or pinned Ollama manifest is missing.")
        if self.provider == "comfy" and (self.workflow not in self.workflow_ids or not self.files):
            raise ValueError("ComfyUI requires a supported workflow and pinned model files.")
        if self.operation == "concept" and {file.path.split("/")[0] for file in self.files} != {
            "diffusion_models", "text_encoders", "vae"
        }:
            raise ValueError("The FLUX adapter requires a denoiser, text encoder and VAE.")
        if len({file.path for file in self.files}) != len(self.files):
            raise ValueError("Duplicate artifact paths.")
        if len({config.profile for config in self.configs}) != len(self.configs):
            raise ValueError("Duplicate execution profiles.")
        return self


def load(path=None):
    path = Path(path or configured("catalog") or ROOT / "shared/model-catalog.json")
    rows = [Entry.model_validate(row).model_dump(exclude_none=True)
            for row in json.loads(path.read_text(encoding="utf-8-sig"))]
    records = {row["id"]: row for row in rows}
    if len(records) != len(rows) or {row["operation"] for row in rows} != set(TASKS):
        raise ValueError("The catalog must have unique IDs and cover every Studio task.")
    if any(row.get("bundled_with") not in records for row in rows if row.get("bundled_with")):
        raise ValueError("A bundled model points to a missing installation.")
    return records
