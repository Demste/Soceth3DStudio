export type ProjectFields = { name: string; description: string; engine: string; platforms: string[] };
export type Project = { id: string; name: string; updated: string; asset_count: number; body: ProjectFields };
export type ExportPreset = { format: string; scale: number; lod_ratios: number[]; collider: string; external_textures: boolean };
export const colorPolicies = {strict: 'Strict Project Palette', guided: 'Guided Project Palette', reference: 'Reference Colors'} as const;
export type ColorPolicy = keyof typeof colorPolicies;
export const optimizationProfiles = {
  preserve_structure: 'Preserve Structure', balanced: 'Balanced', aggressive: 'Aggressive', lod: 'LOD / Distance',
} as const;
export type OptimizationProfile = keyof typeof optimizationProfiles;
export type SemanticMaterial = {
  primary_color: string; approved_colors: string[]; variations: string[]; aliases: string[];
  roughness: [number, number]; metallic: [number, number]; texture_style: 'any' | 'flat' | 'textured'; notes: string;
};
export type MaskStroke = {points: [number, number][]; radius: number};
export type Region = {material: string; category: string; strokes: MaskStroke[]};
type Issue = {level: string; code: string; message: string};
export type Compliance = {
  policy: ColorPolicy; palette: string[]; bible_version: number; geometry_style: string; geometry_budget: number | null;
  geometry_conformity: string; texture_conformity: string; palette_conformity: string; regions: Region[]; issues: Issue[];
  materials: {name: string; categories: string[]; colors: string[]; textured: boolean | null; requires_mapping?: boolean;
    texture_file?: string; original_texture_file?: string; palette_match: {category: string | null;
      fraction: number | null; oklab_distance: number | null; sample_fraction: number}[] | null}[];
};
export type Optimization = {
  original_triangles: number; final_triangles: number; target_triangles: number; target_met: boolean;
  reduction_percent: number; uv_preserved: boolean; materials_preserved: boolean;
  silhouette_overlap: number | null; issues: Issue[];
  profile?: OptimizationProfile; rating?: 'usable' | 'review' | 'failed';
  recommended_usage?: 'main' | 'lod'; recommended_target?: number | null;
  original_vertices?: number; final_vertices?: number; object_count?: number;
  measurements?: {name: string; silhouette: number; weakest_view: number; depth_change: number; bounds_change: number;
    components_before: number; components_after: number; major_parts: number; major_parts_lost: number;
    sampled_openings: number; opening_retention: number | null; open_edges_before: number; open_edges_after: number;
    non_manifold_before: number; non_manifold_after: number}[];
  cleanup?: {welded_vertices: number; removed_fragments: number; removed_faces: number; removed_loose_vertices: number;
    planar_triangles_removed: number; normals_recalculated: boolean}[];
  trials?: {target: number; triangles: number; weighted: boolean; failures: string[]}[];
};
export type Bible = {
  schema_version: 1; style: string; palette: string[]; materials: string[]; positive: string;
  color_policy: ColorPolicy; semantic_materials: Record<string, SemanticMaterial>; default_material: string | null;
  negative: string; geometry: string; triangle_budgets: Record<string, number>; texture_size: number;
  material_rules: string; meters_per_unit: number; coordinates: string; prompt_rules: string[];
  presets: Record<string, string>; export_presets: Record<string, ExportPreset>;
};
export type BibleVersion = { version: number; body: Bible };
export type Material = { name: string; base_color: number[]; metallic?: number; roughness?: number; maps: Record<string, {image: string; channel: string}> };
export type Report = {
  object_count: number; triangles: number; vertices: number; uv_present: boolean; valid: boolean;
  bounds: number[][]; objects: { id: string; name: string; triangles: number; dimensions: number[]; materials: string[] }[];
  materials: Material[]; textures: {name: string; size: number[]; missing: boolean}[];
  issues: Issue[]; art_bible?: Compliance; optimization?: Optimization[]; review_required?: boolean;
  lifecycle?: 'source' | 'candidate' | 'lod'; variant?: 'main' | 'trial' | 'lod'; structural_quality?: Optimization;
};
export type Asset = {
  id: string; project: string; name: string; category: string; current: string; updated: string;
  body: {tags: string[]; thumbnail?: string; reference?: string; source_mesh?: string; generation?: Record<string, unknown>};
};
export type Revision = {
  id: string; created: string; parent: string | null; body: {preview: string; blend: string; report_file: string;
  report: Report; provenance: {kind: string; request?: string; model?: Model; regions?: Region[]};
  approval?: {bible_version: number; category: string; review_notes: string; validation: Report}};
};
export type AssetDetail = Asset & {revisions: Revision[]; exports: {file: string; created: string; revision: string}[]};
export type Source = {id: string; kind: string; name: string; body?: {seed?: number; prompt?: string; composed_prompt?: string}};
export type Model = {
  id: string; name: string; license: string; operation: string; provider: string;
  size: number; installed: boolean; selected: boolean; vram: string;
  files: {source: string; sha256: string; license: string}[]; source?: string; installation?: {managed: boolean};
  family: string; version: string; variant: string; precision: string; capability: string;
  dependencies: string[]; commercial: string; verification: string; attribution: string; traits: string[];
  selection_scope?: string; source_type: string; compatibility?: Compatibility;
};
export type Execution = {profile: string; total_vram: number; free_vram: number; evidence: string; features: string[]; note: string};
export type Compatibility = {status: 'compatible'|'setup_required'|'incompatible'; confidence: string; reasons: string[];
  execution: Execution | null; installed: boolean; measurement?: {seconds: number; peak_system_vram: number; note: string}};
export type DownloadPlan = {download_bytes: number; models: {id: string; name: string; license: string;
  commercial: string; source: string; download_bytes: number; disk_bytes: number; installed: boolean; vram: string}[]};
export type ProviderSettings = {comfy_port: number; ollama_port: number; comfy: string; comfy_python: string};
export type ModelConfiguration = {
  catalog: Model[]; tier: string; plan: DownloadPlan; hardware_changed: boolean;
  hardware: {gpus: {name: string; capability: string; total: number; free: number; driver: string}[];
    ram: {total: number; available: number}; free_disk: number; runtime: {status: string; torch?: string; cuda?: string; error?: string}};
  recommendations: Record<string, {model: string | null; candidates: string[]; stages?: string[]}>;
  existing: {matches: string[]; roots: string[]; ollama: {tag: string; size: number; family?: string}[]};
  providers: ProviderSettings; policy: Record<string, number>;
};
export type System = {
  profile: string; profile_setting: string; gpus: {name: string; total: number; used: number; driver: string}[];
  blender: string; blender_connected: boolean; blender_production: boolean; blender_structure: boolean; comfy: boolean; ollama: boolean; last_project: string | null;
  cpu: string; cores: number; disk_free: number; token_file: string; pytorch?: string; ollama_version?: string; advanced: boolean;
};
export type Operation = {type: string; targets: string[]} & Record<string, unknown>;
export type Proposal = {intent: string; explanation: string; operations: Operation[]};
export type Job = {
  id: string; project: string | null; kind: string; status: string; stage: string; error: string | null;
  progress: number | null; updated: string; provider_id: string | null; cancel: number;
  target_asset?: string; target_revision?: string;
  result?: {preview?: string; unchanged?: boolean; warning?: string; variant?: 'main' | 'trial' | 'lod'; asset?: string; revision?: string; file?: string; report?: Report; proposal?: Proposal;
    request?: string; base?: string; confirmation?: boolean; metrics?: {seconds: number; gpu_peak_system_mib: Record<string, number>}};
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map((e: {loc: string[]; msg: string}) => e.loc.slice(1).join('.') + ' · ' + e.msg).join('\n')
      : 'Request failed (' + response.status + ')');
  }
  return response.status === 204 ? undefined as T : response.json();
}
export function json(method: string, value?: unknown): RequestInit {
  return {method, ...(value !== undefined && {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)})};
}
export function media(id?: string, download = false) {
  return id ? '/api/files/' + id + (download ? '?download=true' : '') : '';
}
export function stageActive(job: Job) { return job.status === 'running' || job.status === 'queued'; }
export function gib(bytes: number) { return (bytes / 1024 ** 3).toFixed(1) + ' GiB'; }
export function split(value: string) { return value.split(',').map(item => item.trim()).filter(Boolean); }
export function time(value: string) { return new Date(value).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'}); }

