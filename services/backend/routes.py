import json
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import UnidentifiedImageError

from .core import PROJECTS, atomic, db, file_record, now, query, safe, set_setting, uid
from .media import IMAGE_LIMIT, MESH_LIMIT, image_data, validate_glb
from .pipeline import working_revision
from .schemas import AssetInfo, Bible, ID, Project

router = APIRouter(prefix="/api")


def found(row, message):
    if not row:
        raise HTTPException(404, message)
    return row


def project_row(project):
    return found(query("SELECT * FROM projects WHERE id=?", (project,)), "Project was not found.")


def asset_row(asset):
    return found(query("SELECT * FROM assets WHERE id=?", (asset,)), "Asset was not found.")


def active_bible(project):
    return found(query("SELECT * FROM bibles WHERE project=? ORDER BY version DESC LIMIT 1", (project,)), "Art Bible was not found.")


@router.get("/projects")
def list_projects():
    return query("""SELECT p.*, (SELECT count(*) FROM assets a WHERE a.project=p.id) AS asset_count
                    FROM projects p ORDER BY p.updated DESC""", many=True)


@router.post("/projects", status_code=201)
def create(project: Project):
    key, stamp = uid(), now()
    safe(PROJECTS, key).mkdir()
    with db() as conn:
        conn.execute("INSERT INTO projects VALUES (?,?,?,?)", (key, project.name, project.model_dump_json(), stamp))
        conn.execute("INSERT INTO bibles VALUES (?,?,?,?)", (key, 1, Bible(color_policy="guided").model_dump_json(), stamp))
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('last_project',?)", (json.dumps(key),))
    return project_row(key)


@router.put("/projects/{project}")
def update(project: ID, body: Project):
    project_row(project)
    with db() as conn:
        conn.execute("UPDATE projects SET name=?,body=?,updated=? WHERE id=?",
                     (body.name, body.model_dump_json(), now(), project))
    return project_row(project)


@router.get("/projects/{project}")
def get(project: ID):
    return project_row(project)


@router.post("/projects/{project}/activate")
def activate(project: ID):
    project_row(project)
    set_setting("last_project", project)
    return {"id": project}


@router.get("/projects/{project}/bible")
def bible(project: ID):
    value = active_bible(project)
    return {**value, "body": Bible.model_validate(value["body"]).model_dump()}


@router.get("/projects/{project}/bible/versions")
def bible_versions(project: ID):
    project_row(project)
    return query("SELECT * FROM bibles WHERE project=? ORDER BY version DESC", (project,), True)


@router.put("/projects/{project}/bible")
def save_bible(project: ID, body: Bible):
    project_row(project)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT version,body FROM bibles WHERE project=? ORDER BY version DESC LIMIT 1",
                                (project,)).fetchone()
        version = previous["version"] + 1
        if "color_policy" not in body.model_fields_set:
            policy = Bible.model_validate_json(previous["body"]).color_policy
            body = Bible.model_validate({**body.model_dump(), "color_policy": policy})
        conn.execute("INSERT INTO bibles VALUES (?,?,?,?)", (project, version, body.model_dump_json(), now()))
        conn.execute("UPDATE projects SET updated=? WHERE id=?", (now(), project))
    return active_bible(project)


@router.get("/projects/{project}/references")
def references(project: ID):
    project_row(project)
    return query("SELECT id,name,kind,body,created FROM files WHERE project=? AND kind IN ('reference','concept') ORDER BY created DESC",
                 (project,), True)


@router.post("/projects/{project}/files", status_code=201)
def upload(project: ID, file: UploadFile = File(...)):
    project_row(project)
    name = Path((file.filename or "reference").replace("\\", "/")).name[:160]
    ext = Path(name).suffix.lower()
    image = ext in {".png", ".jpg", ".jpeg", ".webp"}
    if not image and ext not in {".glb", ".gltf", ".fbx"}:
        raise HTTPException(415, "Upload a PNG, JPEG, WebP, GLB, self-contained GLTF or FBX file.")
    limit = IMAGE_LIMIT if image else MESH_LIMIT
    if not file.size or file.size > limit:
        raise HTTPException(413, f"File is empty or exceeds {limit // 1024**2} MiB.")
    directory = safe(PROJECTS, project, "uploads", uid())
    path = directory / ("reference.png" if image else "source" + ext)
    kind = "reference" if image else "upload_mesh"
    original = None
    try:
        if image:
            original = directory / ("original" + ext)
            content = image_data(file.file)
            file.file.seek(0)
            atomic(original, file.file.read(limit + 1))
            atomic(path, content)
        else:
            directory.mkdir(parents=True)
            with path.open("xb") as target:
                while chunk := file.file.read(2**20):
                    target.write(chunk)
            if ext == ".glb":
                validate_glb(path)
            elif ext == ".gltf":
                graph = json.loads(path.read_text(encoding="utf-8-sig"))
                if graph.get("asset", {}).get("version") != "2.0" or not graph.get("meshes"):
                    raise ValueError("Invalid glTF 2.0 mesh.")
                if any(not item.get("uri", "data:").startswith("data:")
                       for key in ("buffers", "images") for item in graph.get(key, [])):
                    raise ValueError("Import a self-contained GLTF or GLB with embedded textures.")
            else:
                with path.open("rb") as source:
                    header = source.read(512)
                if not (header.startswith(b"Kaydara FBX Binary") or b"FBX" in header):
                    raise ValueError("Invalid FBX header.")
        with db() as conn:
            original_id = file_record(conn, project, original, "original_image", name) if image else None
            item = file_record(conn, project, path, kind, name, {"original": original_id} if image else {})
    except (ValueError, OSError, UnidentifiedImageError) as error:
        path.unlink(missing_ok=True)
        if original:
            original.unlink(missing_ok=True)
        if directory.exists():
            directory.rmdir()
        raise HTTPException(422, str(error)) from error
    return {"id": item, "kind": kind, "name": name}


@router.get("/files/{identifier}")
def open_file(identifier: ID, download: bool = False):
    item = found(query("SELECT * FROM files WHERE id=?", (identifier,)), "Project file was not found.")
    path = safe(PROJECTS, item["path"])
    if not path.is_file():
        raise HTTPException(404, "Project file is missing from disk.")
    types = {".glb": "model/gltf-binary", ".png": "image/png", ".json": "application/json",
             ".zip": "application/zip", ".jpg": "image/jpeg", ".webp": "image/webp", ".jpeg": "image/jpeg"}
    return FileResponse(path, media_type=types.get(path.suffix.lower(), "application/octet-stream"),
                        filename=item["name"] if download or item["kind"] in {"blend", "export"} else None,
                        headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"})


@router.get("/assets")
def all_assets(project: ID | None = None):
    if project:
        return query("SELECT * FROM assets WHERE project=? ORDER BY updated DESC", (project,), True)
    return query("SELECT * FROM assets ORDER BY updated DESC LIMIT 24", many=True)


@router.get("/assets/{asset}")
def asset_details(asset: ID):
    row = asset_row(asset)
    return {**row, "revisions": query("SELECT * FROM revisions WHERE asset=? ORDER BY created DESC", (asset,), True),
            "exports": query("SELECT * FROM exports WHERE asset=? ORDER BY created DESC", (asset,), True)}


@router.patch("/assets/{asset}")
def asset_update(asset: ID, info: AssetInfo):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = found(conn.execute("SELECT body FROM assets WHERE id=?", (asset,)).fetchone(), "Asset was not found.")
        body = {**json.loads(row["body"]), "tags": info.tags}
        conn.execute("UPDATE assets SET name=?,category=?,body=?,updated=? WHERE id=?",
                     (info.name, info.category, json.dumps(body), now(), asset))
    return asset_row(asset)


@router.post("/assets/{asset}/restore/{revision}")
def restore(asset: ID, revision: ID):
    row = asset_row(asset)
    selected = working_revision(row, revision)
    if selected["body"]["report"].get("lifecycle") == "lod":
        raise HTTPException(422, "A distance LOD can be previewed or opened in Blender. Restore a main revision here.")
    with db() as conn:
        conn.execute("UPDATE assets SET current=?,updated=? WHERE id=?", (revision, now(), asset))
    return asset_details(asset)

