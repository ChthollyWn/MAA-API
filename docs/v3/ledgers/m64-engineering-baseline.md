# M64 工程整备：包管理、锁文件、活文档迁移、断链检查（台账）

- 分支：`engineering-baseline`；基点：`dev` = `f0c84d5`；合并：`dev` 上的 `merge: 工程整备（M64，见 docs/v3/13 §8.7–8.9）`；tag：`v3-m64`
- spec：[m64-engineering-baseline](../specs/m64-engineering-baseline.md)
- 计划：[m64-engineering-baseline](../plans/m64-engineering-baseline.md)
- 决策记录：[docs/v3/13 §8.7–8.9](../13-决策记录.md)
- 来源：用户 2026-11-03 确认「原样开工」，按 Superpowers 流程继续 M64（原生执行，主 agent 逐任务实现并亲自验证）。

## 做了什么

1. **包管理实测与决策（T1）**：四源两遍探测，全部可用；S = [aliyun, tuna, ustc, pypi]，s = aliyun，走路径 P（继续 Poetry），源不变。
2. **重锁并同步 `.venv`（T2）**：`poetry lock` 只新增 numpy 2.4.6 与 scipy 1.17.1；`.venv` 同步到同两个版本。
3. **活文档迁入 docs/v3（T3）**：附录 C 脚本建 `docs/v3/14-现行接口清单.md`（v2/05 的 §4+§6）、`15-开放API接入指南.md`、`16-部署指南.md`，链接改写 22 / 9 / 11 条，三处显式替换。
4. **改读 v3 副本（T4）**：两个 domain 测试、指南生成器、前端 `GuideMarkdown.tsx`/`GuidePage.tsx`/页面测试、根 README、两个索引；三份 v2 原件加冻结行。
5. **Dockerfile 与环境/部署文档（T5）**：Dockerfile 固定 `poetry>=2.1,<3`；ENVIRONMENT 记 M64 实测并标注 M27 那条不成立；v3/16 §8 换锁文件现状、新增 §12 可选依赖约定。
6. **断链检查入库（T6）**：`scripts/check_doc_links.py`（跳过行内代码，`BROKEN` 计基线之外）、`scripts/doc_links_baseline.txt`（26 条）、`tests/test_doc_links.py`（6 用例，`BASELINE_LIMIT = 26`）。
7. **列表类型设置项实测（T7）**：`string[]` → `KeyError` → 500；`integer[]` 端口专用；与 spec §4.2.7 一致。
8. **文档同步（T8）**：13 §8.7–8.9、13 §9 Q8 关闭、04 §6 改写、AGENTS.md 命令一节加断链检查。

## 验证证据

### 四源两遍探测（T1）

```text
第一遍：
aliyun index=[200 0.383645] file=[206 0.197497]
tuna index=[200 0.783058] file=[206 0.488959]
ustc index=[200 1.188750] file=[206 0.368688]
pypi index=[200 1.266371] file=[206 0.863425]
第二遍：
aliyun index=[200 0.296820] file=[206 0.260396]
tuna index=[200 1.700970] file=[206 0.972292]
ustc index=[200 1.215319] file=[206 0.416137]
pypi index=[200 1.715994] file=[206 1.227549]
```

结论：四源两遍都满足 `index 200` + `file 206`，全部可用；选 aliyun，走路径 P。

### 锁文件与 `.venv`（T2）

| 命令 | 结果 |
|---|---|
| `poetry check --lock`（重锁前） | 退出码 1，`Error: pyproject.toml changed significantly …` |
| `perl -e 'alarm shift; exec @ARGV' 900 poetry lock` | 退出码 0，日志末尾 `Writing lock file` |
| `poetry check --lock`（重锁后） | 退出码 0，无 `Error` 行（弃用警告保留） |
| `lockdiff.py /tmp/maa_m64/poetry.lock.before poetry.lock` | `OLD 58 NEW 60`；`ADDED {'numpy': ['2.4.6'], 'scipy': ['1.17.1']}`；`REMOVED {}`；`CHANGED {}` |
| `grep -c -E '^name = "(scipy\|numpy)"' poetry.lock` | `2` |
| 重跑一次计时 `poetry lock` | 退出码 0，`real 49.86 s`，锁文件无变化（`git diff --stat poetry.lock` 空） |
| `poetry install --no-root --dry-run` | 实际改动只有 `Downgrading numpy (2.5.3 -> 2.4.6)` 与 `Downgrading scipy (1.18.1 -> 1.17.1)`，其余 58 个包都是 `Skipped … Already installed` |
| `poetry install --no-root` | 退出码 0，只有上面两个 Downgrading |
| `.venv/bin/python -c "import scipy, numpy; …"` | `1.17.1 2.4.6`（与锁文件一致） |
| `pytest tests/services/test_upgrade_planner.py tests/services/test_upgrade_plans.py` | 退出码 0，`49 passed` |
| `scripts/account_smoke.py` | 退出码 0，末行 `SMOKE OK` |
| `launchctl print gui/$(id -u)/local.maa-api` | `service-loaded`（未重启，见遗留） |

### 活文档（T3/T4）

| 命令 | 结果 |
|---|---|
| 复制脚本 | 退出码 0；`wrote docs/v3/14-现行接口清单.md (22 links rewritten)`、`wrote docs/v3/15-开放API接入指南.md (9 links rewritten)`、`wrote docs/v3/16-部署指南.md (11 links rewritten)`，三行 `replaced …` |
| `wc -l` 三份 | `678 / 478 / 292` |
| `grep -n '^## ' docs/v3/14-现行接口清单.md` | 只有 `## 4. 错误码表`（7 行）与 `## 6. 全量路由清单`（260 行），§6 是最后一节 |
| 显式链接检查 | 4 处：15 里 3 处 `./14-现行接口清单.md`，14 里 1 处 `./16-部署指南.md` |
| 三个受影响测试 | 退出码 0，`65 passed` |
| `scripts/generate_api_guide.py --check` | `API guide generated sections are current: docs/v3/15-开放API接入指南.md` |
| `grep docs/v2/1[45]- README.md` / 全仓 `"v2" / "05-API` 等 | 都无输出、`exit 1` |
| `git diff --stat -- docs/v2` | 3 个文件、每个 2 行新增（冻结说明） |

### 断链门禁（T6）

```text
--all → BROKEN 26
docs/copy-audit/ai-tells-docs.md 5
docs/copy-audit/清单-4-脚本与文档.md 1
docs/copy-audit/诊断-4-文档与注释.md 6
docs/v2/superpowers/plans/2026-10-10-m34-account-list-contract.md 3
docs/v2/superpowers/specs/2026-09-28-m14-pwa-push.md 4
docs/v2/superpowers/specs/2026-09-28-m17-task-param-defaults.md 6
tests/fixtures/core_probe_findings.md 1
```

全部落在 `docs/v2/`、`docs/copy-audit/`、`tests/fixtures/` 三处，v3 内 0 条。spec 记 25 条，多出的 1 条是 `清单-4-脚本与文档.md` 的，来自 M63 之前那个 v2 修复提交（13 §8.9）。

| 命令 | 结果 |
|---|---|
| `check_doc_links.py --write-baseline` | `BASELINE 26` |
| `check_doc_links.py` | `BROKEN 0`，退出码 0 |
| 反向验证（临时加 `./不存在.md`） | 打印该条与 `BROKEN 1`，退出码 1；还原后 `BROKEN 0` |
| `pytest tests/test_doc_links.py` | 退出码 0，`6 passed` |

### 列表类型设置项（T7）

```text
deployment.cors_origins ['https://a.example'] -> raised KeyError  'string[]'
adb.common_ports [5555, 16384] -> [5555, 16384]
adb.common_ports [5555, 5555] -> raised AppError SETTING_VALUE_INVALID SETTING_VALUE_INVALID: 端口列表不能重复
---
KeyError: 'string[]'
deployment.cors_origins ['https://a.example'] -> 500 INTERNAL_ERROR
adb.common_ports [5555, 5555] -> 422 SETTING_VALUE_INVALID
adb.common_ports [5555, 16384] -> 200 OK
```

与 spec §4.2.7 完全一致。

### 全量验收（T9）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -o addopts= -q` | `3 failed, 3855 passed, 5 skipped, 2 xfailed in 194.21s`；3 条失败与开工基线同一组（`test_activity_index.py` 2 条 + `test_stage_layout.py` 1 条），新增 6 条用例来自 `tests/test_doc_links.py` |
| `scripts/dump_openapi.py --check` | `OpenAPI snapshot is current` |
| `scripts/generate_api_guide.py --check` | `current: docs/v3/15-开放API接入指南.md` |
| 前端五步 | 退出码 0；vitest 74 文件 773 用例全过 |
| `scripts/frontend_smoke.py` | `SMOKE OK` |
| `scripts/account_smoke.py` | `SMOKE OK` |
| `scripts/check_doc_links.py` | `BROKEN 0` |
| `POETRY_CACHE_DIR=$(mktemp -d) poetry check --lock` | 退出码 0 |
| `git diff --check` | 退出码 0 |
| Docker 补充检查 | `docker info` 退出码 1（Engine 未运行）→ 构建未验证 |
| 浏览器手动检查 `/more/api-console/guide` | 未验证 |

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 按 12 §3 小步提交（计划、重锁、活文档、改读、Docker/文档、断链检查、文档同步、台账），不用 spec T9 的单条提交 | Superpowers 与 12 §3 都要求小步提交；合并信息仍按 spec 写 |
| R2 | 基线取 **26** 条，`BASELINE_LIMIT = 26` | 多出的一条来自 M63 之前那个 v2 修复提交，M63 §8.3 已记；分布与既有断链一致，不是本里程碑新增（13 §8.9） |
| R3 | Dockerfile 固定 `poetry>=2.1,<3` 且保留 aliyun `-i` | 决策树命中路径 P 且 s = aliyun，spec §4.2.2 第 4 步的「s 不是 aliyun 时换 `-i`」不触发 |
| R4 | `poetry install --dry-run` 的判定按「实际改动只有 numpy 与 scipy」 | 其余 58 行是 `Skipped … Already installed` 的信息输出，不是改动 |
| R5 | T5 提交里的自造断链（`../13-决策记录.md` 应为 `./13-决策记录.md`）修复后 `--amend` 进 T5 提交 | 该链接在 T5 验收时没有被测试覆盖，由 T6 的全仓扫描发现；amend 保持分支各提交都可过断链检查 |
| R6 | 不启动 Docker Engine 做镜像构建 | spec 把它列为不硬性的补充检查；本机 Engine 未运行，启动它超出本里程碑范围 |
| R7 | 不修 `deployment.cors_origins` 的 500 | spec §2 非目标明确：只记录，修它属改对外行为 |

## 偏离（已记 13 §8.7–8.9）

- §8.7 包管理结论：继续用 Poetry 与 aliyun 源（Q8 关闭）。
- §8.8 列表类型设置项实测：`string[]` 会 500。
- §8.9 活文档迁移与断链门禁的细则（含基线 26 而非 25）。

## 遗留

- `deployment.cors_origins` 保存报 500 `INTERNAL_ERROR` 是 v2 既有缺陷，未修；等用户决定由哪个里程碑修（属改对外行为）。
- 常驻服务 `local.maa-api` 仍加载着同步前的 scipy 1.18.1 / numpy 2.5.3；只影响养成规划的延迟导入，请择机执行 `./scripts/maa-api-service.sh reload`。本里程碑没有自动重启服务。
- Docker 镜像构建未验证（Engine 未运行）。
- 浏览器里 `/more/api-console/guide` 的手动检查未做（无法打开浏览器）。
- `tests/fixtures/core_probe_findings.md` 的 `../docs/03-MaaCore内核层设计.md` 相对路径本身就是错的（从 `tests/fixtures/` 出发会指向 `tests/docs/`），仍在基线里，属既有问题。
