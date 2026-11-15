import json
import re
from pathlib import Path
from backend.app import vault_layout


def test_mobile_creation_paths_match_desktop_business_catalog():
    source = (Path(__file__).resolve().parents[1] / "companion/core/catalog.ts").read_text(encoding="utf-8")
    directories = json.loads(re.search(r"export const directories = (\{.*?\}) as const;", source, re.S).group(1))
    for key, directory in directories.items():
        assert directory == vault_layout.chapter_dir(key)
