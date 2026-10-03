"""Validated tool surface and browser scene context for external MCP agents."""
from __future__ import annotations

import copy
from datetime import date
import functools
import json
import math
import secrets
import threading
import time
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely.geometry import shape


COMPUTE_LOCK = threading.RLock()
_STATUS_LOCK = threading.Lock()
_COMPUTE_STATUS = {'operation': None, 'started': None, 'waiting': 0}


def compute_status():
    with _STATUS_LOCK:
        return {'operation': _COMPUTE_STATUS['operation'], 'waiting_requests': _COMPUTE_STATUS['waiting'],
                'elapsed_seconds': round(time.monotonic() - _COMPUTE_STATUS['started'], 1)
                if _COMPUTE_STATUS['started'] is not None else None}


def serialized_compute(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with _STATUS_LOCK:
            _COMPUTE_STATUS['waiting'] += 1
        with COMPUTE_LOCK:
            with _STATUS_LOCK:
                parent_status = (_COMPUTE_STATUS['operation'], _COMPUTE_STATUS['started'])
                _COMPUTE_STATUS.update(operation=function.__name__, started=time.monotonic(),
                                       waiting=_COMPUTE_STATUS['waiting'] - 1)
            try:
                return function(*args, **kwargs)
            finally:
                with _STATUS_LOCK:
                    _COMPUTE_STATUS.update(operation=parent_status[0], started=parent_status[1])
    return wrapped


def compact(value):
    """Keep numerical evidence in model context; retain images in the UI response."""
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if not k.endswith('_png')}
    if isinstance(value, list):
        return [compact(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class Scene(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    date: str | None = None
    hour: int = Field(default=15, ge=0, le=23)
    edits: list[dict] = Field(default_factory=list, max_length=100)
    selected_point: dict | None = None
    map_center: list[float] | None = None
    viewport_bounds: list[float] | None = Field(default=None, min_length=4, max_length=4)
    baseline: dict | None = None
    result: dict | None = None
    map_view: Literal['real', 'heat'] = 'real'
    heat_mode: Literal['heat', 'change'] = 'heat'
    show_shade: bool = True
    show_3d: bool = False
    camera: dict | None = None
    selected_tool: str = 'pan'
    tool_options: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_date(self):
        if self.date:
            date.fromisoformat(self.date)
        return self


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')


class NoArgs(ToolArgs):
    pass


class ConnectArgs(ToolArgs):
    connection_code: str = Field(min_length=6, max_length=6)


class SessionArgs(ToolArgs):
    session_id: str = Field(min_length=1, max_length=100)


class FeatureArgs(ToolArgs):
    kind: Literal['buildings', 'trees']
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4,
                                   description='Optional west,south,east,north in lon/lat')


class PointArgs(SessionArgs):
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)


class MarkerArgs(PointArgs):
    view: Literal['real', 'heat'] = 'heat'


class AreaStateArgs(SessionArgs):
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    cell_size_m: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=32, ge=1, le=64)
    hours: list[int] | None = Field(default=None, min_length=1, max_length=3)


class AddTreeArgs(PointArgs):
    size: Literal['small', 'medium', 'large'] = 'medium'


class TreeTargetArgs(SessionArgs):
    tree_id: str = Field(description='tree:<index> from get_features or draft:<index> from get_scene')


class GeometryArgs(SessionArgs):
    geometry: dict


class SurfaceArgs(GeometryArgs):
    surface: Literal['paving', 'asphalt', 'grass', 'soil', 'water', 'cool_asphalt']


class AddBuildingArgs(GeometryArgs):
    height: float = Field(ge=1, le=250, allow_inf_nan=False)


class BuildingTargetArgs(SessionArgs):
    building_id: str = Field(description='building:<idx> from get_features or draft:<index> from get_scene')


class BuildingHeightArgs(BuildingTargetArgs):
    height: float = Field(ge=0, le=250, allow_inf_nan=False)


class ViewArgs(SessionArgs):
    map_view: Literal['real', 'heat'] | None = None
    heat_mode: Literal['heat', 'change'] | None = None
    show_shade: bool | None = None
    show_3d: bool | None = None


class CameraArgs(PointArgs):
    zoom: float | None = Field(default=None, ge=0, le=22, allow_inf_nan=False)
    pitch: float | None = Field(default=None, ge=0, le=85, allow_inf_nan=False)
    bearing: float | None = Field(default=None, ge=-360, le=360, allow_inf_nan=False)


class SelectToolArgs(SessionArgs):
    tool: Literal['pan', 'probe', 'tree', 'remove_trees', 'surface', 'building', 'demolish']
    tree_size: Literal['small', 'medium', 'large'] | None = None
    surface_type: Literal['paving', 'asphalt', 'grass', 'soil', 'water', 'cool_asphalt'] | None = None
    building_height: float | None = Field(default=None, ge=0, le=250, allow_inf_nan=False)


class EditsArgs(SessionArgs):
    edits: list[dict] = Field(max_length=100,
                            description='GeoJSON edits: add_tree(Point,size), remove_trees(Polygon), surface(Polygon,surface), building(Polygon,height)')


class SimulateArgs(SessionArgs):
    hours: list[int] | None = Field(default=None, min_length=1, max_length=10,
                                    description='Hours 9–18; defaults to selected hour')


class CreateSceneArgs(ToolArgs):
    date: str | None = None
    hour: int = Field(default=15, ge=9, le=18)


class SetTimeArgs(SessionArgs):
    date: str | None = None
    hour: int = Field(default=15, ge=9, le=18)


TOOLS = {
    'connect_to_app': (ConnectArgs, 'Pair to the exact live app session using its temporary six-character code.'),
    'get_status': (NoArgs, 'Check the compute backend, active operation, elapsed time and queued calculations.'),
    'list_sessions': (NoArgs, 'List active browser sessions; choose the user’s session before changing its scene.'),
    'get_scene': (SessionArgs, 'Read the current date, hour, selected point, draft edits and numerical results. Re-read before editing.'),
    'create_scene': (CreateSceneArgs, 'Create an independent scenario when no browser is open. Returns a session ID; does not affect other scenes.'),
    'set_time': (SetTimeArgs, 'Set the scene’s local date and hour; clears stale heat results. Omit date for live weather.'),
    'get_area': (NoArgs, 'Read study-area bounds, source data, tree sizes, surface types and modelling limitations.'),
    'get_features': (FeatureArgs, 'Inspect actual building footprints or tree locations; bounded output. Do not invent coordinates.'),
    'get_weather': (SessionArgs, 'Read weather for the scene’s date, including whether weather is a fallback.'),
    'get_baseline': (SessionArgs, 'Calculate/read the baseline and return hourly heat metrics. Updates the connected map.'),
    'inspect_point': (PointArgs, 'Read modelled UTCI, surface and shade at a lon/lat point for the selected hour and scenario.'),
    'get_area_state': (AreaStateArgs, 'Read paginated spatial cells: exact heat ranges/coordinates, shade, surfaces, buildings and canopy. Agent reasons across cells. Use next_offset and bbox for detailed inspection.'),
    'place_temperature_marker': (MarkerArgs, 'Place the temperature popup at a coordinate in the connected map, with before/after UTCI and shade.'),
    'stage_edits': (EditsArgs, 'Replace the browser’s draft with validated edits; include existing edits if retaining them. Changes are reversible simulation drafts.'),
    'run_simulation': (SimulateArgs, 'Simulate the current draft before/after; return measured changes and display result images in the browser.'),
    'add_tree': (AddTreeArgs, 'Append a tree at lon/lat to the existing draft. Does not discard other edits.'),
    'remove_tree': (TreeTargetArgs, 'Remove one existing tree by stable feature ID, using its crown footprint.'),
    'remove_trees': (GeometryArgs, 'Remove canopy within a GeoJSON polygon.'),
    'add_building': (AddBuildingArgs, 'Add a hypothetical building footprint with height in metres.'),
    'set_building_height': (BuildingHeightArgs, 'Change an existing/draft building height by feature ID.'),
    'remove_building': (BuildingTargetArgs, 'Demolish an existing/draft building by ID; staged and reversible.'),
    'change_surface': (SurfaceArgs, 'Change ground surface in a polygon; roofs are protected.'),
    'undo_edit': (SessionArgs, 'Undo the last draft edit; clears stale simulation results.'),
    'clear_edits': (SessionArgs, 'Clear all draft edits and stale simulation results.'),
    'set_map_view': (ViewArgs, 'Set real/heat view, temperature/change mode, shade and 3D-model visibility.'),
    'set_camera': (CameraArgs, 'Pan, zoom, rotate or tilt the connected maps.'),
    'select_tool': (SelectToolArgs, 'Select a UI tool and its tree/surface/building options.'),
}

GUIDANCE = (
    'Pair with the exact app session using connect_to_app and its temporary code. Never guess which browser to control. '
    'CoolBlocks models outdoor pedestrian heat over the University of Sydney. '
    'Coordinates are [longitude, latitude]; dates and hours are Australia/Sydney local time. '
    'UTCI is a feels-like index, not air temperature. Negative UTCI change means cooling. '
    'Use measured tool outputs; do not invent results or describe drafts as simulated. '
    'Report weather_source; fallback hot day is synthetic weather. Tree heights are assumed, '
    'OSM building heights may be estimated, and absolute temperatures are model estimates. '
    'Reflective pavement can increase pedestrian heat; simulate rather than assume cooling. '
    'Use get_area_state to reason about the spatial distribution; follow pagination, then query a smaller bbox '
    'at finer resolution if needed. Cell maxima are exact pixels, not inferred from images. '
    'Never plant on roofs. Make only changes requested by the user. If their location is ambiguous, '
    'ask them to select a point. All edits are hypothetical; source rasters remain unchanged.'
)


class AgentTools:
    def __init__(self, api, edit_model, sim_model):
        self.api, self.edit_model, self.sim_model = api, edit_model, sim_model
        self.sessions = {}
        self.pairings = {}
        self.lock = threading.RLock()

    def pairing_code(self, session_id):
        with self.lock:
            self.session(session_id)
            now = time.monotonic()
            self.pairings = {code: value for code, value in self.pairings.items() if value['expires'] > now}
            for code, value in self.pairings.items():
                if value['session_id'] == session_id:
                    return {'connection_code': code, 'expires_in': int(value['expires'] - now), 'session_id': session_id}
            while True:
                code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
                if code not in self.pairings:
                    break
            self.pairings[code] = {'session_id': session_id, 'expires': now + 600}
            return {'connection_code': code, 'expires_in': 600, 'session_id': session_id}

    def connect(self, code):
        with self.lock:
            value = self.pairings.get(code.strip().upper())
            if not value or value['expires'] <= time.monotonic():
                raise ValueError('Pairing code is invalid or expired; request a new code in the app')
            session = self.session(value['session_id'])
            if not session.get('ws_connected'):
                raise ValueError('This app session is offline; reconnect its browser first')
            session['agent_connected'] = True
            return {'session_id': value['session_id'], 'connected': True}

    def publish(self, session_id, scene, browser=True):
        with self.lock:
            now = time.monotonic()
            for key in list(self.sessions):
                if now - self.sessions[key]['seen'] > 3600:
                    del self.sessions[key]
            if session_id not in self.sessions:
                if len(self.sessions) >= 32:
                    raise HTTPException(429, 'Too many active sessions')
                self.sessions[session_id] = {'scene': compact(scene.model_dump()), 'actions': [], 'seen': now, 'browser': browser}
            else:
                session = self.sessions[session_id]
                # Publishing means a human changed the scene. It supersedes queued agent actions.
                replacement = compact(scene.model_dump())
                replacement['revision'] = max(scene.revision, session['scene']['revision'] + 1)
                session['scene'] = replacement
                session['actions'] = []
                session['seen'] = now
            return {'session_id': session_id, 'revision': self.sessions[session_id]['scene']['revision']}

    def session(self, session_id):
        session = self.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, 'Browser session not found; open CoolBlocks first')
        return session

    def events(self, session_id):
        with self.lock:
            session = self.session(session_id)
            session['seen'] = time.monotonic()
            return copy.deepcopy(session['actions'])

    def acknowledge(self, session_id, event_id):
        with self.lock:
            session = self.session(session_id)
            session['actions'] = [a for a in session['actions'] if a['id'] != event_id]

    def _queue(self, session_id, expected_revision, payload):
        with self.lock:
            session = self.session(session_id)
            if session['scene']['revision'] != expected_revision:
                raise HTTPException(409, 'Scene changed during the tool call; read the scene and retry')
            if len(session['actions']) >= 20:
                raise HTTPException(409, 'Browser has not applied pending changes; wait for it to reconnect')
            if session['browser']:
                session['actions'].append({'id': uuid4().hex, 'revision': expected_revision, **payload})
            session['scene']['revision'] += 1
            if 'edits' in payload:
                session['scene']['edits'] = payload['edits']
                session['scene']['result'] = None
            for key in ('date', 'hour', 'map_view', 'heat_mode', 'show_shade', 'show_3d', 'camera', 'selected_tool', 'tool_options'):
                if key in payload:
                    session['scene'][key] = payload[key]
            if 'selected_point' in payload:
                session['scene']['selected_point'] = payload['selected_point']
            for key in ('baseline', 'result'):
                if key in payload:
                    session['scene'][key] = compact(payload[key])

    def validate_edits(self, edits):
        validated = [self.edit_model.model_validate(e) for e in edits]
        bounds = self.api['area']()['bounds']
        (west, south), (east, north) = bounds
        for edit in validated:
            try:
                geometry = shape(edit.geometry)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError('Invalid GeoJSON geometry') from error
            required = 'Point' if edit.type == 'add_tree' else 'Polygon'
            allowed = ('Point',) if edit.type == 'add_tree' else ('Polygon', 'MultiPolygon')
            if geometry.geom_type not in allowed or geometry.is_empty or not geometry.is_valid:
                raise ValueError(f'{edit.type} requires a valid {required}')
            if edit.type == 'surface' and edit.surface is None:
                raise ValueError('Surface edit needs a surface')
            if edit.type == 'building' and edit.height is None:
                raise ValueError('Building edit needs a height')
            x0, y0, x1, y1 = geometry.bounds
            if not all(math.isfinite(v) for v in geometry.bounds) or not (west <= x0 <= x1 <= east and south <= y0 <= y1 <= north):
                raise ValueError('Edit lies outside the study area')
            if len(json.dumps(edit.geometry)) > 32000:
                raise ValueError('Geometry is too large')
            if edit.type == 'add_tree' and 'is_roof' in self.api and self.api['is_roof'](*edit.geometry['coordinates'][:2]):
                raise ValueError('Cannot plant a tree on a roof')
        return [e.model_dump() for e in validated]

    def features(self, kind):
        features = copy.deepcopy(self.api[kind]()['features'])
        for index, feature in enumerate(features):
            feature['id'] = f"{'building' if kind == 'buildings' else 'tree'}:{index}"
        return features

    def target_building(self, identifier, scene):
        prefix, separator, raw_index = identifier.partition(':')
        if not separator or not raw_index.isdigit():
            raise ValueError('Building ID must be building:<index> or draft:<index>')
        index = int(raw_index)
        if prefix == 'building':
            features = self.features('buildings')
            if index < len(features):
                return features[index]['geometry']
        if prefix == 'draft' and index < len(scene['edits']):
            edit = scene['edits'][index]
            if edit['type'] == 'building' and (edit.get('height') or 0) > 0:
                return edit['geometry']
        raise ValueError('Building ID not found')

    def dispatch(self, name, arguments):
        if name not in TOOLS:
            raise ValueError(f'Unknown tool: {name}')
        args = TOOLS[name][0].model_validate(arguments)
        if name == 'connect_to_app':
            return self.connect(args.connection_code)
        if name == 'get_status':
            return {**compute_status(), 'backend': self.api.get('backend', lambda: 'unknown')(),
                    'source': self.api['area']().get('source', 'unknown')}
        if name == 'create_scene':
            session_id = uuid4().hex
            self.publish(session_id, Scene(revision=0, date=args.date, hour=args.hour), browser=False)
            return self.dispatch('get_scene', {'session_id': session_id})
        if name == 'list_sessions':
            with self.lock:
                return [{'session_id': key, 'date': value['scene']['date'], 'hour': value['scene']['hour'],
                         'revision': value['scene']['revision'], 'browser_connected': value['browser']}
                        for key, value in self.sessions.items()]
        if name == 'get_area':
            return {**self.api['area'](), 'interpretation': GUIDANCE}
        if name == 'get_features':
            features = self.features(args.kind)
            if args.bbox:
                w, s, e, n = args.bbox
                if not all(math.isfinite(v) for v in args.bbox) or w >= e or s >= n:
                    raise ValueError('bbox must be west,south,east,north')
                from shapely.geometry import box
                features = [f for f in features if shape(f['geometry']).intersects(box(w, s, e, n))]
            page = features[args.offset:args.offset + args.limit]
            return {'type': 'FeatureCollection', 'total_matches': len(features), 'features': page,
                    'offset': args.offset, 'next_offset': args.offset + len(page) if args.offset + len(page) < len(features) else None,
                    'truncated': args.offset + len(page) < len(features)}
        with self.lock:
            scene = copy.deepcopy(self.session(args.session_id)['scene'])
        if name == 'get_scene':
            with self.lock:
                pending = len(self.session(args.session_id)['actions'])
            return {**scene, 'session_id': args.session_id, 'pending_browser_actions': pending, 'interpretation': GUIDANCE,
                    'draft_buildings': [{'building_id': f'draft:{index}', **edit} for index, edit in enumerate(scene['edits']) if edit['type'] == 'building'],
                    'draft_trees': [{'tree_id': f'draft:{index}', **edit} for index, edit in enumerate(scene['edits']) if edit['type'] == 'add_tree']}
        if name == 'set_time':
            if args.date:
                date.fromisoformat(args.date)
            self._queue(args.session_id, scene['revision'], {'date': args.date, 'hour': args.hour, 'baseline': None, 'result': None})
            return {'date': args.date, 'hour': args.hour, 'status': 'time changed; compute a baseline for this date'}
        if name == 'get_weather':
            return self.api['weather'](date=scene['date'])
        if name == 'get_area_state':
            if args.bbox and (not all(math.isfinite(v) for v in args.bbox) or args.bbox[0] >= args.bbox[2] or args.bbox[1] >= args.bbox[3]):
                raise ValueError('bbox must be west,south,east,north')
            hours = sorted(set(args.hours or [scene['hour']]))
            if any(hour not in self.api['area']()['hours'] for hour in hours):
                raise ValueError('Area-state hours must be 9–18')
            result = self.api['area_state'](date=scene['date'], hours=hours, sim_id=(scene['result'] or {}).get('sim_id'),
                                           bbox=args.bbox, cell_size_m=args.cell_size_m, offset=args.offset, limit=args.limit)
            return {**result, 'scene_revision': scene['revision'], 'draft_edits': scene['edits'],
                    'draft_note': 'Unsimulated drafts are listed separately and are not applied to spatial data.',
                    'interpretation': GUIDANCE}
        if name in ('set_map_view', 'set_camera', 'select_tool'):
            if name == 'set_map_view':
                payload = args.model_dump(exclude={'session_id'}, exclude_none=True)
                if not payload:
                    raise ValueError('Provide at least one view setting')
            elif name == 'set_camera':
                payload = {'camera': {'center': [args.lon, args.lat], **args.model_dump(exclude={'session_id', 'lon', 'lat'}, exclude_none=True)}}
            else:
                options = {**scene['tool_options'], **args.model_dump(exclude={'session_id', 'tool'}, exclude_none=True)}
                payload = {'selected_tool': args.tool, 'tool_options': options}
            self._queue(args.session_id, scene['revision'], payload)
            return {'status': 'queued for browser', **payload}
        if name in ('add_tree', 'remove_tree', 'remove_trees', 'add_building', 'set_building_height', 'remove_building', 'change_surface', 'undo_edit', 'clear_edits'):
            edits = list(scene['edits'])
            if name == 'add_tree':
                edit = {'type': 'add_tree', 'size': args.size, 'geometry': {'type': 'Point', 'coordinates': [args.lon, args.lat]}}
            elif name == 'remove_tree':
                feature = next((f for f in self.features('trees') if f['id'] == args.tree_id), None)
                if feature is None and args.tree_id.startswith('draft:') and args.tree_id[6:].isdigit():
                    index = int(args.tree_id[6:])
                    if index < len(edits) and edits[index]['type'] == 'add_tree':
                        tree = edits[index]
                        feature = {'geometry': tree['geometry'], 'properties': {'radius': {'small': 3, 'medium': 4, 'large': 6}[tree['size']]}}
                if feature is None:
                    raise ValueError('Tree ID not found')
                from pyproj import Transformer
                from shapely.ops import transform
                forward = Transformer.from_crs('EPSG:4326', 'EPSG:7856', always_xy=True).transform
                inverse = Transformer.from_crs('EPSG:7856', 'EPSG:4326', always_xy=True).transform
                crown = transform(forward, shape(feature['geometry'])).buffer(feature['properties']['radius'])
                from shapely.geometry import mapping
                from shapely.geometry import box
                (w, s), (e, n) = self.api['area']()['bounds']
                edit = {'type': 'remove_trees', 'geometry': mapping(transform(inverse, crown).intersection(box(w, s, e, n)))}
            elif name == 'remove_trees':
                edit = {'type': 'remove_trees', 'geometry': args.geometry}
            elif name == 'add_building':
                edit = {'type': 'building', 'height': args.height, 'geometry': args.geometry}
            elif name in ('set_building_height', 'remove_building'):
                edit = {'type': 'building', 'height': args.height if name == 'set_building_height' else 0,
                        'geometry': self.target_building(args.building_id, scene)}
            elif name == 'change_surface':
                edit = {'type': 'surface', 'surface': args.surface, 'geometry': args.geometry}
            elif name == 'undo_edit':
                if not edits:
                    raise ValueError('No edit to undo')
                edits.pop()
            else:
                edits = []
            if name not in ('undo_edit', 'clear_edits'):
                edits.append(edit)
            if len(edits) > 100:
                raise ValueError('At most 100 draft edits are supported')
            edits = self.validate_edits(edits)
            self._queue(args.session_id, scene['revision'], {'edits': edits})
            return {'status': 'draft; not yet simulated', 'edit_count': len(edits), 'edits': edits}
        if name == 'get_baseline':
            result = self.api['baseline'](date=scene['date'])
            self._queue(args.session_id, scene['revision'], {'baseline': result})
            return compact(result)
        if name in ('inspect_point', 'place_temperature_marker'):
            self.api['baseline'](date=scene['date'])
            result = self.api['point'](lon=args.lon, lat=args.lat, hour=scene['hour'], date=scene['date'],
                                       sim_id=(scene['result'] or {}).get('sim_id'))
            if name == 'place_temperature_marker':
                self._queue(args.session_id, scene['revision'],
                            {'selected_point': {'lon': args.lon, 'lat': args.lat}, 'marker_view': args.view})
            return {**result, 'lon': args.lon, 'lat': args.lat}
        if name == 'stage_edits':
            edits = self.validate_edits(args.edits)
            self._queue(args.session_id, scene['revision'], {'edits': edits})
            return {'staged_edits': edits, 'status': 'draft; not yet simulated'}
        if name == 'run_simulation':
            edits = self.validate_edits(scene['edits'])
            if not edits:
                raise ValueError('Stage at least one edit before simulating')
            hours = args.hours or [scene['hour']]
            if any(h not in self.api['area']()['hours'] for h in hours):
                raise ValueError('Simulation hours must be in the study area hours (9–18)')
            result = self.api['simulate'](self.sim_model(edits=edits, date=scene['date'], hours=hours, images=True))
            self._queue(args.session_id, scene['revision'], {'result': result})
            return compact(result)
        raise ValueError(name)
