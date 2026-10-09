#!/usr/bin/env python3
"""Kurum makinesinden kaynaklara erişim testi — kurulum gerektirmez (yalnızca Python 3 standart kütüphanesi).

Proje kurulmadan önce firewall/proxy/TLS durumunu görmek için tek dosya olarak kopyalanıp çalıştırılır.
Kurulumdan sonraki karşılığı: ``mevzuat-collect check-access``.

  python3 erisim_testi.py                                   # 9 kaynak + yardımcı host'lar
  python3 erisim_testi.py --proxy http://proxy.kurum:8080   # ortamda HTTPS_PROXY yoksa
  python3 erisim_testi.py --ca-file kurum-kok.pem           # kurum proxy'sinin kök sertifikası
  python3 erisim_testi.py --tcp 10.0.0.5:27017 --tcp 10.144.100.204:8806   # MongoDB, vLLM vb. port testi

Proxy ortamdan (HTTPS_PROXY / NO_PROXY; Windows'ta sistem ayarları) okunur. Ara sertifikalar, script
``app/scripts/`` altındaysa ``app/config/certs/*.pem``'den, değilse yanındaki ``certs/`` klasöründen yüklenir.
"""
from __future__ import annotations

import argparse
import glob
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

TARGETS = [
    ("RESMI_GAZETE", "https://www.resmigazete.gov.tr/"),
    ("BDDK", "https://www.bddk.org.tr/"),
    ("SPK", "https://spk.gov.tr/"),
    ("SPK_MEVZUAT", "https://mevzuat.spk.gov.tr/"),
    ("TCMB", "https://www.tcmb.gov.tr/"),
    ("KVKK", "https://www.kvkk.gov.tr/"),
    ("MASAK", "https://masak.hmb.gov.tr/portal/v2/posts?per_page=1"),
    ("MASAK_EKLER", "https://ms.hmb.gov.tr/"),
    ("TICARET", "https://ticaret.gov.tr/"),
    ("REKABET", "https://www.rekabet.gov.tr/"),
    ("TKBB", "https://www.tkbb.org.tr/"),
    ("MEVZUAT_GOV", "https://www.mevzuat.gov.tr/"),
]
# Bu siteler tarayıcı olmayan TLS imzasını engelleyebilir; asıl crawler curl_cffi (impersonate) ile bağlanır.
IMPERSONATE = {"RESMI_GAZETE", "BDDK", "TICARET"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def ssl_context(ca_file: str | None) -> tuple[ssl.SSLContext, list[str]]:
    ctx = ssl.create_default_context()
    here = os.path.dirname(os.path.abspath(__file__))
    extra = []
    for d in (os.path.join(here, "..", "config", "certs"), os.path.join(here, "certs")):
        extra += sorted(glob.glob(os.path.join(d, "*.pem")))
    if ca_file:
        extra.append(ca_file)
    for pem in extra:
        ctx.load_verify_locations(cafile=pem)
    return ctx, extra


def classify(exc: BaseException) -> str:
    text = str(getattr(exc, "reason", exc))
    if isinstance(exc, socket.gaierror) or "Name or service not known" in text or "getaddrinfo" in text:
        return f"DNS çözülemedi ({text})"
    if "CERTIFICATE_VERIFY_FAILED" in text:
        if "local issuer" in text:
            return "TLS: zincir doğrulanamadı — sunucu ara sertifikayı göndermiyor ya da kurum proxy kökü yok (--ca-file)"
        return f"TLS: sertifika doğrulanamadı ({text})"
    if "407" in text:
        return "Proxy kimlik doğrulaması istiyor (407)"
    if "Tunnel connection failed" in text:
        return f"Proxy bağlantıyı reddetti ({text.split(':', 1)[-1].strip()}) — proxy'de site izni/istisnası gerekli"
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in text:
        return "Zaman aşımı — firewall/proxy istisnası eksik olabilir"
    if "refused" in text.lower():
        return f"Bağlantı reddedildi ({text})"
    if "reset" in text.lower():
        return f"Bağlantı kesildi ({text}) — proxy/IPS engeli olabilir"
    return f"{type(exc).__name__}: {text}"


def check_http(code: str, url: str, opener, timeout: float) -> tuple[bool, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json,*/*",
                                               "Accept-Language": "tr-TR,tr;q=0.9"})
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read(512 * 1024)
            return True, f"HTTP {r.status}, {len(body)} bayt"
    except urllib.error.HTTPError as e:
        # HTTPS'te HTTPError = TLS kuruldu ve sunucu yanıt verdi; yani ağ erişimi var.
        # (SSL denetimi yapan proxy kendi engel sayfasını da 403 ile dönebilir.)
        if e.code == 403:
            hint = "site düz istemciyi engelliyor, crawler impersonate ile bağlanır" if code in IMPERSONATE \
                else "sunucu yanıt verdi; SSL denetimli proxy engel sayfası olabilir"
            return True, f"HTTP 403 — {hint}"
        return e.code < 500, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 — her hatayı sınıflandırıp raporla
        return False, classify(e)


def check_tcp(target: str, timeout: float) -> tuple[bool, str]:
    host, _, port = target.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, "port açık"
    except Exception as e:  # noqa: BLE001
        return False, classify(e)


def main() -> int:
    p = argparse.ArgumentParser(description="Kaynaklara erişim testi (firewall/proxy/TLS)")
    p.add_argument("--proxy", help="ör. http://proxy.kurum:8080 (varsayılan: ortam/sistem ayarı)")
    p.add_argument("--ca-file", help="ek kök/ara sertifika (PEM), ör. kurum proxy kökü")
    p.add_argument("--timeout", type=float, default=20)
    p.add_argument("--tcp", action="append", default=[], metavar="HOST:PORT", help="ek TCP port testi")
    p.add_argument("--only", nargs="*", metavar="KOD", help="yalnızca bu kodlar")
    args = p.parse_args()

    ctx, extra = ssl_context(args.ca_file)
    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else urllib.request.getproxies()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies),
                                         urllib.request.HTTPSHandler(context=ctx))

    print(f"Python {sys.version.split()[0]} | {socket.gethostname()} | {ssl.OPENSSL_VERSION}")
    print(f"Proxy : {proxies.get('https') or '(yok — doğrudan)'}  NO_PROXY={os.environ.get('NO_PROXY') or os.environ.get('no_proxy') or '-'}")
    print(f"Ek CA : {', '.join(os.path.basename(x) for x in extra) or '(yok)'}\n")

    failures = 0
    for code, url in TARGETS:
        if args.only and code not in args.only:
            continue
        t0 = time.monotonic()
        ok, detail = check_http(code, url, opener, args.timeout)
        failures += not ok
        print(f"{'OK  ' if ok else 'HATA'} {code:<13} {urlparse(url).hostname:<24} {time.monotonic() - t0:5.1f}s  {detail}")
    for target in args.tcp:
        t0 = time.monotonic()
        ok, detail = check_tcp(target, args.timeout)
        failures += not ok
        print(f"{'OK  ' if ok else 'HATA'} {'TCP':<13} {target:<24} {time.monotonic() - t0:5.1f}s  {detail}")

    print(f"\n{'Tümü erişilebilir.' if not failures else f'{failures} hedefte sorun var.'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
