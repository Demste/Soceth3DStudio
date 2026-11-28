from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
ID = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
Hex = Annotated[str, StringConstraints(pattern=r"^#[a-fA-F0-9]{6}$")]
Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Distance = Annotated[float, Field(ge=-1000, le=1000, allow_inf_nan=False)]
Angle = Annotated[float, Field(ge=-3600, le=3600, allow_inf_nan=False)]
Scale = Annotated[float, Field(gt=0.001, le=100, allow_inf_nan=False)]
Budget = Annotated[int, Field(ge=12, le=10000000)]
OptimizationProfile = Literal["preserve_structure", "balanced", "aggressive", "lod"]
ColorPolicy = Literal["strict", "guided", "reference"]
TextureSize = Literal[256, 512, 1024, 2048, 4096]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportPreset(Strict):
    format: Literal["unity", "glb", "gltf", "fbx"] = "unity"
    scale: Scale = 1
    lod_ratios: list[Annotated[float, Field(gt=0.02, lt=1)]] = Field(default_factory=lambda: [0.5, 0.25], max_length=4)
    collider: Literal["none", "box", "sphere", "capsule", "mesh"] = "box"
    external_textures: bool = True

    @model_validator(mode="after")
    def descending_lods(self):
        if any(a <= b for a, b in zip(self.lod_ratios, self.lod_ratios[1:])):
            raise ValueError("LOD ratios must be strictly descending.")
        return self


class SemanticMaterial(Strict):
    primary_color: Hex
    approved_colors: list[Hex] = Field(default_factory=list, max_length=24)
    variations: list[Hex] = Field(default_factory=list, max_length=24)
    aliases: list[Text] = Field(default_factory=list, max_length=24)
    roughness: tuple[Unit, Unit] = (0, 1)
    metallic: tuple[Unit, Unit] = (0, 1)
    texture_style: Literal["any", "textured", "flat"] = "any"
    notes: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def ordered_ranges(self):
        if any(low > high for low, high in (self.roughness, self.metallic)):
            raise ValueError("Material range minima must not exceed maxima.")
        return self


class Bible(Strict):
    schema_version: Literal[1] = 1
    style: str = Field(default="Stylized", max_length=2000)
    palette: list[Hex] = Field(default_factory=lambda: ["#D3A46F", "#586C71", "#272931"], max_length=24)
    color_policy: ColorPolicy = "reference"
    semantic_materials: dict[Text, SemanticMaterial] = Field(default_factory=dict, max_length=40)
    default_material: Text | None = None
    materials: list[Text] = Field(default_factory=lambda: ["painted wood", "weathered steel"], max_length=32)
    positive: str = Field(default="", max_length=2000)
    negative: str = Field(default="text, watermark, cropped objects, busy background", max_length=2000)
    geometry: Text = "Stylized"
    triangle_budgets: dict[Text, Budget] = Field(default_factory=lambda: {"Prop": 5000, "Character": 20000, "Environment": 15000}, max_length=40)
    vertex_budgets: dict[Text, Budget] = Field(default_factory=dict, max_length=40)
    texture_size: TextureSize = 1024
    material_rules: str = Field(default="Metallic/roughness PBR", max_length=2000)
    meters_per_unit: Scale = 1
    coordinates: Literal["Unity Y-up / Blender Z-up"] = "Unity Y-up / Blender Z-up"
    prompt_rules: list[Text] = Field(default_factory=list, max_length=32)
    presets: dict[Text, Text] = Field(default_factory=dict, max_length=32)
    export_presets: dict[Text, ExportPreset] = Field(default_factory=lambda: {"Unity 6 URP": ExportPreset()}, max_length=16)


    @model_validator(mode="after")
    def palette_configuration(self):
        if self.default_material and self.default_material not in self.semantic_materials:
            raise ValueError("Select an existing default material category.")
        if self.color_policy != "reference" and not (self.palette or self.semantic_materials):
            raise ValueError("An active project palette requires approved colors.")
        return self


class Project(Strict):
    name: Text
    description: str = Field(default="", max_length=4000)
    engine: Text = "Unity 6 URP"
    platforms: list[Text] = Field(default_factory=lambda: ["Windows"], max_length=12)


class AssetInfo(Strict):
    name: Text
    category: Text = "Prop"
    tags: list[Text] = Field(default_factory=list, max_length=24)


class MaskStroke(Strict):
    points: list[tuple[Unit, Unit]] = Field(min_length=1, max_length=512)
    radius: Annotated[float, Field(gt=0, le=0.25, allow_inf_nan=False)]


class Region(Strict):
    material: Text
    category: Text
    strokes: list[MaskStroke] = Field(default_factory=list, max_length=50)


class JobRequest(Strict):
    kind: Literal["import", "concept", "generate", "interpret", "edit", "optimize", "cleanup", "export", "validate", "open", "capture", "palette_preview", "palette_apply", "palette_restore", "approve"]
    file: ID | None = None
    asset: ID | None = None
    revision: ID | None = None
    proposal: ID | None = None
    confirmed: bool = False
    name: Text = "Untitled asset"
    category: Text = "Prop"
    prompt: str = Field(default="", max_length=3000)
    seed: int | None = Field(default=None, ge=0, le=2**53-1)
    model: Text | None = None
    sampling: dict[Literal["concept_size", "concept_steps"], int] | None = None
    preset: Text | None = None
    mode: Literal["background", "interactive"] = "background"
    triangles: Budget | None = None
    source_texture_size: TextureSize | None = None
    auto_optimize: bool = True
    allow_limited_3d: bool = False
    bake: bool = False
    profile: OptimizationProfile = "balanced"
    create_lod: bool = False
    remove_fragments: bool = True
    planar: bool = True
    regions: list[Region] | None = Field(default=None, max_length=24)
    review_notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def mask_size(self):
        if self.kind != "generate" and (self.source_texture_size is not None or not self.auto_optimize or self.allow_limited_3d):
            raise ValueError("Source textures and automatic optimization are settings for Image-to-3D jobs.")
        if self.allow_limited_3d and not self.model:
            raise ValueError("Select a specific model to enable limited 3D output.")
        if self.sampling is not None:
            if self.kind != "concept" or any(
                (key == "concept_size" and value not in (512, 768, 1024)) or
                (key == "concept_steps" and not 1 <= value <= 12) for key, value in self.sampling.items()
            ):
                raise ValueError("Concept sampling supports 512, 768 or 1024 px and one to twelve steps.")
        if self.create_lod and (self.kind != "optimize" or self.bake or self.triangles is None):
            raise ValueError("Creating a saved LOD requires an explicit triangle target and leaves textures intact.")
        if sum(len(stroke.points) for region in self.regions or []
               for stroke in region.strokes) > 6000:
            raise ValueError("Simplify the selection to at most 6000 brush points.")
        return self


class Targeted(Strict):
    targets: list[ID] = Field(min_length=1, max_length=64)


class Transform(Targeted):
    type: Literal["location", "rotation", "scale"]
    values: tuple[Distance, Distance, Distance]
    relative: bool = False

    @model_validator(mode="after")
    def range_by_type(self):
        if self.type == "scale" and any(not 0.001 < value <= 100 for value in self.values):
            raise ValueError("Scale must be within (0.001, 100].")
        return self


class Dimensions(Targeted):
    type: Literal["dimensions"]
    multipliers: tuple[Scale, Scale, Scale]


class MeshAction(Targeted):
    type: Literal["apply_transforms", "normals", "join", "separate", "inspect", "uv"]


class Rename(Targeted):
    type: Literal["rename"]
    name: Text


class Material(Targeted):
    type: Literal["material"]
    match: str = Field(default="", max_length=100)
    name: Text | None = None
    color: Hex | None = None
    roughness: Unit | None = None
    metallic: Unit | None = None

    @model_validator(mode="after")
    def require_change(self):
        if all(value is None for value in (self.name, self.color, self.roughness, self.metallic)):
            raise ValueError("Specify at least one material property.")
        return self


class Pivot(Targeted):
    type: Literal["pivot"]
    mode: Literal["bottom_center", "bounds_center", "world_origin"]


class Bevel(Targeted):
    type: Literal["bevel"]
    width: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
    segments: int = Field(default=2, ge=1, le=6)


class Subdivision(Targeted):
    type: Literal["subdivision"]
    levels: int = Field(ge=0, le=3)


class DecimateMesh(Targeted):
    type: Literal["decimate_mesh"]
    target_triangles: Budget | None = None
    ratio: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)] | None = None
    preserve_uvs: bool = True
    profile: OptimizationProfile = "balanced"
    create_lod: bool = False

    @model_validator(mode="after")
    def reduction(self):
        if self.target_triangles is None and self.ratio is None:
            raise ValueError("Specify a triangle target or remaining triangle ratio.")
        if self.target_triangles is not None:
            # A model may emit a redundant ratio alongside a precise target.
            self.ratio = None
        return self


class CleanupMesh(Targeted):
    type: Literal["cleanup_mesh"]
    profile: OptimizationProfile = "preserve_structure"
    remove_fragments: bool = True
    planar: bool = True
    preserve_uvs: bool = True


class LegacyDecimate(Targeted):
    type: Literal["decimate"]
    triangles: Budget


class Primitive(Targeted):
    type: Literal["primitive"]
    shape: Literal["box", "sphere", "cylinder", "torus"]
    name: Text
    dimensions: tuple[Scale, Scale, Scale]
    offset: tuple[Distance, Distance, Distance] = (0, 0, 0)
    rotation: tuple[Angle, Angle, Angle] = (0, 0, 0)


class Handle(Targeted):
    type: Literal["handles"]
    axis: Literal["X", "Y"] = "X"
    thickness: Annotated[float, Field(gt=0, le=0.25, allow_inf_nan=False)] = 0.035


class Collider(Targeted):
    type: Literal["collider"]
    shape: Literal["box", "sphere", "capsule", "mesh"]


Operation = Annotated[Union[Transform, Dimensions, MeshAction, Rename, Material, Pivot,
                           Bevel, Subdivision, DecimateMesh, CleanupMesh, Primitive, Handle, Collider],
                      Field(discriminator="type")]

DESTRUCTIVE = {"apply_transforms", "decimate_mesh", "cleanup_mesh", "join", "separate", "uv", "pivot"}


class Proposal(Strict):
    protocol_version: Literal[1] = 1
    intent: Literal["edit_mesh", "unsupported"]
    explanation: str = Field(max_length=2000)
    operations: list[Operation] = Field(default_factory=list, max_length=24)

    @model_validator(mode="before")
    @classmethod
    def upgrade_saved_operations(cls, value):
        if isinstance(value, dict) and isinstance(value.get("operations"), list):
            def upgrade(op):
                if isinstance(op, dict) and op.get("type") == "decimate":
                    legacy = LegacyDecimate.model_validate(op)
                    return {"type": "decimate_mesh", "targets": legacy.targets,
                            "target_triangles": legacy.triangles}
                return op
            return {**value, "operations": [upgrade(op) for op in value["operations"]]}
        return value

    @model_validator(mode="after")
    def validate_intent(self):
        if (self.intent == "edit_mesh") != bool(self.operations):
            raise ValueError("An edit must have operations and an unsupported proposal must be empty.")
        if any(isinstance(op, DecimateMesh) and (op.create_lod or op.profile == "lod") for op in self.operations):
            if len(self.operations) != 1:
                raise ValueError("Generate a LOD as a single operation on its saved main revision.")
        return self

