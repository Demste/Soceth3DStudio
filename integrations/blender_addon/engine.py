import json
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix

from . import materials, operations, optimization, palette, scene


def report(objects, budget, texture_limit, vertex_budget=None):
    low, high = scene.bounds(objects)
    rows, issues, mats, textures = [], [], {}, {}
    def add(level, code, message):
        issues.append(dict(level=level, code=code, message=message))
    for obj in objects:
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        data = evaluated.to_mesh()
        geom = bmesh.new()
        try:
            geom.from_mesh(data)
            count = scene.triangle_count(data)
            degenerate = sum(face.calc_area() <= 1e-12 for face in geom.faces)
            nonmanifold = sum(not edge.is_manifold for edge in geom.edges)
            if not count or degenerate:
                add("error", "degenerate", f"{obj.name}: {count} triangles, {degenerate} zero-area faces.")
            if nonmanifold:
                add("warning", "non_manifold", f"{obj.name}: {nonmanifold} open or non-manifold edges.")
            if not data.uv_layers:
                add("warning", "missing_uv", f"{obj.name} has no UV map.")
            names = []
            for material in filter(None, data.materials):
                names.append(material.name)
                mats[material.name] = materials.describe(material)
                for image in materials.images_for(material):
                    textures[image.name] = dict(name=image.name, size=list(image.size),
                        path="" if image.packed_file else image.filepath, missing=not all(image.size))
            rows.append(dict(id=obj.get(scene.ID), name=obj.name, vertices=len(data.vertices),
                             triangles=count, dimensions=list(obj.dimensions), uv=bool(data.uv_layers), materials=names,
                             material_slots=[material.name if material else None for material in data.materials],
                             matrix_world=[list(row) for row in evaluated.matrix_world]))
        finally:
            geom.free()
            evaluated.to_mesh_clear()
    count = sum(row["triangles"] for row in rows)
    vertices = sum(row["vertices"] for row in rows)
    if budget and count > budget:
        add("error", "triangle_budget", f"{count} triangles exceed the {budget} triangle budget.")
    if vertex_budget is not None and vertices > vertex_budget:
        add("error", "vertex_budget", f"{vertices} vertices exceed the {vertex_budget} Blender vertex budget.")
    missing = []
    for texture in textures.values():
        if texture["missing"]:
            missing.append(texture["path"])
            add("error", "missing_texture", f"Texture {texture['name']} cannot be read.")
        if max(texture["size"]) > texture_limit:
            add("error", "texture_budget", f"Texture {texture['name']} exceeds {texture_limit}px.")
    size = max(high - low)
    if not 0.01 <= size <= 100:
        add("warning", "scale", f"Maximum dimension {size:.4g} m is outside 0.01–100 m.")
    if not textures:
        add("info", "no_textures", "No image textures are in use.")
    return dict(objects=rows, object_count=len(rows), triangles=count, vertices=vertices, vertex_budget=vertex_budget,
                bounds=[list(low), list(high)], materials=list(mats.values()), material_count=len(mats),
                uv_present=all(row["uv"] for row in rows), textures=list(textures.values()), textures_present=bool(textures),
                missing_paths=missing, within_budget=not any(item["code"].endswith("_budget") for item in issues),
                valid=not any(item["level"] == "error" for item in issues), issues=issues)


def scale_world(objects, scale):
    matrix = Matrix.Diagonal((scale, scale, scale, 1))
    for obj in objects:
        if obj.parent not in objects:
            obj.matrix_world = matrix @ obj.matrix_world
    bpy.context.view_layer.update()


def load_saved_lod(entry, material_index, scale):
    rows = {row["name"]: row for row in entry["objects"]}
    with bpy.data.libraries.load(entry["source"], link=False) as (source, target):
        if rows.keys() - set(source.objects):
            raise ValueError("A saved LOD scene no longer contains its recorded mesh parts.")
        target.objects = list(rows)
    objects = target.objects
    for obj, row in zip(objects, rows.values()):
        obj.parent = None
        obj.matrix_world = Matrix(row["matrix_world"])
        bpy.context.collection.objects.link(obj)
        for index, name in enumerate(row["material_slots"]):
            if name in material_index:
                obj.data.materials[index] = material_index[name]
    scale_world(objects, scale)
    return objects


def export_package(base, out, command, validation):
    if any(item["code"] == "degenerate" for item in validation["issues"]):
        raise ValueError("The asset contains invalid geometry. Repair it before exporting.")
    preset = command["preset"]
    directory = out / "package"
    directory.mkdir()
    stem = command["slug"]
    colliders = [obj for obj in bpy.context.scene.objects if obj.get("soceth_collider")]
    scale_world(base + colliders, preset["scale"])
    source_count = validation["triangles"]
    lods, copies, levels, lod_warnings = [], [], [], []
    material_index = {mat.name: mat for obj in base for mat in obj.data.materials if mat}
    source_names = [obj.name for obj in base]
    saved = sorted(command.get("saved_lods", []), key=lambda item: item["triangles"], reverse=True)
    ratios = [int(ratio * source_count) for ratio in preset["lod_ratios"]]
    fallback_targets = [target for target in ratios if not any(
        target * .88 <= item["triangles"] <= target * 1.06 for item in saved)]
    for entry in saved:
        clones = load_saved_lod(entry, material_index, preset["scale"])
        check = report(clones, source_count, command["texture_size"])
        if check["triangles"] != entry["triangles"] or any(item["code"] == "degenerate" for item in check["issues"]):
            raise ValueError("A saved LOD failed export validation. Generate it again from the main revision.")
        levels.append((check["triangles"], clones, check, entry["revision"], [row["name"] for row in entry["objects"]]))
    for target in fallback_targets:
        if target < 4:
            lod_warnings.append(f"The {target:,} triangle LOD target is too small for this asset.")
            continue
        clones = []
        for obj in base:
            clone = obj.copy()
            clone.data = obj.data.copy()
            clone.parent = None
            clone.matrix_world = obj.matrix_world.copy()
            bpy.context.collection.objects.link(clone)
            clones.append(clone)
        reduction = optimization.optimize(clones, target, profile="lod", lod=True)
        check = report(clones, source_count, command["texture_size"])
        geometry_errors = [item for item in check["issues"] if item["code"] == "degenerate"]
        geometry_errors.extend(item for item in reduction["issues"] if item["level"] == "error")
        if geometry_errors or check["triangles"] >= source_count:
            for obj in clones:
                bpy.data.objects.remove(obj, do_unlink=True)
            lod_warnings.append(f"No structurally acceptable distance mesh was found for {target:,} triangles.")
            continue
        if not reduction["target_met"]:
            lod_warnings.append(f"The {target:,} triangle LOD used a safer {check['triangles']:,} triangle mesh.")
        check["optimization"] = [reduction]
        levels.append((check["triangles"], clones, check, None, source_names))
    if levels:
        for obj, name in zip(base, source_names):
            obj.name = f"{name}_LOD0"
        lods.append(dict(name="LOD0", triangles=source_count, objects=[obj.name for obj in base]))
    for count, clones, check, revision, names in sorted(levels, key=lambda item: item[0], reverse=True):
        if len(lods) >= 5 or count >= lods[-1]["triangles"]:
            for obj in clones:
                bpy.data.objects.remove(obj, do_unlink=True)
            continue
        name = f"LOD{len(lods)}"
        for obj, original_name in zip(clones, names):
            obj.name = f"{original_name}_{name}"
        lods.append(dict(name=name, triangles=count, vertices=check["vertices"], objects=[obj.name for obj in clones],
                         validation=check, revision=revision))
        copies.extend(clones)
    if not colliders and preset["collider"] != "none":
        colliders.append(operations.collider(base, preset["collider"]))
    formats = ("fbx", "glb") if preset["format"] == "unity" else (preset["format"],)
    for ext in formats:
        scene.export_geometry(base + copies + colliders, directory / f"{stem}.{ext}")
    # Unity's FBX importer needs the external images even when the GLB packs them.
    textures = materials.save_textures(base, directory / "Textures") if (
        preset["external_textures"] or preset["format"] == "unity") else {}
    unity_materials = []
    for surface in validation["materials"]:
        mapping = [dict(kind=kind, path=textures[entry["image"]], channel=entry["channel"])
                   for kind, entry in surface["maps"].items() if entry["image"] in textures]
        unity_materials.append({**surface, "maps": mapping})
    manifest = dict(schema_version=1, asset=stem, files=[f"{stem}.{ext}" for ext in formats],
        axis="Y-up", unit="meter", scale=preset["scale"], materials=validation["materials"], textures=textures,
        art_bible=command.get("approval"),
        lods=lods, lod_warnings=lod_warnings, unity_materials=unity_materials, bounds=[list(value) for value in scene.bounds(base)],
        pivots=[dict(name=obj.name, blender_position=list(obj.matrix_world.translation)) for obj in base],
        colliders=[dict(name=obj.name, shape=obj["soceth_collider"], bounds=[list(v) for v in scene.bounds([obj])]) for obj in colliders])
    (directory / "soceth-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (directory / "validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    return manifest


def run(command):
    if command.get("protocol_version") != 1:
        raise ValueError("Unsupported Blender protocol.")
    out = Path(command["output"])
    kind = command["kind"]
    if kind == "capture":
        active = bpy.context.scene
        if active.get("soceth_live_asset") != command["asset"] or active.get("soceth_live_project") != command["project"]:
            raise ValueError("Open this asset from Soceth before capturing the Blender scene.")
        if active.get("soceth_live_variant") in {"lod", "trial"}:
            raise ValueError("Open the current main revision before capturing a Blender scene.")
    else:
        scene.import_scene(Path(command["source"]))
    if kind == "open":
        return {"opened": True}
    if not bpy.app.background:
        window = next(iter(bpy.context.window_manager.windows), None)
        if window:
            area = next((item for item in window.screen.areas if item.type == "VIEW_3D"), None)
            region = next((item for item in area.regions if item.type == "WINDOW"), None) if area else None
            with bpy.context.temp_override(window=window, area=area, region=region):
                return process_scene(command, out, kind)
    return process_scene(command, out, kind)


def process_scene(command, out, kind):
    scene.assign_ids()
    objects = scene.meshes()
    for obj in objects:
        if obj.data.users > 1:
            obj.data = obj.data.copy()
    active = bpy.context.scene
    active.unit_settings.system = "METRIC"
    active.unit_settings.scale_length = 1
    active.render.image_settings.file_format = "PNG"
    reductions = operations.edit(command["operations"], command) if kind == "edit" else []
    if kind == "import" and command.get("meters_per_unit", 1) != 1:
        scale_world(list(active.objects), command["meters_per_unit"])
    target = command.get("target_triangles", command.get("triangles"))
    lod = command.get("variant") == "lod"
    if kind in {"optimize", "candidate"} and target:
        reductions.append(optimization.optimize(objects, target, profile=command.get("profile", "balanced"),
            lod=lod, project_budget=command.get("triangles"), source_triangles=command.get("source_triangles")))
    if kind == "cleanup":
        reductions.append(optimization.optimize(objects, profile=command.get("profile", "preserve_structure"),
            cleanup=True, remove_fragments=command.get("remove_fragments", True), planar=command.get("planar", True)))
    failed = any(item["rating"] == "failed" for item in reductions)
    detached = any(item["detached"] for item in reductions)
    if kind == "optimize" and command.get("bake") and not (failed or detached):
        materials.bake(objects, command["texture_size"])
    objects = scene.meshes()
    # Capture the baseline before any palette changes or texture-budget processing.
    art_bible = command.get("bible")
    regions = command.get("regions", [])
    if kind in {"candidate", "palette_apply", "palette_preview"}:
        action = "apply"
    elif kind == "palette_restore":
        action = "restore"
    else:
        action = "validate"
    compliance = palette.process(objects, art_bible, regions, action, out) if art_bible else None
    if kind in {"candidate", "optimize", "palette_apply", "palette_preview"} and not (lod or failed):
        materials.resize(objects, command["texture_size"])
        if compliance:
            after = palette.process(objects, art_bible, regions, "validate", out)
            compliance = {**after, "adaptation": compliance["adaptation"],
                          "issues": list({(issue["code"], issue["message"]): issue
                                          for issue in compliance["issues"] + after["issues"]}.values())}
    validation = report(objects, command.get("triangles"), command["texture_size"], command.get("vertex_budget"))
    if compliance:
        compliance["bible_version"] = command["bible_version"]
        compliance["geometry_style"] = art_bible["geometry"]
        compliance["geometry_budget"] = command.get("triangles")
        compliance["geometry_conformity"] = ("fail" if any(issue["code"] in {"triangle_budget", "vertex_budget"}
                                                            for issue in validation["issues"]) else
                                              "review" if command.get("triangles") is None else "pass")
        compliance["texture_conformity"] = (
            "fail" if any(issue["code"] in {"texture_budget", "missing_texture"} for issue in validation["issues"]) else
            "review" if any(issue["code"] in {"texture_style", "albedo_review", "albedo_unmeasured", "shader_review"}
                            for issue in compliance["issues"]) else "pass")
        compliance["palette_conformity"] = ("reference" if compliance["policy"] == "reference" else
                                             "review" if compliance["issues"] else "pass")
        validation["art_bible"] = compliance
    validation["optimization"] = reductions
    if reductions:
        validation["structural_quality"] = reductions[-1]
    elif kind in {"validate", "approve", "export", "palette_preview", "palette_apply", "palette_restore"}:
        validation["structural_quality"] = command.get("structural_quality")
    quality_issues = command.get("quality_issues", []) + [issue for reduction in reductions for issue in reduction["issues"]]
    validation["quality_issues"] = quality_issues
    validation["issues"].extend(quality_issues)
    if art_bible and command.get("triangles") is None:
        validation["issues"].append(dict(level="warning", code="undefined_budget",
                                        message="This asset category has no Art Bible triangle budget."))
    validation["valid"] = not any(item["level"] == "error" for item in validation["issues"])
    validation["review_required"] = (any(item["level"] in {"warning", "error"} for item in validation["issues"])
                                     or bool(compliance and compliance["issues"]))
    if kind == "export":
        return {"report": validation, "manifest": export_package(objects, out, command, validation)}
    if kind not in {"validate", "approve"} and not failed and not command.get("source_reconstruction"):
        invalid = next((item for item in validation["issues"] if item["code"] == "degenerate"), None)
        if invalid:
            raise ValueError(f"{invalid['message']} Repair the mesh or try a higher target. The saved source is unchanged.")
    unchanged = ((kind in {"optimize", "cleanup"} or kind == "edit" and lod) and not command.get("bake") and
                 not any(item["changed"] for item in reductions) and
                 (lod or detached or not (command.get("resize_needed") or command.get("promote"))))
    unchanged = unchanged or failed or (lod and not any(item["changed"] for item in reductions))
    if kind not in {"validate", "approve", "palette_preview"} and not unchanged:
        scene.save_scene(out / "working.blend")
    if kind not in {"validate", "approve"} and not unchanged:
        scene.export_geometry(objects, out / "preview.glb")
    textures = materials.save_textures(objects, out / "Textures") if kind != "approve" and not unchanged else {}
    return {"report": validation, "textures": textures, "unchanged": unchanged, "quality_failed": failed,
            "variant": "lod" if lod else "trial" if detached else "main"}


def run_to_file(command):
    path = Path(command["output"]) / "blender-result.json"
    try:
        result = {"ok": True, **run(command)}
        encoded = json.dumps(result, allow_nan=False)
    except Exception as error:
        traceback.print_exc()
        result = {"ok": False, "error": str(error)}
        encoded = json.dumps(result)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)
    return result

