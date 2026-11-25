"""Adapt authored base color while keeping the source shader inputs recoverable."""

import json
import re
import struct
import zlib
from pathlib import Path

import bpy
import numpy as np

from . import materials

BASELINE = "soceth_palette_baseline"
TRANSFER = "soceth_palette_transfer"
LIGHT = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
LMS = np.array([[.41222147, .53633254, .05144599], [.21190350, .68069955, .10739696],
                [.08830246, .28171884, .62997870]], dtype=np.float32)
LAB = np.array([[.21045426, .79361779, -.00407205], [1.97799850, -2.42859221, .45059371],
                [.02590404, .78277177, -.80867577]], dtype=np.float32)


def linear(rgb):
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def srgb(rgb):
    rgb = np.clip(rgb, 0, 1)
    return np.where(rgb <= .0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - .055)


def oklab(rgb):
    return np.cbrt(rgb @ LMS.T) @ LAB.T


def hex_rgb(rgb):
    return "#" + "".join(f"{value:02X}" for value in np.rint(srgb(rgb) * 255).astype(int))


def swatches(colors):
    return linear(np.array([[int(color[i:i + 2], 16) for i in (1, 3, 5)]
                            for color in colors], dtype=np.float32) / 255)


def colors_for(bible, category):
    rule = bible["semantic_materials"].get(category)
    if rule is None:
        return bible["palette"] or list(dict.fromkeys(
            rule["primary_color"] for rule in bible["semantic_materials"].values()))
    variations = rule["variations"] if bible["color_policy"] == "guided" else []
    return list(dict.fromkeys([rule["primary_color"], *rule["approved_colors"], *variations]))


def active_shader(material):
    if not material.use_nodes:
        return None
    output = next((node for node in material.node_tree.nodes
                   if node.type == "OUTPUT_MATERIAL" and node.is_active_output), None)
    if output and (links := output.inputs["Surface"].links) and links[0].from_node.type == "BSDF_PRINCIPLED":
        return links[0].from_node
    return None


def base_texture(shader):
    links = shader.inputs["Base Color"].links if shader else ()
    if links and links[0].from_node.type == "TEX_IMAGE" and links[0].from_socket.name == "Color":
        return links[0].from_node
    return None


def original_node(material, texture):
    if texture and texture.get(TRANSFER):
        return material.node_tree.nodes.get(texture[TRANSFER])
    return texture


def category_for(name, bible):
    def words(label):
        return " ".join(re.findall(r"\w+", label.casefold().replace("_", " ")))
    label = f" {words(name)} "
    matches = [key for key, rule in bible["semantic_materials"].items()
               if any(words(alias) and f" {words(alias)} " in label for alias in [key, *rule["aliases"]])]
    return matches[0] if len(matches) == 1 else None


def texture_pixels(image):
    if image is None or image.source not in {"FILE", "GENERATED"}:
        raise ValueError("Base-color image is missing or uses an unsupported image source.")
    if image.colorspace_settings.name != "sRGB" or image.is_float:
        raise ValueError("Convert the base-color image to byte sRGB before adapting its palette.")
    width, height = image.size
    if not width or not height or image.channels != 4 or width * height > 4096 ** 2:
        raise ValueError("Base-color adaptation requires a readable RGBA image of at most 16 megapixels.")
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return pixels.reshape(height, width, 4)


def save_rgba(path, pixels):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    height, width = pixels.shape[:2]
    rows = b"".join(b"\x00" + row.tobytes() for row in pixels[::-1])
    path.write_bytes(b"\x89PNG\r\n\x1a\n" +
                     chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def assignments(width, height, regions):
    mask = np.zeros((height, width), dtype=np.uint8)
    for index, region in enumerate(regions, 1):
        if not region["strokes"]:
            mask.fill(index)
            continue
        for stroke in region["strokes"]:
            radius = max(1, stroke["radius"] * min(width, height))
            points = [(u * (width - 1), (1 - v) * (height - 1)) for u, v in stroke["points"]]
            for start, end in zip(points, points[1:] or points):
                # Rasterize a continuous stroke so sparse pointer events leave no gaps.
                dx, dy = end[0] - start[0], end[1] - start[1]
                length_squared = dx * dx + dy * dy
                left = max(0, int(min(start[0], end[0]) - radius))
                right = min(width, int(max(start[0], end[0]) + radius) + 1)
                bottom = max(0, int(min(start[1], end[1]) - radius))
                top = min(height, int(max(start[1], end[1]) + radius) + 1)
                y, x = np.ogrid[bottom:top, left:right]
                offset_x, offset_y = x - start[0], y - start[1]
                along = np.clip((offset_x * dx + offset_y * dy) / length_squared, 0, 1) if length_squared else 0
                inside = (offset_x - along * dx) ** 2 + (offset_y - along * dy) ** 2 <= radius ** 2
                mask[bottom:top, left:right][inside] = index
    return mask


def nearest(rgb, probes):
    lab = oklab(rgb)
    distance = np.full(len(rgb), np.inf, dtype=np.float32)
    selected = np.zeros(len(rgb), dtype=np.int32)
    for i, probe in enumerate(probes):
        squared = np.sum((lab - probe) ** 2, axis=1)
        better = squared < distance
        selected[better] = i
        distance[better] = squared[better]
    return selected, np.sqrt(distance)


def adapt_image(pixels, mask, palettes, policy, semantic_indices):
    output = np.rint(np.clip(pixels, 0, 1) * 255).astype(np.uint8)
    for index, palette in palettes.items():
        selected = (mask == index) & (pixels[..., 3] > 0)
        if not selected.any():
            continue
        rgb = linear(pixels[..., :3][selected])
        approved = swatches(palette)
        luma = rgb @ LIGHT
        # Match chromaticity at comparable lightness, then keep local shading in linear light.
        comparable = rgb * (.25 / np.maximum(luma, .012))[:, None]
        reference = approved * (.25 / np.maximum(approved @ LIGHT, .012))[:, None]
        chosen, _ = nearest(comparable, oklab(reference))
        if index in semantic_indices:
            dominant = np.bincount(chosen).argmax()
            chosen[chosen == dominant] = 0
        for color_id, anchor in enumerate(approved):
            members = chosen == color_id
            if members.any():
                neutral = max(float(np.median(luma[members])), .006)
                low, high = (.35, 1.55) if policy == "strict" else (.25, 1.8)
                shading = np.clip(luma[members] / neutral, low, min(high, 1 / max(float(anchor.max()), .01)))
                rgb[members] = anchor * shading[:, None]
        output[..., :3][selected] = np.rint(srgb(rgb) * 255).astype(np.uint8)
    return output


def observed(samples):
    if not len(samples):
        return []
    bins = np.rint(srgb(samples) * 15).astype(int)
    _, inverse, counts = np.unique(bins, axis=0, return_inverse=True, return_counts=True)
    return [hex_rgb(samples[inverse == i].mean(axis=0)) for i in np.argsort(-counts)[:6]]


def distance_check(samples, approved, textured, policy):
    if not len(samples) or not approved:
        return None, None
    colors = swatches(approved)
    shades = (np.linspace(.23, 1.85, 19) if textured else (1,))
    probes = oklab(np.clip(np.concatenate([colors * shade for shade in shades]), 0, 1))
    _, distance = nearest(samples, probes)
    threshold = .055 if policy == "strict" else .09
    return round(float(np.mean(distance <= threshold)), 3), round(float(np.median(distance)), 4)


def baseline(material, shader, texture):
    if BASELINE not in material:
        material[BASELINE] = json.dumps({key: list(shader.inputs[slot].default_value) if key == "base_color"
                                          else shader.inputs[slot].default_value
                                         for key, slot in materials.CHANNELS.items()
                                         if key in {"base_color", "roughness", "metallic"}})
        if texture and texture.image:
            texture.image.use_fake_user = True
    return json.loads(material[BASELINE])


def restore(material, shader, texture, original):
    if BASELINE not in material:
        return
    values = json.loads(material[BASELINE])
    for key, value in values.items():
        shader.inputs[materials.CHANNELS[key]].default_value = value
    material.diffuse_color = values["base_color"]
    if texture and texture.get(TRANSFER):
        if original is None or original.image is None:
            raise ValueError(f"{material.name}: the stored source texture is missing.")
        material.node_tree.links.new(original.outputs["Color"], shader.inputs["Base Color"])
        material.node_tree.nodes.remove(texture)


def install_texture(material, shader, original, texture, path):
    tree = material.node_tree
    node = tree.nodes.new("ShaderNodeTexImage")
    node[TRANSFER] = original.name
    node.label = "Art Bible base color"
    node.image = bpy.data.images.load(str(path), check_existing=False)
    node.image.colorspace_settings.name = "sRGB"
    node.image.alpha_mode = original.image.alpha_mode
    node.image.pack()
    node.extension, node.interpolation, node.projection = original.extension, original.interpolation, original.projection
    node.projection_blend = original.projection_blend
    vector = original.inputs["Vector"]
    if vector.links:
        tree.links.new(vector.links[0].from_socket, node.inputs["Vector"])
    else:
        node.inputs["Vector"].default_value = vector.default_value
    shader.inputs["Base Color"].default_value[:3] = (1, 1, 1)
    tree.links.new(node.outputs["Color"], shader.inputs["Base Color"])
    if texture and texture.get(TRANSFER):
        tree.nodes.remove(texture)


def process(objects, bible, regions, action, out):
    policy = bible["color_policy"]
    surfaces = set()
    for obj in objects:
        if action == "apply" and policy != "reference" and not any(obj.data.materials):
            material = bpy.data.materials.new(f"{obj.name} material")
            material.use_nodes = True
            if len(obj.data.materials):
                obj.data.materials[0] = material
            else:
                obj.data.materials.append(material)
        surfaces.update(mat for mat in obj.data.materials if mat)
    rows, issues, applied = [], [], []

    def warn(material, code, message):
        issues.append(dict(level="warning", code=code, message=f"{material}: {message}"))

    for region in regions:
        if region["material"] not in {mat.name for mat in surfaces}:
            warn(region["material"], "stale_region", "This saved mapping no longer matches a material.")
        elif region["category"] not in bible["semantic_materials"]:
            warn(region["material"], "stale_region", f"Category {region['category']} was removed from the Art Bible.")

    for index, material in enumerate(sorted(surfaces, key=lambda item: item.name)):
        shader = active_shader(material)
        texture = base_texture(shader)
        original = original_node(material, texture)
        if action == "restore" and shader:
            restore(material, shader, texture, original)
            texture = base_texture(shader)
        if (material.use_nodes and shader is None) or (shader and shader.inputs["Base Color"].is_linked and texture is None):
            warn(material.name, "shader_review", "Base color uses an unsupported shader graph. Review its authored appearance.")
            rows.append(dict(name=material.name, categories=[], colors=[], palette_match=None, textured=None))
            continue
        textured = texture is not None
        authored = any(region["material"] == material.name for region in regions)
        selected = [region for region in regions if region["material"] == material.name
                    and region["category"] in bible["semantic_materials"]]
        if not textured:
            if any(region["strokes"] for region in selected):
                warn(material.name, "mask_without_texture", "Brush masks require a base-color image.")
            selected = [region for region in selected if not region["strokes"]][-1:]
        alias = category_for(material.name, bible)
        if len(surfaces) == 1 and textured and len(bible["semantic_materials"]) > 1:
            alias = None
        fallback = alias or bible["default_material"]
        lookup = {0: fallback, **{i: region["category"] for i, region in enumerate(selected, 1)}}
        palettes = {key: colors_for(bible, category) for key, category in lookup.items()}
        if policy != "reference" and not any(palettes.values()):
            warn(material.name, "palette_missing", "The project has no approved colors.")
        pixels = mask = None
        apply = action == "apply" and policy != "reference" and any(palettes.values())
        try:
            if textured:
                if apply and original is None:
                    raise ValueError("The stored source texture is missing.")
                pixels = texture_pixels(original.image if apply else texture.image)
                height, width = pixels.shape[:2]
                mask = assignments(width, height, selected)
                if apply:
                    baseline(material, shader, original)
                    path = out / f"palette-{index:03d}.png"
                    save_rgba(path, adapt_image(pixels, mask, palettes, policy,
                                                {key for key, category in lookup.items() if category}))
                    install_texture(material, shader, original, texture, path)
                    texture = base_texture(shader)
                    pixels = texture_pixels(texture.image)
                    applied.append(dict(material=material.name, method="albedo_palette_transfer", texture=texture.image.name))
            elif apply:
                if not shader:
                    diffuse = tuple(material.diffuse_color)
                    shader = materials.shader(material)
                    shader.inputs["Base Color"].default_value = diffuse
                key = len(selected) or 0
                values = baseline(material, shader, None)
                approved = swatches(palettes[key])
                rgb = np.array([values["base_color"][:3]], dtype=np.float32)
                match, _ = nearest(rgb, oklab(approved))
                color = list(approved[0 if lookup[key] else match[0]]) + [values["base_color"][3]]
                shader.inputs["Base Color"].default_value = color
                material.diffuse_color = color
                applied.append(dict(material=material.name, method="authored_base_color"))
        except ValueError as error:
            warn(material.name, "albedo_review", str(error))
            pixels = mask = None

        if textured and pixels is not None:
            step = max(1, min(pixels.shape[:2]) // 88)
            sample = pixels[::step, ::step].reshape(-1, 4)
            visible = sample[:, 3] > .05
            rgb = linear(sample[visible, :3])
            labels = mask[::step, ::step].reshape(-1)[visible]
        elif not textured:
            authored_color = shader.inputs["Base Color"].default_value if shader else material.diffuse_color
            rgb = np.array([authored_color[:3]], dtype=np.float32)
            labels = np.array([len(selected)])
        else:
            rgb, labels = np.empty((0, 3)), np.empty(0)
        checks, assigned = [], set()
        for key in np.unique(labels):
            category = lookup[key]
            if category:
                assigned.add(category)
            if policy != "reference":
                match, distance = distance_check(rgb[labels == key], palettes[key], textured, policy)
                checks.append(dict(category=category, fraction=match, oklab_distance=distance,
                                   sample_fraction=round(float(np.mean(labels == key)), 3)))
        if policy != "reference":
            atlas_needs_mapping = textured and not alias and bool(len(labels) and np.any(labels == 0))
            unmapped = bool(bible["semantic_materials"] and not fallback and len(labels) and np.any(labels == 0))
            if atlas_needs_mapping or unmapped:
                warn(material.name, "semantic_review", "Confirm the semantic category or paint and assign atlas regions.")
            if textured and not len(rgb):
                warn(material.name, "albedo_unmeasured", "No usable visible base-color samples were measured.")
            if authored and textured and any(i not in labels for i in range(1, len(selected) + 1)):
                warn(material.name, "mask_unsampled", "A mask has no independently sampled visible pixels. Review coverage.")
            if any(check["fraction"] is None or check["fraction"] < (.98 if policy == "strict" else .92) for check in checks):
                warn(material.name, "palette_distance", "Authored base color falls outside the project's measured palette tolerance.")
            for category in assigned:
                rule = bible["semantic_materials"][category]
                if (rule["texture_style"] == "textured" and not textured or
                        rule["texture_style"] == "flat" and textured):
                    warn(material.name, "texture_style", f"{category} requires {rule['texture_style']} base color.")
            for channel in ("roughness", "metallic"):
                ranges = [bible["semantic_materials"][name][channel] for name in assigned]
                if not ranges or not shader:
                    continue
                low, high = max(item[0] for item in ranges), min(item[1] for item in ranges)
                socket = shader.inputs[materials.CHANNELS[channel]]
                if low > high or (socket.is_linked and (low, high) != (0, 1)):
                    warn(material.name, "pbr_review", f"Review {channel} constraints for its texture or mixed material regions.")
                elif not socket.is_linked:
                    if apply:
                        socket.default_value = max(low, min(high, socket.default_value))
                    if not low - .00001 <= socket.default_value <= high + .00001:
                        warn(material.name, "pbr_range", f"Authored {channel} is outside the assigned material range.")
        rows.append(dict(name=material.name, categories=sorted(assigned), colors=observed(rgb),
                         palette_match=checks or None, textured=textured,
                         image=texture.image.name if texture and texture.image else None,
                         original_image=original.image.name if original and original.image else None,
                         used_default=bool(fallback and not alias), requires_mapping=any(
                             issue["code"] == "semantic_review" and issue["message"].startswith(material.name + ":")
                             for issue in issues)))
    if policy != "reference" and not surfaces:
        warn("Scene", "missing_material", "The asset has no assigned material.")
    return dict(policy=policy, palette=bible["palette"], materials=rows, regions=regions,
                semantic_palette={name: colors_for(bible, name) for name in bible["semantic_materials"]},
                adaptation=applied, issues=issues)
