import math

import bpy
from mathutils import Vector

from . import optimization, scene
from .materials import edit_material


def handles(objects, operation):
    low, high = scene.bounds(objects)
    dim = high - low
    axis = 0 if operation["axis"] == "X" else 1
    cross = 1 - axis
    depth = max(dim[axis] * 0.07, 0.015)
    thickness = max(dim[cross] * operation["thickness"], 0.005)
    width = max(dim[cross] * 0.35, thickness * 3)
    for side in (-1, 1):
        face = high[axis] if side > 0 else low[axis]
        grip = (low + high) / 2
        grip[axis] = face + depth * side
        grip.z = low.z + dim.z * 0.65
        size = [thickness] * 3
        size[cross] = width
        scene.primitive("box", f"Handle_{side}_Grip", size, grip)
        size[cross], size[axis] = thickness, depth + thickness
        for bracket in (-1, 1):
            foot = grip.copy()
            foot[axis] -= side * depth / 2
            foot[cross] += bracket * (width - thickness) / 2
            scene.primitive("box", f"Handle_{side}_Mount", size, foot)


def collider(objects, shape):
    low, high = scene.bounds(objects)
    dims = [max(float(value), 0.001) for value in high - low]
    center = (high + low) / 2
    name = f"COL_{shape.upper()}_{objects[0].name}"
    if shape == "mesh":
        copies = []
        for source in objects:
            obj = source.copy()
            obj.data = source.data.copy()
            obj.parent = None
            obj.matrix_world = source.matrix_world.copy()
            bpy.context.collection.objects.link(obj)
            scene.modifiers(obj)
            copies.append(obj)
        scene.select(copies)
        if len(copies) > 1:
            bpy.ops.object.join()
        obj = bpy.context.view_layer.objects.active
        obj.data.materials.clear()
        obj.name = name
    else:
        if shape == "sphere":
            dims = [max(dims)] * 3
        if shape == "capsule":
            radius = max(dims[:2]) / 2
            height = max(dims[2], radius * 2)
            dims = [radius * 2] * 3
        obj = scene.primitive("box" if shape == "box" else "sphere", name, dims, center)
        if shape == "capsule":
            bpy.context.view_layer.update()
            for vertex in obj.data.vertices:
                if abs(vertex.co.z) > 1e-7:
                    vertex.co.z += math.copysign((height - 2 * radius) / obj.scale.z / 2, vertex.co.z)
    obj["soceth_collider"] = shape
    obj.display_type = "WIRE"
    bpy.context.view_layer.update()
    return obj


def edit_one(obj, kind, op):
    if kind in {"location", "rotation", "scale"}:
        prop = "rotation_euler" if kind == "rotation" else kind
        values = [math.radians(value) for value in op["values"]] if kind == "rotation" else op["values"]
        if op.get("relative"):
            values = [old * new if kind == "scale" else old + new for old, new in zip(getattr(obj, prop), values)]
        setattr(obj, prop, values)
    elif kind == "dimensions":
        bpy.context.view_layer.update()
        obj.dimensions = [value * factor for value, factor in zip(obj.dimensions, op["multipliers"])]
    elif kind == "apply_transforms":
        scene.select([obj])
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    elif kind == "rename":
        obj.name = op["name"]
    elif kind == "pivot":
        scene.pivot(obj, op["mode"])
    elif kind == "bevel":
        mod = obj.modifiers.new("Soceth Bevel", "BEVEL")
        mod.width, mod.segments = op["width"], op["segments"]
    elif kind == "subdivision":
        mod = next((item for item in obj.modifiers if item.type == "SUBSURF"), None)
        mod = mod or obj.modifiers.new("Soceth Subdivision", "SUBSURF")
        mod.levels = mod.render_levels = op["levels"]
    elif kind == "normals":
        scene.normals(obj)
    elif kind in {"uv", "separate"}:
        scene.mesh_operator(obj, kind)
    elif kind == "primitive":
        low, high = scene.bounds([obj])
        scene.primitive(op["shape"], op["name"], op["dimensions"],
                        (low + high) / 2 + Vector(op["offset"]), op["rotation"])
    elif kind != "inspect":
        raise ValueError(f"Unsupported operation: {kind}")


def edit(operations, command):
    reductions = []
    for op in operations:
        kind = op["type"]
        index = {obj[scene.ID]: obj for obj in scene.meshes()}
        if set(op["targets"]) - index.keys():
            raise ValueError("An operation targets a deleted or unknown object.")
        objects = [index[key] for key in dict.fromkeys(op["targets"])]
        if kind == "join":
            if len(objects) > 1:
                scene.select(objects)
                bpy.ops.object.join()
        elif kind == "handles":
            handles(objects, op)
        elif kind == "collider":
            collider(objects, op["shape"])
        elif kind in {"decimate_mesh", "decimate"}:
            reductions.append(optimization.optimize(objects, op.get("target_triangles") or op.get("triangles"),
                op.get("ratio"), op.get("preserve_uvs", True), profile=op.get("profile", "balanced"),
                lod=command.get("variant") == "lod", project_budget=command.get("triangles"),
                source_triangles=command.get("source_triangles")))
            if reductions[-1]["rating"] == "failed" or reductions[-1]["detached"]:
                break
        elif kind == "cleanup_mesh":
            reductions.append(optimization.optimize(objects, preserve_uvs=op.get("preserve_uvs", True),
                profile=op.get("profile", "preserve_structure"), cleanup=True,
                remove_fragments=op.get("remove_fragments", True), planar=op.get("planar", True)))
            if reductions[-1]["rating"] == "failed":
                break
        elif kind == "material":
            matching = [edit_material(obj, op) for obj in objects]
            if not any(matching):
                raise ValueError(f"No material matches {op.get('match')!r}.")
        else:
            for obj in objects:
                edit_one(obj, kind, op)
        scene.assign_ids()
    return reductions

