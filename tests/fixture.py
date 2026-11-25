import sys
from pathlib import Path

import bpy

destination = Path(sys.argv[-1])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=24, radius=1)
obj = bpy.context.object
obj.name = "QA_Stone"
mat = bpy.data.materials.new("QA_Copper")
mat.use_nodes = True
node = mat.node_tree.nodes.get("Principled BSDF")
node.inputs["Base Color"].default_value = (0.64, 0.21, 0.08, 1)
node.inputs["Metallic"].default_value = 0.55
node.inputs["Roughness"].default_value = 0.34
obj.data.materials.append(mat)
bpy.ops.export_scene.gltf(filepath=str(destination), export_format="GLB")

