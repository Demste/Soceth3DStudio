import { FormEvent, useState } from 'react';
import { BookOpen, Download, FolderOpen, Plus, Save, Upload, X } from 'lucide-react';
import { Bible, BibleVersion, ExportPreset, Project, ProjectFields, SemanticMaterial, Source, colorPolicies, media, split, time } from './api';

const initialProject: ProjectFields = {name: '', description: '', engine: 'Unity 6 URP', platforms: ['Windows']};

export function ProjectForm({project, onSubmit, onClose}: {
  project?: Project; onSubmit: (value: ProjectFields) => Promise<void>; onClose: () => void;
}) {
  const [value, setValue] = useState(project?.body ?? initialProject);
  const [platforms, setPlatforms] = useState(value.platforms.join(', '));
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try { await onSubmit({...value, platforms: split(platforms)}); }
    finally { setBusy(false); }
  }
  return <div className="dialog-shade" onClick={onClose}><form className="dialog stack" onClick={e => e.stopPropagation()} onSubmit={submit}>
    <div className="row between"><h2>{project ? 'Project settings' : 'Create project'}</h2><button type="button" className="icon" aria-label="Close" onClick={onClose}><X size={20}/></button></div>
    <label>Project name<input autoFocus required maxLength={160} value={value.name} onChange={e => setValue({...value, name: e.target.value})}/></label>
    <label>Description<textarea value={value.description} onChange={e => setValue({...value, description: e.target.value})}/></label>
    <div className="two-col"><label>Target engine<input required value={value.engine} onChange={e => setValue({...value, engine: e.target.value})}/></label>
      <label>Target platforms<input value={platforms} onChange={e => setPlatforms(e.target.value)}/></label></div>
    <div className="row end"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{project ? <Save/> : <Plus/>}{project ? 'Save changes' : 'Create project'}</button></div>
  </form></div>;
}

export function ProjectList({projects, current, choose, create, edit}: {
  projects: Project[]; current?: string; choose: (id: string) => void; create: () => void; edit: (project: Project) => void;
}) {
  return <div className="tile-grid">
    {projects.map(project => <article className={'project-tile panel' + (project.id === current ? ' selected' : '')} key={project.id}>
      <FolderOpen size={23}/><h3>{project.name}</h3>
      <p>{project.body.description || project.body.engine}</p>
      <div className="small muted">{project.asset_count ?? 0} assets · {time(project.updated)}</div>
      <div className="row"><button onClick={() => choose(project.id)}>Open project</button><button onClick={() => edit(project)}>Settings</button></div>
    </article>)}
    <button className="empty-tile" onClick={create}><Plus size={24}/>New project</button>
  </div>;
}

function PairEditor({rows, onChange, numbers = false}: {
  rows: [string, string][]; onChange: (value: [string, string][]) => void; numbers?: boolean;
}) {
  return <div className="stack tight">{rows.map(([key, value], i) => <div className="pair-row" key={i}>
    <input aria-label="Name" placeholder="Name" value={key} onChange={e => onChange(rows.map((pair, j) => i === j ? [e.target.value, value] : pair))}/>
    <input aria-label={numbers ? 'Triangle budget' : 'Prompt rule'} type={numbers ? 'number' : 'text'} min={numbers ? 12 : undefined} value={value}
      onChange={e => onChange(rows.map((pair, j) => i === j ? [key, e.target.value] : pair))}/>
    <button className="icon" title="Remove entry" type="button" onClick={() => onChange(rows.filter((_, j) => i !== j))}><X size={16}/></button>
  </div>)}<button type="button" className="compact" onClick={() => onChange([...rows, ['', numbers ? '5000' : '']])}><Plus size={15}/>Add entry</button></div>;
}

const emptyMaterial: SemanticMaterial = {primary_color: '#78917f', approved_colors: [], variations: [],
  aliases: [], roughness: [0, 1], metallic: [0, 1], texture_style: 'any', notes: ''};

export function ArtBible({entry, references, save, upload, importConfig, exportConfig, error}: {
  entry: BibleVersion; references: Source[]; save: (value: Bible) => void; upload: (file: File) => void;
  importConfig: (data: unknown) => void; exportConfig: () => void; error: (message: string) => void;
}) {
  const [value, setValue] = useState(entry.body);
  const [budgets, setBudgets] = useState(Object.entries(value.triangle_budgets).map(([key, n]): [string, string] => [key, String(n)]));
  const [vertexBudgets, setVertexBudgets] = useState(Object.entries(value.vertex_budgets ?? {}).map(([key, n]): [string, string] => [key, String(n)]));
  const [presets, setPresets] = useState(Object.entries(value.presets));
  const [exports, setExports] = useState(Object.entries(value.export_presets));
  const [semantic, setSemantic] = useState(Object.entries(entry.body.semantic_materials ?? {}));
  const [materials, setMaterials] = useState(value.materials.join(', '));
  const [rules, setRules] = useState(value.prompt_rules.join('\n'));
  const [texture, setTexture] = useState(String(value.texture_size));
  const change = <K extends keyof Bible>(key: K, next: Bible[K]) => setValue({...value, [key]: next});
  function submit(event: FormEvent) {
    event.preventDefault();
    if ([budgets, vertexBudgets, presets, exports, semantic].some(rows => {
      const names = rows.map(([key]) => key.trim());
      return names.some(name => !name) || new Set(names).size !== names.length;
    })) {error('Give every budget, preset, and semantic material a unique name.'); return;}
    const semantic_materials = Object.fromEntries(semantic.map(([name, rule]) => [name.trim(), rule]));
    save({...value, semantic_materials, default_material: value.default_material?.trim() || null, triangle_budgets: Object.fromEntries(budgets.map(([key, n]) => [key.trim(), Number(n)])),
      vertex_budgets: Object.fromEntries(vertexBudgets.map(([key, n]) => [key.trim(), Number(n)])),
      presets: Object.fromEntries(presets), export_presets: Object.fromEntries(exports),
      materials: split(materials), prompt_rules: rules.split('\n').map(v => v.trim()).filter(Boolean), texture_size: Number(texture)});
  }
  return <form className="stack" onSubmit={submit}>
    <div className="screen-heading"><div><span className="eyebrow">PROJECT STANDARDS</span><h1>Art Bible</h1><p>Version {entry.version} · Schema {entry.body.schema_version}</p></div>
      <div className="row">
        <label className="button"><Upload size={16}/>Import JSON<input type="file" accept=".json" hidden onChange={async e => {
          const file = e.target.files?.[0]; if (!file) return;
          try {importConfig(JSON.parse(await file.text()));} catch {error('This file is not valid Art Bible JSON.');}
        }}/></label>
        <button type="button" onClick={exportConfig}><Download size={16}/>Export JSON</button><button className="primary"><Save size={16}/>Save version</button>
      </div>
    </div>
    <div className="two-col bible-grid">
      <section className="panel stack"><h2><BookOpen size={18}/>Art direction</h2>
        <label>Art style<textarea value={value.style} onChange={e => change('style', e.target.value)}/></label>
        <label>Geometry style<input value={value.geometry} onChange={e => change('geometry', e.target.value)}/></label>
        <label>Material preferences<input value={materials} onChange={e => setMaterials(e.target.value)}/></label>
        <label>PBR material rules<textarea value={value.material_rules} onChange={e => change('material_rules', e.target.value)}/></label>
        <label>Color policy<select value={value.color_policy ?? 'reference'} onChange={e => change('color_policy', e.target.value as Bible['color_policy'])}>
          {Object.entries(colorPolicies).map(([policy, label]) => <option key={policy} value={policy}>{label}</option>)}
        </select></label>
        <div className="stack tight"><span className="label">Color palette</span><div className="swatch-grid">{value.palette.map((color, i) => <div className="swatch" key={i}>
          <input aria-label={'Swatch ' + (i+1)} type="color" value={/^#[0-9a-f]{6}$/i.test(color) ? color : '#000000'} onChange={e => change('palette', value.palette.map((old, j) => i === j ? e.target.value : old))}/>
          <input aria-label="HEX color" value={color} maxLength={7} pattern="#[a-fA-F0-9]{6}" onChange={e => change('palette', value.palette.map((old, j) => i === j ? e.target.value : old))}/>
          <button type="button" className="icon" title="Remove swatch" onClick={() => change('palette', value.palette.filter((_, j) => i !== j))}><X size={14}/></button>
        </div>)}</div><button type="button" className="compact" onClick={() => change('palette', [...value.palette, '#78917F'])}><Plus size={15}/>Add color</button></div>
      </section>
      <section className="panel stack"><h2>Semantic materials</h2>
        <label>Fallback material<select value={value.default_material ?? ''} onChange={e => change('default_material', e.target.value || null)}>
          <option value="">Project palette</option>{semantic.filter(([name]) => name.trim()).map(([name], i) => <option key={i}>{name}</option>)}
        </select></label>
        {semantic.map(([name, rule], i) => <SemanticEditor key={i} name={name} value={rule}
          onChange={(next, updated) => {
            setSemantic(rows => rows.map((row, j) => i === j ? [next, updated] : row));
            if (name && value.default_material === name) change('default_material', next || null);
          }}
          remove={() => {
            setSemantic(rows => rows.filter((_, j) => i !== j));
            if (value.default_material === name) change('default_material', null);
          }}/>)}
        <button type="button" className="compact" onClick={() => setSemantic([...semantic, ['', emptyMaterial]])}><Plus size={15}/>Add material category</button>
      </section>
      <section className="panel stack"><h2>Production targets</h2>
        <div className="two-col"><label>Final texture limit<select value={texture} onChange={e => setTexture(e.target.value)}>{[256, 512, 1024, 2048, 4096].map(n => <option key={n} value={n}>{n} px</option>)}</select></label>
          <label>Meters per source unit<input type="number" min="0.002" max="100" step="0.001" value={value.meters_per_unit} onChange={e => change('meters_per_unit', Number(e.target.value))}/></label></div>
        <label>Coordinate standard<input readOnly value={value.coordinates}/></label>
        <span className="label">Maximum triangles by category</span><PairEditor rows={budgets} onChange={setBudgets} numbers/>
        <span className="label">Maximum Blender vertices by category</span><PairEditor rows={vertexBudgets} onChange={setVertexBudgets} numbers/>
        <p className="small">The vertex budget is checked during validation. Optimization targets triangles, and UV seams can split vertices in Unity.</p>
        <h3>Export profiles</h3>
        {exports.map(([name, preset], i) => <ExportEditor key={i} name={name} value={preset}
          onChange={(key, row) => setExports(exports.map((pair, j) => i === j ? [key, row] : pair))}
          remove={() => setExports(exports.filter((_, j) => i !== j))}/>)}
        <button type="button" className="compact" onClick={() => setExports([...exports, ['', {format:'unity', scale:1, lod_ratios:[0.5,0.25], collider:'box', external_textures:true}]])}><Plus size={15}/>Add export preset</button>
      </section>
      <section className="panel stack"><h2>Prompt constraints</h2>
        <label>Positive<textarea value={value.positive} onChange={e => change('positive', e.target.value)}/></label>
        <label>Negative<textarea value={value.negative} onChange={e => change('negative', e.target.value)}/></label>
        <label>Reusable rules, one per line<textarea value={rules} onChange={e => setRules(e.target.value)}/></label>
        <h3>Generation presets</h3><PairEditor rows={presets} onChange={setPresets}/>
      </section>
      <section className="panel stack"><div className="row between"><h2>Reference gallery</h2><label className="button"><Upload size={15}/>Add image<input type="file" hidden accept="image/png,image/jpeg,image/webp" onChange={e => {if (e.target.files?.[0]) upload(e.target.files[0]);}}/></label></div>
        <div className="reference-grid">{references.filter(item => item.kind === 'reference').map(item => <figure key={item.id}><img src={media(item.id)} alt={item.name}/><figcaption>{item.name}</figcaption></figure>)}</div>
      </section>
    </div>
  </form>;
}

function SemanticEditor({name, value, onChange, remove}: {
  name: string; value: SemanticMaterial; onChange: (name: string, value: SemanticMaterial) => void; remove: () => void;
}) {
  const update = <K extends keyof SemanticMaterial>(key: K, next: SemanticMaterial[K]) =>
    onChange(name, {...value, [key]: next});
  return <details className="preset"><summary>{name || 'New material category'}</summary>
    <div className="stack tight detail-body">
      <div className="row"><label>Category name<input required value={name} onChange={e => onChange(e.target.value, value)}/></label>
        <button type="button" className="icon" title="Remove material category" onClick={remove}><X size={16}/></button></div>
      <label>Primary color<input type="color" value={value.primary_color} onChange={e => update('primary_color', e.target.value)}/></label>
      <ListField label="Approved HEX colors" values={value.approved_colors} update={next => update('approved_colors', next)}/>
      <ListField label="Guided variations (HEX)" values={value.variations} update={next => update('variations', next)}/>
      <ListField label="Material name aliases" values={value.aliases} update={next => update('aliases', next)}/>
      {(['roughness', 'metallic'] as const).map(channel => <div className="two-col" key={channel}>
        <label>Minimum {channel}<input type="number" required min={0} max={1} step="any" value={value[channel][0]}
          onChange={e => update(channel, [Number(e.target.value), value[channel][1]])}/></label>
        <label>Maximum {channel}<input type="number" required min={0} max={1} step="any" value={value[channel][1]}
          onChange={e => update(channel, [value[channel][0], Number(e.target.value)])}/></label>
      </div>)}
      <label>Base-color texture rule<select value={value.texture_style} onChange={e => update('texture_style', e.target.value as SemanticMaterial['texture_style'])}>
        <option value="any">Any</option><option value="textured">Textured</option><option value="flat">Flat color</option>
      </select></label>
      <label>Notes<textarea value={value.notes} onChange={e => update('notes', e.target.value)}/></label>
    </div>
  </details>;
}

function ListField({label, values, update}: {label: string; values: string[]; update: (value: string[]) => void}) {
  const [text, setText] = useState(values.join(', '));
  return <label>{label}<input value={text} onChange={e => {setText(e.target.value); update(split(e.target.value));}}/></label>;
}

function ExportEditor({name, value, onChange, remove}: {name: string; value: ExportPreset; onChange: (name: string, value: ExportPreset) => void; remove: () => void}) {
  const [ratios, setRatios] = useState(value.lod_ratios.join(', '));
  return <div className="preset stack tight">
    <div className="row"><input aria-label="Preset name" placeholder="Preset name" value={name} onChange={e => onChange(e.target.value, value)}/><button type="button" className="icon" title="Remove preset" onClick={remove}><X size={16}/></button></div>
    <div className="two-col"><label>Format<select value={value.format} onChange={e => onChange(name, {...value, format: e.target.value})}>{['unity', 'glb', 'gltf', 'fbx'].map(item => <option key={item}>{item}</option>)}</select></label>
      <label>Export scale<input type="number" min="0.002" max="100" step="0.001" value={value.scale} onChange={e => onChange(name, {...value, scale: Number(e.target.value)})}/></label></div>
    <div className="two-col"><label>LOD ratios<input value={ratios} onChange={e => {setRatios(e.target.value); onChange(name, {...value, lod_ratios: split(e.target.value).map(Number)});}}/></label>
      <label>Collider<select value={value.collider} onChange={e => onChange(name, {...value, collider:e.target.value})}>{['none', 'box', 'sphere', 'capsule', 'mesh'].map(item => <option key={item}>{item}</option>)}</select></label></div>
    <label className="check"><input type="checkbox" checked={value.external_textures} onChange={e => onChange(name, {...value, external_textures: e.target.checked})}/>External PBR textures</label>
  </div>;
}

