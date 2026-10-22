'use strict';
/* ============================================================================================================
   Çizim, etkileşim ve yönlendirme. Tüm yazma işlemleri API'ye gider; arayüz sunucudan dönen kaydı gösterir.
   ============================================================================================================ */
const VIEWS = { gelen: viewGelen, arsiv: viewArsiv, yonetim: viewYonetim };
function renderAll() {
  closePop();
  const graph = $('.flow-scroll'); const graphScroll = graph ? graph.scrollLeft : 0;
  const focusedNode = document.activeElement && document.activeElement.matches('.hat-node') ? document.activeElement.dataset.n : null;
  const keep = document.activeElement && document.activeElement.dataset ? { id: document.activeElement.id, set: document.activeElement.dataset.set, txt: document.activeElement.classList.contains('input') } : null;
  $('#app').innerHTML = appbar() + `<main id="main">${VIEWS[S.view]()}</main>`;
  syncDetailSize();
  if (keep && (keep.id || keep.set)) { const el = keep.id ? document.getElementById(keep.id) : document.querySelector(`input.input[data-set="${keep.set}"]`); if (el && el.focus) { el.focus(); if (el.setSelectionRange && el.type !== 'range') { try { el.setSelectionRange(el.value.length, el.value.length); } catch (_) { /* tür desteklemiyor */ } } } }
  if (focusedNode) { const el = Array.from(document.querySelectorAll('.hat-node')).find(n => n.dataset.n === focusedNode); if (el) el.focus({ preventScroll: true }); }
  if (graph && $('.flow-scroll')) $('.flow-scroll').scrollLeft = graphScroll;
}
function refreshInbox(opts = {}) {
  if (S.meta && S.meta.id !== S.openId) S.meta = null;
  if (S.view !== 'gelen' || !$('#listRegion')) {
    const y = scrollY; const ds = !opts.top && $('#dScroll') ? $('#dScroll').scrollTop : 0;
    renderAll(); scrollTo(0, y); if ($('#dScroll')) $('#dScroll').scrollTop = ds; return;
  }
  $('.appbar').outerHTML = appbar();
  $('#kpis').innerHTML = kpis();
  $('#qtabs').innerHTML = qtabs();
  $('#fbtns').innerHTML = fbtns();
  $('#listRegion').innerHTML = listRegion();
  $('#inbox').classList.toggle('has-detail', !!S.openId);
  const det = $('#detail'); det.hidden = !S.openId;
  const keep = !opts.top && $('#dScroll') ? $('#dScroll').scrollTop : 0;
  det.innerHTML = S.openId ? detailHtml() : '';
  syncDetailSize();
  if ($('#dScroll')) $('#dScroll').scrollTop = keep;
  const f = $('#citeFocus'); if (f && opts.cite) f.scrollIntoView({ block: 'center', behavior: 'smooth' });
}
/** Açık ekranı yeniden çizer (ayrıntı bölmesi dahil). */
function repaint(opts = {}) {
  if (S.view === 'gelen') return refreshInbox(opts);
  if (S.view === 'arsiv' && $('#arsivMain')) {
    const ds = !opts.top && $('#dScroll') ? $('#dScroll').scrollTop : 0;
    $('.appbar').outerHTML = appbar(); paintArchive(); if ($('#dScroll')) $('#dScroll').scrollTop = ds;
    const f = $('#citeFocus'); if (f && opts.cite) f.scrollIntoView({ block: 'center', behavior: 'smooth' });
    return;
  }
  renderAll();
}

/* ---------- bildirim ve iletişim kutuları */
let toastT = null;
function toast(msg, o = {}) {
  let el = $('#toast'); if (el) el.remove(); clearTimeout(toastT);
  el = document.createElement('div'); el.id = 'toast'; el.className = 'toast' + (o.err ? ' err' : ''); el.setAttribute('role', o.err ? 'alert' : 'status');
  el.innerHTML = `${ic(o.err ? 'alert' : 'check')}<span>${o.err ? esc(msg) : msg}</span>${o.link ? `<a href="${o.link[1]}">${o.link[0]}</a>` : ''}`;
  document.body.appendChild(el);
  toastT = setTimeout(() => el.remove(), o.err ? 9000 : 4500);
}
let dlgOnOk = null, dlgPrevFocus = null;
function dialog({ title, body, ok, okKind = 'btn-primary', onOk, cancel = 'Vazgeç' }) {
  closeDialog(); dlgPrevFocus = document.activeElement;
  const bg = document.createElement('div'); bg.className = 'dlg-bg'; bg.id = 'dlg'; bg.dataset.act = 'dlg-bg';
  bg.innerHTML = `<div class="dlg" role="dialog" aria-modal="true" aria-labelledby="dlgT"><div class="dlg-h"><h2 id="dlgT">${title}</h2><button class="btn btn-ghost icon-btn" data-act="dlg-close" aria-label="Kapat">${ic('x')}</button></div>
    <form id="dlgF"><div class="dlg-b">${body}</div><div class="dlg-f">${cancel ? `<button type="button" class="btn" data-act="dlg-close">${cancel}</button>` : ''}${ok ? `<button type="submit" class="btn ${okKind}">${ok}</button>` : ''}</div></form></div>`;
  document.body.appendChild(bg); dlgOnOk = onOk;
  const first = bg.querySelector('input,select,textarea,button[type=submit]'); if (first) first.focus();
}
function closeDialog() { const d = $('#dlg'); if (d) { d.remove(); if (dlgPrevFocus && dlgPrevFocus.focus) dlgPrevFocus.focus(); } dlgOnOk = null; }
function dlgError(f, msg) {
  let el = f.querySelector('.dlg-err'); if (!el) { el = document.createElement('p'); el.className = 'field-err dlg-err'; f.querySelector('.dlg-b').appendChild(el); }
  el.textContent = msg;
}
function dlgBusy(f, on) { const b = f.querySelector('button[type=submit]'); if (b) b.disabled = on; }

/* ---------- açılır menüler */
function closePop() { const p = $('#pop'); if (p) p.remove(); if (S.pop) { S.pop = null; const fb = $('#fbtns'); if (fb) fb.innerHTML = fbtns(); } }
function openPop(anchor, html, info) {
  const p0 = $('#pop'); if (p0) p0.remove();
  S.pop = info; const p = document.createElement('div'); p.id = 'pop'; p.className = 'pop'; p.innerHTML = html; document.body.appendChild(p);
  const r = anchor.getBoundingClientRect(); const w = p.offsetWidth;
  p.style.top = Math.min(r.bottom + 6, innerHeight - p.offsetHeight - 8) + 'px';
  p.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
  p.anchor = anchor;
}
function filterPopHtml(k) {
  let opts;
  if (k === 'src') opts = Boot.sources.map(s => [s.code, s.name]);
  if (k === 'sev') opts = SEVS.map(x => [x, x]);
  if (k === 'eff') opts = EFF_OPTS;
  if (k === 'added') opts = ADDED_OPTS;
  if (k === 'unit') opts = UNITS.map(u => [u.code, u.name]);
  const lbl = { src: 'Kaynak', sev: 'Önem derecesi', eff: 'Yürürlük', added: 'Eklenme zamanı', unit: 'Önerilen birim' }[k];
  return `<div class="pop-h">${lbl}</div><div style="max-height:320px;overflow:auto">${opts.map(o => `<label class="opt"><input type="checkbox" data-change="fopt" data-f="${k}" value="${esc(o[0])}" ${S.f[k].has(o[0]) ? 'checked' : ''}>${k === 'sev' ? sevPill(o[1]) : esc(o[1])}</label>`).join('') || '<div class="opt muted">Seçenek yok</div>'}</div>
    <div class="pop-f"><button class="linkbtn" data-act="fclear" data-f="${k}">Temizle</button><button class="btn btn-sm" data-act="pop-close">Tamam</button></div>`;
}

/* ---------- süzgeç / sayfa değişince listeyi yeniden yükle */
function listChanged() { S.selected.clear(); loadList().then(() => paintAll()); if (DECISION_Q.includes(S.queue)) loadKpi().then(() => paintAll()); }
function changeSort(key, direction) {
  S.sortDir = direction || (S.sort === key ? (S.sortDir === 'asc' ? 'desc' : 'asc') : SORTS[key].direction);
  S.sort = key; S.page = 1; refreshInbox(); listChanged();
}

/* ---------- kayıt açma ve gezinme */
const blockedByMeta = id => { if (S.meta && S.meta.id !== id) { toast('Önce düzenlemeyi kaydedin ya da vazgeçin.', { err: true }); return true; } return false; };
async function openRecord(id, opts = {}) {
  const r = byId(id); if (!r || blockedByMeta(id)) return;
  if (S.openId !== id) { S.dTab = 'ozet'; S.cite = null; S.rejecting = false; cancelEdit(); }
  S.openId = id; S.focusId = id;
  const y = scrollY; repaint({ top: true }); if (S.view !== 'gelen') scrollTo(0, y);
  const row = document.querySelector(`tr[data-id="${id}"]`); if (row) row.scrollIntoView({ block: 'nearest' });
  if (!opts.keepFocus && innerWidth < 1100) { const c = document.querySelector('#detail [data-act=close]'); if (c) c.focus(); }
  await loadDetail(r);
}
async function loadDetail(r) {
  try {
    if (r.kind !== 'reg') upsertArchive(await GET('/archive/regulations/' + r.num));
    else {
      const { data } = await api('GET', '/regulations/' + r.num);
      upsert(data, { resetSel: !r.touched });
      if (r.unread) { r.unread = false; api('POST', `/regulations/${r.num}/views`).catch(() => {}); }
    }
  } catch (e) {
    if (S.openId === r.id) { toast(errMsg(e), { err: true }); if (e.status === 404) { S.openId = null; repaint({ top: true }); reloadAll(); } }
    return;
  }
  if (S.openId === r.id) { repaint(); if (S.dTab === 'belge') ensureDoc(r); }
}
async function ensureDoc(r) {
  if (r.kind !== 'reg' || r.doc || r.docState === 'err') return;
  try { r.doc = await GET(`/regulations/${r.num}/document`); }
  catch (e) { r.docState = 'err'; r.docErr = e.status === 404 ? 'Kayda bağlı belge bulunamadı.' : errMsg(e); }
  if (S.openId === r.id && S.dTab === 'belge') repaint({ cite: true });
}
function step(dir) {
  const list = navList(); if (!list.length) return;
  if (S.view === 'yonetim') return;
  const cur = S.openId || S.focusId; let i = list.findIndex(x => x.id === cur);
  i = i < 0 ? 0 : Math.max(0, Math.min(list.length - 1, i + dir));
  if (S.openId) openRecord(list[i].id, { keepFocus: true });
  else { S.focusId = list[i].id; repaint(); const row = document.querySelector(`tr[data-id="${S.focusId}"]`); if (row) row.scrollIntoView({ block: 'nearest' }); }
}
/** Karardan sonra: listeyi ve sayaçları yeniler, aynı sıradaki kaydı açar. */
async function afterDecision(idx) {
  S.openId = null;
  if (S.view === 'arsiv') { await Promise.all([loadArchive(), loadArchiveStats(), loadStats().catch(() => {})]); repaint({ top: true }); return; }
  await reloadAll();
  const list = currentList(); const nx = list[Math.min(idx, list.length - 1)];
  if (nx) openRecord(nx.id, { keepFocus: true }); else refreshInbox();
}
async function writeFail(e, r) {
  toast(errMsg(e), { err: true });
  if (r && [404, 409, 412].includes(e.status)) { r.touched = false; await loadDetail(r); reloadAll(); }
}
async function busy(fn) {
  if (S.busy) return; S.busy = true; repaint();
  try { await fn(); } finally { S.busy = false; if (S.view !== 'yonetim') repaint(); }
}

/* ---------- karar işlemleri */
async function approve(id) {
  const r = byId(id); if (!r || !r.sel.length) return;
  const idx = S.list.ids.indexOf(id);
  await busy(async () => {
    try {
      let ver = r.version;
      if (!sameSet(r.sel, r.units.map(u => u.code))) { const x = await api('PUT', `/regulations/${r.num}/units`, { etag: ver, body: { unit_codes: r.sel } }); upsert(x.data); ver = x.data.version; }
      const names = r.sel.map(unitName2(r)); const changed = r.sug.length && !sameSet(r.sel, r.sug);
      const { data } = await api('POST', `/regulations/${r.num}/decision`, { etag: ver, body: { decision: 'approve' } });
      r.touched = false; upsert(data, { resetSel: true }); S.selected.delete(id);
      toast(`Onaylandı: ${esc(trunc(names.join(', '), 48))}${changed ? ' (öneri değiştirildi)' : ''}.`);
      await afterDecision(idx);
    } catch (e) { await writeFail(e, r); }
  });
}
async function reject(id, reason, note) {
  const r = byId(id); const idx = S.list.ids.indexOf(id);
  await busy(async () => {
    try {
      const { data } = await api('POST', `/regulations/${r.num}/decision`, { etag: r.version, body: { decision: 'reject', rejection_reason: reason, note: note || null } });
      upsert(data, { resetSel: true }); S.rejecting = false; S.selected.delete(id);
      toast(`Reddedildi: ${esc(REJ_LABEL[reason])}.`);
      await afterDecision(idx);
    } catch (e) { await writeFail(e, r); }
  });
}
/* kayıt bilgilerini düzenleme (PATCH /content: yalnızca değişen alanlar gider) */
function startMeta(id, focusKey) {
  const r = byId(id); if (!r || !canMeta(r)) return;
  cancelEdit(); S.rejecting = false;
  S.meta = { id, draft: metaDraft(r), err: {} };
  refreshInbox({ top: true });
  const el = $(focusKey === 'eff' ? (S.meta.draft.effMode === 'tarih' ? '#m-eff' : '.seg-b[aria-checked=true]') : '#m-title'); if (el) el.focus();
}
const same = (a, b) => (a instanceof Date || b instanceof Date) ? !!(a && b && a.getTime && b.getTime && a.getTime() === b.getTime()) : Array.isArray(a) ? a.join('|') === (b || []).join('|') : (a ?? null) === (b ?? null);
async function saveMeta() {
  const r = byId(S.meta.id), m = S.meta.draft, err = {};
  const title = m.title.trim().replace(/\s+/g, ' ');
  if (title.length < 5) err.title = 'Başlık en az 5 karakter olmalı.';
  const pub = parseTR(m.pub); if (!pub) err.pub = 'Tarihi gg.aa.yyyy biçiminde yazın, örn. 15.10.2026.';
  let eff = m.effMode === 'yayim' ? 'yayim' : m.effMode === 'yok' ? null : parseTR(m.eff);
  if (m.effMode === 'tarih' && !eff) err.eff = 'Tarihi gg.aa.yyyy biçiminde yazın.';
  else if (eff instanceof Date && pub && eff < pub) err.eff = 'Yürürlük tarihi yayım tarihinden önce olamaz.';
  let due = null; const dueOn = m.type === 'Taslak' || r.draftDue;
  if (dueOn && m.draftDue.trim()) { due = parseTR(m.draftDue); if (!due) err.draftDue = 'Tarihi gg.aa.yyyy biçiminde yazın.'; }
  if (r.summary && !m.summary.trim()) err.summary = 'Kısa içerik boş bırakılamaz.';
  const show = e => { S.meta.err = e; refreshInbox(); const f = $('.meta-form .err') || $('.meta-form .field-err'); if (f) { f.scrollIntoView({ block: 'center' }); if (f.focus) f.focus(); } };
  if (Object.keys(err).length) return show(err);
  const body = {};
  if (title !== r.title) body.title = title;
  if (m.type !== r.type) body.reg_type = m.type;
  if (m.summary.trim() !== r.summary) body.summary = m.summary.trim();
  if (!same(pub, r.pub)) body.publish_date = fISO(pub);
  if (!same(eff, r.eff)) body.effective = eff === 'yayim' ? { mode: 'on_publish' } : eff === null ? { mode: 'none' } : { mode: 'date', date: fISO(eff) };
  if (dueOn && !same(due, r.draftDue)) body.draft_deadline = due ? fISO(due) : null;
  if (!same(m.topics, r.topics)) body.topics = m.topics;
  if (!Object.keys(body).length) return show({ _: 'Hiçbir alan değişmedi.' });
  if (m.why.trim()) body.note = m.why.trim();
  await busy(async () => {
    try {
      const { data } = await api('PATCH', `/regulations/${r.num}/content`, { etag: r.version, body });
      upsert(data); S.meta = null;
      const lbl = Object.keys(body).filter(k => k !== 'note').map(k => ({ title: 'Başlık', reg_type: 'Düzenleme türü', summary: 'Kısa içerik', publish_date: 'Yayım tarihi', effective: 'Yürürlük tarihi', draft_deadline: 'Görüş son tarihi', topics: 'İlgili konular' }[k])).join(', ');
      toast(`${lbl} güncellendi.`); refreshInbox({ top: true }); loadList();
    } catch (e) { if (e.status === 400 || e.status === 422) show({ _: errMsg(e) }); else { S.meta = null; await writeFail(e, r); } }
  });
}
const syncDraft = () => { if (!S.meta) return; const d = S.meta.draft; [['m-title', 'title'], ['m-summary', 'summary'], ['m-pub', 'pub'], ['m-eff', 'eff'], ['m-due', 'draftDue'], ['m-why', 'why']].forEach(([id, k]) => { const el = document.getElementById(id); if (el) d[k] = el.value; }); };

/* arşivde karar düzenleme (revise) ve geri alma (reopen) */
function cancelEdit() { if (!S.edit) return; const r = byId(S.edit.id); if (r) r.sel = r.units.map(u => u.code); S.edit = null; }
function startEdit(id) {
  const r = byId(id); if (!r || !ARSIV_Q.includes(r.status) || !canDecide()) return;
  S.edit = { id, mode: r.status === 'Reddedildi' ? 'red' : 'onay', reason: rejReasonOf(r) || 'not_banking_related', why: '', note: '' };
  r.sel = r.units.map(u => u.code); S.dTab = 'ozet'; repaint();
  const sec = $('#editSec'); if (sec) { sec.scrollIntoView({ block: 'start', behavior: 'smooth' }); const b = sec.querySelector('[aria-checked=true]'); if (b) b.focus({ preventScroll: true }); }
}
async function saveEdit() {
  const r = byId(S.edit.id); const whyEl = $('#editWhy'); const why = (whyEl.value || '').trim(); const err = m => { $('#editErr').textContent = m; whyEl.focus(); };
  const ok = S.edit.mode === 'onay'; const noteEl = $('#editNote'); if (noteEl) S.edit.note = noteEl.value;
  if (ok && r.status === 'Onaylandı' && sameSet(r.sel, r.units.map(u => u.code))) return err('Karar değişmedi. Birim ekleyin, çıkarın ya da Reddet\'i seçin.');
  if (!ok && r.status === 'Reddedildi' && rejReasonOf(r) === S.edit.reason) return err('Karar değişmedi. Farklı bir gerekçe ya da Onayla\'yı seçin.');
  if (!ok && S.edit.reason === 'other' && !S.edit.note.trim()) return err('“Diğer” için not zorunlu.');
  if (why.length < 5) return err('Değişikliğin gerekçesini yazın (en az 5 karakter).');
  await busy(async () => {
    try {
      const body = ok ? { decision: 'approve', reason: why, unit_codes: r.sel } : { decision: 'reject', reason: why, rejection_reason: S.edit.reason, note: S.edit.note.trim() || null };
      const { data } = await api('POST', `/regulations/${r.num}/decision/revise`, { etag: r.version, body });
      upsert(data, { resetSel: true }); S.edit = null; S.dTab = 'ozet';
      toast(`Karar güncellendi: ${esc(trunc(decisionText(byId(r.id)), 60))}.`);
      loadArchive(); loadArchiveStats(); loadStats().catch(() => {}); repaint({ top: true });
    } catch (e) { if (e.status === 400) { err(errMsg(e)); } else await writeFail(e, r); }
  });
}
function reopen(id) {
  const r = byId(id);
  dialog({ title: 'Gelen kutusuna geri al', ok: 'Geri al',
    body: `<p>Mevcut karar (<b>${esc(decisionText(r))}</b>) kaldırılır ve kayıt yeniden karar bekler. Önceki karar Geçmiş sekmesinde kalır.</p>
      <label class="field">Gerekçe<textarea class="input" id="roWhy" rows="2" placeholder="Örn. Birim, düzenlemenin kendi alanında olmadığını bildirdi."></textarea></label>`,
    onOk: async f => {
      const why = f.querySelector('#roWhy').value.trim(); if (why.length < 5) return 'Gerekçeyi yazın (en az 5 karakter).';
      dlgBusy(f, true);
      try {
        const { data } = await api('POST', `/regulations/${r.num}/decision/reopen`, { etag: r.version, body: { reason: why } });
        upsert(data, { resetSel: true }); closeDialog(); cancelEdit(); S.openId = null;
        toast('Kayıt Gelen kutusuna geri alındı.', { link: ['Gelen kutusu', '#gelen'] });
        loadArchive(); loadArchiveStats(); loadStats().catch(() => {}); repaint({ top: true });
      } catch (e) { dlgBusy(f, false); if (e.status === 400) return e.message; closeDialog(); await writeFail(e, r); }
      return 'wait';
    } });
}
async function irr(id, ok) {
  const r = byId(id); const idx = S.list.ids.indexOf(id);
  if (!r || S.busy) return;
  await busy(async () => {
    try {
      await api('POST', `/regulations/${r.num}/irrelevant-review`, { body: { verdict: ok ? 'confirm' : 'relevant' } });
      toast(ok ? 'YZ kararı onaylandı; kayıt arşive gitti.' : 'Kayıt ilgili sayıldı ve YZ hattına geri alındı.');
      if (S.openId === id) await afterDecision(idx); else await reloadAll();
    } catch (e) { toast(errMsg(e), { err: true }); if ([404, 409].includes(e.status)) reloadAll(); }
  });
}
async function retryIds(ids) {
  try {
    const r = await api('POST', '/admin/retry-failed', { body: ids ? { ids } : {} });
    toast(r.data.status === 'queued' ? `${r.data.count} kayıt yeniden deneme kuyruğuna alındı.` : 'Yeniden denenecek kayıt yok.');
    await Promise.all([loadStats().catch(() => {}), S.view === 'yonetim' ? loadAdmin(S.adminTab) : Promise.resolve()]);
    if (S.view === 'gelen') reloadAll();
  } catch (e) { toast(errMsg(e), { err: true }); }
}

/* ---------- yönetim verisi yükleme */
const numify = o => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, Number(v)]));
async function loadAdmin(tab) {
  S.admErr = null;
  try {
    if (tab === 'genel') { const [metrics, alerts] = await Promise.all([GET('/admin/metrics'), GET('/admin/alerts', { status: 'open' }), loadHealth(), loadStats()]); S.alerts = alerts; S.adm.genel = { metrics }; }
    else if (tab === 'hat') { S.adm.hat = await GET('/pipeline'); S.refreshed = new Date(); if (S.node) loadNodeItems(S.node); }
    else if (tab === 'kaynaklar') { const [metrics, scans] = await Promise.all([GET('/admin/metrics'), GET('/admin/scans', { limit: 100 }), loadHealth()]); S.adm.kaynaklar = { metrics, scans }; }
    else if (tab === 'ayarlar') {
      const [d, hist] = await Promise.all([GET('/admin/settings', { detail: true }), GET('/admin/settings/history', { limit: 10 })]);
      S.adm.ayarlar = d; S.adm.hist = hist;
      if (!S.set || !settingDirty().length) { S.set = numify(d.values); S.setSaved = { ...S.set }; }
    } else if (tab === 'bakim') { const [baseline, failed] = await Promise.all([GET('/admin/rebaseline'), GET('/pipeline/nodes/reg:AI_FAILED', { limit: 20 }), loadStats()]); S.adm.bakim = { baseline, failed: failed.items }; }
    else if (tab === 'excel') S.adm.excel = true;
  } catch (e) { S.admErr = errMsg(e); }
  if (S.view === 'yonetim' && S.adminTab === tab) { const y = scrollY; renderAll(); scrollTo(0, y); }
}
async function loadNodeItems(id) {
  S.adm.nodeItems = null;
  let result;
  try { result = await GET('/pipeline/nodes/' + encodeURIComponent(id), { limit: 20 }); }
  catch (e) { result = { node: id, items: [], error: errMsg(e) }; }
  if (S.node !== id) return;
  S.adm.nodeItems = result;
  if (S.view === 'yonetim' && S.adminTab === 'hat' && S.node === id) renderAll();
}
let previewT = null;
function schedulePreview() {
  clearTimeout(previewT); S.adm.preview = null;
  const dirty = settingDirty(); if (!dirty.length || Object.keys(settingErrors()).length) return;
  previewT = setTimeout(async () => {
    const values = Object.fromEntries(dirty.map(k => [k, setStr(k, S.set[k])]));
    try { S.adm.preview = await api('POST', '/admin/settings/preview', { body: { values } }).then(r => r.data); }
    catch (e) { S.adm.preview = { err: errMsg(e) }; }
    if (S.view === 'yonetim' && S.adminTab === 'ayarlar') renderAll();
  }, 500);
}

/* ---------- tıklamalar */
document.addEventListener('click', e => {
  const t = e.target.closest('[data-act]');
  const pop = $('#pop');
  if (pop && !pop.contains(e.target) && !(t && ['pop-filter', 'pop-bell', 'pop-user', 'pop-hat'].includes(t.dataset.act) && pop.anchor === t)) closePop();
  if (!t) return;
  const a = t.dataset.act, id = t.dataset.id;
  switch (a) {
    case 'open': if (e.target.closest('a,button,input,select,label')) return; openRecord(id); break;
    case 'sel': e.stopPropagation(); S.selected[t.checked ? 'add' : 'delete'](id); $('#listRegion').innerHTML = listRegion(); break;
    case 'selall': S.list.ids.forEach(i => S.selected[t.checked ? 'add' : 'delete'](i)); $('#listRegion').innerHTML = listRegion(); break;
    case 'close': if (S.meta) { toast('Önce düzenlemeyi kaydedin ya da vazgeçin.', { err: true }); break; } cancelEdit(); S.openId = null; S.rejecting = false; { const y = scrollY; repaint(); if (S.view !== 'gelen') scrollTo(0, y); } { const row = document.querySelector(`tr[data-id="${S.focusId}"]`); if (row) row.querySelector('input,td').scrollIntoView({ block: 'nearest' }); } break;
    case 'prev': step(-1); break;
    case 'next': step(1); break;
    case 'dtab': S.dTab = t.dataset.t; repaint({ top: true }); if (S.dTab === 'belge') ensureDoc(byId(S.openId)); break;
    case 'cite': S.dTab = 'belge'; S.cite = t.dataset.c; repaint({ cite: true }); ensureDoc(byId(S.openId)); break;
    case 'queue': S.queue = t.dataset.q; S.page = 1; S.openId = null; S.f.sev.clear(); S.f.eff.clear(); S.f.added.clear(); S.f.unit.clear(); S.f.needUnit = false; S.list = { ids: [], total: 0, loading: true, err: null }; S.kpi = null; if (S.view !== 'gelen') location.hash = 'gelen'; else { refreshInbox(); listChanged(); } break;
    case 'goq': S.queue = t.dataset.q; S.page = 1; S.openId = null; S.list = { ids: [], total: 0, loading: true, err: null }; S.kpi = null; go('gelen'); break;
    case 'reload': if (S.view === 'arsiv') loadArchive(); else listChanged(); break;
    case 'kpi-sev': { const on = S.f.sev.size === 2 && S.f.sev.has('Kritik') && S.f.sev.has('Yüksek'); S.f.sev = on ? new Set() : new Set(['Kritik', 'Yüksek']); S.page = 1; refreshInbox(); listChanged(); break; }
    case 'kpi-eff': { const on = S.f.eff.size === 1 && S.f.eff.has('30'); S.f.eff = on ? new Set() : new Set(['30']); S.page = 1; refreshInbox(); listChanged(); break; }
    case 'kpi-need': S.f.needUnit = !S.f.needUnit; S.page = 1; refreshInbox(); listChanged(); break;
    case 'pop-filter': { const k = t.dataset.f; if (S.pop && S.pop.f === k) { closePop(); break; } closePop(); const anchor = document.querySelector(`#fbtns [data-act="pop-filter"][data-f="${k}"]`); openPop(anchor, filterPopHtml(k), { f: k }); anchor.setAttribute('aria-expanded', 'true'); break; }
    case 'pop-close': closePop(); break;
    case 'fclear': S.f[t.dataset.f].clear(); S.page = 1; refreshInbox(); closePop(); listChanged(); break;
    case 'unchip': { const k = t.dataset.k; if (k === 'q') { S.q = ''; $('#q').value = ''; } else if (k === 'needUnit') S.f.needUnit = false; else S.f[k].delete(t.dataset.v); S.page = 1; refreshInbox(); listChanged(); break; }
    case 'clear-filters': S.q = ''; if ($('#q')) $('#q').value = ''; Object.values(S.f).forEach(v => v instanceof Set && v.clear()); S.f.needUnit = false; S.page = 1; refreshInbox(); listChanged(); break;
    case 'sort': changeSort(t.dataset.s); break;
    case 'page': S.page = +t.dataset.p; refreshInbox(); loadList(); $('#listRegion').scrollIntoView({ block: 'start' }); break;
    case 'apage': S.arsivPage = +t.dataset.p; loadArchive(); break;
    case 'approve': approve(S.openId); break;
    case 'xl-tab': S.xl.tab = t.dataset.t; renderAll(); break;
    case 'xl-run': { S.xl.tTitle = $('#xlT').value; S.xl.tText = $('#xlX').value; xlRun(); break; }
    case 'xl-export': xlExport(); break;
    case 'xl-open': openAnywhere(id); break;
    case 'edit-start': startEdit(S.openId); break;
    case 'edit-mode': S.edit.mode = t.dataset.m; { const w = $('#editWhy'); if (w) S.edit.why = w.value; } repaint(); { const b = document.querySelector('.seg-b[aria-checked=true]'); if (b) b.focus({ preventScroll: true }); } break;
    case 'edit-cancel': cancelEdit(); repaint(); break;
    case 'edit-save': saveEdit(); break;
    case 'reopen': reopen(S.openId); break;
    case 'arsivf': S.arsivF = t.dataset.f; S.arsivPage = 1; S.openId = null; S.ar = { ids: [], total: 0, loading: true, err: null }; loadArchive(); break;
    case 'reject-open': S.rejecting = true; refreshInbox(); break;
    case 'reject-cancel': S.rejecting = false; refreshInbox(); break;
    case 'open-dup': openAnywhere(id); break;
    case 'conf-ok': { const r = byId(S.openId); busy(async () => { try { const x = await api('PATCH', `/regulations/${r.num}/content`, { etag: r.version, body: { confirm_effective: true } }); upsert(x.data); toast('Yürürlük tarihi doğrulandı.'); repaint(); loadList(); } catch (err) { await writeFail(err, r); } }); break; }
    case 'conf-edit': startMeta(S.openId, 'eff'); break;
    case 'meta-start': startMeta(S.openId); break;
    case 'meta-cancel': S.meta = null; refreshInbox({ top: true }); break;
    case 'meta-save': syncDraft(); saveMeta(); break;
    case 'meta-eff': syncDraft(); S.meta.draft.effMode = t.dataset.m; S.meta.err = {}; refreshInbox(); { const el = $('#m-eff') || $('.seg-b[aria-checked=true]'); if (el) el.focus(); } break;
    case 'meta-restore': { syncDraft(); const r = byId(S.meta.id); const k = t.dataset.k; const v = metaDraft(Object.assign(Object.create(r), r.ai)); if (k === 'eff') { S.meta.draft.effMode = v.effMode; S.meta.draft.eff = v.eff; } else S.meta.draft[k] = Array.isArray(v[k]) ? [...v[k]] : v[k]; refreshInbox(); break; }
    case 'topic-del': syncDraft(); S.meta.draft.topics.splice(+t.dataset.i, 1); refreshInbox(); { const el = $('#m-topic'); if (el) el.focus(); } break;
    case 'irr-ok': e.stopPropagation(); irr(id, true); break;
    case 'irr-back': e.stopPropagation(); irr(id, false); break;
    case 'retry-one': retryIds([byId(id) ? byId(id).num : +id]); break;
    case 'retry-all': retryIds(null); break;
    case 'verify-one': api('GET', '/admin/audit/verify', { query: { regulation_id: byId(id).num } }).then(x => { $('#verifyRes').textContent = x.data.ok ? ' · bütünlük doğrulandı' : ` · ${x.data.problems.length} sorun bulundu`; }).catch(err => toast(errMsg(err), { err: true })); break;
    case 'bulk-clear': S.selected.clear(); $('#listRegion').innerHTML = listRegion(); break;
    case 'bulk-read': bulkRead(); break;
    case 'bulk-approve': bulkApprove(); break;
    case 'bulk-rej': bulkReject(); break;
    case 'export': exportList(); break;
    case 'shortcuts': showShortcuts(); break;
    case 'pop-bell': if (S.pop && S.pop.b) { closePop(); break; } openPop(t, `<div class="pop-h">Açık alarmlar</div>${(S.alerts || []).map(x => `<a class="alarm" href="#yonetim" data-act="pop-close"><span><span class="tag ${SEV_TAG[x.severity] || 'tag-plain'}">${esc(SEV_TXT[x.severity] || x.severity)}</span> <span class="muted">${esc(x.type)} · ${fDs(pdt(x.openedAt))}</span></span>${esc(trunc(x.message, 120))}</a>`).join('') || '<div class="opt muted">Açık alarm yok</div>'}<div class="pop-f"><span></span><a class="btn btn-sm" href="#yonetim" data-act="pop-close">Tümünü gör</a></div>`, { b: 1 }); break;
    case 'pop-user': { if (S.pop && S.pop.u) { closePop(); break; } const me = Boot.me; openPop(t, `<div class="who-card"><b>${esc(me.name)}</b><span>${esc(me.title || '')}</span><span>Roller: ${esc((me.roles || []).join(', '))}</span><span class="muted">Oturum: ${Sess.user ? 'kullanıcı adı elle ayarlandı' : 'varsayılan demo kullanıcısı'} (SSO henüz yok)</span>
      <label class="field" style="margin-top:8px">Kullanıcı adı<input class="input" id="demoUser" value="${esc(Sess.user)}" placeholder="Ad Soyad"></label><div class="pop-f"><button class="linkbtn" data-act="user-reset">Varsayılana dön</button><button class="btn btn-sm" data-act="user-set">Değiştir</button></div></div>`, { u: 1 }); break; }
    case 'user-set': Sess.user = ($('#demoUser').value || '').trim(); location.reload(); break;
    case 'user-reset': Sess.user = ''; location.reload(); break;
    case 'pop-hat': openPop(t, `<button class="opt" data-act="mermaid">${ic('copy')}Mermaid olarak kopyala</button>`, { h: 1 }); break;
    case 'mermaid': { closePop(); api('GET', '/pipeline', { query: { format: 'mermaid' } }).then(x => { const txt = x.data; const done = () => toast('Mermaid metni kopyalandı.'); const fail = () => dialog({ title: 'Mermaid metni', body: `<textarea class="input mono" rows="8" readonly>${esc(txt)}</textarea><p>Metni seçip kopyalayın.</p>`, cancel: 'Kapat' });
      try { navigator.clipboard.writeText(txt).then(done, fail); } catch (_) { fail(); } }).catch(err => toast(errMsg(err), { err: true })); break; }
    case 'alarm-close': api('POST', `/admin/alerts/${id}/resolve`, { body: {} }).then(() => { S.alerts = (S.alerts || []).filter(x => String(x.id) !== id); toast('Alarm kapatıldı.'); if (S.view === 'yonetim') loadAdmin(S.adminTab); else renderAll(); }).catch(err => { toast(errMsg(err), { err: true }); if (err.status === 409) loadAdmin(S.adminTab); }); break;
    case 'adm-reload': loadAdmin(S.adminTab); renderAll(); break;
    case 'node': S.node = t.dataset.n || null; S.adm.nodeItems = null; renderAll(); if (S.node) loadNodeItems(S.node); break;
    case 'refresh': loadAdmin('hat').then(() => toast('Güncellendi.')); break;
    case 'scan': api('POST', `/admin/sources/${encodeURIComponent(t.dataset.k)}/run`).then(x => { toast(`${esc(srcOf(t.dataset.k).n)} taraması kuyruğa alındı.`); setTimeout(() => S.view === 'yonetim' && loadAdmin(S.adminTab), 2500); }).catch(err => toast(errMsg(err), { err: true })); break;
    case 'backscan': { const k = t.dataset.k; dialog({ title: `Geçmişi tara · ${esc(srcOf(k).a)}`, ok: 'Taramayı başlat', body: `<p>Kaynağın listesinde artık görünmeyen eski belgeleri bulmak için. En fazla 62 günlük aralık seçilebilir.</p><div class="frow"><label class="field">Başlangıç<input class="input" id="bsF" value="${fTR(new Date(NOW.getTime() - 30 * DAY))}" placeholder="gg.aa.yyyy"></label><label class="field">Bitiş<input class="input" id="bsT" value="${fTR(NOW)}" placeholder="gg.aa.yyyy"></label></div>`,
      onOk: async f => { const a1 = parseTR(f.querySelector('#bsF').value), b1 = parseTR(f.querySelector('#bsT').value); if (!a1 || !b1) return 'Tarihleri gg.aa.yyyy biçiminde yazın.'; if (b1 < a1) return 'Bitiş, başlangıçtan önce olamaz.'; const n = dd(b1, a1) + 1; if (n > 62) return `Aralık ${n} gün; en fazla 62 gün seçebilirsiniz.`;
        dlgBusy(f, true); try { await api('POST', `/admin/sources/${encodeURIComponent(k)}/backfill`, { body: { from: fISO(a1), to: fISO(b1) } }); closeDialog(); toast(`${esc(srcOf(k).a)} için ${n} günlük geçmiş tarama kuyruğa alındı.`); setTimeout(() => S.view === 'yonetim' && loadAdmin(S.adminTab), 2500); } catch (err) { dlgBusy(f, false); return errMsg(err); } return 'wait'; } }); break; }
    case 'toggle-scans': S.scansOpen = !S.scansOpen; renderAll(); break;
    case 'scanf': S.scanFilter = t.dataset.f; renderAll(); break;
    case 'set-reset': S.set = { ...S.setSaved }; S.setErr = {}; S.adm.preview = null; renderAll(); break;
    case 'set-review': { const d = settingDirty(); dialog({ title: 'Ayar değişikliklerini kaydet', ok: 'Kaydet', body: `<div class="impact" style="padding:0">${d.map(k => `<div class="row"><span>${SET_LBL[k] || k}</span><b class="num">${fmtSet(k, S.setSaved[k])} → ${fmtSet(k, S.set[k])}</b></div>`).join('')}</div><label class="field">Değişiklik gerekçesi<textarea class="input" id="setWhy" rows="2" placeholder="Örn. Son iki haftada kaçırılan TCMB duyuruları nedeniyle"></textarea></label><p>Kayıt adınızla ayar geçmişine yazılır. Yeni ayarlar sonraki YZ çağrılarından itibaren geçerli olur.</p>`,
      onOk: async f => { const why = f.querySelector('#setWhy').value.trim(); if (why.length < 5) return 'Kısa bir gerekçe yazın (en az 5 karakter).'; dlgBusy(f, true);
        try { const values = Object.fromEntries(d.map(k => [k, setStr(k, S.set[k])])); const x = await api('PUT', '/admin/settings', { query: { detail: true }, body: { values, reason: why } });
          S.adm.ayarlar = x.data; S.set = numify(x.data.values); S.setSaved = { ...S.set }; S.setErr = {}; S.adm.preview = null; closeDialog(); toast('Ayarlar kaydedildi.'); loadAdmin('ayarlar'); }
        catch (err) { dlgBusy(f, false); if (err.code === 'invalid_setting') { S.setErr = err.details.fields || {}; closeDialog(); renderAll(); toast(err.message, { err: true }); return 'wait'; } return errMsg(err); } return 'wait'; } }); break; }
    case 'rp-preview': { const a1 = parseTR($('#rpFrom').value), b1 = parseTR($('#rpTo').value); S.rp = { source: $('#rpSrc').value, from: $('#rpFrom').value, to: $('#rpTo').value, stage: $('#rpStep').value };
      if (!a1 || !b1 || b1 < a1) { S.rp.err = 'Geçerli bir tarih aralığı yazın (gg.aa.yyyy).'; renderAll(); break; }
      api('POST', '/admin/reprocess', { query: { dry_run: true }, body: { stage: S.rp.stage, from: fISO(a1), to: fISO(b1), source: S.rp.source || null } }).then(x => { S.rp.est = x.data; }).catch(err => { S.rp.err = errMsg(err); }).then(() => renderAll()); break; }
    case 'rp-start': { const rp = S.rp; dialog({ title: 'Yeniden işlemeyi başlat', ok: 'Başlat', body: `<p><b>${N(rp.est.count)} kayıt</b> yeniden işlenecek. Tahmini süre ${rp.est.estimatedMinutes} dk, yaklaşık ${N(rp.est.llmCalls)} YZ çağrısı. İşlem sürerken bu kayıtlar Gelen kutusunda “YZ hazırlıyor” olarak görünür.</p>`,
      onOk: async f => { dlgBusy(f, true); try { await api('POST', '/admin/reprocess', { body: { stage: rp.stage, from: fISO(parseTR(rp.from)), to: fISO(parseTR(rp.to)), source: rp.source || null } }); S.rp = null; closeDialog(); renderAll(); toast('Yeniden işleme kuyruğa alındı.'); } catch (err) { dlgBusy(f, false); return errMsg(err); } return 'wait'; } }); break; }
    case 'bl-preview': { S.bl = $('#blIn').value; const d = pd(S.bl); S.blPlan = null; S.blErr = null;
      if (!d || fISO(d) !== S.bl || !$('#blIn').checkValidity()) { S.blErr = 'Geçerli bir tarih seçin veya tarih alanına yazın.'; renderAll(); break; }
      const cutoff = S.bl;
      GET('/admin/rebaseline', { cutoff }).then(p => { if (S.bl === cutoff) S.blPlan = p; }).catch(err => { if (S.bl === cutoff) S.blErr = errMsg(err); }).then(() => { if (S.bl === cutoff) renderAll(); }); break; }
    case 'bl-apply': { const pl = S.blPlan; if (!pl || pl.cutoff !== $('#blIn').value) { toast('Önce yeni sınırın etkisini önizleyin.', { err: true }); break; } dialog({ title: 'Baseline sınırını uygula', ok: 'Uygula', body: `<p>Yeni sınır <b>${esc(fTR(pd(pl.cutoff)))}</b>. ${N(pl.converted)} kayıt YZ hattına alınacak${pl.crawl.length ? `; ${pl.crawl.length} kaynak sınıra kadar taranacak` : ''}. Bu işlem YZ maliyeti doğurabilir.</p>`,
      onOk: async f => { dlgBusy(f, true); try { await api('POST', '/admin/rebaseline', { body: { cutoff: pl.cutoff } }); S.blPlan = null; closeDialog(); toast('Baseline sınırı güncelleniyor; iş kuyruğa alındı.'); loadAdmin('bakim'); } catch (err) { dlgBusy(f, false); return errMsg(err); } return 'wait'; } }); break; }
    case 'chain': t.disabled = true; GET('/admin/audit/verify').then(d => { S.chain = { ok: d.ok, problems: d.problems || [], text: d.ok ? 'Zincir doğrulandı · kopukluk yok' : `${d.problems.length} sorun bulundu` }; }).catch(err => { S.chain = { ok: false, problems: [], text: errMsg(err) }; }).then(() => renderAll()); break;
    case 'dlg-close': closeDialog(); break;
    case 'dlg-bg': if (e.target === t) closeDialog(); break;
  }
});

/* ---------- toplu işlemler, dışa aktarma */
async function bulkRead() {
  const ids = [...S.selected].map(byId).filter(r => r && r.unread);
  await Promise.all(ids.map(r => api('POST', `/regulations/${r.num}/views`).then(() => { r.unread = false; }).catch(() => {})));
  toast(`${ids.length} kayıt okundu işaretlendi.`); S.selected.clear(); refreshInbox();
}
function bulkSummary(res, verb) {
  const why = {}; res.skipped.forEach(s => { why[s.message] = (why[s.message] || 0) + 1; });
  return `${res.done.length} kayıt ${verb}${res.skipped.length ? `, ${res.skipped.length} kayıt atlandı (${Object.entries(why).map(([m, n]) => `${n}: ${m}`).join('; ')})` : ''}.`;
}
function bulkApprove() {
  const ids = [...S.selected]; if (ids.length > 100) { toast('Toplu işlem en fazla 100 kayıt alır; seçimi daraltın.', { err: true }); return; }
  const ready = ids.filter(i => { const r = byId(i); return r && !needsUnit(r); });
  dialog({ title: `${ids.length} kaydı onayla`, ok: 'Onayla', body: `<label class="switch" style="font-size:13.5px;color:var(--fg)"><input type="radio" name="bm" value="sug" ${ready.length ? 'checked' : 'disabled'}> Her kayıtta YZ'nin önerdiği birimi onayla <span class="muted">(${ready.length} kayıt; güvenli önerisi olmayan ${ids.length - ready.length} kayıt atlanır)</span></label>
        <label class="switch" style="font-size:13.5px;color:var(--fg)"><input type="radio" name="bm" value="one" ${ready.length ? '' : 'checked'}> Hepsini tek birimle onayla</label><select class="select" id="bmU" style="width:100%">${UNITS.map(u => `<option value="${esc(u.code)}">${esc(u.name)}</option>`).join('')}</select>
        <p>Tek birim seçerseniz, önerisi farklı olan kayıtlar “değiştirilerek onaylandı” olarak işaretlenir.</p>`,
    onOk: async f => {
      const mode = f.querySelector('input[name=bm]:checked').value; dlgBusy(f, true);
      try {
        const body = mode === 'one' ? { ids, decision: 'approve', unit_codes: [f.querySelector('#bmU').value] } : { ids: ready, decision: 'approve' };
        const { data } = await api('POST', '/regulations/bulk-decision', { body });
        const skippedLocal = mode === 'sug' ? ids.length - ready.length : 0;
        closeDialog(); S.selected.clear(); S.openId = null; toast(bulkSummary(data, 'onaylandı') + (skippedLocal ? ` ${skippedLocal} kaydın birimi seçilmediği için gönderilmedi.` : ''), { link: ['Arşiv', '#arsiv'] }); await reloadAll();
      } catch (err) { dlgBusy(f, false); return errMsg(err); } return 'wait';
    } });
}
function bulkReject() {
  const ids = [...S.selected]; if (ids.length > 100) { toast('Toplu işlem en fazla 100 kayıt alır; seçimi daraltın.', { err: true }); return; }
  dialog({ title: `${ids.length} kaydı reddet`, ok: 'Reddet', okKind: 'btn-danger', body: `<div class="rej-opts">${REJECT_REASONS.map((x, i) => `<label><input type="radio" name="br" value="${x[0]}" ${i ? '' : 'checked'}>${esc(x[1])}</label>`).join('')}</div><textarea class="input" id="brN" rows="2" placeholder="Not (“Diğer” için zorunlu)"></textarea>`,
    onOk: async f => {
      const reason = f.querySelector('input[name=br]:checked').value; const note = f.querySelector('#brN').value.trim();
      if (reason === 'other' && !note) return '“Diğer” için açıklama yazın.';
      dlgBusy(f, true);
      try { const { data } = await api('POST', '/regulations/bulk-decision', { body: { ids, decision: 'reject', rejection_reason: reason, note: note || null } });
        closeDialog(); S.selected.clear(); S.openId = null; toast(bulkSummary(data, 'reddedildi')); await reloadAll(); }
      catch (err) { dlgBusy(f, false); return errMsg(err); } return 'wait';
    } });
}
function exportList() {
  if (S.queue === 'ilgisiz') { toast('İlgisizler Excel\'e aktarılamaz.', { err: true }); return; }
  dialog({ title: "Excel'e aktar", ok: 'Dışa aktar', body: `<div class="rej-opts" role="group" aria-label="Dışa aktarma kapsamı">
      <label><input type="radio" name="exportScope" value="filtered" checked>Filtrelenmiş liste (${N(S.list.total)} kayıt)</label>
      <label><input type="radio" name="exportScope" value="selected"${S.selected.size ? '' : ' disabled'}>Yalnızca seçilenler (${N(S.selected.size)} kayıt)</label>
    </div>${S.selected.size ? '' : '<p class="muted">Yalnızca seçilenleri aktarmak için listeden en az bir kayıt seçin.</p>'}
    <p>Uygulanan kuyruk, süzgeçler ve sıralama korunur; tüm sayfalardan en fazla 5.000 kayıt indirilir. Dosyada başlık, kaynak, tarihler, önem, önerilen birim ve YZ özeti yer alır.</p>`,
    onOk: async f => {
      const selectedOnly = f.querySelector('input[name=exportScope]:checked').value === 'selected';
      if (selectedOnly && !S.selected.size) return 'En az bir kayıt seçin.';
      dlgBusy(f, true);
      try { const { page, page_size, ...q } = listParams(); if (selectedOnly) q.ids = [...S.selected];
        const res = await download('/regulations/export', q, 'mevzuat-liste.xlsx'); closeDialog();
        toast(res.headers.get('X-Export-Truncated') === 'true' ? `Dosya indirildi; ${res.headers.get('X-Export-Total')} kaydın yalnızca ilk bölümü yer alıyor.` : 'Dosya indirildi.'); }
      catch (err) { dlgBusy(f, false); return errMsg(err); } return 'wait';
    } });
}
/** Excel karşılaştırma sonucundaki bir sistem kaydını, bulunduğu ekranda açar. */
async function openAnywhere(id) {
  try {
    const { data } = await api('GET', '/regulations/' + id); const r = upsert(data, { resetSel: true });
    S.pendingOpen = r.id;
    if (ARSIV_Q.includes(r.status)) { S.arsivF = r.status === 'Reddedildi' ? 'red' : 'onay'; S.arsivQ = ''; S.arsivPage = 1; S.ar = { ids: [], total: 0, loading: true, err: null }; go('arsiv'); }
    else { S.queue = r.stage ? 'hazirlaniyor' : 'tum'; S.q = ''; Object.values(S.f).forEach(v => v instanceof Set && v.clear()); S.f.needUnit = false; S.page = 1; S.list = { ids: [], total: 0, loading: true, err: null }; go('gelen'); }
  } catch (e) {
    if (e.status !== 404) { toast(errMsg(e), { err: true }); return; }
    try { const d = await GET('/archive/regulations/' + id); const r = upsertArchive(d); S.pendingOpen = r.id; S.arsivF = r.kind === 'baseline' ? 'baseline' : 'irr'; S.arsivQ = ''; S.arsivPage = 1; S.ar = { ids: [], total: 0, loading: true, err: null }; go('arsiv'); }
    catch (_) { toast('Bu kayıt portalda ya da arşivde görünmüyor.', { err: true }); }
  }
}

/* ---------- değişiklik ve yazma olayları */
document.addEventListener('change', e => {
  const t = e.target;
  if (t.dataset.change === 'fopt') { S.f[t.dataset.f][t.checked ? 'add' : 'delete'](t.value); S.page = 1; const k = t.dataset.f; refreshInbox(); listChanged(); const nb = document.querySelector(`[data-act=pop-filter][data-f=${k}]`); if (nb) openPop(nb, filterPopHtml(k), { f: k }); return; }
  if (t.id === 'xlFile' && t.files[0]) { S.xl.file = t.files[0]; S.xl.res = null; S.xl.err = null; renderAll(); return; }
  if (t.id === 'xlM') { S.xl.method = t.value; renderAll(); return; }
  if (t.id === 'm-type') { syncDraft(); S.meta.draft.type = t.value; refreshInbox(); const el = $('#m-type'); if (el) el.focus(); return; }
  if (t.dataset.change === 'unit') { const r = byId(S.openId); const u = t.dataset.u; r.sel = t.checked ? [...r.sel, u] : r.sel.filter(x => x !== u); r.touched = true; repaint(); return; }
  if (t.id === 'addUnit' && t.value) { const r = byId(S.openId); r.sel = [...r.sel, t.value]; r.touched = true; repaint(); return; }
  if (t.id === 'sort') { changeSort(t.value, SORTS[t.value].direction); return; }
  if (t.id === 'sortDir') { changeSort(S.sort, t.value); return; }
  if (t.id === 'ps') { S.ps = +t.value; S.page = 1; refreshInbox(); listChanged(); return; }
  if (t.id === 'techT') { S.tech = t.checked; renderAll(); return; }
  if (t.id === 'hatLabelsT') { S.hatLabels = t.checked; renderAll(); return; }
  if (t.id === 'autoT') { S.auto = t.checked; return; }
  if (t.name === 'rr') { S.rejReason = t.value; const n = $('#rejNote'); if (n) n.placeholder = t.value === 'other' ? 'Açıklama (zorunlu)' : 'Not (isteğe bağlı)'; return; }
  if (t.name === 'er') { S.edit.reason = t.value; const w = $('#editWhy'); if (w) S.edit.why = w.value; const n = $('#editNote'); if (n) S.edit.note = n.value; repaint(); return; }
  if (t.dataset.set && t.type !== 'range') { const k = t.dataset.set; const v = parseFloat(String(t.value).replace(',', '.')); if (!isNaN(v)) { S.set[k] = v; delete S.setErr[k]; } schedulePreview(); renderAll(); return; }
});
document.addEventListener('input', e => {
  const t = e.target;
  if (t.id === 'blIn') {
    S.bl = t.value; S.blPlan = null; S.blErr = null;
    const card = t.closest('.mcard'); card.querySelector('[data-act="bl-apply"]').disabled = true;
    card.querySelectorAll('.result, .field-err').forEach(el => el.remove()); return;
  }
  if (t.id === 'q') { S.q = t.value; S.page = 1; clearTimeout(t._tm); t._tm = setTimeout(() => { S.selected.clear(); loadList().then(() => paintAll()); }, 300); return; }
  if (S.meta && /^m-(title|summary|pub|eff|due|why)$/.test(t.id)) { syncDraft(); t.classList.remove('err'); return; }
  if (t.id === 'xlQ') { S.xl.q = t.value; const pos = t.selectionStart; renderAll(); const n = $('#xlQ'); n.focus(); n.setSelectionRange(pos, pos); return; }
  if (t.id === 'editWhy') { S.edit.why = t.value; $('#editErr').textContent = ''; return; }
  if (t.id === 'editNote') { S.edit.note = t.value; return; }
  if (t.id === 'arsivQ') { S.arsivQ = t.value; S.arsivPage = 1; clearTimeout(t._tm); t._tm = setTimeout(loadArchive, 300); return; }
  if (t.dataset.set && t.type === 'range') { const k = t.dataset.set; S.set[k] = +t.value; delete S.setErr[k]; const box = document.querySelector(`input.input[data-set="${k}"]`); if (box) box.value = fmtSet(k, S.set[k]); clearTimeout(t._tm); t._tm = setTimeout(() => { schedulePreview(); renderAll(); const r = document.getElementById('r-' + k); if (r) r.focus(); }, 250); }
});
document.addEventListener('submit', async e => {
  e.preventDefault(); const f = e.target;
  if (f.dataset.form === 'reject') {
    const reason = (f.querySelector('input[name=rr]:checked') || {}).value || 'not_banking_related'; const note = $('#rejNote').value.trim();
    if (reason === 'other' && !note) { $('#rejErr').textContent = '“Diğer” için açıklama yazın.'; $('#rejNote').focus(); return; }
    reject(S.openId, reason, note); return;
  }
  if (f.id === 'dlgF' && dlgOnOk) {
    const err = await dlgOnOk(f);
    if (typeof err === 'string' && err !== 'wait') { dlgError(f, err); return; }
    if (err === 'wait') return;      // iletişim kutusunu işlem kendisi kapatır
    closeDialog();
  }
});

/* ---------- klavye */
function showShortcuts() {
  const k = [['J / ↓', 'Sonraki kayıt'], ['K / ↑', 'Önceki kayıt'], ['Enter', 'Kaydı aç'], ['X', 'Kaydı seç'], ['A', 'Birim önerisini onayla'], ['R', 'Reddet'], ['E', 'Arşivde kararı düzenle'], ['/', 'Aramaya git'], ['Esc', 'Ayrıntıyı ya da pencereyi kapat'], ['?', 'Bu listeyi göster']];
  dialog({ title: 'Klavye kısayolları', cancel: 'Kapat', body: `<div class="kbd-list">${k.map(x => `<span class="kbd">${x[0]}</span><span>${x[1]}</span>`).join('')}</div>` });
}
let detailDrag = null;
function endDetailResize(e) {
  if (!detailDrag || (e && e.pointerId !== detailDrag.pointerId)) return;
  const { handle, pointerId } = detailDrag;
  detailDrag = null;
  if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
  document.documentElement.classList.remove('detail-resizing');
}
document.addEventListener('pointerdown', e => {
  const handle = e.target.closest('#detailResize');
  if (!handle || e.button !== 0 || !e.isPrimary) return;
  e.preventDefault();
  detailDrag = { handle, pointerId: e.pointerId, x: e.clientX, width: $('#detail').getBoundingClientRect().width };
  handle.setPointerCapture(e.pointerId);
  document.documentElement.classList.add('detail-resizing');
});
document.addEventListener('pointermove', e => {
  if (detailDrag && e.pointerId === detailDrag.pointerId) setDetailSize(detailDrag.width + detailDrag.x - e.clientX);
});
['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => document.addEventListener(name, endDetailResize));
addEventListener('blur', () => endDetailResize());
document.addEventListener('keydown', e => {
  if (e.target.closest('#detailResize') && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
    e.preventDefault();
    const { min, max } = detailSizeBounds();
    const width = $('#detail').getBoundingClientRect().width;
    setDetailSize(e.key === 'Home' ? min : e.key === 'End' ? max : width + (e.key === 'ArrowLeft' ? 1 : -1) * (e.shiftKey ? 50 : 10));
    return;
  }
  const node = e.target.closest('.hat-node');
  if (node && (e.key === 'Enter' || e.key === ' ')) {
    e.preventDefault(); node.dispatchEvent(new MouseEvent('click', { bubbles: true })); return;
  }
  if (e.target.id === 'm-topic' && e.key === 'Enter') {
    e.preventDefault(); const v = e.target.value.trim().replace(/\s+/g, ' ');
    if (v && !S.meta.draft.topics.some(x => lower(x) === lower(v))) { syncDraft(); S.meta.draft.topics.push(v); refreshInbox(); }
    else e.target.value = '';
    const el = $('#m-topic'); if (el) el.focus(); return;
  }
  if (e.target.id === 'm-topic' && e.key === 'Backspace' && !e.target.value && S.meta.draft.topics.length) { syncDraft(); S.meta.draft.topics.pop(); refreshInbox(); $('#m-topic').focus(); return; }
  const tag = (e.target.tagName || '').toLowerCase(); const typing = ['input', 'textarea', 'select'].includes(tag) || e.target.isContentEditable;
  if (e.key === 'Escape') { if ($('#dlg')) return closeDialog(); if ($('#pop')) return closePop(); if (typing) return e.target.blur(); if (S.meta) { S.meta = null; return refreshInbox({ top: true }); } if (S.edit) { cancelEdit(); return repaint(); } if (S.rejecting) { S.rejecting = false; return refreshInbox(); } if (S.openId) { S.openId = null; return repaint(); } return; }
  if (typing || e.metaKey || e.ctrlKey || e.altKey || $('#dlg') || S.meta) return;
  if (e.key === '?') { e.preventDefault(); return showShortcuts(); }
  const k = e.key.toLowerCase();
  if (S.view === 'arsiv' && S.openId && k === 'e' && !S.edit) { e.preventDefault(); startEdit(S.openId); return; }
  if (S.view === 'arsiv' && S.openId && !S.edit) { if (k === 'j' || e.key === 'ArrowDown') { e.preventDefault(); step(1); } else if (k === 'k' || e.key === 'ArrowUp') { e.preventDefault(); step(-1); } return; }
  if (S.view !== 'gelen') return;
  if (k === '/') { e.preventDefault(); $('#q').focus(); }
  else if (k === 'j' || e.key === 'ArrowDown') { if (tag === 'button' && e.key === 'ArrowDown') return; e.preventDefault(); step(1); }
  else if (k === 'k' || e.key === 'ArrowUp') { if (tag === 'button' && e.key === 'ArrowUp') return; e.preventDefault(); step(-1); }
  else if (e.key === 'Enter' && S.focusId && !S.openId && tag !== 'button' && tag !== 'a') { e.preventDefault(); openRecord(S.focusId); }
  else if (k === 'x' && (S.openId || S.focusId) && DECISION_Q.includes(S.queue)) { const id = S.openId || S.focusId; S.selected.has(id) ? S.selected.delete(id) : S.selected.add(id); refreshInbox(); }
  else if (k === 'a' && S.openId) { const r = byId(S.openId); if (r && isPending(r) && r.sel.length && canDecide()) approve(r.id); }
  else if (k === 'r' && S.openId) { const r = byId(S.openId); if (r && isPending(r) && canDecide()) { S.rejecting = true; refreshInbox(); } }
});
addEventListener('resize', () => { closePop(); endDetailResize(); syncDetailSize(); });
['dragover', 'dragleave', 'drop'].forEach(ev => document.addEventListener(ev, e => {
  const z = e.target.closest && e.target.closest('#xlDrop'); if (!z) return; e.preventDefault();
  z.classList.toggle('drag', ev === 'dragover');
  if (ev === 'drop' && e.dataTransfer.files[0]) { const f = e.dataTransfer.files[0]; if (!/\.(xlsx|xlsm|csv|txt)$/i.test(f.name)) { toast('Yalnızca .xlsx ya da .csv dosyası yükleyin.', { err: true }); return; } S.xl.file = f; S.xl.res = null; S.xl.err = null; renderAll(); }
}));
addEventListener('scroll', () => { if (S.pop) closePop(); }, { passive: true });
setInterval(() => { if (S.view === 'yonetim' && S.adminTab === 'hat' && S.auto && !document.hidden && !$('#dlg')) loadAdmin('hat'); }, 30000);

/* ---------- yönlendirme ve açılış */
const ROUTES = { gelen: ['gelen'], arsiv: ['arsiv'], karsilastirma: ['yonetim', 'excel'], yonetim: ['yonetim', 'genel'], hat: ['yonetim', 'hat'], kaynaklar: ['yonetim', 'kaynaklar'], ayarlar: ['yonetim', 'ayarlar'], bakim: ['yonetim', 'bakim'] };
function go(hash) { if (location.hash.slice(1) === hash) route(); else location.hash = hash; }
function route() {
  const h = location.hash.slice(1); let r = ROUTES[h] || ROUTES.gelen;
  if (r[0] === 'yonetim' && !isAdmin()) r = ROUTES.gelen;
  const changed = S.view !== r[0] || (r[1] && S.adminTab !== r[1]);
  if (S.view !== r[0]) { cancelEdit(); S.meta = null; S.openId = null; S.rejecting = false; S.dTab = 'ozet'; }
  S.view = r[0]; if (r[1]) S.adminTab = r[1];
  renderAll(); if (changed) scrollTo(0, 0);
  if (S.view === 'gelen') { if (changed || S.list.loading) reloadAll(); }
  else if (S.view === 'arsiv') { loadArchive(); loadArchiveStats(); }
  else if (S.view === 'yonetim') { if (S.adminTab !== 'hat') S.node = null; loadAdmin(S.adminTab); }
  if (S.pendingOpen) { const id = S.pendingOpen; S.pendingOpen = null; openRecord(id); }
}
addEventListener('hashchange', route);
document.documentElement.lang = 'tr';
async function boot() {
  $('#app').innerHTML = '<div class="empty" style="padding:80px 16px" aria-busy="true"><b>Yükleniyor…</b></div>';
  try {
    await loadRefs();
    await Promise.all([loadStats().catch(() => {}), isAdmin() ? loadHealth() : Promise.resolve(), isAdmin() ? GET('/admin/alerts', { status: 'open' }).then(a => { S.alerts = a; }).catch(() => {}) : Promise.resolve()]);
  } catch (e) {
    $('#app').innerHTML = `<div class="empty" style="padding:80px 16px"><b>Portal açılamadı</b><span>${esc(errMsg(e))}</span><button class="btn" onclick="location.reload()">Yeniden dene</button></div>`; return;
  }
  route();
}
boot();
