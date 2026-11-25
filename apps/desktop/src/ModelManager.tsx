import { useEffect, useState } from 'react';
import { Download, FolderOpen, Search, Trash2 } from 'lucide-react';
import { DownloadPlan, Job, Model, ModelConfiguration, ProviderSettings, System, api, gib, json, media, stageActive } from './api';
import { Hardware, JobList } from './StudioPanels';

const capabilities: Record<string, string> = {
  generate: 'Image to 3D', concept: 'Text to image', interpret: 'Local AI editing',
  preprocess: 'Image preprocessing', mesh: 'Mesh processing', text_to_3d: 'Text to 3D',
};
type Picker = Window & {pywebview?: {api: {choose_path(kind: string): Promise<string | null>}}};

function pickPath(kind: 'gguf' | 'directory' | 'python', select: (value: string) => void, error: (value: unknown) => void) {
  void (window as Picker).pywebview?.api.choose_path(kind).then(value => {if (value) select(value);}).catch(error);
}

export function ModelManager({configuration, system, jobs, project, refresh, queue, watch, changeProfile, error, cancel, retry}: {
  configuration: ModelConfiguration; system: System | null; jobs: Job[]; project?: string;
  refresh: () => Promise<void>; queue: (url: string, request: RequestInit) => void; watch: (ids: string[]) => void;
  changeProfile: (profile: string) => void; error: (value: unknown) => void;
  cancel: (id: string) => void; retry: (id: string) => void;
}) {
  const [capability, setCapability] = useState('');
  const [filter, setFilter] = useState('');
  const [text, setText] = useState('');
  const [plan, setPlan] = useState<DownloadPlan>();
  const [scope, setScope] = useState<'global'|'project'>('global');
  const [remove, setRemove] = useState<string>();
  const busy = jobs.some(job => !job.project && stageActive(job));
  const recommended = Object.values(configuration.recommendations).flatMap(row => row.model ? [row.model] : []);
  const candidates = new Set(Object.values(configuration.recommendations).flatMap(row => row.candidates));
  const rows = configuration.catalog.filter(model => (capability === 'text_to_3d' ?
    ['generate','concept'].includes(model.operation) : !capability || capability === model.operation) &&
    [model.name, model.family, model.variant].join(' ').toLowerCase().includes(text.toLowerCase()) &&
    (filter === 'installed' ? model.installed : filter === 'compatible' ? model.compatibility?.status === 'compatible' :
     filter === 'recommended' ? recommended.includes(model.id) : true));
  const families = new Map<string, Model[]>();
  for (const model of rows) {
    const family = families.get(model.family) ?? [];
    family.push(model);
    families.set(model.family, family);
  }
  function offer(ids: string[]) {
    void api<DownloadPlan>('/models/plan', json('POST', {models: ids})).then(setPlan).catch(error);
  }
  function apply() {
    if (!plan) return;
    void api<{jobs: string[]}>('/models/install-plan', json('POST', {models: plan.models.map(row => row.id)}))
      .then(result => {setPlan(undefined); watch(result.jobs); return refresh();}).catch(error);
  }
  function setModel(model: string | null, operation: string) {
    const body = {model, operation, ...(scope === 'project' && project && {project})};
    void api('/models/selection', json('PUT', body)).then(refresh).catch(error);
  }
  return <div className="stack">
    <div className="screen-heading"><div><h1>Model manager</h1><p>Local models for generation and Blender editing</p></div></div>
    <section className="panel stack">
      <div className="row between wrap"><h2>Recommended for Your Hardware</h2><span className="pill green">{configuration.tier.replaceAll('_', ' ')}</span></div>
      {configuration.hardware.gpus.map(gpu => <p key={gpu.name + gpu.capability} className="small">
        {gpu.name} · compute {gpu.capability} · {gpu.free.toLocaleString()} of {gpu.total.toLocaleString()} MiB free · driver {gpu.driver}
      </p>)}
      <p className="small">{configuration.hardware.ram.total.toLocaleString()} MiB RAM · {gib(configuration.hardware.free_disk)} available · CUDA {configuration.hardware.runtime.status}
        {configuration.hardware.runtime.torch && ' · PyTorch ' + configuration.hardware.runtime.torch}</p>
      {configuration.hardware_changed && <div className="row wrap"><p>Hardware changed. Review the available configurations.</p>
        <button onClick={() => void api('/model-configuration/dismiss', json('POST')).then(refresh).catch(error)}>Keep current choices</button></div>}
      {['generate','concept','interpret'].map(kind => {
        const choice = configuration.recommendations[kind];
        const row = configuration.catalog.find(model => model.id === choice?.model);
        const chosen = configuration.catalog.find(model => model.operation === kind && model.selected);
        return <div className="material stack tight" key={kind}><strong>{capabilities[kind]} · {row?.name || 'No matching configuration'}</strong>
          {row && <p className="small">{row.compatibility?.execution?.features.join(' · ')} · {row.compatibility?.confidence}
            {row.compatibility?.status === 'setup_required' && ' · Provider setup needed'}</p>}
          {chosen?.id !== row?.id && chosen && <p className="small">Active {chosen.selection_scope} choice · {chosen.name}</p>}
          {choice?.candidates.length > 0 && <p className="small muted">Candidates for comparison · {choice.candidates.map(id => configuration.catalog.find(item => item.id === id)?.name).join(', ')}</p>}
        </div>;
      })}
      <div className="row wrap"><button className="primary" disabled={busy || !configuration.plan.models.length}
        onClick={() => offer(configuration.plan.models.map(row => row.id))}>Apply Recommended Configuration</button>
        <button onClick={() => void refresh().catch(error)}>Refresh hardware and providers</button></div>
      {plan && <div className="proposal stack tight" aria-label="Download plan">
        <h3>Download plan · {gib(plan.download_bytes)}</h3>
        {plan.models.map(row => <div key={row.id} className="material">
          <strong>{row.name}</strong><p className="small">{gib(row.download_bytes)} download · {gib(row.disk_bytes)} storage · {row.license} · {row.commercial}</p>
          <p className="small">{row.vram}</p>{/^https:\/\//.test(row.source) && <a href={row.source}>Upstream terms and source</a>}
        </div>)}
        <div className="row wrap"><button onClick={() => setPlan(undefined)}>Close</button>
          {plan.models.some(row => !row.installed) && <button className="primary" disabled={busy} onClick={apply}>Confirm model installation</button>}</div>
      </div>}
    </section>
    <section className="panel stack">
      <div className="row wrap">
        <label className="search"><Search size={16}/><input aria-label="Search models" placeholder="Search models" value={text} onChange={e => setText(e.target.value)}/></label>
        <label>Capability<select value={capability} onChange={e => setCapability(e.target.value)}><option value="">All capabilities</option>
          {Object.entries(capabilities).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
        <label>Availability<select value={filter} onChange={e => setFilter(e.target.value)}><option value="">All models</option>
          <option value="installed">Installed</option><option value="compatible">Compatible</option><option value="recommended">Recommended</option></select></label>
        <label>Default scope<select value={scope} onChange={e => setScope(e.target.value as typeof scope)}>
          <option value="global">All projects</option><option value="project" disabled={!project}>Current project</option></select></label>
      </div>
      {Array.from(families).map(([family, grouped]) => <section className="stack tight" key={family}>
        <h3>{family}</h3><div className="model-grid">{grouped?.map(model => {
          const compatible = model.compatibility?.status === 'compatible';
          const selectable = ['generate','concept','interpret'].includes(model.operation);
          const selected = model.selected && model.selection_scope === scope;
          const status = model.compatibility?.status === 'incompatible' ? 'Incompatible' : model.installed ? 'Installed' : 'Available';
          return <article className="panel model-card" key={model.id}>
            <div className="row between wrap"><strong>{model.variant}</strong><span className={compatible && model.installed ? 'pill green' : 'pill'}>{status}</span></div>
            <p className="small">{capabilities[model.operation]} · {model.source_type} · {model.compatibility?.execution?.profile || 'No profile'}</p>
            <p className="small">{model.license} · {gib(model.size)} · {model.vram}</p>
            {recommended.includes(model.id) && <span className="pill green">Recommended</span>}
            {candidates.has(model.id) && <span className="pill">Comparison candidate</span>}
            <div className="row wrap">
              {selectable && model.installed && <button disabled={!compatible || selected} onClick={() => setModel(model.id, model.operation)}>{selected ? 'Selected' : 'Select'}</button>}
              {selectable && !model.installed && model.source_type === 'Managed' && <button disabled={busy || model.compatibility?.status === 'incompatible'}
                onClick={() => offer([model.id])}><Download size={15}/>Download Model</button>}
              {selected && <button onClick={() => setModel(null, model.operation)}>Clear override</button>}
              {system?.advanced && model.installation?.managed && <button disabled={busy} aria-label={'Remove ' + model.name} onClick={() => setRemove(model.id)}><Trash2 size={15}/></button>}
            </div>
            {remove === model.id && <div className="row wrap"><button onClick={() => setRemove(undefined)}>Keep model</button>
              <button className="danger" onClick={() => {queue('/models/' + model.id, json('DELETE')); setRemove(undefined);}}>Remove managed files</button></div>}
            <details><summary>Compatibility and evidence</summary><div className="stack tight detail-body">
              {model.compatibility?.reasons.map(reason => <p key={reason} className="small">{reason}</p>)}
              {model.compatibility?.measurement && <p className="small">Prior measured run · {model.compatibility.measurement.seconds} s ·
                {' '}{model.compatibility.measurement.peak_system_vram} MiB system GPU peak. {model.compatibility.measurement.note}</p>}
              {system?.advanced && <><p className="small">{model.provider} · {model.precision} · {model.dependencies.join(', ')}</p>
                <p className="small">{model.attribution}</p>{/^https:\/\//.test(model.source ?? '') && <a href={model.source}>Model source</a>}</>}
            </div></details>
          </article>;
        })}</div>
      </section>)}
      {!rows.length && <p>No models match these filters.</p>}
    </section>
    <LocalModels configuration={configuration} queue={queue} busy={busy} error={error}/>
    {jobs.some(job => !job.project) && <section className="panel stack tight"><h2>Model jobs</h2>
      <JobList jobs={jobs.filter(job => !job.project).slice(0, 6)} cancel={cancel} retry={retry}/></section>}
    {system?.advanced && <><ProviderOptions values={configuration.providers} error={error}/>
      <details className="panel"><summary>Performance and preference weights</summary><div className="stack detail-body">
        <label>Quality ceiling<select value={system.profile_setting === 'QUALITY' ? 'HIGH_QUALITY' : system.profile_setting} onChange={e => changeProfile(e.target.value)}>
          {['AUTO','LOW_VRAM','BALANCED','HIGH_QUALITY','CUSTOM'].map(item => <option key={item}>{item}</option>)}</select></label>
        <Policy values={configuration.policy} refresh={refresh} error={error}/>
      </div></details>
      {project && <Comparison project={project} jobs={jobs} error={error}/>}
      <Diagnostics error={error}/>
      <Hardware system={system}/>
      <section className="panel stack tight"><h2>Blender connection</h2><code className="path">{system.token_file}</code></section>
    </>}
  </div>;
}

function Diagnostics({error}: {error: (value: unknown) => void}) {
  const [name, setName] = useState('studio');
  const [lines, setLines] = useState<string>();
  return <details className="panel"><summary>Local logs</summary><div className="stack detail-body">
    <label>Log<select value={name} onChange={event => {setName(event.target.value); setLines(undefined);}}>
      {['studio','comfyui','ollama'].map(value => <option key={value}>{value}</option>)}</select></label>
    <button onClick={() => void api<{text: string}>('/diagnostics/' + name).then(value => setLines(value.text)).catch(error)}>Read recent log</button>
    {lines !== undefined && <pre>{lines || 'No log entries.'}</pre>}
  </div></details>;
}

function LocalModels({configuration, queue, busy, error}: {
  configuration: ModelConfiguration; queue: (url: string, request: RequestInit) => void; busy: boolean; error: (value: unknown) => void;
}) {
  const [model, setModel] = useState('');
  const [path, setPath] = useState('');
  const [tag, setTag] = useState('');
  const [gguf, setGguf] = useState('');
  const [license, setLicense] = useState('');
  return <details className="panel"><summary>Use Existing Local Model / Register Custom Model</summary>
    <div className="stack detail-body">
      <label>Native ComfyUI variant<select value={model} onChange={e => setModel(e.target.value)}><option value="">Select a variant</option>
        {configuration.catalog.filter(row => row.provider === 'comfy' && ['generate','concept'].includes(row.operation)).map(row =>
          <option key={row.id} value={row.id}>{row.name}{configuration.existing.matches.includes(row.id) ? ' · files detected' : ''}</option>)}</select></label>
      <div className="row"><label>Model or Hugging Face snapshot directories<textarea value={path} onChange={e => setPath(e.target.value)} placeholder="Optional paths, one per line"/></label>
        <button onClick={() => pickPath('directory', value => setPath(current => [current, value].filter(Boolean).join('\n')), error)}
          aria-label="Choose model directory"><FolderOpen size={15}/></button></div>
      <button disabled={!model || busy} onClick={() => queue('/models/register', json('POST', {model, roots: path.split('\n').map(value => value.trim()).filter(Boolean)}))}>
        Verify and reference local files</button>
      <p className="small">Restart a Studio-managed provider to reload registered paths. For an independently managed ComfyUI, configure those paths in its model search settings.</p>
      <label>Ollama tag<input list="existing-ollama" value={tag} onChange={e => setTag(e.target.value)}/></label>
      <datalist id="existing-ollama">{configuration.existing.ollama.map(row => <option value={row.tag} key={row.tag}>{row.family}</option>)}</datalist>
      <div className="row"><label>GGUF file for a new tag<input value={gguf} onChange={e => setGguf(e.target.value)} placeholder="Optional; Ollama imports it into its blob store"/></label>
        <button onClick={() => pickPath('gguf', setGguf, error)} aria-label="Choose GGUF file"><FolderOpen size={15}/></button></div>
      <label>License (optional)<input value={license} onChange={e => setLicense(e.target.value)}/></label>
      <button disabled={!tag.trim() || busy} onClick={() => queue('/models/register', json('POST', {tag, gguf: gguf || null, license}))}>Register Local Ollama Model</button>
    </div>
  </details>;
}

function ProviderOptions({values, error}: {values: ProviderSettings; error: (value: unknown) => void}) {
  const [form, setForm] = useState(values);
  const [saved, setSaved] = useState(false);
  useEffect(() => setForm(values), [values]);
  return <details className="panel"><summary>Local providers</summary><form className="stack detail-body" onSubmit={e => {
    e.preventDefault(); void api('/model-configuration/providers', json('PUT', form)).then(() => setSaved(true)).catch(error);
  }}>
    <div className="two-col">{(['comfy_port','ollama_port'] as const).map(key => <label key={key}>{key === 'comfy_port' ? 'ComfyUI loopback port' : 'Ollama loopback port'}
      <input type="number" min={1024} max={65535} value={form[key]} onChange={e => setForm(current => ({...current, [key]: Number(e.target.value)}))}/></label>)}</div>
    {(['comfy','comfy_python'] as const).map(key => <div className="row" key={key}>
      <label>{key === 'comfy' ? 'Existing ComfyUI directory' : 'ComfyUI Python interpreter'}<input value={form[key]}
        onChange={e => setForm(current => ({...current, [key]: e.target.value}))}/></label>
      <button type="button" onClick={() => pickPath(key === 'comfy' ? 'directory' : 'python', value => setForm(current => ({...current, [key]: value})), error)}>Browse</button>
    </div>)}
    <button>Save Provider Settings</button>{saved && <p role="status">Provider settings saved. Restart Studio to connect.</p>}
  </form></details>;
}

function Policy({values, refresh, error}: {values: Record<string, number>; refresh: () => Promise<void>; error: (value: unknown) => void}) {
  const [weights, setWeights] = useState(values);
  return <form className="stack tight" onSubmit={event => {
    event.preventDefault(); void api('/model-configuration/policy', json('PUT', {weights})).then(refresh).catch(error);
  }}><div className="two-col">{Object.entries(weights).map(([key, value]) => <label key={key}>{key}<input type="number"
    min={0} max={20} value={value} onChange={e => setWeights({...weights, [key]: Number(e.target.value)})}/></label>)}</div>
    <button>Save Ranking Preferences</button>
  </form>;
}

type Compared = {id: string; model: string; profile?: string; file?: string; triangles?: number; seconds?: number; operations?: number};
function Comparison({project, jobs, error}: {project: string; jobs: Job[]; error: (value: unknown) => void}) {
  const [left, setLeft] = useState('');
  const [right, setRight] = useState('');
  const [report, setReport] = useState<Compared[]>([]);
  const cases = jobs.filter(job => job.project === project && job.status === 'completed' && ['generate','concept','interpret'].includes(job.kind));
  return <details className="panel"><summary>Compare model runs</summary><div className="stack detail-body">
    <p className="small">Run two variants with the same input, seed and Art Bible. Compare measured results and inspect the outputs.</p>
    {([{label:'First run', value:left, change:setLeft},{label:'Second run', value:right, change:setRight}]).map(pick =>
      <label key={pick.label}>{pick.label}<select value={pick.value} onChange={e => pick.change(e.target.value)}><option value="">Select a run</option>
        {cases.map(job => <option value={job.id} key={job.id}>{capabilities[job.kind]} · {new Date(job.updated).toLocaleString()}</option>)}
      </select></label>)}
    <button disabled={!left || !right || left === right} onClick={() => void api<Compared[]>('/model-configuration/comparison',
      json('POST', {project, first: left, second: right})).then(setReport).catch(error)}>Compare runs</button>
    {report.map(run => <div key={run.id} className="material"><strong>{run.model}</strong>
      <p className="small">{[run.profile, run.seconds === undefined ? '' : run.seconds + ' s', run.triangles === undefined ? '' : run.triangles + ' triangles',
        run.operations === undefined ? '' : run.operations + ' operations'].filter(Boolean).join(' · ')}</p>
      {run.file && <a href={media(run.file, true)}>Retrieve output</a>}
    </div>)}
  </div></details>;
}
