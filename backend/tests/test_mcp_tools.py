import json
import sys
from pathlib import Path
from typing import Literal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_routes import install_agents
from agent_tools import AgentTools, Scene, compact


class Edit(BaseModel):
    type: Literal['add_tree', 'surface', 'building', 'remove_trees']
    geometry: dict
    size: Literal['small', 'medium', 'large'] = 'medium'
    surface: str | None = None
    height: float | None = None


class Simulation(BaseModel):
    edits: list[Edit]
    date: str | None
    hours: list[int]
    images: bool


TREE = {'type': 'add_tree', 'geometry': {'type': 'Point', 'coordinates': [151.187, -33.888]}, 'size': 'medium'}


@pytest.fixture
def service():
    def simulate(request):
        assert request.images is True
        return {'sim_id': 'test', 'weather_source': 'fallback hot day', 'bounds': [[151.18, -33.90], [151.20, -33.88]],
                'hours': [{'hour': request.hours[0], 'utci_change_mean': -1.2, 'utci_png': 'image-data'}]}
    api = {'area': lambda: {'bounds': [[151.18, -33.90], [151.20, -33.88]], 'hours': list(range(9, 19))},
           'baseline': lambda **kwargs: {'weather_source': 'open-meteo', 'hours': [{'hour': 15, 'utci_ground_mean': 30., 'utci_png': 'image-data'}]},
           'simulate': simulate, 'weather': lambda **kwargs: {'source': 'open-meteo'},
           'point': lambda **kwargs: {'before': 30., 'shade_before': False},
           'trees': lambda: {'features': [{'geometry': TREE['geometry']}]},
           'buildings': lambda: {'features': []}, 'is_roof': lambda *args: False}
    service = AgentTools(api, Edit, Simulation)
    service.publish('browser', Scene(revision=0, selected_point={'lon': 151.187, 'lat': -33.888}))
    return service


def test_simulation_flow_preserves_images_for_browser_only(service):
    service.dispatch('stage_edits', {'session_id': 'browser', 'edits': [TREE]})
    result = service.dispatch('run_simulation', {'session_id': 'browser'})
    assert result['hours'][0]['utci_change_mean'] == -1.2
    assert 'utci_png' not in result['hours'][0]
    events = service.events('browser')
    assert events[0]['revision'] == 0 and events[1]['revision'] == 1
    assert events[1]['result']['hours'][0]['utci_png'] == 'image-data'
    assert service.dispatch('get_scene', {'session_id': 'browser'})['result']['sim_id'] == 'test'
    service.acknowledge('browser', events[0]['id'])
    assert len(service.events('browser')) == 1


def test_new_human_state_supersedes_pending_agent_edits(service):
    service.dispatch('stage_edits', {'session_id': 'browser', 'edits': [TREE]})
    response = service.publish('browser', Scene(revision=1, hour=16, edits=[]))
    assert response['revision'] == 2
    assert service.events('browser') == []
    assert service.dispatch('get_scene', {'session_id': 'browser'})['hour'] == 16
    with pytest.raises(HTTPException, match='409'):
        service._queue('browser', 0, {'result': {'sim_id': 'stale'}})


def test_change_during_physics_rejects_stale_result(service):
    service.dispatch('stage_edits', {'session_id': 'browser', 'edits': [TREE]})
    def simulate(request):
        service.publish('browser', Scene(revision=2, hour=17))
        return {'sim_id': 'stale', 'hours': []}
    service.api['simulate'] = simulate
    with pytest.raises(HTTPException, match='409'):
        service.dispatch('run_simulation', {'session_id': 'browser'})
    assert service.dispatch('get_scene', {'session_id': 'browser'})['result'] is None


@pytest.mark.parametrize('edit', [
    {'type': 'add_tree', 'geometry': {'type': 'Point', 'coordinates': [0, 0]}},
    {'type': 'surface', 'geometry': TREE['geometry']},
    {'type': 'add_tree', 'geometry': TREE['geometry'], 'size': 'enormous'},
    {'type': 'building', 'geometry': {'type': 'Polygon', 'coordinates': [[[151.185, -33.889], [151.188, -33.889], [151.188, -33.887], [151.185, -33.889]]] }},
])
def test_invalid_edits_do_not_change_scene(service, edit):
    with pytest.raises(ValueError):
        service.dispatch('stage_edits', {'session_id': 'browser', 'edits': [edit]})
    assert service.events('browser') == []


def test_roof_planting_rejected(service):
    service.api['is_roof'] = lambda *args: True
    with pytest.raises(ValueError, match='roof'):
        service.dispatch('stage_edits', {'session_id': 'browser', 'edits': [TREE]})


def test_independent_scene_and_clear_edits(service):
    scene = service.dispatch('create_scene', {'date': '2026-01-15', 'hour': 12})
    key = scene['session_id']
    for _ in range(25):
        service.dispatch('stage_edits', {'session_id': key, 'edits': [TREE]})
    service.dispatch('stage_edits', {'session_id': key, 'edits': []})
    assert service.dispatch('get_scene', {'session_id': key})['edits'] == []
    assert service.events(key) == []
    assert service.dispatch('get_scene', {'session_id': 'browser'})['hour'] == 15
    service.dispatch('set_time', {'session_id': key, 'hour': 18, 'date': None})
    assert service.dispatch('get_scene', {'session_id': key})['date'] is None


def test_invalid_hours_and_missing_session(service):
    service.dispatch('stage_edits', {'session_id': 'browser', 'edits': [TREE]})
    with pytest.raises(ValueError):
        service.dispatch('run_simulation', {'session_id': 'browser', 'hours': [25]})
    with pytest.raises(HTTPException, match='404'):
        service.dispatch('get_scene', {'session_id': 'missing'})
    assert compact({'v': float('nan'), 'image_png': 'huge'}) == {'v': None}


def test_mcp_protocol_discovers_and_calls_tools(service):
    app = FastAPI()
    install_agents(app, service)
    headers = {'Accept': 'application/json, text/event-stream'}
    with TestClient(app, base_url='http://127.0.0.1:8000') as client:
        def rpc(method, params, number):
            response = client.post('/mcp/', headers=headers,
                                   json={'jsonrpc': '2.0', 'id': number, 'method': method, 'params': params})
            assert response.status_code == 200, response.text
            data = response.json()
            assert 'error' not in data, data
            return data['result']
        initialized = rpc('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
                                        'clientInfo': {'name': 'test', 'version': '1'}}, 1)
        assert initialized['serverInfo']['name'] == 'CoolBlocks'
        tools = rpc('tools/list', {}, 2)['tools']
        assert {t['name'] for t in tools} >= {'get_scene', 'stage_edits', 'run_simulation', 'create_scene'}
        scene = rpc('tools/call', {'name': 'get_scene', 'arguments': {'session_id': 'browser'}}, 3)
        assert scene.get('isError') is not True
        assert '151.187' in json.dumps(scene)
        staged = rpc('tools/call', {'name': 'stage_edits', 'arguments': {'session_id': 'browser', 'edits': [TREE]}}, 4)
        assert staged.get('isError') is not True
        assert client.get('/api/agent/sessions/browser/events').json()['events'][0]['edits']
        guide = rpc('resources/read', {'uri': 'coolblocks://model-guide'}, 5)
        assert 'Negative UTCI change means cooling' in guide['contents'][0]['text']
