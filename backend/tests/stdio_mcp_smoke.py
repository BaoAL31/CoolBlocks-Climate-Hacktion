"""Optional check that stdio connects to the existing engine through the proxy."""
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    parameters = StdioServerParameters(command=sys.executable,
                                      args=['-X', 'utf8', str(Path(__file__).resolve().parents[1] / 'mcp_bridge.py')])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert len(tools.tools) == 14
            response = await client.call_tool('get_status', {})
            assert not response.isError
            status = response.structuredContent or json.loads(response.content[0].text)
            assert status['backend'] in ('gpu', 'cpu')
            assert status['source'] == 'data/area'
            print(json.dumps({'stdio': 'PASS', 'status': status}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
