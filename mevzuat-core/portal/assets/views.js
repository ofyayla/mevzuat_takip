'use strict';
/* ============================================================================================================
   Uygulama çubuğu, arşiv, Excel karşılaştırma ve yönetim ekranları (hepsi sunucu verisiyle çizilir).
   ============================================================================================================ */
S.adm = {};            // yönetim sekmelerinin sunucu verisi (sekme açılınca yüklenir)
S.alerts = null;       // açık alarmlar (yalnızca yönetici)
S.pe = { defaults: null, runs: [], records: [], selected: {}, detail: null, search: '', error: null, loading: false };
S.xl = { file: null, tTitle: 85, tText: 65, running: false, res: null, tab: 'tum', q: '', method: 'tum', err: null, selections: {}, reviewNo: null };
S.set = null; S.setSaved = null; S.setErr = {}; S.scansOpen = false; S.scanFilter = 'tum'; S.node = null; S.tech = false; S.hatLabels = false; S.auto = true; S.refreshed = null;

const openAlarms = () => (S.alerts || []).length;
const initials = n => String(n || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toLocaleUpperCase('tr');
const healthBad = () => ((Boot.health && Boot.health.sources) || []).filter(s => s.status !== 'ok').length;

function appbar() {
  const c = counts(); const me = Boot.me || { name: '—', title: '' };
  const nav = [['gelen', 'Gelen kutusu', 'inbox', c.bekleyen], ['arsiv', 'Arşiv', 'archive'], ...(isAdmin() ? [['yonetim', 'Yönetim', 'settings']] : [])];
  const bad = healthBad(); const al = openAlarms();
  return `<header class="appbar"><div class="appbar-in gut">
    <a class="brand" href="#gelen"><span class="brand-mark">${ic('shield')}</span><span class="brand-txt"><b>Mevzuat Takip</b><small>Mevzuat ve Uyum Başkanlığı</small></span></a>
    <nav class="nav" aria-label="Ana menü">${nav.map(([k, l, i, b]) => `<a href="#${k}" ${S.view === k ? 'aria-current="page"' : ''}>${ic(i)}<span>${l}</span>${b ? `<span class="nav-b" title="Karar bekleyen">${N(b)}</span>` : ''}</a>`).join('')}</nav>
    <div class="appbar-r">${bad && isAdmin() ? `<a class="health" href="#kaynaklar" title="${bad} kaynakta sorun var"><span class="dot"></span><span class="health-txt">${bad} kaynakta sorun</span></a>` : ''}
      ${isAdmin() ? `<button class="btn btn-ghost icon-btn bell" data-act="pop-bell" aria-label="Alarmlar, ${al} açık">${ic('bell')}${al ? `<span class="bell-n">${al}</span>` : ''}</button>` : ''}
      <button class="avatar" data-act="pop-user" aria-label="Hesap: ${esc(me.name)}">${esc(initials(me.name))}</button></div></div></header>`;
}

/* ---------- arşiv: uzman kararları ve YZ önerisi isabeti */
const ARSIV_FILTERS = [['onay', 'Onaylanan'], ['degisik', 'Birimi değiştirilerek onaylanan'], ['red', 'Reddedilen'], ['irr', 'YZ\'nin elediği'], ['baseline', 'İlk tarama']];
let arSeq = 0;
async function loadArchive() {
  const seq = ++arSeq; S.ar.loading = true; S.ar.err = null; paintArchive();
  try {
    const base = { q: S.arsivQ.trim(), page: S.arsivPage, page_size: 25 };
    let d, items;
    if (S.arsivF === 'irr' || S.arsivF === 'baseline') {
      d = await GET('/archive/regulations', { ...base, kind: S.arsivF === 'baseline' ? 'baseline' : 'irrelevant', sort: 'detected_at', direction: 'desc' });
      items = d.items.map(upsertArchive);
    }
    else { d = await GET('/regulations', { ...base, status: S.arsivF === 'red' ? 'Reddedildi' : 'Onaylandı', unit_changed: S.arsivF === 'degisik', sort: 'decided_at', direction: 'desc' }); items = d.items.map(it => upsert(it, { resetSel: true })); }
    if (seq !== arSeq) return;
    S.ar = { ids: items.map(r => r.id), total: d.total, loading: false, err: null };
  } catch (e) { if (seq !== arSeq) return; S.ar = { ids: [], total: 0, loading: false, err: errMsg(e) }; }
  paintArchive();
}
async function loadArchiveStats() {
  try {
    const [ds, archive] = await Promise.all([GET('/regulations/decision-stats'), GET('/archive/regulations', { kind: 'irrelevant', page_size: 1 })]);
    S.arStats = { ...ds, irrAll: (archive.counts || {}).irrelevant ?? archive.total, baselineAll: (archive.counts || {}).baseline };
  } catch (_) { S.arStats = null; }
  if (S.view === 'arsiv') paintArchive();
}
function paintArchive() { if (S.view === 'arsiv' && $('#arsivMain')) { $('#arsivMain').innerHTML = arsivBody(); const det = $('#detail'); if (det) { det.hidden = !S.openId; det.innerHTML = S.openId ? detailHtml() : ''; } $('#inbox') && $('#inbox').classList.toggle('has-detail', !!S.openId); syncDetailSize(); } }
function arsivBody() {
  const af = S.arsivF; const st = S.arStats; const { ids, total, loading, err } = S.ar; const it = ids.map(byId).filter(Boolean);
  const tile = (l, v, s, cl = '') => `<div class="card"><span class="kpi-l">${l}</span><span class="kpi-v num ${cl}">${v}</span><span class="kpi-s">${s}</span></div>`;
  const acc = st && st.unitAccuracy != null ? Math.round(st.unitAccuracy * 100) : null;
  const num = { onay: st && st.approved, degisik: st && st.approvedWithChange, red: st && st.rejected, irr: st && st.irrAll, baseline: st && st.baselineAll };
  const chip = (k, l) => `<button class="btn btn-sm ${af === k ? 'fbtn on' : ''}" data-act="arsivf" data-f="${k}">${l} ${num[k] != null ? `<span class="muted num">${N(num[k])}</span>` : ''}</button>`;
  const pages = Math.max(1, Math.ceil(total / 25)); const start = (S.arsivPage - 1) * 25;
  const row = r => {
    let tg, a, b, who;
    if (r.kind === 'irr') { tg = ['tag-plain', 'YZ eledi']; a = r.why ? trunc(r.why, 140) : 'Gerekçe kaydedilmemiş'; who = r.review ? [r.review.by, pdt(r.review.at)] : ['—', null]; if (r.review) tg = ['tag-ok', r.review.verdict === 'confirm' ? 'İlgisiz doğrulandı' : 'İlgili sayıldı']; b = `İlgililik skoru ${dec(r.relScore)}`; }
    else if (r.kind === 'baseline') { tg = ['tag-plain', 'İlk tarama']; a = 'YZ ilgililik analizine girmedi'; b = 'Kanalın ilk taramasında zaten yayındaydı'; who = ['—', null]; }
    else if (r.status === 'Onaylandı') { const ch = approvedChanged(r); tg = [ch ? 'tag-warn' : 'tag-ok', ch ? 'Değiştirilerek onaylandı' : 'Onaylandı']; a = r.units.map(u => u.n).join(', ') || 'Birim atanmadı'; b = ch ? `YZ önerisi: ${r.aiUnits.map(u => u.n).join(', ')}` : ''; who = r.decided ? [r.decided.by, r.decided.at] : ['—', null]; }
    else { tg = ['tag-crit', 'Reddedildi']; a = 'Gerekçe ayrıntıda'; b = ''; who = r.decided ? [r.decided.by, r.decided.at] : ['—', null]; }
    return `<tr class="${S.openId === r.id ? 'is-open' : ''}" data-act="open" data-id="${r.id}"><td class="c-main" style="min-width:240px"><div class="ttl" style="color:var(--fg)">${esc(r.title)}</div><div class="meta">${srcAbbr(r.src)}${r.type && r.type !== '—' ? `<span>${esc(r.type)}</span>` : ''}${r.pub ? `<span>Yayım ${fDs(r.pub)}</span>` : ''}${r.revCount ? `<span class="tag tag-plain" title="Karar sonradan değiştirildi">Düzenlendi${r.revCount > 1 ? ' ×' + r.revCount : ''}</span>` : ''}${r.sev ? `<span class="m-only">${sevPill(r.sev)}</span>` : ''}</div></td>
      <td><span class="tag ${tg[0]}">${tg[1]}</span></td><td class="c-unit" style="font-size:12.5px"><span class="u-n" style="white-space:normal">${esc(a)}</span>${b ? `<div class="u-s">${esc(b)}</div>` : ''}</td>
      <td class="c-eff" style="font-size:12.5px">${esc(who[0] || '—')}<div class="u-s">${who[1] ? fDT(who[1]) : ''}</div></td><td class="c-go">${ic('chev-r')}</td></tr>`;
  };
  return `<div class="tk-kpis">${tile('Onaylanan', st ? N(st.approved) : '…', 'birim önerisi onaylandı')}${tile('Birim önerisi isabeti', acc == null ? '—' : '%' + acc, st ? `${N(st.approvedWithChange)} kayıtta uzman birimi değiştirdi` : '', acc != null && acc < 70 ? 'warn' : '')}${tile('Reddedilen', st ? N(st.rejected) : '…', 'kayıt reddedildi')}${tile('İlgisiz doğrulandı', st ? N(st.irrelevantConfirmed) : '…', 'örneklem kontrolünden')}</div>
    <div class="fbar"><label class="search"><span class="sr">Arşivde ara</span>${ic('search')}<input id="arsivQ" type="search" placeholder="Arşivde ara" value="${esc(S.arsivQ)}"></label><div class="fbtns">${ARSIV_FILTERS.map(f => chip(f[0], f[1])).join('')}</div></div>
    ${err ? `<div class="empty card"><b>Liste yüklenemedi</b><span>${esc(err)}</span><button class="btn" data-act="reload">Yeniden dene</button></div>` :
      loading && !it.length ? '<div class="empty card" aria-busy="true"><b>Yükleniyor…</b></div>' :
      it.length ? `<div class="tbl-wrap ${loading ? 'is-loading' : ''}" style="margin-bottom:12px"><table class="tbl"><thead><tr><th class="c-main">Düzenleme</th><th>${af === 'baseline' ? 'Durum' : 'Karar'}</th><th class="c-unit">${af === 'irr' ? 'YZ gerekçesi' : af === 'baseline' ? 'Açıklama' : 'Birim / gerekçe'}</th><th class="c-eff">Karar veren</th><th class="c-go"></th></tr></thead><tbody>${it.map(row).join('')}</tbody></table></div>
        <div class="pager" style="margin-bottom:24px"><span class="num">Gösterilen ${N(start + 1)}–${N(start + it.length)} / ${N(total)}</span><span></span><div class="pg"><button class="btn btn-sm" data-act="apage" data-p="${S.arsivPage - 1}" ${S.arsivPage <= 1 ? 'disabled' : ''} aria-label="Önceki sayfa">${ic('chev-l')}</button><span class="num muted">${S.arsivPage} / ${pages}</span><button class="btn btn-sm" data-act="apage" data-p="${S.arsivPage + 1}" ${S.arsivPage >= pages ? 'disabled' : ''} aria-label="Sonraki sayfa">${ic('chev-r')}</button></div></div>`
        : '<div class="empty card"><b>Sonuç yok</b></div>'}`;
}
function viewArsiv() {
  return `<div class="inbox gut ${S.openId ? 'has-detail' : ''}" id="inbox"><section class="list-pane" aria-label="Arşiv listesi"><div class="ph"><div><h1>Arşiv</h1><p class="lede">Karara bağlanmış düzenlemeler, ilk taramada YZ'ye gönderilmeyen kayıtlar ve YZ'nin ilgisiz buldukları. Ayrıntısını görmek için bir kayda tıklayın; karar verilmiş kayıtlar düzenlenebilir ya da Gelen kutusuna geri alınabilir.</p></div></div>
    <div id="arsivMain">${arsivBody()}</div></section>
    <button class="scrim" data-act="close" aria-label="Ayrıntıyı kapat" tabindex="-1"></button>
    <aside class="detail-pane" id="detail" aria-label="Kayıt ayrıntısı" ${S.openId ? '' : 'hidden'}>${S.openId ? detailHtml() : ''}</aside></div>`;
}

/* ---------- Excel karşılaştırma: Başkanlığın listesi ↔ sistem kayıtları (sunucu karşılaştırır, hiçbir şey kaydetmez) */
const XL_RES = { relevant: ['tag-ok', 'Relevant'], irrelevant: ['tag-crit', 'Irrelevant'], missing: ['tag-warn', 'Bulunamadı'] };
function xlParams() { return { title_threshold: S.xl.tTitle, text_threshold: S.xl.tText }; }
function xlSetFile(file) {
  Object.assign(S.xl, { file, res: null, err: null, selections: {}, reviewNo: null });
  renderAll();
}
function xlForm(selections) {
  const fd = new FormData(); fd.append('file', S.xl.file);
  fd.append('selections', JSON.stringify(Object.entries(selections).map(([row, id]) => ({ row: +row, regulation_id: id }))));
  return fd;
}
async function xlSelect(no, id) {
  const selections = { ...S.xl.selections };
  if (id) selections[no] = +id; else delete selections[no];
  await xlRun(selections, true);
}
async function xlRun(selections = S.xl.selections, preserveTab = false) {
  const x = S.xl; if (!x.file || x.running) return;
  const a = +x.tTitle, b = +x.tText; if (!(a >= 50 && a <= 100) || !(b >= 50 && b <= 100)) { toast('Eşikler 50 ile 100 arasında olmalı.', { err: true }); return; }
  x.running = true; x.err = null; renderAll();
  try {
    const r = await api('POST', '/evaluation/excel-compare', { query: xlParams(), form: xlForm(selections) });
    if (!r.data.summary || typeof r.data.summary.notFound !== 'number' || typeof r.data.summary.review !== 'number') throw new Error('Portal ve API sürümleri uyumsuz. İkisi birlikte güncellenmeli; ardından sayfayı yenileyin.');
    if (Object.entries(selections).some(([no, id]) => !(r.data.items || []).some(row => row.no === +no && row.selectedRegulationId === id && row.method === 'manual_selection'))) throw new Error('Sunucu eşleşme onayını doğrulamadı. Sonuç değiştirilmedi; yeniden inceleyin.');
    x.res = r.data; x.selections = selections; if (!preserveTab) x.tab = 'tum'; toast(`${N(r.data.summary.total)} satır karşılaştırıldı.`);
  }
  catch (e) { x.err = errMsg(e); }
  x.running = false; renderAll();
}
async function xlExport() {
  const x = S.xl; if (!x.file) return;
  try { const { blob, res } = await api('POST', '/evaluation/excel-compare', { query: { ...xlParams(), format: 'xlsx' }, form: xlForm(x.selections), blob: true });
    const m = /filename="?([^";]+)"?/i.exec(res.headers.get('Content-Disposition') || ''); saveBlob(blob, m ? m[1] : 'karsilastirma.xlsx'); }
  catch (e) { toast(errMsg(e), { err: true }); }
}
const P1 = v => v == null ? '—' : dec(Math.floor(v * 1000) / 10, 1);
function admExcel() {
  const x = S.xl; const res = x.res;
  const upload = `<section class="card xl-up"><div class="xl-drop ${x.drag ? 'drag' : ''}" id="xlDrop">${ic('download')}<div><b>${x.file ? esc(x.file.name) : 'Excel dosyasını sürükleyin ya da seçin'}</b><span>${x.file ? `${(x.file.size / 1024).toFixed(0)} KB · dosya yalnızca karşılaştırma için gönderilir, kaydedilmez` : 'Beklenen sütunlar: Referans No, Konu, Gönderen/Düzenleyici Kurum, Risk Seviyesi, Oluşturulma Tarihi, Metin (.xlsx, .csv; en fazla 5 MB)'}</span></div>
      <label class="btn btn-sm" for="xlFile">${x.file ? 'Başka dosya seç' : 'Dosya seç'}</label><input type="file" id="xlFile" accept=".xlsx,.xlsm,.csv,.txt" ${x.running ? 'disabled' : ''} hidden></div>
    <div class="xl-ctl"><label class="field">Başlık eşiği<span class="xl-num"><input class="input num" id="xlT" value="${x.tTitle}" inputmode="numeric"><span>%</span></span></label>
      <label class="field">Metin eşiği<span class="xl-num"><input class="input num" id="xlX" value="${x.tText}" inputmode="numeric"><span>%</span></span></label>
      <p class="hint" style="margin:0;flex:1 1 220px">Önce bağlantı, sonra kurum ve belge numarası doğrulanır. Belirsiz kimlikler incelemeye ayrılır. Numara tanınmadığında başlık ve metin benzerliği kullanılır. Eşiği değiştirince yeniden karşılaştırın.</p>
      <button class="btn btn-primary" data-act="xl-run" ${x.file && !x.running ? '' : 'disabled'}>${x.running ? 'Karşılaştırılıyor…' : 'Karşılaştır'}</button>
      <button class="btn" data-act="xl-export" ${res && !x.running ? '' : 'disabled'}>${ic('download')}Sonucu Excel'e aktar</button></div><p class="hint">Manuel eşleşmeler yalnız bu karşılaştırmada ve Excel çıktısında kullanılır; sayfa yenilenince silinir. YZ etiketini değiştirmez.</p>${x.err ? `<span class="field-err" style="margin-top:8px;display:block">${esc(x.err)}</span>` : ''}</section>`;
  if (!res) return upload + `<div class="empty card" style="margin-top:16px">${ic('doc')}<b>Henüz karşılaştırma yok</b><span>Dosyayı yükleyip Karşılaştır'a basın. Karşılaştırma hiçbir kaydı değiştirmez.</span></div>`;
  const s = res.summary, pr = res.precision, sa = res.severityAgreement; const W = v => (s.total ? v / s.total * 100 : 0).toFixed(2) + '%';
  const kpi = (l, v, sub, cl = '', tab) => `<${tab ? `button class="kpi xl-kpi" data-act="xl-tab" data-t="${tab}" aria-pressed="${x.tab === tab}"` : 'div class="kpi xl-kpi"'}><span class="kpi-l">${l}</span><span class="kpi-v num ${cl}">${v}</span><span class="kpi-s">${sub}</span></${tab ? 'button' : 'div'}>`;
  const tabs = [['tum', 'Tüm satırlar', s.total], ['relevant', 'Relevant', s.relevant], ['irrelevant', 'Irrelevant', s.irrelevant], ['unmatched', 'Eşleşme bulunamadı', s.notFound], ['review', 'Aday incelemesi', s.review], ['unprocessed', 'Bulundu; YZ sonucu yok', s.unprocessed], ['fazla', 'Fazla uyarılar', pr.extra]];
  return `${upload}
  <div class="xl-grid">
    <section class="card xl-sec"><div class="sec-h"><h2>Excel'deki ${N(s.total)} satır sistemde ne oldu?</h2><p>Kapsama</p></div>
      <div class="xl-bar" role="img" aria-label="Relevant ${s.relevant}, Irrelevant ${s.irrelevant}, Aday incelemesi ${s.review}, Eşleşme bulunamadı ${s.notFound}"><i class="b-ok" style="width:${W(s.relevant)}"></i><i class="b-crit" style="width:${W(s.irrelevant)}"></i><i class="b-warn" style="width:${W(s.notFound + s.review + s.unprocessed)}"></i></div>
      <div class="kpis xl-kpis">${kpi('Relevant', N(s.relevant), 'Sistem buldu, YZ ilgili dedi', 'h-ok', 'relevant')}${kpi('Irrelevant', N(s.irrelevant), 'Sistem buldu, YZ eledi', 'crit', 'irrelevant')}${kpi('Eşleşme bulunamadı', N(s.notFound), 'Crawl edilmediğinin kesin kanıtı değildir', 'warn', 'unmatched')}${kpi('Aday incelemesi', N(s.review), 'Karşılaştırıp kabul edebilirsiniz', 'warn', 'review')}${kpi('Bulundu; YZ sonucu yok', N(s.unprocessed), 'İlk tarama veya işleme bekleyen', '', 'unprocessed')}</div>
      <div class="xl-rates"><div><b class="num">%${P1(s.recall)}</b><span>İlgili bulunan / Excel toplamı</span></div><div><b class="num">%${P1(s.matchRate)}</b><span>Doğrulanan / onaylanan eşleşme · ilk tarama dahil</span></div><div><b class="num">${sa.exact == null ? '—' : '%' + P1(sa.exact)}</b><span>Risk seviyesi uyumu · ${N(sa.n)} eşleşen kayıt üzerinden</span></div></div></section>
    <section class="card xl-sec"><div class="sec-h"><h2>Portalda görünenler listede var mı?</h2><p>Gürültü</p></div>
      <div class="kpis xl-kpis">${kpi('Portalda görünen', N(pr.shown), pr.period && pr.period.from ? `${fD(pd(pr.period.from))} – ${fD(pd(pr.period.to))}` : '')}${kpi('Fazla uyarı', N(pr.extra), 'Portalda var, listede yok', '', 'fazla')}${kpi('Precision', pr.precision == null ? '—' : '%' + P1(pr.precision), 'Listede olan / görünen', pr.precision != null && pr.precision < .2 ? 'crit' : '')}</div>
      <p class="hint" style="margin:10px 0 0">Liste yalnızca Başkanlığın aksiyon aldığı düzenlemeleri içeriyorsa precision doğal olarak düşük çıkar. Fazla uyarıları inceleyip gerçekten gereksiz olanları belirlemek, ilgililik eşiğini ayarlamak için en iyi veridir.</p></section>
  </div>
  <div class="tabs" role="tablist" style="margin:18px 0 12px">${tabs.map(t => `<button class="tab" role="tab" data-act="xl-tab" data-t="${t[0]}" aria-selected="${x.tab === t[0]}">${t[1]}<span class="cnt">${N(t[2])}</span></button>`).join('')}</div>
  ${x.tab === 'fazla' ? xlExtra(res) : xlTable(res)}`;
}
function xlTable(res) {
  const x = S.xl; const q = lower(x.q.trim());
  const methods = [...new Set(res.items.map(i => i.methodLabel).filter(Boolean))];
  const list = res.items.filter(r => x.tab === 'tum' || r.group === x.tab || r.matchState === x.tab)
    .filter(r => x.method === 'tum' || (x.method === 'risk' ? r.regulation && r.severity && r.regulation.severity && r.severity !== r.regulation.severity : r.methodLabel === x.method))
    .filter(r => !q || lower(`${r.title} ${r.ref} ${r.issuer}`).includes(q));
  return `<div class="fbar"><label class="search" style="max-width:none"><span class="sr">Ara</span>${ic('search')}<input id="xlQ" type="search" placeholder="Konu, referans no veya kurum ara" value="${esc(x.q)}"></label>
    <select class="select" id="xlM" aria-label="Eşleşme yöntemi" style="width:auto"><option value="tum">Tüm eşleşme yöntemleri</option>${methods.map(v => `<option value="${esc(v)}" ${x.method === v ? 'selected' : ''}>${esc(v)}</option>`).join('')}<option value="risk" ${x.method === 'risk' ? 'selected' : ''}>Risk seviyesi uyuşmayanlar</option></select>
    <span class="muted num" style="font-size:12.5px">${list.length} satır</span></div>
  ${list.length ? `<div class="tbl-wrap" style="margin-bottom:24px"><table class="tbl static xl-tbl"><thead><tr><th class="num">#</th><th>Excel satırı</th><th>Sonuç</th><th>Eşleşen sistem kaydı</th><th>Risk (Excel / sistem)</th><th style="text-align:right">Skor</th></tr></thead><tbody>
  ${list.map(r => { const t = r.category === 'reference_review' ? ['tag-warn', r.matchState === 'unmatched' ? 'Eşleşme bulunamadı' : 'İnceleme gerekli'] : r.category === 'reference_raw_only' ? ['tag-warn', 'Toplandı'] : r.matchState === 'unprocessed' ? ['tag-warn', 'Bulundu; YZ sonucu yok'] : XL_RES[r.group] || XL_RES.missing; const g = r.regulation; const same = r.severity && g && g.severity && r.severity === g.severity;
    return `<tr><td class="num muted">${r.no}</td>
      <td style="min-width:260px"><div class="ttl" style="color:var(--fg);font-weight:500">${esc(r.title)}</div><div class="meta">${r.ref ? `<span class="mono">${esc(r.ref)}</span>` : ''}<span>${esc(r.issuer || '—')}</span>${r.createdAt ? `<span>${fD(pd(r.createdAt))}</span>` : ''}</div></td>
      <td style="min-width:130px"><span class="tag ${t[0]}">${t[1]}</span><div class="u-s">${esc(r.categoryLabel || '')}</div>${xlReference(r.reference)}${(r.candidates || []).length ? `<button class="btn btn-sm" data-act="xl-review" data-no="${r.no}">Adayları karşılaştır (${r.candidates.length})</button>` : ''}${r.selectedRegulationId ? `<div class="u-s">Bu karşılaştırmada kullanıcı onaylı</div><button class="linkbtn" data-act="xl-unselect" data-no="${r.no}" ${x.running ? 'disabled' : ''}>Onayı geri al</button>` : ''}</td>
      <td style="min-width:240px">${g ? `<div style="font-size:13px">${esc(g.title)}</div><div class="u-s">#${g.id}${g.publishDate ? ' · ' + fD(pd(g.publishDate)) : ''}${g.relevanceScore != null && g.isRelevant === false ? ` · ilgililik ${dec(g.relevanceScore)}` : ''} · <button class="linkbtn" style="padding:0;font-size:11.5px" data-act="xl-open" data-id="${g.id}">Kaydı aç</button></div>` : `<span class="muted" style="font-size:12.5px">${r.rawDocumentId ? 'Ham belge #' + esc(r.rawDocumentId) + ' · sistem kaydı yok' : 'Eşleşen kayıt yok'}</span>`}</td>
      <td style="white-space:nowrap">${sevPill(r.severity)}${g && g.severity ? ` <span class="${same ? 'h-ok' : 'h-warn'}" title="${same ? 'Uyumlu' : 'Uyumsuz'}">${same ? '=' : '≠'}</span> ${sevPill(g.severity)}` : ''}</td>
      <td style="text-align:right;white-space:nowrap"><b class="num">${r.score != null ? dec(r.score) : '—'}</b><div class="u-s">${esc(r.methodLabel || '')}</div></td></tr>${x.reviewNo === r.no ? `<tr><td colspan="6">${xlComparison(r)}</td></tr>` : ''}`; }).join('')}
  </tbody></table></div>` : '<div class="empty card"><b>Bu süzgeçte satır yok</b></div>'}`;
}
function xlComparison(row) {
  return `<section class="card" aria-label="Excel ve aday karşılaştırması"><h3>Excel satırı ${row.no} — aday karşılaştırması</h3><p class="hint">Numara, karar tarihi, konu ve kapsamı karşılaştırın. Kabul yalnız eşleşmeyi onaylar; YZ ilgililik etiketini değiştirmez.</p>
    <div style="display:flex;flex-wrap:wrap;gap:16px"><div style="flex:1 1 280px"><b>Excel: ${esc(row.title)}</b><p>${esc(row.issuer || '')} · ${esc(row.createdAt || '')}</p><pre style="white-space:pre-wrap;max-height:360px;overflow:auto">${esc(row.textExcerpt || 'Excel metni yok.')}</pre></div>
    <div style="flex:1 1 360px">${(row.candidates || []).map(c => { const r = c.regulation; return `<article class="card" style="margin-bottom:12px;padding:12px"><b>#${r.id} · ${esc(r.title)}</b><p>${esc(r.issuer || 'Kurum bilinmiyor')} · Yayım: ${esc(r.publishDate || '—')} · ${esc(r.status)}</p><p class="hint">${c.evidence === 'similarity' ? 'Yalnız benzerlik adayı; belge kimliği doğrulanamadı.' : 'Numara adayı; birincil karar mı, atıf mı olduğunu kontrol edin.'}</p><pre style="white-space:pre-wrap;max-height:360px;overflow:auto">${esc(c.textExcerpt || 'Ham metin yok; kabul etmeden kaynağı kontrol edin.')}</pre><button class="linkbtn" data-act="xl-open" data-id="${r.id}">Kaydı aç</button> <button class="btn btn-primary" data-act="xl-select" data-no="${row.no}" data-id="${r.id}" ${S.xl.running || row.selectedRegulationId === r.id ? 'disabled' : ''}>${row.selectedRegulationId === r.id ? 'Bu aday kabul edildi' : 'Bu eşleşmeyi kabul et'}</button></article>`; }).join('')}</div></div></section>`;
}
function xlReference(ref) {
  if (!ref) return '';
  const labels = { matched: 'Kimlik doğrulandı', ambiguous: 'Birden çok aday', citation: 'Yalnızca atıf / inceleme gerekli', conflict: 'Kimlik / tarih çelişkisi', unverified: 'Kimlik doğrulanamadı' };
  return `<div class="u-s">${esc(ref.number)}${ref.date ? ' · ' + esc(ref.date) : ''}<br>${esc(labels[ref.status] || ref.status)}${(ref.candidateIds || []).length ? '<br>Aday kayıtlar: ' + ref.candidateIds.map(id => `<button class="linkbtn" data-act="xl-open" data-id="${esc(id)}">#${esc(id)}</button>`).join(', ') : ''}</div>`;
}
function xlExtra(res) {
  const list = [...res.extra].sort((a, b) => SEVS.indexOf(a.severity) - SEVS.indexOf(b.severity) || (b.publishDate || '').localeCompare(a.publishDate || ''));
  return `<p class="qnote">Portalda görünüp Excel listesinde olmayan ${N(res.extra.length)} kayıt, önem sırasına göre. Yüksek önemli olup listede olmayanlar ya Başkanlığın kaçırdığı ya da YZ'nin önemini abarttığı kayıtlardır.</p>
    <div class="tbl-wrap" style="margin-bottom:24px"><table class="tbl xl-tbl"><thead><tr><th>Sistem kaydı</th><th>Önem</th><th>Durum</th><th class="c-go"></th></tr></thead><tbody>
    ${list.slice(0, 200).map(r => `<tr data-act="xl-open" data-id="${r.id}"><td style="min-width:260px"><div class="ttl" style="color:var(--fg)">${esc(r.title)}</div><div class="meta"><span>${esc(r.issuer || '—')}</span>${r.publishDate ? `<span>Yayım ${fDs(pd(r.publishDate))}</span>` : ''}<span class="mono">#${r.id}</span></div></td><td>${sevPill(r.severity)}</td><td style="font-size:12.5px;white-space:nowrap">${esc(r.status)}</td><td class="c-go">${ic('chev-r')}</td></tr>`).join('')}
    </tbody></table></div>${list.length > 200 ? `<p class="hint">İlk 200 kayıt gösteriliyor; tamamı için Excel'e aktarın.</p>` : ''}`;
}

/* ---------- yönetim kabuğu */
const ADM = [['genel', 'Genel bakış', 'yonetim'], ['hat', 'İşleme hattı', 'hat'], ['kaynaklar', 'Kaynaklar ve taramalar', 'kaynaklar'], ['ayarlar', 'YZ ayarları', 'ayarlar'], ['excel', 'Excel karşılaştırma', 'karsilastirma'], ['deney', 'Prompt deneyi', 'prompt-deneyi'], ['bakim', 'Bakım ve denetim', 'bakim']];
const ADM_LEDE = { genel: 'Sistem sağlığı, açık alarmlar ve kaynak durumu. Bu bölümü yalnızca yönetici rolündekiler görür.', hat: 'Kayıtların toplama aşamasından uzman kararına kadar geçtiği adımlar. Kutudaki büyük sayı, o adımda şu an bekleyen kayıt sayısıdır.', kaynaklar: 'Her kaynağın son taraması, sağlığı ve sonraki planı. Taramayı elle başlatabilir ya da geçmiş tarih aralığını yeniden tarayabilirsiniz.', ayarlar: 'İlgililik, güven ve mükerrer tespiti eşikleri. Değişiklikler gerekçesiyle kaydedilir ve etkisi önceden gösterilir.', excel: 'Başkanlığın mevzuat listesini yükleyin; her satır sistem kayıtlarıyla eşleştirilir. Ters yönde, aynı dönemde portalda görünüp listede olmayan kayıtlar da listelenir. Karşılaştırma hiçbir şeyi kaydetmez.', deney: 'Aynı kayıtları ve ayarları mevcut A promptu ile düzenlenebilir B promptunda çalıştırın. Deney sonuçları kalıcı saklanır; canlı ilgililik etiketleri değişmez.', bakim: 'Yeniden işleme, başarısız YZ kayıtları, baseline sınırı ve denetim izi.' };
function viewYonetim() {
  const t = ADM.find(a => a[0] === S.adminTab) || ADM[0];
  const body = S.admErr ? `<div class="empty card"><b>Veri yüklenemedi</b><span>${esc(S.admErr)}</span><button class="btn" data-act="adm-reload">Yeniden dene</button></div>` : !S.adm[t[0]] && t[0] !== 'excel' && t[0] !== 'deney' ? '<div class="empty card" aria-busy="true"><b>Yükleniyor…</b></div>' : { genel: admGenel, hat: admHat, kaynaklar: admKaynak, ayarlar: admAyar, excel: admExcel, deney: admPromptExperiment, bakim: admBakim }[t[0]]();
  return `<div class="page gut"><div class="crumb"><a href="#yonetim">Yönetim</a>${t[0] !== 'genel' ? ` › ${t[1]}` : ''}</div>
    <div class="ph"><div><h1>${t[0] === 'genel' ? 'Yönetim' : t[1]}</h1><p class="lede">${ADM_LEDE[t[0]]}</p></div></div>
    <div class="tabs adm-tabs">${ADM.map(a => `<a class="tab" href="#${a[2]}" ${a[0] === t[0] ? 'aria-current="page"' : ''}>${a[1]}${a[0] === 'genel' && openAlarms() ? `<span class="cnt h-crit">${openAlarms()}</span>` : ''}</a>`).join('')}</div>${body}</div>`;
}
const SEV_TAG = { critical: 'tag-crit', high: 'tag-crit', warning: 'tag-warn', medium: 'tag-warn', info: 'tag-plain', low: 'tag-plain', Yüksek: 'tag-crit', Orta: 'tag-warn', Düşük: 'tag-plain' };
const SEV_TXT = { critical: 'Kritik', high: 'Yüksek', warning: 'Uyarı', medium: 'Orta', info: 'Bilgi', low: 'Düşük' };
const H_ST = { ok: ['h-ok', 'Sağlıklı'], delayed: ['h-warn', 'Gecikmeli'], down: ['h-crit', 'Sorunlu'] };
function healthChart(series, { label, tone, target = null, percent = false, format = N }) {
  const data = series || []; const valid = data.filter(p => Number.isFinite(p.value));
  if (!valid.length) return '<div class="spark-empty">Bu dönem için ölçüm yok</div>';
  const max = percent ? 100 : Math.max(1, target || 0, ...valid.map(p => p.value)) * 1.12;
  const start = Date.parse(data[0].at); const end = Date.parse(data[data.length - 1].at);
  const x = p => end === start ? 150 : 4 + (Date.parse(p.at) - start) / (end - start) * 292;
  const y = v => 48 - v / max * 42;
  const point = p => `${x(p).toFixed(2)},${y(p.value).toFixed(2)}`;
  const segments = []; let segment = [];
  for (const p of data) {
    if (Number.isFinite(p.value)) segment.push(p);
    else if (segment.length) { segments.push(segment); segment = []; }
  }
  if (segment.length) segments.push(segment);
  const last = valid[valid.length - 1];
  const summary = `${label}. ${fDT(pdt(data[0].at))} – ${fDT(pdt(data[data.length - 1].at))}. Son ölçüm: ${format(last.value)}.${target == null ? '' : ` Hedef: ${format(target)}.`}`;
  return `<figure class="health-chart"><svg class="spark sp-${tone}" viewBox="0 0 300 54" role="img" aria-label="${esc(summary)}">
    <title>${esc(summary)}</title>
    ${target == null ? '' : `<line class="sp-target" x1="4" x2="296" y1="${y(target)}" y2="${y(target)}"><title>Hedef: ${esc(format(target))}</title></line>`}
    ${segments.map(s => s.length < 2 ? '' : `<path class="sp-area" d="M${x(s[0])},48 L${s.map(point).join(' L')} L${x(s[s.length - 1])},48 Z"/><polyline class="sp-line" points="${s.map(point).join(' ')}"/>`).join('')}
    ${valid.map(p => `<circle class="sp-end" cx="${x(p)}" cy="${y(p.value)}" r="${p === last ? 3 : 1.5}"><title>${esc(fDT(pdt(p.at)) + ': ' + format(p.value))}</title></circle>`).join('')}
    </svg><figcaption>${valid.length < 2 ? 'Geçmiş ölçümler birikiyor' : 'Boş aralıklar: ölçüm yok'}</figcaption></figure>`;
}
function admGenel() {
  const a = S.adm.genel; const al = S.alerts || []; const m = a.metrics; const llm = m.llm || {}; const pr = (Boot.stats || {}).preparing || {};
  const hs = (Boot.health && Boot.health.sources) || []; const bad = hs.filter(s => s.status !== 'ok');
  const sec = ms => ms == null ? '—' : dec(ms / 1000, 1) + ' sn';
  const scans = m.scans || {}; const queue = m.aiQueue || {}; const ingest = m.ingest || {};
  const llmTarget = llm.targetMs ?? 60000; const scanTarget = scans.targetRate ?? 98;
  const llmTone = llm.p95Ms > llmTarget ? 'crit' : llm.failed ? 'warn' : 'ok';
  const scanTone = scans.successRate != null && scans.successRate < scanTarget ? 'warn' : 'ok';
  const queueValues = (queue.series || []).filter(p => Number.isFinite(p.value)).map(p => p.value);
  const growing = queueValues.length > 1 && queueValues[queueValues.length - 1] > queueValues[0];
  const queueTone = pr.failed || growing ? 'warn' : 'ok'; const ingestTone = m.pendingIngest > 0 ? 'warn' : 'ok';
  const scanPct = v => '%' + dec(v, 1);
  const chart = (series, label, tone, extra = {}) => healthChart(series, { label, tone, ...extra });
  return `<div class="stack"><section><div class="sec-h"><h2>Açık alarmlar <span class="muted num">${al.length}</span></h2><p>Kapatılan alarmlar adınızla denetim izine yazılır.</p></div>
    <div class="card alarms">${al.length ? al.map(x => `<div class="alarm-row"><span class="tag ${SEV_TAG[x.severity] || 'tag-plain'}">${esc(SEV_TXT[x.severity] || x.severity)}</span><div class="msg">${esc(x.message)}<small>${esc(x.type)}${x.source ? ' · ' + esc(srcOf(x.source).a) : ''} · ${fDT(pdt(x.openedAt))}</small></div>
      <div class="acts"><a class="btn btn-sm" href="#${esc(x.link || 'yonetim')}">İncele</a><button class="btn btn-sm btn-ghost" data-act="alarm-close" data-id="${x.id}">Kapat</button></div></div>`).join('') : `<div class="empty">${ic('check')}<b>Açık alarm yok</b></div>`}</div></section>
    <section><div class="sec-h"><h2>Sistem sağlığı</h2><p>Kesikli çizgi hedef değeri gösterir.</p></div><div class="tiles">
      <div class="card tile"><div class="tl-h">LLM gecikmesi (P95, son ${llm.windowHours || 24} saat)<span class="hstat h-${llmTone}">${llm.p95Ms > llmTarget ? 'Hedefin üstünde' : llm.calls ? (llm.failed ? 'Hatalı çağrı var' : 'Normal') : 'Çağrı yok'}</span></div><span class="v num">${sec(llm.p95Ms)}</span><span class="t">Hedef ≤ ${sec(llmTarget)} · ortanca ${sec(llm.p50Ms)} · ${N(llm.calls)} çağrı, ${N(llm.failed)} hata</span>${chart(llm.series, 'Saatlik LLM gecikmesi P95', llmTone, { target: llmTarget, format: sec })}</div>
      <div class="card tile"><div class="tl-h">Tarama başarısı (${scans.windowDays || 7} gün)<span class="hstat h-${scanTone}">${scans.successRate == null ? 'Tarama yok' : scanTone === 'warn' ? 'Dikkat' : 'Normal'}</span></div><span class="v num">${scans.successRate == null ? '—' : scanPct(scans.successRate)}</span><span class="t">Hedef ≥ %${N(scanTarget)} · ${N(scans.total)} taramadan ${N(scans.failed)}'ü başarısız / kısmi</span>${chart(scans.series, 'Günlük tarama başarısı', scanTone, { target: scanTarget, percent: true, format: scanPct })}</div>
      <div class="card tile"><div class="tl-h">YZ kuyruğu (${queue.windowDays || 14} gün)<span class="hstat h-${queueTone}">${pr.failed ? 'Hata var' : growing ? 'Büyüyor' : 'Normal'}</span></div><span class="v num">${N(queue.count ?? pr.count)} kayıt</span><span class="t">En eski ${N(pr.oldestDays)} gün · ${N(pr.failed)} hatalı (${N(pr.exhausted)} deneme hakkı bitti)</span>${chart(queue.series, 'Saatlik YZ kuyruğu', queueTone)}</div>
      <div class="card tile"><div class="tl-h">Bekleyen aktarım (son ${ingest.windowHours || 24} saat)<span class="hstat h-${ingestTone}">${m.pendingIngest == null ? (m.crawlMode === 'remote' ? 'Ölçülemiyor' : 'Uygulanmaz') : m.pendingIngest > 0 ? 'Bekliyor' : 'Normal'}</span></div><span class="v num">${m.pendingIngest == null ? '—' : N(m.pendingIngest) + ' belge'}</span><span class="t">${m.crawlMode === 'remote' ? "Crawler'dan veritabanına aktarılmayı bekleyen" : 'Toplama bu sunucuda çalışıyor'}</span>${m.crawlMode !== 'remote' ? '<div class="spark-empty">Yerel toplamada aktarım kuyruğu yok</div>' : chart(ingest.series, 'Saatlik bekleyen aktarım', ingestTone)}</div></div></section>
    <section><div class="sec-h"><h2>Kaynaklar</h2><a class="btn btn-sm" href="#kaynaklar">Tümünü gör</a></div>
      <div class="card alarms"><div class="alarm-row"><span class="hstat h-ok">${hs.length - bad.length} sağlıklı</span><div class="msg">${hs.filter(s => s.status === 'ok').map(s => esc(srcOf(s.code).a)).join(', ') || '—'}</div><div></div></div>
      ${bad.map(s => `<div class="alarm-row"><span class="hstat ${(H_ST[s.status] || H_ST.down)[0]}">${(H_ST[s.status] || H_ST.down)[1]}</span><div class="msg"><b>${esc(s.name)}</b><small>${esc(s.message || '')}${s.lastFetch ? ' · son başarılı tarama ' + esc(s.lastFetch) : ''}</small></div><div class="acts"><button class="btn btn-sm" data-act="scan" data-k="${esc(s.code)}">Şimdi tara</button></div></div>`).join('')}</div></section></div>`;
}

/* ---------- işleme hattı: /pipeline düğümleri; "bekleyen" sayısı düğümün kayıt sayısıdır */
const HAT_POS = {
  sources: [0, 1], fetch: [0, 2],
  'raw:FETCHED': [1, 0], 'raw:EXTRACTED': [1, 1], 'raw:LINKED': [1, 2], 'raw:EXTRACT_FAILED': [1, 3],
  'reg:BASELINE': [2, 1], 'reg:NEW': [2, 2], 'reg:MERGED': [2, 3],
  'reg:IRRELEVANT': [3, 0], 'reg:RELEVANT': [3, 1], 'reg:SUMMARIZED': [3, 2], 'reg:READY': [3, 3], 'reg:AI_FAILED': [3, 4],
  'review:Onaylandı': [4, 2], 'review:Bekliyor': [4, 3], 'review:Reddedildi': [4, 4],
};
const HAT_KINDS = { flow: 'Toplama', wait: 'Sırada / işleniyor', ok: 'Tamamlandı', fail: 'Hata (yeniden denenir)', terminal: 'Son durum (hat dışı)' };
// Arayüz eşiği (sunucu verisi değil): bekleyen en eski kayıt bu kadar günü geçtiyse düğüm "dikkat" / "darboğaz" renginde çizilir
const HAT_WARN_DAYS = 3, HAT_CRIT_DAYS = 7;
const hatNode = id => ((S.adm.hat && S.adm.hat.nodes) || []).find(n => n.id === id) || { id, label: id, count: 0, metrics: {}, description: '' };
function hatStatus(n) {
  if (!n.count) return ['ok', 'Normal'];
  const d = n.metrics.oldestDays || 0;
  return d >= HAT_CRIT_DAYS ? ['crit', `Bekleme uzun · en eski ${d} gün`] : d >= HAT_WARN_DAYS ? ['warn', `Bekleme var · en eski ${d} gün`] : ['ok', 'Normal'];
}
function hatBox(n, layers) {
  const col = layers.findIndex(l => l.id === n.layer);
  const row = HAT_POS[n.id] ? HAT_POS[n.id][1] : 5;
  return { x: 59 + col * 222, y: 64 + row * 92, w: 164, h: 60, col, row };
}
function hatEdgePath(a, b, retry) {
  const ay = a.y + a.h / 2, by = b.y + b.h / 2;
  if (a.col !== b.col) {
    const x1 = a.x + a.w, x2 = b.x, dx = (x2 - x1) / 2;
    return { d: `M${x1},${ay} C${x1 + dx},${ay} ${x2 - dx},${by} ${x2},${by}`, x: (x1 + x2) / 2, y: (ay + by) / 2, anchor: 'middle' };
  }
  const down = b.row > a.row, x = a.x + a.w / 2;
  if (Math.abs(b.row - a.row) === 1 && !retry) {
    const y1 = down ? a.y + a.h : a.y, y2 = down ? b.y : b.y + b.h;
    return { d: `M${x},${y1} L${x},${y2}`, x: x + 6, y: (y1 + y2) / 2, anchor: 'start' };
  }
  const side = retry ? a.x : a.x + a.w, bulge = (retry ? -1 : 1) * (26 + 8 * Math.abs(b.row - a.row));
  const y1 = ay + (down ? 8 : -8), y2 = by + (down ? -8 : 8);
  return { d: `M${side},${y1} C${side + bulge},${y1} ${side + bulge},${y2} ${side},${y2}`, x: side + bulge * .8, y: (y1 + y2) / 2, anchor: retry ? 'end' : 'start' };
}
function hatGraph(p) {
  const boxes = new Map(p.nodes.map(n => [n.id, hatBox(n, p.layers)]));
  const height = 68 + 92 * Math.max(5, ...Array.from(boxes.values(), b => b.row + 1));
  const lanes = p.layers.map((l, i) => `<g class="hat-lane"><rect x="${36 + i * 222}" y="8" width="210" height="${height - 12}" rx="14"/><text x="${141 + i * 222}" y="32" text-anchor="middle">${esc(l.label)}</text></g>`).join('');
  const edges = p.edges.map(e => {
    const a = boxes.get(e.source), b = boxes.get(e.target);
    if (!a || !b) throw new Error(`İşleme hattında geçiş düğümü eksik: ${e.source} → ${e.target}`);
    const retry = /yeniden/.test(e.label), pos = hatEdgePath(a, b, retry);
    const label = esc(e.label + (e.queue ? ' · ' + e.queue : ''));
    return `<g class="hat-edge${retry ? ' is-retry' : ''}${e.target.endsWith('FAILED') ? ' is-fail' : ''}${S.node === e.source || S.node === e.target ? ' hi' : ''}" data-source="${esc(e.source)}" data-target="${esc(e.target)}"><path d="${pos.d}" marker-end="url(#hat-arrow)"><title>${label}</title></path><text x="${pos.x}" y="${pos.y - 4}" text-anchor="${pos.anchor}">${label}</text></g>`;
  }).join('');
  const nodes = p.nodes.map(n => {
    const b = boxes.get(n.id), [st, stx] = hatStatus(n);
    const technical = n.id.includes(':') ? n.id.split(':')[1] : n.id === 'fetch' ? 'fetch_run' : 'sources.yaml';
    return `<g class="hat-node kind-${esc(n.kind)} st-${st}${!n.count ? ' zero' : ''}${S.node === n.id ? ' sel' : ''}" data-act="node" data-n="${esc(n.id)}" tabindex="0" role="button" aria-pressed="${S.node === n.id}" aria-label="${esc(n.label)}: ${N(n.count)} · ${esc(stx)}">
      <title>${esc(n.description)} · ${esc(stx)}${n.metrics && n.metrics.completed24h != null ? ` · 24 sa: ${N(n.metrics.completed24h)} çıktı` : ''}</title>
      <rect class="hat-box" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="12"/>
      <rect class="hat-kind" x="${b.x}" y="${b.y + 10}" width="4" height="${b.h - 20}" rx="2"/>
      <text class="hat-label" x="${b.x + 14}" y="${b.y + 23}">${esc(n.label)}</text>
      ${S.tech ? `<text class="hat-tech" x="${b.x + 14}" y="${b.y + 43}">${esc(technical)}</text>` : ''}
      <text class="hat-count" x="${b.x + b.w - 12}" y="${b.y + 44}" text-anchor="end">${N(n.count)}</text></g>`;
  }).join('');
  return `<svg class="hat-graph${S.hatLabels ? ' labels' : ''}${S.node ? ' has-selection' : ''}" viewBox="0 0 ${60 + p.layers.length * 222} ${height}" role="group" aria-label="İşleme hattı durum grafiği"><defs><marker id="hat-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker></defs>${lanes}${edges}${nodes}</svg>`;
}
function admHat() {
  const p = S.adm.hat; const rf = p.reviewFlow || {};
  const failed = hatNode('reg:AI_FAILED');
  return `<div class="sec-h"><div class="hat-tools"><label class="switch"><input type="checkbox" id="techT" ${S.tech ? 'checked' : ''}>Teknik adlar</label><label class="switch"><input type="checkbox" id="hatLabelsT" ${S.hatLabels ? 'checked' : ''}>Geçiş etiketleri</label></div>
    <div class="hat-tools"><span class="muted" style="font-size:12.5px">Anlık durum · güncellendi ${S.refreshed ? fT(S.refreshed) : '—'}</span><label class="switch"><input type="checkbox" id="autoT" ${S.auto ? 'checked' : ''}>30 sn'de bir</label><button class="btn btn-sm" data-act="refresh">${ic('refresh')}Yenile</button><button class="btn btn-sm btn-ghost icon-btn" data-act="pop-hat" aria-label="Diğer işlemler">${ic('more')}</button></div></div>
  <div class="callouts">${rf.waiting ? `<div class="banner banner-crit">${ic('alert')}<div><b>Uzman kararı bekleyen: ${N(rf.waiting)} kayıt</b><p>Son 24 saatte ${N(rf.decided24h)}, son 7 günde ${N(rf.decided7d)} karar verildi. Bekleyen kararları toplu işlemle sonuçlandırabilirsiniz.</p><div class="b-act"><a class="btn btn-sm" href="#gelen">Gelen kutusu</a></div></div></div>` : ''}
    ${failed.count ? `<div class="banner banner-warn">${ic('clock')}<div><b>${N(failed.count)} kayıt YZ hatasında</b><p>${N((failed.metrics || {}).retryable)} kayıt otomatik yeniden denenecek, ${N((failed.metrics || {}).exhausted)} kayıt deneme hakkını tüketti ve elle müdahale bekliyor.</p><div class="b-act"><button class="btn btn-sm" data-act="node" data-n="reg:AI_FAILED">Hataları gör</button></div></div></div>` : ''}</div>
  <div class="hat-wrap"><div class="card flow-card"><div class="flow-scroll" tabindex="0" role="region" aria-label="Süreç haritası, yatay kaydırılabilir">${hatGraph(p)}</div>
    <div class="legend">${Object.entries(HAT_KINDS).map(([k, l]) => `<span class="hat-key kind-${k}"><i></i>${l}</span>`).join('')}<span>┄ yeniden deneme / işleme</span><span class="hstat h-warn">En eski bekleyen ≥ ${HAT_WARN_DAYS} gün</span><span class="hstat h-crit">≥ ${HAT_CRIT_DAYS} gün / hata</span></div>
    <div class="hat-sum">${flowSummary(p)}</div></div>
  <aside class="card hat-panel" aria-live="polite">${hatPanel()}</aside></div>`;
}
function flowSummary(p) {
  const rf = p.reviewFlow || {}; const n = id => hatNode(id).count;
  return `<div><h3>Akış özeti</h3><p>${N(n('raw:LINKED'))} belge bir düzenleme kaydına bağlandı; ${N(n('reg:BASELINE'))} kayıt ilk tarama içeriği olduğu için hat dışında, ${N(n('reg:MERGED'))} kayıt başka kayıtla birleştirildi.</p>
    <p style="margin-top:8px">YZ ${N(n('reg:IRRELEVANT'))} kaydı ilgisiz buldu (${N(rf.irrelevantConfirmed)} tanesi uzmanca doğrulandı). ${N(rf.waiting)} kayıt uzman kararı bekliyor; ${N(rf.approved)} onay, ${N(rf.rejected)} ret verildi${rf.unitAccuracy != null ? `, birim önerisi isabeti %${Math.round(rf.unitAccuracy * 100)}` : ''}.</p></div>
    <div><h3>Veri aralığı</h3><p class="muted">Sayılar tüm zamanları kapsar; ${p.generatedAt ? 'üretildi: ' + fDT(pdt(p.generatedAt)) : ''}.</p></div>`;
}
function hatPanel() {
  const id = S.node;
  if (!id) return `<div class="empty" style="padding:28px 8px">${ic('info')}<b>Bir adım seçin</b><span>Diyagramdaki bir kutuya tıklayınca adımın açıklaması, ölçümleri ve en yeni kayıtları burada görünür.</span></div>`;
  const n = hatNode(id); const m = n.metrics || {}; const items = S.adm.nodeItems && S.adm.nodeItems.node === id ? S.adm.nodeItems.items : null;
  const error = S.adm.nodeItems && S.adm.nodeItems.node === id ? S.adm.nodeItems.error : null;
  const transitions = S.adm.hat.edges.filter(e => e.source === id || e.target === id);
  const acts = [];
  if (id === 'reg:AI_FAILED' && isAdmin()) acts.push(`<button class="btn btn-sm" data-act="retry-all">Yeniden denenecekleri şimdi dene</button>`);
  if (id === 'review:Bekliyor') acts.push('<a class="btn btn-sm" href="#gelen">Gelen kutusuna git</a>');
  if (id === 'reg:IRRELEVANT') acts.push('<button class="btn btn-sm" data-act="goq" data-q="ilgisiz">Örneklemi incele</button>');
  if (['reg:RELEVANT', 'reg:SUMMARIZED'].includes(id)) acts.push('<button class="btn btn-sm" data-act="goq" data-q="hazirlaniyor">Bekleyen kayıtları gör</button>');
  if (id.startsWith('raw:')) acts.push('<a class="btn btn-sm" href="#kaynaklar">Kaynakları gör</a>');
  if (id === 'reg:BASELINE') acts.push('<a class="btn btn-sm" href="#bakim">Sınırı değiştir</a>');
  return `<div class="sec-h"><h3>${esc(n.label)}</h3><button class="btn btn-sm btn-ghost icon-btn" data-act="node" data-n="" aria-label="Seçimi kaldır">${ic('x')}</button></div><p>${esc(n.description)}</p>
    <div class="mgrid"><div><span>Kayıt sayısı</span><b class="num">${N(n.count)}</b></div><div><span>En eski</span><b style="font-size:13px">${m.oldestDays != null ? m.oldestDays + ' gün' : '—'}</b></div></div>
    ${m.completed24h != null ? `<p class="muted" style="font-size:12.5px">Son 24 saatte ${m.completed24h} kayıt bu adımı geçti.</p>` : ''}
    ${S.tech ? `<p class="mono">${esc(n.id)} · ${esc(n.module || '')}</p>` : ''}
    ${transitions.length ? `<h3 style="margin-top:16px;font-size:13px">Geçişler</h3><ul class="rlist">${transitions.map(e => {
      const other = e.source === id ? e.target : e.source;
      return `<li><button class="linkbtn" data-act="node" data-n="${esc(other)}">${e.source === id ? '→' : '←'} ${esc(hatNode(other).label)}</button><small>${esc(e.label)}${S.tech && e.queue ? ` · ${esc(e.queue)}` : ''}</small></li>`;
    }).join('')}</ul>` : ''}
    ${acts.length ? `<div class="b-act" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">${acts.join('')}</div>` : ''}
    <h3 style="margin-top:16px;font-size:13px">En yeni kayıtlar</h3>${error ? `<p class="crit" role="alert">${esc(error)}</p><button class="btn btn-sm" data-act="node" data-n="${esc(id)}">Yeniden dene</button>` : items === null ? '<p class="hint">Yükleniyor…</p>' : items.length ? `<ul class="rlist">${items.slice(0, 8).map(r => `<li>${esc(trunc(r.title || r.url || '—', 80))}<small>${esc(srcOf(r.source).a)}${r.at ? ' · ' + fDT(pdt(r.at)) : ''}${r.error ? ' · ' + esc(trunc(r.error, 60)) : ''}</small></li>`).join('')}</ul>` : '<p class="hint">Bu adımda kayıt yok.</p>'}`;
}

/* ---------- kaynaklar */
const JOB_TXT = { run: 'Tarama', backfill: 'Geçmiş tarama', reprocess: 'Yeniden işleme', retry_failed: 'Başarısızları yeniden deneme', rebaseline: 'Baseline sınırı', regenerate: 'Özet yeniden üretimi' };
const JOB_ST = { queued: ['tag-plain', 'Sırada'], running: ['tag-brand', 'Çalışıyor'], succeeded: ['tag-ok', 'Tamamlandı'], done: ['tag-ok', 'Tamamlandı'], success: ['tag-ok', 'Tamamlandı'], failed: ['tag-crit', 'Başarısız'], interrupted: ['tag-crit', 'Yarıda kaldı'], stale: ['tag-crit', 'Yarıda kaldı'] };
const SCAN_ST = { success: ['tag-ok', 'Başarılı'], partial: ['tag-warn', 'Kısmi'], failed: ['tag-crit', 'Başarısız'], running: ['tag-brand', 'Çalışıyor'] };
function admKaynak() {
  const k = S.adm.kaynaklar; const hs = Object.fromEntries(((Boot.health && Boot.health.sources) || []).map(s => [s.code, s]));
  const running = new Set((k.scans.active || []).map(a => a.source));
  const rec = (k.scans.recent || []).filter(s => S.scanFilter === 'tum' || s.status === S.scanFilter);
  const jobs = [...(k.scans.jobs.active || []), ...(k.scans.jobs.recent || [])];
  return `<div class="stack"><section><div class="tbl-wrap"><table class="tbl static"><thead><tr><th>Kaynak</th><th>Durum</th><th>Son tarama</th><th>Süre</th><th>Yeni / listede</th><th>Sonraki</th><th style="text-align:right">İşlem</th></tr></thead><tbody>
    ${k.metrics.sources.map(s => { const h = hs[s.code] || {}; const hst = H_ST[h.status] || ['h-ok', '—']; const lr = s.lastRun;
      return `<tr><td style="min-width:220px"><b style="font-weight:600">${esc(s.name)}</b> <span class="muted">${esc(srcOf(s.code).a)}</span></td>
      <td style="min-width:160px"><span class="hstat ${hst[0]}">${hst[1]}</span>${h.message ? `<div class="u-s">${esc(h.message)}</div>` : ''}</td>
      <td style="white-space:nowrap">${lr && lr.startedAt ? fDT(pdt(lr.startedAt)) : '—'}</td><td class="num">${lr && lr.durationSec != null ? dec(lr.durationSec, 1) + ' sn' : '—'}</td><td class="num">${lr ? `${N(lr.itemsNew)} / ${N(lr.itemsListed)}` : '—'}</td><td class="num">${s.nextDue ? fT(pdt(s.nextDue)) : '—'}</td>
      <td><div class="row-acts"><button class="btn btn-sm" data-act="scan" data-k="${esc(s.code)}" ${running.has(s.code) ? 'disabled' : ''}>${running.has(s.code) ? 'Taranıyor…' : 'Şimdi tara'}</button><button class="btn btn-sm btn-ghost" data-act="backscan" data-k="${esc(s.code)}">Geçmişi tara…</button></div></td></tr>`; }).join('')}
    </tbody></table></div></section>
    <section><div class="sec-h"><button class="collapse-h" data-act="toggle-scans" aria-expanded="${S.scansOpen}">${ic('chev-d')}Son taramalar <span class="muted num" style="font-weight:500">${(k.scans.recent || []).length}</span></button>
      ${S.scansOpen ? `<div class="hat-tools">${[['tum', 'Tümü'], ['success', 'Başarılı'], ['partial', 'Kısmi'], ['failed', 'Başarısız']].map(f => `<button class="btn btn-sm ${S.scanFilter === f[0] ? 'fbtn on' : ''}" data-act="scanf" data-f="${f[0]}">${f[1]}</button>`).join('')}</div>` : ''}</div>
      ${S.scansOpen ? `<div class="tbl-wrap"><table class="tbl static"><thead><tr><th>Kaynak</th><th>Zaman</th><th>Sonuç</th><th>Süre</th><th>İstek</th><th>Yeni / listede</th></tr></thead><tbody>
      ${rec.map(s => { const t = SCAN_ST[s.status] || ['tag-plain', s.status]; return `<tr><td>${esc(s.sourceName)}</td><td style="white-space:nowrap">${fDT(pdt(s.startedAt))}${s.backfillDay ? ` <span class="muted">(geçmiş: ${fD(pd(s.backfillDay))})</span>` : ''}</td><td><span class="tag ${t[0]}">${t[1]}</span>${s.errorCount ? `<div class="u-s">${s.errorCount} hata</div>` : ''}</td><td class="num">${s.durationSec != null ? dec(s.durationSec, 1) + ' sn' : '—'}</td><td class="num">${N(s.requests)}</td><td class="num">${N(s.itemsNew)} / ${N(s.itemsListed)}</td></tr>`; }).join('') || '<tr><td colspan="6" class="muted">Bu filtrede tarama yok.</td></tr>'}</tbody></table></div>` : ''}</section>
    <section><div class="sec-h"><h2>Arka plan işleri</h2></div><div class="card jobs">${jobs.length ? jobs.map(j => { const t = JOB_ST[j.status] || ['tag-plain', j.status]; const pr = j.params || {};
      return `<div class="job"><div><b>${esc(JOB_TXT[j.kind] || j.kind)}${j.source ? ' · ' + esc(srcOf(j.source).a) : ''}</b><br><small>${pr.from ? `${esc(pr.from)} – ${esc(pr.to)} · ` : ''}${pr.stage ? `${esc(pr.stage)} · ` : ''}${pr.count != null ? `${pr.count} kayıt · ` : ''}başlatan ${esc(j.requestedBy || '—')}${j.error ? ` · ${esc(trunc(j.error, 120))}` : ''}</small></div><div style="text-align:right"><span class="tag ${t[0]}">${t[1]}</span><br><small>${j.durationSec != null ? dec(j.durationSec, 0) + ' sn · ' : ''}${fDT(pdt(j.finishedAt || j.startedAt || j.createdAt))}</small></div></div>`; }).join('') : '<div class="empty"><b>Arka plan işi yok</b></div>'}</div></section></div>`;
}

/* ---------- YZ ayarları: değerler ve sınırlar /admin/settings?detail=true'dan; gruplama yalnızca arayüz düzeni */
const SET_GROUPS = [
  { g: 'Güven bantları', d: 'Kayıtlarda “Yüksek / Orta / Düşük güven” etiketini belirler.', keys: ['confidence_high', 'confidence_medium'] },
  { g: 'İlgililik', d: 'Hangi kayıtların uzman kuyruğuna geleceğini belirler.', keys: ['relevance_threshold', 'relevance_samples'], weights: ['relevance_weight_model', 'relevance_weight_rules', 'relevance_weight_votes'] },
  { g: 'Mükerrer tespiti', d: 'Farklı kaynaklardan gelen aynı düzenlemenin birleştirilmesi.', keys: ['dedupe_auto_threshold', 'dedupe_review_threshold'] },
];
const SET_LBL = { confidence_high: 'Yüksek güven eşiği', confidence_medium: 'Orta güven eşiği', relevance_threshold: 'İlgililik eşiği', relevance_samples: 'Tutarlılık için örnek sayısı', dedupe_auto_threshold: 'Otomatik birleştirme benzerliği', dedupe_review_threshold: 'Birleştirme önerisi benzerliği', relevance_weight_model: 'Model skoru', relevance_weight_rules: 'Kurallar', relevance_weight_votes: 'Örnekler arası uyum' };
const W_COL = { relevance_weight_model: 'var(--brand)', relevance_weight_rules: 'var(--med)', relevance_weight_votes: 'var(--high)' };
const setSpec = k => (S.adm.ayarlar.specs || {})[k] || { type: 'float', min: 0, max: 1, description: '' };
const settingDirty = () => Object.keys(S.setSaved || {}).filter(k => Math.abs(S.set[k] - S.setSaved[k]) > 1e-9);
function settingErrors() {
  const e = {}; const s = S.set; if (!s) return e;
  if (s.confidence_medium >= s.confidence_high) e.confidence_medium = 'Orta güven eşiği, yüksek güven eşiğinden küçük olmalı.';
  if (s.dedupe_review_threshold >= s.dedupe_auto_threshold) e.dedupe_review_threshold = 'Öneri eşiği, otomatik birleştirme eşiğinden küçük olmalı.';
  const w = s.relevance_weight_model + s.relevance_weight_rules + s.relevance_weight_votes;
  if (Math.abs(w - 1) > .001) e.weights = `Ağırlıkların toplamı 1,00 olmalı (şu an ${dec(w)}).`;
  Object.keys(s).forEach(k => { const sp = setSpec(k); if (s[k] < sp.min || s[k] > sp.max) e[k] = `Değer ${sp.min} ile ${sp.max} arasında olmalı.`; });
  Object.entries(S.setErr || {}).forEach(([k, m]) => { if (!e[k]) e[k] = m; });
  return e;
}
const fmtSet = (k, v) => setSpec(k).type === 'int' ? String(Math.round(v)) : dec(v);
const setStr = (k, v) => setSpec(k).type === 'int' ? String(Math.round(v)) : String(v);
function admAyar() {
  const a = S.adm.ayarlar; const e = settingErrors(); const dirty = settingDirty(); const pv = S.adm.preview; const hist = S.adm.hist || [];
  const ctl = k => { const sp = setSpec(k); const step = sp.type === 'int' ? (sp.max > 20 ? 10 : 1) : .05;
    return `<div class="set ${dirty.includes(k) ? 'dirty' : ''}"><label for="r-${k}">${SET_LBL[k] || k}</label><div class="set-ctl"><input type="range" id="r-${k}" data-set="${k}" min="${sp.min}" max="${sp.max}" step="${step}" value="${S.set[k]}" aria-label="${SET_LBL[k] || k}"><input class="input ${e[k] ? 'err' : ''}" data-set="${k}" value="${fmtSet(k, S.set[k])}" inputmode="decimal" aria-label="${SET_LBL[k] || k} değeri"></div><p>${esc(sp.description)}${e[k] ? `<br><span class="field-err">${esc(e[k])}</span>` : ''}</p><span class="mono">${k}</span></div>`; };
  const known = new Set(SET_GROUPS.flatMap(g => [...g.keys, ...(g.weights || [])]));
  const other = Object.keys(S.set).filter(k => !known.has(k));
  const groups = other.length ? [...SET_GROUPS, { g: 'Diğer', d: '', keys: other }] : SET_GROUPS;
  const pvBox = !dirty.length ? '<p class="muted" style="font-size:12.5px">Bir ayarı değiştirince son 30 günün kayıtları üzerindeki etkisi burada görünür. Önizleme LLM çağrısı yapmaz ve hiçbir şeyi kaydetmez.</p>'
    : Object.keys(e).length ? '<p class="muted" style="font-size:12.5px">Önce hataları düzeltin.</p>'
    : !pv ? '<p class="muted" style="font-size:12.5px">Etki hesaplanıyor…</p>' : pv.err ? `<span class="field-err">${esc(pv.err)}</span>`
    : `<p class="muted" style="font-size:12.5px">Son ${pv.windowDays} günün ${N(pv.evaluated)} kaydı yeni ayarlarla yeniden değerlendirilseydi${pv.skipped ? ` (${N(pv.skipped)} kayıt atlandı)` : ''}:</p>
      <div class="row"><span>İlgisiz → ilgili</span><b class="num">${N(pv.relevance.toRelevant)}</b></div><div class="row"><span>İlgili → ilgisiz</span><b class="num">${N(pv.relevance.toIrrelevant)}</b></div><div class="row"><span>Değişmeyen</span><b class="num">${N(pv.relevance.unchanged)}</b></div>
      <div class="row"><span>Otomatik birleşecek mükerrer adayı</span><b class="num">${N(pv.dedupe.wouldAutoMerge)} / ${N(pv.dedupe.pendingReview)}</b></div>
      <div class="row"><span>İlgililik çağrısı / gün</span><b class="num">${dec(pv.llm.relevanceCallsPerDay.current, 1)} → ${dec(pv.llm.relevanceCallsPerDay.projected, 1)}</b></div>`;
  return `<div class="grid2"><div class="stack">${groups.map(g => `<section class="card setg"><h3>${g.g}</h3>${g.d ? `<p class="muted" style="font-size:12.5px">${g.d}</p>` : ''}${g.keys.map(ctl).join('')}
    ${g.weights ? `<div class="set" style="grid-template-columns:minmax(0,1fr)"><label>İlgililik skorunun bileşenleri</label><p>Skor bu üç bileşenin ağırlıklı toplamıdır; toplam 1,00 olmalı.</p>
      <div class="wbar">${g.weights.map(k => `<i style="width:${S.set[k] * 100}%;background:${W_COL[k]}" title="${SET_LBL[k]}"></i>`).join('')}</div>
      <div class="frow" style="grid-template-columns:repeat(3,minmax(0,1fr))">${g.weights.map(k => `<label class="field"><span><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${W_COL[k]};margin-right:5px"></i>${SET_LBL[k]}</span><input class="input ${e.weights ? 'err' : ''}" data-set="${k}" value="${dec(S.set[k])}" inputmode="decimal"><span class="mono">${k}</span></label>`).join('')}</div>
      ${e.weights ? `<span class="field-err">${e.weights}</span>` : `<span class="hstat h-ok">Toplam 1,00</span>`}</div>` : ''}</section>`).join('')}
    ${dirty.length ? `<div class="dirtybar"><span>${dirty.length} değişiklik kaydedilmedi</span><button class="btn btn-sm" data-act="set-reset">Vazgeç</button><button class="btn btn-sm btn-primary" data-act="set-review" ${Object.keys(e).length ? 'disabled' : ''}>Gözden geçir ve kaydet</button></div>` : ''}</div>
    <div class="stack"><section class="card impact"><h3 style="font-size:14px">Etki önizlemesi</h3>${pvBox}</section>
    <section class="card impact"><h3 style="font-size:14px">Değişiklik geçmişi</h3>${hist.length ? hist.map(l => `<div class="row" style="display:block"><b style="font-weight:600">${esc(SET_LBL[l.key] || l.key)} ${esc(l.oldValue ?? '—')} → ${esc(l.newValue ?? '—')}</b><div class="u-s">${fDT(pdt(l.changedAt))} · ${esc(l.changedBy || '—')}</div>${l.reason ? `<div class="u-s">${esc(l.reason)}</div>` : ''}</div>`).join('') : '<p class="muted" style="font-size:12.5px">Henüz değişiklik yapılmamış.</p>'}</section></div></div>`;
}

/* ---------- bakım ve denetim */
const RP_STAGES = [['extract', 'Metin çıkarma (belgeden yeniden okuma)'], ['classify', 'İlgililik analizi'], ['summarize', 'Yalnızca özet'], ['match', 'Yalnızca birim eşleme']];
const RB_ACT = { covered: 'Arşiv sınıra kadar uzanıyor', crawl: 'Sınıra kadar taranacak', crawl_shallow: 'Sitenin gösterdiği kadarı alınacak', shallow: 'Yalnızca mevcut kayıtlar dönüştürülecek' };
function admBakim() {
  const b = S.adm.bakim; const rp = S.rp || {}; const failed = b.failed || []; const pr = (Boot.stats || {}).preparing || {}; const pl = S.blPlan;
  return `<div class="cards">
  <section class="card mcard"><h3>Yeniden işleme</h3><p>Seçilen tarih aralığındaki kayıtları seçilen adımdan başlayarak yeniden işler. Uzman kararları korunur.</p>
    <label class="field">Kaynak<select class="select" id="rpSrc"><option value="">Tüm kaynaklar</option>${Boot.sources.map(s => `<option value="${esc(s.code)}" ${rp.source === s.code ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select></label>
    <div class="frow"><label class="field">Başlangıç<input class="input" id="rpFrom" placeholder="gg.aa.yyyy" value="${esc(rp.from ?? fTR(new Date(NOW.getTime() - 7 * DAY)))}"></label><label class="field">Bitiş<input class="input" id="rpTo" placeholder="gg.aa.yyyy" value="${esc(rp.to ?? fTR(NOW))}"></label></div>
    <label class="field">Başlanacak adım<select class="select" id="rpStep">${RP_STAGES.map(s => `<option value="${s[0]}" ${rp.stage === s[0] ? 'selected' : ''}>${s[1]}</option>`).join('')}</select></label>
    ${rp.err ? `<span class="field-err">${esc(rp.err)}</span>` : ''}
    ${rp.est ? `<div class="result"><b>${N(rp.est.count)} kayıt · tahmini ${rp.est.estimatedMinutes} dk · yaklaşık ${N(rp.est.llmCalls)} YZ çağrısı</b><span>Uzman kararı verilmiş ${N(rp.est.decidedKept)} kayıt yeniden işlenir ama kararları değişmez.</span></div>` : ''}
    <div class="ph-act"><button class="btn" data-act="rp-preview">Etkiyi hesapla</button><button class="btn btn-primary" data-act="rp-start" ${rp.est && rp.est.count ? '' : 'disabled'}>${ic('play')}Yeniden işlemeyi başlat</button></div></section>
  <section class="card mcard"><h3>Başarısız YZ kayıtları</h3><p>${N(pr.retryable)} kayıt otomatik olarak yeniden denenecek. Deneme hakkı tükenen ${N(pr.exhausted)} kayıt elle yeniden denenebilir.</p>
    ${failed.length ? `<ul class="rlist">${failed.map(r => `<li style="grid-template-columns:minmax(0,1fr) auto;display:grid;gap:8px;align-items:center"><span>${esc(trunc(r.title, 90))}<small style="display:block">${esc(srcOf(r.source).a)} · ${esc(r.error || 'hata ayrıntısı yok')}</small></span><button class="btn btn-sm" data-act="retry-one" data-id="${r.regulationId}">${ic('retry')}Yeniden dene</button></li>`).join('')}</ul>` : `<div class="result">${ic('check')} Hatalı kayıt yok.</div>`}
    <div class="ph-act"><button class="btn" data-act="retry-all" ${pr.failed ? '' : 'disabled'}>Tüm hatalı ${N(pr.failed)} kaydı şimdi dene</button></div></section>
  <section class="card mcard"><h3>Baseline sınırı</h3><p>Bu tarihten önce yayımlanan ilk tarama kayıtları YZ hattına alınmaz. Sınırı geriye çekmek maliyetli olabilir; önce etkisini görün.</p>
    <div class="frow"><label class="field">Uygulanan sınır<input class="input" value="${b.baseline && b.baseline.current ? fTR(pd(b.baseline.current)) : 'Tanımlı değil'}" disabled></label><label class="field">Yeni sınır<input class="input" type="date" id="blIn" value="${esc(S.bl || '')}"></label></div>
    ${S.blErr ? `<span class="field-err">${esc(S.blErr)}</span>` : ''}
    ${pl ? `<div class="result"><b>${N(pl.converted)} kayıt hatta alınacak</b><span>${pl.crawl.length ? `${pl.crawl.length} kaynak sınıra kadar taranacak.` : 'Ek tarama gerekmiyor.'}</span><ul class="plist">${pl.sources.map(s => `<li>${esc(srcOf(s.source).a)}: ${RB_ACT[s.action] || s.action}${s.converted ? ` · ${N(s.converted)} kayıt` : ''}${s.range ? ` · ${esc(s.range.from)} – ${esc(s.range.to)}` : ''}</li>`).join('')}</ul></div>` : ''}
    <div class="ph-act"><button class="btn" data-act="bl-preview">Önizle</button><button class="btn btn-primary" data-act="bl-apply" ${pl ? '' : 'disabled'}>Uygula</button></div></section>
  <section class="card mcard"><h3>Denetim izi</h3><p>Her karar ve ayar değişikliği, kurumsal hesapla ve bir önceki olayın özetiyle zincirlenerek kaydedilir. Tek kaydın olayları ayrıntı bölmesinin Geçmiş sekmesinde görünür.</p>
    ${S.chain ? `<div class="result" style="color:${S.chain.ok ? 'var(--ok)' : 'var(--crit)'}">${ic(S.chain.ok ? 'check' : 'alert')}<b>${esc(S.chain.text)}</b></div>${S.chain.problems.length ? `<ul class="rlist">${S.chain.problems.slice(0, 8).map(p => `<li>${esc(JSON.stringify(p))}</li>`).join('')}</ul>` : ''}` : ''}
    <div class="ph-act"><button class="btn" data-act="chain">${ic('check')}Zinciri doğrula</button></div></section>
  <section class="card mcard"><h3>Kimlik ve yetki</h3><p>Kararlar kullanıcı adıyla kaydedilir. Kurumsal tek oturum açma (SSO) ayrı fazda eklenecek; o zamana kadar ad, istek başlığıyla gelir.</p>
    <div class="result"><b>${esc(Boot.me.name)}</b><span>${esc(Boot.me.title || '')}</span><span class="muted">Roller: ${esc((Boot.me.roles || []).join(', '))}</span></div></section></div>`;
}

async function loadPromptExperimentHome() {
  const p = S.pe; p.error = null;
  try {
    if (!p.defaults) {
      p.defaults = await GET('/evaluation/prompt-experiments/defaults');
      if (!p.promptB) p.promptB = p.defaults.systemPromptA;
    }
    const [runs, records] = await Promise.all([
      GET('/evaluation/prompt-experiments'),
      GET('/evaluation/prompt-experiments/records', { q: p.search }),
    ]);
    p.runs = runs.items || []; p.records = records.items || [];
  } catch (e) { p.error = errMsg(e); }
  if (S.view === 'yonetim' && S.adminTab === 'deney') renderAll();
}

let peSearchTimer = null;
async function searchPromptExperimentRecords() {
  const p = S.pe;
  try {
    const records = await GET('/evaluation/prompt-experiments/records', { q: p.search });
    p.records = records.items || []; p.error = null;
  } catch (e) { p.error = errMsg(e); }
  if (S.view === 'yonetim' && S.adminTab === 'deney') {
    const pos = $('#peSearch')?.selectionStart;
    renderAll();
    const input = $('#peSearch');
    if (input) { input.focus(); input.setSelectionRange(pos, pos); }
  }
}

function addPromptExperimentRecord(id) {
  const p = S.pe; const record = p.records.find(item => item.id === id);
  if (!p.selected[id] && Object.keys(p.selected).length >= ((p.defaults || {}).maxRecords || 50)) {
    p.error = `Bir deneye en fazla ${N((p.defaults || {}).maxRecords || 50)} kayıt eklenebilir.`; renderAll(); return;
  }
  if (record) p.selected[id] = { ...record, expectedRelevant: null };
  p.error = null;
  renderAll();
}

function removePromptExperimentRecord(id) {
  delete S.pe.selected[id]; S.pe.error = null;
  renderAll();
}

async function startPromptExperiment() {
  const p = S.pe;
  const records = Object.entries(p.selected).map(([id, record]) => ({
    regulation_id: +id, expected_relevant: record.expectedRelevant,
  }));
  const promptInput = $('#pePromptB');
  const titleInput = $('#peTitle');
  const systemPrompt = promptInput ? promptInput.value : p.promptB || '';
  const title = (titleInput ? titleInput.value : p.title || '').trim();
  if (!records.length) { p.error = 'Deney için en az bir kayıt seçin.'; renderAll(); return; }
  if (!title) { p.error = 'Deneye bir ad verin.'; renderAll(); return; }
  p.promptB = systemPrompt; p.error = null; p.loading = true; renderAll();
  try {
    const created = await api('POST', '/evaluation/prompt-experiments', {
      body: { title, records, system_prompt_b: systemPrompt },
    }).then(r => r.data);
    p.detail = created; p.runs = [created, ...p.runs.filter(run => run.id !== created.id)];
    p.selected = {}; toast('Prompt deneyi kaydedildi ve çalıştırıldı.');
    pollPromptExperiment(created.id);
  } catch (e) { p.error = errMsg(e); }
  p.loading = false; renderAll();
}

async function openPromptExperiment(id) {
  const p = S.pe;
  try {
    p.detail = await GET(`/evaluation/prompt-experiments/${id}`);
    pollPromptExperiment(id);
  } catch (e) { p.error = errMsg(e); }
  renderAll();
}

function pollPromptExperiment(id) {
  clearTimeout(S.pe.pollTimer);
  const refresh = async () => {
    if (S.view !== 'yonetim' || S.adminTab !== 'deney') return;
    try {
      const detail = await GET(`/evaluation/prompt-experiments/${id}`);
      if (S.pe.detail && S.pe.detail.id === id) S.pe.detail = detail;
      S.pe.runs = [detail, ...S.pe.runs.filter(run => run.id !== id)];
      if (['queued', 'running'].includes(detail.status)) S.pe.pollTimer = setTimeout(refresh, 1500);
    } catch (e) { S.pe.error = errMsg(e); }
    if (S.view === 'yonetim' && S.adminTab === 'deney') renderAll();
  };
  S.pe.pollTimer = setTimeout(refresh, 500);
}

function peMetricCell(label, metric) {
  return `<div class="card"><span class="kpi-l">${label}</span><span class="kpi-v num">${metric.labelled ? `${N(metric.tp)} / ${N(metric.fp)} / ${N(metric.fn)}` : '—'}</span><span class="kpi-s">Doğru ilgili / gereksiz / kaçırılan · ${N(metric.labelled)} etiketli</span><span class="kpi-s">Precision ${metric.precision == null ? '—' : '%' + Math.round(metric.precision * 100)} · Recall ${metric.recall == null ? '—' : '%' + Math.round(metric.recall * 100)}</span></div>`;
}

function peDecision(result, error) {
  if (error) return `<div class="result"><span class="field-err">${esc(error)}</span></div>`;
  if (!result) return '<div class="result"><span>Sonuç bekleniyor…</span></div>';
  return `<div class="result"><b>${result.is_relevant ? 'İlgili' : 'İlgisiz'} · skor ${dec(result.score, 3)}</b>
    <span>${result.uncertain ? 'Tereddüt var' : 'Tereddüt yok'} · ${esc(result.band || '—')} · ${result.cached ? 'önbellekten' : 'yeni LLM sonucu'}</span>
    <span>${esc(result.rationale || 'Gerekçe yok')}</span>
    ${result.topics && result.topics.length ? `<span class="muted">Konular: ${esc(result.topics.join(', '))}</span>` : ''}</div>`;
}

function promptExperimentResults(detail) {
  if (!detail) return '';
  const summary = detail.summary;
  const metricCards = summary ? `<div class="cards">${peMetricCell('A · mevcut prompt', summary.A)}${peMetricCell('B · taslak prompt', summary.B)}
    <div class="card"><span class="kpi-l">Karar değişikliği</span><span class="kpi-v num">${N(summary.changed)}</span><span class="kpi-s">${N(summary.unlabelled)} etiketsiz kayıt yalnız farklılık olarak sayılır</span><span class="kpi-s">${N(summary.cachedCalls)} sonuç önbellekten</span></div></div>` : '';
  const items = (detail.items || []).map(item => `<article class="card mcard">
    <h3>${esc(item.title)}</h3><p>Kayıt #${N(item.regulationId)} · beklenen: ${item.expectedRelevant == null ? 'etiketlenmedi' : item.expectedRelevant ? 'ilgili' : 'ilgisiz'} · ${esc(item.status)}</p>
    <div class="grid2"><section><b>A · mevcut prompt</b>${peDecision(item.A, item.errorA)}</section><section><b>B · taslak prompt</b>${peDecision(item.B, item.errorB)}</section></div>
    <details><summary>Girdi özeti ve önbellek kimliği</summary><code>${esc(item.inputHash || 'henüz oluşmadı')}</code></details></article>`).join('');
  return `<section class="stack" style="margin-top:18px"><div class="sec-h"><h2>${esc(detail.title)}</h2><p>${esc(detail.status)} · ${detail.createdAt ? fDT(pdt(detail.createdAt)) : ''}</p></div>
    ${summary ? metricCards : '<div class="result">A/B sonuçları kaydediliyor…</div>'}
    <details><summary>A ve B prompt anlık görüntüleri</summary><div class="grid2">
      <pre class="card" style="white-space:pre-wrap;max-height:360px;overflow:auto">${esc(detail.systemPromptA || '')}</pre>
      <pre class="card" style="white-space:pre-wrap;max-height:360px;overflow:auto">${esc(detail.systemPromptB || '')}</pre></div></details>
    ${items || '<div class="empty card">Henüz kayıt sonucu yok.</div>'}</section>`;
}

function admPromptExperiment() {
  const p = S.pe; const d = p.defaults || {}; const selected = Object.values(p.selected);
  const selectedIds = new Set(selected.map(record => record.id));
  const provider = d.provider && d.provider !== 'none';
  const recordRows = p.records.map(record => `<div class="alarm-row" style="grid-template-columns:minmax(0,1fr) auto;display:grid;gap:10px;align-items:center">
    <div class="msg"><b>${esc(record.title)}</b><small>${esc(record.issuer || '—')} · ${record.publishDate ? esc(record.publishDate) : 'tarih yok'} · ${esc(record.status)}</small></div>
    <button class="btn btn-sm" data-act="pe-add" data-id="${record.id}" ${selectedIds.has(record.id) || selected.length >= (d.maxRecords || 50) ? 'disabled' : ''}>${selectedIds.has(record.id) ? 'Eklendi' : 'Seç'}</button></div>`).join('');
  const selectedRows = selected.map(record => `<div class="alarm-row" style="grid-template-columns:minmax(0,1fr) auto auto;display:grid;gap:10px;align-items:center">
    <div class="msg"><b>${esc(record.title)}</b><small>#${N(record.id)} · ${esc(record.issuer || '—')}</small></div>
    <select class="select" data-change="pe-label" data-id="${record.id}" aria-label="${esc(record.title)} beklenen ilgililik">
      <option value="unknown" ${record.expectedRelevant == null ? 'selected' : ''}>Etiketsiz</option>
      <option value="true" ${record.expectedRelevant === true ? 'selected' : ''}>İlgili</option>
      <option value="false" ${record.expectedRelevant === false ? 'selected' : ''}>İlgisiz</option>
    </select><button class="btn btn-sm" data-act="pe-remove" data-id="${record.id}">Kaldır</button></div>`).join('');
  const runs = p.runs.map(run => `<button class="btn btn-sm" data-act="pe-open" data-id="${run.id}">${esc(run.title)} · ${esc(run.status)} · ${run.createdAt ? fDT(pdt(run.createdAt)) : ''}</button>`).join('');
  const settings = d.settings || {};
  return `<div class="cards">
    <section class="card mcard"><h3>Test kayıtları</h3><p>Mevcut kayıtları seçin. Beklenen ilgililik etiketi yalnızca bu deneyin ölçümünde kullanılır; seçilmezse doğruluk metriğine katılmaz.</p>
      <label class="search">${ic('search')}<input id="peSearch" type="search" placeholder="Başlık veya kurumda ara" value="${esc(p.search)}"></label>
      <div class="stack" style="max-height:320px;overflow:auto">${recordRows || '<div class="result">Kayıt bulunamadı.</div>'}</div>
      <h3>Deney örneklemi (${N(selected.length)}/50)</h3><div class="stack">${selectedRows || '<div class="result">Henüz kayıt seçilmedi.</div>'}</div>
    </section>
    <section class="card mcard"><h3>A/B ayarları</h3><p>A: kayıt oluşturulurken anlık görüntüsü alınan mevcut sistem promptu. B: düzenlenebilir tam sistem promptu.</p>
      <div class="result"><b>${esc(d.provider || 'LLM kapalı')} · ${esc(d.model || '—')}</b><span>Eşik ${dec(settings.relevanceThreshold, 2)} · ağırlıklar ${dec(settings.relevanceWeightModel, 2)} / ${dec(settings.relevanceWeightVotes, 2)} / ${dec(settings.relevanceWeightRules, 2)} · örnek ${N(settings.relevanceSamples)} · sıcaklık ${dec(settings.sampleTemperature, 2)}</span><span>A ve B aynı belge girdisi, model ve ayarlarla çalışır. Yeni çağrı gerektiğinde iki promptun her biri için en çok ${N(selected.length * 2 * (settings.relevanceSamples || 1))} örnek çağrısı yapılır; eşleşen sonuçlar önbellekten gelir.</span></div>
      <label class="field">Deney adı<input class="input" id="peTitle" maxlength="200" placeholder="Örn. İşveren ve veri sorumlusu rolleri" value="${esc(p.title || '')}"></label>
      <label class="field">B · taslak sistem promptu<textarea class="input" id="pePromptB" rows="16" maxlength="30000">${esc(p.promptB || d.systemPromptA || '')}</textarea></label>
      ${p.error ? `<span class="field-err">${esc(p.error)}</span>` : ''}
      <div class="ph-act"><button class="btn btn-primary" data-act="pe-start" ${!provider || p.loading || !selected.length ? 'disabled' : ''}>${p.loading ? 'Kaydediliyor…' : 'A/B deneyini çalıştır'}</button><button class="btn" data-act="pe-reload">Deneyleri yenile</button></div>
      <p class="muted">Promptlar ve sonuçlar kalıcı olarak saklanır. Deney canlı ilgililik etiketlerini değiştirmez. Sadece aynı prompt, belge girdisi, model ve örnekleme ayarlarında önbellek kullanılır.</p>
    </section></div>
    ${runs ? `<section class="card mcard" style="margin-top:16px"><h3>Kaydedilmiş deneyler</h3><div class="frow">${runs}</div></section>` : ''}
    ${promptExperimentResults(p.detail)}`;
}
