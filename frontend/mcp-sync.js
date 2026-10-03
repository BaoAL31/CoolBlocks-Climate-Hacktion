// Synchronize this map's semantic context and MCP actions. No model/chat UI.
function startMcpSync({ snapshot, apply, request, status }) {
  const sessionId = crypto.randomUUID();
  let revision = 0;
  let previous = '';
  let busy = false;
  let stopped = false;
  let socket = null;
  let socketEvents = [];
  let pairing = null;
  let pairingExpires = 0;
  const panel = typeof document !== 'undefined' ? document.createElement('div') : null;
  if (panel) {
    panel.style.cssText = 'position:fixed;bottom:18px;left:18px;z-index:1000;background:#fff;color:#172b24;padding:12px 16px;border-radius:10px;box-shadow:0 2px 12px #0003;font:13px sans-serif';
    panel.innerHTML = '<strong>Connect Agent</strong><div data-state>Connecting app?</div><button data-copy>Copy connection code</button><div data-code></div>';
    document.body.appendChild(panel);
    panel.querySelector('[data-copy]').onclick = () => pairing && navigator.clipboard.writeText(pairing.connection_code);
  }
  function connectSocket() {
    if (typeof WebSocket === 'undefined' || typeof location === 'undefined' || stopped || socket) return;
    const url = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/agent/sessions/${sessionId}/ws`;
    socket = new WebSocket(url);
    socket.onmessage = ({ data }) => {
      const message = JSON.parse(data);
      socketEvents = message.events;
      if (panel) panel.querySelector('[data-state]').textContent = message.agent_connected ? '? Agent paired ? app online' : '? App online ? waiting for agent';
      tick();
    };
    socket.onclose = () => {
      socket = null;
      if (panel) panel.querySelector('[data-state]').textContent = '? Reconnecting app?';
    };
    socket.onerror = () => socket?.close();
  }
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
      if (panel && Date.now() >= pairingExpires) {
        pairing = await request(`agent/sessions/${sessionId}/pairing`, { method: 'POST' });
        pairingExpires = Date.now() + pairing.expires_in * 1000;
        panel.querySelector('[data-code]').textContent = `${pairing.connection_code} ? expires in 10 minutes`;
        panel.title = `Session: ${sessionId}`;
      }
      connectSocket();
      const { events } = socket?.readyState === 1 ? { events: socketEvents } : await request(`agent/sessions/${sessionId}/events`);
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
        if (socket?.readyState === 1) socket.send(JSON.stringify({ ack: event.id }));
        else await request(`agent/sessions/${sessionId}/events/${event.id}`, { method: 'DELETE' });
      }
    } catch (error) {
      // Don't block the map if MCP support is offline; try again on the next tick.
      console.warn('CoolBlocks MCP scene sync:', error.message);
      if (/404/.test(error.message)) { previous = ''; pairingExpires = 0; } // Re-publish the scene after a server restart.
    } finally {
      busy = false;
    }
  }
  tick();
  const timer = setInterval(tick, 1000);
  return { sessionId, sync: tick, stop() { stopped = true; clearInterval(timer); socket?.close(); panel?.remove(); } };
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
