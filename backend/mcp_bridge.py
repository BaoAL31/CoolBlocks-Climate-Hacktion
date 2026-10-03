"""MCP tools for CoolBlocks, mounted HTTP or stdio proxy to the running app."""
from __future__ import annotations

import os
from typing import Literal

import anyio
import requests
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from agent_tools import GUIDANCE


def create_mcp(dispatch):
    mcp = FastMCP('CoolBlocks', instructions=GUIDANCE, json_response=True,
                  stateless_http=True, streamable_http_path='/')
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    async def invoke(name, arguments):
        return await anyio.to_thread.run_sync(dispatch, name, arguments)

    @mcp.tool(annotations=read)
    async def get_status() -> dict:
        """Check CPU/GPU backend, active calculation, elapsed seconds and waiting requests."""
        return await invoke('get_status', {})

    @mcp.tool(annotations=read)
    async def list_sessions() -> dict:
        """List open CoolBlocks browser scenes; choose the user's session ID."""
        return {'sessions': await invoke('list_sessions', {})}

    @mcp.tool(annotations=read)
    async def get_scene(session_id: str) -> dict:
        """Understand the user's date, hour, selected point, draft edits and simulation results."""
        return await invoke('get_scene', {'session_id': session_id})

    @mcp.tool(annotations=write)
    async def create_scene(date: str | None = None, hour: int = 15) -> dict:
        """Create an independent scenario/session when no browser is open. Does not change other scenes."""
        return await invoke('create_scene', {'date': date, 'hour': hour})

    @mcp.tool(annotations=write)
    async def set_time(session_id: str, date: str | None = None, hour: int = 15) -> dict:
        """Set local date/hour (9–18) in the scene; clears stale results. Null date selects live weather."""
        return await invoke('set_time', {'session_id': session_id, 'date': date, 'hour': hour})

    @mcp.tool(annotations=read)
    async def get_area() -> dict:
        """Study-area bounds, data provenance, edit options and model interpretation."""
        return await invoke('get_area', {})

    @mcp.tool(annotations=read)
    async def get_features(kind: Literal['buildings', 'trees'], limit: int = 50, bbox: list[float] | None = None, offset: int = 0) -> dict:
        """Actual trees/building GeoJSON; optional west,south,east,north filter; max 200 features."""
        return await invoke('get_features', {'kind': kind, 'limit': limit, 'bbox': bbox, 'offset': offset})

    @mcp.tool(annotations=read)
    async def get_weather(session_id: str) -> dict:
        """Get weather for the selected date and identify fallback weather."""
        return await invoke('get_weather', {'session_id': session_id})

    @mcp.tool(annotations=write)
    async def get_baseline(session_id: str) -> dict:
        """Calculate/read baseline hourly heat metrics and display it in the browser."""
        return await invoke('get_baseline', {'session_id': session_id})

    @mcp.tool(annotations=read)
    async def inspect_point(session_id: str, lon: float, lat: float) -> dict:
        """Inspect pedestrian UTCI, surface and shade at a location for the scene's hour."""
        return await invoke('inspect_point', {'session_id': session_id, 'lon': lon, 'lat': lat})

    @mcp.tool(annotations=read)
    async def get_area_state(session_id: str, bbox: list[float] | None = None, cell_size_m: int = 50,
                             offset: int = 0, limit: int = 32, hours: list[int] | None = None) -> dict:
        """Read agent-readable spatial cells: heat distributions, exact pixel maxima, shade, surfaces, building heights and canopy. Follow next_offset for all cells, or refine a west,south,east,north bbox to 1m cells. Up to 3 selected local hours per call."""
        return await invoke('get_area_state', {'session_id': session_id, 'bbox': bbox, 'cell_size_m': cell_size_m,
                                               'offset': offset, 'limit': limit, 'hours': hours})

    @mcp.tool(annotations=write)
    async def place_temperature_marker(session_id: str, lon: float, lat: float, view: Literal['real', 'heat'] = 'heat') -> dict:
        """Display a temperature popup at lon/lat in the browser's real-world or heat map."""
        return await invoke('place_temperature_marker', {'session_id': session_id, 'lon': lon, 'lat': lat, 'view': view})

    @mcp.tool(annotations=write)
    async def stage_edits(session_id: str, edits: list[dict]) -> dict:
        """Replace hypothetical draft edits in the map; include retained edits. GeoJSON uses lon/lat."""
        return await invoke('stage_edits', {'session_id': session_id, 'edits': edits})

    @mcp.tool(annotations=write)
    async def run_simulation(session_id: str, hours: list[int] | None = None) -> dict:
        """Run before/after physics for the current draft, return numbers and display images."""
        return await invoke('run_simulation', {'session_id': session_id, 'hours': hours})

    @mcp.tool(annotations=write)
    async def add_tree(session_id: str, lon: float, lat: float, size: Literal['small', 'medium', 'large'] = 'medium') -> dict:
        """Append a tree at a ground coordinate without discarding existing drafts; small=6m, medium=10m, large=15m."""
        return await invoke('add_tree', {'session_id': session_id, 'lon': lon, 'lat': lat, 'size': size})

    @mcp.tool(annotations=write)
    async def remove_tree(session_id: str, tree_id: str) -> dict:
        """Remove canopy within a tree's modelled crown by tree:<index> or draft:<edit index>; may overlap nearby canopy."""
        return await invoke('remove_tree', {'session_id': session_id, 'tree_id': tree_id})

    @mcp.tool(annotations=write)
    async def remove_trees(session_id: str, geometry: dict) -> dict:
        """Append removal of all canopy within a GeoJSON Polygon/MultiPolygon in lon/lat."""
        return await invoke('remove_trees', {'session_id': session_id, 'geometry': geometry})

    @mcp.tool(annotations=write)
    async def add_building(session_id: str, geometry: dict, height: float) -> dict:
        """Append a new building footprint (GeoJSON Polygon/MultiPolygon) with height 1–250m."""
        return await invoke('add_building', {'session_id': session_id, 'geometry': geometry, 'height': height})

    @mcp.tool(annotations=write)
    async def set_building_height(session_id: str, building_id: str, height: float) -> dict:
        """Change height of building:<index> or draft:<edit index>, in metres; 0 means demolition."""
        return await invoke('set_building_height', {'session_id': session_id, 'building_id': building_id, 'height': height})

    @mcp.tool(annotations=write)
    async def remove_building(session_id: str, building_id: str) -> dict:
        """Stage demolition of an existing or drafted building by stable building:<index> or draft:<edit index>."""
        return await invoke('remove_building', {'session_id': session_id, 'building_id': building_id})

    @mcp.tool(annotations=write)
    async def change_surface(session_id: str, geometry: dict,
                              surface: Literal['paving', 'asphalt', 'grass', 'soil', 'water', 'cool_asphalt']) -> dict:
        """Append ground-surface change within a GeoJSON polygon. Roofs are protected."""
        return await invoke('change_surface', {'session_id': session_id, 'geometry': geometry, 'surface': surface})

    @mcp.tool(annotations=write)
    async def undo_edit(session_id: str) -> dict:
        """Undo the last draft edit and invalidate simulation results."""
        return await invoke('undo_edit', {'session_id': session_id})

    @mcp.tool(annotations=write)
    async def clear_edits(session_id: str) -> dict:
        """Clear all hypothetical edits and their stale results."""
        return await invoke('clear_edits', {'session_id': session_id})

    @mcp.tool(annotations=write)
    async def set_map_view(session_id: str, map_view: Literal['real', 'heat'] | None = None,
                             heat_mode: Literal['heat', 'change'] | None = None,
                             show_shade: bool | None = None, show_3d: bool | None = None) -> dict:
        """Switch real/heat map, temperature/change overlay, and shade/3D visibility; omit unchanged options."""
        return await invoke('set_map_view', {'session_id': session_id, 'map_view': map_view, 'heat_mode': heat_mode,
                                             'show_shade': show_shade, 'show_3d': show_3d})

    @mcp.tool(annotations=write)
    async def set_camera(session_id: str, lon: float, lat: float, zoom: float | None = None,
                           pitch: float | None = None, bearing: float | None = None) -> dict:
        """Pan, zoom (0–22), tilt (0–85 degrees) and rotate (bearing) the connected map."""
        return await invoke('set_camera', {'session_id': session_id, 'lon': lon, 'lat': lat,
                                           'zoom': zoom, 'pitch': pitch, 'bearing': bearing})

    @mcp.tool(annotations=write)
    async def select_tool(session_id: str, tool: Literal['pan', 'probe', 'tree', 'remove_trees', 'surface', 'building', 'demolish'],
                          tree_size: Literal['small', 'medium', 'large'] | None = None,
                          surface_type: Literal['paving', 'asphalt', 'grass', 'soil', 'water', 'cool_asphalt'] | None = None,
                          building_height: float | None = None) -> dict:
        """Select the current UI tool and its tree-size, surface-type or building-height options."""
        return await invoke('select_tool', {'session_id': session_id, 'tool': tool, 'tree_size': tree_size,
                                            'surface_type': surface_type, 'building_height': building_height})

    @mcp.resource('coolblocks://model-guide')
    def model_guide() -> str:
        return GUIDANCE

    return mcp


def remote_dispatch(name, arguments):
    base = os.environ.get('COOLBLOCKS_URL', 'http://127.0.0.1:8000').rstrip('/')
    response = requests.post(f'{base}/api/agent/tools/{name}', json=arguments, timeout=300)
    if not response.ok:
        raise ValueError(f'CoolBlocks tool failed ({response.status_code}): {response.text[:500]}')
    return response.json()


if __name__ == '__main__':
    # This process does not import SOLWEIG, so engine logs cannot corrupt the stdio protocol.
    create_mcp(remote_dispatch).run(transport='stdio')
