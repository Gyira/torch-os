"""生活运营目录重排：取消独立体检/医疗，所有健康数据归到 `02_健康系统`。"""

from __future__ import annotations

import json
from pathlib import Path

from backend.app import vault_layout


def _write(path: Path, text: str = "正文\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_ops_layout_moves_medical_into_health_and_renumbers(tmp_path):
    ops = tmp_path / "02_生活运营"
    files = {
        "03_体检与医疗/2026 体检.md": "体检",
        "04_证件与事务/身份证.md": "证件",
        "07_财务报告/2026-10 财务报告.md": "财务",
        "08_生活建议/生活建议.md": "建议",
        "09_家庭与智能家居/设备.md": "家庭",
        "05_情感与记忆/旧回忆.md": "旧恋爱",
        "02_财富与记账/2026-10.md": "旧账",
    }
    for relative, body in files.items():
        _write(ops / relative, body)

    result = vault_layout.ensure_ops_layout(tmp_path)
    assert result["status"] == "done", result
    assert result["moved"] >= 7, result
    assert (ops / "02_健康系统/03_体检与检验/2026 体检.md").is_file()
    assert (ops / "03_证件与事务/身份证.md").is_file()
    assert (ops / "04_财务报告/2026-10 财务报告.md").is_file()
    assert (ops / "05_生活建议/生活建议.md").is_file()
    assert (ops / "06_家庭与智能家居/设备.md").is_file()
    assert (ops / "07_情感与记忆/旧回忆.md").is_file()
    assert (ops / "08_财富与记账/2026-10.md").is_file()
    for old in ("03_体检与医疗", "04_证件与事务", "07_财务报告",
                "08_生活建议", "09_家庭与智能家居", "05_情感与记忆",
                "02_财富与记账"):
        assert not (ops / old).exists(), old

    marker = json.loads((tmp_path / ".torch-os/ops-layout.json").read_text(encoding="utf-8"))
    assert marker["schema"] == vault_layout.OPS_LAYOUT_SCHEMA
    assert marker["status"] == "done"
    assert marker["backup"] and (tmp_path / marker["backup"]).is_dir()
    assert (tmp_path / marker["backup"] / "02_生活运营/04_证件与事务/身份证.md").is_file()

    # 幂等：第二次只读标记，不再搬文件。
    again = vault_layout.ensure_ops_layout(tmp_path)
    assert again["already_done"] is True
    assert again["moved"] == 0


def test_ops_layout_conflict_keeps_both_files(tmp_path):
    ops = tmp_path / "02_生活运营"
    _write(ops / "04_证件与事务/同名.md", "旧")
    _write(ops / "03_证件与事务/同名.md", "新")
    result = vault_layout.ensure_ops_layout(tmp_path)
    assert result["status"] == "partial"
    assert any("同名.md" in item for item in result["conflicts"])
    assert (ops / "04_证件与事务/同名.md").read_text(encoding="utf-8") == "旧"
    assert (ops / "03_证件与事务/同名.md").read_text(encoding="utf-8") == "新"


def test_ops_layout_paths_are_the_new_numbering():
    assert vault_layout.chapter_dir("affairs") == "02_生活运营/03_证件与事务"
    assert vault_layout.chapter_dir("finance_report") == "02_生活运营/04_财务报告"
    assert vault_layout.chapter_dir("life_advice") == "02_生活运营/05_生活建议"
    assert vault_layout.chapter_dir("family") == "02_生活运营/06_家庭与智能家居"
    assert vault_layout.chapter_dir("love_memory") == "02_生活运营/07_情感与记忆"
    assert vault_layout.chapter_dir("finance") == "02_生活运营/08_财富与记账"
    assert "medical" not in vault_layout.CHAPTER_BY_KEY
    assert not any(item[3] == "02_生活运营/03_体检与医疗"
                   for item in vault_layout.CHAPTERS)


def test_ensure_layout_does_not_create_retired_or_legacy_dirs(tmp_path):
    vault_layout.ensure_layout(tmp_path)
    for retired in ("02_生活运营/03_体检与医疗", "02_生活运营/07_财务报告",
                    "02_生活运营/08_生活建议", "02_生活运营/09_家庭与智能家居"):
        assert not (tmp_path / retired).exists(), retired
    # 旧只读章节不再自动建空目录，但写入时仍可按 chapter_dir 创建。
    assert not (tmp_path / "02_生活运营/07_情感与记忆").exists()
    assert not (tmp_path / "02_生活运营/08_财富与记账").exists()
    assert (tmp_path / "02_生活运营/03_证件与事务").is_dir()
    assert (tmp_path / "02_生活运营/04_财务报告").is_dir()
    assert (tmp_path / "02_生活运营/05_生活建议").is_dir()
    assert (tmp_path / "02_生活运营/06_家庭与智能家居").is_dir()
    assert (tmp_path / "02_生活运营/02_健康系统/03_体检与检验").is_dir()
