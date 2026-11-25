using System;
using System.Globalization;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class UnityAcceptance
{
    public static void Check()
    {
        var args = Environment.GetCommandLineArgs();
        int manifestArg = Array.IndexOf(args, "-socethManifest");
        var manifest = manifestArg >= 0 ? args[manifestArg + 1] : AssetDatabase.FindAssets("soceth-manifest", new[] {"Assets/SocethAssets"})
            .Select(AssetDatabase.GUIDToAssetPath).Single(path => path.EndsWith("/soceth-manifest.json"));
        var prefab = Soceth.Editor.SocethAssetImporter.Build(manifest);
        if (!prefab) throw new Exception("Prefab export is missing.");
        var levels = prefab.GetComponent<LODGroup>().GetLODs();
        if (levels.Length != 3) throw new Exception("Expected three LOD levels.");
        var counts = levels.Select(level => level.renderers.Sum(renderer => renderer.GetComponent<MeshFilter>().sharedMesh.triangles.Length / 3)).ToArray();
        if (!(counts[0] > counts[1] && counts[1] > counts[2])) throw new Exception("LOD triangle counts did not decrease.");
        var collider = prefab.GetComponentInChildren<CapsuleCollider>();
        if (!collider || collider.GetComponent<MeshRenderer>() || collider.height < collider.radius * 2)
            throw new Exception("Invalid capsule collider or visible collider mesh.");
        var renderer = levels[0].renderers[0];
        var material = renderer.sharedMaterial;
        if (!material || material.shader.name != "Universal Render Pipeline/Lit" || !material.GetTexture("_BaseMap"))
            throw new Exception("URP/Lit texture mapping was not imported.");
        if (args.Contains("-socethPbr"))
        {
            if (!material.GetTexture("_BumpMap") || !material.IsKeywordEnabled("_NORMALMAP") ||
                !material.GetTexture("_MetallicGlossMap") || !material.IsKeywordEnabled("_METALLICSPECGLOSSMAP"))
                throw new Exception("URP normal or packed metallic and smoothness map is missing.");
            if (material.IsKeywordEnabled("_EMISSION") || material.GetColor("_EmissionColor").maxColorComponent > 0f)
                throw new Exception("A material whose Blender emission strength was zero became emissive.");
        }
        if (args.Contains("-socethPalette"))
        {
            var albedo = (Texture2D)material.GetTexture("_BaseMap");
            var albedoPath = AssetDatabase.GetAssetPath(albedo);
            var importer = AssetImporter.GetAtPath(albedoPath) as TextureImporter;
            if (!importer || !importer.sRGBTexture || !System.IO.Path.GetFileName(albedoPath).Contains("palette-"))
                throw new Exception("The URP material did not receive the adapted sRGB atlas.");
            importer.isReadable = true;
            importer.SaveAndReimport();
            albedo = AssetDatabase.LoadAssetAtPath<Texture2D>(albedoPath);
            var center = albedo.GetPixelBilinear(.5f, .5f);
            if (!(center.g > center.r * 1.5f && center.g > center.b * 1.2f))
                throw new Exception("The painted green roof mask is missing from the imported atlas: " + center);
            Debug.Log("SOCETH PALETTE PASSED. Imported atlas center " + center);
        }
        int widthArg = Array.IndexOf(args, "-socethExpectedWidth");
        float expected = widthArg >= 0 ? float.Parse(args[widthArg + 1], CultureInfo.InvariantCulture) : 2f;
        float width = renderer.bounds.size.x;
        if (Mathf.Abs(width - expected) > expected * 0.1f) throw new Exception("FBX meter scale was not preserved: " + width);
        Debug.Log("SOCETH UNITY PASSED. Triangles " + string.Join(", ", counts) + " and width " + width);
    }
}

