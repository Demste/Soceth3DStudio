import math
import re
import uuid
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

ID = "soceth_id"


def meshes():
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and not obj.get("soceth_collider")]


def assign_ids():
    seen = set()
    for obj in meshes():
        key = obj.get(ID, "")
        if not re.fullmatch("[a-f0-9]{32}", key) or key in seen:
            key = uuid.uuid4().hex
            obj[ID] = key
        seen.add(key)


def select(objects):
    view_layer = bpy.context.view_layer
    active = view_layer.objects.active
    if active and active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in view_layer.objects:
        if obj.select_get():
            obj.select_set(False)
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    view_layer.objects.active = objects[0]


def bounds(objects):
    bpy.context.view_layer.update()
    points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    if not points:
        raise ValueError("The scene has no renderable mesh.")
    low = Vector([min(point[i] for point in points) for i in range(3)])
    high = Vector([max(point[i] for point in points) for i in range(3)])
    if not all(math.isfinite(value) for value in (*low, *high)):
        raise ValueError("Mesh contains non-finite bounds.")
    return low, high


def import_scene(path):
    if path.suffix.lower() == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if path.suffix.lower() == ".fbx":
            bpy.ops.import_scene.fbx(filepath=str(path))
        elif path.suffix.lower() in {".glb", ".gltf"}:
            bpy.ops.import_scene.gltf(filepath=str(path))
        else:
            raise ValueError("Import a GLB, self-contained GLTF or FBX file.")
    bpy.context.preferences.filepaths.use_scripts_auto_execute = False


def save_scene(path):
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, compress=True)


def export_geometry(objects, path):
    select(objects)
    if path.suffix.lower() == ".fbx":
        bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True, object_types={"MESH"},
                                 apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS",
                                 axis_forward="-Z", axis_up="Y", bake_anim=False, path_mode="STRIP")
    else:
        bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLTF_SEPARATE" if path.suffix == ".gltf" else "GLB",
                                 use_selection=True, export_yup=True)
    if not path.is_file() or path.stat().st_size < 32:
        raise ValueError(f"Blender produced no usable {path.suffix} file.")


def triangle_count(data):
    data.calc_loop_triangles()
    return len(data.loop_triangles)


def evaluated_count(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    geom = evaluated.to_mesh()
    try:
        return triangle_count(geom)
    finally:
        evaluated.to_mesh_clear()


def modifiers(obj):
    select([obj])
    for mod in list(obj.modifiers):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def mesh_quality(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    data = evaluated.to_mesh()
    geom = bmesh.new()
    try:
        geom.from_mesh(data)
        triangles = triangle_count(data)
        used = {data.materials[face.material_index].name for face in data.polygons
                if face.material_index < len(data.materials) and data.materials[face.material_index]}
        uv_degenerate = {}
        for layer in data.uv_layers:
            uv = layer.uv
            uv_degenerate[layer.name] = sum(
                abs((uv[b].vector - uv[a].vector).cross(uv[c].vector - uv[a].vector)) < 1e-10
                for a, b, c in (triangle.loops for triangle in data.loop_triangles)
            ) / max(triangles, 1)
        return {"triangles": triangles, "uv": uv_degenerate, "materials": used,
                "open_edges": sum(not edge.is_manifold for edge in geom.edges),
                "seams": sum(edge.use_seam for edge in data.edges)}
    finally:
        geom.free()
        evaluated.to_mesh_clear()


def normals(obj):
    geom = bmesh.new()
    try:
        geom.from_mesh(obj.data)
        bmesh.ops.recalc_face_normals(geom, faces=list(geom.faces))
        geom.to_mesh(obj.data)
        obj.data.update()
    finally:
        geom.free()


def mesh_operator(obj, kind):
    select([obj])
    if kind == "uv" and not obj.data.uv_layers:
        obj.data.uv_layers.new(name="UVMap")
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.select_all(action="SELECT")
        if kind == "uv":
            bpy.ops.uv.smart_project(island_margin=0.02)
        else:
            bpy.ops.mesh.separate(type="LOOSE")
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def pivot(obj, mode):
    low, high = bounds([obj])
    center = (low + high) / 2
    if mode == "bottom_center":
        center.z = low.z
    elif mode == "world_origin":
        center = Vector((0, 0, 0))
    cursor = bpy.context.scene.cursor
    previous = cursor.location.copy()
    try:
        cursor.location = center
        select([obj])
        bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    finally:
        cursor.location = previous


def primitive(shape, name, dims, position, rotation=(0, 0, 0)):
    {"box": bpy.ops.mesh.primitive_cube_add, "sphere": bpy.ops.mesh.primitive_uv_sphere_add,
     "cylinder": bpy.ops.mesh.primitive_cylinder_add, "torus": bpy.ops.mesh.primitive_torus_add}[shape](location=position)
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    obj[ID] = uuid.uuid4().hex
    obj.dimensions = dims
    obj.rotation_euler = [math.radians(value) for value in rotation]
    return obj

