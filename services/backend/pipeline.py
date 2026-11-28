import io
import json
import re
import shutil
import zipfile
from pathlib import Path

from . import blender
from .core import ROOT, atomic, db, file_record, now, project_file, query, uid, write_json
from .media import image_data, validate_glb, validate_textured_glb
from .providers.inference import PROVIDERS, compose
from .providers.models import CATALOG, install, is_ready, model_for, remove
from .providers.discovery import check_candidate_install, register_existing, register_ollama
from .providers.registry import TEXTURED
from .providers.workflows import texture_resolution
from .schemas import Bible, DESTRUCTIVE, Proposal


def working_revision(asset, identifier=None):
    key = identifier or asset["current"]
    revision = query("SELECT * FROM revisions WHERE id=? AND asset=?", (key, asset["id"]))
    if not revision:
        raise ValueError("The requested asset revision is unavailable.")
    return revision


def persist(ctx, callback):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ctx.check(conn)
        result = callback(conn)
        conn.execute("UPDATE jobs SET status='completed',stage='Completed',progress=1,result=?,updated=? WHERE id=?",
                     (json.dumps(result), now(), ctx.id))
    return result


def record_textures(conn, project, directory, output):
    return {name: file_record(conn, project, directory / relative, "texture", Path(relative).name)
            for name, relative in output.get("textures", {}).items()}


def attach_textures(report, textures, original=None):
    for item in report["textures"]:
        item["file"] = textures.get(item["name"])
    for item in report.get("art_bible", {}).get("materials", []):
        item["texture_file"] = textures.get(item.get("image"))
        item["original_texture_file"] = (original or {}).get(item.get("original_image"))


def attach_revision_textures(report, revision):
    provenance = revision["body"]["provenance"]
    attach_textures(report, provenance.get("final_textures", {}), provenance.get("original_textures"))


def commit_revision(ctx, asset, parent, output, provenance, source=None, thumbnail=None,
                    directory=None, complete=True, legacy_textures=None, texture_contract=None):
    directory = directory or ctx.out
    report = output["report"]
    if texture_contract:
        preview = validate_textured_glb(directory / "preview.glb", texture_contract)
        if not report["uv_present"] or not any("base_color" in material["maps"] for material in report["materials"]):
            raise ValueError("Blender did not preserve the textured material and its UVs.")
        provenance["preview_textures"] = preview
    else:
        validate_glb(directory / "preview.glb")
    if not (directory / "working.blend").is_file():
        raise ValueError("Blender did not write an editable scene.")
    identifier = uid()
    project = ctx.project
    if thumbnail is not None:
        atomic(directory / "thumbnail.png", image_data(thumbnail, thumbnail=True))

    def finish(conn):
        if parent:
            current = conn.execute("SELECT current FROM assets WHERE id=?", (asset["id"],)).fetchone()
            if current["current"] != parent["id"]:
                raise ValueError("The current revision changed. Restore the requested base or reinterpret the edit.")
        else:
            conn.execute("INSERT INTO assets VALUES (?,?,?,?,?,?,?)",
                         (asset["id"], project, asset["name"], asset["category"], json.dumps(asset["body"]), None, now()))
        files = {key: file_record(conn, project, directory / filename, key, filename)
                 for key, filename in (("blend", "working.blend"), ("preview", "preview.glb"))}
        if thumbnail is not None:
            asset["body"]["thumbnail"] = file_record(conn, project, directory / "thumbnail.png", "thumbnail", "thumbnail.png")
        textures = record_textures(conn, project, directory, output)
        origin = parent["body"]["provenance"] if parent else {}
        original_textures = textures if source else origin.get("original_textures", {})
        if legacy_textures:
            original_textures = record_textures(conn, project, *legacy_textures)
        if source:
            files["source"] = file_record(conn, project, source, "source_mesh", source.name)
            asset["body"]["source_mesh"] = files["source"]
        attach_textures(report, textures, original_textures)
        compliance = report.get("art_bible", {})
        adaptation = compliance.get("adaptation", [])
        applied = origin.get("applied_palette")
        if adaptation:
            applied = {"bible_version": compliance.get("bible_version"), "policy": compliance.get("policy"),
                       "colors": compliance.get("palette"), "semantic_colors": compliance.get("semantic_palette")}
        elif provenance["kind"] == "palette_restore":
            applied = None
            adaptation = [{"method": "restore_authored_colors"}]
        else:
            adaptation = origin.get("adaptation", [])
        provenance.update(
            color_policy=compliance.get("policy"), applied_palette=applied,
            source_reference=asset["body"].get("reference"), original_mesh=asset["body"].get("source_mesh"),
            original_textures=original_textures, final_textures=textures, regions=compliance.get("regions", []),
            adaptation=adaptation)
        variant = output.get("variant", "main")
        report["lifecycle"] = "source" if source else "lod" if variant == "lod" else "candidate"
        report["variant"] = variant
        write_json(directory / "validation.json", report)
        files["report_file"] = file_record(conn, project, directory / "validation.json", "report_file", "validation.json")
        body = {**files, "report": report, "provenance": provenance}
        conn.execute("INSERT INTO revisions VALUES (?,?,?,?,?)",
                     (identifier, asset["id"], parent["id"] if parent else None, json.dumps(body), now()))
        # Keep concurrent metadata edits when publishing a later revision.
        if parent and variant == "main":
            conn.execute("UPDATE assets SET current=?,updated=? WHERE id=?", (identifier, now(), asset["id"]))
        elif not parent:
            conn.execute("UPDATE assets SET current=?,body=?,updated=? WHERE id=?",
                         (identifier, json.dumps(asset["body"]), now(), asset["id"]))
        result = {"asset": asset["id"], "revision": identifier, "report": report, "variant": variant}
        if variant == "trial":
            result["warning"] = "The requested target was rejected. A safer comparison revision was saved and the main revision was kept."
        return result
    if complete:
        return persist(ctx, finish)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ctx.check(conn)
        return finish(conn)


def publish_inspection(ctx, parent, output, preview):
    report = output["report"]
    def finish(conn):
        textures = record_textures(conn, ctx.project, ctx.out, output)
        attach_textures(report, textures, parent["body"]["provenance"].get("original_textures"))
        write_json(ctx.out / "validation.json", report)
        result = {"report": report, "revision": parent["id"],
                  "file": file_record(conn, ctx.project, ctx.out / "validation.json", "report_file", "validation.json")}
        if preview:
            result["preview"] = file_record(conn, ctx.project, ctx.out / "preview.glb", "preview", "preview.glb")
        return result
    if preview:
        validate_glb(ctx.out / "preview.glb")
    return persist(ctx, finish)


def lifecycle(revision):
    report = revision["body"]["report"]
    return report.get("lifecycle", "source" if not revision["parent"] else "candidate")


def lod_sources(asset, parent, project):
    rows = query("SELECT * FROM revisions WHERE asset=? AND parent=? ORDER BY created DESC",
                 (asset["id"], parent["id"]), many=True)
    sources = {}
    for row in rows:
        report = row["body"]["report"]
        if lifecycle(row) != "lod" or report.get("structural_quality", {}).get("rating") == "failed":
            continue
        if report["triangles"] >= parent["body"]["report"]["triangles"] or report["triangles"] in sources:
            continue
        blend, _ = project_file(project, row["body"]["blend"], {"blend"})
        sources[report["triangles"]] = {"source": str(blend), "revision": row["id"],
            "triangles": report["triangles"], "objects": report["objects"]}
    return [sources[count] for count in sorted(sources, reverse=True)[:4]]


def collect_legacy_textures(ctx, asset, parent, command):
    if "original_textures" in parent["body"]["provenance"]:
        return None
    original = query("SELECT * FROM revisions WHERE asset=? AND parent IS NULL", (asset["id"],))
    if not original or not original["body"]["report"]["textures"]:
        return None
    source, _ = project_file(ctx.project, original["body"]["blend"], {"blend"})
    directory = ctx.out / "original-textures"
    output = blender.execute(ctx, {**command, "kind": "validate", "source": str(source),
                                   "output": str(directory), "regions": [], "quality_issues": []})
    return directory, output


def approve_revision(ctx, asset, parent, report, payload):
    if any(issue["code"] == "degenerate" for issue in report["issues"]):
        raise ValueError("Repair invalid or empty geometry before approval.")
    notes = payload.get("review_notes", "").strip()
    if report["review_required"] and not notes:
        raise ValueError("Review the Art Bible and geometry warnings and document any accepted exceptions before approval.")
    attach_revision_textures(report, parent)

    def finish(conn):
        current = conn.execute("SELECT current,category FROM assets WHERE id=?", (asset["id"],)).fetchone()
        version = conn.execute("SELECT MAX(version) FROM bibles WHERE project=?", (ctx.project,)).fetchone()[0]
        if current["current"] != parent["id"] or current["category"] != payload["category"] or version != payload["bible_version"]:
            raise ValueError("The working revision, category, or Art Bible changed. Validate and approve it again.")
        approval = {"bible_version": version, "category": current["category"], "review_notes": notes,
                    "created": now(), "validation": report}
        content = {**parent["body"], "approval": approval}
        conn.execute("UPDATE revisions SET body=? WHERE id=?", (json.dumps(content), parent["id"]))
        return {"asset": asset["id"], "revision": parent["id"], "approval": approval}
    return persist(ctx, finish)


def run(ctx, job):
    payload = job["body"]
    kind = job["kind"]
    project = job["project"]
    if kind in {"install", "remove"}:
        model = CATALOG[payload["model"]]
        if kind == "install":
            check_candidate_install(model)
        result = install(model, ctx) if kind == "install" else remove(model)
        return persist(ctx, lambda _: result)
    if kind == "register":
        result = (register_existing(CATALOG[payload["model"]], payload["roots"], ctx) if payload["model"] else
                  register_ollama(payload["tag"], payload["gguf"], payload["license"], ctx))
        return persist(ctx, lambda _: result)
    bible = Bible.model_validate(payload["bible"]).model_dump()
    category = payload["category"]
    budget = bible["triangle_budgets"].get(category)
    command = {"protocol_version": 1, "kind": kind, "output": str(ctx.out), "triangles": budget,
               "vertex_budget": bible["vertex_budgets"].get(category),
               "texture_size": bible["texture_size"], "meters_per_unit": bible["meters_per_unit"],
               "bible": bible, "bible_version": payload["bible_version"]}
    # Older explicitly queued jobs can be retried from their saved history.
    allow_limited = payload.get("allow_limited_3d", bool(payload.get("model")))
    if kind in {"concept", "generate", "interpret"} and payload.get("model_readiness") == "incompatible":
        chosen = model_for(kind, payload["model"], allow_limited=allow_limited)
        if is_ready(chosen):
            raise ValueError("The selected model cannot run with the current resources. " + " ".join(payload["model_reasons"]))
    if kind == "concept":
        model = model_for(kind, payload["model"])
        seed = payload["seed"]
        prompt = compose(payload["prompt"], bible, category, payload["preset"])
        path = ctx.out / "concept.png"
        provenance = PROVIDERS[model["provider"]].run(ctx, model, prompt=prompt, seed=seed, target=path,
                                                     execution=payload.get("execution"), sampling=payload.get("sampling"))
        atomic(path, image_data(path))
        def finish(conn):
            identifier = file_record(conn, project, path, "concept", "concept.png", {
                **provenance, "prompt": payload["prompt"], "composed_prompt": prompt,
                "bible_version": payload["bible_version"], "category": category, "preset": payload["preset"]})
            return {"file": identifier}
        return persist(ctx, finish)
    if kind in {"generate", "import"}:
        thumbnail = None
        texture_contract = None
        asset = {"id": uid(), "name": payload["name"], "category": category, "body": {"tags": []}}
        source, metadata = project_file(project, payload["file"],
            {"reference", "concept"} if kind == "generate" else {"upload_mesh"})
        provenance = {"kind": kind, "bible_version": payload["bible_version"], "input_file": metadata["id"]}
        target = ctx.out / ("original.glb" if kind == "generate" else "original" + source.suffix)
        if kind == "generate":
            model = model_for(kind, payload["model"], allow_limited=allow_limited)
            execution = payload.get("execution")
            size = texture_resolution(model, execution, payload.get("source_texture_size"), bible["texture_size"])
            capability = model["output_capability"]
            texture_contract = capability if capability in TEXTURED else None
            image = ctx.out / "reference.png"
            atomic(image, image_data(source))
            thumbnail = io.BytesIO(image.read_bytes())
            provenance.update(PROVIDERS[model["provider"]].run(ctx, model, image=image,
                seed=payload["seed"], target=target, texture_limit=size, execution=execution))
            provenance.update(output_capability=capability, source_texture_size=size,
                              auto_optimize=payload.get("auto_optimize", True))
            asset["body"].update(reference=metadata["id"], generation=provenance)
        else:
            shutil.copyfile(source, target)
            asset["body"]["import_file"] = metadata["id"]
        ctx.stage("Validating source mesh")
        if texture_contract:
            observed = validate_textured_glb(target, texture_contract)
            if payload.get("source_texture_size") is not None and observed["base_color_sizes"] != [(size, size)]:
                raise ValueError(f"The model returned a different base-color resolution than the requested {size}px.")
            provenance["source_textures"] = observed
        elif target.suffix == ".glb":
            validate_glb(target)
        command.update(kind="import", source=str(target), source_reconstruction=kind == "generate")
        result = blender.execute(ctx, command)
        source_only = kind == "import" or not payload.get("auto_optimize", True)
        original = commit_revision(ctx, asset, None, result, provenance, target, thumbnail, complete=source_only,
                                   texture_contract=texture_contract)
        if source_only:
            return original
        ctx.stage("Preparing game-ready candidate")
        original_revision = working_revision(asset, original["revision"])
        destination = ctx.out / "candidate"
        command.update(kind="candidate", source=str(ctx.out / "working.blend"), output=str(destination),
                       source_reconstruction=False,
                       target_triangles=budget)
        try:
            result = blender.execute(ctx, command)
            if result.get("quality_failed"):
                return persist(ctx, lambda _: {**original, "warning":
                    "The source was saved. Automatic simplification did not pass structural validation."})
            return commit_revision(ctx, asset, original_revision, result,
                                   {"kind": "candidate", "bible_version": payload["bible_version"]},
                                   directory=destination, texture_contract=texture_contract)
        except (ValueError, OSError) as error:
            return persist(ctx, lambda _: {**original, "warning":
                f"Source reconstruction saved. Game-ready processing needs review: {error}"})
    asset = query("SELECT * FROM assets WHERE id=? AND project=?", (payload["asset"], project))
    if not asset:
        raise ValueError("Asset was not found in this project.")
    parent = working_revision(asset, payload["revision"])
    source, _ = project_file(project, parent["body"]["blend"], {"blend"})
    regions = payload.get("regions")
    if regions is None:
        regions = parent["body"]["provenance"].get("regions", [])
    command.update(source=str(source), asset=asset["id"], project=project, regions=regions,
                   variant=parent["body"]["report"].get("variant", "main"),
                   structural_quality=parent["body"]["report"].get("structural_quality"),
                   quality_issues=parent["body"]["report"].get("quality_issues", []))
    if kind == "open":
        result = blender.execute(ctx, command, "interactive")
        return persist(ctx, lambda _: result)
    if kind == "interpret":
        model = model_for(kind, payload["model"])
        objects = [{key: obj[key] for key in ("id", "name", "triangles", "dimensions", "materials")}
                   for obj in parent["body"]["report"]["objects"]]
        proposal = PROVIDERS[model["provider"]].run(ctx, model, request=payload["prompt"], objects=objects,
                                                  axis="Z-up", triangle_budget=budget, execution=payload.get("execution"))
        validate_targets(proposal, parent, budget)
        return persist(ctx, lambda _: {"proposal": proposal.model_dump(), "asset": asset["id"], "base": parent["id"],
            "request": payload["prompt"], "model": model, "execution": payload.get("execution"),
            "confirmation": needs_confirmation(proposal)})
    if kind in {"edit", "optimize", "cleanup", "capture", "palette_apply", "palette_restore", "approve"} and asset["current"] != parent["id"]:
        raise ValueError("Restore the working revision before editing or optimizing it.")
    if kind in {"approve", "export"} and lifecycle(parent) == "lod":
        raise ValueError("Export the approved main revision to include its saved LODs.")
    provenance = {"kind": kind, "bible_version": payload["bible_version"]}
    create_lod = False
    if kind == "edit":
        item = query("SELECT * FROM jobs WHERE id=? AND project=? AND kind='interpret' AND status='completed'",
                     (payload.get("proposal"), project))
        if not item or item["result"]["base"] != parent["id"] or item["result"]["asset"] != asset["id"]:
            raise ValueError("Obtain a fresh edit proposal for this asset revision.")
        proposal = Proposal.model_validate(item["result"]["proposal"])
        if proposal.intent != "edit_mesh":
            raise ValueError("The proposal contains no executable Blender operations.")
        validate_targets(proposal, parent, budget)
        if needs_confirmation(proposal) and not payload["confirmed"]:
            raise ValueError("Confirm the destructive operations shown in the proposal.")
        command["operations"] = [operation.model_dump() for operation in proposal.operations]
        create_lod = any(operation.type == "decimate_mesh" and
                         (operation.create_lod or operation.profile == "lod") for operation in proposal.operations)
        provenance.update(item["result"])
    if kind == "optimize":
        create_lod = payload.get("create_lod", False) or payload.get("profile") == "lod"
        target = payload.get("triangles") or budget
        if target is None:
            raise ValueError("Define a triangle budget for this asset category or enter a manual target.")
        if budget is not None and target > budget:
            raise ValueError(f"The manual target exceeds the project maximum of {budget} triangles.")
        if create_lod and (payload.get("triangles") is None or payload["bake"]):
            raise ValueError("Enter a manual LOD triangle target. LODs inherit the main revision's textures.")
        if create_lod and target >= parent["body"]["report"]["triangles"]:
            raise ValueError("A distance LOD target must be below the current main revision's triangle count.")
        resize_needed = any(max(item["size"]) > bible["texture_size"] for item in parent["body"]["report"]["textures"])
        command.update(target_triangles=target, bake=payload["bake"], resize_needed=resize_needed,
                       promote=lifecycle(parent) == "source")
        provenance.update(target_triangles=target, bake=payload["bake"])
    if kind in {"edit", "optimize", "cleanup"}:
        reference = query("SELECT * FROM revisions WHERE asset=? AND parent IS NULL", (asset["id"],))
        command["source_triangles"] = reference["body"]["report"]["triangles"]
        command["variant"] = "lod" if create_lod else "main"
        if create_lod and lifecycle(parent) == "source":
            raise ValueError("Create a main asset candidate before generating LOD versions.")
    if kind == "optimize":
        command["profile"] = payload.get("profile", "balanced")
    if kind == "cleanup":
        command.update(profile=payload.get("profile", "preserve_structure"),
                       remove_fragments=payload.get("remove_fragments", True), planar=payload.get("planar", True))
    if (kind == "approve" and lifecycle(parent) == "source"
            and parent["body"]["provenance"].get("auto_optimize") is not False):
        raise ValueError("Create a game-ready candidate before approving this reconstruction.")
    if kind in {"palette_apply", "palette_preview"} and bible["color_policy"] == "reference":
        raise ValueError("Choose Guided or Strict Project Palette in the Art Bible to apply project colors.")
    if kind == "palette_restore":
        command["regions"] = []
    if kind == "export":
        preset = bible["export_presets"].get(payload["preset"])
        if not preset:
            raise ValueError("Select an export preset defined in the Art Bible.")
        approval = parent["body"].get("approval")
        if preset["format"] == "unity" and (not approval or approval["bible_version"] != payload["bible_version"]
                                           or approval["category"] != category):
            raise ValueError("Approve the game-ready candidate against the current Art Bible before exporting to Unity.")
        command.update(preset=preset, approval=approval, saved_lods=lod_sources(asset, parent, project),
                       slug=(re.sub(r"[^a-zA-Z0-9_-]", "_", asset["name"]).strip("_")[:60] or "Asset") + "_" + asset["id"][:8])
        ctx.stage("Exporting")
    mode = "interactive" if kind == "capture" else "background"
    if kind in {"edit", "palette_apply", "palette_restore"}:
        mode = payload["mode"]
    if create_lod:
        mode = "background"
    output = blender.execute(ctx, command, mode)
    report = output["report"]
    ctx.stage("Validating")
    if kind in {"validate", "palette_preview"}:
        return publish_inspection(ctx, parent, output, kind == "palette_preview")
    if kind == "approve":
        return approve_revision(ctx, asset, parent, report, payload)
    if kind == "export":
        if not report["valid"] and not parent["body"].get("approval", {}).get("review_notes"):
            raise ValueError("Validation failed. Review the production report before exporting.")
        if preset["format"] == "unity" and parent["body"]["provenance"].get("preview_textures"):
            validate_textured_glb(ctx.out / "package" / (command["slug"] + ".glb"), "textured")
        return commit_export(ctx, asset, parent, output, command)
    if output.get("unchanged"):
        attach_revision_textures(report, parent)
        result = {"asset": asset["id"], "revision": parent["id"], "report": report, "unchanged": True}
        if output.get("quality_failed") or any(item.get("detached") for item in report["optimization"]):
            result["warning"] = "The requested optimization did not pass. The main revision was preserved."
        return persist(ctx, lambda _: result)
    originals = collect_legacy_textures(ctx, asset, parent, command)
    prior_preview = parent["body"]["provenance"].get("preview_textures")
    texture_contract = None
    if prior_preview and (kind in {"optimize", "cleanup"} or kind == "edit" and all(
            operation["type"] in {"decimate_mesh", "cleanup_mesh"} for operation in command["operations"])):
        # Explicit rebakes may consolidate material maps. They must still retain albedo.
        texture_contract = ("pbr" if {"normalTexture", "metallicRoughnessTexture"} <= set(prior_preview["maps"])
                            and not payload.get("bake") else "textured")
    return commit_revision(ctx, asset, parent, output, provenance, legacy_textures=originals,
                           texture_contract=texture_contract)


def needs_confirmation(proposal):
    return any(operation.type in DESTRUCTIVE for operation in proposal.operations)


def validate_targets(proposal, revision, budget):
    objects = {obj["id"]: obj for obj in revision["body"]["report"]["objects"]}
    for operation in proposal.operations:
        if set(operation.targets) - objects.keys():
            raise ValueError("The local model targeted an object outside the saved revision.")
        if operation.type == "decimate_mesh" and budget is not None:
            if operation.target_triangles is not None and operation.target_triangles > budget:
                raise ValueError(f"The requested triangle target exceeds the project maximum of {budget}.")


def commit_export(ctx, asset, revision, output, command):
    package = ctx.out / "package"
    for glb in package.glob("*.glb"):
        validate_glb(glb)
    manifest = output["manifest"]
    archive = ctx.out / f"{manifest['asset']}.zip"
    if "fbx" in {Path(name).suffix.removeprefix(".") for name in manifest["files"]}:
        shutil.copyfile(ROOT / "integrations/unity/SocethAssetImporter.cs", package / "SocethAssetImporter.cs")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(package.rglob("*")):
            ctx.check()
            if file.is_file():
                name = file.name if file.name == "SocethAssetImporter.cs" else str(file.relative_to(package)).replace("\\", "/")
                bundle.write(file, "Editor/" + name if file.name.endswith(".cs") else f"SocethAssets/{manifest['asset']}/{name}")
    def finish(conn):
        if command["preset"]["format"] == "unity":
            version = conn.execute("SELECT MAX(version) FROM bibles WHERE project=?", (ctx.project,)).fetchone()[0]
            category = conn.execute("SELECT category FROM assets WHERE id=?", (asset["id"],)).fetchone()["category"]
            if command["approval"]["bible_version"] != version or command["approval"]["category"] != category:
                raise ValueError("The category or Art Bible changed during export. Validate and approve the revision again.")
        export_id, created = uid(), now()
        file_id = file_record(conn, ctx.project, archive, "export", archive.name)
        conn.execute("INSERT INTO exports VALUES (?,?,?,?,?,?)",
                     (export_id, asset["id"], revision["id"], file_id, json.dumps(manifest), created))
        result = {"export": export_id, "file": file_id, "manifest": manifest}
        if manifest.get("lod_warnings"):
            result["warning"] = " ".join(manifest["lod_warnings"])
        return result
    return persist(ctx, finish)

