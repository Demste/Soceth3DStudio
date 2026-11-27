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
WORKFLOWS = {"generate": {"pixal3d", "trellis2", "triposr", "triposg"}, "concept": {"flux-klein"}, "interpret": {"blender-edits"},
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
        if (not 2 <= len(parts) <= 3 or any(part in {"/", "..", "."} for part in parts)
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


class CommercialGameUse(BaseModel):
    status: Literal["reviewed", "conditional", "non_commercial", "review_required"]
    terms: str


class Entry(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    name: str
    provider: Literal["comfy", "local3d", "ollama", "blender"]
    operation: Operation
    workflow: str | None = None
    license: str
    vram: str
    family: str
    version: str
    variant: str
    precision: str
    capability: str
    dependencies: list[Literal["comfy", "cuda", "local3d_python", "pixal3d_nodes", "trellis2_nodes", "flux_nodes", "ollama", "blender"]]
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
    commercial_game: CommercialGameUse | None = None
    manual_only: bool = False

    @model_validator(mode="after")
    def supported(self):
        if self.capability != TASKS[self.operation] or len(self.workflow_ids) != 1 or not set(self.workflow_ids) <= WORKFLOWS[self.operation]:
            raise ValueError("Model capability and workflow do not identify a Studio adapter.")
        expected = {"generate": "local3d" if self.workflow in {"triposr", "triposg"} else "comfy",
                    "concept": "comfy", "interpret": "ollama",
                    "preprocess": "comfy", "mesh": "blender"}[self.operation]
        if self.provider != expected or (self.provider == "ollama" and not (self.tag and self.revision)):
            raise ValueError("Model provider or pinned Ollama manifest is missing.")
        if self.provider in {"comfy", "local3d"} and (self.workflow not in self.workflow_ids or not self.files):
            raise ValueError("Local generation requires a supported workflow and pinned files.")
        if self.provider == "local3d" and (not self.manual_only or
                any(PurePosixPath(file.path).parts[0] != self.workflow for file in self.files)):
            raise ValueError("Standalone 3D models require an explicit choice and a dedicated model directory.")
        if self.provider == "comfy" and any(len(PurePosixPath(file.path).parts) != 2 for file in self.files):
            raise ValueError("ComfyUI artifacts must use its standard one-level model categories.")
        if self.workflow == "trellis2" and (self.commercial_game is None or (not self.manual_only and
                self.commercial_game.status != "reviewed")):
            raise ValueError("TRELLIS.2 requires a commercial game license review before automatic selection.")
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
