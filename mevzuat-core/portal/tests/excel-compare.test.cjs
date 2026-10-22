'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadContext() {
  const context = vm.createContext({ URLSearchParams, localStorage: { getItem: () => '' } });
  for (const file of ['api.js', 'inbox.js', 'views.js']) {
    vm.runInContext(readFileSync(path.resolve(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  return context;
}

test('Excel comparison uses title 85 and text 65 in both controls and requests', () => {
  const context = loadContext();
  const params = vm.runInContext('JSON.stringify(xlParams())', context);
  assert.deepEqual(JSON.parse(params), { title_threshold: 85, text_threshold: 65 });
  const html = vm.runInContext('admExcel()', context);
  assert.match(html, /id="xlT" value="85"/);
  assert.match(html, /id="xlX" value="65"/);

  vm.runInContext('S.xl.tText = 80', context);
  assert.equal(vm.runInContext('xlParams().text_threshold', context), 80);
});

test('Excel comparison shows reference evidence and distinguishes review from missing crawl', () => {
  const context = loadContext();
  const evidence = vm.runInContext("xlReference({number:'65/1779',status:'ambiguous',candidateIds:[108,109]})", context);
  assert.match(evidence, /Birden çok aday/);
  assert.match(evidence, /data-id="108"/);
  assert.match(evidence, /65\/1779/);
  assert.equal(vm.runInContext('xlReference(null)', context), '');
  assert.doesNotMatch(vm.runInContext("xlReference({number:'<script>',status:'unverified'})", context), /<script>/);
  const table = vm.runInContext(`xlTable({items:[
    {no:1,title:'Karar',category:'reference_review',group:'missing',reference:{number:'65/1779',status:'conflict'}},
    {no:2,title:'Ham belge',category:'reference_raw_only',group:'missing',rawDocumentId:99}
  ]})`, context);
  assert.match(table, /İnceleme gerekli/);
  assert.match(table, /Kimlik \/ tarih çelişkisi/);
  assert.match(table, /Ham belge #99/);
  assert.match(table, /Toplandı/);
  assert.doesNotMatch(table, /Bulunamadı/);
});
