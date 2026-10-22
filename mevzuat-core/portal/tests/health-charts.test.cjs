'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const assets = path.resolve(__dirname, '..', 'assets');

function setup() {
  const context = vm.createContext({ URLSearchParams, S: {}, localStorage: { getItem: () => '' } });
  for (const file of ['api.js', 'views.js']) {
    vm.runInContext(readFileSync(path.join(assets, file), 'utf8'), context, { filename: file });
  }
  return code => vm.runInContext(code, context);
}

test('renders non-empty chart series including a measured zero and target', () => {
  const run = setup();
  const html = run(`healthChart([
    {at:'2026-10-22T10:00:00Z',value:1250},
    {at:'2026-10-22T11:00:00Z',value:0}
  ], {label:'LLM latency',tone:'ok',target:60000,format:String})`);

  assert.match(html, /class="health-chart"/);
  assert.match(html, /class="sp-target"/);
  assert.equal((html.match(/class="sp-end"/g) || []).length, 2);
  assert.match(html, /Boş aralıklar: ölçüm yok/);
  assert.doesNotMatch(html, /Bu dönem için ölçüm yok/);
});

test('does not connect measurements across missing intervals', () => {
  const run = setup();
  const html = run(`healthChart([
    {at:'2026-10-22T10:00:00Z',value:1},
    {at:'2026-10-22T11:00:00Z',value:null},
    {at:'2026-10-22T12:00:00Z',value:2}
  ], {label:'Queue',tone:'ok'})`);

  assert.equal((html.match(/class="sp-end"/g) || []).length, 2);
  assert.doesNotMatch(html, /class="sp-line"/);
});

test('explains when no measurements exist yet', () => {
  const run = setup();
  assert.equal(
    run("healthChart([], {label:'Queue',tone:'ok'})"),
    '<div class="spark-empty">Bu dönem için ölçüm yok</div>',
  );
});
