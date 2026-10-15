"""文本完整性与结构守卫：把踩过的坑变成机器能拦住的检查。

这些规则全都来自真实事故（2026-10-05 那一轮）：

1. `去 BOM` 的脚本写错，把 9 个文件的首字符删掉了 —— 编码/首字符/替换字符三项检查。
2. 帮助段的 `</section>` 被漏掉，新分区被塞进隐藏的父节点，页面一片空白 —— `<section>` 配对检查。
3. 三个模块还在判断旧页面名 `state.view === "today"`，工作台上的建议永不刷新 —— 视图名必须来自 MODULES。
4. Python 模块首行的 `\"\"\"` 被吃掉一个引号，整个后端 import 失败 —— 首字符是引号时必须是三引号。

它只读文本、不联网、不改任何文件；失败时打印文件与行号。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if len(sys.argv) > 1:
    # 自测用：允许指向一棵临时树，注入“历史上的坑”后要求检查器报错。
    ROOT = Path(sys.argv[1]).resolve()

# 报告里会出现 U+FFFD、中文与文件名：GBK 控制台直接 print 会抛 UnicodeEncodeError
# （实测：检查器本该报错，结果自己先崩了，stdout 全空）。统一按 UTF-8 输出。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - 老解释器/被重定向
    pass
SKIP_DIRS = {".git", ".venv", "node_modules", "data", "__pycache__", ".test-data", "dist", "artifacts"}
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".md", ".html", ".css", ".json", ".yml", ".yaml", ".ps1", ".txt"}
EXPECTED_FIRST = {
    "frontend/index.html": "<!doctype html>",
    "README.md": "# 薪火OS",
    "backend/app/application.py": '"""',
}
BOM = "\ufeff"
REPLACEMENT = "\ufffd"


def text_files() -> list[Path]:
    found: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        found.append(path)
    return found


def module_keys() -> set[str] | None:
    """app.js 被改坏（编码坏掉、MODULES 被删）时返回 None，由调用方当成问题报出来。"""
    try:
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    block = re.search(r"const MODULES = \{(.*?)\n\};", source, re.S)
    if not block:
        return None
    keys = set(re.findall(r"\n  (\w+): \{", block.group(1)))
    return keys or None


def check_encoding(path: Path, problems: list[str]) -> None:
    raw = path.read_bytes()
    rel = path.relative_to(ROOT)
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    if path.suffix == ".ps1":
        # 实测来的反向规则：Windows PowerShell 5.1 读**无 BOM** 的 UTF-8 会把中文当 ANSI（GBK）
        # 解码，脚本直接语法错（2026-10-05 把 check_all.ps1 的 BOM 编辑掉，整条校验链当场失效）。
        # 所以带中文的 .ps1 必须留 BOM，其余文本文件必须不留。
        if any(byte > 127 for byte in raw) and not has_bom:
            problems.append(f"{rel}: 含中文却没有 UTF-8 BOM（Windows PowerShell 5.1 会读成乱码、脚本语法错）")
    elif has_bom:
        problems.append(f"{rel}: 文件带 UTF-8 BOM（本仓库文本一律无 BOM；.ps1 例外）")
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        problems.append(f"{rel}: 文件是 UTF-16（PowerShell 的 `>` 重定向会造成这种后果）")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        problems.append(f"{rel}: 不是合法 UTF-8（{error.reason} at byte {error.start}）")
        return
    if REPLACEMENT in text:
        first = next(index for index, line in enumerate(text.splitlines(), 1) if REPLACEMENT in line)
        problems.append(f"{rel}:{first}: 含替换字符 U+FFFD（解码/编码往返把内容弄坏了）")
    lines = text.splitlines()
    if lines and REPLACEMENT not in text:
        head = lines[0]
        if head and not head.strip():
            problems.append(f"{rel}: 首行是空白（首字符被误删过）")
    expected = EXPECTED_FIRST.get(str(rel).replace("\\", "/"))
    if expected and not text.startswith(expected):
        problems.append(f"{rel}: 首行应以 {expected!r} 开头，实际是 {text[:24]!r}")
    if path.suffix == ".py" and lines:
        head = lines[0].lstrip()
        if head.startswith('"') and not head.startswith('"""'):
            problems.append(f"{rel}: 模块首行的三引号被破坏（{head[:32]!r}）")


def check_sections(path: Path, problems: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    stack: list[int] = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in re.finditer(r"<section\b[^>]*>|</section>", line):
            if match.group(0) == "</section>":
                if not stack:
                    problems.append(f"{path.relative_to(ROOT)}:{number}: 多出一个 </section>")
                else:
                    stack.pop()
            else:
                stack.append(number)
    for number in stack:
        problems.append(f"{path.relative_to(ROOT)}:{number}: <section> 没有闭合（会把后面的分区塞进隐藏的父节点）")


def check_view_names(problems: list[str]) -> None:
    keys = module_keys()
    if not keys:
        problems.append("frontend/app.js: 读不到 MODULES（文件被改坏，或模块定义被删）")
        return
    for path in (ROOT / "frontend").glob("*.js"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for view in re.findall(r'state\.view === "([a-z-]+)"', line):
                if view not in keys:
                    problems.append(f"{path.relative_to(ROOT)}:{number}: state.view === \"{view}\" 不是模块名"
                                    f"（模块只有：{', '.join(sorted(keys))}）")


def main() -> int:
    problems: list[str] = []
    for path in text_files():
        check_encoding(path, problems)
    check_sections(ROOT / "frontend" / "index.html", problems)
    check_view_names(problems)
    if problems:
        print("文本与结构检查未通过：")
        for item in problems:
            print(" -", item)
        return 1
    print("文本与结构检查通过（编码/首字符/section 配对/视图名）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
