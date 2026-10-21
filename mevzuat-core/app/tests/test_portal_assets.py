"""Yeni portal (Faz 6): ``/assets`` yalnızca düz dosya adı ve izinli uzantı sunar."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import create_app
from tests.test_processing import env  # noqa: F401


def test_assets_are_served_from_a_plain_file_name_only(settings, env, tmp_path):  # noqa: F811
    sf, _ = env
    portal = tmp_path / "portal"
    (portal / "assets").mkdir(parents=True)
    (portal / "assets" / "app.js").write_text("window.x=1;")
    (portal / "assets" / "styles.css").write_text("body{margin:0}")
    (portal / "assets" / "data.json").write_text("{}")
    (portal / "secret.txt").write_text("gizli")
    c = TestClient(create_app(settings.model_copy(update={"cors_origins": "", "portal_dir": portal}), sf))
    js = c.get("/assets/app.js")
    assert js.status_code == 200 and js.text == "window.x=1;" and js.headers["content-type"].startswith("application/javascript")
    assert js.headers["cache-control"] == "no-cache" and js.headers["x-content-type-options"] == "nosniff"
    css = c.get("/assets/styles.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    for name in ("yok.js", "data.json", "..%2Fsecret.txt", "..%5Csecret.txt", "%2e%2e%2fsecret.txt", "..%2F..%2Fapp.js"):
        assert c.get(f"/assets/{name}").status_code == 404, name
    assert c.get("/assets/").status_code == 404
