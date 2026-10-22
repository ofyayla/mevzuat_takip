'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadContext() {
  const context = vm.createContext({ URLSearchParams, FormData, localStorage: { getItem: () => '' } });
  for (const file of ['api.js', 'inbox.js', 'views.js']) {
    vm.runInContext(readFileSync(path.resolve(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
  }
  return context;
}

function comparisonResult() {
  const regulation = {id:7,title:'Basın Duyurusu',issuer:'BDDK',publishDate:'2026-09-18',
    status:'IRRELEVANT',isRelevant:false,relevanceScore:0.1,severity:'Orta',rationale:'Genel duyuru'};
  return {
    summary:{total:1,relevant:0,irrelevant:0,missing:1,missingByReason:{reference_review:1},
      notFound:0,review:1,unprocessed:0,matched:0,matchRate:0,recall:0,foundRate:0},
    precision:{period:{from:'2026-09-15',to:'2026-09-18'},shown:1,inList:0,extra:1,precision:0},
    severityAgreement:{n:0,exact:null},
    byIssuer:[{issuer:'BDDK',total:1,relevant:0,irrelevant:0,missing:1}],
    thresholds:{title:85,text:65},
    items:[{no:6,ref:'MVZ-6',title:'Duyuru',issuer:'BDDK',issuerCode:'BDDK',severity:'Yüksek',
      category:'reference_review',categoryLabel:'Belge kimliği: inceleme gerekli',group:'missing',
      method:null,methodLabel:null,score:null,rawDocumentId:null,reference:{number:'65/1779',status:'ambiguous'},
      matchState:'review',candidates:[{regulation,rawDocumentId:70,evidence:'reference',
        selection:{category:'below_threshold',categoryLabel:'İlgisiz (YZ eledi)',group:'irrelevant',
          matchState:'matched',rawDocumentId:70,regulation}}],selectedRegulationId:null,regulation:null}],
    extra:[regulation],
  };
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

test('Candidate comparison escapes text and exposes explicit acceptance without changing AI labels', () => {
  const context = loadContext();
  const html = vm.runInContext(`xlComparison({no:6,title:'Excel <script>',textExcerpt:'<img onerror=alert(1)>',
    candidates:[{regulation:{id:7,title:'Aday',issuer:'BDDK',publishDate:'2026-09-18',status:'IRRELEVANT'},
      evidence:'similarity',textExcerpt:'Karar No: 11572'}]})`, context);
  assert.match(html, /data-act="xl-select" data-no="6" data-id="7"/);
  assert.match(html, /kimliği doğrulanamadı/);
  assert.match(html, /IRRELEVANT/);
  assert.doesNotMatch(html, /<script>|<img/);
});

test('Candidate approval and undo update the cached result; only export sends the selection', async () => {
  const context = loadContext();
  const payload = JSON.stringify(JSON.stringify(comparisonResult()));
  vm.runInContext(`
    var sent = [];
    var renderAll = () => {};
    var toast = () => {};
    var saveBlob = () => {};
    S.xl.file = 'test.xlsx';
    api = async (_method, _url, options) => {
      sent.push({selections:JSON.parse(options.form.get('selections')),format:options.query.format || 'json'});
      if (options.query.format === 'xlsx') return {blob:{},res:{headers:{get:()=>null}}};
      return {data:JSON.parse(${payload})};
    };
  `, context);
  await vm.runInContext('xlRun()', context);
  assert.equal(vm.runInContext('sent.length', context), 1);
  assert.equal(vm.runInContext('JSON.stringify(sent[0].selections)', context), '[]');

  await vm.runInContext("xlSelect(6,'7')", context);
  assert.equal(vm.runInContext('S.xl.selections[6]', context), 7);
  assert.equal(vm.runInContext('sent.length', context), 1);
  assert.equal(vm.runInContext('S.xl.res.items[0].method', context), 'manual_selection');
  assert.equal(vm.runInContext('S.xl.res.items[0].group', context), 'irrelevant');
  assert.equal(vm.runInContext('S.xl.res.summary.review', context), 0);
  assert.equal(vm.runInContext('S.xl.res.summary.matched', context), 1);
  assert.equal(vm.runInContext('S.xl.res.precision.extra', context), 0);
  assert.equal(vm.runInContext('S.xl.res.byIssuer[0].irrelevant', context), 1);
  assert.equal(vm.runInContext('S.xl.res.severityAgreement.exact', context), 0);

  await vm.runInContext('xlExport()', context);
  assert.equal(vm.runInContext('JSON.stringify(sent[1].selections)', context), '[{"row":6,"regulation_id":7}]');
  assert.equal(vm.runInContext('sent[1].format', context), 'xlsx');

  await vm.runInContext('xlRun()', context);
  assert.equal(vm.runInContext('JSON.stringify(sent[2].selections)', context), '[]');
  assert.equal(vm.runInContext('S.xl.res.items[0].selectedRegulationId', context), 7);

  await vm.runInContext('xlSelect(6,null)', context);
  assert.equal(vm.runInContext('sent.length', context), 3);
  assert.equal(vm.runInContext('JSON.stringify(S.xl.selections)', context), '{}');
  assert.equal(vm.runInContext('S.xl.res.items[0].group', context), 'missing');
  assert.equal(vm.runInContext('S.xl.res.precision.extra', context), 1);

  vm.runInContext("S.xl.selections = {6:7}; xlSetFile('different.xlsx')", context);
  assert.equal(vm.runInContext('JSON.stringify(S.xl.selections)', context), '{}');
  assert.equal(vm.runInContext('S.xl.res', context), null);
  assert.equal(vm.runInContext('S.xl.baseRes', context), null);
});

test('A selection absent from the latest candidate list cannot be shown as accepted', () => {
  const context = loadContext();
  const payload = JSON.stringify(JSON.stringify(comparisonResult()));
  vm.runInContext(`
    var renderAll = () => {};
    var toast = () => {};
    S.xl.file = 'test.xlsx'; S.xl.selections = {6:7}; S.xl.res = {old:true};
    api = async () => ({data:JSON.parse(${payload})});
  `, context);
  vm.runInContext('S.xl.baseRes = {items:[{no:6,candidates:[]}]}', context);
  vm.runInContext('xlSelect(6,7)', context);
  assert.equal(vm.runInContext('S.xl.res.old', context), true);
  assert.equal(vm.runInContext('S.xl.selections[6]', context), 7);
  assert.match(vm.runInContext('S.xl.err', context), /Aday yeni karşılaştırmada bulunamadı/);
  assert.equal(vm.runInContext('S.xl.running', context), false);
});

test('An API without candidate selection outcomes reports a version mismatch', () => {
  const context = loadContext();
  vm.runInContext(`
    var renderAll = () => {};
    var toast = () => {};
    S.xl.baseRes = {items:[{no:6,candidates:[{regulation:{id:7}}]}]};
    S.xl.selections = {};
  `, context);
  vm.runInContext('xlSelect(6,7)', context);
  assert.match(vm.runInContext('S.xl.err', context), /sürümleri uyumsuz/);
  assert.equal(vm.runInContext('JSON.stringify(S.xl.selections)', context), '{}');
});

test('An outdated API or stale approval cannot appear successful after comparison', async () => {
  const context = loadContext();
  vm.runInContext(`
    var renderAll = () => {};
    var toast = () => {};
    S.xl.file = 'test.xlsx'; S.xl.res = {old:true}; S.xl.selections = {6:7};
    api = async () => ({data:{summary:{total:1}}});
  `, context);
  await vm.runInContext('xlRun()', context);
  assert.match(vm.runInContext('S.xl.err', context), /sürümleri uyumsuz/);
  vm.runInContext("api = async () => ({data:{summary:{total:1,notFound:0,review:1},items:[],precision:{shown:0},extra:[]}})", context);
  await vm.runInContext('xlRun()', context);
  assert.match(vm.runInContext('S.xl.err', context), /Aday yeni karşılaştırmada bulunamadı/);
  assert.equal(vm.runInContext('S.xl.res.old', context), true);
  assert.equal(vm.runInContext('S.xl.selections[6]', context), 7);
});

test('Review, unmatched and unprocessed tabs do not collapse into a single missing count', () => {
  const context = loadContext();
  vm.runInContext(`S.xl.res = {
    summary:{total:48,relevant:26,irrelevant:11,missing:11,notFound:3,review:7,unprocessed:1,recall:.54,matchRate:.79},
    precision:{shown:1,extra:0,precision:1},severityAgreement:{n:0},items:[]
  }`, context);
  const html = vm.runInContext('admExcel()', context);
  assert.match(html, /Eşleşme bulunamadı/);
  assert.match(html, /Aday incelemesi/);
  assert.match(html, /Bulundu; YZ sonucu yok/);
  assert.doesNotMatch(html, /Kesin eşleşme yok/);
  assert.match(html, /Sayfa yenilenince silinir/);
  const table = vm.runInContext(`xlTable({items:[{no:4,title:'İlk tarama',category:'baseline',
    matchState:'unprocessed',group:'missing',regulation:{id:3186,title:'Karar'}}]})`, context);
  assert.match(table, /Bulundu; YZ sonucu yok/);
  assert.doesNotMatch(table, /Bulunamadı/);
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
