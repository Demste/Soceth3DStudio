# Acceptance checks

## Measured inference baseline

Windows x64, NVIDIA RTX 3070 8 GiB, native ComfyUI with PyTorch 2.10.0+cu126, and the LOW_VRAM execution profile.

| Workload | Measured result | Wall time | System GPU peak |
| --- | --- | ---: | ---: |
| Pixal3D INT8 image reconstruction | 60,000 source triangles with three embedded PBR textures | 201.24 s | 7,838 MiB |
| FLUX.2 Klein 4B FP8 concept | 512 px PNG with CPU text encoding | 53.88 s | 5,800 MiB |
| Pixal3D INT8 reconstruction from a generated concept | 60,000 source triangles with embedded PBR | 219.52 s | 7,540 MiB |
| Qwen2.5-Coder 7B Q4_K_M | Interpreted a structured Blender edit | 6.09 s | 6,651 MiB |

These are individual previously measured local jobs. Peak memory includes other GPU processes. The current release's CUDA kernel probe and provider graph checks are lighter than inference benchmarks. Blackwell recommendation tests simulate hardware properties. Full inference on an RTX 5070 Ti remains unverified.

## Reproduce locally

- **Hardware and registry** Run **python -m unittest discover -s tests -p "test_*.py"** in the application environment, then **.\verify.ps1 -Detailed** for the physical device.
- **Mesh, Art Bible and export** Run **tests/check_pipeline.py** with the app interpreter. The command defaults to local mesh fixtures and skips full AI generation.
- **Actual house regression** Supply an existing Pixal3D GLB to **tests/check_structure_house.py** and **tests/check_production_house.py** with an isolated QA directory. Add their local AI flags only to exercise installed Ollama. Use **tests/check_desktop.py** for native WebView2 comparisons and **tests/check_interactive.py** for the Blender connection.
- **Unity** Extract the package into **Assets** in a disposable Unity 6 URP project. Copy **tests/UnityAcceptance.cs** into its Editor directory and run **UnityAcceptance.Check**.
- **Inference graphs** With a supported local provider running, the **graph** mode in **tests/check_ai.py** verifies native node signatures without generating a new image or mesh.

Use your own fixtures and keep machine-specific logs, private art, tokens and database files outside Git.

