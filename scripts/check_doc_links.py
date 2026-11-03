#!/usr/bin/env python3
"""检查仓库内 Markdown 的相对链接是否指向存在的文件或目录。

基线 scripts/doc_links_baseline.txt 记录冻结文档里已知的断链，每处一行，
同一处出现几次就写几行。默认模式只报告基线之外的断链，末行 ``BROKEN N``，
N 为 0 时退出码 0，否则 1。锚点不检查。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "scripts" / "doc_links_baseline.txt"
EXCLUDE_DIRS = frozenset({
    ".git", ".venv", "node_modules", ".pnpm-store", "static", "resource", ".pytest_cache",
    ".idea", ".vscode", ".superpowers", ".archify", "debug", "__pycache__",
})
INLINE_LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
REF_LINK = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)")
HTML_ATTR = re.compile(r"(?:src|href)=\"([^\"]+)\"")
CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "/", "data:", "tel:", "javascript:")
BASELINE_HEADER = (
    "# 已知断链基线，scripts/check_doc_links.py 不把这里的条目算作新增断链。\n"
    "# 只允许 docs/v2/、docs/copy-audit/、tests/fixtures/ 下的文件；条目只减不增。\n"
    "# 每行「<文件>: <链接目标>」，同一处断链出现几次就写几行。\n"
)


def iter_markdown(root: Path) -> list[Path]:
    """os.walk 遍历，跳过 EXCLUDE_DIRS，返回排好序的 .md 路径。"""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDE_DIRS)
        found.extend(Path(dirpath) / name for name in filenames if name.endswith(".md"))
    return sorted(found)


def is_relative(target: str) -> bool:
    """非空、不以 SKIP_PREFIXES 开头、不含 ``://``。"""
    return bool(target) and not target.startswith(SKIP_PREFIXES) and "://" not in target


def extract_links(text: str) -> list[str]:
    """按行扫：``` 或 ~~~ 开头的行切换围栏状态，围栏内跳过；
    围栏外先用 CODE_SPAN 去掉行内代码，再收集 INLINE_LINK、HTML_ATTR 与行首的 REF_LINK；
    只返回 is_relative 的目标，保持出现顺序。"""
    links: list[str] = []
    fence = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = not fence
            continue
        if fence:
            continue
        cleaned = CODE_SPAN.sub("", line)
        found: list[tuple[int, str]] = []
        found.extend((match.start(), match.group(1)) for match in INLINE_LINK.finditer(cleaned))
        found.extend((match.start(), match.group(1)) for match in HTML_ATTR.finditer(cleaned))
        definition = REF_LINK.match(cleaned)
        if definition:
            found.append((definition.start(1), definition.group(1)))
        for _, target in sorted(found):
            if is_relative(target):
                links.append(target)
    return links


def find_broken(root: Path) -> list[str]:
    """对每个文件的每个目标：去掉 # 或 ? 之后的部分，空则跳过；
    相对文件所在目录 unquote 后 normpath，root 下不存在就记 "<相对 root 的文件路径>: <原始目标>"。"""
    broken: list[str] = []
    for path in iter_markdown(root):
        rel = path.relative_to(root).as_posix()
        for target in extract_links(path.read_text(encoding="utf-8", errors="replace")):
            part = target.split("#", 1)[0].split("?", 1)[0]
            if not part:
                continue
            resolved = os.path.normpath(
                os.path.join(os.path.dirname(rel), unquote(part))
            ).replace(os.sep, "/")
            if not (root / resolved).exists():
                broken.append(f"{rel}: {target}")
    return broken


def load_baseline(path: Path) -> Counter[str]:
    """文件不存在返回空 Counter；忽略空行与 # 开头的行。"""
    baseline: Counter[str] = Counter()
    if not path.exists():
        return baseline
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            baseline[stripped] += 1
    return baseline


def compare(broken: Iterable[str], baseline: Counter[str]) -> tuple[list[str], list[str]]:
    """返回 (新增, 过期)：新增 = Counter(broken) - baseline，过期 = baseline - Counter(broken)，都展开成排好序的列表。"""
    counts = Counter(broken)
    return sorted((counts - baseline).elements()), sorted((baseline - counts).elements())


def main(argv: list[str] | None = None) -> int:
    """--all 与 --write-baseline 互斥。
    --write-baseline：把当前全部断链排序后写成基线（先写 BASELINE_HEADER），打印 "BASELINE N"，返回 0。
    --all：打印全部断链（含基线里的），末行 "BROKEN <总数>"，返回 0。
    默认：先逐行打印 "stale: <条目>"，再逐行打印新增断链，末行 "BROKEN <新增数>"；新增为 0 返回 0，否则 1。"""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="列出全部断链（含基线里的已知项）")
    group.add_argument("--write-baseline", action="store_true", help="把当前全部断链写成基线文件")
    args = parser.parse_args(argv)

    broken = find_broken(REPO_ROOT)
    if args.write_baseline:
        lines = sorted(broken)
        BASELINE_PATH.write_text(
            BASELINE_HEADER + "".join(f"{line}\n" for line in lines), encoding="utf-8"
        )
        print(f"BASELINE {len(lines)}")
        return 0

    baseline = load_baseline(BASELINE_PATH)
    if args.all:
        for line in sorted(broken):
            print(line)
        print(f"BROKEN {len(broken)}")
        return 0

    new, stale = compare(broken, baseline)
    for line in stale:
        print(f"stale: {line}")
    for line in new:
        print(line)
    print(f"BROKEN {len(new)}")
    return 0 if not new else 1


if __name__ == "__main__":
    sys.exit(main())
