import { PointerEvent, useEffect, useRef, useState } from 'react';
import { Bible, Compliance, MaskStroke, Optimization, Region, colorPolicies, media, optimizationProfiles } from './api';

export function OptimizationResult({result}: {result: Optimization}) {
  return <div className="proposal stack tight">
    <strong>{result.rating === 'failed' ? 'Failed quality check' : result.rating === 'review' ? 'Review required' :
      result.target_met ? 'Usable structure' : 'Triangle target needs review'}</strong>
    {result.profile && <p className="small">{optimizationProfiles[result.profile]} · {result.recommended_usage === 'lod' ? 'Distance LOD' : 'Main asset'}
      {result.recommended_target && ' · Safer target ' + result.recommended_target.toLocaleString()}</p>}
    <p className="small">Original {result.original_triangles.toLocaleString()} · Final {result.final_triangles.toLocaleString()} · Target {result.target_triangles.toLocaleString()}</p>
    <p className="small">Reduction {result.reduction_percent}% · UV {result.uv_preserved ? 'checks passed' : 'review'} · Material assignments {result.materials_preserved ? 'retained' : 'review'}</p>
    {result.silhouette_overlap !== null && <p className="small">Projected silhouette agreement {(result.silhouette_overlap * 100).toFixed(1)}%</p>}
    {result.original_vertices !== undefined && <p className="small">Vertices {result.original_vertices.toLocaleString()} → {result.final_vertices?.toLocaleString()} · Objects {result.object_count}</p>}
    {!!result.measurements?.length && <details><summary>Structural measurements</summary>
      {result.measurements.map(part => <div key={part.name} className="stack tight detail-body">
        <strong>{part.name}</strong>
        <p className="small">Parts {part.components_before} → {part.components_after} · Major parts preserved {part.major_parts - part.major_parts_lost}/{part.major_parts}</p>
        <p className="small">Bounds shifted {(part.bounds_change * 100).toFixed(1)}% · Visible depths changed {(part.depth_change * 100).toFixed(1)}%</p>
        <p className="small">{part.sampled_openings ? 'Enclosed opening projections ' + part.sampled_openings +
          ' · Lowest retention ' + ((part.opening_retention ?? 0) * 100).toFixed(1) + '%' : 'No enclosed through-openings detected in sampled views'}</p>
        <p className="small">Open edges {part.open_edges_before} → {part.open_edges_after} · Non-manifold junctions {part.non_manifold_before} → {part.non_manifold_after}</p>
      </div>)}</details>}
    {!!result.cleanup?.length && <p className="small">Cleanup removed {result.cleanup.reduce((n, part) => n + part.removed_fragments, 0)} tiny fragments,
      {' '}{result.cleanup.reduce((n, part) => n + part.removed_faces, 0)} degenerate faces and
      {' '}{result.cleanup.reduce((n, part) => n + part.planar_triangles_removed, 0)} triangles through planar dissolve.</p>}
    {!result.target_met && result.trials?.some(trial => trial.failures.length > 0) &&
      <details><summary>Rejected reductions</summary>{result.trials.filter(trial => trial.failures.length > 0).map((trial, i) =>
        <p className="small" key={i}>{trial.triangles.toLocaleString()} triangles · {trial.failures.map(code => code.replaceAll('_', ' ')).join(', ')}</p>)}</details>}
    {result.issues.map(issue => <p className={issue.level === 'error' ? 'small error-text' : 'small'} key={issue.message}>{issue.message}</p>)}
  </div>;
}

export function ArtCompliance({report, bible, version}: {report?: Compliance; bible: Bible; version: number}) {
  return <details open>
    <summary>Art Bible Compliance</summary>
    <div className="stack tight detail-body">
      <p className="small">{colorPolicies[bible.color_policy]} · Art Bible v{version}</p>
      <Swatches values={bible.palette}/>
      {report ? <>
        {report.bible_version !== version && <p className="small">These results use Art Bible v{report.bible_version}. Validate against the current version.</p>}
        <p className="small">Palette {report.palette_conformity} · Geometry budget {report.geometry_conformity}
          {report.geometry_budget && ' (' + report.geometry_budget.toLocaleString() + ')'} · Texture policy {report.texture_conformity}</p>
        <p className="small muted">Geometry style {report.geometry_style} · Review visually.</p>
        <p className="small muted">Colors are measured from authored base color. Viewport lighting affects rendered appearance.</p>
        {report.materials.map(mat => <div key={mat.name} className="material stack tight">
          <strong>{mat.name}</strong>
          <p className="small">{mat.textured ? 'Base-color texture' : mat.textured === false ? 'Base-color property' : 'Shader review'}
            {' · '}{mat.categories.join(', ') || 'Unassigned'}{mat.requires_mapping && ' · Mapping needs review'}</p>
          <Swatches values={mat.colors}/>
          {mat.palette_match?.map((match, i) => <p key={i} className="small">
            {match.category || 'Project palette'} · {match.fraction === null ? 'Unmeasured' : (match.fraction * 100).toFixed(1) + '% conforming samples'}
          </p>)}
        </div>)}
        {report.issues.map((issue, i) => <p className="small error-text" key={i}>{issue.message}</p>)}
      </> : <p className="small muted">Validate this revision to measure its current colors and texture policy.</p>}
    </div>
  </details>;
}

function Swatches({values}: {values: string[]}) {
  return <div className="color-chips">{values.map((hex, i) => <span key={i}><i style={{backgroundColor: hex}}/>{hex}</span>)}</div>;
}

type Point = [number, number];

export function RegionEditor({report, bible, regions, change}: {
  report: Compliance; bible: Bible; regions: Region[]; change: (value: Region[]) => void;
}) {
  const [material, setMaterial] = useState(report.materials[0]?.name ?? '');
  const categories = Object.keys(bible.semantic_materials);
  const [category, setCategory] = useState(categories[0] ?? '');
  const [radius, setRadius] = useState(0.055);
  const [points, setPoints] = useState<Point[]>([]);
  const [image, setImage] = useState<HTMLImageElement>();
  const canvas = useRef<HTMLCanvasElement>(null);
  const drawing = useRef<Point[]>([]);
  const surface = report.materials.find(item => item.name === material);
  const textureUrl = media(surface?.original_texture_file || surface?.texture_file);
  const usedPoints = regions.reduce((total, region) => total + region.strokes.reduce((n, stroke) => n + stroke.points.length, 0), 0);
  const last = regions.at(-1);
  const extendStroke = last?.category === category && last.material === material && last.strokes.length > 0 && last.strokes.length < 50;
  const canPaint = Boolean(material && category && image && (extendStroke || regions.length < 24) && usedPoints < 6000);
  function add(stroke?: MaskStroke) {
    if (stroke && extendStroke && last) {
      change([...regions.slice(0, -1), {...last, strokes: [...last.strokes, stroke]}]);
    } else if (material && category && regions.length < 24) {
      change([...regions, {material, category, strokes: stroke ? [stroke] : []}]);
    }
  }
  function undo() {
    const earlier = regions.slice(0, -1);
    change(last && last.strokes.length > 1 ? [...earlier, {...last, strokes: last.strokes.slice(0, -1)}] : earlier);
  }
  useEffect(() => {
    setImage(undefined);
    if (!textureUrl) return;
    const next = new Image();
    next.onload = () => setImage(next);
    next.src = textureUrl;
    return () => {next.onload = null;};
  }, [textureUrl]);
  useEffect(() => {
    const target = canvas.current;
    const context = target?.getContext('2d');
    if (!image || !target || !context) return;
    const scale = Math.min(1, 768 / Math.max(image.width, image.height));
    const width = target.width = Math.max(1, Math.round(image.width * scale));
    const height = target.height = Math.max(1, Math.round(image.height * scale));
    context.drawImage(image, 0, 0, width, height);
    const draw = (stroke: MaskStroke, color: string) => {
      if (!stroke.points.length) return;
      const [u, v] = stroke.points[0];
      context.lineCap = 'round';
      context.lineJoin = 'round';
      context.strokeStyle = color;
      context.lineWidth = 2 * stroke.radius * Math.min(width, height);
      context.beginPath();
      context.moveTo(u * width, v * height);
      if (stroke.points.length === 1) context.lineTo(u * width + .01, v * height);
      for (const [x, y] of stroke.points.slice(1)) context.lineTo(x * width, y * height);
      context.stroke();
    };
    context.globalAlpha = .46;
    for (const region of regions.filter(item => item.material === material)) {
      const color = bible.semantic_materials[region.category]?.primary_color || '#ffffff';
      if (region.strokes.length) region.strokes.forEach(stroke => draw(stroke, color));
      else {context.fillStyle = color; context.fillRect(0, 0, width, height);}
    }
    draw({points, radius}, bible.semantic_materials[category]?.primary_color || '#ffffff');
  }, [image, regions, material, points, radius, category, bible.semantic_materials]);

  function point(event: PointerEvent<HTMLCanvasElement>): Point {
    const bounds = event.currentTarget.getBoundingClientRect();
    const unit = (value: number) => Math.max(0, Math.min(1, value));
    return [unit((event.clientX - bounds.left) / bounds.width), unit((event.clientY - bounds.top) / bounds.height)];
  }
  function stop(event: PointerEvent<HTMLCanvasElement>) {
    if (drawing.current.length) {
      const path = drawing.current;
      add({points: path, radius});
      drawing.current = [];
      setPoints([]);
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  return <details><summary>Assign material or paint texture regions</summary>
    <div className="stack tight detail-body">
      {!categories.length ? <p className="small">Add semantic materials in the Art Bible to assign a category.</p> : <>
        <label>Asset material<select value={material} onChange={e => setMaterial(e.target.value)}>
          {report.materials.map(item => <option key={item.name}>{item.name}</option>)}
        </select></label>
        <label>Art Bible category<select value={category} onChange={e => setCategory(e.target.value)}>
          {categories.map(item => <option key={item}>{item}</option>)}
        </select></label>
        <button disabled={!material || !category || regions.length >= 24} onClick={() => add()}>Assign whole material</button>
        {image && <>
          <p className="small">Paint over the source atlas. The latest assignment takes priority where masks overlap.</p>
          <label>Brush radius<input type="range" min=".01" max=".25" step=".005" value={radius} onChange={e => setRadius(Number(e.target.value))}/></label>
          <canvas className="mask-canvas" ref={canvas} aria-label="Paint a texture region"
            onPointerDown={event => {
              if (!canPaint || !event.isPrimary || event.button !== 0) return;
              event.currentTarget.setPointerCapture(event.pointerId);
              drawing.current = [point(event)]; setPoints(drawing.current);
            }}
            onPointerMove={event => {
              if (!drawing.current.length || drawing.current.length >= Math.min(512, 6000 - usedPoints)) return;
              drawing.current = [...drawing.current, point(event)]; setPoints(drawing.current);
            }}
            onPointerUp={stop} onPointerCancel={() => {drawing.current = []; setPoints([]);}}/>
        </>}
        {surface?.textured && !textureUrl && <p className="small">Validate Against Art Bible to load this revision's atlas for painting.</p>}
        {regions.length > 0 && <div className="stack tight">
          <button onClick={undo}>Undo last mapping</button>
          {regions.map((region, i) => <div key={i} className="row between">
            <span className="small">{region.material} · {region.category} · {region.strokes.length ? 'Mask' : 'Whole'}</span>
            <button aria-label={'Remove mapping ' + (i + 1)} onClick={() => change(regions.filter((_, j) => i !== j))}>Remove</button>
          </div>)}
        </div>}
        {regions.length >= 24 && <p className="small">Remove a mapping to add another.</p>}
      </>}
    </div>
  </details>;
}
