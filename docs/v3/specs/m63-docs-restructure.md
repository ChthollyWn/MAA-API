# M63 v3 文档与 v2 目录迁移（规格）

> 阶段：P0　依赖：无（v2 的 M62 已合并）　分支：`v3-plan`　迁移：无
> 本文是 M63 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在 `v3-plan` 分支，基点是 dev | `git status -sb && git merge-base --is-ancestor dev HEAD && echo ok` | `## v3-plan` 与 `ok` |
| 2 | v2 文档还在 `docs/` 根 | `ls docs/0*.md docs/1*.md docs/README.md docs/DEFECTS.md \| wc -l` | `18`（01–15、README、DEFECTS） |
| 3 | `docs/v2/` 还不存在 | `test -e docs/v2 && echo exists \|\| echo absent` | `absent` |
| 4 | 搬迁前的断链基线 | 附录 A 脚本 `check` 模式 | v3 之外 `BROKEN 25`（2026-11-02 实测：copy-audit 11、v2 spec 13、tests 1） |

## 1. 背景与目标

用户要求把完整执行方案写进 docs，旧方案放 `docs/v2/`，新方案放 `docs/v3/`，spec 要详细到能力较弱的执行者也能直接照做。

目标：

1. `docs/v3/` 下有 README（决策速查表）、01–13、`specs/`（README + M63–M98 共 36 份）。
2. v2 的方案、记录与现行文档整体移到 `docs/v2/`，git 历史可追溯（用 `git mv`）。
3. 所有按路径读文档的代码与测试改指新位置，测试与前端构建保持通过。
4. 所有 markdown 相对链接在搬迁后仍然有效（新增断链为 0）。
5. `AGENTS.md`、根 `README.md`、新的 `docs/README.md` 指向新结构。

## 2. 非目标

- 不改 v2 文档的正文内容（只重写相对链接）。
- 不改代码注释里形如 `docs/13 §8.x` 的文字引用；`docs/v3/README.md` 已说明它们指 `docs/v2/`。
- 不建 `docs/v3/14-现行接口清单.md` 与 `15-开放API接入指南.md`（M64 做）。

## 3. 相关文档

- [docs/v3/README.md](../README.md) 的「给执行者的约定」。
- [12 §3](../12-实施路线与里程碑.md)：分支与 tag。

## 4. 设计

### 4.1 目录结构（完成后）

```
docs/
├── README.md              新建：总索引，指向 v2/、v3/ 与跨版本文档
├── ENVIRONMENT.md         不动（跨版本）
├── assets/                不动（根 README 与 scripts/generate_pwa_icons.py 用）
├── copy-audit/            不动（文案规范与审计脚本产物，scripts/copy_inventory.py 默认输出到这里）
├── ideas/                 不动（未入库的讨论稿）
├── v2/
│   ├── README.md          原 docs/README.md
│   ├── 01-…15-*.md        原 docs/01–15
│   ├── DEFECTS.md
│   └── superpowers/       原 docs/superpowers/（specs、plans、ledgers）
└── v3/
    ├── README.md、01–13
    ├── specs/             README + m63…m98
    ├── plans/             执行时建
    └── ledgers/           执行时建
```

### 4.2 按路径读文档的代码（必须同步改）

| 文件 | 符号 | 改前 | 改后 |
|---|---|---|---|
| `tests/domain/test_errors.py` | `API_SPEC_PATH`（约 39 行） | `REPO_ROOT / "docs" / "05-API规范与路由清单.md"` | `REPO_ROOT / "docs" / "v2" / "05-API规范与路由清单.md"` |
| `tests/domain/test_route_inventory.py` | `API_SPEC_PATH`（约 36 行） | 同上 | 同上 |
| `tests/agent/test_knowledge_service.py` | `SPEC_DIR`（约 45 行） | `REPO_ROOT / "docs" / "superpowers" / "specs"` | `REPO_ROOT / "docs" / "v2" / "superpowers" / "specs"` |
| `scripts/generate_api_guide.py` | `GUIDE_PATH`、`ERROR_SPEC_PATH`（约 15–16 行） | `REPO_ROOT / "docs" / "14-开放API接入指南.md"`、`… / "05-API规范与路由清单.md"` | 中间加 `"v2"` |
| `web/src/features/api-console/GuideMarkdown.tsx` | 第 2 行 import | `'../../../../docs/14-开放API接入指南.md?raw'` | `'../../../../docs/v2/14-开放API接入指南.md?raw'` |
| `.gitignore` | 约 139 行注释 | 「复制到 docs/superpowers/ledgers/」 | 「v3 复制到 docs/v3/ledgers/（v2 的在 docs/v2/superpowers/ledgers/）」 |

核实方法：`rg -n '"docs" /|docs/1[0-9]-|docs/0[0-9]-|docs/superpowers' tests scripts web/src --glob '!**/node_modules/**' --glob '!web/src/types/**'`，只剩注释里的文字引用。

### 4.3 链接重写

用附录 A 的一次性脚本（放 `/tmp/maa_v3/relink.py`，不进仓库）：

- `rewrite` 模式：遍历仓库所有 `.md`（排除 `.git`、`.venv`、`node_modules`、`static`、`resource` 等），对每个相对链接（`](…)`、引用式链接、HTML 的 `src=`/`href=`），按「文件旧位置」解析出目标，再按「目标新位置」与「文件新位置」重算相对路径；跳过围栏代码块；保留锚点与 URL 编码形式。
- `check` 模式：列出所有解析不到的相对链接与总数。

## 5. 实施任务

### T1 写 v3 文档

- 写范围：`docs/v3/**`
- 步骤：写 README、01–13、`specs/README.md` 与 36 份 spec（M63 本文由主 agent 写，M64–M98 可由子代理并行写，主 agent 校对一致性）。
- 验证：`ls docs/v3/*.md | wc -l` → `14`；`ls docs/v3/specs/m*.md | wc -l` → `36`。

### T2 搬迁 v2 文档

- 写范围：`docs/`
- 步骤：
  ```bash
  mkdir -p docs/v2
  git mv docs/README.md docs/DEFECTS.md docs/v2/
  git mv docs/0[1-9]-*.md docs/1[0-5]-*.md docs/v2/
  git mv docs/superpowers docs/v2/superpowers
  ```
- 验证：`ls docs/v2 | wc -l` → `18`（15 份编号文档 + README + DEFECTS + superpowers）；`git status --porcelain | grep -c '^R '` 大于 0。

### T3 改按路径读文档的代码

- 写范围：§4.2 表中的 6 个文件
- 步骤：逐个按表修改。
- 验证：
  ```bash
  .venv/bin/python -m pytest -q tests/domain/test_errors.py tests/domain/test_route_inventory.py tests/agent/test_knowledge_service.py tests/test_api_guide_generator.py > /tmp/m63-t3.log 2>&1; echo $?; tail -n 5 /tmp/m63-t3.log
  ```
  退出码 0。

### T4 重写链接

- 写范围：所有 `.md`
- 步骤：`mkdir -p /tmp/maa_v3`，把附录 A 存成 `/tmp/maa_v3/relink.py`；`.venv/bin/python /tmp/maa_v3/relink.py rewrite`。
- 验证：`.venv/bin/python /tmp/maa_v3/relink.py check 2>&1 | tail -n 1` → `BROKEN 25`（只剩搬迁前就有的 25 个）。若大于 25，看输出列表逐个修。

### T5 索引与约定

- 写范围：`docs/README.md`（新建）、根 `README.md`、`AGENTS.md`
- 步骤：
  1. 新建 `docs/README.md`：说明 v2/、v3/、ENVIRONMENT、copy-audit、assets、ideas 各是什么；说明代码注释里的 `docs/NN` 指 `docs/v2/NN`。
  2. 根 `README.md` 的文档链接：「文档」指 `docs/README.md`；开放 API、部署指南、决策记录指 `docs/v2/` 下对应文件（M64 后改指 v3 副本）；文档表增加 v3 方案入口 `docs/v3/README.md`。
  3. `AGENTS.md`：「规格与决策」改为 v3 口径（`docs/v3/README.md` 决策速查表是单一事实来源；`docs/v2/` 冻结只读；偏离记 `docs/v3/13 §8`；spec、计划、台账放 `docs/v3/specs|plans|ledgers`）；「分支与发布」加 `v3-m<N>` tag；开头一句改为「v2（M0–M62）已完成；v3（SuperAgent 改造）规划见 docs/v3/」。
- 验证：`rg -n "docs/superpowers|docs/13|docs/12" AGENTS.md README.md` 只剩指向 `docs/v2/` 的条目。

### T6 全量验证

- 步骤与期望：
  ```bash
  .venv/bin/python -m pytest -q > /tmp/m63-pytest.log 2>&1; echo $?; tail -n 15 /tmp/m63-pytest.log
  .venv/bin/python scripts/dump_openapi.py --check; echo $?
  cd web && pnpm install --frozen-lockfile > /tmp/m63-pnpm.log 2>&1 && pnpm typecheck > /tmp/m63-tc.log 2>&1 && pnpm test --run > /tmp/m63-vitest.log 2>&1 && pnpm build > /tmp/m63-build.log 2>&1; echo $?; cd ..
  git diff --check; echo $?
  ```
  pytest 与 dev 基线相比不新增失败；openapi `--check` 退出码 0（本里程碑不改接口）；前端四步退出码 0（`GuideMarkdown.tsx` 的新导入路径能解析）；`git diff --check` 退出码 0。

### T7 提交与合并

- 步骤：
  ```bash
  git add -A docs AGENTS.md README.md .gitignore tests scripts web/src/features/api-console/GuideMarkdown.tsx
  git status --porcelain | grep -v '^?? docs/ideas/'   # 确认没有误加的文件
  git commit -m "docs(m63): v3 SuperAgent 方案与 v2 文档迁入 docs/v2"
  git checkout dev && git merge --no-ff v3-plan -m "merge: v3 文档与 v2 目录迁移（M63，无偏离）"
  git tag v3-m63
  git branch -d v3-plan
  ```
  `docs/ideas/` 保持未跟踪，不要加进提交。

## 6. 测试清单

本里程碑不加新测试；T3 列出的四个测试文件与全量测试保持通过即可。

## 7. 验收

- 前置检查第 4 条的基线不变：v3 之外断链 25 个，v3 内 0 个。
- T6 全部通过。
- `git log --follow docs/v2/13-决策记录.md | head -n 3` 能看到搬迁前的提交。

## 8. 风险与回滚

- 漏改按路径读文档的代码，测试或前端构建失败：T3、T6 会暴露，按 §4.2 补。
- 链接重写误伤：脚本只改相对链接，跳过代码块；回滚用 `git checkout -- <文件>` 后手工修。
- 整体回滚：`git revert -m 1 <merge>`。

## 9. 文档同步

- 本里程碑本身就是文档改动；完成后在 `docs/v3/README.md` 顶部状态行写明「M63 已完成」。

## 10. 停下来问

- 全量测试出现与路径无关的新失败。
- 断链数大于 25 且不是本次搬迁造成的。

## 附录 A：`/tmp/maa_v3/relink.py`

```python
#!/usr/bin/env python3
"""One-off helper for the docs/v2 move: check or rewrite relative markdown links."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path.cwd()
EXCLUDE_DIRS = {
    ".git", ".venv", "node_modules", ".pnpm-store", "static", "resource", ".pytest_cache",
    ".idea", ".vscode", ".superpowers", ".archify", "debug", "__pycache__",
}
MOVED_FILES = ["docs/README.md", "docs/DEFECTS.md"]
MOVED_DIRS = ["docs/superpowers"]
V2 = "docs/v2"
INLINE_LINK = re.compile(r"(\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")
REF_LINK = re.compile(r"^(\s*\[[^\]]+\]:\s*)(\S+)(.*)$")
HTML_ATTR = re.compile(r"((?:src|href)=\")([^\"]+)(\")")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "/", "data:", "tel:", "javascript:")


def numbered_docs() -> list[str]:
    names: set[str] = set()
    for base in (ROOT / "docs", ROOT / V2):
        if base.is_dir():
            for p in base.glob("*.md"):
                if re.match(r"^\d\d-", p.name):
                    names.add(p.name)
    return sorted(names)


FILE_MAP = {f: f"{V2}/{Path(f).name}" for f in MOVED_FILES}
FILE_MAP.update({f"docs/{n}": f"{V2}/{n}" for n in numbered_docs()})
DIR_MAP = {d: f"{V2}/{Path(d).name}" for d in MOVED_DIRS}
INV_FILE_MAP = {v: k for k, v in FILE_MAP.items()}
INV_DIR_MAP = {v: k for k, v in DIR_MAP.items()}


def _map(rel: str, files: dict[str, str], dirs: dict[str, str]) -> str:
    if rel in files:
        return files[rel]
    for src, dst in dirs.items():
        if rel == src or rel.startswith(src + "/"):
            return dst + rel[len(src):]
    return rel


def iter_markdown() -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        out += [Path(dirpath) / f for f in filenames if f.endswith(".md")]
    return sorted(out)


def split_target(target: str) -> tuple[str, str]:
    idx = min([i for i in (target.find("#"), target.find("?")) if i >= 0], default=-1)
    return (target, "") if idx < 0 else (target[:idx], target[idx:])


def is_relative(target: str) -> bool:
    return bool(target) and not target.startswith(SKIP_PREFIXES) and "://" not in target


def norm(rel_dir: str, part: str) -> str:
    return os.path.normpath(os.path.join(rel_dir, unquote(part))).replace(os.sep, "/")


def process(lines: list[str], fn) -> list[str]:
    out, fence = [], False
    for line in lines:
        s = line.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            fence = not fence
            out.append(line)
            continue
        if not fence:
            line = INLINE_LINK.sub(lambda m: m.group(1) + fn(m.group(2)) + m.group(3), line)
            line = HTML_ATTR.sub(lambda m: m.group(1) + fn(m.group(2)) + m.group(3), line)
            m = REF_LINK.match(line)
            if m:
                line = m.group(1) + fn(m.group(2)) + m.group(3)
        out.append(line)
    return out


def check() -> int:
    broken = []
    for path in iter_markdown():
        rel = path.relative_to(ROOT).as_posix()
        def fn(t, rel=rel):
            if is_relative(t):
                part, _ = split_target(t)
                if part and not (ROOT / norm(os.path.dirname(rel), part)).exists():
                    broken.append(f"{rel}: {t}")
            return t
        process(path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True), fn)
    print("\n".join(broken))
    print(f"BROKEN {len(broken)}", file=sys.stderr)
    return 0


def rewrite() -> int:
    files = links = 0
    for path in iter_markdown():
        rel_new = path.relative_to(ROOT).as_posix()
        rel_old = _map(rel_new, INV_FILE_MAP, INV_DIR_MAP)
        new_dir, old_dir = os.path.dirname(rel_new), os.path.dirname(rel_old)
        original = path.read_text(encoding="utf-8").splitlines(keepends=True)
        count = [0]
        def fn(t):
            if not is_relative(t):
                return t
            part, suffix = split_target(t)
            if not part:
                return t
            old_target = norm(old_dir, part)
            new_target = _map(old_target, FILE_MAP, DIR_MAP)
            if rel_new == rel_old and new_target == old_target:
                return t
            new_rel = os.path.relpath(new_target, new_dir or ".").replace(os.sep, "/")
            if part.startswith("./") and not new_rel.startswith("../"):
                new_rel = "./" + new_rel
            if part.endswith("/") and not new_rel.endswith("/"):
                new_rel += "/"
            if "%" in part:
                new_rel = quote(new_rel, safe="/._-~")
            if new_rel + suffix != t:
                count[0] += 1
            return new_rel + suffix
        updated = process(original, fn)
        if updated != original:
            path.write_text("".join(updated), encoding="utf-8")
            files += 1
            links += count[0]
            print(f"rewrote {count[0]:3d} links in {rel_new}")
    print(f"FILES {files} LINKS {links}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(rewrite() if sys.argv[1:] == ["rewrite"] else check())
```

在仓库根目录执行（脚本以当前目录为仓库根）。M64 会把 `check` 的逻辑整理成 `scripts/check_doc_links.py` 入库，作为长期门禁。
