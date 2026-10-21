'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function portal() {
  const events = new Map();
  const requests = [];
  const context = vm.createContext({
    URLSearchParams,
    document: {
      documentElement: {},
      querySelector: selector => selector === '#app' ? {} : null,
      addEventListener: (name, handler) => events.set(name, handler),
    },
    addEventListener() {},
    setInterval() {},
    fetch: async url => {
      requests.push(new URL(url, 'http://localhost'));
      return {
        ok: true, status: 200,
        headers: { get: name => name === 'content-type' ? 'application/json' : null },
        json: async () => ({ items: [], total: 0 }),
      };
    },
  });
  const run = code => vm.runInContext(code, context);
  for (const file of ['api.js', 'inbox.js', 'views.js', 'app.js']) {
    if (file === 'app.js') run('loadRefs = () => new Promise(() => {});');
    vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  run('refreshInbox = () => {}; paintList = () => {}; paintAll = () => {}; loadKpi = async () => {};');
  return {
    run, requests,
    change(id, value) { events.get('change')({ target: { id, value, dataset: {} } }); },
    click() {
      const target = { dataset: { act: 'sort', s: 'yayim' } };
      events.get('click')({ target: { closest: () => target } });
    },
  };
}

for (const queue of ['yeni', 'birikim', 'tum']) {
  for (const direction of ['asc', 'desc']) {
    test(`${queue}: publication sorting sends ${direction} to the API and retains filters`, () => {
      const p = portal();
      p.run(`S.queue = '${queue}'; S.q = 'banka'; S.f.src.add('BDDK'); S.f.sev.add('Kritik');`);
      p.change('sort', 'yayim');
      p.run("S.page = 4; S.selected.add('42');");
      p.change('sortDir', direction);
      const query = p.requests.at(-1).searchParams;
      assert.equal(query.get('queue'), queue);
      assert.equal(query.get('sort'), 'publish_date');
      assert.equal(query.get('direction'), direction);
      assert.equal(query.get('page'), '1');
      assert.equal(query.get('q'), 'banka');
      assert.equal(query.get('source'), 'BDDK');
      assert.equal(query.get('severity'), 'Kritik');
      assert.equal(p.run('S.selected.size'), 0);
      assert.match(p.run('chipsRow(0, 0, 0)'), /<option value="yayim" selected>Yayım tarihi<\/option>/);
      p.run('S.page = 2;');
      assert.equal(p.run('listParams().sort'), 'publish_date');
      assert.equal(p.run('listParams().direction'), direction);
    });
  }
}

test('publication header starts newest-first and toggles with accessible direction and arrows', () => {
  const p = portal();
  assert.equal(p.run('listParams().sort'), 'priority');
  for (const direction of ['desc', 'asc', 'desc', 'asc']) {
    p.click();
    assert.equal(p.run('listParams().sort'), 'publish_date');
    assert.equal(p.run('listParams().direction'), direction);
    const html = p.run('table([])');
    assert.match(html, new RegExp(`<th class="c-pub" aria-sort="${direction === 'asc' ? 'ascending' : 'descending'}">`));
    assert.match(html, new RegExp(`href="#i-chev-${direction === 'asc' ? 'u' : 'd'}"`));
    assert.match(p.run('chipsRow(0, 0, 0)'), new RegExp(`<option value="${direction}" selected>`));
  }
  p.change('sortDir', 'desc');
  p.click();
  assert.equal(p.run('listParams().direction'), 'asc');
});

test('publication column renders the full year, missing dates and the mobile date', () => {
  const p = portal();
  p.run(`
    globalThis.rows = [
      upsert({ id: 1, title: 'Dated', publishDate: '2026-10-01', detectedAt: '2026-10-20T10:00:00Z', severity: 'Orta' }),
      upsert({ id: 2, title: 'Older', publishDate: '2025-12-31', detectedAt: '2026-10-20T10:00:00Z', severity: 'Orta' }),
      upsert({ id: 3, title: 'Undated', publishDate: null, detectedAt: '2026-10-20T10:00:00Z', severity: 'Orta' }),
    ];
  `);
  const html = p.run('table(rows)');
  assert.match(html, /<td class="c-pub">1 Eki 2026<\/td>/);
  assert.match(html, /<td class="c-pub">31 Ara 2025<\/td>/);
  assert.match(html, /<td class="c-pub"><span class="muted">—<\/span><\/td>/);
  assert.match(html, /<span class="pub-mobile">Yayım 1 Eki 2026<\/span>/);
  assert.doesNotMatch(html, /Invalid Date/);
  for (const queue of ['hazirlaniyor', 'ilgisiz']) {
    p.run(`S.queue = '${queue}';`);
    const special = p.run('table(rows)');
    assert.match(special, /Yayım 1 Eki 2026/);
    assert.doesNotMatch(special, /pub-mobile/);
    assert.doesNotMatch(p.run('chipsRow(0, 0, 0)'), /id="sort"/);
  }
});

test('filtered Excel export preserves both publication directions without pagination', async () => {
  const p = portal();
  p.run(`
    dialog = options => { globalThis.exportDialog = options; };
    dlgBusy = () => {}; closeDialog = () => {}; toast = () => {};
    download = async (path, query) => {
      globalThis.exported = { path, query };
      return { headers: { get: () => null } };
    };
  `);
  for (const direction of ['asc', 'desc']) {
    p.change('sort', 'yayim');
    p.change('sortDir', direction);
    p.run('S.page = 3; exportList();');
    await p.run("exportDialog.onOk({ querySelector: () => ({ value: 'filtered' }) })");
    assert.equal(p.run('exported.path'), '/regulations/export');
    assert.equal(p.run('exported.query.sort'), 'publish_date');
    assert.equal(p.run('exported.query.direction'), direction);
    assert.equal(p.run("'page' in exported.query || 'page_size' in exported.query"), false);
  }
});
