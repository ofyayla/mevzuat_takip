'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function portal() {
  const events = new Map();
  const app = {};
  let pop = null;
  const context = vm.createContext({
    document: {
      documentElement: {},
      querySelector: selector => selector === '#app' ? app : selector === '#pop' ? pop : null,
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
  context.capturePop = (anchor, html, info) => {
    pop = { html, anchor, contains: () => true, remove: () => { pop = null; } };
    run(`S.pop = ${JSON.stringify(info)};`);
  };
  run('openPop = capturePop;');
  return {
    run,
    get html() { return pop && pop.html; },
    click(action) {
      const target = { dataset: { act: action } };
      events.get('click')({ target: { closest: () => target } });
    },
  };
}

test('every notification links to the alarm list, not its operational target', () => {
  const p = portal();
  p.run(`S.alerts = [
    { severity: 'high', type: 'source', openedAt: '2026-10-21T09:00:00Z', message: 'Source unavailable', link: 'kaynaklar' },
    { severity: 'warning', type: 'pipeline', openedAt: '2026-10-21T10:00:00Z', message: 'Processing delayed', link: 'hat' }
  ];`);
  p.click('pop-bell');
  assert.equal((p.html.match(/<a class="alarm" href="#yonetim" data-act="pop-close">/g) || []).length, 2);
  assert.match(p.html, /Source unavailable/);
  assert.match(p.html, /Processing delayed/);
  assert.doesNotMatch(p.html, /href="#(?:kaynaklar|hat)"/);
  assert.match(p.html, /<a class="btn btn-sm" href="#yonetim" data-act="pop-close">/);
});

for (const hash of ['gelen', 'kaynaklar', 'yonetim']) {
  test(`notification activation closes the popup from #${hash}`, () => {
    const p = portal();
    p.run(`globalThis.location = { hash: '#${hash}' };`);
    p.click('pop-bell');
    assert.equal(p.run('S.pop.b'), 1);
    p.click('pop-close');
    assert.equal(p.html, null);
    assert.equal(p.run('S.pop'), null);
    assert.equal(p.run("ROUTES.yonetim.join('/')"), 'yonetim/genel');
  });
}

test('empty notifications retain the all-notifications link', () => {
  const p = portal();
  p.click('pop-bell');
  assert.doesNotMatch(p.html, /class="alarm"/);
  assert.match(p.html, /href="#yonetim" data-act="pop-close"/);
});

test('notification text stays escaped inside the link', () => {
  const p = portal();
  p.run(`S.alerts = [{ severity: 'info', type: '<img>', openedAt: '2026-10-21T09:00:00Z', message: '<script>alert("x")</script>' }];`);
  p.click('pop-bell');
  assert.match(p.html, /&lt;img&gt;/);
  assert.match(p.html, /&lt;script&gt;/);
  assert.doesNotMatch(p.html, /<script>|<img>/);
});
