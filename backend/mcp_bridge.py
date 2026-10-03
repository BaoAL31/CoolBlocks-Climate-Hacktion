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
    async def get_features(kind: Literal['buildings', 'trees'], limit: int = 50, bbox: list[float] | None = None) -> dict:
        """Actual trees/building GeoJSON; optional west,south,east,north filter; max 200 features."""
        return await invoke('get_features', {'kind': kind, 'limit': limit, 'bbox': bbox})

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
    async def find_hotspot(session_id: str) -> dict:
        """Find the hottest finite ground-level UTCI cell for the current date/hour and scenario, excluding roofs."""
        return await invoke('find_hotspot', {'session_id': session_id})

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
