"""个人八字系统：命盘 profile、排盘、AI 解读与历史。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import self_bazi, vault_layout


class FakeBaziRole:
    def __init__(self):
        self.calls = []

    def run_once(self, role_id, mode, input_text, *, entry="", selectors=None,
                 max_chars=None, runner=None):
        self.calls.append({"role_id": role_id, "mode": mode, "entry": entry})
        return {
            "structured": {
                "title": "综合解读",
                "summary": "丙火身弱，先补木火。",
                "sections": [{"title": "性格", "body": "务实、重稳定。"}],
                "favor": ["木", "火"],
                "avoid": ["金", "水"],
                "years": [{"year": "2027", "gz": "丁未", "text": "适合定事", "tone": "up"}],
                "advice": [{"title": "作息", "body": "23 点前睡。"}],
                "boundary": "八字是传统文化参考。",
            },
            "text": "", "sources": [{"id": "S1", "title": "命盘"}],
            "truncated": False, "seconds": 0.2,
        }


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault_layout.ensure_layout(vault)
    return vault


def test_profile_missing_reads_empty(tmp_path):
    vault = make_vault(tmp_path)
    view = self_bazi.profile_view(vault)
    assert view["exists"] is False
    assert view["chart"] is None


def test_manual_pillars_profile_and_chart(tmp_path):
    vault = make_vault(tmp_path)
    view = self_bazi.write_profile(vault, {
        "name": "王衡岳", "sex": "male", "pillars_manual": "癸未 辛酉 丙戌 戊子",
    })
    assert view["exists"] is True
    chart = view["chart"]
    assert [row["gan"] + row["zhi"] for row in chart["pillars"]] == [
        "癸未", "辛酉", "丙戌", "戊子"]
    assert chart["day_master"] == "丙火"
    assert chart["elements"]["火"] >= 1
    assert (vault / vault_layout.SELF_BAZI_ROOT / "命盘.md").is_file()


def test_birth_date_chart_and_revision_conflict(tmp_path):
    vault = make_vault(tmp_path)
    first = self_bazi.write_profile(vault, {
        "name": "王衡岳", "sex": "male", "birth_date": "2003-09-10",
        "birth_time": "00:00", "birth_place": "江西赣州",
    })
    assert first["chart"]["day_master"] == "丙火"
    with pytest.raises(self_bazi.BaziError):
        self_bazi.write_profile(vault, {
            "name": "王衡岳", "sex": "male", "birth_date": "2003-09-10",
            "birth_time": "00:00", "revision": "wrong",
        })
    updated = self_bazi.write_profile(vault, {
        "name": "王衡岳", "sex": "male", "birth_date": "2003-09-10",
        "birth_time": "01:00", "revision": first["revision"],
    })
    assert updated["birth_time"] == "01:00"


def test_generate_and_list_delete_reading(tmp_path):
    vault = make_vault(tmp_path)
    self_bazi.write_profile(vault, {
        "name": "王衡岳", "sex": "male", "pillars_manual": "癸未 辛酉 丙戌 戊子",
    })
    fake = FakeBaziRole()
    item = self_bazi.generate_reading(fake, vault, topic="综合")
    assert fake.calls[0]["mode"] == "bazi"
    assert item["topic"] == "综合"
    assert "丙火身弱" in item["body"]
    rows = self_bazi.list_readings(vault)
    assert len(rows) == 1
    read = self_bazi.read_reading(vault, rows[0]["name"])
    assert read["title"] == "综合解读"
    deleted = self_bazi.delete_reading(vault, rows[0]["name"])
    assert deleted["deleted"] is True
    assert not self_bazi.list_readings(vault)
    assert (vault / deleted["archived_as"]).is_file()


def test_chart_context_has_four_pillars(tmp_path):
    vault = make_vault(tmp_path)
    self_bazi.write_profile(vault, {
        "name": "王衡岳", "sex": "male", "pillars_manual": "癸未 辛酉 丙戌 戊子",
    })
    text = self_bazi.chart_context(vault)
    assert "癸未 辛酉 丙戌 戊子" in text
    assert "丙火" in text
