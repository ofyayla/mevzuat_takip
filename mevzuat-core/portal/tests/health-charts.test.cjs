'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function portal() {
  const context = vm.createContext({});
  const run = code => vm.runInContext(code, context);
  for (const file of ['api.js', 'inbox.js', 'views.js']) {
    vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  return run;
}

const points = values => values.map((value, i) => ({ at: new Date(Date.UTC(2026, 9, 21, i)).toISOString(), value }));
function chart(values, options = {}) {
  return portal()(`healthChart(${JSON.stringify(points(values))}, ${JSON.stringify({ label: 'Health', tone: 'ok', ...options })})`);
}

test('line, translucent area, endpoint, accessible label and target use the same numeric scale', () => {
  const html = chart([0, 60000, 230500], { target: 60000, tone: 'crit' });
  assert.match(html, /class="spark sp-crit"/);
  assert.match(html, /role="img" aria-label="Health\./);
  assert.match(html, /class="sp-area"/);
  assert.match(html, /class="sp-line"/);
  assert.match(html, /class="sp-target"/);
  assert.match(html, /r="3"/);
  const targetY = Number(html.match(/class="sp-target"[^>]*y1="([^"]+)"/)[1]);
  const valueY = Number([...html.matchAll(/<circle[^>]*cy="([^"]+)"/g)][1][1]);
  assert.equal(targetY, valueY);
  assert.doesNotMatch(html, /NaN|Infinity/);
});

test('null samples split paths instead of drawing fabricated measurements', () => {
  const html = chart([1, 2, null, 3, 4]);
  assert.equal((html.match(/class="sp-line"/g) || []).length, 2);
  assert.equal((html.match(/class="sp-area"/g) || []).length, 2);
  assert.equal((html.match(/<circle/g) || []).length, 4);
  assert.match(html, /Boş aralıklar: ölçüm yok/);
});

test('timestamps, not array positions, determine horizontal spacing', () => {
  const series = [
    { at: '2026-10-21T00:00:00Z', value: 2 },
    { at: '2026-10-21T01:00:00Z', value: 3 },
    { at: '2026-10-21T04:00:00Z', value: 1 },
  ];
  const html = portal()(`healthChart(${JSON.stringify(series)}, {label:'Queue', tone:'ok'})`);
  assert.deepEqual([...html.matchAll(/<circle[^>]*cx="([^"]+)"/g)].map(m => Number(m[1])), [4, 77, 296]);
});

test('empty or all-null series explicitly show missing data rather than zero', () => {
  for (const values of [[], [null, null]]) {
    const html = chart(values);
    assert.match(html, /Bu dönem için ölçüm yok/);
    assert.doesNotMatch(html, /<svg|<polyline/);
  }
});

test('a single measurement renders only a dot, not a made-up trend', () => {
  const html = chart([null, 7]);
  assert.equal((html.match(/<circle/g) || []).length, 1);
  assert.doesNotMatch(html, /<polyline|class="sp-area"/);
  assert.match(html, /Geçmiş ölçümler birikiyor/);
});

test('zero is valid data and flat histories never divide by zero', () => {
  const html = chart([0, 0, 0]);
  assert.match(html, /class="sp-line" points="4.00,48.00 150.00,48.00 296.00,48.00"/);
  assert.doesNotMatch(html, /NaN|Infinity|Bu dönem için ölçüm yok/);
});

test('percentage target is visible within a fixed 0–100 scale', () => {
  const html = chart([100, 99, 92.6], { percent: true, target: 98, tone: 'warn' });
  const y = Number(html.match(/class="sp-target"[^>]*y1="([^"]+)"/)[1]);
  assert.ok(y > 4 && y < 48);
  assert.match(html, /sp-warn/);
});

test('chart labels and tooltips are escaped', () => {
  const html = chart([1, 2], { label: '<img onerror="x">' });
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img/);
});

function overview(overrides = {}, preparing = {}) {
  const run = portal();
  const metrics = {
    crawlMode: 'remote', pendingIngest: 0,
    llm: { calls: 710, failed: 9, p95Ms: 230500, p50Ms: 41100, targetMs: 60000, series: points([1000, 230500]) },
    scans: { successRate: 92.6, targetRate: 98, total: 578, failed: 43, series: points([99, 92.6]) },
    aiQueue: { count: 316, series: points([10, 316]) },
    ingest: { series: points([2, 0]) },
    ...overrides,
  };
  run(`S.adm.genel = {metrics:${JSON.stringify(metrics)}}; Boot.stats = {preparing:${JSON.stringify(preparing)}};`);
  return run('admGenel()');
}

test('overview renders four charts with reference values and target-based warning states', () => {
  const html = overview({}, { count: 316, oldestDays: 12 });
  assert.equal((html.match(/role="img"/g) || []).length, 4);
  assert.equal((html.match(/class="sp-target"/g) || []).length, 2);
  for (const text of ['230,5 sn', '%92,6', '316 kayıt', '0 belge', 'Hedefin üstünde', 'Büyüyor', 'Tarama başarısı (7 gün)']) {
    assert.ok(html.includes(text), text);
  }
  assert.match(html, /h-crit">Hedefin üstünde/);
  assert.match(html, /h-warn">Dikkat/);
  assert.doesNotMatch(html, /Boş aralıklar: ölçüm yok/);
});

test('threshold equality is healthy and targets come from API', () => {
  const html = overview({
    llm: { calls: 1, failed: 0, p95Ms: 50000, targetMs: 50000, series: points([50000, 50000]) },
    scans: { successRate: 95, targetRate: 95, total: 1, failed: 0, series: points([95, 95]) },
    aiQueue: { count: 0, series: points([1, 0]) },
  });
  assert.doesNotMatch(html, /Hedefin üstünde|Büyüyor|h-warn">Dikkat/);
  assert.match(html, /Hedef ≤ 50,0 sn/);
  assert.match(html, /Hedef ≥ %95/);
});

test('failed calls below latency target remain visible as a warning', () => {
  const html = overview({ llm: { calls: 2, failed: 1, p95Ms: 1000, series: points([1000, 1000]) } });
  assert.match(html, /h-warn">Hatalı çağrı var/);
});

test('unavailable remote ingest is not presented as healthy or not applicable', () => {
  const html = overview({ pendingIngest: null, ingest: { series: points([null, null]) } });
  assert.match(html, /h-warn">Ölçülemiyor/);
  assert.doesNotMatch(html, /Uygulanmaz|0 belge/);
});

test('local ingest is explicitly not applicable, even if old remote history exists', () => {
  const html = overview({ crawlMode: 'local', pendingIngest: null });
  assert.match(html, /Uygulanmaz/);
  assert.match(html, /Yerel toplamada aktarım kuyruğu yok/);
  assert.equal((html.match(/role="img"/g) || []).length, 3);
});

test('no completed scans displays a dash, never a fictitious 100 percent', () => {
  const html = overview({ scans: { total: 0, failed: 0, successRate: null, series: points([null, null]) } });
  assert.match(html, /Tarama yok/);
  assert.doesNotMatch(html, /%100/);
});
