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
  const connection = typeof document !== 'undefined' ? document.createElement('div') : null;
  let panel = null;
  if (connection) {
    connection.className = 'agent-connection';
    connection.innerHTML = `
      <button type="button" popovertarget="agent-pairing" aria-label="Connect an agent" title="Connect an agent">
        <i class="ph ph-plugs-connected" aria-hidden="true"></i><span>Connect Agent</span>
      </button>
      <section id="agent-pairing" class="agent-pairing" popover aria-labelledby="agent-pairing-title">
        <div class="agent-pairing-heading">
          <strong id="agent-pairing-title">Connect an agent</strong>
          <button type="button" class="icon-btn" popovertarget="agent-pairing" popovertargetaction="hide" aria-label="Close agent connection"><i class="ph ph-x" aria-hidden="true"></i></button>
        </div>
        <p>Give this code to your agent to connect it to this map session.</p>
        <div data-state role="status">Connecting app...</div>
        <div data-code class="agent-pairing-code">Generating code...</div>
        <button type="button" data-copy disabled>Copy connection code</button>
        <p data-expiry class="agent-pairing-expiry">Codes expire after 10 minutes.</p>
      </section>`;
    (document.querySelector('header .actions') || document.body).appendChild(connection);
    panel = connection.querySelector('.agent-pairing');
    panel.addEventListener('toggle', (event) => { if (event.newState === 'open') tick(); });
    panel.querySelector('[data-copy]').onclick = async () => {
      if (!pairing) return;
      try {
        await navigator.clipboard.writeText(pairing.connection_code);
        panel.querySelector('[data-copy]').textContent = 'Code copied';
      } catch {
        panel.querySelector('[data-copy]').textContent = 'Select and copy the code above';
      }
    };
  }
  function connectSocket() {
    if (typeof WebSocket === 'undefined' || typeof location === 'undefined' || stopped || socket) return;
    const url = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/agent/sessions/${sessionId}/ws`;
    socket = new WebSocket(url);
    socket.onmessage = ({ data }) => {
      const message = JSON.parse(data);
      socketEvents = message.events;
      if (panel) panel.querySelector('[data-state]').textContent = message.agent_connected ? 'Agent connected' : 'App online - waiting for agent';
      tick();
    };
    socket.onclose = () => {
      socket = null;
      if (panel) panel.querySelector('[data-state]').textContent = 'Reconnecting app...';
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
      if (panel?.matches(':popover-open') && Date.now() >= pairingExpires) {
        pairing = await request(`agent/sessions/${sessionId}/pairing`, { method: 'POST' });
        pairingExpires = Date.now() + pairing.expires_in * 1000;
        panel.querySelector('[data-code]').textContent = pairing.connection_code;
        panel.querySelector('[data-copy]').disabled = false;
        panel.querySelector('[data-copy]').textContent = 'Copy connection code';
        panel.querySelector('[data-expiry]').textContent = `Expires at ${new Date(pairingExpires).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}.`;
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
  return { sessionId, sync: tick, stop() { stopped = true; clearInterval(timer); socket?.close(); connection?.remove(); } };
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
