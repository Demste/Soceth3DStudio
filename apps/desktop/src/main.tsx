import { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, BookOpen, Box, ChevronDown, FolderKanban, Gauge, Layers3, Plus, X } from 'lucide-react';
import { api, Asset, AssetDetail, Bible, BibleVersion, Job, ModelConfiguration, Project, ProjectFields, Source, System, json, media, stageActive } from './api';
import { ArtBible, ProjectForm, ProjectList } from './Projects';
import { AssetTools, Comparison, Generation } from './Workspace';
import { AssetTiles, Hardware, JobList, Library } from './StudioPanels';
import { ModelManager } from './ModelManager';
import { Viewport } from './Viewport';
import './style.css';

type Screen = 'Overview' | 'Projects' | 'Art Bible' | 'Workspace' | 'Library' | 'Models' | 'Jobs';
const nav = [
  ['Overview', Gauge], ['Projects', FolderKanban], ['Art Bible', BookOpen], ['Workspace', Box],
  ['Library', Layers3], ['Models', Activity], ['Jobs', Activity],
] as const;

function App() {
  const [ready, setReady] = useState(false);
  const [screen, setScreen] = useState<Screen>('Overview');
  const [system, setSystem] = useState<System | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState('');
  const [projectForm, setProjectForm] = useState<Project | 'new'>();
  const [bible, setBible] = useState<BibleVersion>();
  const [sources, setSources] = useState<Source[]>([]);
  const [chosen, setChosen] = useState<Source>();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [recent, setRecent] = useState<Asset[]>([]);
  const [asset, setAsset] = useState<AssetDetail>();
  const [revisionId, setRevisionId] = useState('');
  const [compareId, setCompareId] = useState<string>();
  const [configuration, setConfiguration] = useState<ModelConfiguration>();
  const [update, setUpdate] = useState<{available: boolean; tag?: string; url?: string}>();
  const models = configuration?.catalog ?? [];
  const [jobs, setJobs] = useState<Job[]>([]);
  const [notice, setNotice] = useState<{text: string; bad: boolean}>();
  const watched = useRef(new Set<string>());
  const project = projects.find(item => item.id === projectId);
  const revision = asset?.revisions.find(item => item.id === revisionId) || asset?.revisions.find(item => item.id === asset.current);
  const compareRevision = asset?.revisions.find(item => item.id === compareId);
  const activeJobs = jobs.filter(stageActive);
  const relevant = jobs.filter(item => item.project === projectId);
  const notify = useCallback((text: string, bad = false) => setNotice({text, bad}), []);
  const error = useCallback((value: unknown) => notify(value instanceof Error ? value.message : String(value), true), [notify]);
  const refreshProjects = () => api<Project[]>('/projects').then(setProjects);
  const refreshModels = () => api<ModelConfiguration>('/model-configuration' +
    (projectId ? '?project=' + projectId : '')).then(setConfiguration);
  const refreshRecent = () => api<Asset[]>('/assets').then(setRecent);

  useEffect(() => {
    const controller = new AbortController();
    async function boot() {
      const token = new URLSearchParams(location.hash.slice(1)).get('session');
      if (token) {
        history.replaceState(null, '', location.pathname);
        await api('/session', {method:'POST', headers:{Authorization: 'Bearer ' + token},signal:controller.signal});
      }
      const status = await api<System>('/status', {signal:controller.signal});
      setSystem(status);
      if (status.last_project) {
        setProjectId(status.last_project);
        setScreen(localStorage.getItem('soceth-screen') === 'Workspace' ? 'Workspace' : 'Overview');
      }
      setReady(true);
    }
    boot().catch(err => {if (!controller.signal.aborted) error(err);});
    return () => controller.abort();
  }, [error]);

  useEffect(() => {
    if (!ready) return;
    void Promise.all([refreshProjects(), refreshRecent()]).catch(error);
    let closed = false, connection: WebSocket | undefined, retry: number | undefined;
    function connect() {
      if (closed) return;
      connection = new WebSocket('ws://' + location.host + '/api/events');
      connection.onmessage = event => {setJobs(JSON.parse(event.data) as Job[]);};
      connection.onclose = () => {
        if (!closed) {
          void api<Job[]>('/jobs').then(setJobs).catch(() => {});
          retry = window.setTimeout(connect, 1800);
        }
      };
    }
    connect();
    const poll = window.setInterval(() => {void api<System>('/status').then(setSystem).catch(() => {});}, 6500);
    return () => {closed = true; connection?.close(); clearInterval(poll); clearTimeout(retry);};
  }, [ready, error]);

  useEffect(() => {
    if (ready) void refreshModels().catch(error);
  }, [ready, projectId, error]);

  useEffect(() => {
    if (ready) void api<{available: boolean; tag?: string; url?: string}>('/app-update').then(setUpdate).catch(() => {});
  }, [ready]);

  useEffect(() => {
    if (!projectId || !ready) return;
    let current = true;
    void Promise.all([
      api<BibleVersion>('/projects/' + projectId + '/bible'),
      api<Source[]>('/projects/' + projectId + '/references'),
      api<Asset[]>('/assets?project=' + projectId),
    ]).then(([value, references, items]) => {
      if (!current) return;
      setBible(value); setSources(references); setAssets(items);
      const last = localStorage.getItem('soceth-asset:' + projectId);
      if (last && items.some(item => item.id === last)) {
        void api<AssetDetail>('/assets/' + last).then(detail => {
          if (current) setAsset(previous => previous ?? detail);
        }).catch(error);
      }
    }).catch(error);
    return () => {current = false;};
  }, [projectId, ready, error]);

  useEffect(() => {
    const finished = jobs.filter(item => watched.current.has(item.id) && !stageActive(item));
    for (const job of finished) {
      watched.current.delete(job.id);
      if (job.status === 'failed') notify(job.error || 'Job failed.', true);
      if (job.status !== 'completed') continue;
      if (job.result?.warning) notify(job.result.warning, true);
      if (!job.project) {void refreshModels().catch(error); continue;}
      if (job.result?.asset) {
        void Promise.all([refreshRecent(), refreshProjects()]).catch(error);
        if (job.project === projectId) {
          void api<Asset[]>('/assets?project=' + projectId).then(setAssets).catch(error);
          if (job.kind !== 'interpret') void fetchAsset(job.result.asset, job.project, job.result.revision).catch(error);
        }
      }
      if (job.kind === 'export' && asset && job.project === projectId) {
        void fetchAsset(asset.id, projectId, revisionId).catch(error);
      }
      if (job.kind === 'concept' && job.project === projectId) {
        void api<Source[]>('/projects/' + projectId + '/references').then(items => {
          setSources(items); setChosen(items.find(item => item.id === job.result?.file));
        }).catch(error);
      }
    }
  }, [jobs, projectId]);

  function go(next: Screen) {setScreen(next); localStorage.setItem('soceth-screen', next);}
  async function selectProject(id: string, next: Screen) {
    if (id !== projectId) {
      await api('/projects/' + id + '/activate', json('POST'));
      setAsset(undefined); setRevisionId(''); setChosen(undefined); setBible(undefined); setSources([]); setAssets([]);
      setProjectId(id);
    }
    go(next);
  }
  async function fetchAsset(id: string, owner: string, selected = '') {
    const detail = await api<AssetDetail>('/assets/' + id);
    setAsset(detail); setRevisionId(selected || detail.current);
    localStorage.setItem('soceth-asset:' + owner, id);
  }
  async function openAsset(item: Asset) {
    await selectProject(item.project, 'Workspace');
    await fetchAsset(item.id, item.project);
  }
  async function saveProject(values: ProjectFields) {
    try {
      const editing = projectForm && projectForm !== 'new';
      const result = await api<Project>('/projects' + (editing ? '/' + projectForm.id : ''), json(editing ? 'PUT' : 'POST', values));
      await refreshProjects();
      setProjectForm(undefined);
      await selectProject(result.id, editing ? 'Projects' : 'Art Bible');
      notify(editing ? 'Project settings saved.' : 'Project created.');
    } catch (err) {error(err);}
  }
  async function saveBible(value: unknown) {
    try {
      const entry = await api<BibleVersion>('/projects/' + projectId + '/bible', json('PUT', value));
      setBible(entry); await refreshProjects(); notify('Art Bible version ' + entry.version + ' saved.');
    } catch (err) {error(err);}
  }
  function exportBible() {
    if (!bible) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(bible.body, null, 2)], {type:'application/json'}));
    const a = document.createElement('a'); a.href = url; a.download = 'art-bible-v' + bible.version + '.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }
  async function upload(file: File) {
    try {
      const data = new FormData();
      data.append('file', file);
      const result = await api<Source>('/projects/' + projectId + '/files', {method:'POST', body:data});
      if (result.kind === 'reference') setSources(items => [result, ...items]);
      return result;
    } catch (err) {error(err); return undefined;}
  }
  async function queue(url: string, request: RequestInit) {
    try {
      const reply = await api<{id:string}>(url, request);
      watched.current.add(reply.id);
      setJobs(await api<Job[]>('/jobs'));
    } catch (err) {error(err);}
  }
  function submit(kind: string, body: Record<string, unknown> = {}) {
    void queue('/projects/' + projectId + '/jobs', json('POST', {kind, ...body}));
  }
  function cancel(id: string) {
    void api('/jobs/' + id + '/cancel', json('POST')).catch(error);
  }
  function retry(id: string) {void queue('/jobs/' + id + '/retry', json('POST'));}
  function restore(id: string) {
    if (!asset) return;
    void api<AssetDetail>('/assets/' + asset.id + '/restore/' + id, json('POST')).then(detail => {
      setAsset(detail); setRevisionId(id); notify('Working revision restored.');
      if (system?.blender_connected) submit('open', {asset: asset.id, revision: id});
    }).catch(error);
  }
  const tasks = {cancel, retry};
  const requiresProject = ['Art Bible', 'Workspace', 'Library'].includes(screen);
  return <div className="app">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark"><Box size={22}/></span><div><strong>Soceth</strong><span>3D STUDIO</span></div></div>
      <label className="project-select"><span>PROJECT</span><div><FolderKanban size={16}/>
        <select aria-label="Active project" value={projectId} onChange={e => {
          if (e.target.value === 'new') setProjectForm('new');
          else if (e.target.value) void selectProject(e.target.value, 'Workspace').catch(error);
        }}><option value="" disabled>Select project</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}<option value="new">＋ Create project</option></select><ChevronDown size={14}/></div></label>
      <nav>{nav.map(([label, Icon]) => <button key={label} className={screen === label ? 'active' : ''} onClick={() => go(label)}>
        <Icon size={18}/><span>{label}</span>{label === 'Jobs' && activeJobs.length > 0 && <em>{activeJobs.length}</em>}</button>)}</nav>
      <footer><span className="status-dot"/><div><strong>Local workspace</strong><span>{system?.profile || 'Starting Studio'}</span></div></footer>
    </aside>
    <main>
      <header className="topbar"><div>{project ? <><strong>{project.name}</strong><span>/</span></> : null}{screen}</div>
        <div className="row"><span className="top-system">{system?.gpus[0]?.name || 'Local system'}</span>
          {configuration?.hardware_changed && <button className="compact" onClick={() => go('Models')}>Hardware recommendations</button>}
          {update?.available && <a className="button compact" href={update.url}>Update {update.tag}</a>}
          {system && <button className="compact" aria-pressed={system.advanced} onClick={() =>
            void api<System>('/settings', json('PUT', {profile: system.profile_setting, advanced: !system.advanced})).then(setSystem).catch(error)}>
            {system.advanced ? 'Advanced mode' : 'Basic mode'}</button>}
          <button className="compact" onClick={() => setProjectForm('new')}><Plus size={16}/>New project</button></div></header>
      <div className={'content' + (screen === 'Workspace' ? ' workspace-content' : '')}>
        {!ready && <div className="empty">{notice?.bad ? notice.text : 'Starting Soceth 3D Studio…'}</div>}
        {ready && requiresProject && !projectId && <div className="empty stack"><h2>Open a project to continue</h2>
          <button onClick={() => go('Projects')}>Browse projects</button><button className="primary" onClick={() => setProjectForm('new')}><Plus size={16}/>Create project</button></div>}
        {ready && screen === 'Overview' && <div className="stack dashboard">
          <div className="overview panel"><div><h1>Asset production studio</h1>
            <p>{project ? project.name + ' is the active project.' : 'Create a project to set your art direction and start building assets.'}</p>
            <div className="row"><button className="primary" onClick={() => project ? go('Workspace') : setProjectForm('new')}>{project ? <Box size={17}/> : <Plus size={17}/>} {project ? 'Open workspace' : 'Create project'}</button>
              <button onClick={() => go('Projects')}>All projects</button></div></div><Box className="overview-shape" strokeWidth={0.45}/></div>
          <div className="dashboard-split"><section className="stack tight"><div className="row between"><h2>Recent projects</h2><button className="link" onClick={() => go('Projects')}>View all</button></div>
            <ProjectList projects={projects.slice(0,3)} current={projectId} create={() => setProjectForm('new')} choose={id => void selectProject(id,'Workspace').catch(error)} edit={setProjectForm}/></section>
            <Hardware system={system}/></div>
          <section className="stack tight"><div className="row between"><h2>Recent assets</h2><button className="link" onClick={() => go('Library')}>Asset library</button></div><AssetTiles assets={recent.slice(0,6)} open={item => void openAsset(item).catch(error)}/></section>
          <div className="dashboard-split"><section className="panel stack tight"><h2>Running jobs</h2><JobList jobs={activeJobs} {...tasks} empty="No jobs running."/></section>
            <section className="panel stack tight"><div className="row between"><h2>AI models</h2><button className="link" onClick={() => go('Models')}>Manage</button></div>
              {models.map(item => <div className="row between small" key={item.id}><span>{item.name}</span><span className={item.installed ? 'pill green' : 'pill'}>{item.installed ? 'Installed' : 'Available'}</span></div>)}</section></div>
        </div>}
        {ready && screen === 'Projects' && <div className="stack"><div className="screen-heading"><div><h1>Projects</h1><p>{projects.length} game projects</p></div><button className="primary" onClick={() => setProjectForm('new')}><Plus size={17}/>Create project</button></div>
          <ProjectList projects={projects} current={projectId} create={() => setProjectForm('new')} choose={id => void selectProject(id, 'Workspace').catch(error)} edit={setProjectForm}/></div>}
        {ready && screen === 'Art Bible' && bible && <ArtBible key={projectId + bible.version} entry={bible} references={sources} save={saveBible} upload={upload} importConfig={saveBible} exportConfig={exportBible} error={msg => notify(msg,true)}/>}
        {ready && screen === 'Library' && projectId && <Library assets={assets} open={item => void openAsset(item).catch(error)}/>}
        {ready && screen === 'Models' && (configuration ? <ModelManager configuration={configuration} system={system}
          jobs={jobs} project={projectId || undefined} refresh={refreshModels} queue={queue} error={error} {...tasks}
          watch={ids => {ids.forEach(id => watched.current.add(id)); void api<Job[]>('/jobs').then(setJobs).catch(error);}}
          changeProfile={value => void api<System>('/settings', json('PUT', {profile:value})).then(next => {
            setSystem(next); return refreshModels();
          }).catch(error)}/> : <div className="empty">Checking local hardware and providers…</div>)}
        {ready && screen === 'Jobs' && <div className="stack"><div className="screen-heading"><div><h1>Jobs</h1><p>{activeJobs.length} active</p></div></div>
          <section className="panel"><JobList jobs={jobs} {...tasks}/></section></div>}
        {ready && screen === 'Workspace' && bible && <div className="workspace">
          <div className="workspace-left"><Generation key={projectId} bible={bible.body} models={models} sources={sources} chosen={chosen}
            select={setChosen} upload={upload} submit={submit} busy={relevant.some(stageActive)} advanced={Boolean(system?.advanced)}/></div>
          <div className="workspace-view stack"><Viewport url={media(revision?.body.preview)} name={asset?.name}/>
            <section className="panel stack tight"><div className="row between"><h2>Project jobs</h2><button className="link" onClick={() => go('Jobs')}>All jobs</button></div>
              <JobList jobs={relevant.slice(0,6)} {...tasks}/></section></div>
          <div className="workspace-right" role="region" aria-label="Asset Inspector" tabIndex={0}>{asset && revision ? <AssetTools key={asset.id + revision.id} asset={asset} revision={revision} bible={bible.body} bibleVersion={bible.version} system={system}
            jobs={relevant} models={models} submit={submit} restore={restore} compare={setCompareId} selectRevision={setRevisionId}
            saveMeta={body => void api('/assets/' + asset.id, json('PATCH',body)).then(() => Promise.all([fetchAsset(asset.id, projectId, revision.id), api<Asset[]>('/assets?project=' + projectId).then(setAssets), refreshRecent()])).then(() => notify('Metadata saved.')).catch(error)}/>
            : <div className="panel stack tight"><h2>Inspector</h2><p className="muted">Select an asset to inspect materials, edit geometry, and manage revisions.</p><button onClick={() => go('Library')}>Open asset library</button></div>}</div>
        </div>}
      </div>
    </main>
    {projectForm && <ProjectForm project={projectForm === 'new' ? undefined : projectForm} onSubmit={saveProject} onClose={() => setProjectForm(undefined)}/>}
    {revision && compareRevision && <Comparison a={revision} b={compareRevision} close={() => setCompareId(undefined)}/>}
    {notice && <div className={'toast' + (notice.bad ? ' bad' : '')} role={notice.bad ? 'alert' : 'status'}><span>{notice.text}</span><button className="icon" onClick={() => setNotice(undefined)} aria-label="Dismiss"><X size={17}/></button></div>}
  </div>;
}
createRoot(document.getElementById('root')!).render(<App/>);

