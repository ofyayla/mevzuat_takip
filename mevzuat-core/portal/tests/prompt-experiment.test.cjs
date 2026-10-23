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
  const setup = vm.runInContext('admPromptExperiment()', context);
  assert.match(setup, /B · taslak sistem promptu/);
  assert.match(setup, /A · mevcut prompt/);
  assert.match(setup, /0,60 \/ 0,30 \/ 0,10/);
  assert.match(setup, /önbellekten/);
  assert.doesNotMatch(setup, /<script>|<img/);

  vm.runInContext(`S.pe.mode = 'sonuc'; S.pe.runs = [S.pe.detail]; S.pe.itemOpen = 1;`, context);
  const results = vm.runInContext('admPromptExperiment()', context);
  assert.match(results, /Gereksiz uyarı/);
  assert.match(results, /B bozdu/);
  assert.match(results, /A gerekçesi[\s\S]*B gerekçesi/);
  assert.match(results, /önbellekten/);
  assert.match(results, /Tamamlandı/);
  assert.doesNotMatch(results, /<script>|<img/);
});

test('Run card counts calls from the chosen set and blocks a B prompt identical to A', () => {
  const context = loadContext();
  vm.runInContext(`S.pe.defaults = {provider:'vllm',model:'m',systemPromptA:'Aynı prompt metni',settings:{relevanceSamples:3}};
    S.pe.setId = 3; S.pe.setDetail = {id:3,name:'Set',labels:[{regulationId:1,expectedRelevant:true},{regulationId:2,expectedRelevant:null}]};
    S.pe.title = 'Deney'; S.pe.promptB = 'Aynı prompt metni';`, context);
  let card = vm.runInContext('peRunCard()', context);
  assert.match(card, /<dd class="num">12<\/dd>/);
  assert.match(card, /2 kayıt, 1 etiketli/);
  assert.match(card, /data-act="pe-start" disabled/);
  vm.runInContext(`S.pe.promptB = 'Aynı prompt metni\\nYeni kural';`, context);
  card = vm.runInContext('peRunCard()', context);
  assert.match(card, /data-act="pe-start" >/);
  assert.match(vm.runInContext('peBStat()', context), /\+1<\/span>/);
});

test('Prompt diff marks added and removed lines, collapses unchanged runs and escapes text', () => {
  const context = loadContext();
  const lines = JSON.parse(vm.runInContext(`JSON.stringify(peLineDiff('a\\nb\\nc', 'a\\nB\\nc\\nd'))`, context));
  assert.deepEqual(lines, [[' ', 'a'], ['-', 'b'], ['+', 'B'], [' ', 'c'], ['+', 'd']]);
  const same = Array.from({ length: 20 }, (_, i) => `satır ${i}`).join('\\n');
  const html = vm.runInContext(`peDiffView('${same}', '${same}\\n<script>x</script>')`, context);
  assert.match(html, /18 satır değişmedi/);
  assert.match(html, /&lt;script&gt;x&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test('Record table filters by outcome, shows failed records and colours strata by direction', () => {
  const context = loadContext();
  vm.runInContext(`var renderAll = () => {};
    const r = rel => ({is_relevant:rel,score:rel ? .8 : .1,uncertain:false,rationale:'g',cached:false});
    S.pe.detail = {id:6,title:'D',status:'partial',items:[
      {regulationId:1,title:'Düzelen',expectedRelevant:true,status:'completed',A:r(false),B:r(true)},
      {regulationId:2,title:'Bozulan',expectedRelevant:false,status:'completed',A:r(false),B:r(true)},
      {regulationId:3,title:'Aynı',expectedRelevant:true,status:'completed',A:r(true),B:r(true)},
      {regulationId:4,title:'Hatalı',expectedRelevant:null,status:'failed',A:r(true),B:null,errorB:'LLM yanıtı geçersiz'}],
      summary:{total:4,labelled:3,unlabelled:1,changed:2,cachedCalls:0,A:{},B:{},significance:{aOnlyCorrect:1,bOnlyCorrect:1,p:1},
        strata:{extra_alert:{total:2,A:{n:2,relevant:0,rate:0,ci:null},B:{n:2,relevant:2,rate:1,ci:null},gained:2,lost:0}}}};`, context);
  let html = vm.runInContext('promptExperimentResults(S.pe.detail)', context);
  assert.match(html, /Karar değişen<span class="cnt num">2/);
  assert.match(html, /Hatalı<span class="cnt num">1/);
  assert.match(html, /Berabere/);
  assert.match(html, /<span class="h-crit">\+2<\/span>/);
  assert.match(html, /Düzelen[\s\S]*B düzeltti/);
  assert.doesNotMatch(html, />Aynı<\/span><\/button>/);
  vm.runInContext(`S.pe.itemFilter = 'error';`, context);
  html = vm.runInContext('promptExperimentResults(S.pe.detail)', context);
  assert.match(html, /Hatalı[\s\S]*<span class="tag tag-crit">Hata<\/span>/);
  assert.doesNotMatch(html, /Düzelen/);
});

test('Reusing a manual experiment copies its B prompt and record selection into a new draft', () => {
  const context = loadContext();
  vm.runInContext(`var renderAll = () => {}; var toast = () => {};
    S.pe.detail = {id:8,title:'Elle',status:'completed',evaluationSetId:null,systemPromptB:'B metni',
      items:[{regulationId:5,title:'K',expectedRelevant:false}]};
    reusePromptExperiment(8);`, context);
  assert.equal(vm.runInContext('S.pe.mode', context), 'yeni');
  assert.equal(vm.runInContext('S.pe.promptB', context), 'B metni');
  assert.equal(vm.runInContext('peSource(S.pe)', context), 'manual');
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(S.pe.selected)', context)),
    { 5: { id: 5, title: 'K', expectedRelevant: false } });
});

test('Experiment results offer an Excel export that is disabled while the run is in progress', async () => {
  const context = loadContext();
  const detail = status => `S.pe.detail = {id:4,title:'Deney',status:'${status}',items:[]};`;
  vm.runInContext(detail('running'), context);
  assert.match(vm.runInContext('promptExperimentResults(S.pe.detail)', context),
    /data-act="pe-export" data-id="4" disabled/);
  vm.runInContext(detail('completed'), context);
  assert.match(vm.runInContext('promptExperimentResults(S.pe.detail)', context),
    /data-act="pe-export" data-id="4" >/);

  vm.runInContext(`var requested = null; var toast = () => {}; var renderAll = () => {};
    download = async (path, query, name) => { requested = [path, name]; };`, context);
  await vm.runInContext('exportPromptExperiment(4)', context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(requested)', context)),
    ['/evaluation/prompt-experiments/4/export', 'prompt-deneyi-4.xlsx']);
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
