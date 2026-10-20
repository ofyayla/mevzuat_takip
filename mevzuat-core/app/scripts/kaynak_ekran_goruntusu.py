"""Kaynak tarama kılavuzu için ekran görüntüsü üretir: crawler'ın taradığı sayfaları açar, crawler'ın okuduğu alanları
numaralı çerçevelerle işaretler ve ``docs/kaynak-tarama-kilavuzu/img/`` altına PNG yazar.

  cd mevzuat-core/app
  python scripts/kaynak_ekran_goruntusu.py --list                 # çekim listesi
  python scripts/kaynak_ekran_goruntusu.py                        # hepsi
  python scripts/kaynak_ekran_goruntusu.py BDDK SPK               # yalnız bu kaynaklar
  python scripts/kaynak_ekran_goruntusu.py --only tkbb-duyurular  # tek çekim

İşaretler ``config/sources.yaml``'daki seçicilerden okunur (``css_list``: item/title/date/link, ``link_pattern``:
href_pattern/date_selector); böylece görüntüde çerçevelenen alan, crawler'ın gerçekten okuduğu alandır. Seçici
değişirse görüntü yeniden üretilir. Çekim özel (``custom``) ise işaretler bu dosyadaki ``SHOTS`` tanımındadır.

Kurum ağından çalıştırın: BDDK yurt dışı IP'lerine, SPK/KGK/Rekabet/Resmî Gazete/GİB bulut IP'lerine kapalıdır. Erişilemeyen
sayfa için görüntü üretilmez ve rapora ``ERİŞİLEMEDİ`` yazılır (engel sayfasının görüntüsü kılavuza girmez).

Ortam: ``pip install playwright pyyaml`` ve Chromium (``playwright install chromium`` ya da ``CHROMIUM_PATH``). Vekil sunucu
için ``HTTPS_PROXY`` okunur. TLS doğrulaması kapatılmaz; BDDK gibi ara sertifika göndermeyen sitelerde ağ tarafında kurum
CA paketi tanımlı olmalıdır.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

APP_DIR = Path(__file__).resolve().parent.parent
REPO = APP_DIR.parent.parent
SOURCES = APP_DIR / "config" / "sources.yaml"
OUT = REPO / "docs" / "kaynak-tarama-kilavuzu" / "img"
VIEW_W, VIEW_H = 1366, 860
COLORS = {1: "#d6336c", 2: "#1c7ed6", 3: "#2f9e44", 4: "#e8590c", 5: "#7048e8"}
BLOCK_MARKERS = ("Web Filter Violation", "Erişiminiz Engellenmiştir", "Web Page Blocked", "has been blocked",
                 "Erişim Engellendi", "Güvenlik Sorunu", "Access Denied")


@dataclass
class Mark:
    num: int
    label: str
    find: str          # sayfada çalışan JS ifadesi: Element dizisi döner
    max: int = 3       # en çok kaç öğe çerçevelensin (rozet yalnız ilkinde)


@dataclass
class Shot:
    id: str
    source: str
    channel: str
    title: str
    url: str
    marks: list[Mark] = field(default_factory=list)
    prep: str = ""                 # çekimden önce sayfada çalışan JS (sekme aç, akordeon genişlet…)
    wait_ms: int = 2500
    height: int = VIEW_H
    kind: str = "page"             # page | raw (API/RSS yanıtı: ham metin biçimlendirilip işaretlenir)
    note: str = ""


# ------------------------------------------------------------------------------------------------ bulucular (JS)

HELPERS = r"""
window.__vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
  return r.width > 4 && r.height > 4 && s.visibility !== 'hidden' && s.display !== 'none'; };
window.__q = (sel, root) => Array.from((root || document).querySelectorAll(sel)).filter(window.__vis);
window.__href = (re, root) => Array.from((root || document).querySelectorAll('a[href]'))
  .filter(a => new RegExp(re).test(a.getAttribute('href')) && window.__vis(a));
window.__up = (els, n) => els.map(e => { let x = e; for (let i = 0; i < n && x.parentElement; i++) x = x.parentElement; return x; });
window.__rng = (re, flags) => { const out = []; const rx = new RegExp(re, flags || 'g');
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = w.nextNode());) { rx.lastIndex = 0; let m;
    while ((m = rx.exec(n.nodeValue))) { const r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
      out.push({ getBoundingClientRect: () => r.getBoundingClientRect() }); if (!rx.global) break; } }
  return out; };
window.__text = (re, sel) => Array.from(document.querySelectorAll(sel || '*')).filter(e => e.children.length === 0
  && new RegExp(re).test(e.textContent) && window.__vis(e));
"""

OVERLAY = r"""
([marks, stamp]) => {
  const old = document.getElementById('__marks'); if (old) old.remove();
  const layer = document.createElement('div'); layer.id = '__marks';
  layer.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none';
  document.body.appendChild(layer);
  // gövde konumlandırılmışsa (ör. sabit üst çubuk için kaydırılmış gövde) katman sayfa köşesinden kayar; farkı düş
  const lo = layer.getBoundingClientRect();
  const sx = -lo.left, sy = -lo.top; let first = null; const found = {};
  for (const m of marks) {
    let els = []; try { els = (new Function('return (' + m.find + ')'))() || []; } catch (e) { els = []; }
    els = Array.from(new Set(els)).filter(Boolean).slice(0, m.max); found[m.num] = els.length;
    els.forEach((el, i) => {
      const r = el.getBoundingClientRect(); if (r.width < 2 && r.height < 2) return;
      const b = document.createElement('div');
      b.style.cssText = `position:absolute;left:${r.left+sx-3}px;top:${r.top+sy-3}px;width:${r.width+6}px;height:${r.height+6}px;`
        + `border:3px solid ${m.color};border-radius:6px;background:${m.color}1c;box-sizing:border-box`;
      layer.appendChild(b);
      if (i === 0) {
        const n = document.createElement('div'); n.textContent = m.num;
        n.style.cssText = `position:absolute;left:${r.left+sx-16}px;top:${r.top+sy-16}px;width:30px;height:30px;border-radius:50%;`
          + `background:${m.color};color:#fff;font:700 17px/30px Arial,sans-serif;text-align:center;box-shadow:0 1px 4px #0006`;
        layer.appendChild(n);
        if (first === null || r.top + window.scrollY < first) first = r.top + window.scrollY;
      }
    });
  }
  const st = document.createElement('div'); st.textContent = stamp;
  st.style.cssText = 'position:fixed;left:0;bottom:0;right:0;background:#111c;color:#fff;font:12px/22px Arial,sans-serif;padding:0 10px;z-index:2147483647';
  document.body.appendChild(st);
  return {found, first};
}
"""


def _kw(sel: str) -> str:
    return json.dumps(sel)


def marks_from_config(source: dict, channel: str, url_index: int = 0) -> list[Mark]:
    """``sources.yaml`` kanal seçicilerinden işaretler: kutu / başlık / tarih / bağlantı."""
    ch = next(c for c in source["channels"] if c["name"] == channel)
    p = ch.get("params", {})
    if ch["strategy"] == "css_list":
        item = p["item"]
        marks = [Mark(1, "Liste öğesi (her biri ayrı kayıt)", f"__q({_kw(item)})", 3)]
        if p.get("title"):
            marks.append(Mark(2, "Başlık", f"__q({_kw(p['title'])}, __q({_kw(item)})[0])", 1))
        if p.get("date"):
            marks.append(Mark(3, "Tarih", f"__q({_kw(p['date'])}, __q({_kw(item)})[0])", 1))
        link = p.get("link")
        if link:
            marks.append(Mark(4, "Ayrıntı bağlantısı", f"__q({_kw(link)}, __q({_kw(item)})[0])", 1))
        return marks
    if ch["strategy"] == "link_pattern":
        scope = f"document.querySelector({_kw(p['scope'])})" if p.get("scope") else "document"
        if p.get("link_selector"):
            marks = [Mark(1, "Taranan bağlantı (kapsam + bağlantı seçicisi)", f"__q({_kw(p['link_selector'])}, {scope})", 4)]
        else:
            marks = [Mark(1, "Taranan bağlantı (href deseni)", f"__href({_kw(p.get('href_pattern', '.'))}, {scope})", 4)]
        if p.get("date_regex"):
            marks.append(Mark(3, "Tarih (başlığın içinden ayrıştırılır)", f"__rng({_kw(p['date_regex'])})", 1))
        if p.get("date_selector"):
            marks.append(Mark(3, "Tarih", f"__q({_kw(p['date_selector'])})", 1))
        if p.get("title_selector"):
            marks.append(Mark(2, "Başlık", f"__q({_kw(p['title_selector'])})", 1))
        return marks
    raise ValueError(f"{channel}: {ch['strategy']} için otomatik işaret yok; custom çekim tanımlayın")


# ------------------------------------------------------------------------------------------------ çekim listesi
# Yapılandırmadan türetilemeyen (özel) işaretler burada. Seçiciler canlı sayfada doğrulandığı tarihle birlikte yazılır.

def build_shots(cfg: dict) -> list[Shot]:
    src = {s["code"]: s for s in cfg["sources"]}
    year = datetime.now(ZoneInfo("Europe/Istanbul")).year
    S: list[Shot] = []
    WP = "https://{host}/portal/v2/{ep}?per_page=2&_fields=id,date,modified,slug,link,title"

    # ---- TKBB (link_pattern; Tailwind şablonu: kart bir <a> öğesidir)
    card = "__q('a[href^=\"/kurumsal/duyurular/\"]').filter(a=>/devam/i.test(a.textContent))"
    S.append(Shot("tkbb-duyurular", "TKBB", "duyurular", "Kurumsal › Duyurular", "https://www.tkbb.org.tr/kurumsal/duyurular",
                  marks=[Mark(1, "Duyuru kartı — taranan bağlantı (href: /kurumsal/duyurular/…)", card, 3),
                         Mark(2, "Başlık (kartın ikinci <span>'ı)", f"{card}.slice(0,1).map(a=>a.querySelectorAll('span')[1])", 1),
                         Mark(3, "Yayım tarihi", f"{card}.slice(0,1).map(a=>a.querySelector('span'))", 1),
                         Mark(4, "\"Devamını oku\" — ayrıntı sayfası", f"{card}.slice(0,1).map(a=>a.querySelector('button'))", 1)]))
    open_all = "document.querySelectorAll('.accordion-title').forEach((e,i)=>{ if(i<2) e.click(); });"
    S.append(Shot("tkbb-idari-duzenlemeler", "TKBB", "birlik_duzenlemeleri", "Mevzuat › Birliğimiz Düzenlemeleri › İdari Düzenlemeler",
                  "https://www.tkbb.org.tr/mevzuat/birligimiz-duzenlemeleri/idari-duzenlemeler", prep=open_all,
                  marks=[Mark(1, "Düzenleme grubu (açılır başlık: \"Göster\")", "__q('.accordion-item').filter(e=>e.getBoundingClientRect().left>500)", 3),
                         Mark(4, "Taranan belge bağlantısı (details/N veya /upload/….pdf|doc)",
                              "__href('birligimiz-duzenlemeleri-details/\\\\d+|/upload/.+\\\\.(pdf|docx?)$')", 4)],
                  note="Açılır başlıklar tıklanarak genişletildi; crawler sayfanın HTML'inde bulunan tüm bağlantıları görür."))

    # ---- TCMB (feed + link_pattern)
    tcmb_basin = "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Duyurular/Basin"
    S.append(Shot("tcmb-basin-sayfa", "TCMB", "basin_duyurulari", "Duyurular ve Konuşmalar › Basın Duyuruları (sitedeki görünüm)", tcmb_basin,
                  marks=[Mark(1, "Basın duyurusu satırı", "__q('.block-collection-box')", 3),
                         Mark(2, "Başlık", "__q('a.collection-title[title]')", 1),
                         Mark(3, "Yayım tarihi", "__q('.collection-tag')", 1),
                         Mark(4, "PDF ek simgesi", "__q('a[target=_blank] .collection-title.pdf').map(e=>e.parentElement)", 1)],
                  note="Crawler bu sayfayı değil, aynı içeriği veren Atom beslemesini okur (sonraki görüntü)."))
    S.append(Shot("tcmb-basin-feed", "TCMB", "basin_duyurulari", "Basın Duyuruları — Atom beslemesi (crawler'ın okuduğu kaynak)",
                  "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Bottom+Menu/Diger/RSS/Basin+Duyurulari", kind="raw", height=760,
                  marks=[Mark(1, "<entry> = bir duyuru", "__rng('<entry>[\\\\s\\\\S]*?</entry>')", 1),
                         Mark(2, "Başlık", "__rng('<title type=\"text\">.*?</title>')", 1),
                         Mark(4, "Duyuru bağlantısı (href)", "__rng('<link rel=\"alternate\"[^>]*>')", 1),
                         Mark(3, "Yayım tarihi", "__rng('<published>.*?</published>')", 1)]))
    tcmb_mev = "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Banka+Hakkinda/Mevzuat/Bankacilik/"
    S.append(Shot("tcmb-mevzuat-bankacilik", "TCMB", "mevzuat", "Banka Hakkında › Mevzuat › Bankacılık ile İlgili Mevzuat", tcmb_mev,
                  prep="const T=document.querySelectorAll('.w-tab-link'),P=document.querySelectorAll('.w-tab-pane'); T.forEach((t,i)=>t.classList.toggle('w--current',i===1)); P.forEach((p,i)=>{p.style.display=i===1?'block':'none'});",
                  marks=[Mark(1, "Belge satırı (sekme içinde: Yönetmelikler, Kararlar/Tebliğler/Genelgeler, Talimatlar, Mülga)", "__q('.block-collection-box')", 3),
                         Mark(4, "Taranan bağlantı: /wps/wcm/connect/<GUID>/… (CACHEID değişince yeni sürüm)",
                              "__q('a[target=_blank]:has(.pdf)')", 3)]))

    # ---- MASAK (WordPress API; ön yüz React)
    S.append(Shot("masak-duyurular", "MASAK", "icerik", "Duyurular (sitedeki görünüm)", "https://masak.hmb.gov.tr/kategori/duyurular/sayfa/1",
                  marks=[Mark(1, "Duyuru (WordPress \"post\")", "__q('article.mb-category-post')", 3),
                         Mark(2, "Başlık ve ayrıntı bağlantısı", "__q('.mb-post-title a')", 1),
                         Mark(3, "Yayım tarihi", "__q('time.mb-post-date')", 1)],
                  note="Crawler bu sayfayı değil, sayfanın beslendiği WordPress API'sini okur (sonraki görüntü)."))
    S.append(Shot("masak-api", "MASAK", "icerik", "WordPress REST API yanıtı: /portal/v2/posts (crawler'ın okuduğu kaynak)",
                  WP.format(host="masak.hmb.gov.tr", ep="posts"), kind="raw", prep="json",
                  marks=[Mark(1, "Bir kayıt (post)", "__rng('\\\\{\\\\s*\"id\"[\\\\s\\\\S]*?\\\\n  \\\\}')", 1),
                         Mark(3, "date / modified — artımlı tarama ve sürüm anahtarı", "__rng('\"(date|modified)\": \"[^\"]*\"')", 2),
                         Mark(4, "link (iç adres, herkese açık adrese çevrilir)", "__rng('\"link\": \"[^\"]*\"')", 1),
                         Mark(2, "Başlık (içerik HTML'i ve ek bağlantıları aynı yanıtta \"content\" alanında gelir; görüntüde kısaltıldı)", "__rng('\"title\": \\\\{')", 1)]))

    # ---- HMB (MASAK ile aynı platform)
    S.append(Shot("hmb-basin-duyurulari", "HMB", "duyurular", "Duyurular › Basın Duyuruları (sitedeki görünüm)", "https://www.hmb.gov.tr/kategori/basin-duyurulari/sayfa/1",
                  marks=[Mark(1, "Duyuru (WordPress \"post\")", "__q('article.mb-category-post')", 3),
                         Mark(2, "Başlık ve ayrıntı bağlantısı (/duyuru/{slug})", "__q('.mb-post-title a')", 1),
                         Mark(3, "Yayım tarihi", "__q('time.mb-post-date')", 1)]))
    S.append(Shot("hmb-kanunlar", "HMB", "sayfalar", "Mevzuat › Kanunlar (WordPress \"page\")", "https://www.hmb.gov.tr/kanunlar",
                  marks=[Mark(1, "Mevzuat satırı", "__q('ul.mb-list li')", 3),
                         Mark(4, "mevzuat.gov.tr bağlantısı (dış host: yalnız sayfanın kendi sürümü izlenir)", "__q('ul.mb-list li a.btn')", 1)]))
    S.append(Shot("hmb-api", "HMB", "duyurular", "WordPress REST API yanıtı: /portal/v2/posts (crawler'ın okuduğu kaynak)",
                  WP.format(host="www.hmb.gov.tr", ep="posts"), kind="raw", prep="json",
                  marks=[Mark(1, "Bir kayıt (post)", "__rng('\\\\{\\\\s*\"id\"[\\\\s\\\\S]*?\\\\n  \\\\}')", 1),
                         Mark(3, "date / modified — artımlı tarama ve sürüm anahtarı", "__rng('\"(date|modified)\": \"[^\"]*\"')", 2),
                         Mark(4, "link (iç adres; ön yüz adresi /duyuru/{slug} şablonundan üretilir)", "__rng('\"(link|slug)\": \"[^\"]*\"')", 2),
                         Mark(2, "Başlık (içerik HTML'i ve ek bağlantıları aynı yanıtta \"content\" alanında gelir; görüntüde kısaltıldı)", "__rng('\"title\": \\\\{')", 1)]))

    # =============================================================================================
    # Aşağıdaki çekimler BU ORTAMDA DOĞRULANAMADI (siteler bulut IP'lerine / yurt dışına kapalı). Kurum ağından ilk
    # çalıştırmada rapordaki "UYARI: işaret bulunamadı" satırlarına bakın; seçici değiştiyse sources.yaml ile birlikte güncelleyin.
    # =============================================================================================

    # ---- Resmî Gazete (fihrist: #html-content > .html-title / .html-subtitle / .fihrist-item a)
    today = datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d")
    S.append(Shot("rg-fihrist", "RESMI_GAZETE", "fihrist", "Günün fihristi (asıl sayı)", f"https://www.resmigazete.gov.tr/fihrist?tarih={today}",
                  marks=[Mark(1, "Bölüm başlığı (İLÂN BÖLÜMÜ alınmaz; yalnız ilgili kurum ilanları ayrıca alınır)", "__q('#html-content .html-title')", 4),
                         Mark(2, "Alt başlık (YÖNETMELİKLER, TEBLİĞLER …)", "__q('#html-content .html-subtitle')", 2),
                         Mark(4, "Madde bağlantısı → /eskiler/YYYY/MM/YYYYMMDD-N.htm|pdf (her biri ayrı kayıt)", "__q('#html-content .fihrist-item a')", 3)]))

    # ---- BDDK (link_pattern; kimlikler: Duyuru 39/40/48, Mevzuat 49–52/55/56/58)
    bddk = src["BDDK"]
    S.append(Shot("bddk-basin-duyurulari", "BDDK", "duyurular", "Duyurular › Basın Duyuruları (Duyuru/Liste/39)", "https://www.bddk.org.tr/Duyuru/Liste/39",
                  marks=marks_from_config(bddk, "duyurular")))
    S.append(Shot("bddk-kurul-kararlari-rg", "BDDK", "rg_kurul_kararlari", "Mevzuat › Resmî Gazete'de yayımlanan Kurul Kararları (Mevzuat/Liste/55)",
                  "https://www.bddk.org.tr/Mevzuat/Liste/55", marks=marks_from_config(bddk, "rg_kurul_kararlari")))
    S.append(Shot("bddk-duzenlemeler", "BDDK", "duzenlemeler", "Mevzuat › Bankacılık Kanununa İlişkin Düzenlemeler (Mevzuat/Liste/50)",
                  "https://www.bddk.org.tr/Mevzuat/Liste/50", marks=marks_from_config(bddk, "duzenlemeler")))

    # ---- SPK
    spk = src["SPK"]
    S.append(Shot("spk-bultenler", "SPK", "bultenler", f"SPK Bültenleri {year}", f"https://spk.gov.tr/spk-bultenleri/{year}-yili-spk-bultenleri",
                  marks=marks_from_config(spk, "bultenler")))
    S.append(Shot("spk-basin-duyurulari", "SPK", "basin_duyurulari", f"Basın Duyuruları {year}", f"https://spk.gov.tr/duyurular/basin-duyurulari/{year}",
                  marks=marks_from_config(spk, "basin_duyurulari")))
    S.append(Shot("spk-mevzuat", "SPK", "mevzuat", "SPK Mevzuat Sistemi (React; liste /api/Search/All ile gelir)", "https://mevzuat.spk.gov.tr/", wait_ms=5000,
                  note="Görüntü işaretsizdir: crawler bu ekranın kullandığı JSON uç noktasını okur."))

    # ---- KVKK
    kvkk = src["KVKK"]
    S.append(Shot("kvkk-duyurular", "KVKK", "duyurular", "Duyurular", "https://www.kvkk.gov.tr/Icerik/2015/Duyurular", marks=marks_from_config(kvkk, "duyurular")))
    S.append(Shot("kvkk-kurul-kararlari", "KVKK", "kurul_kararlari", "Kurul Kararları", "https://www.kvkk.gov.tr/Icerik/5419/kurul-kararlari",
                  marks=marks_from_config(kvkk, "kurul_kararlari")))
    S.append(Shot("kvkk-yonetmelikler", "KVKK", "mevzuat", "Mevzuat › Yönetmelikler", "https://www.kvkk.gov.tr/Icerik/5257/yonetmelikler", marks=marks_from_config(kvkk, "mevzuat")))

    # ---- TICARET
    tic = src["TICARET"]
    S.append(Shot("ticaret-duyurular", "TICARET", "duyurular", "Duyurular", "https://ticaret.gov.tr/duyurular", marks=marks_from_config(tic, "duyurular")))
    S.append(Shot("ticaret-tuketici-mevzuati", "TICARET", "tuketici_mevzuati", "Tüketici › Mevzuat › Tebliğ ve Kılavuzlar",
                  "https://ticaret.gov.tr/tuketici/mevzuat/teblig-ve-kilavuzlar", marks=marks_from_config(tic, "tuketici_mevzuati")))

    # ---- REKABET
    rek = src["REKABET"]
    S.append(Shot("rekabet-duyurular", "REKABET", "duyurular", "Duyurular", "https://www.rekabet.gov.tr/tr/Duyurular", marks=marks_from_config(rek, "duyurular")))
    S.append(Shot("rekabet-kararlar", "REKABET", "kurul_kararlari", "Kurul Kararları", "https://www.rekabet.gov.tr/tr/Kararlar", marks=marks_from_config(rek, "kurul_kararlari")))

    # ---- GIB (Next.js ön yüz; liste uçları yalnız POST + JSON gövde kabul eder, tarayıcıdan GET ile görüntülenemez)
    S.append(Shot("gib-mevzuat", "GIB", "ozelgeler", "Mevzuat › Vergi Mevzuatına Yeni Eklenenler", "https://www.gib.gov.tr/mevzuat", wait_ms=5000,
                  marks=[Mark(1, "Mevzuat satırı (özelge, tebliğ, CBK…)", "__q(\"a[href*='/mevzuat/kanun/']\")"),
                         Mark(2, "Başlık", "__q(\"a[href*='/mevzuat/kanun/']\").map(a => { const d = [...a.querySelectorAll('*')].filter(e => !e.children.length && e.textContent.trim().length > 20); return d[d.length - 1]; }).filter(Boolean)"),
                         Mark(3, "Yayım tarihi", "__rng('\\\\d{2}\\\\.\\\\d{2}\\\\.\\\\d{4}')")],
                  note="Crawler bu ön yüzün kullandığı /api/gibportal/ POST uç noktalarını okur (özelge, sirküler, tebliğ, CBK, iç genelge, yönetmelik, taslak, duyuru); "
                       "görüntü aynı içeriğin ekrandaki karşılığıdır."))

    # ---- KGK
    kgk = src["KGK"]
    S.append(Shot("kgk-duyurular", "KGK", "duyurular", "Duyurular", "https://www.kgk.gov.tr/Assignments/1/0/Duyurular-", marks=marks_from_config(kgk, "duyurular")))
    S.append(Shot("kgk-son-yayimlananlar", "KGK", "son_yayimlananlar", "Son Yayımlananlar", "https://www.kgk.gov.tr/Assignments/2/0/Son-Yay%C4%B1mlananlar",
                  marks=marks_from_config(kgk, "son_yayimlananlar")))
    S.append(Shot("kgk-mevzuat-yonetmelikler", "KGK", "mevzuat", "Mevzuat › Yönetmelikler", "https://www.kgk.gov.tr/DynamicContentDetail/5106/Y%C3%B6netmelikler",
                  marks=marks_from_config(kgk, "mevzuat")))
    return S


def block_reason(page_text: str, status: int | None) -> str | None:
    if status is not None and status >= 400:
        return f"HTTP {status}"
    head = page_text[:600]
    for m in BLOCK_MARKERS:
        if m.lower() in head.lower():
            return f"engel sayfası ({m})"
    return None


def capture(shot: Shot, ctx, out: Path) -> dict:
    page = ctx.new_page()
    rep = {"id": shot.id, "source": shot.source, "url": shot.url, "status": "ok", "marks": {}}
    try:
        resp = page.goto(shot.url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001 — sürekli istek atan sayfalar
            pass
        page.wait_for_timeout(shot.wait_ms)
        reason = block_reason(page.inner_text("body"), resp.status if resp else None)
        if reason:
            rep["status"] = f"ERİŞİLEMEDİ: {reason}"
            return rep
        if shot.kind == "raw":
            text = resp.text() if resp else ""
            if shot.prep == "json":
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            page.set_content("<body style='margin:0'><pre style='margin:0;padding:14px 18px;font:15px/1.55 Consolas,Menlo,monospace;"
                             "white-space:pre-wrap;word-break:break-all;background:#fff;color:#222'></pre></body>")
            page.evaluate("t => document.querySelector('pre').textContent = t", text)
        page.evaluate(f"() => {{ {HELPERS} }}")
        if shot.prep and shot.kind == "page":
            page.evaluate(f"async () => {{ {shot.prep} }}")
            page.wait_for_timeout(1200)
        stamp = f"{shot.source} · {shot.title} · {shot.url} · çekim: {datetime.now(ZoneInfo('Europe/Istanbul')):%d.%m.%Y %H:%M}"
        marks = [{"num": m.num, "label": m.label, "find": m.find, "max": m.max, "color": COLORS[m.num]} for m in shot.marks]
        res = page.evaluate(OVERLAY, [marks, stamp]) if marks else {"found": {}, "first": None}
        rep["marks"] = {str(k): v for k, v in res["found"].items()}
        top = max(0, int(res["first"] - 150)) if res["first"] is not None else 0
        page.evaluate("(y) => window.scrollTo(0, y)", top)
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / f"{shot.id}.png"), clip=None, full_page=False)
        missing = [m.num for m in shot.marks if not res["found"].get(str(m.num))]
        if missing:
            rep["status"] = f"UYARI: işaret bulunamadı {missing} (seçici değişmiş olabilir)"
    except Exception as e:  # noqa: BLE001
        rep["status"] = f"ERİŞİLEMEDİ: {str(e).splitlines()[0][:100]}"
    finally:
        page.close()
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*", help="kaynak kodları (boşsa hepsi)")
    ap.add_argument("--only", help="tek çekim kimliği")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--emit-js", metavar="DIR", help="tarayıcı yerine, işaretleri gerçek bir tarayıcıda (konsol / uzantı) çalıştırılacak "
                    "JS parçalarını DIR/<çekim-kimliği>.js olarak yaz; sayfa açıldıktan sonra çalıştırıp ekran görüntüsünü elle alın")
    args = ap.parse_args()
    cfg = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    shots = build_shots(cfg)
    if args.sources:
        shots = [s for s in shots if s.source in {c.upper() for c in args.sources}]
    if args.only:
        shots = [s for s in shots if s.id == args.only]
    if args.list:
        for s in shots:
            print(f"{s.id:34} {s.source:13} {s.url}")
        return 0
    if args.emit_js:
        d = Path(args.emit_js)
        d.mkdir(parents=True, exist_ok=True)
        for s in shots:
            stamp = f"{s.source} · {s.title} · {s.url} · çekim: {datetime.now(ZoneInfo('Europe/Istanbul')):%d.%m.%Y %H:%M}"
            marks = [{"num": m.num, "label": m.label, "find": m.find, "max": m.max, "color": COLORS[m.num]} for m in s.marks]
            prep = f"await (async () => {{ {s.prep} }})(); await new Promise(r => setTimeout(r, 1200));" if s.prep and s.kind == "page" else ""
            parts = ["(async () => { " + HELPERS, prep,
                     "const res = (" + OVERLAY.strip() + ")(" + json.dumps([marks, stamp], ensure_ascii=False) + ");",
                     "window.scrollTo(0, res.first === null ? 0 : Math.max(0, res.first - 150));",
                     "return JSON.stringify(res.found); })()"]
            js = chr(10).join(parts)
            (d / f"{s.id}.js").write_text(js, encoding="utf-8")
            print(f"{s.id:34} -> {d / (s.id + '.js')}")
        return 0
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    launch = {"args": ["--no-sandbox"]}
    if os.environ.get("CHROMIUM_PATH"):
        launch["executable_path"] = os.environ["CHROMIUM_PATH"]
    if proxy:
        launch["proxy"] = {"server": proxy}
    reports = []
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(viewport={"width": VIEW_W, "height": VIEW_H}, locale="tr-TR", timezone_id="Europe/Istanbul")
        for s in shots:
            r = capture(s, ctx, OUT)
            reports.append(r)
            print(f"{r['status'][:60]:60} {s.id}  {r['marks']}")
        browser.close()
    (OUT.parent / "cekim-raporu.json").write_text(json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if any(r["status"].startswith("ERİŞİLEMEDİ") for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
