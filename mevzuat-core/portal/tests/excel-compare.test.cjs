'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('Excel comparison uses title 85 and text 65 in both controls and requests', () => {
  const context = vm.createContext({ URLSearchParams, S: {}, localStorage: { getItem: () => '' } });
  for (const file of ['api.js', 'views.js']) {
    vm.runInContext(readFileSync(path.resolve(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }

  const params = vm.runInContext('JSON.stringify(xlParams())', context);
  assert.deepEqual(JSON.parse(params), { title_threshold: 85, text_threshold: 65 });
  const html = vm.runInContext('admExcel()', context);
  assert.match(html, /id="xlT" value="85"/);
  assert.match(html, /id="xlX" value="65"/);

  vm.runInContext('S.xl.tText = 80', context);
  assert.equal(vm.runInContext('xlParams().text_threshold', context), 80);
});
