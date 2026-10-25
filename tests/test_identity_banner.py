"""工作台横幅：身份（identity）.md 的 mottos/heroes/overlay/focal/mode 读写与 hero 图片端点。"""

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database


def make_client(tmp_path):
    app = create_app(
        Database(tmp_path / "banner.sqlite3"), tmp_path / "vault",
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client


def identity_path(tmp_path):
    return tmp_path / "vault" / "01_自我档案" / "01_内核与三观" / "身份（identity）.md"


def seed_identity(tmp_path):
    path = identity_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: identity\nvisibility: private\nname: 林小舟\nmottos:\n  - '旧座右铭'\nhero: image/old.png\n---\n\n这里是正文。\n",
        encoding="utf-8")
    return path


def test_read_falls_back_from_single_hero_and_uses_defaults(tmp_path):
    client = make_client(tmp_path)
    seed_identity(tmp_path)
    payload = client.get("/api/identity").json()
    assert payload["name"] == "林小舟"
    assert payload["mottos"] == ["旧座右铭"]
    assert payload["heroes"] == ["image/old.png"]  # heroes 缺省时从 hero 回退
    assert payload["hero_overlay"] == 0.45 and payload["banner_mode"] == "quote"


def test_banner_write_updates_only_banner_fields_and_preserves_the_rest(tmp_path):
    client = make_client(tmp_path)
    seed_identity(tmp_path)
    revision = client.get("/api/identity").json()["revision"]

    saved = client.put("/api/identity/banner", json={
        "mottos": ["深度理解 -> 制定系统 -> 坚决行动", "把每天过成想要的样子"],
        "heroes": ["image/a.png", "https://example.com/b.jpg"],
        "hero_overlay": 0.6,
        "hero_focal": "50% 40%",
        "banner_mode": "stats",
        "banner_accent": "#ff8800",
        "revision": revision,
    })
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["name"] == "林小舟"  # 表单字段原样保留
    assert payload["mottos"][0].startswith("深度理解")
    assert payload["heroes"] == ["image/a.png", "https://example.com/b.jpg"]
    assert payload["hero_overlay"] == 0.6
    assert payload["hero_focal"] == "50% 40%"
    assert payload["banner_mode"] == "stats"
    assert payload["banner_accent"] == "#ff8800"
    assert "这里是正文。" in identity_path(tmp_path).read_text(encoding="utf-8")

    # 强调色可清回默认（空 = 荧光绿）；非法色值 422。
    cleared = client.put("/api/identity/banner", json={
        "mottos": payload["mottos"], "heroes": payload["heroes"],
        "banner_accent": "", "revision": payload["revision"]})
    assert cleared.status_code == 200 and cleared.json()["banner_accent"] == ""

    stale = client.put("/api/identity/banner", json={"mottos": ["x"], "revision": "deadbeefdeadbeef"})
    assert stale.status_code == 409

    bad_focal = client.put("/api/identity/banner", json={
        "mottos": [], "heroes": [], "hero_focal": "五十", "revision": cleared.json()["revision"]})
    assert bad_focal.status_code == 422
    bad_accent = client.put("/api/identity/banner", json={
        "mottos": [], "heroes": [], "banner_accent": "#zzzzzz", "revision": cleared.json()["revision"]})
    assert bad_accent.status_code == 422


def test_banner_font_round_trips_and_survives_a_profile_write(tmp_path):
    """字体（2026-10-05）：写回 身份（identity）.md、可清回默认、非法值 422，且不被档案表单冲掉。"""
    client = make_client(tmp_path)
    seed_identity(tmp_path)
    revision = client.get("/api/identity").json()["revision"]

    font = '"LXGW WenKai","霞鹜文楷",serif'
    saved = client.put("/api/identity/banner", json={
        "mottos": ["深度理解 -> 制定系统 -> 坚决行动"], "heroes": [],
        "banner_font": font, "revision": revision})
    assert saved.status_code == 200, saved.text
    assert saved.json()["banner_font"] == font
    assert "banner_font" in identity_path(tmp_path).read_text(encoding="utf-8")

    # 另一张表单（个人档案）保存时，横幅字体要原样保留。
    profile = client.put("/api/identity", json={
        "name": "林小舟", "motto": "把每天过成想要的样子",
        "revision": saved.json()["revision"]})
    assert profile.status_code == 200, profile.text
    assert profile.json()["banner_font"] == font

    # 清回默认：键从 front-matter 里删掉，读回来是空。
    refreshed = client.get("/api/identity").json()
    cleared = client.put("/api/identity/banner", json={
        "mottos": ["深度理解 -> 制定系统 -> 坚决行动"], "heroes": [],
        "banner_font": "", "revision": refreshed["revision"]})
    assert cleared.status_code == 200 and cleared.json()["banner_font"] == ""
    assert "banner_font" not in identity_path(tmp_path).read_text(encoding="utf-8")

    # 能拼出别的 CSS 声明的值直接 422，不留到样式里。
    bad = client.put("/api/identity/banner", json={
        "mottos": ["深度理解 -> 制定系统 -> 坚决行动"], "heroes": [],
        "banner_font": "serif;} body{color:red", "revision": cleared.json()["revision"]})
    assert bad.status_code == 422


def test_read_drops_a_hand_written_unsafe_font(tmp_path):
    """手写进 身份（identity）.md 的怪字体读回来当空——前端只会拿到安全的字体栈。"""
    client = make_client(tmp_path)
    path = identity_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: identity\nbanner_font: 'serif;} body{color:red'\n---\n\n正文\n",
                    encoding="utf-8")
    assert client.get("/api/identity").json()["banner_font"] == ""


def test_hero_endpoint_serves_vault_images_by_index(tmp_path):
    client = make_client(tmp_path)
    seed_identity(tmp_path)
    image = tmp_path / "vault" / "image" / "a.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    revision = client.get("/api/identity").json()["revision"]
    client.put("/api/identity/banner", json={
        "mottos": [], "heroes": ["image/a.png", "https://example.com/b.jpg"],
        "revision": revision})

    first = client.get("/api/identity/hero?i=0")
    assert first.status_code == 200 and first.content.startswith(b"\x89PNG")
    assert client.get("/api/identity/hero?i=1").status_code == 404  # 网络图不走服务
    assert client.get("/api/identity/hero?i=5").status_code == 404
