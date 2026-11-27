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

