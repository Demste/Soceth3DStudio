import re

import bpy

from .scene import mesh_operator, select

CHANNELS = {"base_color": "Base Color", "metallic": "Metallic", "roughness": "Roughness",
            "normal": "Normal", "emission": "Emission Color", "alpha": "Alpha"}


def shader(material):
    material.use_nodes = True
    return next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)


def edit_material(obj, params):
    match = params.get("match", "").lower().strip()
    if not len(obj.data.materials) and not match:
        obj.data.materials.append(bpy.data.materials.new(params.get("name") or "Soceth Material"))
    changed = False
    for i, original in enumerate(obj.data.materials):
        if match and (original is None or match not in original.name.lower()):
            continue
        material = original.copy() if original else bpy.data.materials.new("Soceth Material")
        obj.data.materials[i] = material
        node = shader(material)
        if node is None:
            raise ValueError(f"{material.name} has no Principled BSDF.")
        changed = True
        if params.get("name"):
            material.name = params["name"]
        for key, label in {"color": "Base Color", "metallic": "Metallic", "roughness": "Roughness"}.items():
            value = params.get(key)
            if value is None:
                continue
            if key == "color":
                srgb = [int(value[j:j+2], 16) / 255 for j in (1, 3, 5)]
                value = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055)**2.4 for v in srgb] + [1]
                material.diffuse_color = value
            socket = node.inputs[label]
            for connection in list(socket.links):
                material.node_tree.links.remove(connection)
            socket.default_value = value
    return changed


def image_nodes(material):
    if not material.use_nodes:
        return set()
    connected = set()
    stack = [node for node in material.node_tree.nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output]
    while stack:
        node = stack.pop()
        if node not in connected:
            connected.add(node)
            stack.extend(link.from_node for socket in node.inputs for link in socket.links)
    return {node for node in connected if node.type == "TEX_IMAGE" and node.image}


def images_for(material):
    return {node.image for node in image_nodes(material)}


def texture_images(objects):
    return {image for obj in objects for mat in obj.data.materials if mat for image in images_for(mat)}


def describe(material):
    result = {"name": material.name, "base_color": list(material.diffuse_color), "maps": {}}
    if not material.use_nodes:
        return result
    node = shader(material)
    if node is None:
        return result
    for label, slot in CHANNELS.items():
        socket = node.inputs[slot]
        value = socket.default_value
        result[label] = list(value) if hasattr(value, "__len__") else float(value)
        if socket.links:
            link = socket.links[0]
            source, channel = link.from_node, link.from_socket.name
            if source.type in {"NORMAL_MAP", "SEPARATE_COLOR", "SEPRGB"}:
                color = source.inputs.get("Color")
                source = color.links[0].from_node if color and color.links else source
            if source.type == "TEX_IMAGE" and source.image:
                result["maps"][label] = {"image": source.image.name, "channel": channel}
    emission_strength = node.inputs["Emission Strength"].default_value
    if "emission" in result["maps"]:
        result["emission"] = [emission_strength] * 3 + [1.0]
    else:
        result["emission"] = [value * emission_strength for value in result["emission"][:3]] + [result["emission"][3]]
    return result


def resize(objects, maximum):
    slots = {node for obj in objects for material in obj.data.materials if material
             for node in image_nodes(material)}
    for image in {node.image for node in slots}:
        width, height = image.size
        if max(width, height) > maximum:
            ratio = maximum / max(width, height)
            active = image
            if image.use_fake_user:
                # Keep the original-resolution palette baseline recoverable.
                active = image.copy()
                active.use_fake_user = False
                for node in slots:
                    if node.image == image:
                        node.image = active
            active.scale(max(1, round(width * ratio)), max(1, round(height * ratio)))
            active.pack()


def bake(objects, resolution):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    for obj in objects:
        if not obj.data.uv_layers:
            mesh_operator(obj, "uv")
        if not len(obj.data.materials):
            obj.data.materials.append(bpy.data.materials.new("Baked material"))
        image = bpy.data.images.new(f"{obj.name}_Albedo", resolution, resolution, alpha=True)
        remaps = []
        try:
            for index, original in enumerate(obj.data.materials):
                material = original.copy() if original else bpy.data.materials.new("Baked material")
                obj.data.materials[index] = material
                node = shader(material)
                if node is None:
                    raise ValueError("Base-color bake requires Principled BSDF materials.")
                tree = material.node_tree
                output = next((item for item in tree.nodes if item.type == "OUTPUT_MATERIAL" and item.is_active_output), None)
                if not output or not output.inputs["Surface"].links:
                    raise ValueError("Base-color bake requires a connected material output.")
                socket = output.inputs["Surface"]
                previous = socket.links[0].from_socket
                emission = tree.nodes.new("ShaderNodeEmission")
                base_color = node.inputs["Base Color"]
                if base_color.links:
                    tree.links.new(base_color.links[0].from_socket, emission.inputs["Color"])
                else:
                    emission.inputs["Color"].default_value = base_color.default_value
                tree.links.new(emission.outputs[0], socket)
                texture = tree.nodes.new("ShaderNodeTexImage")
                texture.image = image
                tree.nodes.active = texture
                remaps.append((tree, socket, previous, emission, texture, base_color))
            select([obj])
            if "FINISHED" not in bpy.ops.object.bake(type="EMIT", margin=8):
                raise ValueError(f"Cycles did not finish baking {obj.name}.")
            image.pack()
        finally:
            for tree, socket, previous, emission, texture, base_color in remaps:
                tree.links.new(previous, socket)
                tree.nodes.remove(emission)
        for tree, _, _, _, texture, base_color in remaps:
            tree.links.new(texture.outputs["Color"], base_color)


def save_textures(objects, dest):
    entries = {}
    dest.mkdir(exist_ok=True)
    for index, image in enumerate(sorted(texture_images(objects), key=lambda value: value.name)):
        stem = re.sub(r"[^a-zA-Z0-9_-]", "_", image.name)[:75]
        raw = image.packed_file.data if image.packed_file else b""
        ext = "jpg" if raw.startswith(b"\xff\xd8") else "png"
        path = dest / f"{index:03d}_{stem}.{ext}"
        if raw.startswith((b"\xff\xd8", b"\x89PNG")):
            path.write_bytes(raw)
        else:
            image.save(filepath=str(path))
        entries[image.name] = f"Textures/{path.name}"
    return entries

