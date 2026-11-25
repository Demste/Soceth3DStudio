# Soceth 3D Studio

Soceth is a Windows desktop studio for generating, editing and shipping 3D game assets locally. It keeps Art Bibles, source reconstructions, editable Blender revisions, and Unity-ready exports in project storage.

## Install

Windows 10 or 11, WebView2, Python 3.11–3.13, and Blender 4.3 or newer are supported by the installer. The tested Blender version is 5.1.2. Source builds also require Node.js 22.12 or later. The [Windows release](https://github.com/Demste/Soceth3DStudio/releases/latest) includes the built desktop UI. Initial dependency and model downloads require internet access. Generation runs locally afterward.

```powershell
git clone https://github.com/Demste/Soceth3DStudio.git
cd Soceth3DStudio
.\setup.ps1 -NoLaunch
.\start.ps1
```

Choose recommended models, custom variants, existing local weights, or skip AI and use Blender mesh import and export. The installer shows download and storage requirements before model installation. Follow the [Turkish setup guide](SETUP.md) to reuse providers, change storage locations, update, or recover an older version.

## Models and hardware

Recommendations use compute capability, total and available VRAM, system RAM, driver, native provider nodes, and a real CUDA kernel test. They distinguish measured workloads, documented configurations, estimated resource gates, and experimental variants. A successful CUDA probe proves kernel execution. Full-run VRAM fit and image quality are verified separately.

| Hardware and evidence | Recommended configuration |
| --- | --- |
| 8 GiB NVIDIA, compute 8.6, measured locally | Pixal3D INT8 (512 reconstruction), FLUX.2 Klein 4B FP8 and Qwen2.5-Coder 7B Q4_K_M |
| 16 GiB NVIDIA, compute 12.0, simulated policy only | Keep the measured model families, with greater reconstruction and image settings when free VRAM and CUDA checks allow. FLUX.2 Klein 4B BF16 and Qwen2.5-Coder 14B Q4_K_M are comparison candidates. |
| 24 GiB and above, provided resources and native nodes qualify | Higher catalog execution profiles, with published limits and verification labels in Models. Pixal3D BF16 is an experimental candidate with no measured minimum. |

The underlying model source and licenses are listed in the [model catalog](shared/model-catalog.json) and [license manifest](docs/LICENSES.md). Existing local model paths are referenced without copying when the pinned files match. An already installed local Ollama tag can be registered without a pull. Ollama imports a standalone GGUF through its own blob store. Custom ComfyUI files must match a supported native workflow; arbitrary checkpoints and custom nodes need a separate adapter.

A model selected for one run takes precedence over the project default, global default and automatic recommendation, in that order. Hardware changes refresh recommendations while keeping saved selections. Use Advanced mode to compare same-input runs, configure providers, tune the concept sampler and inspect logs.

## Production workflow

1. Create a project and define its palette, material categories, geometry and export budgets in **Art Bible**.
2. In **Workspace**, import a mesh or generate 3D from an image. In Text mode, generate and approve a concept before reconstruction.
3. Inspect the result, map ambiguous texture regions and preview the project palette. Interpret editing instructions, review the structured operations, then apply in Blender.
4. Optimize to the project budget or a manual target. Review structural measurements, compare against the source and create independent distance LODs as needed.
5. Approve a compliant main revision. Export a Unity package with FBX, GLB, material bindings, textures and the Editor importer.

## Update and contribute

Run **.\\update.ps1 -Check** to inspect Git updates and **.\\update.ps1** to apply a fast-forward. The ZIP uses **upgrade-release.ps1** with the published checksum. Studio checks public release metadata and shows newer versions. Installation changes are explicit.

See [architecture](ARCHITECTURE.md), [Blender protocol](docs/BLENDER_PROTOCOL.md), [acceptance checks](docs/ACCEPTANCE.md), [contributing](CONTRIBUTING.md), and [security reporting](SECURITY.md).

