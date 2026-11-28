import { useState } from 'react';
import { Box, Cpu, HardDrive, Layers3, Search, Triangle } from 'lucide-react';
import { Asset, Job, Report, System, gib, media, stageActive, time } from './api';

export function AssetTiles({assets, open}: {assets: Asset[]; open: (asset: Asset) => void}) {
  return assets.length ? <div className="tile-grid">{assets.map(asset => <button key={asset.id} className="asset-tile" onClick={() => open(asset)}>
    <div className="asset-image">{asset.body.thumbnail ? <img src={media(asset.body.thumbnail)} alt=""/> : <Box size={34}/>}</div>
    <div className="asset-caption"><strong>{asset.name}</strong><span>{asset.category}</span>
      {asset.body.tags.length > 0 && <small>{asset.body.tags.join(' · ')}</small>}</div>
  </button>)}</div> : <div className="empty">No assets in this view.</div>;
}

export function Library({assets, open}: {assets: Asset[]; open: (asset: Asset) => void}) {
  const [filter, setFilter] = useState('');
  const [category, setCategory] = useState('');
  const categories = Array.from(new Set(assets.map(asset => asset.category))).sort();
  const items = assets.filter(asset => (!category || category === asset.category) &&
    [asset.name, asset.category, ...asset.body.tags].join(' ').toLowerCase().includes(filter.toLowerCase()));
  return <><div className="screen-heading"><div><h1>Asset library</h1><p>{assets.length} assets</p></div>
    <div className="row"><label className="search"><Search size={16}/><input aria-label="Search assets" placeholder="Search assets" value={filter} onChange={e => setFilter(e.target.value)}/></label>
      <select aria-label="Category filter" value={category} onChange={e => setCategory(e.target.value)}><option value="">All categories</option>{categories.map(item => <option key={item}>{item}</option>)}</select></div></div>
    <AssetTiles assets={items} open={open}/></>;
}

export function Hardware({system}: {system: System | null}) {
  return <section className="panel stack tight">
    <h2><Cpu size={18}/>System</h2>
    {system?.gpus.map(gpu => <div key={gpu.name} className="system-item"><strong>{gpu.name}</strong>
      <span>{gpu.used.toLocaleString()} / {gpu.total.toLocaleString()} MiB · Driver {gpu.driver}</span>
      <progress max={gpu.total} value={gpu.used} aria-label={gpu.name + ' memory use'}/></div>)}
    {!system?.gpus.length && <p className="muted">NVIDIA GPU telemetry unavailable.</p>}
    {system && <div className="system-item"><span>{system.cores} logical cores · {system.profile}</span>
      <span><HardDrive size={14}/>{gib(system.disk_free)} available</span>
      {system.pytorch && <span>PyTorch {system.pytorch}</span>}{system.ollama_version && <span>Ollama {system.ollama_version}</span>}</div>}
  </section>;
}

export function JobList({jobs, cancel, retry, empty = 'No jobs yet.'}: {
  jobs: Job[]; cancel: (id: string) => void; retry: (id: string) => void; empty?: string;
}) {
  if (!jobs.length) return <div className="empty">{empty}</div>;
  return <div className="job-list">{jobs.map(job => <div key={job.id} className="job">
    <div className="job-main"><div className="row wrap"><strong>{job.kind[0].toUpperCase() + job.kind.slice(1)}</strong>
      <span className={'pill ' + (job.status === 'failed' ? 'red' : job.status === 'completed' ? 'green' : '')}>{job.status}</span></div>
      <div className="small muted">{job.stage} · {time(job.updated)}</div>
      {job.result?.warning && <div className="error-text">{job.result.warning}</div>}
      {job.error && <div className="error-text">{job.error}</div>}
      {stageActive(job) && job.progress !== null && <progress aria-label="Job progress" max={1} value={job.progress}/>}
      {job.result?.metrics && <div className="small muted">{job.result.metrics.seconds}s · {Object.values(job.result.metrics.gpu_peak_system_mib).map(n => n + ' MiB system peak').join(', ') || 'GPU telemetry unavailable'}</div>}
      {job.result?.file && job.kind !== 'concept' && <a href={media(job.result.file, true)}>Download {job.kind === 'export' ? 'package' : 'report'}</a>}
    </div>
    {stageActive(job) ? <button disabled={Boolean(job.cancel)} onClick={() => cancel(job.id)}>{job.cancel ? 'Cancelling…' : 'Cancel'}</button>
      : ['failed', 'cancelled'].includes(job.status) && <button onClick={() => retry(job.id)}>Retry</button>}
  </div>)}</div>;
}

export function Inspector({report}: {report: Report}) {
  return <div className="stack tight">
    <div className="stats-grid"><div><Triangle size={15}/><strong>{report.triangles.toLocaleString()}</strong><span>Triangles</span></div>
      <div><Box size={15}/><strong>{report.vertices.toLocaleString()}</strong><span>{report.vertex_budget ? 'Vertices · max ' + report.vertex_budget.toLocaleString() : 'Vertices'}</span></div>
      <div><Layers3 size={15}/><strong>{report.object_count}</strong><span>Objects</span></div></div>
    <p className="small muted">Bounds {report.bounds[1].map((n, i) => (n - report.bounds[0][i]).toFixed(3)).join(' × ')} m · {report.uv_present ? 'UVs present' : 'Missing UVs'}</p>
    <details><summary>Objects and materials</summary><div className="stack tight detail-body">
      {report.objects.map(obj => <div className="row between small" key={obj.id}><strong>{obj.name}</strong><span className="muted">{obj.triangles.toLocaleString()} tris</span></div>)}
      {report.materials.map(mat => <div className="material" key={mat.name}>
        <strong>{mat.name}</strong><div className="muted small">Metallic {mat.metallic?.toFixed(2) ?? '—'} · Roughness {mat.roughness?.toFixed(2) ?? '—'}</div>
        {Object.keys(mat.maps).length > 0 && <div className="small muted">{Object.keys(mat.maps).join(', ')}</div>}</div>)}
      {report.textures.map(texture => <div className="small muted" key={texture.name}>{texture.name} · {texture.size.join(' × ')}{texture.missing ? ' · missing' : ''}</div>)}
    </div></details>
    <details open={report.issues.some(item => item.level === 'error')}><summary>Validation · {report.valid ? (report.review_required ? 'Review' : 'Pass') : 'Errors'}</summary>
      {report.issues.map((item, i) => <p className={item.level === 'error' ? 'small error-text' : 'small muted'} key={i}>{item.level.toUpperCase()} · {item.message}</p>)}</details>
  </div>;
}

