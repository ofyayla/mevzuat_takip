'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function portal() {
  const requests = [];
  const response = {
    items: [{ id: 45, kind: 'baseline', title: 'Örnek düzenleme', issuerCode: 'BDDK',
      sources: ['BDDK'], publishDate: '2026-01-01', detectedAt: '2026-01-02T00:00:00Z' }],
    total: 1,
  };
  const context = vm.createContext({
    URLSearchParams,
    document: { documentElement: {}, querySelector: () => null, addEventListener() {} },
    addEventListener() {},
    setInterval() {},
    fetch: async url => {
      requests.push(new URL(url, 'http://localhost'));
      return {
        ok: true, status: 200,
        headers: { get: name => name === 'content-type' ? 'application/json' : null },
        json: async () => response,
      };
    },
  });
  const run = code => vm.runInContext(code, context);
  for (const file of ['api.js', 'inbox.js', 'views.js']) {
    vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  run('paintArchive = () => {};');
  return { run, requests };
}

test('baseline archive filter requests and renders baseline records', async () => {
  const p = portal();
  p.run("S.arsivF = 'baseline'; S.arStats = { approved: 0, approvedWithChange: 0, rejected: 0, irrelevantConfirmed: 0, baselineAll: 1 };");
  await p.run('loadArchive()');

  assert.equal(p.requests[0].pathname, '/api/v1/archive/regulations');
  assert.equal(p.requests[0].searchParams.get('kind'), 'baseline');
  assert.equal(p.run('S.ar.total'), 1);
  assert.equal(p.run("byId('45').kind"), 'baseline');
  const html = p.run('arsivBody()');
  assert.match(html, /İlk tarama/);
  assert.match(html, /YZ ilgililik analizine girmedi/);
  assert.match(html, /Örnek düzenleme/);
});

test('baseline details explain their status and expose no decision actions', () => {
  const p = portal();
  p.run(`globalThis.baseline = upsertArchive({
    id: 45, kind: 'baseline', title: 'Örnek düzenleme', issuerCode: 'BDDK',
    sources: ['BDDK'], note: 'İlk taramada zaten yayındaydı.', documents: []
  });`);

  assert.equal(p.run('baselineTab(baseline)').includes('İlk taramada zaten yayındaydı.'), true);
  assert.match(p.run('docTab(baseline)'), /Kaynak belgeler/);
  assert.equal(p.run('actionsHtml(baseline)'), '');
  assert.equal(p.run('queueLabel(baseline)'), 'Arşiv · İlk tarama');
});
