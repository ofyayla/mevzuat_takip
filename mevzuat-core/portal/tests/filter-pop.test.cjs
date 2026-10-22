'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('opening another filter positions its popup next to the current filter button', () => {
  const events = new Map();
  const app = {};
  let pop = null;
  let anchors = new Map();
  const makeAnchor = (key, left) => {
    const anchor = {
      dataset: { act: 'pop-filter', f: key },
      isConnected: true,
      getBoundingClientRect: () => ({ left: anchor.isConnected ? left : 0, bottom: 100 }),
      setAttribute() {},
    };
    return anchor;
  };
  const renderFilterButtons = () => {
    for (const anchor of anchors.values()) anchor.isConnected = false;
    anchors = new Map([
      ['src', makeAnchor('src', 100)],
      ['sev', makeAnchor('sev', 300)],
    ]);
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
        const match = selector.match(/data-f="(src|sev)"/);
        return match ? anchors.get(match[1]) : null;
      },
      createElement: () => ({
        style: {},
        offsetWidth: 240,
        offsetHeight: 160,
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

  const click = anchor => events.get('click')({ target: { closest: () => anchor } });
  click(anchors.get('src'));
  const secondFilter = anchors.get('sev');
  click(secondFilter);

  assert.equal(pop.style.left, '300px');
  assert.equal(pop.anchor, anchors.get('sev'));
  assert.equal(pop.anchor.isConnected, true);
});
