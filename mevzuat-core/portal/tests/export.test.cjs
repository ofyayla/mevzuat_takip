'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function portal() {
  const context = vm.createContext({
    URLSearchParams,
    document: {
      documentElement: {},
      querySelector: selector => selector === '#app' ? {} : null,
      addEventListener() {},
    },
    addEventListener() {},
    setInterval() {},
  });
  const run = code => vm.runInContext(code, context);
  for (const file of ['api.js', 'inbox.js', 'views.js', 'app.js']) {
    if (file === 'app.js') run('loadRefs = () => new Promise(() => {});');
    vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  run(`
    globalThis.downloads = [];
    globalThis.busy = false;
    globalThis.closed = false;
    dialog = options => { globalThis.exportDialog = options; };
    dlgBusy = (form, value) => { busy = value; };
    closeDialog = () => { closed = true; };
    toast = (message, options) => { globalThis.notice = { message, options }; };
    download = async (path, query, filename) => {
      downloads.push({ path, query, filename });
      return { headers: { get: () => null } };
    };
  `);
  return {
    run,
    submit(scope) {
      return run(`exportDialog.onOk({ querySelector: () => ({ value: '${scope}' }) })`);
    },
  };
}

test('export dialog defaults to filtered list and disables empty selection', () => {
  const p = portal();
  p.run('S.list.total = 123; exportList();');
  const html = p.run('exportDialog.body');
  assert.match(html, /name="exportScope" value="filtered" checked/);
  assert.match(html, /name="exportScope" value="selected" disabled/);
  assert.match(html, /Filtrelenmiş liste/);
  assert.match(html, /Yalnızca seçilenler/);
  assert.match(html, /123/);
  p.run("S.selected.add('42'); exportList();");
  assert.match(p.run('exportDialog.body'), /name="exportScope" value="selected">/);
  assert.match(p.run('exportDialog.body'), /Yalnızca seçilenler \(1 kayıt\)/);
});

test('filtered export ignores selected IDs and exports all pages with filters and sorting', async () => {
  const p = portal();
  p.run(`
    S.selected.add('42'); S.page = 3; S.q = 'banka';
    S.f.src.add('BDDK'); S.f.sev.add('Kritik'); S.f.eff.add('7');
    S.f.unit.add('RISK'); S.f.needUnit = true;
    S.sort = 'yururluk'; S.sortDir = 'asc'; S.queue = 'birikim'; exportList();
  `);
  assert.equal(await p.submit('filtered'), 'wait');
  const result = JSON.parse(p.run('JSON.stringify(downloads[0])'));
  assert.equal(result.path, '/regulations/export');
  assert.equal(result.filename, 'mevzuat-liste.xlsx');
  assert.deepEqual(result.query, {
    queue: 'birikim', sort: 'effective_date', direction: 'asc', q: 'banka',
    source: ['BDDK'], severity: ['Kritik'], effective: ['7'], unit: ['RISK'], needs_unit: true,
  });
  assert.equal(p.run('closed'), true);
});

test('selected export includes exactly the selection across pages with the same filters and order', async () => {
  const p = portal();
  p.run(`
    S.selected.add('42'); S.selected.add('77'); S.list.ids = ['77', '88'];
    S.page = 2; S.q = 'banka'; S.f.src.add('BDDK'); S.sort = 'onem'; S.sortDir = 'desc';
    exportList();
  `);
  await p.submit('selected');
  assert.deepEqual(JSON.parse(p.run('JSON.stringify(downloads[0].query.ids)')), ['42', '77']);
  assert.equal(p.run('downloads[0].query.q'), 'banka');
  assert.equal(p.run('downloads[0].query.sort'), 'severity');
  assert.equal(p.run('downloads[0].query.direction'), 'desc');
  assert.equal(p.run("'page' in downloads[0].query || 'page_size' in downloads[0].query"), false);
  assert.equal(p.run('qs(downloads[0].query).includes("ids=42%2C77")'), true);
  assert.equal(p.run('S.selected.size'), 2);
});

test('empty selection never falls back to exporting the filtered list', async () => {
  const p = portal();
  p.run('exportList();');
  assert.match(await p.submit('selected'), /En az bir kayıt seçin/);
  assert.equal(p.run('downloads.length'), 0);
  assert.equal(p.run('busy'), false);
});

test('download failures remain visible and allow retry', async () => {
  const p = portal();
  p.run("download = async () => { throw new ApiError(0, 'network', 'Bağlantı kesildi.'); }; exportList();");
  assert.equal(await p.submit('filtered'), 'Bağlantı kesildi.');
  assert.equal(p.run('busy'), false);
  assert.equal(p.run('closed'), false);
});

test('truncated export retains its warning', async () => {
  const p = portal();
  p.run(`
    download = async () => ({ headers: { get: name => name === 'X-Export-Truncated' ? 'true' : '6000' } });
    exportList();
  `);
  await p.submit('filtered');
  assert.match(p.run('notice.message'), /6000 kaydın yalnızca ilk bölümü/);
});

test('irrelevant sample remains unavailable for Excel export', () => {
  const p = portal();
  p.run("S.queue = 'ilgisiz'; exportList();");
  assert.equal(p.run("typeof exportDialog"), 'undefined');
  assert.equal(p.run('notice.options.err'), true);
  assert.match(p.run('notice.message'), /aktarılamaz/);
});
