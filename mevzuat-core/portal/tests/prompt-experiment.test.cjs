'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadContext() {
  const context = vm.createContext({
    URLSearchParams, FormData, localStorage: { getItem: () => '' },
    document: { querySelector: () => null },
    setTimeout: () => 1, clearTimeout: () => {},
  });
  for (const file of ['api.js', 'inbox.js', 'views.js']) {
    vm.runInContext(readFileSync(path.resolve(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  return context;
}

test('Prompt experiment editor presents fixed settings and escaped, side-by-side saved decisions', () => {
  const context = loadContext();
  vm.runInContext(`S.pe.defaults = {provider:'vllm',model:'test-model',systemPromptA:'A prompt',
    settings:{relevanceThreshold:.3,relevanceWeightModel:.6,relevanceWeightVotes:.3,
      relevanceWeightRules:.1,relevanceSamples:3,sampleTemperature:.7}};
    S.pe.promptB = 'B prompt';
    S.pe.records = [{id:1,title:'<script>alert(1)</script>',issuer:'BDDK',status:'READY'}];
    S.pe.detail = {id:4,title:'İşveren rolü',status:'completed',createdAt:'2026-10-22T10:00:00Z',
      systemPromptA:'A prompt',systemPromptB:'B prompt',
      summary:{total:1,labelled:1,unlabelled:0,changed:1,cachedCalls:0,
        A:{tp:1,fp:0,fn:0,tn:0,labelled:1,precision:1,recall:1},
        B:{tp:0,fp:0,fn:1,tn:0,labelled:1,precision:null,recall:0}},
      items:[{regulationId:1,title:'<img src=x>',expectedRelevant:true,status:'completed',
        A:{is_relevant:true,score:.9,band:'Yüksek',uncertain:false,rationale:'A gerekçesi',cached:false},
        B:{is_relevant:false,score:.1,band:'Düşük',uncertain:true,rationale:'B gerekçesi',cached:true}}]};`, context);
  const html = vm.runInContext('admPromptExperiment()', context);
  assert.match(html, /B · taslak sistem promptu/);
  assert.match(html, /A · mevcut prompt/);
  assert.match(html, /Doğru ilgili \/ gereksiz \/ kaçırılan/);
  assert.match(html, /önbellekten/);
  assert.doesNotMatch(html, /<script>|<img/);
});

test('Starting a prompt experiment persists the selected records, labels and edited B prompt', async () => {
  const context = loadContext();
  vm.runInContext(`var sent = null; var renderAll = () => {}; var toast = () => {};
    api = async (_method, _path, options) => { sent = options.body; return {data:{id:17,title:sent.title,status:'queued'}}; };
    S.pe.promptB = 'Edited full B system prompt';
    S.pe.title = 'Role-based A/B';
    S.pe.selected = {42:{id:42,expectedRelevant:false,title:'Record'}};`, context);
  await vm.runInContext('startPromptExperiment()', context);
  const body = JSON.parse(vm.runInContext('JSON.stringify(sent)', context));
  assert.deepEqual(body, {
    title: 'Role-based A/B',
    system_prompt_b: 'Edited full B system prompt',
    records: [{ regulation_id: 42, expected_relevant: false }],
  });
  assert.equal(vm.runInContext('S.pe.detail.id', context), 17);
});
