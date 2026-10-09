"""Eksik TLS zinciri tamamlama.

Bazı kamu siteleri (BDDK; Türkiye'den bağlanıldığında Resmî Gazete ve mevzuat.gov.tr) TLS el sıkışmasında ara
sertifikayı göndermiyor. Tarayıcılar eksik halkayı sertifikadaki AIA ("CA Issuers") adresinden kendileri indirir,
curl/httpx/Python indirmez ve "unable to get local issuer certificate" hatası verir.

``fetch_intermediate`` sunucunun yaprak sertifikasını doğrulamasız okur, AIA adresinden ara sertifikayı indirir ve
yaprak + ara sertifika zincirini güvenilir kök deposuna (certifi veya CA_BUNDLE) karşı **doğrular**. Yalnızca doğrulanan
ara sertifika ``config/certs/`` altına yazılır; güven her zaman kök sertifikaya dayanır.
"""
from __future__ import annotations

import datetime as dt
import re
import socket
import ssl
import warnings
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID, NameOID
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError


@dataclass
class IntermediateResult:
    host: str
    subject: str
    not_after: dt.datetime
    sha256: str
    pem: bytes
    aia_url: str

    @property
    def filename(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.subject.lower()).strip("-") + ".pem"

    def file_content(self) -> bytes:
        header = (f"# {self.subject}\n"
                  f"# Eksik zinciri tamamlar: {self.host}\n"
                  f"# Kaynak (AIA): {self.aia_url}\n"
                  f"# SHA256: {self.sha256}\n"
                  f"# Geçerlilik sonu: {self.not_after:%Y-%m-%d}\n")
        return header.encode("utf-8") + self.pem


def leaf_certificate(host: str, port: int = 443, timeout: float = 15) -> x509.Certificate:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # yalnızca okumak için; doğrulama aşağıda kök depoya karşı yapılır
    with socket.create_connection((host, port), timeout=timeout) as sock, \
            ctx.wrap_socket(sock, server_hostname=host) as tls:
        der = tls.getpeercert(binary_form=True)
    return x509.load_der_x509_certificate(der)


def _load_store(ca_bundle: str | bool) -> Store:
    if isinstance(ca_bundle, str):
        path = Path(ca_bundle)
    else:
        import certifi

        path = Path(certifi.where())
    with warnings.catch_warnings():  # certifi içindeki eski bir kökün negatif seri numarası uyarısı
        warnings.simplefilter("ignore")
        return Store(x509.load_pem_x509_certificates(path.read_bytes()))


def _aia_issuer_urls(cert: x509.Certificate) -> list[str]:
    try:
        aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    except x509.ExtensionNotFound:
        return []
    return [d.access_location.value for d in aia
            if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS]


def _load_any(data: bytes) -> x509.Certificate:
    if b"-----BEGIN" in data:
        return x509.load_pem_x509_certificate(data)
    return x509.load_der_x509_certificate(data)


def fetch_intermediate(host: str, *, ca_bundle: str | bool = True, proxy: str | None = None,
                       port: int = 443) -> IntermediateResult:
    """Sunucunun eksik ara sertifikasını AIA'dan indirir ve zinciri doğrular. Doğrulanamazsa ValueError."""
    leaf = leaf_certificate(host, port)
    urls = _aia_issuer_urls(leaf)
    if not urls:
        raise ValueError(f"{host}: sertifikada AIA 'CA Issuers' adresi yok")
    store = _load_store(ca_bundle)
    verifier = PolicyBuilder().store(store).build_server_verifier(x509.DNSName(host))
    last_error: Exception | None = None
    with httpx.Client(proxy=proxy, timeout=20, follow_redirects=True) as client:
        for url in urls:
            try:
                inter = _load_any(client.get(url).raise_for_status().content)
                verifier.verify(leaf, [inter])
            except (httpx.HTTPError, ValueError, VerificationError) as e:
                last_error = e
                continue
            subject = inter.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
            fp = inter.fingerprint(hashes.SHA256()).hex(":").upper()
            return IntermediateResult(host=host, subject=str(subject), not_after=inter.not_valid_after_utc,
                                      sha256=fp, pem=inter.public_bytes(serialization.Encoding.PEM), aia_url=url)
    raise ValueError(f"{host}: AIA ara sertifikasıyla zincir doğrulanamadı ({last_error})")


def is_chain_error(message: str) -> bool:
    return "unable to get local issuer certificate" in message or "CERTIFICATE_VERIFY_FAILED" in message
