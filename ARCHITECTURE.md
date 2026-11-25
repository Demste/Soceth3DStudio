# Architecture

- `apps/desktop` hosts the React and TypeScript application in a native WebView2 window. Three.js loads revision GLBs from the authorized local backend. The frontend never calls model libraries.
- `services/backend` hosts FastAPI on loopback, migration-based SQLite metadata, and a single persistent GPU-aware work queue. WebSocket updates reflect SQLite job records. Large binaries and logs live on disk.
- `services/backend/providers` separates provider selection, model artifacts, file verification, and inference. `shared/model-catalog.json` pins model revisions and weights. Add a catalog entry and, when needed, a provider or workflow adapter without altering the desktop.
- `integrations/blender_addon` is the deterministic Blender engine shared by interactive and background execution. The add-on uses a local authenticated pull bridge, a thread-safe queue, and a Blender main-thread timer.
- `integrations/unity` builds prefabs from the explicit export manifest. Binary validation and per-LOD triangle checks run before packaging.

An immutable revision contains a packed editable Blend file, a GLB preview, validation, and provenance. A revision and its file records become visible together with the completed job in a database transaction. The parent is checked at commit to prevent lost updates after restore. Failed attempts can leave diagnostic files in job directories. Queued jobs survive restart. Jobs active during a crash become interrupted and can be retried. Keep the project store on a disk with enough free space for both current and historical revisions.

The launcher hands the local capability to WebView in a fragment, which is removed before the frontend starts rendering. The API exchanges it for an HttpOnly same-site cookie. Blender uses the capability from the local token file. Asset file routes resolve against the Studio project root, and revision jobs get their paths from database records under that root. Model inputs and Blender operations are validated by typed schemas. Untrusted text is never evaluated as Python or shell code.

Blender works in meters and Z-up. A glTF export uses Y-up and Blender's exporter performs the axis conversion. The Unity manifest lists local pivots, object names, bounds, textures, LODs, and collider shapes. Unity uses the FBX conversion and maps direct Principled base color, roughness, metallic, normal and emission components. Metallic-smoothness is repacked for URP/Lit from the available maps. Cycles baking currently supports base color for compatible Principled surfaces. Pixal3D's PBR textures remain embedded in the original GLB and are imported into the editable scene.

