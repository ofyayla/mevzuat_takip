'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadContext() {
  const context = vm.createContext({
    URLSearchParams, FormData, File, localStorage: { getItem: () => '' },
    document: { querySelector: () => null },
    setTimeout: () => 1, clearTimeout: () => {},
  });
  for (const file of ['api.js', 'inbox.js', 'views.js']) {
    vm.runInContext(readFileSync(path.resolve(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  return context;
}

const SET = `{id:3,name:'<b>Ekim seti</b>',counts:{excel_relevant:1,excel_irrelevant:1,extra_alert:1,system_irrelevant:0},
  params:{seed:42,leakageExcluded:2,populations:{extra_alert:{population:10,sampled:1},system_irrelevant:{population:0,sampled:0}}},
  labels:[{regulationId:7,title:'<img src=x>',stratum:'extra_alert',stratumLabel:"Fazla uyarı (Excel'de yok)",
    expectedRelevant:false,source:'excel_absent',excelRow:null,note:null}]}`;

test('Evaluation set card shows strata, label editors and escapes saved text', () => {
  const context = loadContext();
  vm.runInContext(`S.pe.defaults = {provider:'vllm',model:'m',systemPromptA:'A',settings:{},maxRecords:200};
    S.pe.sets = [${SET}]; S.pe.setId = 3; S.pe.setDetail = ${SET};`, context);
  const html = vm.runInContext('admPromptExperiment()', context);
  assert.match(html, /Değerlendirme seti/);
  assert.match(html, /data-change="pe-set-label" data-id="7"/);
  assert.match(html, /few-shot nedeniyle çıkarılan 2/);
  assert.match(html, /Setle A\/B deneyini çalıştır/);
  assert.match(html, /\/200\)/);
  assert.doesNotMatch(html, /<b>Ekim|<img/);
});

test('Starting an experiment with a set sends only the set id and the B prompt', async () => {
  const context = loadContext();
  vm.runInContext(`var sent = null; var renderAll = () => {}; var toast = () => {};
    api = async (_m, _p, options) => { sent = options.body; return {data:{id:9,title:sent.title,status:'queued'}}; };
    S.pe.promptB = 'Edited full B system prompt'; S.pe.title = 'Set run';
    S.pe.setId = 3; S.pe.setDetail = ${SET}; S.pe.selected = {42:{id:42,expectedRelevant:false,title:'ignored'}};`, context);
  await vm.runInContext('startPromptExperiment()', context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(sent)', context)),
    { title: 'Set run', evaluation_set_id: 3, system_prompt_b: 'Edited full B system prompt' });
  assert.equal(vm.runInContext('S.pe.detail.id', context), 9);
});

test('Results show per-stratum rates, weighted precision, significance and changed decisions', () => {
  const context = loadContext();
  vm.runInContext(`S.pe.defaults = {provider:'vllm',model:'m',systemPromptA:'A',settings:{}};
    S.pe.detail = {id:5,title:'Set run',status:'completed',createdAt:'2026-10-23T10:00:00Z',systemPromptA:'A',systemPromptB:'B',
      summary:{total:2,labelled:2,unlabelled:0,changed:1,cachedCalls:0,
        A:{tp:1,fp:1,fn:0,tn:0,labelled:2,precision:.5,recall:1},B:{tp:0,fp:0,fn:1,tn:1,labelled:2,precision:null,recall:0},
        strata:{excel_relevant:{total:1,A:{n:1,relevant:1,rate:1,ci:[.206,1]},B:{n:1,relevant:0,rate:0,ci:[0,.794]},gained:0,lost:1},
                extra_alert:{total:1,A:{n:1,relevant:1,rate:1,ci:[.206,1]},B:{n:1,relevant:0,rate:0,ci:[0,.794]},gained:0,lost:1}},
        significance:{aOnlyCorrect:1,bOnlyCorrect:1,p:1},estimatedPrecision:{A:.286,B:null}},
      items:[{regulationId:1,title:'<script>x</script>',stratum:'extra_alert',expectedRelevant:false,status:'completed',
        A:{is_relevant:true,score:.9,band:'Yüksek',uncertain:false,rationale:'a',cached:false},
        B:{is_relevant:false,score:.1,band:'Düşük',uncertain:false,rationale:'b',cached:true}}]};`, context);
  const html = vm.runInContext('promptExperimentResults(S.pe.detail)', context);
  assert.match(html, /Fazla uyarı \(Excel&#39;de yok\)/);
  assert.match(html, /beklenen: ilgisiz/);
  assert.match(html, /%100 \(1\/1 · %95 aralık %21–%100\)/);
  assert.match(html, /A %29 · B —/);
  assert.match(html, /Fark gürültü olabilir/);
  assert.match(html, /İlgili → İlgisiz · B doğru/);
  assert.doesNotMatch(html, /<script>/);
});

test('Creating a set posts the file, approvals, name and sample sizes', async () => {
  const context = loadContext();
  vm.runInContext(`var posted = null; var renderAll = () => {}; var toast = () => {};
    api = async (method, path, options) => { posted = {method, path, query: options.query, form: options.form};
      return {data:{id:4,counts:{excel_relevant:1,excel_irrelevant:0,extra_alert:2,system_irrelevant:1}}}; };
    S.xl.file = new File(['x'], 'liste.xlsx'); S.xl.res = {summary:{}}; S.xl.selections = {2: 11};
    S.xl.setName = ' Ekim seti '; S.xl.setExtra = '25'; S.xl.setControl = '10';`, context);
  await vm.runInContext('xlCreateSet()', context);
  const posted = vm.runInContext('posted', context);
  assert.equal(posted.method, 'POST'); assert.equal(posted.path, '/evaluation/sets/from-excel');
  assert.equal(posted.form.get('name'), 'Ekim seti');
  assert.equal(posted.form.get('extra_sample'), '25'); assert.equal(posted.form.get('control_sample'), '10');
  assert.equal(posted.form.get('selections'), JSON.stringify([{ row: 2, regulation_id: 11 }]));
  assert.equal(vm.runInContext('S.pe.setId', context), 4);
  assert.equal(vm.runInContext('S.xl.setBusy', context), false);
});

test('Choosing a candidate template fills prompt B and an empty title', () => {
  const context = loadContext();
  vm.runInContext(`var renderAll = () => {};
    S.pe.defaults = {provider:'vllm',model:'m',systemPromptA:'A',settings:{},
      candidates:[{key:'v2-kapsam',title:'<i>Kapsam</i>',description:'Fazla uyarılar',systemPrompt:'B kapsam'}]};
    applyPromptCandidate('v2-kapsam');`, context);
  assert.equal(vm.runInContext('S.pe.promptB', context), 'B kapsam');
  assert.equal(vm.runInContext('S.pe.title', context), '<i>Kapsam</i>');
  const html = vm.runInContext('admPromptExperiment()', context);
  assert.match(html, /id="peCandidate"/);
  assert.match(html, /value="v2-kapsam" selected/);
  assert.match(html, /Fazla uyarılar/);
  assert.doesNotMatch(html, /<i>Kapsam/);
  vm.runInContext(`S.pe.title = 'Kendi adım'; applyPromptCandidate('v2-kapsam');`, context);
  assert.equal(vm.runInContext('S.pe.title', context), 'Kendi adım');
});

test('Creating a set rejects an empty name or out-of-range samples without calling the API', async () => {
  const context = loadContext();
  vm.runInContext(`var called = false; var renderAll = () => {}; var toast = () => {};
    api = async () => { called = true; return {data:{}}; };
    S.xl.file = new File(['x'], 'liste.xlsx'); S.xl.res = {summary:{}}; S.xl.setName = '  '; S.xl.setExtra = 40; S.xl.setControl = 35;`, context);
  await vm.runInContext('xlCreateSet()', context);
  vm.runInContext(`S.xl.setName = 'Ad'; S.xl.setExtra = 500;`, context);
  await vm.runInContext('xlCreateSet()', context);
  assert.equal(vm.runInContext('called', context), false);
});
