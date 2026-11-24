import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.identity import create_identity_router
from backend.app.storage_layout import initialize_empty_workspace, new_layout, register_layout


def banner_client(vault):
    app = FastAPI()
    app.include_router(create_identity_router(vault))
    return TestClient(app)


@pytest.mark.parametrize("split", [False, True])
def test_banner_image_and_crop_move_with_personal_notes(tmp_path, split):
    if split:
        layout = new_layout(tmp_path / "split")
        initialize_empty_workspace(layout)
        register_layout(layout)
        vault = layout.notes
    else:
        vault = tmp_path / "notes"
    client = banner_client(vault)
    image = b"GIF89a" + bytes.fromhex("01000100800000000000ffffff2c00000000010001000002024401003b")
    response = client.post("/api/identity/banner/image", files={"file": ("旧相册.gif", image, "image/gif")})
    assert response.status_code == 200
    relative = response.json()["path"]
    assert (vault / relative).read_bytes() == image
    crop = {"x": 0, "y": 85, "zoom": 2.25}
    # Old identity notes may contain a local absolute path. Saving rewrites both keys.
    original = relative if split else str(vault / relative)
    saved = client.put("/api/identity/banner", json={"heroes": [original], "hero_crops": {original: crop}})
    assert saved.status_code == 200

    moved = tmp_path / "another-device" / "notes"
    shutil.copytree(vault, moved)
    other = banner_client(moved)
    identity = other.get("/api/identity").json()
    assert identity["heroes"] == [relative]
    assert identity["hero_crops"] == {relative: crop}
    assert other.get("/api/identity/hero?i=0").content == image
    assert other.put("/api/identity/banner", json={"heroes": [], "revision": identity["revision"]}).is_success
    assert other.get("/api/identity/banner/images").json()["paths"] == [relative]
    assert other.get("/api/identity/banner/image", params={"path": relative}).content == image
    assert other.get("/api/identity/banner/image", params={"path": "../outside.gif"}).status_code == 404
