"""把 openGym 的动作目录整理成健康系统可用的本地动作库（2026-10-31）。

来源：``参考开源项目/openGym-main/catalogue``
- ``exercises/<id>.json``：英文名、部位、器械、目标肌群、说明与步骤（exercisedb 文本，MIT）；
- ``i18n/zh.json``：中文名/说明/步骤（缺中文时回退英文）。

只复制**文本**，不复制 ``media/``（openGym 的图片与动画是它自己的授权素材，
不能转存）。输出一个紧凑 JSON：``backend/app/health_assets/exercises.json``。

用法：
    .venv\\Scripts\\python.exe scripts\\import_opengym_exercises.py [--source <目录>]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path(r"C:\Users\19814\Desktop\参考开源项目\openGym-main\catalogue")
OUTPUT = ROOT / "backend" / "app" / "health_assets" / "exercises.json"
REPLACEMENT = "\ufffd"
MAX_STEPS = 6
MAX_DESCRIPTION = 220


def _text(value: object, limit: int = 0) -> str:
    text = str(value or "").strip()
    if REPLACEMENT in text:
        return ""
    text = " ".join(text.split())
    return text[:limit] if limit else text


def build(source: Path, *, include_steps: bool = False) -> dict:
    translations_path = source / "i18n" / "zh.json"
    try:
        translations = json.loads(translations_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        translations = {}
    items: list[dict] = []
    seen: set[str] = set()
    for path in sorted((source / "exercises").glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        exercise_id = _text(raw.get("id")) or path.stem
        zh = translations.get(exercise_id) if isinstance(translations, dict) else None
        zh = zh if isinstance(zh, dict) else {}
        name = _text(zh.get("name"), 80) or _text(raw.get("name"), 80)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        description = _text(zh.get("description"), MAX_DESCRIPTION) \
            or _text(raw.get("description"), MAX_DESCRIPTION)
        steps = []
        if include_steps:
            raw_steps = zh.get("instructions") or raw.get("instructions") or []
            if isinstance(raw_steps, list):
                for step in raw_steps[:3]:
                    cleaned = _text(step, 160)
                    if cleaned:
                        steps.append(cleaned)
        item = {
            "id": exercise_id,
            "name": name,
            "body": _text(raw.get("bodyPart"), 30),
            "equipment": _text(raw.get("equipment"), 30),
            "target": _text(raw.get("target"), 40),
            "category": _text(raw.get("category"), 20),
            "description": description,
            "steps": steps,
        }
        items.append(item)
    return {
        "schema": 1,
        "source": "openGym catalogue (github.com/DuarteSantos8/openGym), MIT / exercisedb text",
        "generated": date.today().isoformat(),
        "count": len(items),
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成健康系统动作库")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(OUTPUT))
    parser.add_argument("--with-steps", action="store_true",
                        help="附带前 3 步中文说明（默认只留一句描述，控制仓库体积）")
    args = parser.parse_args()
    source = Path(args.source)
    if not (source / "exercises").is_dir():
        print(f"找不到动作目录：{source / 'exercises'}", file=sys.stderr)
        return 2
    payload = build(source, include_steps=bool(args.with_steps))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                      encoding="utf-8", newline="\n")
    size = output.stat().st_size / 1024
    print(f"写入 {payload['count']} 个动作 → {output}（{size:.0f} KiB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
