"""Validated tool surface and browser scene context for external MCP agents."""
from __future__ import annotations

import copy
from datetime import date
import functools
import json
import math
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
                _COMPUTE_STATUS.update(operation=function.__name__, started=time.monotonic(),
                                       waiting=_COMPUTE_STATUS['waiting'] - 1)
            try:
                return function(*args, **kwargs)
            finally:
                with _STATUS_LOCK:
                    _COMPUTE_STATUS.update(operation=None, started=None)
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
    baseline: dict | None = None
    result: dict | None = None

    @model_validator(mode='after')
    def valid_date(self):
        if self.date:
            date.fromisoformat(self.date)
        return self


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')


class NoArgs(ToolArgs):
    pass


class SessionArgs(ToolArgs):
    session_id: str = Field(min_length=1, max_length=100)


class FeatureArgs(ToolArgs):
    kind: Literal['buildings', 'trees']
    limit: int = Field(default=50, ge=1, le=200)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4,
                                   description='Optional west,south,east,north in lon/lat')


class PointArgs(SessionArgs):
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)


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
    'stage_edits': (EditsArgs, 'Replace the browser’s draft with validated edits; include existing edits if retaining them. Changes are reversible simulation drafts.'),
    'run_simulation': (SimulateArgs, 'Simulate the current draft before/after; return measured changes and display result images in the browser.'),
}

GUIDANCE = (
    'CoolBlocks models outdoor pedestrian heat over the University of Sydney. '
    'Coordinates are [longitude, latitude]; dates and hours are Australia/Sydney local time. '
    'UTCI is a feels-like index, not air temperature. Negative UTCI change means cooling. '
    'Use measured tool outputs; do not invent results or describe drafts as simulated. '
    'Report weather_source; fallback hot day is synthetic weather. Tree heights are assumed, '
    'OSM building heights may be estimated, and absolute temperatures are model estimates. '
    'Reflective pavement can increase pedestrian heat; simulate rather than assume cooling. '
    'Never plant on roofs. Make only changes requested by the user. If their location is ambiguous, '
    'ask them to select a point. All edits are hypothetical; source rasters remain unchanged.'
)


class AgentTools:
    def __init__(self, api, edit_model, sim_model):
        self.api, self.edit_model, self.sim_model = api, edit_model, sim_model
        self.sessions = {}
        self.lock = threading.RLock()

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
            for key in ('date', 'hour'):
                if key in payload:
                    session['scene'][key] = payload[key]
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
            if geometry.geom_type != required or geometry.is_empty or not geometry.is_valid:
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

    def dispatch(self, name, arguments):
        if name not in TOOLS:
            raise ValueError(f'Unknown tool: {name}')
        args = TOOLS[name][0].model_validate(arguments)
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
            features = self.api[args.kind]() ['features']
            if args.bbox:
                w, s, e, n = args.bbox
                if not all(math.isfinite(v) for v in args.bbox) or w >= e or s >= n:
                    raise ValueError('bbox must be west,south,east,north')
                from shapely.geometry import box
                features = [f for f in features if shape(f['geometry']).intersects(box(w, s, e, n))]
            return {'type': 'FeatureCollection', 'total_matches': len(features), 'features': features[:args.limit],
                    'truncated': len(features) > args.limit}
        with self.lock:
            scene = copy.deepcopy(self.session(args.session_id)['scene'])
        if name == 'get_scene':
            return {**scene, 'session_id': args.session_id, 'interpretation': GUIDANCE}
        if name == 'set_time':
            if args.date:
                date.fromisoformat(args.date)
            self._queue(args.session_id, scene['revision'], {'date': args.date, 'hour': args.hour, 'baseline': None, 'result': None})
            return {'date': args.date, 'hour': args.hour, 'status': 'time changed; compute a baseline for this date'}
        if name == 'get_weather':
            return self.api['weather'](date=scene['date'])
        if name == 'get_baseline':
            result = self.api['baseline'](date=scene['date'])
            self._queue(args.session_id, scene['revision'], {'baseline': result})
            return compact(result)
        if name == 'inspect_point':
            self.api['baseline'](date=scene['date'])
            return self.api['point'](lon=args.lon, lat=args.lat, hour=scene['hour'], date=scene['date'],
                                     sim_id=(scene['result'] or {}).get('sim_id'))
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
