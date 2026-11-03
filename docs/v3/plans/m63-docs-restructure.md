# M63 文档与 v2 目录迁移 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v3 文档就位，v2 文档整体迁入 `docs/v2/`，按路径读文档的代码与前端改指新位置，全仓相对链接有效，既有测试与前端门禁不降级，合并回 `dev` 并打 tag `v3-m63`。

**Architecture:** 纯文档搬迁（`git mv` 保历史）加 6 处路径常量修改，再跑一次性相对链接重写脚本（`/tmp` 下，不入库）。不新增测试：回归由既有测试、生成物 `--check` 与链接检查承担。

**Tech Stack:** Python 3.13（`.venv/bin/python`）、pytest、git、pnpm 11 / Vite（`web/`）。

**Spec:** [m63-docs-restructure](../specs/m63-docs-restructure.md)

## 开工基线（2026-11-03，写计划前已固化）

- 分支 `docs-restructure`，基点是 `dev` 的 `46740f2`。`dev` 上先 `cherry-pick` 了原本在 `v3-plan` 上的两个 v2 修复（`e1dc275` 与 `46740f2` 本身），再把 `319ccb2` 的 v3 文档 `cherry-pick` 到本分支。
- spec 的 T1（写 v3 文档）已由 `319ccb2` 完成并随上一条落到本分支，本计划从 T2 开始执行。
- 计数：`docs/` 根 md 17 份；`docs/v3/*.md` 14 份；spec 36 份 = `docs/v3/specs/` 23 + `docs/v3/specs/todo/` 13；`docs/v2/` 不存在。
- 后端基线：`3 failed`（`tests/services/test_activity_index.py` 2 条、`tests/services/test_stage_layout.py` 1 条，都是读真实资源包的用例），其余全过。
- 生成物与前端：`scripts/dump_openapi.py --check` 退 0、`scripts/generate_api_guide.py --check` 退 0、`pnpm gen:api:check` 退 0、`pnpm typecheck` 退 0、`pnpm test --run` 74 文件 773 用例全过、`pnpm build` 退 0。
- 链接基线：`relink.py check` 为 `BROKEN 66` = `docs/v3` 40 + `docs/superpowers` 13 + `docs/copy-audit` 12 + `tests/fixtures` 1。搬迁后 `docs/v3` 应为 0，其余 26 条是既有断链。
- 环境：`rg` 不在 PATH，spec 里的 `rg` 命令一律改用 `grep -rn`；`static/` 已 gitignore，`pnpm build` 不会脏工作区。

## Global Constraints

- 一律 `.venv/bin/python`；`python` 不在 PATH；macOS 没有 `timeout`。
- 长输出（pytest、pnpm、diff）重定向到 `/tmp/m63-*.log`，只看尾部与退出码。
- v2 文档只改相对链接，不改正文；代码注释里 `docs/13 §8.x` 之类的文字引用不动，它们指 `docs/v2/` 下同名文件。
- 搬迁必须用 `git mv`，保住 `git log --follow`。
- 不 push；提交信息用 `docs(m63): <中文摘要>`。
- 文案遵守 [文案规范](../../copy-audit/规范-文案规范.md)：不用破折号当冒号、不用感叹号与 emoji。
- 不把 `static/`、`resource/`、`.venv/`、`web/node_modules/`、`config.yaml` 加进提交。

## Review Focus

1. **漏改按路径读文档的代码**：`docs/05`、`docs/14`、`docs/superpowers/specs` 三处被 6 个文件按路径读取，漏一个就是测试红或前端构建失败。由 Task 1 的四个后端测试与 `pnpm build` 钉住。
2. **链接重写误伤代码块与行内代码**：围栏代码块与行内代码里的 `](…)`、`](url)` 是教学示例，不是链接。由 Task 0 的 `BROKEN 63` 与 Task 2 的 `v3: 0` 钉住。
3. **中文路径与 URL 编码**：相对链接里的 `%xx` 与中文文件名混用，重写后必须仍能解析。由 Task 2 的检查总数与 Task 4 的抽查钉住。
4. **`git mv` 丢历史**：用「删掉再新建」会让 `git log --follow` 断掉。由 Task 1 的 `grep -c '^R'` 与 Task 6 的 `git log --follow` 钉住。
5. **断链基线漂移**：`docs/v2/superpowers`、`docs/copy-audit`、`tests/fixtures` 里本来就有 26 条断链，不能因为本次搬迁新增，也不能被顺手「修好」而改判。由 Task 2 的「`docs/v3` 内 0 条、总数不超过 26 且记录实际值」钉住。

---

### Task 0: 计划入库与链接脚本就绪

**Files:**
- Create: `docs/v3/plans/m63-docs-restructure.md`（本文件）
- Create: `/tmp/maa_v3/relink.py`（一次性脚本，不入库）

**Interfaces:**
- Consumes: spec 附录 A 的脚本
- Produces: `/tmp/maa_v3/relink.py` 的 `check` 与 `rewrite` 两个模式，供 Task 2 使用

- [ ] **Step 1: 从 spec 附录 A 抄出脚本**

Run:
```bash
mkdir -p /tmp/maa_v3
sed -n '178,322p' docs/v3/specs/m63-docs-restructure.md > /tmp/maa_v3/relink.py
head -n 2 /tmp/maa_v3/relink.py; tail -n 2 /tmp/maa_v3/relink.py
```
Expected: 首行 `#!/usr/bin/env python3`，第二行是 `"""One-off helper for the docs/v2 move...`，末行是 `sys.exit(rewrite() if sys.argv[1:] == ["rewrite"] else check())`。

- [ ] **Step 2: 让 check/rewrite 跳过行内代码**

在正则区追加 `INLINE_CODE`，并把 `process()` 换成下面两个函数（其余不动）。**`INLINE_CODE` 必须带捕获组**：不带捕获组时 `re.split` 会把匹配到的代码段整个丢掉，剩下的普通文字被当成「代码段」跳过，实测断链数会从 63 掉到 48，并且漏报 `docs/superpowers/specs/2026-09-28-m14-pwa-push.md` 这类真实断链。

```python
INLINE_CODE = re.compile(r"(`[^`\n]*`)")


def _apply_outside_inline_code(line: str, fn) -> str:
    """Apply link handling only to the parts of a line that are not inline code."""
    parts = INLINE_CODE.split(line)
    for i in range(0, len(parts), 2):
        seg = parts[i]
        seg = INLINE_LINK.sub(lambda m: m.group(1) + fn(m.group(2)) + m.group(3), seg)
        seg = HTML_ATTR.sub(lambda m: m.group(1) + fn(m.group(2)) + m.group(3), seg)
        match = REF_LINK.match(seg)
        if match:
            seg = match.group(1) + fn(match.group(2)) + match.group(3)
        parts[i] = seg
    return "".join(parts)


def process(lines: list[str], fn) -> list[str]:
    out, fence = [], False
    for line in lines:
        s = line.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            fence = not fence
            out.append(line)
            continue
        if not fence:
            line = _apply_outside_inline_code(line, fn)
        out.append(line)
    return out
```

- [ ] **Step 3: 验证脚本改造后的基线**

Run:
```bash
.venv/bin/python /tmp/maa_v3/relink.py check > /tmp/m63-links-patched.log 2>/tmp/m63-links-patched.err; echo $?
cat /tmp/m63-links-patched.err; echo "v3: $(grep -c '^docs/v3/' /tmp/m63-links-patched.log)"
```
Expected: 退出码 0，`BROKEN 63`，`v3: 37`。比 66/40 各少 3 条，正是 `specs/m63-docs-restructure.md`、`specs/m76-rag-index.md`、`specs/todo/m97-persona-voice.md` 里三处行内代码示例（`` `](…)` ``、`` `](url)` ``、`` `](地址)` ``）。

- [ ] **Step 4: 提交计划**

```bash
git add docs/v3/plans/m63-docs-restructure.md
git commit -m "docs(m63): M63 文档与 v2 目录迁移实施计划"
```

### Task 1: 搬迁 v2 文档并改指新路径（spec T2 + T3）

**Files:**
- Move: `docs/README.md`、`docs/DEFECTS.md`、`docs/01`–`docs/15` 共 17 个文件 → `docs/v2/`；`docs/superpowers/` → `docs/v2/superpowers/`
- Modify: `tests/domain/test_errors.py:39`、`tests/domain/test_route_inventory.py:36`、`tests/agent/test_knowledge_service.py:45`、`scripts/generate_api_guide.py:15-16`、`web/src/features/api-console/GuideMarkdown.tsx:2`、`.gitignore:139`

**Interfaces:**
- Consumes: 无
- Produces: `docs/v2/` 文档树，Task 2 的链接映射以它为目标

- [ ] **Step 1: 证明基线绿**

Run:
```bash
.venv/bin/python -m pytest -q tests/domain/test_errors.py tests/domain/test_route_inventory.py tests/agent/test_knowledge_service.py tests/test_api_guide_generator.py > /tmp/m63-t1-base.log 2>&1; echo $?
```
Expected: `0`。不为 0 就停下报告，不要往下一步走。

- [ ] **Step 2: 搬迁**

Run:
```bash
mkdir -p docs/v2
git mv docs/README.md docs/DEFECTS.md docs/v2/
git mv docs/0[1-9]-*.md docs/1[0-5]-*.md docs/v2/
git mv docs/superpowers docs/v2/superpowers
```
Expected: 三条命令都退 0。

- [ ] **Step 3: 核对搬迁结果**

Run:
```bash
ls docs/v2 | wc -l
git status --porcelain | grep -c '^R'
test -e docs/v2 && test ! -e docs/superpowers && echo ok
ls docs/*.md
```
Expected: `18`（15 份编号文档 + README + DEFECTS + superpowers）、计数大于 0、`ok`、`docs/` 根只列出 `docs/ENVIRONMENT.md`。

- [ ] **Step 4: 跑红，证明这 6 个文件是路径依赖的入口**

Run:
```bash
.venv/bin/python -m pytest -q tests/domain/test_errors.py tests/domain/test_route_inventory.py tests/agent/test_knowledge_service.py tests/test_api_guide_generator.py > /tmp/m63-t1-red.log 2>&1; echo $?
cd web && pnpm build > /tmp/m63-t1-build-red.log 2>&1; echo $?; cd ..
```
Expected: 两条都非 0。pytest 日志尾部有 `FileNotFoundError` 或 `No such file`；build 日志有 `Failed to resolve import` 且带 `docs/14-开放API接入指南.md`。

- [ ] **Step 5: 改 6 处路径**

1. `tests/domain/test_errors.py:39`
   `API_SPEC_PATH = REPO_ROOT / "docs" / "05-API规范与路由清单.md"`
   → `API_SPEC_PATH = REPO_ROOT / "docs" / "v2" / "05-API规范与路由清单.md"`
2. `tests/domain/test_route_inventory.py:36`：同上加 `"v2"`。
3. `tests/agent/test_knowledge_service.py:45`
   `SPEC_DIR = REPO_ROOT / "docs" / "superpowers" / "specs"`
   → `SPEC_DIR = REPO_ROOT / "docs" / "v2" / "superpowers" / "specs"`
4. `scripts/generate_api_guide.py:15-16`：两行都在 `"docs"` 与文件名之间插 `"v2"`：
   `GUIDE_PATH = REPO_ROOT / "docs" / "v2" / "14-开放API接入指南.md"`、
   `ERROR_SPEC_PATH = REPO_ROOT / "docs" / "v2" / "05-API规范与路由清单.md"`。
5. `web/src/features/api-console/GuideMarkdown.tsx:2`
   `import guideMarkdown from '../../../../docs/14-开放API接入指南.md?raw'`
   → `import guideMarkdown from '../../../../docs/v2/14-开放API接入指南.md?raw'`
6. `.gitignore:139` 注释改为：
   `# superpowers 的临时工作区（执行台账、任务简报、审核材料）；需要留存的台账复制到 docs/v2/superpowers/ledgers/（v2）或 docs/v3/ledgers/（v3）`

- [ ] **Step 6: 跑绿**

Run:
```bash
.venv/bin/python -m pytest -q tests/domain/test_errors.py tests/domain/test_route_inventory.py tests/agent/test_knowledge_service.py tests/test_api_guide_generator.py > /tmp/m63-t1-green.log 2>&1; echo $?
cd web && pnpm build > /tmp/m63-t1-build-green.log 2>&1; echo $?; cd ..
```
Expected: 两条都 `0`。

- [ ] **Step 7: 提交**

```bash
git add -A docs tests scripts web/src/features/api-console/GuideMarkdown.tsx .gitignore
git status --porcelain
git diff --check; echo $?
git commit -m "docs(m63): v2 文档迁入 docs/v2，代码与测试改指新路径"
```
Expected: `git status --porcelain` 只列出本次搬迁与修改的文件，没有 `docs/ideas` 之外的意外文件；`git diff --check` 退 0。

### Task 2: 重写全仓相对链接（spec T4）

**Files:**
- Modify: 全仓 `.md`（由脚本自动改；预期主要落在根 `README.md`、`docs/copy-audit/`、`docs/ideas/`、`tests/fixtures/core_probe_findings.md`；v2 文档内部的相对链接因为整体搬迁保持不变）
- 不改: `docs/v3/specs/` 与 `docs/v3/01–13`（写时已是搬迁后的口径，脚本应为零改动）

**Interfaces:**
- Consumes: `docs/v2/` 文档树（Task 1）
- Produces: 有效的相对链接树，Task 3 与 Task 5 的文档直接引用新路径

- [ ] **Step 1: 记录重写前的数字**

Run:
```bash
.venv/bin/python /tmp/maa_v3/relink.py check > /tmp/m63-links-before-rewrite.log 2>/tmp/m63-links-before-rewrite.err; echo $?
cat /tmp/m63-links-before-rewrite.err
```
Expected: 退出码 0；`BROKEN` 数字大于 63（跨 `docs/` 与 `docs/v2/` 边界的链接此刻断了）。把数字抄进台账草稿。

- [ ] **Step 2: 重写**

Run:
```bash
.venv/bin/python /tmp/maa_v3/relink.py rewrite > /tmp/m63-rewrite.log 2>/tmp/m63-rewrite.err; echo $?
cat /tmp/m63-rewrite.err
```
Expected: 退出码 0；末行 `FILES <n> LINKS <m>`，n 大于 0。

- [ ] **Step 3: 复检**

Run:
```bash
.venv/bin/python /tmp/maa_v3/relink.py check > /tmp/m63-links-final.log 2>/tmp/m63-links-final.err; echo $?
cat /tmp/m63-links-final.err; echo "v3: $(grep -c '^docs/v3/' /tmp/m63-links-final.log)"
```
Expected: `BROKEN` 不超过 26，`v3: 0`。不满足就按输出逐个修，不许改判基线。

- [ ] **Step 4: 抽查链接真的指对**

Run:
```bash
grep -n "docs/v2/14-开放API接入指南.md" README.md | head -3
grep -rn "docs/v2/" docs/copy-audit/*.md | head -3
grep -n "docs/v2/03-MaaCore内核层设计.md" tests/fixtures/core_probe_findings.md
```
Expected: 三条命令都有输出。

- [ ] **Step 5: 提交**

```bash
git add -A
git status --porcelain | head -20
git diff --check; echo $?
git commit -m "docs(m63): 全仓相对链接改指 docs/v2 与 docs/v3"
```
Expected: 状态里只有 `.md` 的修改，没有意外的非文档文件；`git diff --check` 退 0。

### Task 3: 文档索引与约定切到 v3（spec T5）

**Files:**
- Create: `docs/README.md`
- Modify: `README.md`、`AGENTS.md`

**Interfaces:**
- Consumes: Task 2 的链接树
- Produces: `docs/README.md` 入口与 AGENTS.md 的 v3 口径；Task 5 与后续里程碑按它取路径

- [ ] **Step 1: 新建 `docs/README.md`**

内容：

```markdown
# MAA-API 文档索引

文档分 v2 与 v3 两套：v2 冻结只读，v3 是现行方案。代码注释与测试里形如 `docs/13 §8.x`、`docs/05 §6` 的文字引用指 `docs/v2/` 下的同名文件。

| 目录 | 内容 |
|---|---|
| [v3/](v3/README.md) | v3（SuperAgent 改造）方案与决策、实施路线、可执行 spec、计划与台账 |
| [v2/](v2/README.md) | v2（M0–M62）冻结方案、决策、实施记录与 superpowers 产物 |
| [ENVIRONMENT.md](ENVIRONMENT.md) | 跨版本的环境陷阱与实测记录 |
| [copy-audit/](copy-audit/规范-文案规范.md) | 文案规范与审计基线 |
| [assets/](assets/) | 文档用图片 |
| [ideas/](ideas/) | 立项讨论稿 |
```

- [ ] **Step 2: 改根 `README.md`**

1. 顶部导航：`[文档](docs/v2/README.md)` 改回 `[文档](docs/README.md)`；同一行的开放 API、部署指南、决策记录保持 Task 2 已改好的 `docs/v2/...`。
2. 「文档」表：`| [文档索引](docs/v2/README.md) | ... |` 改回 `docs/README.md`；在它下面加一行
   `| [v3 方案](docs/v3/README.md) | SuperAgent 改造：总控与工单、运行时、知识检索、主动性与 IM |`
3. 「目录结构」句尾 `docs/` 的说明改为：`docs/ 是文档索引与 v2、v3 两套方案（v2 冻结在 docs/v2/，v3 在 docs/v3/）`。

- [ ] **Step 3: 改 `AGENTS.md`**

按下面几处替换：

开头一句：

```markdown
以 MaaAssistantArknights 为内核的 FastAPI 服务。v2 重构 M0–M62 已完成并合入 master；v3（SuperAgent 改造）方案见 docs/v3/README.md，实施路线见 docs/v3/12，决策与偏离见 docs/v3/13。
```

`## 规格与决策` 整节：

```markdown
- docs/v3/README.md 的决策速查表是 v3 的单一事实来源，docs/v3/01–13 是 v3 方案，docs/v3/13 是 v3 决策记录。
- docs/v3/13 §8 记录实施后的偏离与待决事项，与其他 v3 文档冲突时以它为准。
- 实现偏离 v3 文档时，在同一任务内更新对应文档，并在 docs/v3/13 §8 补一条。
- docs/v2/ 是 v2（M0–M62）的冻结文档，只读；代码注释与测试里形如 docs/13 §8.x、docs/05 §6 的文字引用指 docs/v2/ 下同名文件。
- v3 的 spec 放 docs/v3/specs/，计划放 docs/v3/plans/，台账放 docs/v3/ledgers/。
```

「分支与发布」第二、四条：

```markdown
- 日常工作从 dev 新建 `<简短英文名>` 分支；完成后用 `git merge --no-ff` 合并回 dev。v2 里程碑完成打 `v2-m<N>` tag，v3 里程碑完成打 `v3-m<N>` tag。
- 本地分支只保留 master 与 dev 两个；临时分支用完即删。历史 tag（`v2-m*`）已公开，不要改写已发布历史。
```

「对 superpowers 流程的项目约定」第二、三、五条：

```markdown
- 只影响实现细节的裁决可以自行决定并记入台账；改变对外行为、范围或与 docs/v3/13 冲突的裁决，停下来问我。
- 计划完成、删除 SDD 工作区之前，把 progress.md 复制到 docs/v3/ledgers/<计划文件名>.md 并提交，"Rulings I made" 一并写入。
- 上下文压缩或换新会话后，先读当前计划的台账、docs/v3/13 §8 和 `git log`，再继续。
```

「环境陷阱」末尾加一条：

```markdown
- 本机没有 ripgrep（`rg` 不在 PATH）；v3 spec 里的 `rg` 命令改用 `grep -rn`。
```

- [ ] **Step 4: 验证**

Run:
```bash
grep -n "docs/0[0-9]-\|docs/1[0-9]-\|docs/superpowers" AGENTS.md README.md docs/README.md | grep -v "docs/v2/"
.venv/bin/python /tmp/maa_v3/relink.py check > /tmp/m63-links-t3.log 2>/tmp/m63-links-t3.err; cat /tmp/m63-links-t3.err; echo "v3: $(grep -c '^docs/v3/' /tmp/m63-links-t3.log)"
```
Expected: 第一条命令无输出（引用都指向 `docs/v2/` 或 `docs/v3/`）；`BROKEN` 不超过 26，`v3: 0`。

- [ ] **Step 5: 提交**

```bash
git add docs/README.md README.md AGENTS.md
git commit -m "docs(m63): 文档索引与 AGENTS.md 切到 v3 口径"
```

### Task 4: 全量验收（spec T6 + 12 §4）

- [ ] **Step 1: 后端全量测试**

Run:
```bash
.venv/bin/python -m pytest -q > /tmp/m63-pytest.log 2>&1; echo $?; tail -n 15 /tmp/m63-pytest.log
```
Expected: 退出码 1；`failed` 恰好 3 条，且是 `tests/services/test_activity_index.py` 的 2 条与 `tests/services/test_stage_layout.py` 的 1 条（与开工基线同一组）。出现别的失败就是回归，停下报告。

- [ ] **Step 2: 生成物检查**

Run:
```bash
.venv/bin/python scripts/dump_openapi.py --check; echo $?
.venv/bin/python scripts/generate_api_guide.py --check; echo $?
```
Expected: 两个都退 0。

- [ ] **Step 3: 前端全量门禁**

Run:
```bash
cd web && pnpm install --frozen-lockfile > /tmp/m63-pnpm.log 2>&1 && pnpm gen:api:check > /tmp/m63-genapi.log 2>&1 && pnpm typecheck > /tmp/m63-tc.log 2>&1 && pnpm test --run > /tmp/m63-vitest.log 2>&1 && pnpm build > /tmp/m63-build.log 2>&1; echo $?; cd ..
tail -n 4 /tmp/m63-vitest.log
```
Expected: 退出码 0；vitest 为 74 个文件、773 条用例全过。

- [ ] **Step 4: 前端集成冒烟**

Run:
```bash
.venv/bin/python scripts/frontend_smoke.py > /tmp/m63-frontend-smoke.log 2>&1; echo $?; tail -n 3 /tmp/m63-frontend-smoke.log
```
Expected: 退出码 0，末行 `SMOKE OK`。

- [ ] **Step 5: 工作区与空白检查**

Run:
```bash
git diff --check; echo $?
git status --porcelain
```
Expected: 都退 0、状态空。本任务不改文件，不提交。

### Task 5: 台账、决策记录与状态行

**Files:**
- Create: `docs/v3/ledgers/m63-docs-restructure.md`
- Modify: `docs/v3/13-决策记录.md`（§8）、`docs/v3/README.md`（顶部状态行）

- [ ] **Step 1: 写台账**

按 [specs/README §4](../specs/README.md) 的模板写 `docs/v3/ledgers/m63-docs-restructure.md`：分支与基点的 commit、合并 commit（Task 6 完成后回填）、做了什么、验证证据表（Task 4 各命令与真实输出）、Rulings I made、偏离（指向 13 §8.1–8.6）、遗留。验证证据必须来自真实运行结果，不许凭记忆填。

- [ ] **Step 2: 在 `docs/v3/13-决策记录.md` §8 追加下面六条**

```markdown
### 8.1 分支改为从 dev 拉 docs-restructure，两个 v2 修复单独进 dev（M63，2026-11-03）

- 原定：spec 头部与 §5 T7 要求在 `v3-plan` 分支上做，最后整体 `git merge --no-ff v3-plan` 回 dev（m63 §0、§5 T7）。
- 实际：先把 `v3-plan` 上的两个 v2 修复（README 的 Tailscale 说明、手机端被动断流）`cherry-pick` 到 `dev`，再从 dev 拉 `docs-restructure`，`cherry-pick 319ccb2` 的 v3 文档后做 T2–T7；合并信息与 tag 不变。
- 理由：`v3-plan` 顶部混着与 M63 无关的 v2 修复，整体合并会把它们算进 M63；12 §3 要求从 dev 拉短分支。
- 影响：`dev` 多了两个 v2 修复提交；本地 `v3-plan` 分支在合并后删除，远端 `origin/v3-plan` 保留。

### 8.2 前置检查与 T1 计数的过时数字（M63，2026-11-03）

- 原定：§0 检查 2 期望 `docs/` 根 18 份文档；§5 T1 期望 `ls docs/v3/specs/m*.md | wc -l` 为 36（m63 §0、§5 T1）。
- 实际：`docs/` 根为 17 份（15 份编号文档 + README + DEFECTS）；spec 共 36 份但分两处（`docs/v3/specs/` 23 + `docs/v3/specs/todo/` 13）。
- 理由：§0 把 ENVIRONMENT.md 也算进了编号文档；T1 的计数命令写于 M86–M98 收进 `todo/` 之前。
- 影响：只影响验收命令的写法，交付物不变。

### 8.3 断链基线由 25 变为 26，检查跳过行内代码（M63，2026-11-03）

- 原定：§0 检查 4 与 §7 期望 `BROKEN 25`（m63 §0、§7）；附录 A 的脚本不区分行内代码。
- 实际：搬迁前 26 条既有断链（`docs/superpowers` 13 + `docs/copy-audit` 12 + `tests/fixtures` 1；copy-audit 比 spec 记录的 11 多 1 条，来自 v2 修复提交的文案更新）。附录 A 脚本会把 `specs/m63`、`specs/m76`、`specs/todo/m97` 里三处行内代码教学示例（`` `](…)` ``、`` `](url)` ``、`` `](地址)` ``）误判为断链，因此给脚本加了跳过行内代码的处理。
- 理由：断链门槛只应约束真实链接；把教学示例算进去会让「v3 内 0 条」永远不成立。
- 影响：M64 写 `scripts/check_doc_links.py` 时必须沿用「跳过行内代码」的口径。

### 8.4 本机没有 ripgrep，验证命令改用 grep（M63，2026-11-03）

- 原定：spec 的前置检查与验证命令用 `rg -n`（m63 §0、§4.2）。
- 实际：`rg` 不在 PATH；改用 `grep -rn`，`AGENTS.md` 的「环境陷阱」补了一条。
- 理由：不装新依赖也能复现验证。
- 影响：后续里程碑的 spec 若写 `rg`，执行时一律换 `grep -rn`；M64 更新 `docs/ENVIRONMENT.md` 时补同一条。

### 8.5 docs/ideas 已由先前会话提交，保留跟踪（M63，2026-11-03）

- 原定：§5 T7 要求 `docs/ideas/` 保持未跟踪、不要加进提交（m63 §5 T7）。
- 实际：`319ccb2` 已把 `docs/ideas/2026-10-30-maa-agent-app.md` 提交进仓库（449 行讨论稿，不含密钥与个人信息）；本里程碑保留现状，不移动、不删除。
- 理由：删历史需要改写已推送的提交，代价大于收益。
- 影响：`docs/ideas/` 保持已跟踪状态。

### 8.6 计划与台账的落盘位置与命名（M63，2026-11-03）

- 原定：AGENTS.md 沿用 superpowers 口径（`docs/superpowers/`），v3 文档要求 `docs/v3/specs|plans|ledgers`（AGENTS.md、docs/v3/README.md）。
- 实际：T5 已把 AGENTS.md 改成 v3 口径；本里程碑的计划写 `docs/v3/plans/m63-docs-restructure.md`，台账写 `docs/v3/ledgers/m63-docs-restructure.md`，与 spec 文件同名。
- 理由：v2 的 superpowers 产物随 T2 整体迁入 `docs/v2/superpowers/`，v3 用 docs/v3 三目录。
- 影响：v3 后续里程碑一律按此路径与命名。
```

- [ ] **Step 3: 改 `docs/v3/README.md` 顶部状态行**

```
> 状态：M63 已完成（2026-11-03）；按 [12-实施路线与里程碑](./12-实施路线与里程碑.md) 从 M64 起分阶段实施。
```

- [ ] **Step 4: 提交**

```bash
git add docs/v3/ledgers/m63-docs-restructure.md docs/v3/13-决策记录.md docs/v3/README.md
git commit -m "docs(m63): 台账、实施偏离与 v3 状态行"
```

### Task 6: 合并回 dev、打 tag、清理（spec T7）

- [ ] **Step 1: 复查分支**

Run:
```bash
git status --porcelain; git log --oneline dev..HEAD
```
Expected: 状态空；列出 Task 0–5 的 5 个提交。

- [ ] **Step 2: 合并**

Run:
```bash
git checkout dev
git merge --no-ff docs-restructure -m "merge: v3 文档与 v2 目录迁移（M63，见 docs/v3/13 §8.1–8.6）"
```
Expected: 合并成功，无冲突。

- [ ] **Step 3: 打 tag**

```bash
git tag v3-m63
git tag -l 'v3-m*'
```
Expected: 输出 `v3-m63`。

- [ ] **Step 4: 合并后复验（主 agent 亲自跑）**

Run:
```bash
.venv/bin/python -m pytest -q > /tmp/m63-pytest-dev.log 2>&1; echo $?; tail -n 6 /tmp/m63-pytest-dev.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
.venv/bin/python scripts/generate_api_guide.py --check; echo $?
git diff --check; echo $?
git log --follow --oneline docs/v2/13-决策记录.md | head -2
git status --porcelain
```
Expected: pytest 的 3 条失败与分支上一致；两个 `--check` 退 0；`git diff --check` 退 0；`git log --follow` 能看到搬迁前的提交；状态空。

- [ ] **Step 5: 清理分支**

Run:
```bash
git branch -d docs-restructure
git diff --stat v3-plan dev -- docs/v3/specs web/src/features/agent | head
git branch -D v3-plan
git branch
```
Expected: 第一条删除成功；`git diff --stat` 无输出（spec 与 v2 修复已完整落到 dev）；本地只剩 `dev` 与 `master`。远端 `origin/v3-plan` 不动，在汇报里说明。

- [ ] **Step 6: 汇报**

汇报内容：tag `v3-m63`、台账路径、Task 4/6 的验证证据、遗留问题（`origin/v3-plan` 远端分支仍在；`docs/v2/superpowers`、`docs/copy-audit`、`tests/fixtures` 的 26 条既有断链留给 M64；`scripts/probe_core_api.py:390` 的 markdown 链接指向旧 `docs/03`，链接脚本只处理 `.md`，留给 M64 决定是否修）。
