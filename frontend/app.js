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
  draft: null,         // box being dragged: {start: LngLat, ring: [[lng,lat]...]}
  trees: [],           // existing trees from the backend: [{lng, lat, height, radius}]
  buildings: null,     // existing building footprints (GeoJSON, each with properties.idx)
  probe: null,         // last spot checked with the Temperature tool: {map, lngLat}
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
  map.addSource('buildings', { type: 'geojson', data: state.buildings || EMPTY });
  map.addSource('trees', { type: 'geojson', data: EMPTY });
  map.addSource('newtrees', { type: 'geojson', data: EMPTY });
  map.addSource('removedtrees', { type: 'geojson', data: EMPTY });
  map.addSource('edits', { type: 'geojson', data: EMPTY });
  map.addSource('draft', { type: 'geojson', data: EMPTY });

  map.addLayer({ id: 'edit-surface', type: 'fill', source: 'edits', filter: ['==', ['get', 'kind'], 'surface'],
    paint: { 'fill-color': ['get', 'color'], 'fill-opacity': heat ? 0.25 : 0.7 } });
  map.addLayer({ id: 'edit-outline', type: 'line', source: 'edits',
    paint: { 'line-color': ['get', 'color'], 'line-width': 2, 'line-dasharray': [2, 1] } });
  map.addLayer({ id: 'removed-trees', type: 'fill', source: 'removedtrees',
    paint: { 'fill-color': '#ff3b30', 'fill-opacity': 0.35, 'fill-outline-color': '#ff3b30' } });
  map.addLayer({ id: 'buildings-3d', type: 'fill-extrusion', source: 'buildings',
    paint: { 'fill-extrusion-color': heat ? '#9aa3a0' : '#e8dfd0', 'fill-extrusion-height': ['get', 'height'],
             'fill-extrusion-opacity': heat ? 0.75 : 0.9 } });
  const treePaint = (opacity) => ({ 'fill-extrusion-color': ['get', 'color'], 'fill-extrusion-height': ['get', 'height'],
    'fill-extrusion-base': ['get', 'base'], 'fill-extrusion-opacity': opacity });
  map.addLayer({ id: 'trees-3d', type: 'fill-extrusion', source: 'trees', paint: treePaint(heat ? 0.4 : 0.9) });
  map.addLayer({ id: 'newtrees-3d', type: 'fill-extrusion', source: 'newtrees', paint: treePaint(heat ? 0.6 : 0.95) });
  map.addLayer({ id: 'edit-3d', type: 'fill-extrusion', source: 'edits', filter: ['>', ['get', 'height'], 0],
    paint: { 'fill-extrusion-color': ['get', 'color'], 'fill-extrusion-height': ['get', 'height'],
             'fill-extrusion-base': ['get', 'base'], 'fill-extrusion-opacity': heat ? 0.5 : 0.9 } });
  map.addLayer({ id: 'draft-fill', type: 'fill', source: 'draft', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.25 } });
  map.addLayer({ id: 'draft-line', type: 'line', source: 'draft', paint: { 'line-color': ['get', 'color'], 'line-width': 2 } });
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

// A tree drawn as a trunk plus a round crown, stacked from flat discs (MapLibre can only extrude flat shapes).
const CROWN_TIERS = 6;
function treeModel(lng, lat, h, r, greens) {
  const feats = [];
  const crownBase = 0.3 * h, half = (h - crownBase) / 2, mid = crownBase + half, step = (h - crownBase) / CROWN_TIERS;
  feats.push({ type: 'Feature', geometry: circle(lng, lat, Math.max(0.25, r * 0.08), 8),
    properties: { color: '#6b4a2b', base: 0, height: crownBase + step } });
  for (let i = 0; i < CROWN_TIERS; i++) {
    const z0 = crownBase + i * step, zMid = z0 + step / 2;
    const ri = r * Math.sqrt(Math.max(0.05, 1 - ((zMid - mid) / half) ** 2));
    feats.push({ type: 'Feature', geometry: circle(lng, lat, ri, 14),
      properties: { color: greens[i % greens.length], base: z0, height: z0 + step } });
  }
  return feats;
}
const OLD_GREENS = ['#3e7a35', '#4a8a3e'];
const NEW_GREENS = ['#2fa84f', '#3cbf5c'];

function inside(pt, poly) {  // point-in-polygon (outer ring only)
  const ring = poly.coordinates[0];
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

function refreshTrees() {
  // existing trees disappear when any remove box covers them; a placed tree only if a LATER remove box covers it
  const removes = state.edits.map((e, i) => (e.type === 'remove_trees' ? [i, e.geometry] : null)).filter(Boolean);
  const gone = (t) => removes.some(([, g]) => inside([t.lng, t.lat], g));
  const oldTrees = state.trees.filter((t) => !gone(t)).flatMap((t) => treeModel(t.lng, t.lat, t.height, t.radius, OLD_GREENS));
  const removed = state.trees.filter(gone).map((t) => ({ type: 'Feature', geometry: circle(t.lng, t.lat, t.radius, 14), properties: {} }));
  const sizes = state.area.tree_sizes;
  const newTrees = state.edits.flatMap((e, i) => {
    if (e.type !== 'add_tree') return [];
    const p = e.geometry.coordinates;
    if (removes.some(([j, g]) => j > i && inside(p, g))) return [];
    const [h, r] = sizes[e.size];
    return treeModel(p[0], p[1], h, r, NEW_GREENS);
  });
  maps.forEach((m) => {
    m.getSource('trees')?.setData({ type: 'FeatureCollection', features: oldTrees });
    m.getSource('newtrees')?.setData({ type: 'FeatureCollection', features: newTrees });
    m.getSource('removedtrees')?.setData({ type: 'FeatureCollection', features: removed });
  });
}

function insideGeom(pt, g) {
  if (g.type === 'Polygon') return inside(pt, g);
  if (g.type === 'MultiPolygon') return g.coordinates.some((c) => inside(pt, { coordinates: c }));
  return false;
}

// buildings knocked down with the Demolish tool: existing ones by idx, new ones by their edit index
function demolished() {
  const idx = new Set(), edits = new Set();
  state.edits.forEach((e) => {
    if (e.demolish?.idx != null) idx.add(e.demolish.idx);
    if (e.demolish?.edit != null) edits.add(e.demolish.edit);
  });
  return { idx, edits };
}

function editFeatures() {
  const gone = demolished().edits;
  return {
    type: 'FeatureCollection',
    features: state.edits.map((e, i) => [e, i]).filter(([e, i]) => e.type !== 'add_tree' && !gone.has(i)).map(([e, i]) => {
      if (e.type === 'surface') return { type: 'Feature', geometry: e.geometry, properties: { kind: 'surface', color: SURFACE_COLORS[e.surface], height: 0, base: 0 } };
      if (e.type === 'building') return { type: 'Feature', geometry: e.geometry,
        properties: { kind: 'building', editIndex: i, color: e.height > 0 ? '#c9a227' : '#ff3b30', height: e.height, base: 0 } };
      return { type: 'Feature', geometry: e.geometry, properties: { kind: 'remove', color: '#ff3b30', height: 0, base: 0 } };
    }),
  };
}

function refreshEdits() {
  const fc = editFeatures();
  const gone = [...demolished().idx];
  maps.forEach((m) => {
    m.getSource('edits')?.setData(fc);
    if (m.getLayer('buildings-3d')) m.setFilter('buildings-3d', ['!', ['in', ['get', 'idx'], ['literal', gone]]]);
  });
  refreshTrees();
  refreshChanges();
  $('simulate').querySelector('span').textContent = state.edits.length ? `Simulate (${state.edits.length})` : 'Simulate';
}

function toolColor() {
  if (state.tool === 'surface') return SURFACE_COLORS[$('surfaceType').value];
  if (state.tool === 'building') return Number($('buildingHeight').value) > 0 ? '#c9a227' : '#ff3b30';
  return '#ff3b30';
}

function refreshDraft() {
  const ring = state.draft?.ring;
  const feats = ring ? [{ type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: { color: toolColor() } }] : [];
  maps.forEach((m) => m.getSource('draft')?.setData({ type: 'FeatureCollection', features: feats }));
}

// ---------------------------------------------------------------- your changes list

const CHANGE_ICONS = { add_tree: 'ph-tree', remove_trees: 'ph-axe', surface: 'ph-paint-roller', building: 'ph-buildings' };
const SURFACE_NAMES = { grass: 'grass', cool_asphalt: 'cool asphalt', asphalt: 'dark asphalt', paving: 'paving', soil: 'bare soil', water: 'water' };

function describe(e) {
  if (e.type === 'add_tree') return `Planted a ${e.size} tree`;
  if (e.type === 'remove_trees') return 'Removed trees';
  if (e.type === 'surface') return `Changed ground to ${SURFACE_NAMES[e.surface]}`;
  if (e.demolish) return 'Demolished a building';
  return `Building, ${e.height} m tall`;
}

let shownChanges = 0;
function refreshChanges() {
  const box = $('changes');
  box.innerHTML = state.edits.map((e, i) => {
    const icon = e.demolish ? 'ph-bulldozer' : CHANGE_ICONS[e.type];
    const dot = e.type === 'surface' ? `<span class="dot" style="background:${SURFACE_COLORS[e.surface]}"></span>` : '';
    return `<button class="chip${i >= shownChanges ? ' new' : ''}" data-i="${i}" title="${describe(e)}. Click to remove." aria-label="Remove: ${describe(e)}">`
      + `<i class="ph ${icon} kind"></i><i class="ph ph-trash bin"></i>${dot}</button>`;
  }).join('');
  shownChanges = state.edits.length;
  box.scrollLeft = box.scrollWidth;
}

$('changes').addEventListener('click', (ev) => {
  const chip = ev.target.closest('.chip');
  if (chip) removeEdit(Number(chip.dataset.i));
});

// remove one edit (and any demolish of it), keeping the other edits' references right
function removeEdit(i) {
  // drop the edit (and a demolish of it), then re-map demolish.edit indexes to the new positions
  const newIndex = [];
  let k = 0;
  state.edits.forEach((e, j) => { newIndex[j] = (j === i || e.demolish?.edit === i) ? -1 : k++; });
  state.edits = state.edits.filter((e, j) => newIndex[j] >= 0)
    .map((e) => (e.demolish?.edit != null ? { ...e, demolish: { edit: newIndex[e.demolish.edit] } } : e));
  shownChanges = state.edits.length;
  state.result = null;
  refreshEdits();
  render();
}

function addEdit(e) {
  state.edits.push(e);
  state.result = null;  // results are stale until re-simulated
  refreshEdits();
  render();
}

// ---------------------------------------------------------------- tools

document.querySelectorAll('[data-tool]').forEach((b) => b.addEventListener('click', () => setTool(b.dataset.tool)));

const BOX_TOOLS = ['remove_trees', 'surface', 'building'];
const HINTS = {
  probe: 'Click any spot on either map to see how hot it feels there.',
  demolish: 'Click a building (existing or one you added) to knock it down. Undo brings it back.',
  tree: 'Click (or tap) on either map to plant a tree.',
  remove_trees: 'Drag a box over trees to remove them. Esc cancels.',
  surface: 'Drag a box over the ground to change its surface. Esc cancels.',
  building: 'Drag a box to add a building, or over one to change its height. Esc cancels.',
};

function setTool(tool) {
  if (tool !== 'probe') closeProbe();
  state.tool = tool;
  state.draft = null;
  refreshDraft();
  document.querySelectorAll('[data-tool]').forEach((b) => b.classList.toggle('active', b.dataset.tool === tool));
  document.querySelectorAll('.option').forEach((o) => o.classList.toggle('show', o.dataset.for === tool));
  $('drawHint').textContent = HINTS[tool] || '';
  $('drawHint').classList.toggle('hidden', !HINTS[tool]);
  const box = BOX_TOOLS.includes(tool);
  maps.forEach((m) => {
    m.getCanvas().style.cursor = tool === 'pan' ? '' : tool === 'demolish' ? 'pointer' : 'crosshair';
    if (box) { m.dragPan.disable(); m.touchZoomRotate.disable(); } else { m.dragPan.enable(); m.touchZoomRotate.enable(); }
  });
}

// A box on the ground, lined up with the screen (so it looks like a rectangle at any map rotation).
function groundBox(a, b, bearing) {
  const mLat = 111320, mLng = 111320 * Math.cos(a.lat * Math.PI / 180);
  const dx = (b.lng - a.lng) * mLng, dy = (b.lat - a.lat) * mLat;  // metres east, north
  const t = bearing * Math.PI / 180;
  const right = [Math.cos(t), -Math.sin(t)], up = [Math.sin(t), Math.cos(t)];
  const du = dx * right[0] + dy * right[1], dv = dx * up[0] + dy * up[1];
  const pt = (u, v) => [a.lng + (u * right[0] + v * up[0]) / mLng, a.lat + (u * right[1] + v * up[1]) / mLat];
  return { ring: [pt(0, 0), pt(du, 0), pt(du, dv), pt(0, dv), pt(0, 0)], size: Math.min(Math.abs(du), Math.abs(dv)) };
}

function finishBox() {
  const d = state.draft;
  state.draft = null;
  refreshDraft();
  if (!d || d.size < 1) return;  // just a click, not a drag
  const geometry = { type: 'Polygon', coordinates: [d.ring] };
  if (state.tool === 'remove_trees') addEdit({ type: 'remove_trees', geometry });
  if (state.tool === 'surface') addEdit({ type: 'surface', surface: $('surfaceType').value, geometry });
  if (state.tool === 'building') addEdit({ type: 'building', height: Math.max(0, Number($('buildingHeight').value) || 0), geometry });
}

maps.forEach((m) => {
  m.on('click', (ev) => {
    if (state.tool === 'tree') addEdit({ type: 'add_tree', size: $('treeSize').value, geometry: { type: 'Point', coordinates: [ev.lngLat.lng, ev.lngLat.lat] } });
    if (state.tool === 'probe') { state.probe = { map: m, lngLat: ev.lngLat }; probe(); }
    if (state.tool === 'demolish') demolish(m, ev);
  });
  const start = (ev) => {
    if (!BOX_TOOLS.includes(state.tool)) return;
    if (ev.originalEvent.touches && ev.originalEvent.touches.length > 1) return;
    ev.preventDefault();
    state.draft = { start: ev.lngLat, ring: null, size: 0 };
  };
  const move = (ev) => {
    if (!state.draft) return;
    Object.assign(state.draft, groundBox(state.draft.start, ev.lngLat, m.getBearing()));
    refreshDraft();
  };
  m.on('mousedown', start);
  m.on('touchstart', start);
  m.on('mousemove', move);
  m.on('touchmove', move);
  m.on('touchend', finishBox);
});
document.addEventListener('mouseup', finishBox);

// ---------------------------------------------------------------- demolish

function pickBuilding(m, ev) {
  const { idx: goneIdx, edits: goneEdits } = demolished();
  // 1) the building drawn under the cursor (works when clicking a wall or roof in 3D)
  const layers = ['edit-3d', 'buildings-3d'].filter((id) => m.getLayer(id) && m.getLayoutProperty(id, 'visibility') !== 'none');
  for (const f of m.queryRenderedFeatures(ev.point, { layers })) {
    if (f.layer.id === 'edit-3d' && f.properties.editIndex != null) return { edit: f.properties.editIndex };
    if (f.layer.id === 'buildings-3d' && f.properties.idx != null) return { idx: f.properties.idx };
  }
  // 2) otherwise the footprint under the clicked ground point (e.g. 3D blocks hidden on the photo view)
  const p = [ev.lngLat.lng, ev.lngLat.lat];
  for (let i = state.edits.length - 1; i >= 0; i--) {
    const e = state.edits[i];
    if (e.type === 'building' && e.height > 0 && !e.demolish && !goneEdits.has(i) && insideGeom(p, e.geometry)) return { edit: i };
  }
  const b = (state.buildings?.features || []).find((f) => !goneIdx.has(f.properties.idx) && insideGeom(p, f.geometry));
  return b ? { idx: b.properties.idx } : null;
}

function demolish(m, ev) {
  const pick = pickBuilding(m, ev);
  if (!pick) { $('status').textContent = 'No building there. Click on a building to demolish it.'; return; }
  const geometry = pick.edit != null ? state.edits[pick.edit].geometry
    : state.buildings.features.find((f) => f.properties.idx === pick.idx).geometry;
  addEdit({ type: 'building', height: 0, geometry, demolish: pick });
}

// ---------------------------------------------------------------- temperature at a spot

let probePopup = null, probeRequest = 0;
function closeProbe() {
  state.probe = null;
  probePopup?.remove();
  probePopup = null;
}

async function probe() {
  const p = state.probe;
  if (!p) return;
  const req = ++probeRequest;
  probePopup?.remove();
  probePopup = new maplibregl.Popup({ closeButton: true, maxWidth: '260px' }).setLngLat(p.lngLat)
    .setHTML(`<div class="probe">Checking ${fmtHour(state.hour)}…</div>`).addTo(p.map);
  probePopup.on('close', () => { if (req === probeRequest) state.probe = null; });
  const q = new URLSearchParams({ lon: p.lngLat.lng, lat: p.lngLat.lat, hour: state.hour });
  if (state.date) q.set('date', state.date);
  if (state.result?.sim_id) q.set('sim_id', state.result.sim_id);
  let html;
  try {
    const r = await api(`point?${q}`);
    const shade = (s) => (s ? 'in shade' : 'in sun');
    const t = (v) => (v == null ? 'n/a' : `${v.toFixed(1)} °C`);
    if (r.after != null && r.before != null && Math.abs(r.after - r.before) >= 0.1) {
      const d = r.after - r.before;
      html = `Feels like <b>${t(r.after)}</b> after your changes`
        + `<br><span class="${d < 0 ? 'cool' : 'hot'}">${d < 0 ? '' : '+'}${d.toFixed(1)} °C</span> (was ${t(r.before)}, ${r.surface_before === 'roof' && r.surface !== 'roof' ? 'on a rooftop' : shade(r.shade_before)})`
        + `<br><small>${fmtHour(r.hour)} · air ${t(r.air_temp)} · ${r.surface} · ${shade(r.shade_after)}</small>`;
    } else {
      html = `Feels like <b>${t(r.before)}</b>`
        + `<br><small>${fmtHour(r.hour)} · air ${t(r.air_temp)} · ${r.surface} · ${shade(r.shade_before)}</small>`
        + (state.edits.length && !state.result ? '<br><small>Press Simulate to see the effect of your changes here.</small>' : '');
    }
  } catch (err) {
    html = /409/.test(err.message) ? 'The heat map is still loading. Try again in a moment.'
      : /404/.test(err.message) ? 'That spot is outside the study area.' : `Couldn't check this spot (${err.message}).`;
  }
  if (req === probeRequest && probePopup) probePopup.setHTML(`<div class="probe">${html}</div>`);
}  // also catches a drag released outside the map

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') { state.draft = null; refreshDraft(); }
});
$('undo').onclick = () => { state.edits.pop(); shownChanges = state.edits.length; state.result = null; refreshEdits(); render(); };
$('clear').onclick = () => { state.edits = []; state.result = null; refreshEdits(); render(); };

// ---------------------------------------------------------------- time + weather

const fmtHour = (h) => (h === 12 ? '12 pm' : h > 12 ? `${h - 12} pm` : `${h} am`);
$('hour').oninput = (ev) => { state.hour = Number(ev.target.value); render(); };
$('hour').onchange = () => probe();  // update the temperature popup once the slider is let go
$('date').onchange = (ev) => { state.date = ev.target.value; state.result = null; loadBaseline(); };
$('live').onclick = () => { $('date').value = ''; state.date = ''; state.result = null; loadBaseline(); };
document.querySelectorAll('input[name=heatMode]').forEach((r) => r.addEventListener('change', (ev) => { state.heatMode = ev.target.value; render(); }));

// ---------------------------------------------------------------- rendering

function legend() {
  const [lo, hi] = state.area.utci_range;
  const c = state.area.change_range;
  $('legend').innerHTML = state.heatMode === 'heat'
    ? `<b>Feels like</b>, °C<div class="bar" style="background:linear-gradient(90deg,#000004,#57106e,#bc3754,#f98e09,#fcffa4)"></div><div class="ticks"><span>${lo}</span><span>${(lo + hi) / 2}</span><span>${hi}+</span></div>`
    : `<b>Change</b> after your edits, °C<div class="bar" style="background:linear-gradient(90deg,#053061,#4393c3,#f7f7f7,#d6604d,#67001f)"></div><div class="ticks"><span>−${c} cooler</span><span>0</span><span>+${c} hotter</span></div>`;
  $('heatTitle').textContent = state.heatMode === 'heat'
    ? '"Feels like" temperature for people outside' : 'Blue is cooler, red is hotter, after your changes';
}

function render() {
  if (!state.area) return;
  $('hourLabel').textContent = fmtHour(state.hour);
  legend();
  const base = state.baseline?.hours.find((h) => h.hour === state.hour);
  const res = state.result?.hours.find((h) => h.hour === state.hour);
  const showChange = state.heatMode === 'change';

  setImage(mapHeat, 'heat', showChange ? null : base?.utci_png);
  setImage(mapReal, 'shadow', res?.shadow_full_png || base?.shadow_png);
  const rb = state.result?.bounds;
  setImage(mapHeat, 'patch', res ? (showChange ? res.change_png : res.utci_png) : null, rb);
  setImage(mapReal, 'patch-shadow', res?.shadow_full_png ? null : res?.shadow_png, rb);
  setImage(mapHeat, 'patch-shadow', null, rb);

  const card = $('card');
  if (res) {
    const d = res.utci_change_mean, best = res.utci_change_min, worst = res.utci_change_max;
    const cooler = d <= 0;
    card.innerHTML = `<div class="big ${cooler ? 'cool' : 'hot'}">${cooler ? '−' : '+'}${Math.abs(d).toFixed(1)} °C</div>`
      + `<div class="lines"><strong>${cooler ? 'Cooler' : 'Hotter'} near your changes at ${fmtHour(res.hour)}</strong>`
      + `<span>Best spot ${best.toFixed(1)} °C${worst > 0.5 ? `, some spots <b class="hot">+${worst.toFixed(1)} °C</b>` : ''}. Air ${res.air_temp.toFixed(0)} °C.</span></div>`;
  } else if (base) {
    card.innerHTML = `<div class="big">${base.utci_ground_mean.toFixed(0)} °C</div>`
      + `<div class="lines"><strong>How hot the streets feel at ${fmtHour(base.hour)}</strong>`
      + `<span>Air ${base.air_temp.toFixed(0)} °C. ${state.edits.length ? 'Press Simulate to see what your changes do.' : 'Plant a tree or change a surface, then press Simulate.'}</span></div>`;
  }
}

// ---------------------------------------------------------------- API calls

async function api(path, opts) {
  const r = await fetch(`${API}/api/${path}`, opts);
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json();
}

function setLoading(msg) {
  document.querySelectorAll('.loading').forEach((el) => {
    el.classList.toggle('hidden', !msg);
    el.querySelector('.msg').textContent = msg || '';
  });
}

const fmtDate = (d) => new Date(`${d}T00:00`).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' });

// plain-language line about where the weather and buildings come from
function showStatus() {
  const src = state.baseline?.weather_source;
  const when = state.date ? fmtDate(state.date) : 'live';
  const weather = src === 'fallback hot day' ? 'built-in hot day (no internet weather)' : `${when}, from Open-Meteo`;
  $('status').innerHTML = `Weather: ${weather}`
    + (state.area?.source === 'synthetic demo' ? '<br><span class="warn">Demo area with made-up buildings</span>' : '');
}

let baselineRequest = 0;
async function loadBaseline() {
  const req = ++baselineRequest;  // ignore answers to older requests if the date changed meanwhile
  const what = state.date ? `weather and heat for ${fmtDate(state.date)}` : "today's live weather and heat";
  setLoading(`Loading ${what}…`);
  render();  // drop results from the previous day straight away
  $('live').classList.toggle('on', !state.date);
  $('status').textContent = 'Working out the heat for the whole area. A new day takes about a minute.';
  try {
    const baseline = await api(`baseline${state.date ? `?date=${state.date}` : ''}`);
    if (req !== baselineRequest) return;
    state.baseline = baseline;
    showStatus();
  } catch (err) {
    if (req !== baselineRequest) return;
    $('status').textContent = `Couldn't load heat map: ${err.message}`;
  }
  setLoading(null);
  render();
  probe();
}

$('simulate').onclick = async () => {
  if (!state.edits.length) { $('status').textContent = 'Add a tree or change a surface first.'; return; }
  const btn = $('simulate');
  btn.disabled = true;
  btn.querySelector('span').textContent = 'Simulating…';
  setLoading('Simulating your changes…');
  try {
    state.result = await api('simulate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edits: state.edits.map(({ demolish: _, ...e }) => e), date: state.date || null, hours: state.area.hours }),
    });
    showStatus();
    probe();
  } catch (err) {
    $('status').textContent = `Simulation failed: ${err.message}`;
  }
  setLoading(null);
  btn.disabled = false;
  refreshEdits();
  render();
};

// ---------------------------------------------------------------- welcome + example

function closeWelcome() {
  $('welcome').classList.add('hidden');
  try { localStorage.setItem('coolblocks-welcomed', '1'); } catch (e) { /* private mode */ }
}
$('welcomeClose').onclick = closeWelcome;
$('welcomeExample').onclick = () => { closeWelcome(); runExample(); };
try { if (!localStorage.getItem('coolblocks-welcomed')) $('welcome').classList.remove('hidden'); } catch (e) { $('welcome').classList.remove('hidden'); }

// plant a row of street trees along the road nearest the middle of the area, then simulate
async function runExample() {
  if (!state.area) return;
  try {
    const { points, date } = await api('example');
    if (!points.length) throw new Error('no street found');
    if (date && state.date !== date) {  // a known hot day, so the difference is clear
      state.date = date;
      $('date').value = date;
      state.result = null;
      await loadBaseline();
    }
    points.forEach((p) => state.edits.push({ type: 'add_tree', size: 'medium', geometry: { type: 'Point', coordinates: p } }));
    const lng = points.reduce((a, p) => a + p[0], 0) / points.length, lat = points.reduce((a, p) => a + p[1], 0) / points.length;
    mapReal.easeTo({ center: [lng, lat], zoom: 17.6, duration: 800 });
    state.result = null;
    refreshEdits();
    render();
    $('simulate').click();
  } catch (err) {
    $('status').textContent = `Couldn't make the example: ${err.message}`;
  }
}
$('example').onclick = runExample;

// ---------------------------------------------------------------- phone: one map at a time

document.querySelectorAll('[data-view]').forEach((b) => b.tagName === 'BUTTON' && b.addEventListener('click', () => {
  document.querySelector('main').dataset.view = b.dataset.view;
  document.querySelectorAll('.view-tabs button').forEach((x) => x.classList.toggle('active', x === b));
  maps.forEach((m) => m.resize());
}));

// ---------------------------------------------------------------- start

(async function init() {
  try {
    setLoading('Loading the area…');
    state.area = await api('area');
    const [trees, buildings] = await Promise.all([api('trees'), api('buildings')]);
    state.buildings = buildings;
    state.trees = trees.features.map((f) => ({ lng: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], ...f.properties }));
  } catch (err) {
    setLoading(null);
    $('card').textContent = `Can't reach the server (${err.message}). Is "uvicorn server:app" running?`;
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
  refreshTrees();
  mapReal.fitBounds([[w, s], [e, n]], { padding: 20, pitch: 55, bearing: -20, duration: 0 });
  $('hour').min = Math.min(...state.area.hours);
  $('hour').max = Math.max(...state.area.hours);
  render();
  loadBaseline();
})();
