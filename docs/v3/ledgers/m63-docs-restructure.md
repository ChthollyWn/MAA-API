# M63 文档与 v2 目录迁移（台账）

- 分支：`docs-restructure`；基点：`dev` = `46740f2`（含 `cherry-pick` 进来的两个 v2 修复 `e1dc275`、`46740f2`）；合并：`dev` 上的 `merge: v3 文档与 v2 目录迁移（M63，见 docs/v3/13 §8.1–8.6）`；tag：`v3-m63`
- spec：[m63-docs-restructure](../specs/m63-docs-restructure.md)
- 计划：[m63-docs-restructure](../plans/m63-docs-restructure.md)
- 决策记录：[docs/v3/13 §8](../13-决策记录.md)
- 来源：用户 2026-11-03「先切换到 dev 分支，然后使用 using-superpowers 技能，然后按 Superpowers 流程开发 MAA-API v3：先 brainstorming，再 writing-plans，然后按任务 TDD 执行」；brainstorming 阶段确认「原样开工」，即 `docs/v3/01–13` 作为已批准设计，不重审；执行方式选原生。

## 做了什么

1. 计划入库：`docs/v3/plans/m63-docs-restructure.md`，7 个任务（T0 计划与脚本、T1 搬迁与路径、T2 链接、T3 索引与约定、T4 全量验收、T5 台账、T6 合并）。
2. 链接脚本就绪：把 spec 附录 A 的脚本放到 `/tmp/maa_v3/relink.py`，加「跳过行内代码」改造（`INLINE_CODE` 带捕获组）；`check` 基线 66 → 63，`docs/v3` 内 40 → 37。
3. T2 搬迁：15 份编号文档、`README.md`、`DEFECTS.md` 与 `docs/superpowers/` 用 `git mv` 整体迁入 `docs/v2/`，121 条重命名。
4. T3 路径：6 个按路径读文档的文件改指 `docs/v2/`（两个 domain 测试、知识服务测试、API 指南生成器、前端 `GuideMarkdown.tsx`、`.gitignore` 注释）。
5. T4 链接：一次性脚本重写 7 个文件 53 条相对链接；全仓断链 73 → 26（全部既有），`docs/v3` 内 0。
6. T5 索引与约定：新建 `docs/README.md`；根 `README.md` 的导航、文档表、目录结构句改到新结构并加 v3 入口；`AGENTS.md` 切到 v3 口径，环境陷阱补 `rg` 缺失与 pytest `-q` 叠加两条。
7. T6 验收与合并：证据见下；之后合并回 `dev`、打 `v3-m63`、删临时分支。

## 验证证据

| 命令 | 结果 |
|---|---|
| 四个受影响测试（搬迁后、改路径前） | 红：退出码 2，`FileNotFoundError: docs/05-API规范与路由清单.md` |
| 四个受影响测试（改路径后） | 绿：退出码 0，`123 passed in 1.71s` |
| `cd web && pnpm build`（改路径前 / 后） | 1（`Failed to resolve import ... docs/14-开放API接入指南.md`）/ 0 |
| `/tmp/maa_v3/relink.py check`（搬迁后、重写前） | `BROKEN 73` |
| `/tmp/maa_v3/relink.py rewrite` | `FILES 7 LINKS 53` |
| `/tmp/maa_v3/relink.py check`（重写后） | `BROKEN 26`，`docs/v3` 内 0 |
| `.venv/bin/python -m pytest -o addopts= -q` | `3 failed, 3849 passed, 5 skipped, 2 xfailed in 219.49s`；3 条失败与开工基线同一组（`test_activity_index.py` 2 条 + `test_stage_layout.py` 1 条，都是真资源用例） |
| `scripts/dump_openapi.py --check` | 退出码 0，`OpenAPI snapshot is current: web/openapi.json` |
| `scripts/generate_api_guide.py --check` | 退出码 0，`API guide generated sections are current: docs/v2/14-开放API接入指南.md` |
| 前端五步（install / gen:api:check / typecheck / test / build） | 退出码 0；vitest 74 文件 773 用例全过 |
| `scripts/frontend_smoke.py` | 退出码 0，末行 `SMOKE OK` |
| `git diff --check` | 退出码 0 |

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 计划与台账放 `docs/v3/plans/`、`docs/v3/ledgers/`，文件名与 spec 同名 | v3 文档口径如此；v2 的 superpowers 产物随搬迁整体进 `docs/v2/superpowers/`（13 §8.6） |
| R2 | 链接检查跳过行内代码，`INLINE_CODE` 正则带捕获组 | 附录 A 的脚本会把 `docs/v3` 里三处教学示例（`](…)`、`](url)`、`](地址)`）当成断链；不带捕获组时 `re.split` 还会把普通文字误当代码段，实测断链数从 63 掉到 48 并漏报真实断链（13 §8.3） |
| R3 | 断链验收改为「`docs/v3` 内 0 条，其余不超过搬迁前的 26 条」，不追求 spec §7 写的 25 | copy-audit 的既有断链从 11 变 12，是 v2 修复提交的文案更新造成的，与 M63 无关（13 §8.3） |
| R4 | `AGENTS.md` 的「规格与决策」只写「偏离记 `docs/v3/13 §8`」，不写「与其他文档冲突时以它为准」 | v3 README 已声明「决策速查表是单一事实来源」，再并列一条会让冲突优先级出现两条规则；优先级链留给 M64 校准契约时定（见遗留） |
| R5 | 根 `README.md` 的「文档索引」行文案改为「v2 与 v3 文档目录、跨版本说明」，并新增「v3 方案」一行 | spec 只要求入口指向新结构，文案自拟 |
| R6 | `AGENTS.md` 环境陷阱补两条：本机没有 `rg`；pytest `-q` 叠加会隐藏汇总行 | 都是本次实测踩到的环境事实，后续每个里程碑的验证都会用到（13 §8.4） |

## 偏离（已记 13 §8.1–8.6）

- §8.1 分支改为从 dev 拉 `docs-restructure`，两个 v2 修复单独进 dev。
- §8.2 前置检查与 T1 计数的过时数字。
- §8.3 断链基线由 25 变为 26，检查跳过行内代码。
- §8.4 本机没有 ripgrep，验证命令改用 grep。
- §8.5 `docs/ideas` 已由先前会话提交，保留跟踪。
- §8.6 计划与台账的落盘位置与命名。

## 遗留

- 远端 `origin/v3-plan` 分支还在（本地已删）。按约定不 push，要删远端需要你明确要求。
- 26 条既有断链（`docs/v2/superpowers` 13、`docs/copy-audit` 12、`tests/fixtures` 1）本次不修，留给 M64 的 `scripts/check_doc_links.py` 建基线；M64 必须沿用「跳过行内代码」的口径。
- `tests/fixtures/core_probe_findings.md:31` 的 `../docs/03-...` 相对路径本来就是错的（从 `tests/fixtures/` 出发会指向 `tests/docs/`），搬迁后依旧断，属既有问题。
- `scripts/probe_core_api.py:390` 的 markdown 链接指向旧 `docs/03`，链接脚本只处理 `.md`，留待 M64 决定是否修。
- 冲突优先级链（决策速查表 / 02–05 / spec / 13 §8）在文档里仍是多条并列的说法，M64 校准契约时一并定。
