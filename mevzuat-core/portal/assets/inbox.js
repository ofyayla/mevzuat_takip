'use strict';
/* ============================================================================================================
   Gelen kutusu: durum, sunucudan liste/KPI yükleme ve liste/ayrıntı çizimi.
   ============================================================================================================ */
const S = {
  view: 'gelen', adminTab: 'genel', queue: 'yeni', q: '', sort: 'oncelik', sortDir: 'desc', page: 1, ps: 25,
  f: { src: new Set(), sev: new Set(), eff: new Set(), unit: new Set(), needUnit: false },
  selected: new Set(), openId: null, focusId: null, dTab: 'ozet', cite: null, rejecting: false, rejReason: 'not_banking_related',
  list: { ids: [], total: 0, loading: true, err: null }, kpi: null, busy: false,
  pop: null, edit: null, meta: null,
  arsivF: 'onay', arsivQ: '', arsivPage: 1, ar: { ids: [], total: 0, loading: true, err: null }, arStats: null,
};
const QUEUES = {
  yeni: { label: 'Yeni', note: 'Son 7 günde tespit edilen, YZ işlemi tamamlanmış kayıtlar.' },
  birikim: { label: 'Birikim', note: 'Baseline sonrası yayımlanan ve 7 günden uzun süredir bekleyen kayıtlar. Benzer kayıtları seçip toplu onaylayın ya da reddedin.' },
  tum: { label: 'Tümü', note: 'Karar bekleyen tüm kayıtlar.' },
  hazirlaniyor: { label: 'YZ hazırlıyor', note: 'Özeti ya da birim önerisi henüz hazır olmayan kayıtlar. Hazır olunca Yeni kuyruğuna düşer; bu sırada karar verilemez.' },
  ilgisiz: { label: 'İlgisiz örneklemi', note: 'YZ\'nin eşiğe yakın skorla ilgisiz bulduğu kayıtlar. Kaçırılan düzenleme riskine karşı her birinde YZ kararını onaylayın ya da düzeltin.' },
};
const DECISION_Q = ['yeni', 'birikim', 'tum'];
const SORTS = { oncelik: { sort: 'priority', direction: 'desc' }, tespit: { sort: 'detected_at', direction: 'desc' },
  yururluk: { sort: 'effective_date', direction: 'asc' }, onem: { sort: 'severity', direction: 'desc' } };
const EFF_OPTS = [['7', '7 gün içinde'], ['30', '30 gün içinde'], ['in', 'Yürürlükte'], ['none', 'Belirtilmemiş']];
const EFF_API = { 7: '7', 30: '30', in: 'in_force', none: 'none' };

/* ---------- türetilen değerler */
const curSev = r => r.sev;
const needsUnit = r => r.sel.length === 0;
function counts() {
  const q = (Boot.stats && Boot.stats.queues) || {};
  return { yeni: q.yeni || 0, birikim: q.birikim || 0, tum: q.tum || 0, hazirlaniyor: q.hazirlaniyor || 0, ilgisiz: q.ilgisizOrneklem || 0, bekleyen: q.tum || 0 };
}
const currentList = () => S.list.ids.map(byId).filter(Boolean);
const effDays = r => r.eff instanceof Date ? dd(r.eff, NOW) : null;
const anyFilter = () => S.q || S.f.src.size || S.f.sev.size || S.f.eff.size || S.f.unit.size || S.f.needUnit;
const isAdmin = () => !!(Boot.me && Boot.me.isAdmin);
const canDecide = () => !!(Boot.me && Boot.me.canDecide);

/* ---------- sunucudan yükleme */
function listParams(extra = {}) {
  const hz = S.queue === 'hazirlaniyor';
  const so = hz ? { sort: 'detected_at', direction: 'asc' } : { sort: SORTS[S.sort].sort, direction: S.sortDir };
  return { queue: S.queue, page: S.page, page_size: S.ps, ...so, q: S.q.trim(), source: [...S.f.src], severity: [...S.f.sev],
    effective: [...S.f.eff].map(v => EFF_API[v]), unit: [...S.f.unit], needs_unit: S.f.needUnit, ...extra };
}
let listSeq = 0;
async function loadList() {
  const seq = ++listSeq;
  S.list.loading = true; S.list.err = null;
  paintList();
  try {
    let d, items;
    if (S.queue === 'ilgisiz') { d = await GET('/regulations/irrelevant-sample', { page: S.page, page_size: S.ps }); items = d.items.map(upsertIrr); }
    else { d = await GET('/regulations', listParams()); items = d.items.map(it => upsert(it)); }
    if (seq !== listSeq) return;
    S.list = { ids: items.map(r => r.id), total: d.total, loading: false, err: null };
    const pages = Math.max(1, Math.ceil(d.total / S.ps));
    if (S.page > pages) { S.page = pages; return loadList(); }
  } catch (e) {
    if (seq !== listSeq) return;
    S.list = { ids: [], total: 0, loading: false, err: errMsg(e) };
  }
  NOW = new Date();
  paintList();
}
/** KPI kutuları kuyruğun tamamını sayar (kullanıcı süzgeçlerinden bağımsız); yalnızca sayı için page_size=1. */
async function loadKpi() {
  if (!DECISION_Q.includes(S.queue)) { S.kpi = null; return; }
  const q = S.queue;
  try {
    const [e30, e7, need] = await Promise.all([GET('/regulations', { queue: q, effective: '30', page_size: 1 }), GET('/regulations', { queue: q, effective: '7', page_size: 1 }),
      GET('/regulations', { queue: q, needs_unit: true, page_size: 1 })]);
    if (q === S.queue) S.kpi = { n30: e30.total, n7: e7.total, need: need.total };
  } catch (_) { S.kpi = null; }
}
async function reloadAll() {
  await Promise.all([loadList(), loadStats().catch(() => {}), loadKpi()]);
  if (S.view === 'gelen') paintAll();
}
function paintList() {
  if (S.view !== 'gelen' || !$('#listRegion')) return;
  $('#listRegion').innerHTML = listRegion();
}
function paintAll() { if (S.view === 'gelen' && $('#listRegion')) refreshInbox(); }

/* ---------- küçük parçalar */
const sevPill = s => s ? `<span class="sev sev-${SEVK[s]}">${s}</span>` : '<span class="muted">—</span>';
const srcAbbr = k => `<abbr class="src" title="${esc(srcOf(k).n)}">${esc(srcOf(k).a)}</abbr>`;
function effCell(r, compact) {
  const d = effDays(r);
  if (d !== null) {
    if (d < 0) return `<div class="eff"><span>${fDs(r.eff)}</span><span class="eff-in">Yürürlükte</span></div>`;
    const cls = d <= 7 ? 'tag-crit' : d <= 30 ? 'tag-warn' : 'tag-plain';
    return `<div class="eff"><span>${compact ? fDs(r.eff) : fD(r.eff)}</span><span class="tag ${cls}">${d === 0 ? 'bugün' : d + ' gün kaldı'}</span></div>`;
  }
  if (r.eff === 'yayim') return `<div class="eff"><span>Yayımında</span><span class="eff-in">Yürürlükte</span></div>`;
  return `<div class="eff muted">Belirtilmemiş</div>`;
}
function unitCell(r) {
  const weak = r.units[0];
  if (!r.sel.length) return `<span class="need">${ic('alert')}Birim atanmalı</span>${weak ? `<div class="u-s">Zayıf öneri: ${esc(trunc(weak.n, 26))}</div>` : ''}`;
  const u = r.units.find(x => x.code === r.sel[0]);
  const names = r.sel.map(unitName2(r));
  return `<div class="unit"><span class="u-n" title="${esc(names.join(', '))}">${esc(names[0])}</span><span class="u-s">${u && u.s != null && u.origin !== 'manual' ? `Uyum ${pct(u.s)}` : 'Elle seçildi'}${r.sel.length > 1 ? ` · +${r.sel.length - 1} birim` : ''}</span></div>`;
}
const unitName2 = r => c => (r.units.find(x => x.code === c) || {}).n || unitName(c);

/* ---------- gelen kutusu */
function viewGelen() {
  const dec_ = DECISION_Q.includes(S.queue);
  return `<div class="inbox gut ${S.openId ? 'has-detail' : ''}" id="inbox">
    <section class="list-pane" aria-label="Kayıt listesi">
      <div class="ph"><div><h1>Gelen kutusu</h1><p class="lede">YZ'nin tespit ettiği düzenlemeleri inceleyin; önerilen birimi onaylayın ya da kaydı reddedin.</p></div>
        <div class="ph-act"><button class="btn" data-act="export">${ic('download')}Excel'e aktar</button><button class="btn btn-ghost" data-act="shortcuts">${ic('kbd')}Kısayollar <span class="kbd">?</span></button></div></div>
      <div id="kpis">${dec_ ? kpis() : ''}</div>
      <div id="qtabs">${qtabs()}</div>
      <div class="fbar"><label class="search"><span class="sr">Ara</span>${ic('search')}<input id="q" type="search" placeholder="Başlık veya özet içinde ara" value="${esc(S.q)}" autocomplete="off"><span class="kbd">/</span></label><div class="fbtns" id="fbtns">${fbtns()}</div></div>
      <div id="listRegion">${listRegion()}</div>
    </section>
    <button class="scrim" data-act="close" aria-label="Ayrıntıyı kapat" tabindex="-1"></button>
    <aside class="detail-pane" id="detail" aria-label="Kayıt ayrıntısı" ${S.openId ? '' : 'hidden'}>${S.openId ? detailHtml() : ''}</aside>
  </div>`;
}
function kpis() {
  const st = Boot.stats || {}; const c = counts(); const k = S.kpi || {};
  const d = (st.severityDistribution || {})[S.queue] || {};
  const dist = SEVS.map(s => d[s] || 0); const tot = Math.max(1, dist.reduce((a, b) => a + b, 0) + (d[''] || 0));
  const pr = st.preparing || {};
  const sevOn = S.f.sev.size === 2 && S.f.sev.has('Kritik') && S.f.sev.has('Yüksek');
  const effOn = S.f.eff.size === 1 && S.f.eff.has('30');
  const n30 = k.n30, n7 = k.n7;
  return `<div class="kpis">
    <div class="kpi"><span class="kpi-l">${QUEUES[S.queue].label} kuyruğu</span><span class="kpi-v num">${N((st.queues || {})[S.queue])}</span><span class="kpi-s">Toplam karar bekleyen ${N(c.bekleyen)}</span></div>
    <button class="kpi" data-act="kpi-sev" aria-pressed="${sevOn}" title="Kritik ve Yüksek önemlileri göster"><span class="kpi-l">Kritik ve yüksek</span><span class="kpi-v num">${N(dist[0] + dist[1])}</span>
      <span class="dist" aria-label="Kritik ${dist[0]}, Yüksek ${dist[1]}, Orta ${dist[2]}, Düşük ${dist[3]}">${dist.map((n, i) => n ? `<i class="d-${'kyod'[i]}" style="width:${n / tot * 100}%" title="${SEVS[i]}: ${n}"></i>` : '').join('')}</span></button>
    <button class="kpi" data-act="kpi-eff" aria-pressed="${effOn}"><span class="kpi-l">Yürürlüğe 30 gün içinde</span><span class="kpi-v num ${n30 ? 'warn' : ''}">${n30 == null ? '…' : N(n30)}</span><span class="kpi-s">${n7 == null ? '' : n7 ? `${n7} tanesi 7 gün içinde` : '7 gün içinde yok'}</span></button>
    <button class="kpi" data-act="kpi-need" aria-pressed="${S.f.needUnit}"><span class="kpi-l">Birim atanmalı</span><span class="kpi-v num">${k.need == null ? '…' : N(k.need)}</span><span class="kpi-s">YZ güvenli öneri yapamadı</span></button>
    <button class="kpi" data-act="queue" data-q="hazirlaniyor"><span class="kpi-l">YZ hazırlıyor</span><span class="kpi-v num">${N(pr.count)}</span><span class="kpi-s ${pr.failed ? 'h-crit' : ''}">${N(pr.failed)} hata · en eski ${N(pr.oldestDays)} gün</span></button>
  </div>`;
}
function qtabs() {
  const c = counts(); const t = k => `<button class="tab" role="tab" data-act="queue" data-q="${k}" aria-selected="${S.queue === k}">${QUEUES[k].label}<span class="cnt">${N(c[k])}</span></button>`;
  return `<div class="tabs qtabs" role="tablist" aria-label="Kuyruklar">${t('yeni')}${t('birikim')}${t('tum')}<span class="tab-sep" aria-hidden="true"></span>${t('hazirlaniyor')}${t('ilgisiz')}</div>`;
}
function fbtns() {
  const b = (k, l) => { const n = S.f[k].size; return `<button class="btn fbtn ${n ? 'on' : ''}" data-act="pop-filter" data-f="${k}" aria-expanded="${S.pop && S.pop.f === k}">${l}${n ? ` <b>${n}</b>` : ''}${ic('chev-d')}</button>`; };
  if (S.queue === 'ilgisiz') return '';
  const dec_ = DECISION_Q.includes(S.queue);
  return b('src', 'Kaynak') + (dec_ ? b('sev', 'Önem') + b('eff', 'Yürürlük') + b('unit', 'Birim') +
    `<button class="btn fbtn ${S.f.needUnit ? 'on' : ''}" data-act="kpi-need" aria-pressed="${S.f.needUnit}">${ic('alert')}Birim atanmalı</button>` : '');
}
function chipsRow(total, start, n) {
  const chips = [];
  if (S.q) chips.push(['q', '', `“${S.q}”`]);
  S.f.src.forEach(v => chips.push(['src', v, srcOf(v).a]));
  S.f.sev.forEach(v => chips.push(['sev', v, v]));
  S.f.eff.forEach(v => chips.push(['eff', v, 'Yürürlük: ' + EFF_OPTS.find(o => o[0] === v)[1]]));
  S.f.unit.forEach(v => chips.push(['unit', v, trunc(unitName(v), 30)]));
  if (S.f.needUnit) chips.push(['needUnit', '', 'Birim atanmalı']);
  const sortable = DECISION_Q.includes(S.queue);
  return `<div class="chiprow">${chips.map(c => `<span class="chip">${esc(c[2])}<button data-act="unchip" data-k="${c[0]}" data-v="${esc(c[1])}" aria-label="${esc(c[2])} filtresini kaldır">${ic('x')}</button></span>`).join('')}
    ${chips.length ? '<button class="linkbtn" data-act="clear-filters">Filtreleri temizle</button>' : ''}
    <div class="chiprow-r"><span class="num">${total ? `${N(start + 1)}–${N(start + n)} / ${N(total)}` : '0 kayıt'}</span>
    ${sortable ? `<label>Sırala <select class="select" id="sort" aria-label="Sıralama">${[['oncelik', 'Öncelik (önem × yürürlük)'], ['tespit', 'Tespit tarihi'], ['yururluk', 'Yürürlük tarihi'], ['onem', 'Önem']].map(o => `<option value="${o[0]}" ${S.sort === o[0] ? 'selected' : ''}>${o[1]}</option>`).join('')}</select></label>
    <label>Yön <select class="select" id="sortDir" aria-label="Sıralama yönü">${[['asc', 'Küçükten büyüğe'], ['desc', 'Büyükten küçüğe']].map(o => `<option value="${o[0]}" ${S.sortDir === o[0] ? 'selected' : ''}>${o[1]}</option>`).join('')}</select></label>` : ''}</div></div>`;
}
function listRegion() {
  const { total, loading, err } = S.list; const rows = currentList();
  const start = (S.page - 1) * S.ps; const pages = Math.max(1, Math.ceil(total / S.ps));
  if (err) return `${chipsRow(0, 0, 0)}<div class="empty card"><b>Liste yüklenemedi</b><span>${esc(err)}</span><button class="btn" data-act="reload">Yeniden dene</button></div>`;
  if (loading && !rows.length) return `${chipsRow(0, 0, 0)}<div class="empty card" aria-busy="true"><b>Yükleniyor…</b></div>`;
  return `${chipsRow(total, start, rows.length)}<p class="qnote">${esc(QUEUES[S.queue].note)}</p>
    ${total ? `<div class="tbl-wrap ${loading ? 'is-loading' : ''}" ${loading ? 'aria-busy="true"' : ''}>${table(rows)}</div>${pager(total, start, rows.length, pages)}` : emptyState()}${bulkbar()}`;
}
function emptyState() {
  const c = counts();
  if (anyFilter()) return `<div class="empty card"><b>Bu filtrelerle kayıt yok</b><span>Filtreleri gevşetin ya da temizleyin.</span><button class="btn" data-act="clear-filters">Filtreleri temizle</button></div>`;
  if (S.queue === 'yeni') return `<div class="empty card">${ic('check')}<b>Yeni kuyruk boş</b><span>Birikimde ${N(c.birikim)} kayıt bekliyor.</span><button class="btn" data-act="queue" data-q="birikim">Birikime geç</button></div>`;
  return `<div class="empty card"><b>Bu kuyrukta kayıt yok</b></div>`;
}
function sortTh(k, l, cls) {
  const on = S.sort === k;
  const next = on ? (S.sortDir === 'asc' ? 'desc' : 'asc') : SORTS[k].direction;
  return `<th class="${cls}" aria-sort="${on ? (S.sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}"><button class="th-sort ${on ? 'on' : ''}" data-act="sort" data-s="${k}" aria-label="${l} sütununa göre ${next === 'asc' ? 'küçükten büyüğe' : 'büyükten küçüğe'} sırala">${l}${on ? ic(S.sortDir === 'asc' ? 'chev-u' : 'chev-d') : ''}</button></th>`;
}
function table(rows) {
  const q = S.queue; const pageIds = rows.map(r => r.id);
  const allSel = pageIds.length && pageIds.every(id => S.selected.has(id));
  const cb = r => `<td class="c-cb"><input type="checkbox" data-act="sel" data-id="${r.id}" ${S.selected.has(r.id) ? 'checked' : ''} aria-label="Seç: ${esc(trunc(r.title, 60))}"></td>`;
  const cls = r => `${r.unread ? 'unread' : ''} ${S.openId === r.id ? 'is-open' : ''} ${S.focusId === r.id ? 'is-focus' : ''} ${curSev(r) === 'Kritik' ? 'sv-k' : curSev(r) === 'Yüksek' ? 'sv-y' : ''}`;
  const meta = (r, extra = '') => `<div class="meta">${srcAbbr(r.src)}${r.type && r.type !== '—' ? `<span>${esc(r.type)}</span>` : ''}${r.pub ? `<span>Yayım ${fDs(r.pub)}</span>` : ''}<span>Tespit ${rel(r.det)}</span>${extra}</div>`;
  if (q === 'hazirlaniyor') return `<table class="tbl"><thead><tr><th class="c-main">Düzenleme</th><th>YZ aşaması</th><th class="c-eff">Bekleme</th><th class="c-sev">Önem</th><th class="c-go"></th></tr></thead><tbody>
    ${rows.map(r => { const st = r.stage || {}; const w = dd(NOW, pdt(st.waitingSince) || r.det); const err = st.stage === 'retry' || st.stage === 'failed'; const n = { summary: 2, unit_match: 3, retry: 2, failed: 2 }[st.stage] || 2;
      return `<tr class="${cls(r)}" data-act="open" data-id="${r.id}"><td class="c-main"><div class="ttl">${esc(r.title)}</div>${meta(r)}</td>
      <td><span class="stage ${err ? 'h-crit' : ''}"><span class="steps">${[1, 2, 3, 4].map(i => `<i class="${i < n ? 'on' : i === n && err ? 'err' : ''}"></i>`).join('')}</span>${STAGE_TXT[st.stage] || esc(r.analysis || '—')}</span></td>
      <td class="c-eff"><span class="tag ${w > 3 ? 'tag-warn' : 'tag-plain'}">${w <= 0 ? 'bugün' : w + ' gün'}</span></td><td class="c-sev">${sevPill(r.sev)}</td><td class="c-go">${ic('chev-r')}</td></tr>`; }).join('')}</tbody></table>`;
  if (q === 'ilgisiz') return `<table class="tbl"><thead><tr><th class="c-main">Düzenleme</th><th class="c-sev">İlgililik skoru</th><th class="c-unit">YZ gerekçesi</th><th style="text-align:right">Karar</th></tr></thead><tbody>
    ${rows.map(r => `<tr class="${cls(r)}" data-act="open" data-id="${r.id}"><td class="c-main"><div class="ttl">${esc(r.title)}</div>${meta(r)}</td>
      <td class="c-sev"><span class="tag ${r.relScore != null && r.threshold != null && r.threshold - r.relScore < .05 ? 'tag-warn' : 'tag-plain'}">${dec(r.relScore)} / eşik ${dec(r.threshold)}</span></td><td class="c-unit"><span class="u-s">${esc(trunc(r.why || 'Gerekçe kaydedilmemiş.', 180))}</span></td>
      <td><div class="row-acts"><button class="btn btn-sm" data-act="irr-ok" data-id="${r.id}">İlgisiz, doğru</button><button class="btn btn-sm" data-act="irr-back" data-id="${r.id}">İlgili</button></div></td></tr>`).join('')}</tbody></table>`;
  return `<table class="tbl"><thead><tr><th class="c-cb"><input type="checkbox" data-act="selall" ${allSel ? 'checked' : ''} aria-label="Bu sayfadaki tüm kayıtları seç"></th><th class="c-main">Düzenleme</th>${sortTh('yururluk', 'Yürürlük', 'c-eff')}${sortTh('onem', 'Önem', 'c-sev')}<th class="c-unit">Önerilen birim</th><th class="c-go"></th></tr></thead><tbody>
    ${rows.map(r => `<tr class="${cls(r)}" data-act="open" data-id="${r.id}">${cb(r)}<td class="c-main"><div class="ttl">${esc(r.title)}</div>${meta(r,
      (r.dup ? '<span class="tag tag-warn">Olası mükerrer</span>' : '') + (r.draftDue ? `<span class="tag tag-brand">Görüş son günü ${fDs(r.draftDue)}</span>` : '') + (r.conflict ? '<span class="tag tag-warn">Tarih çelişkisi</span>' : '') +
      `<span class="m-only">${sevPill(curSev(r))}</span>` + (needsUnit(r) ? `<span class="m-only m-unit need">${ic('alert')}Birim atanmalı</span>` : ''))}</td>
      <td class="c-eff">${effCell(r, true)}</td><td class="c-sev">${sevPill(curSev(r))}</td><td class="c-unit">${unitCell(r)}</td><td class="c-go">${ic('chev-r')}</td></tr>`).join('')}</tbody></table>`;
}
function pager(total, start, n, pages) {
  const p = S.page; const nums = [];
  for (let i = 1; i <= pages; i++) if (i === 1 || i === pages || Math.abs(i - p) <= 1) nums.push(i); else if (nums[nums.length - 1] !== '…') nums.push('…');
  return `<div class="pager"><span class="num">Gösterilen ${N(start + 1)}–${N(start + n)} / ${N(total)}</span>
    <label>Sayfa başına <select class="select" id="ps">${[25, 50, 100].map(v => `<option ${S.ps === v ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
    <div class="pg"><button class="btn btn-sm" data-act="page" data-p="${p - 1}" ${p <= 1 ? 'disabled' : ''} aria-label="Önceki sayfa">${ic('chev-l')}</button>
    ${nums.map(i => i === '…' ? '<span class="muted">…</span>' : `<button class="btn btn-sm num" data-act="page" data-p="${i}" ${i === p ? 'aria-current="page"' : ''}>${i}</button>`).join('')}
    <button class="btn btn-sm" data-act="page" data-p="${p + 1}" ${p >= pages ? 'disabled' : ''} aria-label="Sonraki sayfa">${ic('chev-r')}</button></div></div>`;
}
function bulkbar() {
  const n = S.selected.size; if (!n || !DECISION_Q.includes(S.queue) || !canDecide()) return '';
  const noUnit = [...S.selected].filter(id => { const r = byId(id); return r && needsUnit(r); }).length;
  return `<div class="bulkbar" role="region" aria-label="Toplu işlemler"><b>${n} kayıt seçildi</b>
    <button class="btn btn-sm btn-primary" data-act="bulk-approve">${ic('check')}Onayla…</button>
    <button class="btn btn-sm" data-act="bulk-rej">Reddet…</button><button class="btn btn-sm" data-act="bulk-read">Okundu işaretle</button>
    <button class="btn btn-sm btn-ghost" data-act="bulk-clear">Seçimi kaldır</button>${noUnit ? `<span style="font-size:12px;opacity:.75">${noUnit} tanesinin birimi yok</span>` : ''}${n > 100 ? '<span style="font-size:12px;opacity:.75">Toplu işlem en fazla 100 kayıt alır</span>' : ''}</div>`;
}

/* ---------- ayrıntı */
const ARSIV_Q = ['Onaylandı', 'Reddedildi'];
const isArchived = r => r.kind === 'irr' ? S.view === 'arsiv' : ARSIV_Q.includes(r.status);
const navList = () => S.view === 'arsiv' ? S.ar.ids.map(byId).filter(Boolean) : currentList();
const queueLabel = r => r.kind === 'irr' ? (S.view === 'arsiv' ? 'Arşiv · YZ\'nin elediği' : QUEUES.ilgisiz.label) : r.status === 'Onaylandı' ? 'Arşiv · Onaylandı' : r.status === 'Reddedildi' ? 'Arşiv · Reddedildi' : r.stage ? QUEUES.hazirlaniyor.label : (QUEUES[S.queue] || {}).label || 'Kayıt';
const isPending = r => r.kind === 'reg' && r.status === 'Bekliyor' && !r.stage;

function detailHtml() {
  const r = byId(S.openId); if (!r) return '';
  const list = navList(); const idx = list.findIndex(x => x.id === r.id); const inL = idx >= 0;
  const pos = S.view === 'arsiv' ? (S.arsivPage - 1) * 25 + idx + 1 : (S.page - 1) * S.ps + idx + 1;
  const total = S.view === 'arsiv' ? S.ar.total : S.list.total;
  const sev = curSev(r);
  return `<div class="d-head"><div class="d-pos"><span>${esc(queueLabel(r))}</span>${inL ? `<b class="num">${N(pos)} / ${N(total)}</b>` : ''}</div>
    <div class="d-nav"><button class="btn btn-ghost icon-btn" data-act="prev" ${!inL || idx <= 0 || S.meta ? 'disabled' : ''} title="Önceki (K)" aria-label="Önceki kayıt">${ic('chev-u')}</button>
    <button class="btn btn-ghost icon-btn" data-act="next" ${!inL || idx >= list.length - 1 || S.meta ? 'disabled' : ''} title="Sonraki (J)" aria-label="Sonraki kayıt">${ic('chev-d')}</button>
    <button class="btn btn-ghost icon-btn" data-act="close" title="Kapat (Esc)" aria-label="Kapat">${ic('x')}</button></div></div>
  <div class="d-scroll" id="dScroll">
    ${metaOn(r) ? `<div class="d-top"><div class="d-chips"><span class="d-src">${esc(srcOf(r.src).n)}</span><span class="mono muted">#${r.num}</span></div><h2 class="d-title">Bilgileri düzenle</h2></div>${metaForm(r)}` : `
    <div class="d-top"><div class="d-chips">${sev ? sevPill(sev) : ''}${r.type && r.type !== '—' ? `<span class="tag tag-plain">${esc(r.type)}</span>` : ''}<span class="d-src">${esc(srcOf(r.src).n)}</span>
      ${r.kind === 'reg' && canDecide() ? `<button class="btn btn-sm btn-ghost" style="margin-left:auto" data-act="meta-start" ${canMeta(r) && !S.edit ? '' : `disabled title="${S.edit ? 'Önce karar düzenlemesini bitirin' : 'YZ işlemi sürerken düzenlenemez'}"`}>${ic('edit')}Bilgileri düzenle</button>` : ''}</div>
      <h2 class="d-title">${esc(r.title)}</h2>
      <div class="d-meta">${r.pub ? `<span>Yayım ${fD(r.pub)}</span>` : ''}<span>Tespit ${rel(r.det)}</span><span class="mono">#${r.num}</span>${edTag(r, 'title')}${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">Orijinal belge${ic('ext')}</a>` : ''}</div></div>
    ${banners(r)}
    <div class="tabs d-tabs" role="tablist">${[['ozet', 'Özet'], ['belge', 'Belge'], ['gecmis', 'Geçmiş']].map(t => `<button class="tab" role="tab" data-act="dtab" data-t="${t[0]}" aria-selected="${S.dTab === t[0]}">${t[1]}</button>`).join('')}</div>
    <div class="d-body">${S.dTab === 'belge' ? docTab(r) : S.dTab === 'gecmis' ? histTab(r) : summaryTab(r)}</div>`}
  </div>${actionsHtml(r)}`;
}
const rejReasonOf = r => { const ev = [...(r.trail || [])].reverse().find(e => e.rejectionReason); return ev ? ev.rejectionReason : null; };
const approvedChanged = r => !!(r.aiUnits && r.aiUnits.length && !sameSet(r.units.map(u => u.code), r.aiUnits.map(u => u.code)));
function banners(r) {
  const b = [];
  if (r.kind === 'reg' && r.stage) {
    const st = r.stage; const w = dd(NOW, pdt(st.waitingSince) || r.det);
    if (st.stage === 'failed') b.push(`<div class="banner banner-crit">${ic('alert')}<div><b>Deneme hakkı tükendi (${st.attempts}/${st.maxAttempts})${st.error ? ': ' + esc(st.error) : ''}</b><p>Kayıt otomatik olarak yeniden denenmeyecek. ${isAdmin() ? 'Yeniden deneyebilirsiniz.' : 'Yönetici yeniden denemelidir.'}</p>${isAdmin() ? `<div class="b-act"><button class="btn btn-sm" data-act="retry-one" data-id="${r.id}">${ic('retry')}Yeniden dene</button></div>` : ''}</div></div>`);
    else b.push(`<div class="banner banner-warn">${ic('lock')}<div><b>YZ işlemi sürüyor: ${STAGE_TXT[st.stage] || esc(r.analysis)}</b><p>Kayıt ${w > 0 ? w + ' gündür' : 'bugünden beri'} bu adımda${st.stage === 'retry' ? ` (deneme ${st.attempts}/${st.maxAttempts}${st.error ? ', son hata: ' + esc(st.error) : ''})` : ''}. Özet ve birim önerisi hazır olunca Yeni kuyruğuna düşer; o zamana kadar karar verilemez.</p></div></div>`);
  }
  if (r.kind === 'irr') {
    b.push(`<div class="banner banner-info">${ic('info')}<div><b>YZ bu kaydı ilgisiz buldu (ilgililik skoru ${dec(r.relScore)}${r.threshold != null ? `, eşik ${dec(r.threshold)}` : ''})</b><p>${r.review ? `İncelendi: ${r.review.verdict === 'confirm' ? 'ilgisiz olarak doğrulandı' : 'ilgili sayıldı'} · ${esc(r.review.by || '')}${r.review.at ? ' · ' + fDT(pdt(r.review.at)) : ''}${r.review.note ? ' · Not: ' + esc(r.review.note) : ''}` : 'Kaçırılan düzenleme riskine karşı örneklem kontrolü için önünüze geldi.'}</p></div></div>`);
    if (r.note) b.push(`<div class="banner banner-info">${ic('info')}<div><p>${esc(r.note)}</p></div></div>`);
  }
  if (r.status === 'Onaylandı' && r.decided) b.push(`<div class="banner banner-info">${ic('check')}<div><b>Onaylandı: ${esc(r.units.map(u => u.n).join(', ') || 'birim atanmadı')}</b><p>${fDT(r.decided.at)} · ${esc(r.decided.by)}${approvedChanged(r) ? ` · YZ önerisi değiştirildi (öneri: ${esc(r.aiUnits.map(u => u.n).join(', '))})` : ' · YZ önerisi aynen onaylandı'}${r.decisionNote ? ` · Not: ${esc(r.decisionNote)}` : ''}</p></div></div>`);
  if (r.status === 'Reddedildi' && r.decided) { const rr = rejReasonOf(r); b.push(`<div class="banner banner-info">${ic('x')}<div><b>Reddedildi${rr ? ': ' + esc(REJ_LABEL[rr] || rr) : ''}</b><p>${fDT(r.decided.at)} · ${esc(r.decided.by)}${r.decisionNote ? ` · Not: ${esc(r.decisionNote)}` : ''}</p></div></div>`); }
  if (r.dup) b.push(`<div class="banner banner-warn">${ic('copy')}<div><b>Olası mükerrer${r.dup.score != null ? ` (benzerlik ${dec(r.dup.score)})` : ''}</b><p>${r.dup.regulationId ? `#${r.dup.regulationId} numaralı kayıtla benzer.` : 'Benzer bir kayıt var.'} Mükerrerse kaydı “Mükerrer kayıt” gerekçesiyle reddedebilirsiniz.</p>${r.dup.regulationId ? `<div class="b-act"><button class="btn btn-sm" data-act="open-dup" data-id="${r.dup.regulationId}">Benzer kaydı aç</button></div>` : ''}</div></div>`);
  return b.length ? `<div class="d-banners">${b.join('')}</div>` : '';
}
const wait = (n = 3) => `<div class="skel" aria-label="Yükleniyor">${['95%', '88%', '60%'].slice(0, n).map(w => `<i style="width:${w}"></i>`).join('')}</div>`;
/** Özet metnini dayanak numaralarıyla çizer: her cümlenin sonuna sunucudaki summaryCitations numarası eklenir. */
function summaryHtml(r) {
  let text = r.summary; const marks = [];
  let from = 0;
  (r.citations || []).forEach(c => {
    const i = c.sentence ? text.indexOf(c.sentence, from) : -1;
    if (i >= 0 && c.passages.length) { const end = i + c.sentence.length; marks.push([end, c.n]); from = end; }
  });
  let out = '', last = 0;
  marks.forEach(([end, n]) => { out += esc(text.slice(last, end)) + `<button class="cite" data-act="cite" data-c="${n}" aria-label="Dayanak pasaj ${n}">${n}</button>`; last = end; });
  return out + esc(text.slice(last));
}
function summaryTab(r) {
  if (r.kind === 'irr') return irrTab(r);
  if (r.stage && !r.summary) return `<section class="d-sec"><h3>Kısa içerik <span class="ai">${ic('spark')}YZ özeti</span></h3>${wait()}<p class="hint">Özet hazırlanıyor.</p></section>`;
  const sev = curSev(r); const pend = isPending(r);
  const d = effDays(r);
  const effTxt = d !== null ? `${fD(r.eff)} <span class="tag ${d < 0 ? 'tag-plain' : d <= 7 ? 'tag-crit' : d <= 30 ? 'tag-warn' : 'tag-plain'}">${d < 0 ? 'yürürlükte' : d === 0 ? 'bugün' : d + ' gün kaldı'}</span>` : r.eff === 'yayim' ? 'Yayımı tarihinde <span class="tag tag-plain">yürürlükte</span>' : '<span class="muted">Belgede belirtilmemiş</span>';
  const hasCites = (r.citations || []).some(c => c.passages.length);
  return `<section class="d-sec"><h3>Kısa içerik <span class="ai">${ic('spark')}YZ özeti</span>${edTag(r, 'summary')}</h3><p class="sum">${r.summary ? summaryHtml(r) : '<span class="muted">Özet yok.</span>'}</p>${hasCites ? '<p class="hint">Numaralar, özetin dayandığı pasajı belgede açar.</p>' : ''}</section>
  <section class="d-sec"><h3>Tarihler</h3><dl class="facts">
    <div><dt>Yayım tarihi</dt><dd>${r.pub ? fD(r.pub) : '—'}${prov(r, 'pub')}</dd></div>
    <div><dt>Yürürlük tarihi</dt><dd>${effTxt}${prov(r, 'eff')}</dd></div>
    ${r.draftDue ? `<div><dt>Görüş son tarihi</dt><dd>${fD(r.draftDue)} <span class="tag tag-warn">${dd(r.draftDue, NOW)} gün kaldı</span>${prov(r, 'draftDue')}</dd></div>` : ''}
  </dl>${r.conflict ? `<div class="banner banner-warn" style="margin-top:10px">${ic('alert')}<div><b>Tarih çelişkisi olabilir</b><p>${esc(r.conflict.message)}${r.conflict.foundText ? ` <i>“${esc(trunc(r.conflict.foundText, 200))}”</i>` : ''}</p>${canDecide() ? `<div class="b-act"><button class="btn btn-sm" data-act="conf-ok">${r.eff instanceof Date ? fD(r.eff) + ' doğru' : 'Mevcut tarih doğru'}</button><button class="btn btn-sm btn-ghost" data-act="conf-edit">Tarihi düzelt</button></div>` : ''}</div></div>` : ''}</section>
  ${r.topics.length ? `<section class="d-sec"><h3>Bankanın faaliyetleri açısından ilgili konular <span class="ai">YZ</span>${edTag(r, 'topics')}</h3><div class="topics">${r.topics.map(t => `<span class="topic">${esc(t)}</span>`).join('')}</div></section>` : ''}
  <section class="d-sec"><h3>Önem ve güven</h3>
    <div class="sevrow">${sevPill(sev)}<span>Güven: <b>${esc(r.band || '—')}</b></span></div>
    ${r.detail ? `<details class="why"><summary>Neden ${sev || 'bu önem'}?${ic('chev-d')}</summary><p>${esc(r.sevWhy || SEV_WHY[sev] || 'Gerekçe kaydedilmemiş.')}</p></details>
    <details class="why"><summary>Neden ilgili?${ic('chev-d')}</summary><p>${esc((r.relevance && r.relevance.rationale) || 'Gerekçe kaydedilmemiş.')}${r.relevance && r.relevance.score != null ? ` <span class="muted num">(ilgililik skoru ${dec(r.relevance.score)})</span>` : ''}</p></details>` : wait(2)}</section>
  ${pend ? unitSection(r) : editing(r) ? editSection(r) : r.status === 'Onaylandı' ? unitReadonly(r) : ''}`;
}
function irrTab(r) {
  return `<section class="d-sec"><h3>YZ neden ilgisiz buldu?</h3>${r.detail || r.why ? `<p class="sum">${esc(r.why || 'Gerekçe kaydedilmemiş.')}</p>` : wait(2)}</section>
  ${r.criteria && r.criteria.length ? `<section class="d-sec"><h3>Değerlendirme ölçütleri</h3><ul class="plist">${r.criteria.map(c => `<li>${esc(typeof c === 'string' ? c : (c.criterion || c.name || c.label || JSON.stringify(c)))}${c && c.met != null ? ` — ${c.met ? 'sağlanıyor' : 'sağlanmıyor'}` : ''}</li>`).join('')}</ul></section>` : ''}
  <section class="d-sec"><h3>Tarihler</h3><dl class="facts"><div><dt>Yayım tarihi</dt><dd>${r.pub ? fD(r.pub) : '—'}</dd></div><div><dt>Tespit</dt><dd>${r.det ? fDT(r.det) : '—'}</dd></div></dl></section>`;
}

/* ---------- kayıt bilgilerini düzenleme: YZ'nin ilk değerleri r.ai'de, değişen alanlar r.edited'de */
const TYPES = ['Kanun', 'Cumhurbaşkanı Kararı', 'Yönetmelik', 'Tebliğ', 'Genelge', 'Karar', 'Kurul Kararı', 'Rehber', 'Usul ve Esas', 'Standart', 'Duyuru', 'Bülten', 'Taslak', 'İlan'];
const META_F = { title: 'Başlık', type: 'Düzenleme türü', summary: 'Kısa içerik', pub: 'Yayım tarihi', eff: 'Yürürlük tarihi', draftDue: 'Görüş son tarihi', topics: 'İlgili konular' };
const metaOn = r => !!(S.meta && S.meta.id === r.id);
const canMeta = r => r.kind === 'reg' && !r.stage && canDecide();
const isEd = (r, k) => !!(r.edited && r.edited.includes(k));
const effStr = e => e instanceof Date ? fD(e) : e === 'yayim' ? 'Yayımında' : 'Belirtilmemiş';
const fieldVal = (o, k) => !o ? '' : k === 'pub' || k === 'draftDue' ? (o[k] ? fD(o[k]) : '—') : k === 'eff' ? effStr(o.eff) : k === 'topics' ? (o.topics || []).join(', ') : (o[k] || '');
const edTag = (r, k) => isEd(r, k) ? `<span class="tag tag-plain" title="YZ'nin ilk değeri: ${esc(trunc(fieldVal(r.ai, k), 160))}">${ic('edit')}Uzman düzenledi</span>` : '';
function prov(r, k) {
  if (isEd(r, k)) return `<span class="prov" title="YZ'nin ilk değeri: ${esc(fieldVal(r.ai, k))}">${ic('edit')}Uzman düzeltti</span>`;
  return k === 'pub' ? `<span class="prov" title="Belgeden okundu">${ic('doc')}Belgeden</span>` : `<span class="prov" title="YZ tarafından çıkarıldı">${ic('spark')}YZ</span>`;
}
function metaDraft(r) {
  return { title: r.title, type: r.type, summary: r.summary, pub: r.pub ? fTR(r.pub) : '', effMode: r.eff instanceof Date ? 'tarih' : r.eff === 'yayim' ? 'yayim' : 'yok', eff: r.eff instanceof Date ? fTR(r.eff) : '', draftDue: r.draftDue ? fTR(r.draftDue) : '', topics: [...r.topics], why: '' };
}
function metaForm(r) {
  const m = S.meta.draft, e = S.meta.err || {};
  const er = k => e[k] ? `<span class="field-err">${esc(e[k])}</span>` : '';
  const orig = k => isEd(r, k) ? `<span class="orig">YZ'nin ilk değeri: ${esc(trunc(fieldVal(r.ai, k), 120))} <button type="button" class="linkbtn" data-act="meta-restore" data-k="${k}">Geri yükle</button></span>` : '';
  return `<div class="meta-form">
    <div class="banner banner-info">${ic('edit')}<div><b>Kayıt bilgilerini düzenliyorsunuz</b><p>Değişiklikler adınızla Geçmiş sekmesine yazılır. YZ'nin ilk değerleri saklanır ve geri yüklenebilir.</p></div></div>
    ${e._ ? `<span class="field-err">${esc(e._)}</span>` : ''}
    <label class="field">Başlık<textarea class="input ${e.title ? 'err' : ''}" id="m-title" rows="2">${esc(m.title)}</textarea>${er('title')}${orig('title')}</label>
    <div class="frow"><label class="field">Düzenleme türü<select class="select" id="m-type">${TYPES.concat(TYPES.includes(m.type) ? [] : [m.type]).map(t => `<option ${t === m.type ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>${orig('type')}</label>
      <label class="field">Yayım tarihi<input class="input ${e.pub ? 'err' : ''}" id="m-pub" value="${esc(m.pub)}" placeholder="gg.aa.yyyy" inputmode="numeric">${er('pub')}${orig('pub')}</label></div>
    <div class="field"><span>Yürürlük tarihi</span><div class="seg" role="radiogroup" aria-label="Yürürlük tarihi türü">${[['tarih', 'Tarih'], ['yayim', 'Yayımında'], ['yok', 'Belirtilmemiş']].map(o => `<button type="button" class="seg-b" role="radio" aria-checked="${m.effMode === o[0]}" data-act="meta-eff" data-m="${o[0]}">${o[1]}</button>`).join('')}</div>
      ${m.effMode === 'tarih' ? `<input class="input ${e.eff ? 'err' : ''}" id="m-eff" value="${esc(m.eff)}" placeholder="gg.aa.yyyy" inputmode="numeric" style="max-width:180px">` : ''}${er('eff')}${orig('eff')}
      ${r.conflict ? `<span class="hint" style="margin:0">${esc(r.conflict.message)}</span>` : ''}</div>
    ${m.type === 'Taslak' || r.draftDue ? `<label class="field">Görüş son tarihi<input class="input ${e.draftDue ? 'err' : ''}" id="m-due" value="${esc(m.draftDue)}" placeholder="gg.aa.yyyy (boş bırakırsanız kaldırılır)" inputmode="numeric" style="max-width:180px">${er('draftDue')}${orig('draftDue')}</label>` : ''}
    <label class="field">Kısa içerik<textarea class="input ${e.summary ? 'err' : ''}" id="m-summary" rows="7">${esc(m.summary)}</textarea>${er('summary')}<span class="hint" style="margin:0;font-weight:400">Özeti elle değiştirirseniz belgedeki dayanak bağlantıları kalkar.</span>${orig('summary')}</label>
    <div class="field"><span>İlgili konular</span><div class="topics-edit">${m.topics.map((t, i) => `<span class="chip">${esc(t)}<button type="button" data-act="topic-del" data-i="${i}" aria-label="${esc(t)} konusunu kaldır">${ic('x')}</button></span>`).join('')}<input id="m-topic" placeholder="Konu yazıp Enter'a basın" autocomplete="off"></div>${orig('topics')}</div>
    <label class="field">Değişiklik notu <span class="muted" style="font-weight:400">(isteğe bağlı)</span><input class="input" id="m-why" value="${esc(m.why)}" placeholder="Örn. Başlık Resmî Gazete'deki haliyle düzeltildi."></label>
    <details class="why"><summary>Hangi alanlar düzenlenemez?${ic('chev-d')}</summary><p>Kaynak kurum, kayıt numarası, tespit zamanı, orijinal belge ve ondan çıkarılan metin kaynaktan gelir; YZ'nin gerekçeleri ve skorları modelin çıktısıdır. Önem derecesi sistemde değiştirilemez; birim ise karar alanından değiştirilir.</p></details>
  </div>`;
}
const metaActions = () => `<div class="d-actions"><div class="act-row"><button class="btn" data-act="meta-cancel">Vazgeç</button><button class="btn btn-primary grow" data-act="meta-save" ${S.busy ? 'disabled' : ''}>${ic('check')}Değişiklikleri kaydet</button></div><div class="act-hint">Kaydetmeden başka kayda geçemezsiniz. Esc ile vazgeçebilirsiniz.</div></div>`;

/* ---------- arşivde karar düzenleme (revise) */
const editing = r => !!(S.edit && S.edit.id === r.id);
const decisionText = r => r.status === 'Onaylandı' ? `Onay · ${r.units.map(u => u.n).join(', ') || 'birim yok'}` : r.status === 'Reddedildi' ? `Ret · ${REJ_LABEL[rejReasonOf(r)] || '—'}` : r.status;
function editSection(r) {
  const m = S.edit.mode;
  return `<section class="d-sec" id="editSec"><h3>Kararı düzenle</h3>
    <p class="hint" style="margin:0 0 10px">Mevcut karar: <b>${esc(decisionText(r))}</b>. Kaydedene kadar değişmez.</p>
    <div class="seg" role="radiogroup" aria-label="Yeni karar"><button class="seg-b" role="radio" aria-checked="${m === 'onay'}" data-act="edit-mode" data-m="onay">${ic('check')}Onayla</button><button class="seg-b" role="radio" aria-checked="${m === 'red'}" data-act="edit-mode" data-m="red">${ic('x')}Reddet</button></div>
    ${m === 'red' ? `<div class="rej-opts" style="margin-top:10px">${REJECT_REASONS.map(x => `<label><input type="radio" name="er" value="${x[0]}" ${S.edit.reason === x[0] ? 'checked' : ''}>${esc(x[1])}</label>`).join('')}</div>
      <label class="field" style="margin-top:8px">Not ${S.edit.reason === 'other' ? '<span class="muted" style="font-weight:400">(zorunlu)</span>' : '<span class="muted" style="font-weight:400">(isteğe bağlı)</span>'}<textarea class="input" id="editNote" rows="2">${esc(S.edit.note)}</textarea></label>` : ''}</section>
    ${m === 'onay' ? unitSection(r) : ''}
    <section class="d-sec"><label class="field">Değişiklik gerekçesi <span class="muted" style="font-weight:400">(zorunlu, en az 5 karakter, denetim izine yazılır)</span><textarea class="input" id="editWhy" rows="2" placeholder="Örn. Birimin görüşü üzerine Hazine Müdürlüğü de eklendi.">${esc(S.edit.why)}</textarea><span class="field-err" id="editErr"></span></label></section>`;
}
function unitReadonly(r) {
  const ai = r.aiUnits || [];
  const rows = r.units.map(u => ({ ...u, sugd: ai.some(a => a.code === u.code) }));
  ai.forEach(a => { if (!rows.find(u => u.code === a.code)) rows.push({ code: a.code, n: a.n, s: a.s, off: true, sugd: true }); });
  return `<section class="d-sec"><h3>Birim kararı</h3><div class="uopts">${rows.map(u => { const on = !u.off;
    return `<div class="uopt ${on ? 'on' : ''}" style="cursor:default"><span>${on ? ic('check') : ''}</span><span><span class="uopt-n">${esc(u.n)}</span><span class="uopt-w">${on ? 'Onaylandı' : 'Çıkarıldı'}${u.sugd ? ' · YZ önerisi' : ' · Uzman ekledi'}</span></span>
      <span class="uconf">${u.s != null && u.sugd ? `<span class="num">Uyum ${pct(u.s)}</span><span class="bar ${u.s < .5 ? 'low' : ''}"><i style="width:${Math.round(u.s * 100)}%"></i></span>` : '<span>Elle</span>'}</span></div>`; }).join('') || '<p class="muted">Birim atanmadı.</p>'}</div></section>`;
}
function unitSection(r) {
  const opts = [...r.units]; r.sel.forEach(c => { if (!opts.find(u => u.code === c)) opts.push({ code: c, n: unitName(c), s: null, why: 'Uzman tarafından eklendi.' }); });
  const top = r.units[0];
  const changed = r.sug.length && r.sel.length && !sameSet(r.sel, r.sug);
  return `<section class="d-sec"><h3>Birim önerisi <span class="ai">${ic('spark')}YZ önerisi</span>${changed ? '<span class="tag tag-warn">Öneri değiştirildi</span>' : ''}</h3>
    <p class="hint" style="margin:0 0 8px">Onaylamadan önce birimi değiştirebilir ya da birden fazla birim seçebilirsiniz. Değişiklikler modele geri bildirim olarak kaydedilir.</p>
    ${!r.units.length || r.needsUnit ? `<div class="banner banner-warn" style="margin-bottom:8px">${ic('alert')}<div><b>Birimi siz seçin</b><p>YZ güvenli bir öneri yapamadı${top ? ` (en yüksek uyum ${pct(top.s || 0)})` : ''}. Aşağıdan birim seçin ya da ekleyin.</p></div></div>` : ''}
    <div class="uopts">${opts.map(u => { const on = r.sel.includes(u.code); return `<label class="uopt ${on ? 'on' : ''}"><input type="checkbox" data-change="unit" data-u="${esc(u.code)}" ${on ? 'checked' : ''}>
      <span><span class="uopt-n">${esc(u.n)}</span><span class="uopt-w">${esc(u.why || '')}</span></span>
      <span class="uconf">${u.s != null && u.origin !== 'manual' ? `<span class="num">Uyum ${pct(u.s)}</span><span class="bar ${u.s < .5 ? 'low' : ''}"><i style="width:${Math.round(u.s * 100)}%"></i></span>` : '<span>Elle</span>'}</span></label>`; }).join('')}</div>
    <div class="addunit"><select class="select" id="addUnit" aria-label="Birim değiştir ya da ekle"><option value="">Birim değiştir / ekle…</option>${UNITS.filter(u => !opts.find(o => o.code === u.code)).map(u => `<option value="${esc(u.code)}">${esc(u.name)}</option>`).join('')}</select></div></section>`;
}

/* ---------- belge sekmesi: sunucudaki çıkarılmış metin + özet dayanak aralıkları */
function docTab(r) {
  if (r.kind === 'irr') {
    if (!r.docs) return `<div class="empty">${ic('doc')}<b>Yükleniyor…</b></div>`;
    return `<div class="doc-head">${ic('doc')}<div><b>Kaynak belgeler</b>Bu kayıt portalda görünmediği için çıkarılmış metin gösterilmez; orijinaller kaynak sitesinde.</div></div>
      <ul class="rlist">${r.docs.map(d => `<li>${esc(d.title || d.url)}<small>${esc(srcOf(d.source).a)} · ${esc(d.role)}${d.publishedAt ? ' · ' + fD(pd(d.publishedAt)) : ''}${d.isBaseline ? ' · ilk tarama' : ''} · <a href="${esc(d.url)}" target="_blank" rel="noopener">Orijinali aç</a></small></li>`).join('')}</ul>`;
  }
  if (r.docState === 'err') return `<div class="empty">${ic('doc')}<b>Metin henüz çıkarılmadı</b><span>${esc(r.docErr || 'Belge YZ hattında.')} Orijinali kaynağın sitesinde açabilirsiniz.</span></div>`;
  if (!r.doc) return `<div class="empty">${ic('doc')}<b>Belge yükleniyor…</b></div>`;
  const doc = r.doc; const text = doc.text || '';
  if (!text.trim()) return `<div class="empty">${ic('doc')}<b>Metin henüz çıkarılmadı</b><span>Orijinali kaynağın sitesinde açabilirsiniz.</span>${doc.url ? `<a class="btn btn-sm" href="${esc(doc.url)}" target="_blank" rel="noopener">Orijinali aç${ic('ext')}</a>` : ''}</div>`;
  const ranges = [];
  (r.citations || []).forEach(c => c.passages.forEach(p => { if (p.rawDocumentId === doc.rawDocumentId && p.charStart != null) ranges.push([p.charStart, p.charEnd, c.n]); }));
  const lastN = Math.max(0, ...(r.citations || []).map(c => c.n));
  let off = 0; const paras = [];
  text.split('\n').forEach(line => { const s = off; off += line.length + 1; if (line.trim()) paras.push([s, s + line.length, line]); });
  const html = paras.map(([s, e, line]) => {
    const hit = ranges.filter(([a, b]) => a < e && b > s).map(x => x[2]);
    const ns = [...new Set(hit)];
    const f = ns.length && (S.cite === 'last' ? ns.includes(lastN) : ns.map(String).includes(String(S.cite)));
    return `<p class="${ns.length ? 'cited' : ''} ${f ? 'focus' : ''}" ${f ? 'id="citeFocus"' : ''}>${ns.length ? `<span class="cnum">${ns.join(',')}</span>` : ''}${esc(line)}</p>`;
  }).join('');
  return `<div class="doc-head">${ic('doc')}<div><b>${esc(trunc(doc.title || r.title, 70))}</b>${esc(srcOf(doc.source).a)}${doc.extractionMethod ? ' · ' + esc(doc.extractionMethod) : ''}${doc.extractedAt ? ' · metin ' + fD(pdt(doc.extractedAt)) + ' tarihinde çıkarıldı' : ''}${doc.pageCount ? ' · ' + doc.pageCount + ' sayfa' : ''}${doc.truncated ? ' · metin kısaltıldı' : ''}</div>${doc.url ? `<a class="btn btn-sm" href="${esc(doc.url)}" target="_blank" rel="noopener">Orijinali aç${ic('ext')}</a>` : ''}</div>
    <div class="doc">${html}</div>${ranges.length ? `<p class="hint">Vurgulu pasajlar YZ özetindeki numaralı ifadelerin dayanağıdır.</p>` : ''}`;
}
function histTab(r) {
  if (r.kind === 'irr') return `<div class="empty"><b>Bu kayıt için denetim izi gösterilmiyor</b><span>İlgisiz arşivindeki kayıtların olayları yalnızca inceleme kararıyla görünür.</span></div>`;
  if (!r.detail) return wait();
  const ev = [...(r.trail || [])].sort((a, b) => pdt(b.timestamp) - pdt(a.timestamp));
  return `<ol class="tl">${ev.map(e => `<li><time>${fDT(pdt(e.timestamp))}</time><div><span class="who">${esc(e.actor || '')}</span> · ${esc(e.action)}${e.rejectionReason ? ` (${esc(REJ_LABEL[e.rejectionReason] || e.rejectionReason)})` : ''}${e.note ? `<small>${esc(e.note)}</small>` : ''}</div></li>`).join('') || '<li><div>Olay yok.</div></li>'}</ol>
    <div class="chain">${ic('check')}Olaylar hash zinciriyle korunur.${isAdmin() ? ` <button class="linkbtn" data-act="verify-one" data-id="${r.id}">Bu kaydın bütünlüğünü doğrula</button>` : ''}<span id="verifyRes"></span></div>`;
}
function actionsHtml(r) {
  if (metaOn(r)) return metaActions();
  if (r.kind === 'irr') {
    if (S.view === 'arsiv' || r.review || !canDecide()) return '';
    return `<div class="d-actions"><div class="act-row"><button class="btn grow" data-act="irr-back" data-id="${r.id}">İlgili, YZ'ye geri gönder</button><button class="btn btn-primary grow" data-act="irr-ok" data-id="${r.id}">${ic('check')}İlgisiz, doğru</button></div><div class="act-hint">Kararınız ilgililik modelinin değerlendirme setine eklenir.</div></div>`;
  }
  if (r.stage) return `<div class="d-actions"><div class="lockline">${ic('lock')}Özet ve birim önerisi hazır olunca karar verebilirsiniz.</div></div>`;
  if (!canDecide()) return '';
  if (ARSIV_Q.includes(r.status)) {
    if (editing(r)) {
      const ok = S.edit.mode === 'onay';
      return `<div class="d-actions"><div class="recip">${ok ? (r.sel.length ? `Yeni karar: <b>Onay · ${esc(r.sel.map(unitName2(r)).join(', '))}</b>` : 'Birim seçilmedi') : `Yeni karar: <b>Ret · ${esc(REJ_LABEL[S.edit.reason])}</b>`}</div>
        <div class="act-row"><button class="btn" data-act="edit-cancel">Vazgeç</button><button class="btn btn-primary grow" data-act="edit-save" ${(ok && !r.sel.length) || S.busy ? 'disabled' : ''}>${ic('check')}Değişikliği kaydet</button></div>
        <div class="act-hint">Önceki karar silinmez; Geçmiş sekmesinde ve denetim izinde kalır.</div></div>`;
    }
    return `<div class="d-actions"><div class="act-row"><button class="btn" data-act="reopen" ${S.busy ? 'disabled' : ''}>${ic('undo')}Gelen kutusuna geri al</button><button class="btn btn-primary grow" data-act="edit-start">${ic('edit')}Kararı düzenle <span class="kbd">E</span></button></div>
      <div class="act-hint">Değişiklikler gerekçesiyle denetim izine yazılır.</div></div>`;
  }
  if (r.status !== 'Bekliyor') return '';
  if (S.rejecting) return `<div class="d-actions"><form class="rej" data-form="reject"><b style="font-size:13px">Reddetme gerekçesi</b><div class="rej-opts">${REJECT_REASONS.map(x => `<label><input type="radio" name="rr" value="${x[0]}" ${S.rejReason === x[0] ? 'checked' : ''}>${esc(x[1])}</label>`).join('')}</div>
    <textarea class="input" id="rejNote" rows="2" placeholder="${S.rejReason === 'other' ? 'Açıklama (zorunlu)' : 'Not (isteğe bağlı)'}"></textarea><span class="field-err" id="rejErr"></span><div class="act-row"><button type="button" class="btn" data-act="reject-cancel">Vazgeç</button><button type="submit" class="btn btn-danger grow" ${S.busy ? 'disabled' : ''}>${ic('x')}Reddet</button></div><div class="act-hint">Gerekçe, YZ modelinin iyileştirilmesinde kullanılır.</div></form></div>`;
  const n = r.sel.length; const changed = r.sug.length && n && !sameSet(r.sel, r.sug);
  return `<div class="d-actions"><div class="recip">${n ? `Onaylanacak birim: <b>${esc(r.sel.map(unitName2(r)).join(', '))}</b>${changed ? ' <span class="muted">(YZ önerisinden farklı)</span>' : ''}` : 'Birim seçilmedi'}</div>
    <div class="act-row"><button class="btn btn-danger" data-act="reject-open" ${S.busy ? 'disabled' : ''}>${ic('x')}Reddet <span class="kbd">R</span></button>
    <button class="btn btn-primary grow" data-act="approve" ${n && !S.busy ? '' : 'disabled'}>${ic('check')}${n ? (changed ? 'Değişiklikle onayla' : 'Onayla') : 'Önce birim seçin'} <span class="kbd">A</span></button></div>
    <div class="act-hint">Onayladığınızda sıradaki kayıt açılır.</div></div>`;
}
