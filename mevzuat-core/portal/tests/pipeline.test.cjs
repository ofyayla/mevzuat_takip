const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const portal = path.resolve(__dirname, '..');
const service = readFileSync(path.join(portal, '..', 'app', 'app', 'services', 'pipeline.py'), 'utf8');
const views = readFileSync(path.join(portal, 'assets', 'views.js'), 'utf8');
const app = readFileSync(path.join(portal, 'assets', 'app.js'), 'utf8');
const api = readFileSync(path.join(portal, 'assets', 'api.js'), 'utf8');

function setup() {
  const context = vm.createContext({ console, URLSearchParams, S: {}, isAdmin: () => true, localStorage: { getItem: () => '' } });
  vm.runInContext(api + '\n' + views, context);
  const pipeline = {
    layers: Array.from(service.matchAll(/\{"id": "([^"]+)", "label": "([^"]+)"\}/g), m => ({ id: m[1], label: m[2] })),
    nodes: Array.from(service.matchAll(/Node\("([^"]+)", "([^"]+)", "([^"]+)", "([^"]+)"/g), (m, i) => ({
      id: m[1], label: m[2], layer: m[3], kind: m[4], count: i,
      description: `Description ${m[1]}`, metrics: {},
    })),
    edges: Array.from(service.matchAll(/Edge\("([^"]+)", "([^"]+)", "([^"]+)"(?:, "([^"]+)")?\)/g), m => ({
      source: m[1], target: m[2], label: m[3], queue: m[4] || '',
    })),
    reviewFlow: {},
  };
  context.S.adm.hat = pipeline;
  context.pipeline = pipeline;
  return { context, pipeline, run: code => vm.runInContext(code, context) };
}

test('renders every server node and edge in the original five lanes', () => {
  const { run, pipeline } = setup();
  assert.equal(pipeline.layers.length, 5);
  assert.equal(pipeline.nodes.length, 17);
  assert.equal(pipeline.edges.length, 21);
  const html = run('hatGraph(pipeline)');
  assert.equal((html.match(/class="hat-lane"/g) || []).length, 5);
  assert.equal((html.match(/class="hat-node /g) || []).length, 17);
  assert.equal((html.match(/class="hat-edge/g) || []).length, 21);
  for (const node of pipeline.nodes) assert.ok(html.includes(`data-n="${node.id}"`));
  assert.ok(html.includes('marker-end="url(#hat-arrow)"'));
  assert.ok(html.includes('is-retry'));
  assert.ok(html.includes('is-fail'));
  assert.ok(!/NaN|undefined/.test(html));
});

test('keeps original node positions distinct and inside the viewBox', () => {
  const { run, pipeline } = setup();
  const positions = new Set();
  for (const n of pipeline.nodes) {
    const b = run(`hatBox(pipeline.nodes.find(n => n.id === ${JSON.stringify(n.id)}), pipeline.layers)`);
    positions.add(`${b.x},${b.y}`);
    assert.ok(b.x >= 0 && b.x + b.w < 1170);
    assert.ok(b.y >= 0 && b.y + b.h < 528);
  }
  assert.equal(positions.size, pipeline.nodes.length);
  assert.equal(run("hatBox(hatNode('reg:READY'), pipeline.layers).row"), 3);
});

test('exposes counts, selection, accessible buttons and technical labels', () => {
  const { context, run } = setup();
  context.S.node = 'reg:READY';
  context.S.tech = true;
  context.S.hatLabels = true;
  const html = run('hatGraph(pipeline)');
  assert.ok(html.includes('hat-graph labels has-selection'));
  assert.ok(html.includes('sel" data-act="node" data-n="reg:READY" tabindex="0" role="button" aria-pressed="true"'));
  assert.ok(html.includes('>READY</text>'));
  assert.equal((html.match(/class="hat-tech"/g) || []).length, 17);
  assert.ok(html.includes('hat-edge hi'));
  context.S.tech = false;
  assert.ok(!run('hatGraph(pipeline)').includes('class="hat-tech"'));
});

test('preserves waiting age thresholds including exact boundaries', () => {
  const { run } = setup();
  for (const [count, oldestDays, expected] of [[0, 10, 'ok'], [1, 2, 'ok'], [1, 3, 'warn'], [1, 6, 'warn'], [1, 7, 'crit']]) {
    assert.equal(run(`hatStatus({count:${count},metrics:{oldestDays:${oldestDays}}})[0]`), expected);
  }
});

test('escapes server labels, descriptions, ids and transition text', () => {
  const { pipeline, run } = setup();
  const payload = '<script>alert("x")</script>';
  pipeline.nodes[0].label = payload;
  pipeline.nodes[0].description = payload;
  pipeline.edges[0].label = payload;
  const html = run('hatGraph(pipeline)');
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
  pipeline.nodes[0].id = 'bad" onload="alert(1)';
  pipeline.edges = [];
  assert.ok(run('hatGraph(pipeline)').includes('data-n="bad&quot; onload=&quot;alert(1)"'));
});

test('does not silently omit invalid transitions', () => {
  const { pipeline, run } = setup();
  pipeline.edges[0].target = 'missing';
  assert.throws(() => run('hatGraph(pipeline)'), /missing/);
});

test('detail panel retains actions, transitions, loading and explicit errors', () => {
  const { context, run } = setup();
  context.S.node = 'reg:AI_FAILED';
  assert.match(run('hatPanel()'), /data-act="retry-all"/);
  assert.match(run('hatPanel()'), /Yükleniyor/);
  assert.match(run('hatPanel()'), /data-n="reg:RELEVANT"/);
  context.S.adm.nodeItems = { node: 'reg:AI_FAILED', items: [], error: 'Connection failed' };
  assert.match(run('hatPanel()'), /role="alert">Connection failed/);
  assert.ok(!run('hatPanel()').includes('Bu adımda kayıt yok.'));
});

test('node request errors remain visible and stale responses cannot replace selection', async () => {
  const { context, run } = setup();
  const load = app.slice(app.indexOf('async function loadNodeItems('), app.indexOf('\n}', app.indexOf('async function loadNodeItems(')) + 2);
  context.renderAll = () => {};
  run(`globalThis.loadTestNode = (function(GET) { ${load}; return loadNodeItems; })((...args) => testGet(...args))`);
  context.S.node = 'fetch';
  context.testGet = async () => { throw new Error('offline'); };
  await run("loadTestNode('fetch')");
  assert.match(context.S.adm.nodeItems.error, /offline/);
  context.testGet = () => new Promise(resolve => { context.resolveRequest = resolve; });
  const pending = run("loadTestNode('fetch')");
  context.S.node = 'sources';
  context.S.adm.nodeItems = { node: 'sources', items: [] };
  context.resolveRequest({ node: 'fetch', items: [] });
  await pending;
  assert.equal(context.S.adm.nodeItems.node, 'sources');
});

test('keeps administration routing and refresh wiring unchanged', () => {
  assert.match(app, /hat: \['yonetim', 'hat'\]/);
  assert.match(app, /if \(t.id === 'hatLabelsT'\)/);
  assert.match(app, /node && \(e.key === 'Enter' \|\| e.key === ' '\)/);
  assert.match(app, /scrollLeft = graphScroll/);
  const { run } = setup();
  const html = run('admHat()');
  assert.match(html, /id="autoT" checked/);
  assert.match(html, /data-act="refresh"/);
  assert.match(html, /data-act="pop-hat"/);
});
