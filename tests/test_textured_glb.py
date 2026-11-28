import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from services.backend.media import validate_textured_glb


class TexturedGLB(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "mesh.glb"
        texture = io.BytesIO()
        Image.new("RGB", (2, 2), (142, 64, 17)).save(texture, "PNG")
        self.binary = struct.pack("<15f", 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1) + texture.getvalue()
        self.document = {
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": len(self.binary)}],
            "bufferViews": [{"buffer": 0, "byteLength": 36},
                            {"buffer": 0, "byteOffset": 36, "byteLength": 24},
                            {"buffer": 0, "byteOffset": 60, "byteLength": len(texture.getvalue())}],
            "accessors": [{"bufferView": index, "componentType": 5126, "count": 3, "type": shape}
                          for index, shape in enumerate(("VEC3", "VEC2"))],
            "images": [{"bufferView": 2, "mimeType": "image/png"}],
            "textures": [{"source": 0}],
            "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "material": 0}]}],
        }

    def write(self):
        text = json.dumps(self.document).encode()
        text += b" " * (-len(text) % 4)
        binary = self.binary + b"\0" * (-len(self.binary) % 4)
        self.path.write_bytes(struct.pack("<4sIIII", b"glTF", 2, 28 + len(text) + len(binary), len(text), 0x4E4F534A)
                              + text + struct.pack("<II", len(binary), 0x004E4942) + binary)

    def test_albedo_requires_uv_and_material_bindings(self):
        self.write()
        self.assertEqual(validate_textured_glb(self.path, "albedo"),
                         {"maps": ["baseColorTexture"], "base_color_sizes": [(2, 2)]})
        with self.assertRaisesRegex(ValueError, "metallic/roughness or normal"):
            validate_textured_glb(self.path, "pbr")
        primitive = self.document["meshes"][0]["primitives"][0]
        del primitive["attributes"]["TEXCOORD_0"]
        self.write()
        with self.assertRaisesRegex(ValueError, "accessor reference"):
            validate_textured_glb(self.path, "albedo")

    def test_unbound_or_unreadable_image_is_insufficient(self):
        material = self.document["materials"][0]
        binding = material.pop("pbrMetallicRoughness")
        self.write()
        with self.assertRaisesRegex(ValueError, "UV-mapped base-color"):
            validate_textured_glb(self.path, "albedo")
        material["pbrMetallicRoughness"] = binding
        self.document["images"][0]["bufferView"] = 0
        self.write()
        with self.assertRaisesRegex(ValueError, "geometry or texture bindings"):
            validate_textured_glb(self.path, "albedo")
