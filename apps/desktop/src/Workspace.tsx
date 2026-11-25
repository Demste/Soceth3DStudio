import { useState } from 'react';
import { Box, ClipboardCheck, Download, ImagePlus, Layers3, RotateCcw, Send, Sparkles, Upload, X } from 'lucide-react';
import { AssetDetail, Bible, Job, Model, OptimizationProfile, Region, Revision, Source, System, media, optimizationProfiles, split, stageActive, time } from './api';
import { Inspector } from './StudioPanels';
import { Viewport } from './Viewport';
import { ArtCompliance, OptimizationResult, RegionEditor } from './PaletteTools';

type Submit = (kind: string, value?: Record<string, unknown>) => void;

export function Generation({bible, sources, models, upload, submit, busy, chosen, select, advanced}: {
  bible: Bible; sources: Source[]; models: Model[]; upload: (file: File) => Promise<Source | undefined>;
  submit: Submit; busy: boolean; advanced: boolean; chosen?: Source; select: (item?: Source) => void;
}) {
  const [name, setName] = useState('');
  const [category, setCategory] = useState(Object.keys(bible.triangle_budgets)[0] ?? 'Prop');
  const [mode, setMode] = useState<'reference' | 'concept' | 'import'>('reference');
  const [model2d, setModel2d] = useState('');
  const [model3d, setModel3d] = useState('');
  const [samplingSize, setSamplingSize] = useState('');
  const [samplingSteps, setSamplingSteps] = useState('');
  const [prompt, setPrompt] = useState('');
  const [preset, setPreset] = useState('');
  const [seed, setSeed] = useState('');
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploaded, setUploaded] = useState<Source>();
  const selected = mode === 'concept' && chosen?.kind !== 'concept' ? undefined : chosen;
  async function accept(file: File) {
    setUploading(true);
    try {
      const item = await upload(file);
      if (item?.kind === 'upload_mesh') {setMode('import'); setUploaded(item);}
      if (item?.kind === 'reference') {select(item); setMode('reference');}
      if (item && !name) setName(item.name.replace(/\.[^.]+$/, ''));
    } finally {setUploading(false);}
  }
  const request = {name: name.trim() || 'Untitled asset', category, ...(seed && {seed: Number(seed)})};
  const pick = (operation: string, value: string, change: (value: string) => void) =>
    <select aria-label={operation === 'generate' ? 'Image-to-3D model' : 'Text-to-image model'} value={value} onChange={e => change(e.target.value)}>
      <option value="">Project default</option>{models.filter(item => item.operation === operation).map(item =>
        <option key={item.id} value={item.id}>{item.name}{item.installed ? '' : ' · download required'}</option>)}</select>;
  const chosenModel = (kind: string) => models.find(item => item.operation === kind && item.selected)?.name || 'Automatic recommendation';
  const sampling = {...(advanced && samplingSize && {concept_size:Number(samplingSize)}),
    ...(advanced && samplingSteps && {concept_steps:Number(samplingSteps)})};
  return <section className="panel stack generation"><h2><Sparkles size={17}/>Create asset</h2>
    <div className="segmented">{([['reference', 'Image'], ['concept', 'Text'], ['import', 'Import']] as const).map(([key,label]) =>
      <button key={key} aria-pressed={mode === key} onClick={() => setMode(key)}>{label}</button>)}</div>
    <label>Asset name<input placeholder="Asset name" value={name} onChange={e => setName(e.target.value)}/></label>
    <label>Category<input list="categories" value={category} onChange={e => setCategory(e.target.value)}/><datalist id="categories">{Object.keys(bible.triangle_budgets).map(key => <option key={key}>{key}</option>)}</datalist></label>
    {mode !== 'concept' && <label className={'drop' + (dragging ? ' dragging' : '')}
      onDragOver={e => {e.preventDefault(); setDragging(true);}} onDragLeave={() => setDragging(false)} onDrop={e => {
        e.preventDefault(); setDragging(false); if (e.dataTransfer.files[0]) void accept(e.dataTransfer.files[0]);
      }}>
      <Upload size={20}/><strong>{uploading ? 'Importing file…' : mode === 'import' ? uploaded?.name || 'Drop a GLB, GLTF or FBX' : 'Drop a reference image'}</strong>
      <span>or choose a file</span><input type="file" hidden accept={mode === 'import' ? '.glb,.gltf,.fbx' : '.png,.jpg,.jpeg,.webp'} onChange={e => {if (e.target.files?.[0]) void accept(e.target.files[0]); e.target.value = '';}}/>
    </label>}
    {mode === 'import' ? <button disabled={!uploaded || busy} className="primary" onClick={() => submit('import', {...request,file:uploaded?.id})}><Box size={17}/>Import mesh</button>
      : <>
      {mode === 'concept' && <><label>Concept prompt<textarea placeholder="Describe the asset you want to make…" value={prompt} onChange={e => setPrompt(e.target.value)}/></label>
        {advanced ? <><label>Text-to-image model{pick('concept', model2d, setModel2d)}</label>
        <label>Generation preset<select value={preset} onChange={e => setPreset(e.target.value)}><option value="">Art Bible defaults</option>
          {Object.keys(bible.presets).map(key => <option key={key}>{key}</option>)}</select></label>
        <details><summary>Concept sampling</summary><div className="stack tight detail-body">
          <label>Concept resolution<select value={samplingSize} onChange={e => setSamplingSize(e.target.value)}><option value="">Profile default</option>
            {[512,768,1024].map(n => <option key={n} value={n}>{n} px</option>)}</select></label>
          <label>Concept steps<input type="number" min={1} max={12} value={samplingSteps} placeholder="4" onChange={e => setSamplingSteps(e.target.value)}/></label>
        </div></details></> : <p className="small">{chosenModel('concept')}</p>}
        <button disabled={!prompt.trim() || !category.trim() || busy} onClick={() => submit('concept', {...request,prompt,preset:preset || undefined, model:advanced && model2d || undefined, ...(Object.keys(sampling).length && {sampling})})}><ImagePlus size={17}/>{selected ? 'Regenerate concept' : 'Generate concept'}</button></>}
      <label>{mode === 'concept' ? 'Approve saved concept' : 'Reference image'}<select value={selected?.id ?? ''} onChange={e => select(sources.find(item => item.id === e.target.value))}>
        <option value="">{mode === 'concept' ? 'Select a concept' : 'Select a reference'}</option>
        {sources.filter(item => mode === 'concept' ? item.kind === 'concept' : true).map(item => <option key={item.id} value={item.id}>{item.body?.prompt || item.name}{item.body?.seed !== undefined ? ' · ' + item.body.seed : ''}</option>)}</select></label>
      {selected && <div className="reference-frame"><img src={media(selected.id)} alt={selected.body?.prompt || selected.name}/>{selected.body?.seed !== undefined && <span>Seed {selected.body.seed}</span>}</div>}
      {advanced ? <><label>Image-to-3D model{pick('generate', model3d, setModel3d)}</label>
      <label>Seed (optional)<input type="number" min="0" max={Number.MAX_SAFE_INTEGER} value={seed} onChange={e => setSeed(e.target.value)} placeholder="Random"/></label></>
      : <p className="small">{chosenModel('generate')}</p>}
      <button className="primary" disabled={!selected || !category.trim() || busy} onClick={() => submit('generate', {...request,file:selected?.id, model:advanced && model3d || undefined})}>
        <Sparkles size={17}/>{mode === 'concept' || selected?.kind === 'concept' ? 'Approve concept & generate 3D' : 'Generate 3D'}</button>
    </>}
  </section>;
}

export function AssetTools({asset, revision, bible, bibleVersion, system, jobs, models, submit, restore, compare, selectRevision, saveMeta}: {
  asset: AssetDetail; revision: Revision; bible: Bible; bibleVersion: number; system: System | null; jobs: Job[]; models: Model[]; submit: Submit;
  restore: (id: string) => void; compare: (id: string) => void; selectRevision: (id: string) => void;
  saveMeta: (body: {name: string; category: string; tags: string[]}) => void;
}) {
  const [prompt, setPrompt] = useState('');
  const [llm, setLlm] = useState('');
  const [mode, setMode] = useState(system?.blender_structure ? 'interactive' : 'background');
  const [target, setTarget] = useState('');
  const [profile, setProfile] = useState<OptimizationProfile>('balanced');
  const [createLod, setCreateLod] = useState(false);
  const [bake, setBake] = useState(false);
  const [preset, setPreset] = useState(Object.keys(bible.export_presets)[0] ?? '');
  const [confirm, setConfirm] = useState(false);
  const [regions, setRegions] = useState<Region[]>(revision.body.provenance.regions ?? []);
  const [review, setReview] = useState('');
  const [previewRequest, setPreviewRequest] = useState<{previous?: string}>();
  const [name, setName] = useState(asset.name);
  const [category, setCategory] = useState(asset.category);
  const [tags, setTags] = useState(asset.body.tags.join(', '));
  const active = revision.id === asset.current;
  const busy = jobs.some(stageActive);
  const base = {asset: asset.id, revision: revision.id};
  const editJob = jobs.find(item => item.target_asset === asset.id && ['interpret', 'edit'].includes(item.kind));
  const proposalJob = jobs.find(item => item.kind === 'interpret' && item.result?.base === revision.id);
  const productionJob = jobs.find(item => item.target_revision === revision.id &&
    ['validate', 'optimize', 'cleanup', 'palette_preview', 'palette_apply', 'palette_restore', 'approve', 'export'].includes(item.kind));
  const preview = previewRequest && productionJob?.kind === 'palette_preview' &&
    productionJob.id !== previewRequest.previous ? productionJob.result : undefined;
  const inspection = jobs.find(item => item.status === 'completed' && item.result?.revision === revision.id &&
    (item.kind === 'validate' || item.result?.unchanged))?.result;
  const proposal = proposalJob?.result?.proposal;
  const failedAttempt = inspection?.report?.structural_quality?.rating === 'failed';
  const report = (!failedAttempt && inspection?.report) || revision.body.approval?.validation || revision.body.report;
  const reductions = inspection?.unchanged ? inspection.report?.optimization :
    revision.body.report.optimization?.length ? revision.body.report.optimization :
    report.structural_quality ? [report.structural_quality] : [];
  const savedMaterials = revision.body.report.art_bible?.materials ?? [];
  const compliance = report.art_bible && {...report.art_bible, materials: report.art_bible.materials.map(material => {
    const saved = savedMaterials.find(item => item.name === material.name);
    return {...material, texture_file: material.texture_file ?? saved?.texture_file,
      original_texture_file: material.original_texture_file ?? saved?.original_texture_file};
  })};
  const source = asset.revisions.find(item => !item.parent);
  const sourceStage = (revision.body.report.lifecycle ?? (revision.parent ? 'candidate' : 'source')) === 'source';
  const lodStage = revision.body.report.lifecycle === 'lod';
  const trialStage = revision.body.report.variant === 'trial';
  const lodRequest = createLod || profile === 'lod';
  const approved = revision.body.approval?.bible_version === bibleVersion && revision.body.approval.category === asset.category;
  const budget = bible.triangle_budgets[asset.category];
  const invalidTarget = Boolean(target) && (!Number.isInteger(Number(target)) || Number(target) < 12 ||
    Number(target) > (budget ?? 10000000));
  const paletteEnabled = bible.color_policy !== 'reference';
  const paletteMode = system?.blender_production ? 'interactive' : 'background';
  const proposedLod = proposal?.operations.some(item => item.create_lod === true || item.type === 'decimate_mesh' && item.profile === 'lod');
  const interactiveAvailable = Boolean(system?.blender_connected &&
    (system.blender_structure || !proposal?.operations.some(item => ['decimate_mesh', 'cleanup_mesh'].includes(item.type))));
  return <div className="stack">
    <section className="panel stack tight">
      <div className="row between"><h2><Layers3 size={17}/>Inspector</h2><span className={active ? 'pill green' : 'pill'}>{active ? 'Current' : 'History'}</span></div>
      <div className="row wrap"><span className={approved ? 'pill green' : 'pill'}>
        {approved ? 'Approved Game Asset' : sourceStage ? 'Source Reconstruction' : lodStage ? 'LOD Candidate' :
          trialStage ? 'Safer Main Candidate' : 'Main Asset Candidate'}</span>
        {failedAttempt && <span className="pill red">Last attempt failed quality check</span>}
        {!approved && report.review_required && <span className="pill red">Review required</span>}</div>
      <Inspector report={report}/>
      <ArtCompliance report={compliance} bible={bible} version={bibleVersion}/>
      <details><summary>Asset metadata</summary><form className="stack tight detail-body" onSubmit={e => {e.preventDefault(); saveMeta({name, category, tags: split(tags)});}}>
        <label>Name<input required value={name} onChange={e => setName(e.target.value)}/></label>
        <label>Category<input required value={category} onChange={e => setCategory(e.target.value)}/></label>
        <label>Tags<input value={tags} onChange={e => setTags(e.target.value)}/></label><button>Save metadata</button>
        {asset.body.reference && <a href={media(asset.body.reference, true)}>Source image</a>}
        {asset.body.source_mesh && <a href={media(asset.body.source_mesh, true)}>Original mesh</a>}
        {asset.body.generation && <details><summary>Generation provenance</summary><pre>{JSON.stringify(asset.body.generation, null, 2)}</pre></details>}
      </form></details>
    </section>
    <section className="panel stack tight"><h2>Blender editing</h2>
      <div className="row wrap"><span className={system?.blender_connected ? 'pill green' : 'pill'}>{system?.blender_connected ? 'Add-on connected' : 'Add-on offline'}</span>
        <button disabled={!system?.blender_connected || busy || ((lodStage || trialStage) && !system.blender_structure)} onClick={() => submit('open', base)}>Open in Blender</button>
        <button disabled={!system?.blender_connected || !active || busy} onClick={() => submit('capture', base)}>Save Blender scene</button>
        <a href={media(revision.body.blend, true)} title="Download editable Blender scene"><Download size={16}/> .blend</a></div>
      <label>Editing instruction<textarea value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="Clean up floating fragments and preserve openings…"/></label>
      {system?.advanced ? <label>Language model<select value={llm} onChange={e => setLlm(e.target.value)}><option value="">Project default</option>
        {models.filter(model => model.operation === 'interpret').map(model => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
        : <p className="small">{models.find(model => model.operation === 'interpret' && model.selected)?.name || 'Automatic recommendation'}</p>}
      <button disabled={!prompt.trim() || busy} onClick={() => {setConfirm(false); submit('interpret', {...base, prompt, model: system?.advanced && llm || undefined});}}><Send size={16}/>Interpret command</button>
      {editJob && stageActive(editJob) && <p role="status" className="small">{editJob.kind === 'interpret' ? 'Interpreting command' : 'Applying in Blender'} · {editJob.stage}</p>}
      {editJob?.status === 'failed' && <p role="alert" className="small error-text">{editJob.kind === 'interpret' ? 'Interpretation failed' : 'Blender execution failed'} · {editJob.error}</p>}
      {revision.body.provenance.kind === 'edit' && <p className="small" role="status">Execution completed · Saved revision loaded in viewport</p>}
      {revision.body.provenance.kind === 'edit' && revision.body.report.optimization?.map((item, i) => <OptimizationResult key={i} result={item}/>)}
      {proposal && <div className="proposal stack tight"><strong>Structured operation preview</strong><p>{proposalJob.result?.request}</p><p className="small muted">{proposal.explanation}</p>
        {proposal.operations.map((operation, i) => <details key={i}><summary>{operation.type.replaceAll('_',' ')} · {operation.targets.map(id => report.objects.find(obj => obj.id === id)?.name ?? id).join(', ')}</summary>
          <pre>{JSON.stringify(operation, null, 2)}</pre></details>)}
        {proposal.operations.length > 0 && <>{proposedLod ? <p className="small">Saves a distance LOD alongside the main revision.</p> :
          <label>Execution mode<select value={mode} onChange={e => setMode(e.target.value)}><option value="background">Background</option><option value="interactive" disabled={!interactiveAvailable}>Interactive Blender</option></select></label>}
          {proposalJob.result?.confirmation && <label className="check"><input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)}/>Confirm mesh changes</label>}
          <button className="primary" disabled={busy || !active || (proposedLod ? sourceStage : mode === 'interactive' && !interactiveAvailable) || Boolean(proposalJob.result?.confirmation && !confirm)}
            onClick={() => {submit('edit', {...base, proposal: proposalJob.id, mode, confirmed: confirm}); setConfirm(false);}}>Apply in Blender</button></>}
      </div>}
    </section>
    <section className="panel stack tight"><h2>Production</h2>
      <p className="small">Category budget {budget?.toLocaleString() ?? 'undefined'}</p>
      <label>Optimization profile<select aria-label="Optimization profile" value={profile} onChange={e => setProfile(e.target.value as OptimizationProfile)}>
        {Object.entries(optimizationProfiles).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select></label>
      <label>Manual triangle target<input type="number" min={12} max={budget ?? 10000000} value={target}
        placeholder={budget ? String(budget) : 'Enter a target'} onChange={e => setTarget(e.target.value)}/></label>
      {invalidTarget && <p className="small error-text">Enter a whole-number target from 12 to {(budget ?? 10000000).toLocaleString()}.</p>}
      {lodRequest && target && !sourceStage && Number(target) >= report.triangles &&
        <p className="small error-text">Enter a target below the main revision's {report.triangles.toLocaleString()} triangles.</p>}
      <label className="check"><input type="checkbox" checked={lodRequest} disabled={sourceStage || profile === 'lod'} onChange={e => setCreateLod(e.target.checked)}/>Create as LOD</label>
      {lodRequest && sourceStage && <p className="small">Create and select a main candidate before saving a LOD.</p>}
      {target && budget && source && source.body.report.triangles >= budget * 2 && Number(target) <= budget * .25 && !lodRequest &&
        <p className="small error-text">This target is better suited to a distance LOD. The main revision stays active if the target is rejected.</p>}
      <label className="check"><input type="checkbox" checked={bake && !lodRequest} disabled={lodRequest} onChange={e => setBake(e.target.checked)}/>Bake base color with Cycles</label>
      <button disabled={busy || !active || !budget} onClick={() => submit('optimize', {...base, bake: bake && !lodRequest,
        profile: profile === 'lod' ? 'balanced' : profile})}>Optimize to Project Budget</button>
      {target && <button disabled={busy || !active || invalidTarget || (lodRequest && (sourceStage || Number(target) >= report.triangles))}
        onClick={() => submit('optimize', {...base, bake: bake && !lodRequest, profile, create_lod: lodRequest, triangles: Number(target)})}>
        {lodRequest ? 'Create LOD Candidate' : 'Optimize to Manual Target'}</button>}
      <button disabled={busy || !active} onClick={() => submit('cleanup', {...base, profile: 'preserve_structure'})}>Clean Up Mesh</button>
      {source && source.id !== revision.id && <button onClick={() => compare(source.id)}>Compare Source / Result</button>}
      {revision.parent && (lodStage || trialStage) && <button onClick={() => compare(revision.parent!)}>Compare with Main Revision</button>}
      {inspection?.unchanged && <p className="small" role="status">{inspection.warning || 'No new revision was needed.'}</p>}
      {(revision.body.provenance.kind !== 'edit' || inspection?.unchanged) &&
        reductions?.map((item, i) => <OptimizationResult key={i} result={item}/>)}
      <button disabled={busy} onClick={() => submit('validate', base)}><ClipboardCheck size={16}/>Validate Against Art Bible</button>
      {compliance && paletteEnabled && <RegionEditor report={compliance} bible={bible} regions={regions} change={setRegions}/>}
      <div className="row wrap palette-actions">
        <button disabled={busy || !paletteEnabled} onClick={() => {
          setPreviewRequest({previous: productionJob?.id}); submit('palette_preview', {...base, regions});
        }}>Preview Palette Changes</button>
        <button disabled={busy || !active || !paletteEnabled} onClick={() => submit('palette_apply', {...base, regions, mode: paletteMode})}>Apply Project Palette</button>
        <button disabled={busy || !active || sourceStage} onClick={() => submit('palette_restore', {...base, mode: paletteMode})}>Restore Original Colors</button>
        <button disabled={!source || source.id === revision.id} onClick={() => source && compare(source.id)}>Compare Original / Project Style</button>
      </div>
      {productionJob && stageActive(productionJob) && <p className="small" role="status">{productionJob.stage}</p>}
      {productionJob?.status === 'failed' && <p className="small error-text" role="alert">{productionJob.error}</p>}
      {preview?.preview && <div className="dialog-shade"><div className="dialog compare stack">
        <div className="row between"><h2>Palette preview</h2><button className="icon" aria-label="Close palette preview"
          onClick={() => setPreviewRequest(undefined)}><X size={20}/></button></div>
        <div className="two-col"><Viewport minimal url={media(revision.body.preview)} name="Current revision"/>
          <Viewport minimal url={media(preview.preview)} name="Proposed project colors"/></div>
        <ArtCompliance report={preview.report?.art_bible} bible={bible} version={bibleVersion}/>
      </div></div>}
      {!sourceStage && !lodStage && !approved && <><label>Reviewed exceptions (when required)
        <textarea value={review} maxLength={2000} onChange={e => setReview(e.target.value)}
          placeholder="Document any accepted geometry, material or texture warnings."/>
      </label><button disabled={busy || !active} onClick={() => submit('approve', {...base, review_notes: review})}>Approve Game Asset</button></>}
      {approved && revision.body.approval?.review_notes && <p className="small">Reviewed exceptions · {revision.body.approval.review_notes}</p>}
      <label>Export preset<select value={preset} onChange={e => setPreset(e.target.value)}>{Object.keys(bible.export_presets).map(key => <option key={key}>{key}</option>)}</select></label>
      <button disabled={busy || lodStage || !preset || (bible.export_presets[preset]?.format === 'unity' && !approved)} onClick={() => submit('export', {...base, preset})}><Download size={17}/>Export package</button>
      {asset.exports.map(item => <a key={item.file} href={media(item.file, true)}>Download export · {time(item.created)}</a>)}
    </section>
    <section className="panel stack tight"><div className="row between"><h2>Revisions</h2>{!active && !lodStage && <button disabled={busy} onClick={() => restore(revision.id)}><RotateCcw size={15}/>{trialStage ? 'Use safer revision' : 'Restore'}</button>}</div>
      {asset.revisions.map((item, i) => <div className="revision-row" key={item.id}>
        <button className={revision.id === item.id ? 'active' : ''} onClick={() => selectRevision(item.id)}>
          <strong>Revision {asset.revisions.length - i} · {item.body.report.lifecycle === 'lod' ? 'LOD' :
            item.body.report.variant === 'trial' ? 'Safer candidate' : item.body.provenance.kind.replaceAll('_', ' ')}</strong>
          <small>{item.body.report.triangles.toLocaleString()} tris · {item.id === asset.current ? 'Working revision' : time(item.created)}</small></button>
        {item.id !== revision.id && <button onClick={() => compare(item.id)}>Compare</button>}
      </div>)}
    </section>
  </div>;
}

export function Comparison({a, b, close}: {a: Revision; b: Revision; close: () => void}) {
  return <div className="dialog-shade"><div className="dialog compare stack">
    <div className="row between"><h2>Compare revisions</h2><button className="icon" onClick={close} aria-label="Close comparison"><X size={20}/></button></div>
    <div className="two-col">{[a,b].map(item => <div className="stack tight" key={item.id}><Viewport minimal url={media(item.body.preview)}
      name={item.body.report.lifecycle === 'lod' ? 'Distance LOD' : item.body.report.variant === 'trial' ? 'Safer comparison' :
        item.parent ? 'Main revision' : 'Source reconstruction'}/><Inspector report={item.body.report}/></div>)}</div>
  </div></div>;
}

