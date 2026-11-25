"""Structure-aware cleanup, simplification and measured acceptance in Blender."""

import math
from collections import deque
from dataclasses import dataclass

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import scene


@dataclass(frozen=True)
class Profile:
    planar_angle: float
    silhouette: float
    weakest_view: float
    depth_change: float
    bounds_change: float
    openings: float
    feature_weight: float


PROFILES = {
    "preserve_structure": Profile(1.4, .980, .951, .075, .022, .83, .87),
    "balanced": Profile(2.5, .970, .936, .105, .034, .73, .72),
    "aggressive": Profile(3.8, .954, .895, .155, .047, .62, .60),
    "lod": Profile(5.0, .925, .846, .235, .064, .48, .48),
}
VIEWS = [Vector(direction).normalized() for direction in (
    (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1), (1, -1, .65), (-1, 1, .55))]
RESOLUTION = 38


def issue(level, code, message):
    return dict(level=level, code=code, message=message)


def parts(geom):
    """Face islands and their areas, along with loose edges/vertices."""
    geom.faces.index_update()
    seen = set()
    regions = []
    for first in geom.faces:
        if first.index in seen:
            continue
        stack, linked = [first], []
        seen.add(first.index)
        while stack:
            face = stack.pop()
            linked.append(face)
            for edge in face.edges:
                for other in edge.link_faces:
                    if other.index not in seen:
                        seen.add(other.index)
                        stack.append(other)
        vertices = {vertex for face in linked for vertex in face.verts}
        low = Vector(tuple(min(vertex.co[axis] for vertex in vertices) for axis in range(3)))
        high = Vector(tuple(max(vertex.co[axis] for vertex in vertices) for axis in range(3)))
        regions.append((linked, vertices, sum(face.calc_area() for face in linked), low, high))
    return regions


def summarize(mesh):
    geom = bmesh.new()
    try:
        geom.from_mesh(mesh)
        size = Vector(tuple(max(v.co[axis] for v in mesh.vertices) - min(v.co[axis] for v in mesh.vertices)
                            for axis in range(3))).length if mesh.vertices else 0
        split_edges = sum(edge.is_boundary for edge in geom.edges)
        non_manifold = sum(not edge.is_manifold and not edge.is_boundary for edge in geom.edges)
        degenerate = sum(face.calc_area() <= 1e-12 for face in geom.faces)
        if geom.verts and split_edges:
            # Normalize glTF corner splits before counting structural components.
            bmesh.ops.remove_doubles(geom, verts=list(geom.verts), dist=min(1e-6, max(size, 1e-6) * 1e-7))
        regions = parts(geom)
        area = sum(region[2] for region in regions)
        geom.verts.index_update()
        major = []
        for _, vertices, area_part, low, high in regions:
            if area_part >= area * .025 or (high - low).length >= size * .20:
                ordered = sorted(vertices, key=lambda vertex: vertex.index)
                samples = [vertex.co.copy() for vertex in ordered[::max(1, len(ordered) // 64)]]
                major.append((area_part, low, high, samples))
        return {
            "components": len(regions), "major": sorted(major, key=lambda item: item[0], reverse=True)[:128],
            "scale": max(size, 1e-7), "surface": BVHTree.FromBMesh(geom),
            "split_edges": split_edges, "non_manifold": non_manifold, "degenerate": degenerate,
        }
    finally:
        geom.free()


def openings(depth):
    """Find sampled through-openings enclosed by the projection of the source."""
    void = ~np.isfinite(depth)
    size = len(depth)
    visited = np.zeros_like(void)
    holes = []
    for row, col in np.argwhere(void):
        if visited[row, col]:
            continue
        queue = deque([(row, col)])
        visited[row, col] = True
        region, outside = [], False
        while queue:
            y, x = queue.popleft()
            region.append((y, x))
            outside |= y in (0, size - 1) or x in (0, size - 1)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < size and 0 <= nx < size and void[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    queue.append((ny, nx))
        if not outside and len(region) >= 5:
            holes.append(tuple(zip(*region)))
    return holes


def projections(obj, frames=None):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        matrix = evaluated.matrix_world
        vertices = [matrix @ vertex.co for vertex in mesh.vertices]
        if not vertices or not mesh.polygons:
            return None
        coordinates = np.array(vertices)
        low, high = coordinates.min(axis=0), coordinates.max(axis=0)
        scale = max(float(np.linalg.norm(high - low)), 1e-7)
        if frames is None:
            frames = []
            for direction in VIEWS:
                up = Vector((0, 0, 1) if abs(direction.z) < .95 else (0, 1, 0))
                u = direction.cross(up).normalized()
                v = u.cross(direction).normalized()
                horizontal, vertical, distance = (coordinates @ np.asarray(axis) for axis in (u, v, direction))
                margin = scale * .028
                frames.append((direction, u, v, horizontal.min() - margin, horizontal.max() + margin,
                               vertical.min() - margin, vertical.max() + margin, distance.min() - scale))
        tree = BVHTree.FromPolygons(vertices, [tuple(face.vertices) for face in mesh.polygons])
        depth_maps = []
        for direction, u, v, left, right, bottom, top, start in frames:
            depth = np.full((RESOLUTION, RESOLUTION), np.nan)
            base = direction * start
            for y in range(RESOLUTION):
                line = base + v * (bottom + (top - bottom) * (y + .5) / RESOLUTION)
                for x in range(RESOLUTION):
                    point = line + u * (left + (right - left) * (x + .5) / RESOLUTION)
                    _, _, _, distance = tree.ray_cast(point, direction)
                    if distance is not None:
                        depth[y, x] = distance
            depth_maps.append(depth)
        return frames, depth_maps, low, high, scale, summarize(mesh)
    finally:
        evaluated.to_mesh_clear()


def retained_parts(original, result):
    candidate = list(result["major"])
    scale = original["scale"]
    retained = 0
    for area, low, high, samples in original["major"]:
        center = (low + high) / 2
        extent = (high - low).length
        matches = [(abs(other_area / max(area, 1e-12) - 1) * .03
                    + ((other_low + other_high) / 2 - center).length / scale, index)
                   for index, (other_area, other_low, other_high, _) in enumerate(candidate)
                   if ((other_low + other_high) / 2 - center).length < scale * .09
                   and (other_high - other_low).length >= extent * .66 and other_area >= area * .48]
        if matches:
            candidate.pop(min(matches)[1])
            retained += 1
        elif samples:
            # A connected island may be welded into a neighboring part. Check that its surface still exists.
            tolerance = min(scale * .020, extent * .14)
            covered = sum(result["surface"].find_nearest(point, tolerance)[0] is not None for point in samples)
            if covered >= len(samples) * .88:
                retained += 1
    return retained


def measure(original, candidate, limits, preserve_uvs):
    failures, warnings, measurements = [], [], []
    for baseline, obj in zip(original, candidate):
        name, reference, original_quality, holes = baseline
        frames, depth, low, high, scale, geo = reference
        sample = projections(obj, frames)
        if sample is None:
            failures.append(issue("error", "empty_part", f"{name}: a mesh part disappeared."))
            continue
        _, current, new_low, new_high, _, new_geo = sample
        quality = scene.mesh_quality(obj)
        overlap, damaged, measured, openings_retained, opening_count = [], 0, 0, [], 0
        for before, after, enclosed in zip(depth, current, holes):
            b, a = np.isfinite(before), np.isfinite(after)
            if (b | a).any():
                overlap.append(float((b & a).sum() / (b | a).sum()))
            common = b & a
            damaged += int((np.abs(before[common] - after[common]) > .024 * scale).sum())
            measured += int(common.sum())
            opening_count += len(enclosed)
            openings_retained.extend(float(np.isnan(after[region]).mean()) for region in enclosed)
        silhouette = float(np.mean(overlap)) if overlap else 0
        weakest = min(overlap, default=0)
        depth_change = damaged / max(1, measured)
        bounds_change = float(max(np.max(np.abs(low - new_low)), np.max(np.abs(high - new_high))) / scale)
        retained = retained_parts(geo, new_geo)
        lost_parts = len(geo["major"]) - retained
        # Welding duplicated glTF corners can join fragmented coplanar faces.
        if lost_parts and len(new_geo["major"]) < len(geo["major"]) and (
            geo["split_edges"] > original_quality["triangles"] * .6 and silhouette > .98 and depth_change < .025
        ):
            lost_parts = 0
        opening = min(openings_retained, default=None)
        uv_ok = not (set(original_quality["uv"]) - quality["uv"].keys()) and all(
            quality["uv"].get(layer, 1) <= fraction + .045 for layer, fraction in original_quality["uv"].items())
        material_ok = original_quality["materials"] <= quality["materials"]
        checks = (
            (silhouette < limits.silhouette or weakest < limits.weakest_view, "silhouette_change",
             f"projected silhouette {silhouette:.1%} overall, {weakest:.1%} in the weakest view"),
            (depth_change > limits.depth_change, "surface_depth_change", f"{depth_change:.1%} of visible depths shifted"),
            (bounds_change > limits.bounds_change, "bounds_change", f"bounds moved by {bounds_change:.1%} of the part diagonal"),
            (lost_parts > 0, "major_part_loss", f"{lost_parts} major connected parts lost or substantially reduced"),
            (opening is not None and opening < limits.openings, "opening_loss",
             f"weakest sampled opening retained {(opening or 0):.1%}"),
            (new_geo["degenerate"] > 0, "degenerate", f"{new_geo['degenerate']} degenerate faces"),
            (new_geo["non_manifold"] > geo["non_manifold"] + max(10, quality["triangles"] * .022),
             "topology_damage", "a substantial number of non-manifold junctions appeared"),
            (new_geo["split_edges"] > geo["split_edges"] + max(18, quality["triangles"] * .065),
             "open_boundary_damage", "substantial new open mesh boundaries appeared"),
            (preserve_uvs and not uv_ok, "uv_damage", "UV layers were lost or more UV triangles collapsed"),
            (not material_ok, "material_loss", "visible material assignments were lost"),
        )
        failures.extend(issue("error", code, f"{name}: {message}.") for failed, code, message in checks if failed)
        if new_geo["non_manifold"] > geo["non_manifold"] or new_geo["split_edges"] > geo["split_edges"] + 4:
            warnings.append(issue("warning", "topology_change", f"{name}: inspect new open or non-manifold edges."))
        if original_quality["seams"] and not quality["seams"]:
            warnings.append(issue("warning", "uv_seams", f"{name}: explicit UV seam markers were lost."))
        measurements.append(dict(name=name, silhouette=silhouette, weakest_view=weakest,
            depth_change=depth_change, bounds_change=bounds_change, components_before=geo["components"],
            components_after=new_geo["components"], major_parts=len(geo["major"]), major_parts_lost=lost_parts,
            sampled_openings=opening_count, opening_retention=opening, uv_preserved=uv_ok,
            materials_preserved=material_ok, open_edges_before=geo["split_edges"], open_edges_after=new_geo["split_edges"],
            non_manifold_before=geo["non_manifold"], non_manifold_after=new_geo["non_manifold"]))
    return measurements, failures, warnings


def preclean(obj, profile, remove_fragments, planar, preserve_uvs):
    mesh = obj.data
    geom = bmesh.new()
    stats = dict(welded_vertices=0, removed_fragments=0, removed_faces=0, removed_loose_vertices=0,
                 planar_triangles_removed=0, normals_recalculated=False)
    try:
        geom.from_mesh(mesh)
        initial = len(geom.verts)
        bounds = [max(v.co[i] for v in geom.verts) - min(v.co[i] for v in geom.verts) for i in range(3)] if initial else [0, 0, 0]
        scale = max(Vector(bounds).length, 1e-6)
        bmesh.ops.remove_doubles(geom, verts=list(geom.verts), dist=min(1e-6, scale * 1e-7))
        stats["welded_vertices"] = initial - len(geom.verts)
        zero_faces = [face for face in geom.faces if face.calc_area() <= 1e-12]
        stats["removed_faces"] = len(zero_faces)
        if zero_faces:
            bmesh.ops.delete(geom, geom=zero_faces, context="FACES_ONLY")
        if remove_fragments:
            regions = parts(geom)
            area = sum(region[2] for region in regions)
            dominant_slots = {face.material_index for faces, _, part_area, low, high in regions
                              if part_area > area * .002 or (high - low).length > scale * .035 for face in faces}
            noise = []
            removed_area = 0.
            for faces, _, part_area, low, high in sorted(regions, key=lambda part: part[2]):
                if (len(regions) > 1 and part_area < area * .001 and (high - low).length < scale * .023
                    and (removed_area + part_area) < area * .008
                    and all(face.material_index in dominant_slots for face in faces)):
                    noise.extend(faces)
                    removed_area += part_area
                    stats["removed_fragments"] += 1
            if noise:
                bmesh.ops.delete(geom, geom=noise, context="FACES_ONLY")
        loose = [edge for edge in geom.edges if not edge.link_faces]
        if loose:
            bmesh.ops.delete(geom, geom=loose, context="EDGES")
        orphans = [vertex for vertex in geom.verts if not vertex.link_faces]
        stats["removed_loose_vertices"] = len(orphans)
        if orphans:
            bmesh.ops.delete(geom, geom=orphans, context="VERTS")
        if planar and geom.faces:
            count = sum(len(face.verts) - 2 for face in geom.faces)
            delimit = {"NORMAL", "MATERIAL", "SEAM", "SHARP"}
            if preserve_uvs and mesh.uv_layers:
                delimit.add("UV")
            geom.normal_update()
            bmesh.ops.dissolve_limit(geom, angle_limit=math.radians(profile.planar_angle),
                                    use_dissolve_boundaries=False, verts=list(geom.verts),
                                    edges=list(geom.edges), delimit=delimit)
            stats["planar_triangles_removed"] = max(0, count - sum(len(face.verts) - 2 for face in geom.faces))
        if geom.faces and (any(stats.values()) or not planar):
            bmesh.ops.recalc_face_normals(geom, faces=list(geom.faces))
            stats["normals_recalculated"] = True
        if stats["normals_recalculated"]:
            geom.to_mesh(mesh)
            mesh.update()
        return stats
    finally:
        geom.free()


def features(obj, preserve_uvs):
    geom = bmesh.new()
    try:
        geom.from_mesh(obj.data)
        geom.verts.index_update()
        edges = list(geom.edges)
        length = float(np.median([edge.calc_length() for edge in edges])) if edges else 0
        protected = set()
        for edge in edges:
            faces = edge.link_faces
            crease = (len(faces) == 2 and edge.calc_length() >= length * .8
                      and (faces[0].material_index != faces[1].material_index or edge.calc_face_angle() > .46))
            if crease or edge.is_boundary or (edge.seam and preserve_uvs):
                protected.update(vertex.index for vertex in edge.verts)
        for _, vertices, area, low, high in parts(geom):
            extent = tuple(sorted(high - low))
            if area and extent[-1] > length * 3 and extent[0] < extent[-1] * .22:
                protected.update(vertex.index for vertex in vertices)
        if not protected or len(protected) >= len(geom.verts) * .94:
            return None
        group = obj.vertex_groups.new(name="Soceth structure")
        group.add(list(protected), 1, "REPLACE")
        return group
    finally:
        geom.free()


def collapse(obj, quota, profile, preserve_uvs, weighted):
    for _ in range(2):
        count = scene.triangle_count(obj.data)
        if count <= quota:
            break
        scene.select([obj])
        group = features(obj, preserve_uvs) if weighted else None
        group_name = group.name if group else None
        modifier = obj.modifiers.new("Soceth controlled collapse", "DECIMATE")
        modifier.ratio = max(.0001, min(.9999, quota / count))
        modifier.use_collapse_triangulate = True
        if group:
            modifier.vertex_group = group_name
            modifier.invert_vertex_group = True
            modifier.vertex_group_factor = profile.feature_weight
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        group = obj.vertex_groups.get(group_name) if group_name else None
        if group is not None:
            obj.vertex_groups.remove(group)
        if scene.triangle_count(obj.data) >= count:
            break


def allocate(objects, counts, target):
    # Denser small objects yield triangles earlier, but independent parts keep a base allocation.
    extents = [max(obj.dimensions) for obj in objects]
    span = max(max(extents), 1e-8)
    floors = [min(count, 12 if size >= span * .055 else 4) for count, size in zip(counts, extents)]
    if sum(floors) > target:
        return None
    weights = [math.sqrt(count) * max(size / span, .12) ** .45 for count, size in zip(counts, extents)]
    quotas = floors[:]
    remaining = min(sum(counts), target) - sum(quotas)
    while remaining:
        eligible = [i for i, count in enumerate(counts) if quotas[i] < count]
        total = sum(weights[i] for i in eligible)
        assigned = 0
        for i in eligible:
            share = min(counts[i] - quotas[i], int(remaining * weights[i] / total))
            quotas[i] += share
            assigned += share
        if assigned == 0:
            for i in sorted(eligible, key=weights.__getitem__, reverse=True)[:remaining]:
                quotas[i] += 1
                assigned += 1
        remaining -= assigned
    return quotas


def optimize(objects, target=None, ratio=None, preserve_uvs=True, *, profile="balanced",
             lod=False, project_budget=None, source_triangles=None, cleanup=False,
             remove_fragments=True, planar=True):
    if profile not in PROFILES:
        raise ValueError("Unknown mesh optimization profile.")
    settings = PROFILES["lod" if lod else profile]
    counts = [scene.evaluated_count(obj) for obj in objects]
    total = sum(counts)
    if not total or not all(counts):
        raise ValueError("Structure optimization requires a non-empty mesh for each target.")
    target = total if cleanup else target if target is not None else max(1, math.floor(total * ratio))
    vertices = sum(len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices) for obj in objects)
    baseline = []
    for obj in objects:
        view = projections(obj)
        if view is None:
            raise ValueError(f"{obj.name}: could not sample the source geometry.")
        baseline.append((obj.name, view, scene.mesh_quality(obj), [openings(depth) for depth in view[1]]))
    low_main = (not cleanup and not lod and project_budget and (source_triangles or total) >= 2 * project_budget
                and target <= project_budget * .25)
    starting_target = max(target, math.ceil(project_budget * .35)) if low_main else target
    trial_targets = sorted({min(total, math.ceil(starting_target * factor)) for factor in (1, 1.4, 1.95, 2.7, 3.6)})
    trials, prepared, working, discarded = [], [], [], []
    result = None
    try:
        if not cleanup and total <= target:
            measurements, errors, warnings = measure(baseline, objects, settings, preserve_uvs)
            result = (total, vertices, measurements, errors, warnings, [])
        else:
            for obj in objects:
                clone = obj.copy()
                clone.data = obj.data.copy()
                bpy.context.collection.objects.link(clone)
                working.append(clone)
                scene.modifiers(clone)
            cleanup_stats = [preclean(obj, settings, remove_fragments, planar, preserve_uvs) for obj in working]
            prepared = [obj.data for obj in working]
            bpy.context.view_layer.update()
            prepared_counts = [scene.triangle_count(mesh) for mesh in prepared]
            for trial in trial_targets:
                quotas = allocate(working, prepared_counts, trial) if all(prepared_counts) else None
                if not quotas:
                    continue
                for weighted in (True, False) if sum(prepared_counts) > trial else (False,):
                    for obj, mesh, quota in zip(working, prepared, quotas):
                        if obj.data not in prepared:
                            discarded.append(obj.data)
                        obj.data = mesh.copy()
                        collapse(obj, quota, settings, preserve_uvs, weighted)
                    bpy.context.view_layer.update()
                    final = sum(scene.triangle_count(obj.data) for obj in working)
                    measurements, failures, warnings = measure(baseline, working, settings, preserve_uvs)
                    if final > trial:
                        failures.append(issue("error", "target_missed", f"Measured {final:,} triangles against {trial:,}."))
                    trials.append(dict(target=trial, triangles=final, weighted=weighted,
                        failures=[entry["code"] for entry in failures],
                        silhouette=round(min((item["silhouette"] for item in measurements), default=0), 4)))
                    if not failures:
                        result = (final, sum(len(obj.data.vertices) for obj in working),
                                  measurements, failures, warnings, cleanup_stats)
                        break
                if result:
                    break

        if result is None:
            final, final_vertices, measurements = total, vertices, []
            errors = [issue("error", "structure_quality_failed",
                            "No simplification passed structural validation. The working revision was preserved.")]
            warnings, cleanup_stats = [], []
        else:
            final, final_vertices, measurements, errors, warnings, cleanup_stats = result
        fallback = final > target or bool(low_main)
        changed = bool(result and working and (final != total or final_vertices != vertices or
                       (cleanup and any(stat["normals_recalculated"] for stat in cleanup_stats))))
        if changed and not errors:
            for original, clone in zip(objects, working):
                original.data = clone.data
                original.modifiers.clear()
        if fallback and result:
            message = (f"{target:,} triangles is better suited to a distance LOD for this source." if low_main else
                       f"{target:,} triangles did not pass the {profile.replace('_', ' ')} quality checks.")
            warnings.append(issue("warning", "safer_triangle_target",
                                  f"{message} The verified safer target is {final:,} triangles."))
        elif fallback:
            warnings.append(issue("warning", "try_higher_target",
                                  f"Try a higher target or a distance LOD. The last saved mesh has {final:,} triangles."))
        borderline = any(item["bounds_change"] > settings.bounds_change * .85 or
                         item["silhouette"] < settings.silhouette + .008 for item in measurements)
        if borderline and not (errors or fallback):
            warnings.append(issue("warning", "structure_review", "Inspect the result beside the source. A structural measurement is close to its limit."))
        grade = "failed" if errors else "review" if warnings else "usable"
        return dict(original_triangles=total, final_triangles=final, target_triangles=target,
                    target_met=not errors and final <= target and not low_main, changed=changed and not errors,
                    original_vertices=vertices, final_vertices=final_vertices, object_count=len(objects),
                    reduction_percent=round((1 - final / total) * 100, 2),
                    uv_preserved=bool(measurements) and all(item["uv_preserved"] for item in measurements),
                    materials_preserved=bool(measurements) and all(item["materials_preserved"] for item in measurements),
                    preserve_uvs_requested=preserve_uvs, silhouette_overlap=round(min(
                        (item["silhouette"] for item in measurements), default=0), 4) if measurements else None,
                    profile="lod" if lod else profile, rating=grade, recommended_usage="lod" if lod else "main",
                    recommended_target=final if fallback else None, detached=bool(fallback and not lod),
                    measurements=measurements, cleanup=cleanup_stats, trials=trials, issues=errors + warnings)
    finally:
        for obj in working:
            if obj.data not in prepared:
                discarded.append(obj.data)
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in set(prepared + discarded):
            if not mesh.users:
                bpy.data.meshes.remove(mesh)
