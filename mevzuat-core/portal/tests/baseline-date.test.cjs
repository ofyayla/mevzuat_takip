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
  const apply = { disabled: false };
  const removed = [];
  const card = {
    querySelector: () => apply,
    querySelectorAll: () => ['result', 'error'].map(name => ({ remove: () => removed.push(name) })),
  };
  const input = { id: 'blIn', value: '', checkValidity: () => true, closest: () => card };
  const context = vm.createContext({
    document: {
      documentElement: {},
      querySelector: selector => selector === '#blIn' ? input : selector === '#app' ? app : null,
      addEventListener: (name, handler) => events.set(name, handler),
    },
    addEventListener() {},
    setInterval() {},
  });
  const run = code => vm.runInContext(code, context);
  for (const file of ['api.js', 'inbox.js', 'views.js']) {
    vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  run('loadRefs = () => new Promise(() => {});');
  vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', 'app.js'), 'utf8'), context, { filename: 'app.js' });
  context.api = (method, url, options) => {
    if (method === 'GET') return new Promise((resolve, reject) => {
      requests.push({ url, query: options.query, resolve: data => resolve({ data }), reject });
    });
    requests.push({ method, url, options });
    return Promise.resolve({});
  };
  run(`globalThis.renders = 0; globalThis.messages = []; globalThis.confirmation = null;
    renderAll = () => { renders++; };
    toast = message => messages.push(message);
    dialog = options => { confirmation = options; };
    dlgBusy = () => {}; closeDialog = () => {}; loadAdmin = () => {};
    Boot.me = { name: 'Test', roles: ['admin'] };
    S.adm.bakim = { baseline: { current: '2026-10-01' }, failed: [] };`);
  return {
    run, input, apply, removed, requests,
    enter(value) { input.value = value; events.get('input')({ target: input }); },
    click(action) {
      const target = { dataset: { act: action } };
      events.get('click')({ target: { closest: () => target } });
    },
  };
}

const plan = cutoff => ({ cutoff, converted: 3, crawl: [], sources: [] });
const settle = () => new Promise(resolve => setImmediate(resolve));

test('baseline renders an accessible native date field and preserves the selected date', () => {
  const p = portal();
  p.enter('2026-09-15');
  const html = p.run('admBakim()');
  assert.match(html, /<label class="field">Yeni sınır<input[^>]*type="date"[^>]*id="blIn"[^>]*value="2026-09-15"/);
  assert.match(html, /value="01.10.2026" disabled/);
  assert.match(html, /data-act="bl-apply" disabled/);
});

test('a selected date is previewed and applied as the same ISO calendar date', async () => {
  const p = portal();
  p.enter('2024-02-29');
  p.click('bl-preview');
  assert.equal(p.requests[0].url, '/admin/rebaseline');
  assert.equal(p.requests[0].query.cutoff, '2024-02-29');
  p.requests[0].resolve(plan('2024-02-29'));
  await settle();
  assert.equal(p.run('S.blPlan.cutoff'), '2024-02-29');
  p.click('bl-apply');
  assert.match(p.run('confirmation.body'), /29.02.2024/);
  assert.equal(p.requests.length, 1, 'applying still requires confirmation');
  await p.run('confirmation.onOk({})');
  assert.equal(p.requests[1].method, 'POST');
  assert.equal(p.requests[1].options.body.cutoff, '2024-02-29');
});

for (const value of ['', '2026-02-30', '15.10.2026']) {
  test(`invalid or empty date ${JSON.stringify(value)} never requests a preview`, () => {
    const p = portal();
    p.enter(value);
    p.click('bl-preview');
    assert.equal(p.requests.length, 0);
    assert.match(p.run('S.blErr'), /Geçerli bir tarih/);
    assert.equal(p.run('S.blPlan'), null);
  });
}

test('native date validity is checked before previewing', () => {
  const p = portal();
  p.enter('2026-10-15');
  p.input.checkValidity = () => false;
  p.click('bl-preview');
  assert.equal(p.requests.length, 0);
  assert.match(p.run('S.blErr'), /Geçerli bir tarih/);
});

test('editing or clearing the date invalidates the preview without replacing the input', () => {
  const p = portal();
  for (const value of ['2026-09-01', '']) {
    p.run("S.blPlan = { cutoff: '2026-10-01' }; S.blErr = 'old error';");
    p.enter(value);
    assert.equal(p.run('S.bl'), value);
    assert.equal(p.run('S.blPlan'), null);
    assert.equal(p.run('S.blErr'), null);
    assert.equal(p.apply.disabled, true);
    assert.equal(p.run('renders'), 0);
    p.click('bl-apply');
    assert.equal(p.run('confirmation'), null);
  }
  assert.deepEqual(p.removed, ['result', 'error', 'result', 'error']);
});

for (const outcome of ['resolve', 'reject']) {
  test(`a stale preview ${outcome} cannot restore an old plan or error`, async () => {
    const p = portal();
    p.enter('2026-09-01');
    p.click('bl-preview');
    p.enter('2026-10-01');
    p.click('bl-preview');
    p.requests[1].resolve(plan('2026-10-01'));
    await settle();
    const renders = p.run('renders');
    p.requests[0][outcome](outcome === 'resolve' ? plan('2026-09-01') : new Error('old failure'));
    await settle();
    assert.equal(p.run('S.blPlan.cutoff'), '2026-10-01');
    assert.equal(p.run('S.blErr'), null);
    assert.equal(p.run('renders'), renders);
  });
}

test('current preview failures remain visible', async () => {
  const p = portal();
  p.enter('2026-10-01');
  p.click('bl-preview');
  p.requests[0].reject(new Error('preview unavailable'));
  await settle();
  assert.ok(p.run('S.blErr'));
  assert.equal(p.run('S.blPlan'), null);
});

test('a mismatched preview cannot be applied even without an input event', () => {
  const p = portal();
  p.run("S.blPlan = { cutoff: '2026-10-01' };");
  p.input.value = '2026-09-01';
  p.click('bl-apply');
  assert.equal(p.run('confirmation'), null);
  assert.match(p.run('messages[0]'), /önizleyin/);
});
