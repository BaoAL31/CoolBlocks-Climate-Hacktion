"""Optional live physics/protocol check: run after starting CoolBlocks on localhost."""
import asyncio
import json
import math
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import numpy as np
from pyproj import Transformer
import rasterio


async def main():
    async with httpx.AsyncClient(timeout=300) as http:
        async with streamable_http_client('http://127.0.0.1:8000/mcp/', http_client=http) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                async def tool(name, **arguments):
                    result = await client.call_tool(name, arguments)
                    assert not result.isError, result
                    if result.structuredContent:
                        return result.structuredContent
                    return json.loads(result.content[0].text)
                listed = await client.list_tools()
                assert len(listed.tools) == 12
                status = await tool('get_status')
                area = await tool('get_area')
                assert area['source'] == 'data/area'
                scene = await tool('create_scene', date='2026-01-15', hour=15)
                key = scene['session_id']
                # Pick an actual open ground cell, rather than guessing a coordinate or planting on a roof.
                data = Path(__file__).resolve().parents[1] / 'data/area'
                with rasterio.open(data / 'landcover.tif') as raster:
                    lc, transform = raster.read(1), raster.transform
                with rasterio.open(data / 'cdsm.tif') as raster:
                    cdsm = raster.read(1)
                rows, cols = np.nonzero((lc == 5) & (cdsm == 0))
                middle = len(rows) // 2
                x, y = transform * (int(cols[middle]) + .5, int(rows[middle]) + .5)
                lon, lat = Transformer.from_crs('EPSG:7856', 'EPSG:4326', always_xy=True).transform(x, y)
                await tool('stage_edits', session_id=key, edits=[{'type': 'add_tree', 'size': 'medium',
                           'geometry': {'type': 'Point', 'coordinates': [lon, lat]}}])
                simulation = await tool('run_simulation', session_id=key, hours=[15])
                metrics = simulation['hours'][0]
                assert math.isfinite(metrics['utci_change_mean'])
                assert not any(k.endswith('_png') for k in metrics)
                await tool('get_baseline', session_id=key)
                inspected = await tool('inspect_point', session_id=key, lon=lon, lat=lat)
                assert inspected['before'] is not None and inspected['after'] is not None
                guide = await client.read_resource('coolblocks://model-guide')
                assert 'UTCI' in guide.contents[0].text
                print(json.dumps({'protocol': 'PASS', 'tools': len(listed.tools), 'backend': status['backend'],
                                  'area_source': area['source'], 'sim_id': simulation['sim_id'],
                                  'weather_source': simulation['weather_source'], 'metrics': metrics,
                                  'point': inspected}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
