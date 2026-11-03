# M64 工程整备 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐锁文件的 scipy 与 numpy、给出 Q8 的包管理结论、三份活文档出 v3 副本并改读、`scripts/check_doc_links.py` 入库、Dockerfile 与环境/部署文档按新锁文件更新、列表类型设置项行为落档；合并回 `dev` 并打 tag `v3-m64`。

**Architecture:** 先实测四个 PyPI 源并重锁（T1/T2），再复制活文档并改读（T3/T4），然后按新锁文件更新 Dockerfile 与文档（T5）、断链检查入库（T6）、设置项实测（T7）、文档同步（T8）、全量验收与合并（T9）。

**Tech Stack:** Poetry 2.1.3（uv 0.7.6 作兜底）、Python 3.11 下限（`.venv` 实为 3.13.3）、pytest 8、pnpm 11 / Vite（`web/`）。

**Spec:** [m64-engineering-baseline](../specs/m64-engineering-baseline.md)

## 开工基线（2026-11-03，写计划前实测）

- `dev` = `f0c84d5`（M63 合并提交），tag `v3-m63`，工作区干净。
- `docs/ideas/2026-10-30-maa-agent-app.md` 已跟踪（M63 §8.5 记录）。spec §0.1 的「除未跟踪的 `docs/ideas/` 外没有改动」按「工作区干净」判定。
- `poetry.lock` 58 个包，没有 scipy / numpy；`poetry check --lock` 输出 `Error: pyproject.toml changed significantly since poetry.lock was last generated`。
- `.venv`：Python 3.13.3，scipy 1.18.1、numpy 2.5.3（M27 用 pip 手装）。`poetry` 2.1.3、`uv` 0.7.6 都在 PATH。
- 网络（今日 10:40 实测）：`pypi.org`、`mirrors.aliyun.com`、`pypi.tuna.tsinghua.edu.cn` 的 `/simple/scipy/` 都是 HTTP 200。ENVIRONMENT.md 记的「aliyun 镜像不可达」今日不复现。
- 三份 v2 活文档：`docs/v2/05` 1417 行（`## 4.` 146–398、`## 6.` 458–877）、`docs/v2/14` 478 行、`docs/v2/15` 292 行；`docs/v3/14-*`、`15-*`、`16-*` 都不存在。
- 后端基线：`3 failed, 3849 passed, 5 skipped, 2 xfailed`（`test_activity_index.py` 2 条 + `test_stage_layout.py` 1 条，真资源用例）；前端 74 文件 773 用例全绿。
- 断链：26 条，全部在 `docs/v2/`、`docs/copy-audit/`、`tests/fixtures/` 下。spec §4.2.6 记的是 25 条，差在 `docs/copy-audit/清单-4-脚本与文档.md` 的 1 条（来自 M63 之前那个 v2 修复提交，M63 13 §8.3 已记录）。因此 `BASELINE_LIMIT = 26`，偏差记 13 §8.9。
- 常驻服务 `local.maa-api` 处于加载状态；`/Applications/Docker.app/Contents/Resources/bin/docker info` 退出码 1（Engine 未运行）→ Docker 构建补充检查记「未验证」。
- 环境：没有 `rg`（一律 `grep -rn`）；没有 `timeout`（用 `perl -e 'alarm shift; exec @ARGV'`）。

## Global Constraints

- 一律 `.venv/bin/python`；macOS 没有 `timeout`。
- 长输出重定向到 `/tmp/maa_m64/`，只看尾部与退出码。
- 不升级任何已锁定的依赖版本；不新增运行时依赖（numpy 是 scipy 的传递依赖）。
- 不改 Python 版本下限与 Docker 基底镜像；路径 P 下不把 `pyproject.toml` 改成 PEP 621。
- `docs/v2/` 只允许插三行冻结说明，其余不动。
- 不修 `deployment.cors_origins` 的 500（属改对外行为），只记录。
- 不检查锚点与外部 URL。
- 不 push；提交信息 `<类型>(m64): <中文摘要>`，按 T0–T10 小步提交（记录在台账 Rulings）。
- 不改工具数（84 / 模型可见 83）、错误码（124 / 123 / 20）、HTTP 路由（121）、业务表（28）、OpenAPI tag（19）。

## Review Focus

1. **重锁带出版本漂移**：`lockdiff` 出现 `REMOVED` 或 `CHANGED` 非空就是漂移，按决策树回滚并停下来。由 Task 2 的差异输出钉住。
2. **`.venv` 同步降级 scipy 影响养成规划**：`poetry install --dry-run` 若动 numpy、scipy 之外的包要停。由 Task 2 的 dry-run grep 与重锁后的规划测试、`account_smoke.py` 钉住。
3. **活文档两节抽取破坏解析**：`§6` 必须是文件最后一节、只有 `## 4.` 与 `## 6.` 两个二级标题，否则路由清单读到文件末尾的假设失效。由 Task 3 的 `grep '^## '` 与 Task 4 的路由/错误码测试钉住。
4. **断链基线 26 而非 25**：多的一条已由 M63 记录；`BROKEN` 只计基线之外，基线只减不增。由 Task 6 的 `--all` 分布核对、反向验证与 `tests/test_doc_links.py` 钉住。
5. **前端 `?raw` 导入层数**：`../../../../docs/v3/15-…` 与原来 `docs/` 同级；层数错会让 `pnpm build` 失败。由 Task 4 的前端门禁与 Task 9 的五步钉住。

---

### Task 0: 分支、前置检查与一次性脚本就绪

**Files:**
- Create: `docs/v3/plans/m64-engineering-baseline.md`（本文件）
- Create: `/tmp/maa_m64/{probe.sh,wheel_url.py,lockdiff.py,pins.py,copy_live_docs.py,probe_string_list.py}`（不入库）

**Interfaces:**
- Consumes: spec 附录 A–D
- Produces: `/tmp/maa_m64/` 下的全部一次性脚本；分支 `engineering-baseline`

- [ ] **Step 1: 建分支**

Run:
```bash
git status --porcelain
git checkout dev && git checkout -b engineering-baseline
git status -sb
```
Expected: 工作区干净；首行 `## engineering-baseline`。

- [ ] **Step 2: 抄附录脚本**

Run:
```bash
mkdir -p /tmp/maa_m64
sed -n '683,691p' docs/v3/specs/m64-engineering-baseline.md > /tmp/maa_m64/wheel_url.py
sed -n '695,708p' docs/v3/specs/m64-engineering-baseline.md > /tmp/maa_m64/probe.sh
sed -n '714,742p' docs/v3/specs/m64-engineering-baseline.md > /tmp/maa_m64/lockdiff.py
sed -n '746,756p' docs/v3/specs/m64-engineering-baseline.md > /tmp/maa_m64/pins.py
sed -n '762,873p' docs/v3/specs/m64-engineering-baseline.md > /tmp/maa_m64/copy_live_docs.py
sed -n '903,943p' docs/v3/specs/m64-engineering-baseline.md > /tmp/maa_m64/probe_string_list.py
head -n 1 /tmp/maa_m64/probe.sh; head -n 1 /tmp/maa_m64/copy_live_docs.py; head -n 1 /tmp/maa_m64/lockdiff.py
```
Expected: `probe.sh` 首行 `#!/bin/sh`（前面可能有一行注释 `# /tmp/maa_m64/probe.sh…`，以实际抄出的内容为准，脚本可执行）；`copy_live_docs.py` 首行 `#!/usr/bin/env python3`；`lockdiff.py` 首行 `# /tmp/maa_m64/lockdiff.py` 注释。若行号漂移导致首行不对，按文件名在 spec 里 `grep -n '^## 附录'` 重新定位代码块（围栏 ` ```python ` 的下一行到 ` ``` ` 的上一行）。

- [ ] **Step 3: 跑 spec §0 前置检查（`rg` 换 `grep`）**

Run:
```bash
git status -sb && git merge-base --is-ancestor dev HEAD && echo ok
git tag -l 'v3-m63'
ls docs/v2/05-API规范与路由清单.md docs/v2/14-开放API接入指南.md docs/v2/15-部署指南.md | wc -l
ls docs/v3/14-*.md docs/v3/15-*.md docs/v3/16-*.md 2>/dev/null | wc -l
grep -n 'API_SPEC_PATH =\|GUIDE_PATH =\|ERROR_SPEC_PATH =' tests/domain/test_errors.py tests/domain/test_route_inventory.py scripts/generate_api_guide.py
grep -n "docs/v2/14-开放API接入指南.md" web/src/features/api-console/GuideMarkdown.tsx
grep -c -E '^name = "(scipy|numpy)"' poetry.lock; POETRY_CACHE_DIR=$(mktemp -d) poetry check --lock 2>&1 | grep '^Error'
uv --version; poetry --version
ls scripts/check_doc_links.py scripts/doc_links_baseline.txt 2>/dev/null | wc -l
```
Expected: 首行 `## engineering-baseline` 与末行 `ok`；`v3-m63`；`3`；`0`；4 行且都含 `"v2"`；1 行；第一行 `0`、第二行以 `Error: pyproject.toml changed significantly` 开头；`uv 0.7.6 …` 与 `Poetry (version 2.1.3)`；`0`。

- [ ] **Step 4: 全量测试基线**

Run:
```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/maa_m64/pytest-base.log 2>&1; echo $?; tail -n 2 /tmp/maa_m64/pytest-base.log
```
Expected: 退出码 1；`3 failed, 3849 passed, 5 skipped, 2 xfailed`，失败用例与开工基线同一组。抄进台账草稿。

- [ ] **Step 5: 提交计划**

```bash
git add docs/v3/plans/m64-engineering-baseline.md
git commit -m "docs(m64): M64 工程整备实施计划"
```

### Task 1: 四源两遍探测与包管理决策（spec T1）

**Files:** 只写 `/tmp/maa_m64/`，仓库文件不改。

- [ ] **Step 1: 第一遍探测**

Run:
```bash
sh /tmp/maa_m64/probe.sh | tee /tmp/maa_m64/probe-1.txt; echo "EXIT:$?"
wc -l /tmp/maa_m64/probe-1.txt
```
Expected: 4 行，每行形如 `aliyun index=[200 0.150412] file=[206 0.080100]`。

- [ ] **Step 2: 隔一分钟跑第二遍**

Run:
```bash
sleep 60; sh /tmp/maa_m64/probe.sh | tee /tmp/maa_m64/probe-2.txt; echo "EXIT:$?"
cat /tmp/maa_m64/probe-1.txt /tmp/maa_m64/probe-2.txt
```
Expected: 共 8 行，每行都有 `index=[…]` 与 `file=[…]`。

- [ ] **Step 3: 按决策树判定**

判定：源可用 ⇔ 两遍都是 `index` 状态码 `200` 且 `file` 是 `206` 或 `200`。可用源按 aliyun、tuna、ustc、pypi 排序成 S；`s = S[0]`。若 S 为空，停下来问。

把两遍原始输出、S、s 与准备走的路径（P 或 U）抄进台账草稿（`/tmp/maa_m64/ledger-draft.md` 或直接留在会话里）。

### Task 2: 重锁并同步 `.venv`（spec T2，路径 P）

**Files:**
- Modify: `poetry.lock`（`pyproject.toml` 只在 s 不是 aliyun 时改源）
- Modify: `.venv/`（不进版本库）

**Interfaces:**
- Consumes: Task 1 的 s
- Produces: 含 scipy、numpy 的锁文件与一致的 `.venv`；Task 5 的 Dockerfile 依赖它

- [ ] **Step 1: 红——锁文件与 pyproject 不一致**

Run:
```bash
git show HEAD:poetry.lock > /tmp/maa_m64/poetry.lock.before
POETRY_CACHE_DIR=$(mktemp -d) poetry check --lock > /tmp/maa_m64/poetry-check-before.log 2>&1; echo "EXIT:$?"; grep '^Error' /tmp/maa_m64/poetry-check-before.log
```
Expected: 退出码 1，`Error: pyproject.toml changed significantly …`。

- [ ] **Step 2: 换源（仅 s 不是 aliyun 时）**

把 `pyproject.toml` 末尾的 `[[tool.poetry.source]]` 改成 s 的名字与 simple 地址。

- [ ] **Step 3: 重锁**

Run:
```bash
export POETRY_CACHE_DIR=$(mktemp -d)
perl -e 'alarm shift; exec @ARGV' 900 poetry lock > /tmp/maa_m64/poetry-lock.log 2>&1; echo "lock EXIT:$?"
tail -n 3 /tmp/maa_m64/poetry-lock.log
poetry check --lock > /tmp/maa_m64/poetry-check.log 2>&1; echo "check EXIT:$?"
grep '^Error' /tmp/maa_m64/poetry-check.log
.venv/bin/python /tmp/maa_m64/lockdiff.py /tmp/maa_m64/poetry.lock.before poetry.lock
grep -c -E '^name = "(scipy|numpy)"' poetry.lock
```
Expected: `lock EXIT:0`，日志末尾有 `Writing lock file`；`check EXIT:0` 且无 `Error` 行；差异 `OLD 58 NEW 60`、`ADDED {'numpy': [...], 'scipy': [...]}`、`REMOVED {}`、`CHANGED {}`；最后一行 `2`。`REMOVED`/`CHANGED` 非空或要求提高 Python 下限时停下报告。

- [ ] **Step 4: 绿——锁文件检查通过**

Run:
```bash
POETRY_CACHE_DIR=$(mktemp -d) poetry check --lock; echo "EXIT:$?"
```
Expected: 退出码 0。

- [ ] **Step 5: `.venv` 同步 dry-run**

Run:
```bash
POETRY_CACHE_DIR=$(mktemp -d) poetry install --no-root --dry-run > /tmp/maa_m64/install-dry.log 2>&1; echo "EXIT:$?"
grep -E 'Installing|Updating|Downgrading|Removing' /tmp/maa_m64/install-dry.log
```
Expected: 只出现 numpy 与 scipy 两行（形如 `Downgrading numpy (2.5.3 -> 2.4.x)`、`Downgrading scipy (1.18.1 -> 1.17.x)`）。出现其他包就停下报告。

- [ ] **Step 6: 真正同步并验证**

Run:
```bash
POETRY_CACHE_DIR=$(mktemp -d) poetry install --no-root > /tmp/maa_m64/install.log 2>&1; echo "EXIT:$?"
.venv/bin/python -c "import scipy, numpy; print(scipy.__version__, numpy.__version__)"
launchctl print gui/$(id -u)/local.maa-api > /dev/null 2>&1 && echo service-loaded || echo service-absent
.venv/bin/python -m pytest -q tests/services/test_upgrade_planner.py tests/services/test_upgrade_plans.py > /tmp/maa_m64/t2.log 2>&1; echo "pytest EXIT:$?"; tail -n 3 /tmp/maa_m64/t2.log
.venv/bin/python scripts/account_smoke.py > /tmp/maa_m64/account-smoke.log 2>&1; echo "smoke EXIT:$?"; tail -n 1 /tmp/maa_m64/account-smoke.log
```
Expected: install 退 0；版本与锁文件一致；服务输出之一；规划测试退 0；冒烟退 0 且末行 `SMOKE OK`。`service-loaded` 时不要重启服务，写进台账遗留。

- [ ] **Step 7: 提交**

```bash
git add pyproject.toml poetry.lock
git status --short
git diff --check; echo "EXIT:$?"
git commit -m "chore(m64): 重锁补齐 scipy 与 numpy"
```

### Task 3: 复制三份活文档（spec T3）

**Files:**
- Create: `docs/v3/14-现行接口清单.md`、`docs/v3/15-开放API接入指南.md`、`docs/v3/16-部署指南.md`

**Interfaces:**
- Consumes: `docs/v2/05`、`docs/v2/14`、`docs/v2/15`（M63 后未改）
- Produces: Task 4 改读的三份 v3 副本；Task 5 往 `16` 补 §12

- [ ] **Step 1: 红——v3 副本不存在**

Run:
```bash
ls docs/v3/14-*.md docs/v3/15-*.md docs/v3/16-*.md 2>/dev/null | wc -l
```
Expected: `0`。

- [ ] **Step 2: 跑复制脚本**

Run:
```bash
.venv/bin/python /tmp/maa_m64/copy_live_docs.py; echo "EXIT:$?"
```
Expected: 退出码 0；三行 `wrote …`（链接数 22 / 9 / 11）与三行替换记录。

- [ ] **Step 3: 绿——行数、标题与链接**

Run:
```bash
wc -l docs/v3/14-现行接口清单.md docs/v3/15-开放API接入指南.md docs/v3/16-部署指南.md
grep -n '^## ' docs/v3/14-现行接口清单.md
grep -o -E '\]\(\./[0-9][0-9]-[^)]*\)' docs/v3/14-现行接口清单.md docs/v3/15-开放API接入指南.md docs/v3/16-部署指南.md
```
Expected: 678 / 478 / 292 行；只有 `## 4. 错误码表` 与 `## 6. 全量路由清单` 两行；链接 4 处（15 里 3 处 `./14-现行接口清单.md`，14 里 1 处 `./16-部署指南.md`）。

- [ ] **Step 4: 提交**

```bash
git add docs/v3/14-现行接口清单.md docs/v3/15-开放API接入指南.md docs/v3/16-部署指南.md
git commit -m "docs(m64): 活文档副本迁入 docs/v3"
```

### Task 4: 测试、脚本、前端与 README 改读 v3 副本（spec T4）

**Files:**
- Modify: `tests/domain/test_errors.py`、`tests/domain/test_route_inventory.py`、`scripts/generate_api_guide.py`、`web/src/features/api-console/GuideMarkdown.tsx`、`web/src/features/api-console/GuidePage.tsx`、`web/src/features/api-console/api-console.page.test.tsx`、`README.md`、`docs/README.md`、`docs/v3/README.md`
- Modify（只插冻结行）: `docs/v2/05-API规范与路由清单.md`、`docs/v2/14-开放API接入指南.md`、`docs/v2/15-部署指南.md`

**Interfaces:**
- Consumes: Task 3 的三份 v3 副本
- Produces: 现行清单读取面切到 v3；Task 6 的断链基线依赖这一步

- [ ] **Step 1: 改路径常量与前端导入**

1. `tests/domain/test_errors.py`：`API_SPEC_PATH` → `REPO_ROOT / "docs" / "v3" / "14-现行接口清单.md"`；上方加注释 `#: 现行清单在 docs/v3/14；docs/v2/05 已冻结。`
2. `tests/domain/test_route_inventory.py`：同上。
3. `scripts/generate_api_guide.py`：`GUIDE_PATH` → `… / "v3" / "15-开放API接入指南.md"`；`ERROR_SPEC_PATH` → `… / "v3" / "14-现行接口清单.md"`；模块 docstring、`--check` 的 help、`parse_error_descriptions` 与 `render_error_table` 的 docstring 里的 `docs/14`、`docs/05` 改成 `docs/v3/15`、`docs/v3/14 §4`。
4. `web/src/features/api-console/GuideMarkdown.tsx` 第 2 行 → `'../../../../docs/v3/15-开放API接入指南.md?raw'`。
5. `web/src/features/api-console/GuidePage.tsx` 第 4 行注释：`docs/14` → `docs/v3/15`。
6. `web/src/features/api-console/api-console.page.test.tsx` 约 197 行：正则 `/05-API规范与路由清单/` → `/14-现行接口清单/`。

- [ ] **Step 2: 三份 v2 原件插冻结行**

每个文件第 1 行前插入它自己那一行，后面空一行：
- `docs/v2/05-API规范与路由清单.md`：`> 已冻结，现行版本见 [v3/14-现行接口清单](../v3/14-现行接口清单.md)。`
- `docs/v2/14-开放API接入指南.md`：`> 已冻结，现行版本见 [v3/15-开放API接入指南](../v3/15-开放API接入指南.md)。`
- `docs/v2/15-部署指南.md`：`> 已冻结，现行版本见 [v3/16-部署指南](../v3/16-部署指南.md)。`

- [ ] **Step 3: 改 README 与两个索引**

1. 根 `README.md`：所有 `docs/v2/14-开放API接入指南.md` → `docs/v3/15-开放API接入指南.md`；`docs/v2/15-部署指南.md` → `docs/v3/16-部署指南.md`；文档表这两行显示文字改成「15-开放 API 接入指南」「16-部署指南」。
2. `docs/README.md` v3 一节加一行：现行接口清单、开放 API 接入指南、部署指南在 `docs/v3/14`–`16`，M64 起随实现更新。
3. `docs/v3/README.md`「文档索引」表在 `13-决策记录` 之后加 14、15、16 三行（内容、谁要读按该文件第 31 行那段话）。

- [ ] **Step 4: 绿——测试与生成器**

Run:
```bash
.venv/bin/python -m pytest -o addopts= -q tests/domain/test_errors.py tests/domain/test_route_inventory.py tests/test_api_guide_generator.py > /tmp/maa_m64/t4.log 2>&1; echo "EXIT:$?"; tail -n 3 /tmp/maa_m64/t4.log
.venv/bin/python scripts/generate_api_guide.py --check; echo "guide EXIT:$?"
grep -n 'docs/v2/1[45]-' README.md; echo "grep1 EXIT:$?"
grep -rn '"v2" / "05-API\|"v2" / "14-开放\|docs/v2/14-' tests scripts web/src --exclude-dir=node_modules; echo "grep2 EXIT:$?"
git diff --stat -- docs/v2
```
Expected: pytest 退 0；生成器输出 `API guide generated sections are current: docs/v3/15-开放API接入指南.md`；两条 grep 都无输出、`EXIT:1`；`docs/v2` 的 diff 只有三个文件、每个 2 行新增。

- [ ] **Step 5: 提交**

```bash
git add -A tests scripts web/src/features/api-console README.md docs/README.md docs/v3/README.md docs/v2
git status --short
git commit -m "docs(m64): 测试、脚本、前端与 README 改读 v3 活文档"
```

### Task 5: Dockerfile、环境文档与 extra 约定（spec T5）

**Files:**
- Modify: `Dockerfile`、`docs/ENVIRONMENT.md`、`docs/v3/16-部署指南.md`

- [ ] **Step 1: 改 Dockerfile**

运行阶段 22–24 行改成（s 不是 aliyun 时 `-i` 换 s 的 simple 地址）：
```dockerfile
COPY pyproject.toml poetry.lock ./
RUN pip install --no-cache-dir "poetry>=2.1,<3" -i https://mirrors.aliyun.com/pypi/simple/ \
    && poetry install --no-root --only main --no-interaction
```
`FROM`、`COPY alembic.ini`、`COPY config.template.yaml`、`COPY --from=web`、`CMD` 与注释都不动。

- [ ] **Step 2: 改 `docs/ENVIRONMENT.md`**

1. 「基础工具链」末尾追加 M64 实测条目：两遍探测结果、选的路径与源、重锁耗时、新增的两个包与版本、「其余 58 个包零漂移」、`.venv` 同步前后 scipy/numpy 版本、Python 3.11 与 ≥ 3.12 能装的 scipy 版本不同这一事实。
2. M27 那条（约第 18 行）末尾加「（M64 复测不成立，见本节末条）」。

- [ ] **Step 3: 改 `docs/v3/16-部署指南.md`**

1. §8「本机的安装偏离（重要）」整段换成锁文件现状（含 scipy、numpy 与版本；按锁文件安装的环境都带养成规划；结论见 13 §8）。
2. §8「镜像体积」一段里的安装命令按实际路径改。
3. 文末追加「## 12. 可选依赖（extra）」一节，正文照抄 spec §4.2.4 的 markdown 块，并保留预留名字表。

- [ ] **Step 4: 验证**

Run:
```bash
.venv/bin/python -m pytest -o addopts= -q tests/api/test_frontend.py > /tmp/maa_m64/t5.log 2>&1; echo "EXIT:$?"; tail -n 3 /tmp/maa_m64/t5.log
grep -n 'poetry.lock' Dockerfile
grep -n '^## 12\. 可选依赖' docs/v3/16-部署指南.md
/Applications/Docker.app/Contents/Resources/bin/docker info > /dev/null 2>&1 && echo docker-ok || echo docker-unavailable
```
Expected: pytest 退 0；Dockerfile 的 `COPY` 行含 `poetry.lock`；`16` 有第 12 节；Docker Engine 未运行 → `docker-unavailable`，台账写「Docker 构建未验证」。

- [ ] **Step 5: 提交**

```bash
git add Dockerfile docs/ENVIRONMENT.md "docs/v3/16-部署指南.md"
git commit -m "chore(m64): Dockerfile 与环境/部署文档按新锁文件更新"
```

### Task 6: 断链检查入库（spec T6）

**Files:**
- Create: `scripts/check_doc_links.py`、`scripts/doc_links_baseline.txt`、`tests/test_doc_links.py`

**Interfaces:**
- Consumes: M63 附录 A 的 `check` 逻辑（跳过行内代码）；Task 3/4 后的链接树
- Produces: `check_doc_links.py` 的 `--all` / `--write-baseline` / 默认模式；`BASELINE_LIMIT`

- [ ] **Step 1: 写 `scripts/check_doc_links.py`**

按 spec §4.2.6 的常量（照抄）、docstring（照抄）与函数签名实现；`CODE_SPAN` 去掉行内代码后再收集链接；`main` 的三种模式与输出格式按 spec。文件以 `if __name__ == "__main__": sys.exit(main())` 结尾。

- [ ] **Step 2: 核对全部断链**

Run:
```bash
.venv/bin/python scripts/check_doc_links.py --all > /tmp/maa_m64/links-all.txt; echo "EXIT:$?"
tail -n 1 /tmp/maa_m64/links-all.txt
cut -d: -f1 /tmp/maa_m64/links-all.txt | grep -v '^BROKEN' | sed 's|/[^/]*$||' | sort | uniq -c
```
Expected: `BROKEN 26`；条目全部落在 `docs/v2/`、`docs/copy-audit/`、`tests/fixtures/` 下（`docs/copy-audit` 12：ai-tells-docs 5、清单-4 1、诊断-4 6；`docs/v2/superpowers` 13：m34 plans 3、m14 specs 4、m17 specs 6；`tests/fixtures` 1）。出现 v3 内或其他位置的断链先修；多于 26 停下报告。

- [ ] **Step 3: 写基线**

Run:
```bash
.venv/bin/python scripts/check_doc_links.py --write-baseline; echo "EXIT:$?"
head -n 5 scripts/doc_links_baseline.txt
.venv/bin/python scripts/check_doc_links.py; echo "EXIT:$?"
```
Expected: `BASELINE 26`；基线条目只含三个允许目录；默认模式末行 `BROKEN 0`、退出码 0。

- [ ] **Step 4: 写 `tests/test_doc_links.py`**

按 spec §6 的 6 个用例写：`test_repository_has_no_new_broken_doc_links`、`test_baseline_has_no_stale_entries`、`test_baseline_stays_in_frozen_areas_and_never_grows`（`BASELINE_LIMIT = 26`）、`test_extract_links_skips_code_and_external_targets`、`test_compare_counts_duplicate_occurrences`、`test_main_prints_broken_count_as_last_line`。导入写 `import scripts.check_doc_links as doc_links`。

Run:
```bash
.venv/bin/python -m pytest -o addopts= -q tests/test_doc_links.py > /tmp/maa_m64/t6.log 2>&1; echo "EXIT:$?"; tail -n 3 /tmp/maa_m64/t6.log
```
Expected: 退 0，6 passed。

- [ ] **Step 5: 反向验证**

Run:
```bash
printf '\n[坏链接](./不存在.md)\n' >> docs/v3/16-部署指南.md
.venv/bin/python scripts/check_doc_links.py; echo "EXIT:$?"
git checkout -- docs/v3/16-部署指南.md
.venv/bin/python scripts/check_doc_links.py; echo "EXIT:$?"
```
Expected: 第一次打印该条与 `BROKEN 1`、退 1；还原后 `BROKEN 0`、退 0。确认 `git diff docs/v3/16-部署指南.md` 为空。

- [ ] **Step 6: 提交**

```bash
git add scripts/check_doc_links.py scripts/doc_links_baseline.txt tests/test_doc_links.py
git commit -m "feat(m64): 文档断链检查入库"
```

### Task 7: 核实列表类型设置项（spec T7）

**Files:** 只写 `/tmp/maa_m64/probe_string_list.py` 与台账草稿，仓库代码不改。

- [ ] **Step 1: 直接调 `_validate`**

Run:
```bash
.venv/bin/python - <<'EOF'
from maa_api.services.setting_service import SettingService
service = SettingService.__new__(SettingService)
for key, value in [("deployment.cors_origins", ["https://a.example"]), ("adb.common_ports", [5555, 16384]), ("adb.common_ports", [5555, 5555])]:
    try:
        print(key, value, "->", repr(service._validate(key, value)))
    except Exception as exc:
        print(key, value, "-> raised", type(exc).__name__, getattr(exc, "code", ""), str(exc))
EOF
```
Expected: `cors_origins` 抛 `KeyError 'string[]'`；`common_ports` 正常返回；重复端口抛 `AppError SETTING_VALUE_INVALID`。

- [ ] **Step 2: 走 HTTP 探测**

Run:
```bash
.venv/bin/python /tmp/maa_m64/probe_string_list.py 2>&1 | tail -n 4
```
Expected: 末四行与 spec §4.2.7 一致：`KeyError: 'string[]'`、`cors_origins … -> 500 INTERNAL_ERROR`、`重复端口 -> 422 SETTING_VALUE_INVALID`、`正常端口 -> 200 OK`。贴在台账草稿，Task 8 写进 13 §8 与 04 §6。

### Task 8: 文档同步（spec T8）

**Files:**
- Modify: `docs/v3/13-决策记录.md`（§8 追加三条、§9 Q8 关闭）、`docs/v3/04-接口与事件协议.md`（§6 一句）、`AGENTS.md`（命令一节加一行）

- [ ] **Step 1: 13 §8 追加 8.7、8.8、8.9**

按 spec §9 写三条（编号接在 8.6 之后）：
- 8.7 包管理结论（原定 Q8 待定/镜像不可达；实际两遍探测、路径、源、锁文件差异、`.venv` 同步、extra 约定；Q8 关闭）。
- 8.8 列表类型设置项实测（`string[]` → `KeyError` → 500 `INTERNAL_ERROR`，`deployment.cors_origins` 受影响；`integer[]` 只适合端口；缺陷未修，写进遗留）。
- 8.9 活文档迁移与断链门禁细则（相对链接改写、三处改指 v3/14、一处改指 v3/16、前端正则、三份 v2 原件冻结行、`check_doc_links.py` 跳过行内代码、基线 26 条且只减不增——注意 spec 写 25，实测 26，差的一条来自 M63 之前那个 v2 修复提交，13 §8.3 已记）。

- [ ] **Step 2: 13 §9 Q8 那一行改成「已定，见 §8.7」**

- [ ] **Step 3: 改 04 §6 那一句**

把「`setting_service._validate` 的类型表里没有 `string[]`，PUT 会报错（推断，见 M64 核实）」改成：「`setting_service._validate` 的类型表只有 string、integer、number、boolean 与端口专用的 integer[]；`string[]` 查表时抛 `KeyError`，PUT 返回 500 `INTERNAL_ERROR`（M64 实测，13 §8.8；既有的 `deployment.cors_origins` 也受影响）」。

- [ ] **Step 4: AGENTS.md 命令一节加一行**

在 `scripts/*_smoke.py` 那行之后加：
`- 文档断链检查：\`.venv/bin/python scripts/check_doc_links.py\`，末行 \`BROKEN 0\` 表示没有基线之外的断链；\`--all\` 列出全部断链（含 \`scripts/doc_links_baseline.txt\` 里的已知项）。`

- [ ] **Step 5: 验证并提交**

Run:
```bash
grep -c 'check_doc_links' AGENTS.md
grep -n '推断，见 M64' docs/v3/04-接口与事件协议.md; echo "EXIT:$?"
grep -n '^### 8\.' docs/v3/13-决策记录.md
```
Expected: `1`；第二条无输出、`EXIT:1`；第三条能看到 8.1–8.9。

```bash
git add AGENTS.md docs/v3/13-决策记录.md docs/v3/04-接口与事件协议.md
git commit -m "docs(m64): 实施偏离、设置项实测与文档同步"
```

### Task 9: 全量验收与台账（spec T9 前半）

**Files:**
- Create: `docs/v3/ledgers/m64-engineering-baseline.md`

- [ ] **Step 1: 跑 spec §7 全部验收命令**

```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/maa_m64/pytest.log 2>&1; echo "pytest EXIT:$?"; tail -n 2 /tmp/maa_m64/pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo "openapi EXIT:$?"
.venv/bin/python scripts/generate_api_guide.py --check; echo "guide EXIT:$?"
cd web && pnpm install --frozen-lockfile > /tmp/maa_m64/pnpm.log 2>&1 && pnpm gen:api:check > /tmp/maa_m64/genapi.log 2>&1 && pnpm typecheck > /tmp/maa_m64/tc.log 2>&1 && pnpm test --run > /tmp/maa_m64/vitest.log 2>&1 && pnpm build > /tmp/maa_m64/build.log 2>&1; echo "frontend EXIT:$?"; tail -n 4 /tmp/maa_m64/vitest.log; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/maa_m64/fe-smoke.log 2>&1; echo "fe-smoke EXIT:$?"; tail -n 1 /tmp/maa_m64/fe-smoke.log
.venv/bin/python scripts/account_smoke.py > /tmp/maa_m64/account-smoke.log 2>&1; echo "account-smoke EXIT:$?"; tail -n 1 /tmp/maa_m64/account-smoke.log
.venv/bin/python scripts/check_doc_links.py | tail -n 1
POETRY_CACHE_DIR=$(mktemp -d) poetry check --lock; echo "lock EXIT:$?"
git diff --check; echo "diff-check EXIT:$?"
grep -c -E '^name = "(scipy|numpy)"' poetry.lock
```
Expected: pytest 3 failed（与 0.10 基线同一组）+ 3849 passed；openapi / guide 退 0；frontend 五步退 0、773 用例全过；两个冒烟末行 `SMOKE OK`；断链 `BROKEN 0`；锁检查退 0；`git diff --check` 退 0；`2`。

- [ ] **Step 2: 写台账**

`docs/v3/ledgers/m64-engineering-baseline.md`，按 [specs/README §4](../specs/README.md) 模板，必须含：两遍探测原始输出、选的路径与理由、锁文件差异输出、`.venv` 前后版本、常驻服务状态、断链基线 26 条分布、附录 D 输出、Docker 补充检查结果（未验证）、Rulings I made、偏离（指向 13 §8.7–8.9）、遗留（`deployment.cors_origins` 500 未修、常驻服务仍加载旧 scipy、Docker 构建未验证）。

- [ ] **Step 3: 提交**

```bash
git add docs/v3/ledgers/m64-engineering-baseline.md docs/v3/README.md
git status --short
git commit -m "docs(m64): M64 台账与 v3 索引状态"
```

### Task 10: 合并回 dev、打 tag、清理（spec T9 后半）

- [ ] **Step 1: 复查**

Run:
```bash
git status --porcelain; git log --oneline dev..HEAD
```
Expected: 状态空；列出 T0–T9 的提交。

- [ ] **Step 2: 合并与 tag**

```bash
git checkout dev
git merge --no-ff engineering-baseline -m "merge: 工程整备（M64，见 docs/v3/13 §8.7–8.9）"
git tag v3-m64 && git tag -l 'v3-m*'
```
Expected: 合并成功；`v3-m63`、`v3-m64`。

- [ ] **Step 3: 合并后复验（主 agent 亲自跑）**

```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/maa_m64/pytest-dev.log 2>&1; echo "EXIT:$?"; tail -n 2 /tmp/maa_m64/pytest-dev.log
.venv/bin/python scripts/dump_openapi.py --check; echo "EXIT:$?"
.venv/bin/python scripts/generate_api_guide.py --check; echo "EXIT:$?"
.venv/bin/python scripts/check_doc_links.py | tail -n 1
POETRY_CACHE_DIR=$(mktemp -d) poetry check --lock; echo "EXIT:$?"
git diff --check; echo "EXIT:$?"
git status --porcelain
```
Expected: 与分支上一致；状态空（构建产物已 gitignore）。

- [ ] **Step 4: 清理分支**

```bash
git branch -d engineering-baseline
git branch
```
Expected: 只剩 `dev` 与 `master`。

- [ ] **Step 5: 汇报**

汇报：tag `v3-m64`、合并提交、Q8 结论与源、锁文件差异、`.venv` 版本、断链基线、遗留（`cors_origins` 500、常驻服务 reload、Docker 构建未验证）、下一步 M65。
