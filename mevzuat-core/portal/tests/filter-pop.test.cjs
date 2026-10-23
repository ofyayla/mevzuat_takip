'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

/** Tek "Filtrele" penceresi: açılır, seçim yapılınca yerinde yenilenir ve yeniden çizilen düğmeye bağlı kalır. */
function portal() {
  const events = new Map();
  const app = {};
  let pop = null;
  let anchor = null;
  const makeAnchor = left => {
    const el = {
      dataset: { act: 'pop-filter' },
      isConnected: true,
      getBoundingClientRect: () => ({ left: el.isConnected ? left : 0, bottom: 100 }),
      setAttribute() {},
    };
    return el;
  };
  const renderFilterButtons = () => {
    if (anchor) anchor.isConnected = false;
    anchor = makeAnchor(300);
  };
  renderFilterButtons();
  const filterButtons = {};
  Object.defineProperty(filterButtons, 'innerHTML', { set: renderFilterButtons });
  const context = vm.createContext({
    document: {
      documentElement: {},
      body: { appendChild: element => { pop = element; } },
      querySelector: selector => {
        if (selector === '#app') return app;
        if (selector === '#pop') return pop;
        if (selector === '#fbtns') return filterButtons;
        return selector.includes('pop-filter') ? anchor : null;
      },
      querySelectorAll: () => [],
      createElement: () => ({
        style: {},
        offsetWidth: 240,
        offsetHeight: 160,
        scrollTop: 0,
        contains: () => false,
        remove() { pop = null; },
      }),
      addEventListener: (name, handler) => events.set(name, handler),
    },
    innerHeight: 900,
    innerWidth: 1280,
    addEventListener() {},
    setInterval() {},
  });
  const run = code => vm.runInContext(code, context);
  for (const file of ['api.js', 'inbox.js', 'views.js']) {
    vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  run('loadRefs = () => new Promise(() => {});');
  vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', 'app.js'), 'utf8'), context, { filename: 'app.js' });
  run("refreshInbox = () => { document.querySelector('#fbtns').innerHTML = fbtns(); }; listChanged = () => {}; Boot.sources = [{ code: 'BDDK', name: 'BDDK' }, { code: 'TCMB', name: 'TCMB' }];");
  return {
    run,
    pop: () => pop,
    anchor: () => anchor,
    click: target => events.get('click')({ target: { closest: () => target } }),
    change: target => events.get('change')({ target }),
  };
}

test('the filter button opens one popup with every filter group next to it', () => {
  const p = portal();
  p.click(p.anchor());
  assert.ok(p.pop());
  assert.equal(p.pop().style.left, '300px');   // pencere düğmenin soluna hizalanır
  assert.equal(p.pop().anchor, p.anchor());
  for (const group of ['Önem derecesi', 'Kaynak', 'Yürürlük', 'Eklenme zamanı', 'Önerilen birim']) {
    assert.match(p.pop().innerHTML, new RegExp(group));
  }
  assert.match(p.pop().innerHTML, /data-change="needUnit"/);
});

test('choosing an option refreshes the open popup in place and keeps it tied to the redrawn button', () => {
  const p = portal();
  p.click(p.anchor());
  const firstAnchor = p.anchor();
  p.run("S.f.src.add('BDDK');");
  p.change({ dataset: { change: 'fopt', f: 'src' }, checked: true, value: 'BDDK' });
  assert.ok(p.pop());
  assert.match(p.pop().innerHTML, /value="BDDK" checked/);
  assert.notEqual(p.anchor(), firstAnchor);
  p.run('closePop()');
  p.click(p.anchor());
  p.change({ dataset: { change: 'fopt', f: 'src' }, checked: true, value: 'TCMB' });
  assert.equal(p.run('S.f.src.has("TCMB")'), true);
  assert.equal(p.pop().anchor.isConnected, true);
});

test('the unit-needed switch and clear-all work from the same popup', () => {
  const p = portal();
  p.click(p.anchor());
  p.change({ dataset: { change: 'needUnit' }, checked: true });
  assert.equal(p.run('S.f.needUnit'), true);
  assert.match(p.pop().innerHTML, /data-change="needUnit" checked/);
  p.click({ dataset: { act: 'fclear-all' } });
  assert.equal(p.run('S.f.needUnit'), false);
  assert.equal(p.run('S.f.src.size + S.f.sev.size'), 0);
});

test('clicking the filter button again closes the popup', () => {
  const p = portal();
  p.click(p.anchor());
  assert.ok(p.pop());
  p.click(p.anchor());
  assert.equal(p.pop(), null);
});
