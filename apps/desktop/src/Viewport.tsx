import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { Box, Focus, Grid2X2, Layers3, Move3d, Sun, Triangle } from 'lucide-react';

type Controls = {
  frame(): void; projection(value: boolean): void;
  wire(value: boolean): void; grid(value: boolean): void; light(value: string): void;
  bounds(value: boolean): void; texture(value: boolean): void;
};

function dispose(root: THREE.Object3D) {
  const resources = new Set<{dispose(): void}>();
  root.traverse(object => {
    if (!(object instanceof THREE.Mesh || object instanceof THREE.Line)) return;
    resources.add(object.geometry);
    for (const material of [object.material].flat()) {
      resources.add(material);
      for (const field of Object.values(material)) {
        if (field instanceof THREE.Texture) resources.add(field);
      }
    }
  });
  resources.forEach(resource => resource.dispose());
}

export function Viewport({url, name, minimal = false}: {url?: string; name?: string; minimal?: boolean}) {
  const holder = useRef<HTMLDivElement>(null);
  const controls = useRef<Controls | null>(null);
  const [status, setStatus] = useState('');
  const [ortho, setOrtho] = useState(false);
  const [wire, setWire] = useState(false);
  const [grid, setGrid] = useState(true);
  const [bounds, setBounds] = useState(false);
  const [lighting, setLighting] = useState('Studio');
  const [showTextures, setTextures] = useState(true);

  useEffect(() => {
    if (!url || !holder.current) return;
    const target = holder.current;
    setStatus('Loading mesh…');
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({antialias: true, alpha: true}); }
    catch { setStatus('WebGL is unavailable. Enable hardware acceleration and restart Studio.'); return; }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.setAttribute('aria-label', 'Interactive 3D model');
    target.appendChild(renderer.domElement);
    const world = new THREE.Scene();
    const room = new RoomEnvironment();
    const pmrem = new THREE.PMREMGenerator(renderer);
    const environment = pmrem.fromScene(room);
    world.environment = environment.texture;
    room.dispose();
    pmrem.dispose();
    const perspective = new THREE.PerspectiveCamera(38, 1, 0.001, 10000);
    const parallel = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.001, 10000);
    let camera: THREE.Camera = perspective;
    let orbit = new OrbitControls(camera, renderer.domElement);
    const gridHelp = new THREE.GridHelper(12, 24, '#39443e', '#2b3430');
    const axes = new THREE.AxesHelper(1.4);
    axes.position.y = 0.003;
    const hemi = new THREE.HemisphereLight('#e1ebff', '#b69b77', 1.5);
    const key = new THREE.DirectionalLight('#fff1db', 2.5);
    key.position.set(4, 5, 3);
    world.add(gridHelp, axes, hemi, key);
    const box = new THREE.Box3();
    const center = new THREE.Vector3();
    let radius = 1, aspect = 1;
    const outline = new THREE.Box3Helper(box, '#c5f16b');
    outline.visible = false;
    world.add(outline);
    let model: THREE.Group | undefined;
    let active = true;
    const maps = new Map<THREE.MeshStandardMaterial, THREE.Texture | null>();
    function resize() {
      const w = Math.max(target.clientWidth, 1), h = Math.max(target.clientHeight, 1);
      aspect = w / h;
      perspective.aspect = aspect;
      perspective.updateProjectionMatrix();
      parallel.left = -radius * 1.35 * aspect;
      parallel.right = radius * 1.35 * aspect;
      parallel.top = radius * 1.35;
      parallel.bottom = -radius * 1.35;
      parallel.updateProjectionMatrix();
      renderer.setSize(w, h);
    }
    function frame() {
      const distance = radius * 3.5 / Math.min(aspect, 1);
      orbit.target.copy(center);
      camera.position.copy(center).add(new THREE.Vector3(0.69, 0.46, 0.64).normalize().multiplyScalar(distance));
      if (camera === parallel) { parallel.zoom = Math.min(aspect, 1); parallel.updateProjectionMatrix(); }
      orbit.update();
    }
    controls.current = {
      frame,
      projection(value) {
        const position = camera.position.clone(), aim = orbit.target.clone();
        orbit.dispose();
        camera = value ? parallel : perspective;
        camera.position.copy(position);
        orbit = new OrbitControls(camera, renderer.domElement);
        orbit.target.copy(aim);
        orbit.update();
      },
      wire(value) { maps.forEach((_, material) => {material.wireframe = value;}); },
      grid(value) { gridHelp.visible = axes.visible = value; },
      bounds(value) { outline.visible = value; },
      texture(value) {
        maps.forEach((map, material) => { material.map = value ? map : null; material.needsUpdate = true; });
      },
      light(value) {
        key.intensity = value === 'Clay' ? 4 : value === 'Flat' ? 0.2 : 2.5;
        hemi.intensity = value === 'Flat' ? 3 : 1.5;
        world.environmentIntensity = value === 'Clay' ? 0.15 : 1;
      },
    };
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(target);
    resize();
    frame();
    new GLTFLoader().load(url, result => {
      if (!active) { dispose(result.scene); return; }
      model = result.scene;
      world.add(model);
      box.setFromObject(model);
      if (box.isEmpty()) { setStatus('This revision contains no previewable geometry.'); return; }
      box.getCenter(center);
      radius = Math.max(box.getBoundingSphere(new THREE.Sphere()).radius, 0.0001);
      gridHelp.position.set(center.x, box.min.y, center.z);
      gridHelp.scale.setScalar(radius / 1.4);
      axes.position.copy(gridHelp.position);
      axes.scale.setScalar(radius / 1.4);
      model.traverse(object => {
        if (object instanceof THREE.Mesh) {
          for (const material of [object.material].flat()) {
            if (material instanceof THREE.MeshStandardMaterial) maps.set(material, material.map);
          }
        }
      });
      renderer.domElement.dataset.baseColorTextures = String([...maps.values()].filter(Boolean).length);
      resize();
      frame();
      setStatus('');
    }, undefined, () => {if (active) setStatus('The GLB preview could not be loaded.');});
    renderer.setAnimationLoop(() => {orbit.update(); renderer.render(world, camera);});
    return () => {
      active = false;
      controls.current = null;
      resizeObserver.disconnect();
      renderer.setAnimationLoop(null);
      orbit.dispose();
      maps.forEach((map, material) => { material.map = map; });
      dispose(world);
      environment.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    };
  }, [url]);

  useEffect(() => { controls.current?.projection(ortho); }, [ortho, url]);
  useEffect(() => { controls.current?.wire(wire); }, [wire, url, status]);
  useEffect(() => { controls.current?.grid(grid); }, [grid, url]);
  useEffect(() => { controls.current?.bounds(bounds); }, [bounds, url, status]);
  useEffect(() => { controls.current?.light(lighting); }, [lighting, url]);
  useEffect(() => { controls.current?.texture(showTextures); }, [showTextures, url, status]);

  return <div className={minimal ? 'viewport mini' : 'viewport'}>
    <div ref={holder} className="canvas-host"/>
    <div className="viewport-top"><span><Box size={15}/>{name || '3D viewport'}</span>
      {url && <button onClick={() => setOrtho(!ortho)}><Move3d size={14}/>{ortho ? 'Orthographic' : 'Perspective'}</button>}
    </div>
    {!url ? <div className="viewport-message"><Box size={44}/><strong>No asset selected</strong><span>Import a mesh or generate an asset to begin.</span></div>
      : status && <div className="viewport-message" role="status">{status}</div>}
    {url && <div className="viewport-tools">
      <button title="Reset and frame model" aria-label="Frame model" onClick={() => controls.current?.frame()}><Focus size={17}/></button>
      <button title="Grid and axes" aria-label="Grid and axes" aria-pressed={grid} onClick={() => setGrid(!grid)}><Grid2X2 size={17}/></button>
      <button title="Wireframe" aria-label="Wireframe" aria-pressed={wire} onClick={() => setWire(!wire)}><Triangle size={17}/></button>
      <button title="Bounding box" aria-label="Bounding box" aria-pressed={bounds} onClick={() => setBounds(!bounds)}><Box size={17}/></button>
      <button title="Base-color textures" aria-label="Base-color textures" aria-pressed={showTextures} onClick={() => setTextures(!showTextures)}><Layers3 size={17}/></button>
      <label className="light-select"><Sun size={16}/><select aria-label="Lighting preset" value={lighting} onChange={e => setLighting(e.target.value)}>{['Studio', 'Clay', 'Flat'].map(item => <option key={item}>{item}</option>)}</select></label>
    </div>}
  </div>;
}

