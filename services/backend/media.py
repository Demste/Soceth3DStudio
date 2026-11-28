import io
import json
import struct
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_LIMIT = 32 * 1024**2
MESH_LIMIT = 1024**3
Image.MAX_IMAGE_PIXELS = 25_000_000


def image_data(data, thumbnail=False):
    with Image.open(data) as image:
        if image.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("Choose a PNG, JPEG or WebP image.")
        image.load()
        if image.width < 32 or image.height < 32:
            raise ValueError("Reference images need at least 32×32 pixels.")
        image = ImageOps.exif_transpose(image)
        image.thumbnail((320, 320) if thumbnail else (2048, 2048))
        image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()


def validate_glb(path: Path):
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError("GLB header is truncated.")
        magic, version, length, json_size, json_type = struct.unpack("<4sIIII", header)
        if (magic, version, json_type) != (b"glTF", 2, 0x4E4F534A) or length != path.stat().st_size:
            raise ValueError("Invalid GLB header or file length.")
        if not 0 < json_size <= min(length - 20, 32 * 1024**2):
            raise ValueError("Invalid GLB JSON chunk length.")
        data = json.loads(stream.read(json_size))
    if any("uri" in item for key in ("images", "buffers") for item in data.get(key, [])):
        raise ValueError("GLB must embed its images and buffers.")
    primitives = [primitive for mesh in data.get("meshes", []) for primitive in mesh.get("primitives", [])
                  if "POSITION" in primitive.get("attributes", {}) and primitive.get("mode", 4) in {4, 5, 6}]
    if not primitives:
        raise ValueError("GLB contains no triangle mesh.")
    accessors = data.get("accessors", [])
    if not any(0 <= (index := primitive["attributes"]["POSITION"]) < len(accessors)
               and accessors[index].get("count", 0) >= 3 for primitive in primitives):
        raise ValueError("GLB contains no readable vertex accessor.")
    return data


def validate_textured_glb(path: Path, capability: str):
    """Verify that triangle primitives bind readable images to their UV accessors."""
    document = validate_glb(path)

    def indexed(items, key, label):
        if type(key) is not int or not 0 <= key < len(items):
            raise ValueError(f"Invalid GLB {label} reference.")
        return items[key]

    positions = document.get("accessors", [])
    views = document.get("bufferViews", [])
    buffers = document.get("buffers", [])
    maps = set()
    dimensions = set()
    images = {}
    with path.open("rb") as stream:
        stream.seek(12)
        json_size, _ = struct.unpack("<II", stream.read(8))
        stream.seek(json_size, 1)
        header = stream.read(8)
        if len(header) != 8:
            raise ValueError("Textured GLB must include its binary data.")
        bin_size, bin_type = struct.unpack("<II", header)
        bin_offset = stream.tell()
        if bin_type != 0x004E4942 or bin_offset + bin_size != path.stat().st_size or len(buffers) != 1:
            raise ValueError("Invalid embedded GLB binary data.")
        buffer_size = buffers[0].get("byteLength", 0)
        if not 0 < buffer_size <= bin_size <= buffer_size + 3:
            raise ValueError("Invalid embedded GLB buffer length.")

        def view_range(index):
            view = indexed(views, index, "buffer view")
            offset, size = view.get("byteOffset", 0), view.get("byteLength", 0)
            if view.get("buffer") != 0 or offset < 0 or size <= 0 or offset + size > buffer_size:
                raise ValueError("Invalid GLB buffer view range.")
            return bin_offset + offset, size, view

        def accessor(index, shapes, allowed, minimum):
            item = indexed(positions, index, "accessor")
            width = shapes.get(item.get("type"))
            component = item.get("componentType")
            count = item.get("count", 0)
            if not width or component not in allowed or count < minimum:
                raise ValueError("Invalid GLB geometry or UV accessor.")
            _, length, view = view_range(item.get("bufferView"))
            element = width * {5121: 1, 5123: 2, 5125: 4, 5126: 4}[component]
            start, stride = item.get("byteOffset", 0), view.get("byteStride", element)
            if start < 0 or stride < element or start + (count - 1) * stride + element > length:
                raise ValueError("GLB geometry or UV accessor exceeds its data.")
            return count

        def image_size(index):
            if index not in images:
                entry = indexed(document.get("images", []), index, "image")
                start, size, _ = view_range(entry.get("bufferView"))
                if size > 128 * 1024**2:
                    raise ValueError("Embedded GLB texture exceeds the supported image size.")
                stream.seek(start)
                with Image.open(io.BytesIO(stream.read(size))) as image:
                    formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
                    if formats.get(entry.get("mimeType")) != image.format or min(image.size) < 2:
                        raise ValueError("Invalid embedded GLB texture format or dimensions.")
                    image.load()
                    images[index] = image.size
            return images[index]

        try:
            for mesh in document["meshes"]:
                for primitive in mesh.get("primitives", []):
                    attributes = primitive.get("attributes", {})
                    if primitive.get("mode", 4) not in {4, 5, 6} or "POSITION" not in attributes:
                        continue
                    count = accessor(attributes["POSITION"], {"VEC3": 3}, {5126}, 3)
                    if "indices" in primitive:
                        accessor(primitive["indices"], {"SCALAR": 1}, {5121, 5123, 5125}, 3)
                    if "material" not in primitive:
                        continue
                    material = indexed(document.get("materials", []), primitive["material"], "material")
                    pbr = material.get("pbrMetallicRoughness", {})
                    channels = {**{name: pbr[name] for name in ("baseColorTexture", "metallicRoughnessTexture") if name in pbr},
                                **{name: material[name] for name in ("normalTexture", "occlusionTexture", "emissiveTexture")
                                   if name in material}}
                    for name, binding in channels.items():
                        uv_index = binding.get("extensions", {}).get("KHR_texture_transform", {}).get(
                            "texCoord", binding.get("texCoord", 0))
                        uv = attributes.get(f"TEXCOORD_{uv_index}")
                        if accessor(uv, {"VEC2": 2}, {5121, 5123, 5126}, count) != count:
                            raise ValueError("GLB texture UV count does not match its mesh.")
                        texture = indexed(document.get("textures", []), binding.get("index"), "texture")
                        if "sampler" in texture:
                            indexed(document.get("samplers", []), texture["sampler"], "sampler")
                        source = texture.get("source", texture.get("extensions", {}).get("EXT_texture_webp", {}).get("source"))
                        size = image_size(source)
                        maps.add(name)
                        if name == "baseColorTexture":
                            dimensions.add(size)
        except (KeyError, TypeError, AttributeError, OSError) as error:
            raise ValueError("Could not read the GLB geometry or texture bindings.") from error

    if "baseColorTexture" not in maps:
        raise ValueError("No mesh primitive has an embedded, UV-mapped base-color texture.")
    if capability == "pbr" and not {"metallicRoughnessTexture", "normalTexture"} <= maps:
        raise ValueError("The PBR provider returned no bound metallic/roughness or normal texture.")
    if capability == "albedo" and maps != {"baseColorTexture"}:
        raise ValueError("An albedo-only provider returned unexpected additional texture maps.")
    return {"maps": sorted(maps), "base_color_sizes": sorted(dimensions)}

