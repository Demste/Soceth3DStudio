# Licenses and sources

Original standalone Studio code is available under MIT. The Blender add-on and in-process worker are GPL-3.0-or-later. The separately bundled ComfyUI templates retain their upstream MIT notice. Releases do not bundle AI weights or provider runtimes. See [NOTICE](../NOTICE).

The model manager installs pin- and hash-verified artifacts listed in [the model catalog](../shared/model-catalog.json). Keep upstream notices with any redistributed model or runtime. User-generated asset rights and applicable license conditions remain the studio's responsibility.

| Component | License and upstream |
|---|---|
| Pixal3D and Trellis.2 VAEs | MIT. TencentARC, Microsoft, and the Comfy-Org conversion. [Model repository](https://huggingface.co/Comfy-Org/Pixal3D) |
| DINOv3 vision encoder used by Pixal3D | Meta DINOv3 License. Commercial usage is conditional on its terms, including use and trade-control restrictions. Research publications require acknowledgement. Redistribution requires the license terms. [Full license](https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md) |
| MoGe and BiRefNet | MIT. [MoGe](https://huggingface.co/Comfy-Org/MoGe) · [BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet) |
| FLUX.2 Klein 4B FP8 and 4B model auxiliaries | Apache-2.0. [Black Forest Labs FP8](https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8) · [Comfy-Org 4B](https://huggingface.co/Comfy-Org/Flux-2-Klein-4B) |
| Qwen2.5-Coder 7B Instruct Q4_K_M | Apache-2.0. [Qwen](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct) · [Ollama tag](https://ollama.com/library/qwen2.5-coder:7b-instruct-q4_K_M) |
| Optional native comparison variants | FLUX.2 Klein 4B BF16 and Qwen2.5-Coder 14B Instruct are Apache-2.0. Pixal3D BF16 uses the same conditional DINOv3 dependency as INT8. The catalog pins their artifact revisions and separates documented requirements from execution measurements. [FLUX 4B card](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) · [Qwen 14B tag](https://ollama.com/library/qwen2.5-coder:14b-instruct-q4_K_M) |
| ComfyUI, official workflow templates | See the GPL-3.0 runtime notice and the pinned template repository notice. [ComfyUI](https://github.com/Comfy-Org/ComfyUI) · [Templates](https://github.com/Comfy-Org/workflow_templates/tree/3d63e0a274cac5d8e232c217aaaf0ad548f8258e) |
| Blender and Ollama | Blender GPL-3.0-or-later, Ollama MIT. [Blender license](https://www.blender.org/about/license/) · [Ollama license](https://github.com/ollama/ollama/blob/main/LICENSE) |
| Desktop and backend | Licenses are provided with installed packages. React, Three.js, FastAPI, Uvicorn, Pydantic and TypeScript use permissive licenses. pywebview is BSD-3-Clause. |

Soceth uses the native ComfyUI pipeline, which replaces the original project's NVIDIA research-only rendering dependencies. FLUX.2 9B and unreviewed auxiliary models are outside the included production catalog. [ComfyUI implementation notes](https://blog.comfy.org/p/trellis2-and-pixal3d-are-now-native)

