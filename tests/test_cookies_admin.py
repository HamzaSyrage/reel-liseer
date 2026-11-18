import importlib
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

FUTURE = int(time.time()) + 86400 * 30
PAST = int(time.time()) - 86400


def line(domain, name, value="v", expires=FUTURE, path="/", sub="TRUE", secure="TRUE", http_only=False):
    prefix = "#HttpOnly_" if http_only else ""
    return "\t".join([prefix + domain, sub, path, secure, str(expires), name, value])


UNIVERSAL = "\n".join([
    "# Netscape HTTP Cookie File",
    line(".instagram.com", "sessionid", "IGSECRET", http_only=True),
    line(".youtube.com", "SID", "YTSECRET"),
    line(".google.com", "HSID", "GSECRET"),
    line(".mybank.example", "auth", "BANKSECRET"),          # must be dropped
    line(".instagram.com", "old", "x", expires=PAST),         # expired -> dropped
    "garbage line",
]) + "\n"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKIES_DIR", str(tmp_path / "ck"))
    monkeypatch.setenv("ADMIN_TOKEN", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret-token")
    monkeypatch.setenv("ADMIN_USERNAME", "boss")
    from reel_liseer import config
    importlib.reload(config)
    from reel_liseer.services import cookies
    importlib.reload(cookies)
    from reel_liseer import admin
    importlib.reload(admin)
    app = FastAPI()
    app.include_router(admin.router)
    return TestClient(app), cookies, config, tmp_path / "ck"


AUTH = {"Authorization": "Bearer s3cret-token"}


def test_disabled_without_token(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKIES_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_TOKEN", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    from reel_liseer import admin, config
    importlib.reload(config)
    importlib.reload(admin)
    app = FastAPI()
    app.include_router(admin.router)
    c = TestClient(app)
    assert c.get("/admin").status_code == 404
    assert c.get("/admin/api/cookies", headers=AUTH).status_code == 404


def test_auth_required(env):
    c, *_ = env
    assert c.get("/admin").status_code == 200  # login shell, no secrets
    assert c.get("/admin/api/cookies").status_code == 401
    assert c.get("/admin/api/cookies", headers={"Authorization": "Bearer nope"}).status_code == 401
    import base64
    def basic(u, p):
        return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}
    assert c.get("/admin/api/cookies", headers=basic("boss", "wrong")).status_code == 401
    assert c.get("/admin/api/cookies", headers=basic("admin", "s3cret-token")).status_code == 401
    assert c.get("/admin/api/cookies", headers={"Authorization": "Basic !!notb64"}).status_code == 401
    assert c.get("/admin/api/cookies", headers=basic("boss", "s3cret-token")).status_code == 200
    assert c.post("/admin/api/cookies", data={"text": UNIVERSAL}).status_code == 401
    assert c.delete("/admin/api/cookies/universal").status_code == 401
    assert c.get("/admin/api/cookies", headers=AUTH).status_code == 200


def test_throttle(env):
    c, *_ = env
    for _ in range(8):
        assert c.get("/admin/api/cookies", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert c.get("/admin/api/cookies", headers={"Authorization": "Bearer bad"}).status_code == 429
    assert c.get("/admin/api/cookies", headers=AUTH).status_code == 429  # locked out for this client


def test_upload_filters_and_hides_values(env):
    c, cookies, config, d = env
    r = c.post("/admin/api/cookies", headers=AUTH, files={"file": ("cookies.txt", UNIVERSAL)}, data={"slot": "universal"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["kept"] == 3 and j["skipped_domain"] == 1 and j["expired"] == 1 and j["invalid"] == 1
    stored = (d / "universal.txt").read_text()
    assert "BANKSECRET" not in stored and "mybank" not in stored
    assert "IGSECRET" in stored and "#HttpOnly_.instagram.com" in stored
    assert oct((d / "universal.txt").stat().st_mode & 0o777) == "0o600"
    listing = c.get("/admin/api/cookies", headers=AUTH).text
    assert "SECRET" not in listing and "instagram.com" in listing


def test_paste_and_per_site_merge_and_delete(env):
    c, cookies, *_ = env
    c.post("/admin/api/cookies", headers=AUTH, data={"slot": "instagram", "text": line(".instagram.com", "sessionid", "A")})
    c.post("/admin/api/cookies", headers=AUTH, data={"slot": "youtube", "text": line(".youtube.com", "SID", "B")})
    with cookies.cookiefile() as path:
        body = Path(path).read_text()
        assert body.startswith("# Netscape HTTP Cookie File") and "sessionid" in body and "SID" in body
    assert c.delete("/admin/api/cookies/instagram", headers=AUTH).status_code == 200
    with cookies.cookiefile() as path:
        assert "sessionid" not in Path(path).read_text()
    assert c.delete("/admin/api/cookies/instagram", headers=AUTH).status_code == 404
    c.delete("/admin/api/cookies/youtube", headers=AUTH)
    with cookies.cookiefile() as path:
        assert path is None


def test_cookiefile_is_per_job_copy_and_removed(env):
    c, cookies, *_ = env
    c.post("/admin/api/cookies", headers=AUTH, data={"text": line(".tiktok.com", "sid_tt", "A")})
    import os
    with cookies.cookiefile() as a, cookies.cookiefile() as b:
        assert a != b and os.path.exists(a) and os.path.exists(b)
    assert not os.path.exists(a) and not os.path.exists(b)


@pytest.mark.parametrize("payload,status", [
    (b"", 400), (b"hello world", 422), (b"\x00\x01\x02", 422),
    (line(".evil.example", "a").encode(), 422), (b"x" * (3 * 1024 * 1024), 422),
])
def test_bad_uploads(env, payload, status):
    c, *_ = env
    r = c.post("/admin/api/cookies", headers=AUTH, files={"file": ("c.txt", payload)} if payload else None,
               data={} if payload else {"text": ""})
    assert r.status_code == status, r.text


def test_space_separated_paste_and_slot_sanitised(env):
    c, cookies, _, d = env
    spaced = line(".reddit.com", "reddit_session", "tok").replace("\t", "  ")
    r = c.post("/admin/api/cookies", headers=AUTH, data={"slot": "../../etc/passwd", "text": spaced})
    assert r.status_code == 200 and r.json()["slot"] == "etc-passwd"
    assert not any(p.name.startswith("..") for p in d.iterdir())


def test_env_seed(tmp_path, monkeypatch):
    import base64
    monkeypatch.setenv("COOKIES_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("COOKIES_B64", base64.b64encode(UNIVERSAL.encode()).decode())
    from reel_liseer import config
    importlib.reload(config)
    from reel_liseer.services import cookies
    importlib.reload(cookies)
    cookies.seed_from_env()
    assert (tmp_path / "s" / "env.txt").is_file()
    assert cookies.status()[0]["count"] == 3
