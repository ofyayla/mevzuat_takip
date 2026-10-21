'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function portal() {
  const events = new Map();
  const requests = [];
  const app = {};
  const context = vm.createContext({
    URLSearchParams,
    document: {
      documentElement: {},
      querySelector: selector => selector === '#app' ? app : null,
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
  for (const file of ['api.js', 'inbox.js', 'views.js']) {
    vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  run('loadRefs = () => new Promise(() => {});');
  vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', 'app.js'), 'utf8'), context, { filename: 'app.js' });
  run('refreshInbox = () => {}; paintList = () => {}; paintAll = () => {}; loadKpi = async () => {};');
  return {
    run, requests,
    change(id, value) { events.get('change')({ target: { id, value, dataset: {} } }); },
    click(key) {
      const target = { dataset: { act: 'sort', s: key } };
      events.get('click')({ target: { closest: () => target } });
    },
  };
}

const sorts = [
  ['oncelik', 'priority', 'desc'],
  ['tespit', 'detected_at', 'desc'],
  ['yururluk', 'effective_date', 'asc'],
  ['onem', 'severity', 'desc'],
];

for (const [key, field] of sorts) {
  for (const direction of ['asc', 'desc']) {
    test(`${key} sends ${direction} to the list API`, () => {
      const p = portal();
      p.change('sort', key);
      p.change('sortDir', direction);
      const query = p.requests.at(-1).searchParams;
      assert.equal(query.get('sort'), field);
      assert.equal(query.get('direction'), direction);
      assert.equal(query.get('page'), '1');
      const html = p.run('chipsRow(0, 0, 0)');
      assert.match(html, new RegExp(`<option value="${key}" selected>`));
      assert.match(html, new RegExp(`<option value="${direction}" selected>`));
      assert.match(html, /Küçükten büyüğe/);
      assert.match(html, /Büyükten küçüğe/);
      assert.doesNotMatch(html, /Tespit \(yeniden eskiye\)|Yürürlük \(yakın önce\)/);
    });
  }
}

test('initial sort and defaults on field changes remain unchanged', () => {
  const p = portal();
  assert.equal(p.run('listParams().sort'), 'priority');
  assert.equal(p.run('listParams().direction'), 'desc');
  for (const [key, field, direction] of sorts) {
    p.change('sort', key);
    assert.equal(p.run('S.sort'), key);
    assert.equal(p.run('listParams().sort'), field);
    assert.equal(p.run('listParams().direction'), direction);
    p.change('sortDir', direction === 'asc' ? 'desc' : 'asc');
  }
});

for (const [key, field, direction, label, cls] of [
  ['yururluk', 'effective_date', 'asc', 'Yürürlük', 'c-eff'],
  ['onem', 'severity', 'desc', 'Önem', 'c-sev'],
]) {
  test(`${key} header repeatedly toggles its own direction with matching indicators`, () => {
    const p = portal();
    let expected = direction;
    for (let i = 0; i < 4; i++) {
      p.click(key);
      assert.equal(p.run('S.sort'), key);
      assert.equal(p.run('S.sortDir'), expected);
      const query = p.requests.at(-1).searchParams;
      assert.equal(query.get('sort'), field);
      assert.equal(query.get('direction'), expected);
      const html = p.run('table([])');
      assert.match(html, new RegExp(`<th class="${cls}" aria-sort="${expected === 'asc' ? 'ascending' : 'descending'}">`));
      assert.match(html, new RegExp(`href="#i-chev-${expected === 'asc' ? 'u' : 'd'}"`));
      assert.match(html, new RegExp(`aria-label="${label} sütununa göre ${expected === 'asc' ? 'büyükten küçüğe' : 'küçükten büyüğe'} sırala"`));
      assert.match(p.run('chipsRow(0, 0, 0)'), new RegExp(`<option value="${expected}" selected>`));
      expected = expected === 'asc' ? 'desc' : 'asc';
    }
  });
}

test('dropdown direction and column clicks share the same state', () => {
  const p = portal();
  p.change('sort', 'onem');
  p.change('sortDir', 'asc');
  p.click('onem');
  assert.equal(p.run('S.sortDir'), 'desc');
  p.click('yururluk');
  assert.equal(p.run('S.sortDir'), 'asc');
  const html = p.run('table([])');
  assert.match(html, /<th class="c-eff" aria-sort="ascending">/);
  assert.match(html, /<th class="c-sev" aria-sort="none">/);
});

test('sort changes reset pagination and selection without dropping filters', () => {
  const p = portal();
  for (const change of [
    () => p.change('sort', 'tespit'),
    () => p.change('sortDir', 'asc'),
    () => p.click('onem'),
  ]) {
    p.run("S.page = 4; S.selected.add('42'); S.q = 'test'; S.f.src.add('BDDK'); S.f.sev.add('Kritik');");
    change();
    assert.equal(p.run('S.page'), 1);
    assert.equal(p.run('S.selected.size'), 0);
    const query = p.requests.at(-1).searchParams;
    assert.equal(query.get('q'), 'test');
    assert.equal(query.get('source'), 'BDDK');
    assert.equal(query.get('severity'), 'Kritik');
  }
});

test('all decision queues expose both sorting controls', () => {
  const p = portal();
  for (const queue of ['yeni', 'birikim', 'tum']) {
    p.run(`S.queue = '${queue}';`);
    const html = p.run('chipsRow(0, 0, 0)');
    assert.match(html, /id="sort"/);
    assert.match(html, /id="sortDir"/);
  }
});

test('preparing and irrelevant queues retain their fixed ordering', async () => {
  const p = portal();
  p.change('sort', 'onem');
  p.change('sortDir', 'desc');
  p.run("S.queue = 'hazirlaniyor';");
  assert.doesNotMatch(p.run('chipsRow(0, 0, 0)'), /id="sort(?:Dir)?"/);
  await p.run('loadList()');
  assert.equal(p.requests.at(-1).searchParams.get('sort'), 'detected_at');
  assert.equal(p.requests.at(-1).searchParams.get('direction'), 'asc');
  p.run("S.queue = 'ilgisiz';");
  assert.doesNotMatch(p.run('chipsRow(0, 0, 0)'), /id="sort(?:Dir)?"/);
  await p.run('loadList()');
  assert.equal(p.requests.at(-1).pathname, '/api/v1/regulations/irrelevant-sample');
  assert.equal(p.requests.at(-1).searchParams.has('sort'), false);
  assert.equal(p.requests.at(-1).searchParams.has('direction'), false);
});

test('Excel export uses the selected sort and direction for every field', async () => {
  const p = portal();
  p.run(`
    dialog = options => { globalThis.exportDialog = options; };
    dlgBusy = () => {}; closeDialog = () => {}; toast = () => {};
    download = async (path, query, filename) => {
      globalThis.exported = { path, query, filename };
      return { headers: { get: () => null } };
    };
  `);
  for (const [key, field] of sorts) {
    for (const direction of ['asc', 'desc']) {
      p.change('sort', key);
      p.change('sortDir', direction);
      p.run('S.page = 3; exportList();');
      await p.run("exportDialog.onOk({ querySelector: () => ({ value: 'filtered' }) })");
      assert.equal(p.run('exported.path'), '/regulations/export');
      assert.equal(p.run('exported.filename'), 'mevzuat-liste.xlsx');
      assert.equal(p.run('exported.query.sort'), field);
      assert.equal(p.run('exported.query.direction'), direction);
      assert.equal(p.run("'page' in exported.query || 'page_size' in exported.query"), false);
    }
  }
});
