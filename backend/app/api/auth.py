"""Kimlik doğrulama (plan §8). Backend'in Keycloak'a ağ erişimi yok: token çevrimdışı doğrulanır.

AUTH_MODE=disabled  demo/yerel: token aranmaz; kullanıcı ``X-Demo-User`` başlığından (URL kodlamalı) veya
                    DEMO_USER_NAME'den; tüm roller.
AUTH_MODE=keycloak  ``Authorization: Bearer <JWT>`` zorunlu. İmza, KEYCLOAK_PUBLIC_KEYS_DIR altındaki realm RS256 açık
                    anahtar(lar)ıyla doğrulanır (JWKS çağrısı yok). Anahtar rotasyonu için birden fazla ``.pem``
                    konabilir; token başlığındaki ``kid`` dosya adıyla (``<kid>.pem``) eşleşirse o anahtar, yoksa
                    hepsi denenir. ``iss``, ``aud``/``azp``, ``exp``/``nbf`` (±leeway) kontrol edilir.
Roller: realm_access.roles ∪ resource_access.<client>.roles → mevzuat_viewer | mevzuat_expert | mevzuat_admin.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

import jwt
from fastapi import Request

from app.services.audit import Actor
from app.services.regulations import ApiError
from app.settings import Settings

ROLE_VIEWER, ROLE_EXPERT, ROLE_ADMIN = "mevzuat_viewer", "mevzuat_expert", "mevzuat_admin"
ALL_ROLES = {ROLE_VIEWER, ROLE_EXPERT, ROLE_ADMIN}


@lru_cache(maxsize=4)
def _load_keys(directory: str, mtime: float) -> dict[str, object]:
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    keys = {}
    for path in sorted(Path(directory).glob("*.pem")):
        keys[path.stem] = load_pem_public_key(path.read_bytes())
    return keys


def public_keys(settings: Settings) -> dict[str, object]:
    d = settings.keycloak_public_keys_dir
    if not d.is_dir():
        return {}
    return _load_keys(str(d), max((p.stat().st_mtime for p in d.glob("*.pem")), default=0.0))


def verify_token(token: str, settings: Settings) -> dict:
    keys = public_keys(settings)
    if not keys:
        raise ApiError(500, "auth_misconfigured", "Keycloak açık anahtarı yapılandırılmamış")
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as e:
        raise ApiError(401, "invalid_token", f"token çözümlenemedi: {e}") from e
    candidates = [keys[kid]] if kid in keys else list(keys.values())
    last: Exception | None = None
    for key in candidates:
        try:
            claims = jwt.decode(token, key, algorithms=["RS256"], issuer=settings.keycloak_issuer,
                                leeway=settings.keycloak_leeway_s, options={"verify_aud": False,
                                                                            "require": ["exp", "iss"]})
        except jwt.InvalidSignatureError as e:
            last = e
            continue
        except jwt.PyJWTError as e:
            raise ApiError(401, "invalid_token", str(e)) from e
        aud = claims.get("aud") or []
        aud = [aud] if isinstance(aud, str) else aud
        if settings.keycloak_client_id not in aud and claims.get("azp") != settings.keycloak_client_id:
            raise ApiError(401, "invalid_token", "token bu istemci için verilmemiş (aud/azp)")
        return claims
    raise ApiError(401, "invalid_token", f"imza doğrulanamadı: {last}")


def roles_from_claims(claims: dict, client_id: str) -> set[str]:
    roles = set((claims.get("realm_access") or {}).get("roles", []))
    roles |= set(((claims.get("resource_access") or {}).get(client_id) or {}).get("roles", []))
    return roles & ALL_ROLES


def current_actor(request: Request, settings: Settings) -> Actor:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    if settings.auth_mode == "disabled":
        # HTTP başlıkları Türkçe karakter taşıyamadığı için URL kodlamalı gönderilir ("Ay%C5%9Fe%20Kaya")
        name = unquote(request.headers.get("x-demo-user") or "") or settings.demo_user_name
        return Actor(id=f"demo:{name}", name=name, title=settings.demo_user_title, roles=set(ALL_ROLES),
                     client_ip=ip, user_agent=ua)
    if settings.auth_mode != "keycloak":
        raise ApiError(500, "auth_misconfigured", f"bilinmeyen AUTH_MODE: {settings.auth_mode}")
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise ApiError(401, "missing_token", "Authorization: Bearer <token> gerekli")
    claims = verify_token(header[7:].strip(), settings)
    roles = roles_from_claims(claims, settings.keycloak_client_id)
    if not roles:
        raise ApiError(403, "no_role", "portal rolü yok (mevzuat_viewer | mevzuat_expert | mevzuat_admin)")
    return Actor(id=claims.get("sub") or claims.get("preferred_username"),
                 name=claims.get("name") or claims.get("preferred_username") or "?",
                 title=claims.get("title") or claims.get("department"), roles=roles, client_ip=ip, user_agent=ua)


def require(actor: Actor, *needed: str) -> None:
    """Yetki hiyerarşisi: admin ⊇ expert ⊇ viewer."""
    effective = set(actor.roles)
    if ROLE_ADMIN in effective:
        effective |= {ROLE_EXPERT, ROLE_VIEWER}
    if ROLE_EXPERT in effective:
        effective |= {ROLE_VIEWER}
    if not set(needed) <= effective:
        raise ApiError(403, "forbidden", f"bu işlem için rol gerekli: {', '.join(needed)}")
