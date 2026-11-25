using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Soceth.Editor
{
    public static class SocethAssetImporter
    {
        [Serializable] private sealed class Manifest
        {
            public int schema_version;
            public string asset;
            public string[] files;
            public LodInfo[] lods;
            public ColliderInfo[] colliders;
            public Surface[] unity_materials;
        }

        [Serializable] private sealed class LodInfo { public string name; public int triangles; public string[] objects; }
        [Serializable] private sealed class ColliderInfo { public string name; public string shape; }
        [Serializable] private sealed class Map { public string kind; public string path; public string channel; }
        [Serializable] private sealed class Surface
        {
            public string name;
            public float[] base_color;
            public float metallic;
            public float roughness;
            public float[] emission;
            public Map[] maps;
        }

        [MenuItem("Assets/Soceth/Build Unity prefab from manifest", true)]
        private static bool CanBuild()
        {
            return AssetDatabase.GetAssetPath(Selection.activeObject).EndsWith("/soceth-manifest.json", StringComparison.Ordinal);
        }

        [MenuItem("Assets/Soceth/Build Unity prefab from manifest")]
        private static void BuildSelection()
        {
            var result = Build(AssetDatabase.GetAssetPath(Selection.activeObject));
            if (result != null) Selection.activeObject = result;
        }

        public static GameObject Build(string manifestPath)
        {
            if (!manifestPath.StartsWith("Assets/", StringComparison.Ordinal))
                throw new ArgumentException("Import the Soceth package beneath Assets.");
            var config = JsonUtility.FromJson<Manifest>(File.ReadAllText(manifestPath));
            if (config == null || config.schema_version != 1) throw new InvalidDataException("Unsupported Soceth manifest.");
            string folder = Path.GetDirectoryName(manifestPath).Replace('\\', '/');
            string modelFile = config.files.FirstOrDefault(path => path.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase));
            if (modelFile == null) throw new InvalidDataException("This helper requires the Unity/FBX export preset.");
            var source = AssetDatabase.LoadAssetAtPath<GameObject>(Child(folder, modelFile));
            if (source == null) throw new InvalidDataException("Unity could not import the FBX.");
            var studioShader = Shader.Find("Universal Render Pipeline/Lit");
            if (studioShader == null) throw new InvalidOperationException("Activate a Unity URP pipeline before importing this package.");
            var root = (GameObject)PrefabUtility.InstantiatePrefab(source);
            try
            {
                PrefabUtility.UnpackPrefabInstance(root, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
                root.name = Path.GetFileName(config.asset);
                var index = root.GetComponentsInChildren<Transform>(true).GroupBy(node => node.name)
                    .ToDictionary(group => group.Key, group => group.ToArray());
                foreach (var item in config.colliders ?? Array.Empty<ColliderInfo>())
                    ConfigureCollider(Find(index, item.name), item.shape);
                var surfaces = new Dictionary<string, Material>();
                var materials = config.unity_materials ?? Array.Empty<Surface>();
                for (int i = 0; i < materials.Length; i++)
                    surfaces[materials[i].name] = BuildMaterial(folder, studioShader, materials[i], i);
                foreach (var renderer in root.GetComponentsInChildren<Renderer>(true))
                    renderer.sharedMaterials = renderer.sharedMaterials.Select(item =>
                        item != null && surfaces.TryGetValue(item.name, out var mapped) ? mapped : item).ToArray();
                var lods = config.lods ?? Array.Empty<LodInfo>();
                if (lods.Length > 0)
                {
                    var group = root.GetComponent<LODGroup>();
                    if (!group) group = root.AddComponent<LODGroup>();
                    foreach (var importedGroup in root.GetComponentsInChildren<LODGroup>())
                        if (importedGroup != group) UnityEngine.Object.DestroyImmediate(importedGroup);
                    group.SetLODs(lods.Select((lod, i) =>
                    {
                        var parts = lod.objects.SelectMany(name => Find(index, name).GetComponentsInChildren<Renderer>()).Distinct().ToArray();
                        if (parts.Length == 0) throw new InvalidDataException($"{lod.name} has no renderers.");
                        return new LOD(0.65f * Mathf.Pow(0.42f, i), parts);
                    }).ToArray());
                    group.RecalculateBounds();
                }
                if (!root.GetComponentsInChildren<MeshFilter>().Any(item => item.sharedMesh != null && item.sharedMesh.vertexCount > 2))
                    throw new InvalidDataException("Imported geometry is empty.");
                var prefab = PrefabUtility.SaveAsPrefabAsset(root, Child(folder, root.name + ".prefab"));
                AssetDatabase.SaveAssets();
                return prefab;
            }
            finally { UnityEngine.Object.DestroyImmediate(root); }
        }

        private static Transform Find(Dictionary<string, Transform[]> index, string name)
        {
            if (!index.TryGetValue(name, out var matches) || matches.Length != 1)
                throw new InvalidDataException($"The FBX must contain exactly one object named {name}.");
            return matches[0];
        }

        private static string Child(string directory, string path)
        {
            string full = Path.GetFullPath(Path.Combine(directory, path));
            string root = Path.GetFullPath(directory) + Path.DirectorySeparatorChar;
            if (!full.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Manifest path is outside the asset folder.");
            return Path.GetRelativePath(Directory.GetCurrentDirectory(), full).Replace('\\', '/');
        }

        private static void ConfigureCollider(Transform item, string shape)
        {
            var filter = item.GetComponent<MeshFilter>();
            var mesh = filter ? filter.sharedMesh : null;
            if (mesh == null) throw new InvalidDataException($"{item.name} does not contain collider geometry.");
            var bounds = mesh.bounds;
            if (shape == "box")
            {
                var box = item.gameObject.AddComponent<BoxCollider>();
                box.center = bounds.center;
                box.size = bounds.size;
            }
            else if (shape == "sphere")
            {
                var sphere = item.gameObject.AddComponent<SphereCollider>();
                sphere.center = bounds.center;
                sphere.radius = Mathf.Max(bounds.extents.x, bounds.extents.y, bounds.extents.z);
            }
            else if (shape == "capsule")
            {
                var capsule = item.gameObject.AddComponent<CapsuleCollider>();
                capsule.center = bounds.center;
                capsule.direction = 1;
                capsule.radius = Mathf.Max(bounds.extents.x, bounds.extents.z);
                capsule.height = Mathf.Max(bounds.size.y, capsule.radius * 2);
            }
            else if (shape == "mesh") item.gameObject.AddComponent<MeshCollider>().sharedMesh = mesh;
            else throw new InvalidDataException($"Unknown collider shape: {shape}.");
            foreach (var renderer in item.GetComponents<Renderer>())
                UnityEngine.Object.DestroyImmediate(renderer);
        }

        private static Material BuildMaterial(string folder, Shader shader, Surface surface, int index)
        {
            string path = Child(folder, $"URP_Material_{index:000}.mat");
            var material = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (!material)
            {
                material = new Material(shader);
                AssetDatabase.CreateAsset(material, path);
            }
            material.shader = shader;
            material.SetColor("_BaseColor", ColorFrom(surface.base_color, Color.white));
            material.SetFloat("_Metallic", surface.metallic);
            material.SetFloat("_Smoothness", 1 - surface.roughness);
            var maps = (surface.maps ?? Array.Empty<Map>()).ToDictionary(map => map.kind);
            SetMap(material, folder, maps, "base_color", "_BaseMap", null, TextureImporterType.Default, true);
            SetMap(material, folder, maps, "normal", "_BumpMap", "_NORMALMAP", TextureImporterType.NormalMap, false);
            SetMap(material, folder, maps, "emission", "_EmissionMap", null, TextureImporterType.Default, true);
            var emission = ColorFrom(surface.emission, Color.black);
            material.SetColor("_EmissionColor", emission);
            if (emission.maxColorComponent > 0) material.EnableKeyword("_EMISSION"); else material.DisableKeyword("_EMISSION");
            var packed = PackMetallicSmoothness(folder, maps, surface, index);
            material.SetTexture("_MetallicGlossMap", packed);
            if (packed) { material.EnableKeyword("_METALLICSPECGLOSSMAP"); material.SetFloat("_Smoothness", 1f); }
            else material.DisableKeyword("_METALLICSPECGLOSSMAP");
            EditorUtility.SetDirty(material);
            return material;
        }

        private static Color ColorFrom(float[] channels, Color fallback)
        {
            return channels != null && channels.Length >= 3
                ? new Color(channels[0], channels[1], channels[2], channels.Length > 3 ? channels[3] : 1)
                : fallback;
        }

        private static void SetMap(Material material, string folder, Dictionary<string, Map> maps, string kind,
                                   string property, string keyword, TextureImporterType type, bool srgb)
        {
            Texture2D texture = maps.TryGetValue(kind, out var map) ? LoadTexture(folder, map.path, type, srgb, false) : null;
            material.SetTexture(property, texture);
            if (keyword == null) return;
            if (texture) material.EnableKeyword(keyword); else material.DisableKeyword(keyword);
        }

        private static Texture2D LoadTexture(string folder, string path, TextureImporterType type, bool srgb, bool readable)
        {
            string asset = Child(folder, path);
            var importer = AssetImporter.GetAtPath(asset) as TextureImporter;
            if (importer == null) throw new FileNotFoundException("Missing Unity texture.", asset);
            if (importer.textureType != type || importer.sRGBTexture != srgb || importer.isReadable != readable)
            {
                importer.textureType = type;
                importer.sRGBTexture = srgb;
                importer.isReadable = readable;
                importer.SaveAndReimport();
            }
            return AssetDatabase.LoadAssetAtPath<Texture2D>(asset);
        }

        private static float Sample(Texture2D texture, Map map, float x, float y, float fallback)
        {
            if (!texture) return fallback;
            Color value = texture.GetPixelBilinear(x, y);
            return map.channel switch { "Green" or "G" => value.g, "Blue" or "B" => value.b, "Alpha" or "A" => value.a, _ => value.r };
        }

        private static Texture2D PackMetallicSmoothness(string folder, Dictionary<string, Map> maps, Surface surface, int i)
        {
            maps.TryGetValue("metallic", out var metallic);
            maps.TryGetValue("roughness", out var roughness);
            if (metallic == null && roughness == null) return null;
            var metal = metallic == null ? null : LoadTexture(folder, metallic.path, TextureImporterType.Default, false, true);
            var rough = roughness == null ? null : LoadTexture(folder, roughness.path, TextureImporterType.Default, false, true);
            int width = Mathf.Max(metal ? metal.width : 1, rough ? rough.width : 1);
            int height = Mathf.Max(metal ? metal.height : 1, rough ? rough.height : 1);
            var packed = new Texture2D(width, height, TextureFormat.RGBA32, false, true);
            string path = Child(folder, $"URP_MetallicSmoothness_{i:000}.png");
            try
            {
                var pixels = new Color[width * height];
                for (int y = 0; y < height; y++)
                    for (int x = 0; x < width; x++)
                    {
                        float u = (x + 0.5f) / width, v = (y + 0.5f) / height;
                        pixels[y * width + x] = new Color(Sample(metal, metallic, u, v, surface.metallic),
                            0, 0, 1 - Sample(rough, roughness, u, v, surface.roughness));
                    }
                packed.SetPixels(pixels);
                packed.Apply();
                File.WriteAllBytes(path, packed.EncodeToPNG());
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                return LoadTexture(folder, Path.GetFileName(path), TextureImporterType.Default, false, false);
            }
            finally { UnityEngine.Object.DestroyImmediate(packed); }
        }
    }
}

