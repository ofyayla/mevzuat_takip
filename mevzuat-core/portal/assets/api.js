'use strict';
/* ============================================================================================================
   Mevzuat Takip Portalı — API katmanı. Tüm sunucu iletişimi buradan geçer: /api/v1, X-Demo-User başlığı
   (URL kodlamalı; SSO gelene kadar), ETag/If-Match ve {error:{code,message,details}} hata biçimi.
   ============================================================================================================ */

/* ---------- yardımcılar */
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ic = n => `<svg class="i" aria-hidden="true"><use href="#i-${n}"/></svg>`;
/** Açıklama ve ek uyarı metni: ⓘ düğmesi; metin araç ipucunda (fare, klavye odağı ve dokunmayla) açılır. */
const tipBtn = (t, label = 'Bilgi') => `<button type="button" class="info" data-tip="${esc(t)}" aria-label="${esc(label + ': ' + t)}">${ic('info')}</button>`;
/** Tek satırlık durum satırı: başlık (HTML), isteğe bağlı kısa ek metin, ayrıntı araç ipucunda, eylem düğmeleri sağda. */
const banner = (kind, icon, title, { tip = '', act = '', sub = '' } = {}) => `<div class="banner banner-${kind}">${ic(icon)}<div>${title ? `<b>${title}</b>` : ''}${sub ? `<span class="b-sub">${sub}</span>` : ''}${tip ? tipBtn(tip) : ''}</div>${act}</div>`;
const AY = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara'];
const DAY = 864e5;
let NOW = new Date();
const sod = d => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const dd = (a, b) => Math.round((sod(a) - sod(b)) / DAY);
const p2 = n => String(n).padStart(2, '0');
const fT = d => `${p2(d.getHours())}:${p2(d.getMinutes())}`;
const fD = d => `${d.getDate()} ${AY[d.getMonth()]} ${d.getFullYear()}`;
const fDs = d => d.getFullYear() === NOW.getFullYear() ? `${d.getDate()} ${AY[d.getMonth()]}` : fD(d);
const fTR = d => `${p2(d.getDate())}.${p2(d.getMonth() + 1)}.${d.getFullYear()}`;
const fISO = d => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
const fDT = d => d ? `${fDs(d)} ${fT(d)}` : '—';
const parseTR = s => { const m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(String(s).trim()); if (!m) return null; const d = new Date(+m[3], +m[2] - 1, +m[1]); return d.getMonth() === +m[2] - 1 ? d : null; };
const rel = d => { const n = dd(NOW, d); if (n <= 0) return `bugün ${fT(d)}`; if (n === 1) return `dün ${fT(d)}`; if (n < 7) return `${n} gün önce`; return fDs(d); };
const nf = new Intl.NumberFormat('tr-TR');
const N = n => nf.format(n ?? 0);
const dec = (x, p = 2) => (x == null ? '—' : Number(x).toFixed(p).replace('.', ','));
const pct = x => '%' + Math.round(x * 100);
const lower = s => String(s ?? '').toLocaleLowerCase('tr');
const trunc = (s, n) => { s = String(s ?? ''); return s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s; };
const pd = s => { if (!s) return null; const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s); return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null; };   // 'YYYY-MM-DD' → yerel tarih
const pdt = s => (s ? new Date(s) : null);                                                                                               // ISO zaman damgası
const sameSet = (a, b) => a.length === b.length && a.every(x => b.includes(x));

/* ---------- sabitler (YZ/iş kuralı değil; yalnızca arayüz metinleri) */
const SEVS = ['Kritik', 'Yüksek', 'Orta', 'Düşük'];
const SEVK = { Kritik: 'k', Yüksek: 'y', Orta: 'o', Düşük: 'd' };
const SEV_WHY = {
  Kritik: 'Bankanın yasal oranlarını veya temel faaliyet iznini doğrudan etkiliyor; uyumsuzluk idari yaptırım doğurabilir.',
  Yüksek: 'Bankanın süreç veya ürünlerinde değişiklik gerektiriyor; uyum için sınırlı bir süre tanınmış.',
  Orta: 'Bankayı dolaylı olarak ya da yalnızca belirli koşullarda etkiliyor; izlenmesi yeterli olabilir.',
  Düşük: 'Bilgilendirme amaçlı; bankanın faaliyetlerinde değişiklik gerektirmiyor.',
};
// Sunucunun kabul ettiği ret nedenleri (rejection_reason); "other" için açıklama zorunludur
const REJECT_REASONS = [['not_banking_related', 'Bankayı etkilemiyor'], ['outdated', 'Güncelliğini yitirmiş'], ['duplicate', 'Mükerrer kayıt'], ['other', 'Diğer (açıklama zorunlu)']];
const REJ_LABEL = Object.fromEntries(REJECT_REASONS);
const ABBR = { RESMI_GAZETE: 'RG', TICARET: 'TB', REKABET: 'RK', GIB: 'GİB' };

/* ---------- sunucu oturumu: kullanıcı adı X-Demo-User ile gider (kurumsal SSO ayrı fazda) */
const Sess = {
  get user() { try { return localStorage.getItem('mtp_user') || ''; } catch (_) { return ''; } },
  set user(v) { try { v ? localStorage.setItem('mtp_user', v) : localStorage.removeItem('mtp_user'); } catch (_) { /* özel pencere */ } },
};

class ApiError extends Error {
  constructor(status, code, message, details) { super(message); this.status = status; this.code = code; this.details = details || {}; }
}
const qs = o => {
  const p = new URLSearchParams();
  Object.entries(o || {}).forEach(([k, v]) => { if (v == null || v === '' || v === false) return; p.set(k, Array.isArray(v) ? v.join(',') : v === true ? 'true' : String(v)); });
  const s = p.toString(); return s ? '?' + s : '';
};
/** o: {query, body (JSON), form (FormData), etag (If-Match için sürüm), blob (dosya indirme)} → {data, etag, res} */
async function api(method, path, o = {}) {
  const h = { Accept: 'application/json' };
  if (Sess.user) h['X-Demo-User'] = encodeURIComponent(Sess.user);
  if (o.etag != null) h['If-Match'] = `"${o.etag}"`;
  let body;
  if (o.form) body = o.form;
  else if (o.body !== undefined) { h['Content-Type'] = 'application/json'; body = JSON.stringify(o.body); }
  let res;
  try { res = await fetch(API_BASE + path + qs(o.query), { method, headers: h, body }); }
  catch (_) { throw new ApiError(0, 'network', 'Sunucuya ulaşılamadı. Bağlantıyı kontrol edip yeniden deneyin.'); }
  if (o.blob && res.ok) return { blob: await res.blob(), res };
  const ct = res.headers.get('content-type') || '';
  let data = null;
  try { data = ct.includes('json') ? await res.json() : res.status === 204 ? null : await res.text(); } catch (_) { /* gövde yok */ }
  if (!res.ok) {
    const e = data && data.error;
    throw new ApiError(res.status, e ? e.code : 'http_' + res.status, e ? e.message : `Sunucu hatası (${res.status})`, e ? e.details : {});
  }
  const tag = (res.headers.get('ETag') || '').replace(/^W\//, '').replace(/"/g, '');
  return { data, etag: tag || null, res };
}
const API_BASE = '/api/v1';
const GET = (p, query, o) => api('GET', p, { query, ...o }).then(r => r.data);

/** Kullanıcıya gösterilecek hata metni. 412: kayıt başkası tarafından değişti; 409: iş kuralı (sunucu iletisi Türkçedir). */
function errMsg(e) {
  if (!(e instanceof ApiError)) return 'Beklenmeyen hata: ' + (e && e.message || e);
  if (e.code === 'version_mismatch') return 'Kayıt başka bir kullanıcı tarafından değiştirildi; güncel hali yüklendi. Kararınızı yeniden gözden geçirin.';
  if (e.code === 'validation_error') { const l = (e.details.errors || []).map(x => `${(x.loc || []).slice(1).join('.')}: ${x.msg}`); return 'Geçersiz istek' + (l.length ? ' — ' + l.join('; ') : ''); }
  if (e.status === 403) return 'Bu işlem için yetkiniz yok.';
  if (e.status === 404) return e.message || 'Kayıt bulunamadı (başkası işlemiş olabilir).';
  return e.message;
}

/* ---------- referans verileri */
const SRC = {};          // kod → {n: ad, a: kısaltma}
let UNITS = [];          // [{code, name, group}]
const srcOf = k => SRC[k] || { n: k || '—', a: ABBR[k] || k || '—' };
const unitName = c => (UNITS.find(u => u.code === c) || {}).name || c;

/* ---------- kayıt önbelleği ve API ↔ arayüz kayıt dönüşümü */
const REC = new Map();
const byId = id => REC.get(String(id));
const STAGE_TXT = { summary: 'Özet bekliyor', unit_match: 'Birim eşleme', retry: 'Hata · yeniden denenecek', failed: 'Hata · müdahale gerekli' };
// sunucudaki alan anahtarı → arayüz anahtarı (düzenleme formu ve "Uzman düzenledi" etiketleri)
const EDIT_KEY = { title: 'title', type: 'type', summary: 'summary', publishDate: 'pub', effective: 'eff', draftDeadline: 'draftDue', topics: 'topics' };

/** Liste/ayrıntı öğesini kayda çevirir. resetSel: birim seçimini sunucudaki duruma sıfırlar. */
function upsert(it, { resetSel = false } = {}) {
  const r = REC.get(String(it.id)) || { id: String(it.id), num: it.id, kind: 'reg' };
  REC.set(r.id, r);
  r.kind = 'reg';
  const u = it.suggestedUnits || [];
  Object.assign(r, {
    src: it.sources && it.sources.length && !it.issuerCode ? it.sources[0] : it.issuerCode, sources: it.sources || [], type: it.type || '—',
    title: it.title, pub: pd(it.publishDate), det: pdt(it.detectedAt),
    eff: it.effectiveMode === 'date' ? pd(it.effectiveDate) : it.effectiveMode === 'on_publish' ? 'yayim' : null,
    sev: it.severity, band: it.confidence, summary: it.summary || '', topics: it.topics || [],
    units: u.map(x => ({ code: x.code, n: x.unit, s: x.score, why: x.reason, origin: x.origin })),
    needsUnit: !!it.needsUnit, status: it.status, canDecide: !!it.canDecide, version: it.version, stage: it.aiStage || null,
    analysis: it.analysisStatus, conflict: it.effectiveConflict || null, dup: it.possibleDuplicate || null,
    draftDue: it.draftDeadline ? pd(it.draftDeadline) : null, url: it.sourceUrl,
    decided: it.decidedBy ? { by: it.decidedBy.name, title: it.decidedBy.title, at: pdt(it.decidedBy.at) } : null,
    aiUnits: it.aiSuggestedUnits ? it.aiSuggestedUnits.map(x => ({ code: x.code, n: x.unit, s: x.score })) : null, revCount: it.decisionRevisions || 0,
  });
  if (it.viewed !== undefined) r.unread = !it.viewed;
  r.sug = r.units.filter(x => x.origin === 'ai').map(x => x.code);
  if (it.auditTrail !== undefined) {   // ayrıntı yanıtı
    r.detail = true;
    r.trail = it.auditTrail; r.relevance = it.relevance || {}; r.sevWhy = it.severityRationale;
    r.citations = it.summaryCitations || []; r.effText = it.effectiveDateText; r.srcLinks = it.sourceLinks || [];
    r.decisionNote = it.decisionNote; r.removed = it.removedClaims || []; r.grounding = it.groundingScore;
    r.edited = (it.editedFields || []).map(k => EDIT_KEY[k]).filter(Boolean);
    const o = it.aiOriginal || {};
    r.ai = { title: o.title, type: o.type, summary: o.summary || '', pub: pd(o.publishDate), draftDue: o.draftDeadline ? pd(o.draftDeadline) : null, topics: o.topics || [],
      eff: o.effectiveMode === 'date' ? pd(o.effectiveDate) : o.effectiveMode === 'on_publish' ? 'yayim' : null };
  }
  if (resetSel || !r.sel) r.sel = r.status === 'Bekliyor' ? (r.needsUnit ? [] : r.units.map(x => x.code)) : r.units.map(x => x.code);
  return r;
}
/** Arşiv öğesi (kayıt henüz portalda görünmez; ayrı ayrıntı uç noktası). */
function upsertArchive(it) {
  const r = REC.get(String(it.id)) || { id: String(it.id), num: it.id };
  REC.set(r.id, r);
  const kind = it.kind === 'baseline' ? 'baseline' : 'irr';
  Object.assign(r, { kind, src: it.issuerCode || (it.sources || [])[0], sources: it.sources || [], type: it.type || '—', title: it.title, pub: pd(it.publishDate), det: pdt(it.detectedAt),
    relScore: it.relevanceScore, why: it.rationale, uncertain: it.uncertain, threshold: it.threshold ?? r.threshold, review: it.irrelevantReview || null, url: it.sourceUrl,
    units: [], sel: [], sug: [], topics: [], summary: '', status: kind === 'baseline' ? 'İlk tarama' : 'İlgisiz' });
  if (it.unread !== undefined) r.unread = it.unread;
  if (it.documents) { r.detail = true; r.docs = it.documents; r.criteria = it.criteria || []; r.note = it.note; }
  return r;
}
/** İlgisiz arşivi / örneklem öğesi (geriye dönük çağıranlar için). */
function upsertIrr(it) { return upsertArchive(it); }

/* ---------- önyükleme */
const Boot = { me: null, stats: null, sources: [], health: null };
async function loadRefs() {
  const [me, sources, units] = await Promise.all([GET('/me'), GET('/sources'), GET('/units')]);
  Boot.me = me; Boot.sources = sources; UNITS = units;
  sources.forEach(s => { SRC[s.code] = { n: s.name, a: ABBR[s.code] || s.code }; });
}
async function loadStats() { Boot.stats = await GET('/regulations/stats'); return Boot.stats; }
async function loadHealth() { try { Boot.health = await GET('/sources/health'); } catch (_) { Boot.health = null; } }

/* ---------- dosya indirme (Excel): kimlik başlığı gerektiği için fetch + Blob */
async function download(path, query, fallbackName) {
  const { blob, res } = await api('GET', path, { query, blob: true });
  const m = /filename="?([^";]+)"?/i.exec(res.headers.get('Content-Disposition') || '');
  saveBlob(blob, m ? m[1] : fallbackName);
  return res;
}
function saveBlob(blob, name) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}
