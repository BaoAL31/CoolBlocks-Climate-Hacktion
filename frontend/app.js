// CoolBlocks frontend: two synced 3D maps (real-world + heat), edit tools, time slider.
// Talks to the FastAPI backend in ../backend (same origin by default; override with ?api=http://host:8000).

const API = new URLSearchParams(location.search).get('api') || '';
const $ = (id) => document.getElementById(id);

const state = {
  area: null,
  baseline: null,      // {bounds, hours:[{hour, utci_png, shadow_png, ...}]}
  result: null,        // last /simulate response
  edits: [],           // edits sent to the backend
  tool: 'pan',
  draft: [],           // polygon corners being drawn
  hour: 15,
  date: '',            // '' = live
  heatMode: 'heat',
};

// ---------------------------------------------------------------- basemaps

const IMAGERY = {  // NSW Spatial Services imagery (CC BY 4.0)
  type: 'raster', tileSize: 256, maxzoom: 21,
  tiles: ['https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Imagery/MapServer/tile/{z}/{y}/{x}'],
  attribution: '© Spatial Services NSW (backup imagery © Esri)',
};
const IMAGERY_BACKUP = [  // Esri World Imagery, used automatically if the NSW tiles fail to load
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'];
const OSM = {
  type: 'raster', tileSize: 256, maxzoom: 19,
  tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
  attribution: '© OpenStreetMap contributors',
};
const EMPTY = { type: 'FeatureCollection', features: [] };
const BLANK_PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';

function makeMap(container, base, baseDim) {
  return new maplibregl.Map({
    container,
    style: {
      version: 8,
      sources: { base },
      layers: [
        { id: 'bg', type: 'background', paint: { 'background-color': '#dfe3df' } },
        { id: 'base', type: 'raster', source: 'base',
          paint: baseDim ? { 'raster-saturation': -1, 'raster-brightness-max': 0.75 } : {} },
      ],
    },
    center: [151.187, -33.888], zoom: 16.3, pitch: 55, bearing: -20,
    maxPitch: 75, attributionControl: { compact: true },
  });
}

const mapReal = makeMap('mapReal', IMAGERY, false);
const mapHeat = makeMap('mapHeat', OSM, true);
const maps = [mapReal, mapHeat];
const mapsReady = Promise.all(maps.map((m) => new Promise((res) => m.once('load', res))));

// keep the two cameras in sync
let syncing = false;
function sync(from, to) {
  from.on('move', () => {
    if (syncing) return;
    syncing = true;
    to.jumpTo({ center: from.getCenter(), zoom: from.getZoom(), bearing: from.getBearing(), pitch: from.getPitch() });
    syncing = false;
  });
}
sync(mapReal, mapHeat);

// fall back to Esri imagery if NSW imagery tiles keep failing
let imageryErrors = 0;
mapReal.on('error', (ev) => {
  if (ev.sourceId === 'base' && ++imageryErrors === 4) {
    mapReal.getSource('base').setTiles(IMAGERY_BACKUP);
  }
});

// real-world view: photo first; existing buildings/trees as 3D blocks only if asked
function applyRealToggles() {
  const blocks = $('blocks').checked ? 'visible' : 'none';
  ['buildings-3d', 'trees-3d'].forEach((id) => mapReal.getLayer(id) && mapReal.setLayoutProperty(id, 'visibility', blocks));
  const shade = $('shade').checked ? 'visible' : 'none';
  ['shadow', 'patch-shadow'].forEach((id) => mapReal.getLayer(id) && mapReal.setLayoutProperty(id, 'visibility', shade));
}
$('blocks').onchange = applyRealToggles;
$('shade').onchange = applyRealToggles;
sync(mapHeat, mapReal);
maps.forEach((m) => m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-left'));

// ---------------------------------------------------------------- layers

function corners(b) {  // [[w,s],[e,n]] -> image corners (TL, TR, BR, BL)
  const [[w, s], [e, n]] = b;
  return [[w, n], [e, n], [e, s], [w, s]];
}

function addImage(map, id, before) {
  map.addSource(id, { type: 'image', url: BLANK_PNG, coordinates: corners(state.area.bounds) });
  map.addLayer({ id, type: 'raster', source: id, paint: { 'raster-fade-duration': 0, 'raster-resampling': 'nearest' } }, before);
}

function setImage(map, id, url, bounds) {
  const src = map.getSource(id);
  if (src) src.updateImage({ url: url || BLANK_PNG, coordinates: corners(bounds || state.area.bounds) });
}

function addVectorLayers(map, heat) {
  map.addSource('buildings', { type: 'geojson', data: `${API}/api/buildings` });
  map.addSource('trees', { type: 'geojson', data: `${API}/api/trees` });
  map.addSource('edits', { type: 'geojson', data: EMPTY });
  map.addSource('draft', { type: 'geojson', data: EMPTY });

  map.addLayer({ id: 'edit-surface', type: 'fill', source: 'edits', filter: ['==', ['get', 'kind'], 'surface'],
    paint: { 'fill-color': ['get', 'color'], 'fill-opacity': heat ? 0.25 : 0.7 } });
  map.addLayer({ id: 'edit-outline', type: 'line', source: 'edits', filter: ['!=', ['get', 'kind'], 'tree'],
    paint: { 'line-color': ['get', 'color'], 'line-width': 2, 'line-dasharray': [2, 1] } });
  map.addLayer({ id: 'buildings-3d', type: 'fill-extrusion', source: 'buildings',
    paint: { 'fill-extrusion-color': heat ? '#9aa3a0' : '#e8dfd0', 'fill-extrusion-height': ['get', 'height'],
             'fill-extrusion-opacity': heat ? 0.75 : 0.9 } });
  map.addLayer({ id: 'trees-3d', type: 'fill-extrusion', source: 'trees',
    paint: { 'fill-extrusion-color': '#3f8f4a', 'fill-extrusion-height': ['get', 'height'],
             'fill-extrusion-base': ['*', 0.35, ['get', 'height']], 'fill-extrusion-opacity': heat ? 0.35 : 0.85 } });
  map.addLayer({ id: 'edit-3d', type: 'fill-extrusion', source: 'edits', filter: ['>', ['get', 'height'], 0],
    paint: { 'fill-extrusion-color': ['get', 'color'], 'fill-extrusion-height': ['get', 'height'],
             'fill-extrusion-base': ['get', 'base'], 'fill-extrusion-opacity': heat ? 0.5 : 0.9 } });
  map.addLayer({ id: 'draft-line', type: 'line', source: 'draft', paint: { 'line-color': '#ff3b30', 'line-width': 2 } });
  map.addLayer({ id: 'draft-pts', type: 'circle', source: 'draft', filter: ['==', ['geometry-type'], 'Point'],
    paint: { 'circle-radius': 4, 'circle-color': '#ff3b30' } });
}

// ---------------------------------------------------------------- edits -> display

const SURFACE_COLORS = { grass: '#5cb85c', cool_asphalt: '#d9d9d9', asphalt: '#333333', paving: '#b8a58c', soil: '#a0703c', water: '#3a8fd8' };

function circle(lng, lat, r, n = 20) {  // r in metres
  const dLat = r / 111320, dLng = r / (111320 * Math.cos(lat * Math.PI / 180));
  const ring = [];
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * 2 * Math.PI;
    ring.push([lng + dLng * Math.cos(a), lat + dLat * Math.sin(a)]);
  }
  return { type: 'Polygon', coordinates: [ring] };
}

function editFeatures() {
  const sizes = state.area.tree_sizes;
  return {
    type: 'FeatureCollection',
    features: state.edits.map((e) => {
      if (e.type === 'add_tree') {
        const [h, r] = sizes[e.size];
        const [lng, lat] = e.geometry.coordinates;
        return { type: 'Feature', geometry: circle(lng, lat, r), properties: { kind: 'tree', color: '#2f9e44', height: h, base: h * 0.35 } };
      }
      if (e.type === 'surface') return { type: 'Feature', geometry: e.geometry, properties: { kind: 'surface', color: SURFACE_COLORS[e.surface], height: 0, base: 0 } };
      if (e.type === 'building') return { type: 'Feature', geometry: e.geometry,
        properties: { kind: 'building', color: e.height > 0 ? '#c9a227' : '#ff3b30', height: e.height, base: 0 } };
      return { type: 'Feature', geometry: e.geometry, properties: { kind: 'remove', color: '#ff3b30', height: 0, base: 0 } };
    }),
  };
}

function refreshEdits() {
  const fc = editFeatures();
  maps.forEach((m) => m.getSource('edits')?.setData(fc));
  $('simulate').textContent = state.edits.length ? `Simulate (${state.edits.length})` : 'Simulate';
}

function refreshDraft() {
  const pts = state.draft;
  const feats = pts.map((p) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: p } }));
  if (pts.length > 1) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: pts } });
  maps.forEach((m) => m.getSource('draft')?.setData({ type: 'FeatureCollection', features: feats }));
}

function addEdit(e) {
  state.edits.push(e);
  state.result = null;  // results are stale until re-simulated
  refreshEdits();
  render();
}

// ---------------------------------------------------------------- tools

document.querySelectorAll('[data-tool]').forEach((b) => b.addEventListener('click', () => setTool(b.dataset.tool)));

function setTool(tool) {
  state.tool = tool;
  state.draft = [];
  refreshDraft();
  document.querySelectorAll('[data-tool]').forEach((b) => b.classList.toggle('active', b.dataset.tool === tool));
  const drawing = ['remove_trees', 'surface', 'building'].includes(tool);
  $('drawHint').classList.toggle('hidden', !drawing);
  maps.forEach((m) => {
    m.getCanvas().style.cursor = tool === 'pan' ? '' : 'crosshair';
    if (drawing) m.doubleClickZoom.disable(); else m.doubleClickZoom.enable();
  });
}

function finishPolygon() {
  if (state.draft.length < 3) return;
  const ring = [...state.draft, state.draft[0]];
  const geometry = { type: 'Polygon', coordinates: [ring] };
  if (state.tool === 'remove_trees') addEdit({ type: 'remove_trees', geometry });
  if (state.tool === 'surface') addEdit({ type: 'surface', surface: $('surfaceType').value, geometry });
  if (state.tool === 'building') addEdit({ type: 'building', height: Math.max(0, Number($('buildingHeight').value) || 0), geometry });
  state.draft = [];
  refreshDraft();
}

maps.forEach((m) => {
  m.on('click', (ev) => {
    const p = [ev.lngLat.lng, ev.lngLat.lat];
    if (state.tool === 'tree') addEdit({ type: 'add_tree', size: $('treeSize').value, geometry: { type: 'Point', coordinates: p } });
    else if (state.tool !== 'pan') { state.draft.push(p); refreshDraft(); }
  });
  m.on('dblclick', (ev) => {
    if (['remove_trees', 'surface', 'building'].includes(state.tool)) {
      ev.preventDefault();
      state.draft.pop();  // the double-click also fired a click
      finishPolygon();
    }
  });
});

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter') finishPolygon();
  if (ev.key === 'Escape') { state.draft = []; refreshDraft(); }
});
$('undo').onclick = () => { state.edits.pop(); state.result = null; refreshEdits(); render(); };
$('clear').onclick = () => { state.edits = []; state.result = null; refreshEdits(); render(); };

// ---------------------------------------------------------------- time + weather

const fmtHour = (h) => (h === 12 ? '12 pm' : h > 12 ? `${h - 12} pm` : `${h} am`);
$('hour').oninput = (ev) => { state.hour = Number(ev.target.value); render(); };
$('date').onchange = (ev) => { state.date = ev.target.value; state.result = null; loadBaseline(); };
$('live').onclick = () => { $('date').value = ''; state.date = ''; state.result = null; loadBaseline(); };
document.querySelectorAll('input[name=heatMode]').forEach((r) => r.addEventListener('change', (ev) => { state.heatMode = ev.target.value; render(); }));

// ---------------------------------------------------------------- rendering

function legend() {
  const [lo, hi] = state.area.utci_range;
  const c = state.area.change_range;
  $('legend').innerHTML = state.heatMode === 'heat'
    ? `"Feels like" °C<div class="bar" style="background:linear-gradient(90deg,#000004,#57106e,#bc3754,#f98e09,#fcffa4)"></div><div class="ticks"><span>${lo}</span><span>${(lo + hi) / 2}</span><span>${hi}+</span></div>`
    : `Change after edits, °C<div class="bar" style="background:linear-gradient(90deg,#053061,#4393c3,#f7f7f7,#d6604d,#67001f)"></div><div class="ticks"><span>−${c} cooler</span><span>0</span><span>+${c} hotter</span></div>`;
}

function render() {
  if (!state.area) return;
  $('hourLabel').textContent = fmtHour(state.hour);
  legend();
  const base = state.baseline?.hours.find((h) => h.hour === state.hour);
  const res = state.result?.hours.find((h) => h.hour === state.hour);
  const showChange = state.heatMode === 'change';

  setImage(mapHeat, 'heat', showChange ? null : base?.utci_png);
  setImage(mapReal, 'shadow', base?.shadow_png);
  const rb = state.result?.bounds;
  setImage(mapHeat, 'patch', res ? (showChange ? res.change_png : res.utci_png) : null, rb);
  setImage(mapReal, 'patch-shadow', res?.shadow_png, rb);
  setImage(mapHeat, 'patch-shadow', null, rb);

  const card = $('card');
  if (res) {
    const d = res.utci_change_mean, best = res.utci_change_min, worst = res.utci_change_max;
    card.innerHTML = `At <b>${fmtHour(res.hour)}</b> (air ${res.air_temp.toFixed(0)} °C), near your changes it feels `
      + `<b class="${d > 0 ? 'hot' : ''}">${Math.abs(d).toFixed(1)} °C ${d <= 0 ? 'cooler' : 'hotter'}</b> on average`
      + ` (best spot ${best.toFixed(1)} °C${worst > 0.5 ? `, some spots <span class="hot">+${worst.toFixed(1)} °C</span>` : ''}).`;
  } else if (base) {
    card.innerHTML = `At <b>${fmtHour(base.hour)}</b>: air ${base.air_temp.toFixed(0)} °C, streets feel like <b>${base.utci_ground_mean.toFixed(0)} °C</b> on average. `
      + (state.edits.length ? 'Press <b>Simulate</b> to see the effect of your changes.' : 'Plant a tree or change a surface, then press Simulate.');
  }
}

// ---------------------------------------------------------------- API calls

async function api(path, opts) {
  const r = await fetch(`${API}/api/${path}`, opts);
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json();
}

async function loadBaseline() {
  $('status').textContent = 'Calculating heat for the whole area (first time takes a minute)…';
  try {
    state.baseline = await api(`baseline${state.date ? `?date=${state.date}` : ''}`);
    $('status').textContent = `Weather: ${state.baseline.weather_source}${state.date ? ` · ${state.date}` : ' · live'} · area: ${state.area.source}`
      + (state.area.source === 'synthetic demo' ? ' ⚠ made-up buildings: run scripts/build_area.py for real ones' : '');
  } catch (err) {
    $('status').textContent = `Couldn't load heat map: ${err.message}`;
  }
  render();
}

$('simulate').onclick = async () => {
  if (!state.edits.length) { $('status').textContent = 'Add a tree or change a surface first.'; return; }
  const btn = $('simulate');
  btn.disabled = true;
  btn.textContent = 'Simulating…';
  $('status').textContent = 'Running the shade and heat model for your changes…';
  try {
    state.result = await api('simulate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edits: state.edits, date: state.date || null, hours: state.area.hours }),
    });
    $('status').textContent = `Done · weather: ${state.result.weather_source}`;
  } catch (err) {
    $('status').textContent = `Simulation failed: ${err.message}`;
  }
  btn.disabled = false;
  refreshEdits();
  render();
};

// ---------------------------------------------------------------- start

(async function init() {
  try {
    state.area = await api('area');
  } catch (err) {
    $('card').textContent = `Can't reach the backend (${err.message}). Is "uvicorn server:app" running?`;
    return;
  }
  const [[w, s], [e, n]] = state.area.bounds;
  await mapsReady;
  // heat view: baseline heat, then patch result on top; real view: shadows
  addImage(mapHeat, 'heat');
  addImage(mapHeat, 'patch');
  addImage(mapHeat, 'patch-shadow');
  addImage(mapReal, 'shadow');
  addImage(mapReal, 'patch-shadow');
  addVectorLayers(mapReal, false);
  addVectorLayers(mapHeat, true);
  applyRealToggles();
  mapReal.fitBounds([[w, s], [e, n]], { padding: 20, pitch: 55, bearing: -20, duration: 0 });
  $('hour').min = Math.min(...state.area.hours);
  $('hour').max = Math.max(...state.area.hours);
  render();
  loadBaseline();
})();
