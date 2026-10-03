const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startMcpSync, decorateMcpEdits } = require('../mcp-sync.js');

const flush = () => new Promise((resolve) => setImmediate(resolve));

test('MCP demolition/height edits hide existing and earlier draft buildings', () => {
  const geometry = { type: 'Polygon', coordinates: [[[1, 1], [2, 1], [2, 2], [1, 1]]] };
  const buildings = [{ geometry, properties: { idx: 4 } }];
  const raised = decorateMcpEdits([{ type: 'building', geometry, height: 10 }], buildings);
  assert.deepEqual(raised[0].replaces, { idx: 4 });
  const removed = decorateMcpEdits([{ type: 'building', geometry, height: 10 }, { type: 'building', geometry, height: 0 }], buildings);
  assert.deepEqual(removed[1].demolish, { idx: 4, edit: 0 });
  assert.equal(decorateMcpEdits([], buildings).length, 0, 'clear restores the original buildings');
});

test('publishes semantic context, applies sequential MCP actions and acknowledges once', async () => {
  let scene = { date: null, hour: 15, edits: [], selected_point: null, map_center: [151.187, -33.888],
    baseline: { hours: [{ hour: 15, utci_png: 'large-image', utci_ground_mean: 30 }] }, result: null };
  let events = [];
  const published = [];
  const acked = [];
  const sync = startMcpSync({
    snapshot: () => structuredClone(scene),
    apply: (event) => { if (event.edits) scene.edits = event.edits; if (event.result) scene.result = event.result; },
    status: () => {},
    request: async (path, opts) => {
      if (opts?.method === 'PUT') { const body = JSON.parse(opts.body); published.push(body); return { revision: body.revision }; }
      if (opts?.method === 'DELETE') { acked.push(path.split('/').pop()); events = events.filter((e) => e.id !== acked.at(-1)); return { ok: true }; }
      return { events: structuredClone(events) };
    },
  });
  try {
    await flush();
    assert.equal(published[0].baseline.hours[0].utci_png, undefined);
    assert.equal(published[0].baseline.hours[0].utci_ground_mean, 30);
    events = [
      { id: 'draft', revision: 0, edits: [{ type: 'add_tree' }] },
      { id: 'result', revision: 1, result: { sim_id: 'sim', hours: [{ utci_png: 'browser-image' }] } },
    ];
    await sync.sync();
    assert.equal(scene.edits.length, 1);
    assert.equal(scene.result.hours[0].utci_png, 'browser-image');
    assert.deepEqual(acked, ['draft', 'result']);
    await sync.sync();
    assert.equal(published.length, 1, 'agent actions must not be re-published as human edits');
    scene.hour = 16;
    await sync.sync();
    assert.equal(published[1].hour, 16);
    assert.equal(published[1].revision, 3);
  } finally { sync.stop(); }
});

test('does not overwrite a human edit made while events are being fetched', async () => {
  let scene = { date: null, hour: 15, edits: [], result: null };
  let interfere = false;
  let applied = false;
  const published = [];
  const sync = startMcpSync({
    snapshot: () => structuredClone(scene), apply: () => { applied = true; }, status: () => {},
    request: async (path, opts) => {
      if (opts?.method === 'PUT') { const body = JSON.parse(opts.body); published.push(body); return { revision: body.revision }; }
      if (interfere) { interfere = false; scene.hour = 17; return { events: [{ id: 'old', revision: 0, edits: [{ type: 'add_tree' }] }] }; }
      return { events: [] };
    },
  });
  try {
    await flush();
    interfere = true;
    await sync.sync();
    assert.equal(applied, false);
    await sync.sync();
    assert.equal(published.at(-1).hour, 17);
  } finally { sync.stop(); }
});
