# CoolBlocks MCP

External agents can understand the current map and use the same physics tools as
the browser. There is no in-app chat, model provider or model API key requirement.

## Run and connect

From the repository root in Windows PowerShell:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/` and reload an existing tab to load scene synchronization.
The Streamable HTTP MCP endpoint is **`http://127.0.0.1:8000/mcp/`**. Add this URL
to your external agent's MCP connections. Keep the app bound to localhost; this
local prototype does not implement remote authentication or multi-user access.

For clients that use stdio, run `backend/mcp_bridge.py` with the backend's Python:

```json
{
  "mcpServers": {
    "coolblocks": {
      "command": "D:/Projects/BaoAL31-climate-tion-heatmap/backend/.venv/Scripts/python.exe",
      "args": ["-X", "utf8", "D:/Projects/BaoAL31-climate-tion-heatmap/backend/mcp_bridge.py"],
      "env": { "COOLBLOCKS_URL": "http://127.0.0.1:8000" }
    }
  }
}
```

Replace paths if your checkout is elsewhere. The stdio bridge proxies the running
app; it does not start another SOLWEIG engine. Engine logs stay out of MCP stdout.
HTTP mounting follows the [official Python SDK mounting example](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/examples/snippets/servers/streamable_http_basic_mounting.py),
including entering the MCP session manager in the parent application's lifespan.

## Tools and context

| Tool | Purpose |
| --- | --- |
| `get_status` | CPU/GPU backend, active calculation, elapsed time, queued requests |
| `list_sessions` | Discover scenes and identify connected browser sessions |
| `get_scene` | Selected date/hour/point, map center, draft edits, baseline and scenario metrics |
| `create_scene` | Create an independent agent scenario when no browser is open |
| `set_time` | Change local date/hour and clear stale results |
| `get_area` | Campus bounds, data source, editable surfaces/tree sizes and limitations |
| `get_features` | Bounded building/tree GeoJSON, optionally filtered by bounding box |
| `get_weather` | Selected day's weather and fallback provenance |
| `get_baseline` | Calculate baseline heat and display it in a connected browser |
| `inspect_point` | UTCI, surface and shade before/after at a coordinate |
| `find_hotspot` | Hottest finite ground-level UTCI cell for the selected time/scenario |
| `place_temperature_marker` | Display a temperature popup at a coordinate on the real/heat map |
| `stage_edits` | Replace hypothetical draft edits; an empty list clears them |
| `run_simulation` | Before/after calculation, measured changes and browser result overlays |

Read `coolblocks://model-guide` for units, assumptions and interpretation. Tools
return compact numerical evidence; image payloads are sent only to the browser.
UTCI is a feels-like index, not air temperature. Negative UTCI deltas mean cooling;
source assumptions and fallback weather remain explicit.

Typical agent workflow:

1. Call `get_status`, `get_area`, and `list_sessions`.
2. Choose the user's browser session, then call `get_scene`. If working independently,
   use `create_scene` and the returned session ID instead.
3. Use the selected point or actual `get_features` coordinates; do not invent locations.
4. Inspect weather and baseline/point metrics before explaining the heat.
5. For requested changes, call `stage_edits`, then `run_simulation` and inspect measured deltas.

Example draft tree (coordinates must be inside the study area and off roofs):

```json
{
  "session_id": "<from list_sessions or create_scene>",
  "edits": [{
    "type": "add_tree",
    "geometry": { "type": "Point", "coordinates": [151.187, -33.888] },
    "size": "medium"
  }]
}
```

`stage_edits` replaces the whole draft: include existing edits to retain them.
Use valid GeoJSON Polygons for surface/building/removal edits, `surface` for ground
changes, and `height` for buildings (`0` means hypothetical demolition).
Simulation hours are 9–18, defaulting to the selected hour.

Browser scenes synchronize once per second after the maps initialize. If the user
changes the scene while a calculation is running, the tool rejects its stale result
instead of overwriting the user's work. Drafts and results are held in server memory
and disappear on restart. Session IDs identify tabs, not authenticated users.
Simulations are serialized across browser and MCP calls because their working
directories and native GPU context are shared. Source area rasters are never edited.

## Validation

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
node --test frontend/tests/mcp-sync.test.js
# Optional, with the app running (the first command performs a real GPU/CPU simulation):
.\backend\.venv\Scripts\python.exe backend/tests/live_mcp_smoke.py
.\backend\.venv\Scripts\python.exe backend/tests/stdio_mcp_smoke.py
```

Protocol tests cover initialization, tool discovery/calls, model-guide resource reads,
invalid/outside/roof edits, independent scenarios, image separation and stale results.
