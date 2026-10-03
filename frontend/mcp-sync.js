// Synchronize this map's semantic context and MCP actions. No model/chat UI.
function startMcpSync({ snapshot, apply, request, status }) {
  const sessionId = crypto.randomUUID();
  let revision = 0;
  let previous = '';
  let busy = false;
  let stopped = false;
  const summarize = (value) => {
    if (Array.isArray(value)) return value.map(summarize);
    if (value && typeof value === 'object') return Object.fromEntries(
      Object.entries(value).filter(([key]) => !key.endsWith('_png')).map(([key, v]) => [key, summarize(v)]));
    return value;
  };
  async function tick() {
    if (busy || stopped) return;
    busy = true;
    try {
      const current = summarize(snapshot());
      const fingerprint = JSON.stringify(current);
      if (fingerprint !== previous) {
        // A local edit supersedes any pending agent action. Server revisions prevent stale results.
        const response = await request(`agent/sessions/${sessionId}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...current, revision: revision + (previous ? 1 : 0) }),
        });
        revision = response.revision;
        previous = fingerprint;
      }
      const { events } = await request(`agent/sessions/${sessionId}/events`);
      for (const event of events) {
        if (event.revision === revision) {
          // A human may have interacted while the network request was in flight.
          if (JSON.stringify(summarize(snapshot())) !== previous) break;
          apply(event);
          revision += 1;
          previous = JSON.stringify(summarize(snapshot()));
          const change = event.selected_point ? 'a temperature marker' : event.edits ? 'draft edits' : event.camera ? 'the map camera'
            : event.selected_tool ? 'the selected tool' : 'map_view' in event || 'heat_mode' in event || 'show_shade' in event || 'show_3d' in event ? 'map display settings'
            : 'date' in event || 'hour' in event ? 'the selected time'
            : event.result ? 'simulation results' : 'baseline heat map';
          status(`Agent applied ${change}.`);
        }
        await request(`agent/sessions/${sessionId}/events/${event.id}`, { method: 'DELETE' });
      }
    } catch (error) {
      // Don't block the map if MCP support is offline; try again on the next tick.
      console.warn('CoolBlocks MCP scene sync:', error.message);
      if (/404/.test(error.message)) previous = ''; // Re-publish the scene after a server restart.
    } finally {
      busy = false;
    }
  }
  tick();
  const timer = setInterval(tick, 1000);
  return { sessionId, sync: tick, stop() { stopped = true; clearInterval(timer); } };
}

function decorateMcpEdits(edits, buildings) {
  return edits.map((edit, index) => {
    if (edit.type !== 'building') return edit;
    const key = JSON.stringify(edit.geometry);
    const building = buildings.find((feature) => JSON.stringify(feature.geometry) === key);
    let prior = -1;
    for (let i = index - 1; i >= 0; i--) {
      if (edits[i].type === 'building' && edits[i].height > 0 && JSON.stringify(edits[i].geometry) === key) { prior = i; break; }
    }
    const target = { ...(building ? { idx: building.properties.idx } : {}), ...(prior >= 0 ? { edit: prior } : {}) };
    if (!Object.keys(target).length) return edit;
    return { ...edit, [edit.height > 0 ? 'replaces' : 'demolish']: target };
  });
}

if (typeof module !== 'undefined') module.exports = { startMcpSync, decorateMcpEdits };
