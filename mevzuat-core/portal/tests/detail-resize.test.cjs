'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function portal(viewport = 1440, clientWidth = viewport) {
  const events = new Map(), windowEvents = new Map(), styles = new Map(), attrs = new Map(), classes = new Set();
  let width = 580, captured = null;
  const handle = {
    closest: selector => selector === '#detailResize' ? handle : null,
    setAttribute: (key, value) => attrs.set(key, Number(value)),
    setPointerCapture: id => { captured = id; },
    hasPointerCapture: id => captured === id,
    releasePointerCapture: () => { captured = null; },
  };
  const pane = { hidden: false, getBoundingClientRect: () => ({ width }) };
  const elements = { '#app': {}, '#inbox': { clientWidth: viewport }, '#detail': pane, '#detailResize': handle };
  const context = vm.createContext({
    innerWidth: viewport,
    document: {
      documentElement: {
        clientWidth,
        style: { setProperty: (name, value) => {
          styles.set(name, value);
          if (name === '--detail-width') width = parseFloat(value);
        } },
        classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
      },
      querySelector: selector => elements[selector] || null,
      addEventListener: (name, handler) => events.set(name, handler),
    },
    getComputedStyle: () => ({ paddingLeft: '28px', paddingRight: '28px' }),
    addEventListener: (name, handler) => windowEvents.set(name, handler),
    setInterval() {},
  });
  const run = code => vm.runInContext(code, context);
  for (const file of ['api.js', 'inbox.js', 'views.js', 'app.js']) {
    if (file === 'app.js') run('loadRefs = () => new Promise(() => {});');
    vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  const fire = (name, extra = {}) => {
    const event = { target: handle, pointerId: 1, button: 0, isPrimary: true, clientX: 800,
      preventDefault() { this.prevented = true; }, ...extra };
    events.get(name)(event);
    return event;
  };
  return { run, fire, styles, attrs, classes, pane, windowEvents, width: () => width, captured: () => captured };
}

test('dragging continuously grows and shrinks the panel and clamps both desktop limits', () => {
  const p = portal();
  p.fire('pointerdown');
  assert.equal(p.captured(), 1);
  assert.equal(p.classes.has('detail-resizing'), true);
  p.fire('pointermove', { clientX: 763 });
  assert.equal(p.width(), 617);
  assert.equal(p.styles.get('--detail-column'), '641px');
  assert.equal(p.attrs.get('aria-valuenow'), 617);
  p.fire('pointermove', { clientX: 892 });
  assert.equal(p.width(), 488);
  p.fire('pointermove', { clientX: 3000 });
  assert.equal(p.width(), 320);
  p.fire('pointermove', { clientX: -3000 });
  assert.equal(p.width(), 1040);
  assert.equal(p.attrs.get('aria-valuemin'), 320);
  assert.equal(p.attrs.get('aria-valuemax'), 1040);
  p.fire('pointerup');
  assert.equal(p.captured(), null);
  assert.equal(p.classes.size, 0);
  p.fire('pointermove', { clientX: 0 });
  assert.equal(p.width(), 1040);
});

test('secondary buttons and pointers cannot start or interfere with resizing', () => {
  const p = portal();
  p.fire('pointerdown', { button: 2 });
  assert.equal(p.captured(), null);
  p.fire('pointerdown', { isPrimary: false });
  assert.equal(p.captured(), null);
  p.fire('pointerdown');
  p.fire('pointermove', { pointerId: 2, clientX: 0 });
  p.fire('pointerup', { pointerId: 2 });
  assert.equal(p.width(), 580);
  assert.equal(p.captured(), 1);
  p.fire('pointercancel');
  assert.equal(p.classes.size, 0);
});

test('pointer capture loss, window blur and viewport resize end dragging', () => {
  for (const end of ['lostpointercapture', 'blur', 'resize']) {
    const p = portal();
    p.fire('pointerdown');
    if (end === 'lostpointercapture') p.fire(end);
    else p.windowEvents.get(end)();
    assert.equal(p.captured(), null, end);
    assert.equal(p.classes.size, 0, end);
  }
});

test('keyboard arrows, Shift, Home and End resize without invoking record shortcuts', () => {
  const p = portal();
  assert.equal(p.fire('keydown', { key: 'ArrowLeft' }).prevented, true);
  assert.equal(p.width(), 590);
  p.fire('keydown', { key: 'ArrowRight', shiftKey: true });
  assert.equal(p.width(), 540);
  p.fire('keydown', { key: 'Home' });
  assert.equal(p.width(), 320);
  p.fire('keydown', { key: 'End' });
  assert.equal(p.width(), 1040);
});

test('mobile widths are constrained to the viewport, including screens below 320px', () => {
  for (const viewport of [900, 375, 280]) {
    const p = portal(viewport);
    p.run('setDetailSize(2000)');
    assert.equal(p.width(), viewport);
    p.run('setDetailSize(100)');
    assert.equal(p.width(), Math.min(320, viewport));
  }
  const p = portal(280, 265);
  p.run('setDetailSize(2000)');
  assert.equal(p.width(), 265);
  assert.equal(p.attrs.get('aria-valuemax'), 265);
});

test('the shared detail markup exposes an accessible resize handle in inbox and archive', () => {
  const p = portal();
  p.run(`upsertArchive({ id: 45, kind: 'baseline', title: 'Example', issuerCode: 'BDDK',
    sources: ['BDDK'], detectedAt: '2026-10-01T00:00:00Z', documents: [] }); S.openId = '45';`);
  for (const view of ['gelen', 'arsiv']) {
    p.run(`S.view = '${view}';`);
    const html = p.run(view === 'gelen' ? 'viewGelen()' : 'viewArsiv()');
    assert.match(html, /id="detailResize" role="separator" tabindex="0" aria-orientation="vertical"/);
    assert.match(html, /aria-controls="detail"/);
  }
});
