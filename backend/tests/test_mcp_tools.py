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
    api['area_state'] = lambda **kwargs: {'cells': [], 'date': kwargs['date'], 'hours_requested': kwargs['hours'], 'cell_size_m': kwargs['cell_size_m']}
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


def test_area_state_and_marker_queue(service):
    state = service.dispatch('get_area_state', {'session_id': 'browser', 'cell_size_m': 10})
    assert state['cell_size_m'] == 10
    result = service.dispatch('place_temperature_marker',
                              {'session_id': 'browser', 'lon': 151.187, 'lat': -33.888})
    assert result['before'] == 30.
    event = service.events('browser')[0]
    assert event['selected_point'] == {'lon': 151.187, 'lat': -33.888}
    assert event['marker_view'] == 'heat'
    assert service.dispatch('get_scene', {'session_id': 'browser'})['selected_point'] == event['selected_point']


def test_named_edits_append_target_ids_and_undo(service):
    polygon = {'type': 'Polygon', 'coordinates': [[[151.185, -33.889], [151.188, -33.889], [151.188, -33.887], [151.185, -33.889]]]}
    service.api['buildings'] = lambda: {'features': [{'geometry': polygon, 'properties': {'height': 20}}]}
    key = service.dispatch('create_scene', {})['session_id']
    tree = service.dispatch('add_tree', {'session_id': key, 'lon': 151.187, 'lat': -33.888, 'size': 'large'})
    assert tree['edit_count'] == 1 and tree['edits'][0]['size'] == 'large'
    removed = service.dispatch('remove_building', {'session_id': key, 'building_id': 'building:0'})
    assert removed['edit_count'] == 2 and removed['edits'][1]['height'] == 0
    undone = service.dispatch('undo_edit', {'session_id': key})
    assert undone['edits'] == tree['edits']
    built = service.dispatch('add_building', {'session_id': key, 'geometry': polygon, 'height': 12})
    changed = service.dispatch('set_building_height', {'session_id': key, 'building_id': 'draft:1', 'height': 8})
    assert changed['edit_count'] == 3 and changed['edits'][-1]['height'] == 8
    clear = service.dispatch('clear_edits', {'session_id': key})
    assert clear['edits'] == []
    with pytest.raises(ValueError):
        service.dispatch('remove_building', {'session_id': key, 'building_id': 'building:100'})


def test_named_tree_removal_surface_and_view_controls(service):
    polygon = {'type': 'Polygon', 'coordinates': [[[151.185, -33.889], [151.188, -33.889], [151.188, -33.887], [151.185, -33.889]]]}
    service.api['trees'] = lambda: {'features': [{'geometry': TREE['geometry'], 'properties': {'radius': 4}}]}
    key = service.dispatch('create_scene', {})['session_id']
    removed = service.dispatch('remove_tree', {'session_id': key, 'tree_id': 'tree:0'})
    assert removed['edits'][0]['type'] == 'remove_trees'
    service.dispatch('add_tree', {'session_id': key, 'lon': 151.187, 'lat': -33.888})
    service.dispatch('remove_tree', {'session_id': key, 'tree_id': 'draft:1'})
    service.dispatch('change_surface', {'session_id': key, 'geometry': polygon, 'surface': 'grass'})
    service.dispatch('set_map_view', {'session_id': key, 'map_view': 'heat', 'heat_mode': 'change', 'show_3d': False})
    service.dispatch('set_camera', {'session_id': key, 'lon': 151.187, 'lat': -33.888, 'zoom': 18})
    service.dispatch('select_tool', {'session_id': key, 'tool': 'building', 'building_height': 30})
    scene = service.dispatch('get_scene', {'session_id': key})
    assert scene['map_view'] == 'heat' and scene['heat_mode'] == 'change'
    assert scene['camera']['zoom'] == 18 and scene['tool_options']['building_height'] == 30
    assert len(scene['edits']) == 4


def test_feature_pagination_retains_stable_ids(service):
    service.api['trees'] = lambda: {'features': [{'geometry': TREE['geometry']} for _ in range(3)]}
    page = service.dispatch('get_features', {'kind': 'trees', 'offset': 1, 'limit': 1})
    assert page['features'][0]['id'] == 'tree:1' and page['next_offset'] == 2


def test_grid_state_paging_and_evidence():
    import numpy as np
    from affine import Affine
    from area_state import grid_state
    land = np.array([[2, 1, 5], [1, 5, 5]])
    temperatures = np.array([[100., 40., np.nan], [45., 35., 30.]])
    values = {15: {'utci': temperatures, 'shadow': np.array([[0, 1, 1], [1, 0, 0]])}}
    first = grid_state(np.ones((2, 3)), np.full((2, 3), 20), np.zeros((2, 3)), land,
                       values, None, Affine.identity(), lambda x, y: (x, y), cell_size_m=2, limit=1)
    cell = first['cells'][0]
    assert first['total_cells'] == 2 and first['next_offset'] == 1
    assert cell['surfaces_m2'] == {'asphalt': 2, 'roof': 1, 'grass': 1}
    assert cell['hours'][0]['after_utci_c']['max'] == 45.
    assert cell['hours'][0]['maximum_ground_point']['lon'] == .5
    second = grid_state(np.ones((2, 3)), np.full((2, 3), 20), np.zeros((2, 3)), land,
                        values, None, Affine.identity(), lambda x, y: (x, y), cell_size_m=2, offset=1, limit=1)
    assert second['next_offset'] is None and second['cells'][0]['hours'][0]['after_utci_c']['max'] == 30.


def test_hotspot_excludes_roofs_nan_and_uses_pixel_centers():
    import numpy as np
    from affine import Affine
    from heat_analysis import hottest_ground_point
    values = np.array([[100., np.nan], [45., 40.]])
    surfaces = np.array([[2, 1], [1, 5]])
    result = hottest_ground_point(values, surfaces, Affine.translation(10, 20), lambda x, y: (x, y))
    assert result == {'lon': 10.5, 'lat': 21.5, 'utci': 45., 'row': 1, 'col': 0}
    with pytest.raises(ValueError, match='No valid ground'):
        hottest_ground_point(values, np.full((2, 2), 2), Affine.identity(), lambda x, y: (x, y))


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
        assert len(tools) == 26
        assert {t['name'] for t in tools} >= {'get_area_state', 'add_tree', 'remove_building', 'undo_edit'}
        assert 'find_hotspot' not in {t['name'] for t in tools}
        scene = rpc('tools/call', {'name': 'get_scene', 'arguments': {'session_id': 'browser'}}, 3)
        assert scene.get('isError') is not True
        assert '151.187' in json.dumps(scene)
        staged = rpc('tools/call', {'name': 'stage_edits', 'arguments': {'session_id': 'browser', 'edits': [TREE]}}, 4)
        assert staged.get('isError') is not True
        assert client.get('/api/agent/sessions/browser/events').json()['events'][0]['edits']
        guide = rpc('resources/read', {'uri': 'coolblocks://model-guide'}, 5)
        assert 'Negative UTCI change means cooling' in guide['contents'][0]['text']
