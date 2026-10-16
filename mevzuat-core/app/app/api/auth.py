"""Kullanıcı kimliği (plan §8). Kimlik doğrulama yoktur: herkes tüm rollerle girer.

Kullanıcı adı ``X-Demo-User`` başlığından (URL kodlamalı) veya DEMO_USER_NAME'den alınır; denetim izine bu ad yazılır.
Roller (mevzuat_viewer | mevzuat_expert | mevzuat_admin) kod içinde korunur; ileride bir kimlik sağlayıcı eklenirse
``current_actor`` rolleri oradan dolduracak şekilde değiştirilir.
"""
from __future__ import annotations

from urllib.parse import unquote

from fastapi import Request

from app.services.audit import Actor
from app.services.regulations import ApiError
from app.settings import Settings

ROLE_VIEWER, ROLE_EXPERT, ROLE_ADMIN = "mevzuat_viewer", "mevzuat_expert", "mevzuat_admin"
ALL_ROLES = {ROLE_VIEWER, ROLE_EXPERT, ROLE_ADMIN}


def current_actor(request: Request, settings: Settings) -> Actor:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    # HTTP başlıkları Türkçe karakter taşıyamadığı için URL kodlamalı gönderilir ("Ay%C5%9Fe%20Kaya")
    name = unquote(request.headers.get("x-demo-user") or "") or settings.demo_user_name
    return Actor(id=f"demo:{name}", name=name, title=settings.demo_user_title, roles=set(ALL_ROLES),
                 client_ip=ip, user_agent=ua)


def require(actor: Actor, *needed: str) -> None:
    """Yetki hiyerarşisi: admin ⊇ expert ⊇ viewer."""
    effective = set(actor.roles)
    if ROLE_ADMIN in effective:
        effective |= {ROLE_EXPERT, ROLE_VIEWER}
    if ROLE_EXPERT in effective:
        effective |= {ROLE_VIEWER}
    if not set(needed) <= effective:
        raise ApiError(403, "forbidden", f"bu işlem için rol gerekli: {', '.join(needed)}")
