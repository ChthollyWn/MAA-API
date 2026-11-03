# SDD ledger — plan: docs/superpowers/plans/2026-09-28-m17-task-param-defaults.md

## Setup

- Base：`a90c9cc`（`feat(agent): drop the step limit, render markdown tables, fix the page height`），分支 `m17-task-param-defaults`（从 `refactor/v2` 新建，直接在当前检出上工作，AGENTS.md 禁止 worktree）。
- Spec：`docs/superpowers/specs/2026-09-28-m17-task-param-defaults.md`；计划：`docs/superpowers/plans/2026-09-28-m17-task-param-defaults.md`。
- 基线：后端 `.venv/bin/python -m pytest -q`；前端 `pnpm test --run` **50 文件 / 473 项全绿**（`/opt/homebrew/bin/node` v23.11.0 + `pnpm.mjs`，PATH 里没有 node/pnpm）。
- 内核：捆绑的 MAA v6.17.5（`resource/lib/maa/Darwin`）。取证用 MAA 仓库 tag `v6.17.5` 的源码与官方文档，并用本机内核实测（`AsstAppendTask` 不需要连设备）。

## User decisions（2026-09-28）

- 有默认值的任务参数一律**按默认值显示并预填**，预填值**随提交体显式下发**（所见即所提交）；接受「MAA 以后改默认值我们不再自动跟随」。
- 默认值由**后端逐字段补真实值**，走标准 JSON Schema `default`。
- 范围只到任务参数，**不做**「日常预设模板」。
- 渠道字段（`client_type` / `server`）**也预填当前全局默认并下发**。
- 内核必填但无默认值的 `Recruit.select` / `confirm`、`Infrast.facility` **由本服务给默认值**（内核缺它们会静默返回 0）。
- `facility` 等枚举在界面上**显示中文**，不显示英文原名。

## Rulings I made

- Ruling: **四类字段刻意不预填**（写入规格 §3.2）——语义是「无限」的 `Fight.times` / `Roguelike.starts_count` / `investments_count`（内核默认 `INT_MAX`，写进表单没有意义，`Fight.times=0` 另有「跳过整个任务」的含义）；语义是「空 = 不启用」的空串字段；随主题 / 模式变化的**条件默认值**（`use_foldartal` / `check_collapsal_paradigms` / `difficulty` / `expected_collapsal_paradigms`——静态默认会在别的模式/主题下改掉内核行为，其中两个还会直接触发本服务的 422）；本服务校验器禁止「设了但不生效」的 `Recruit.expedite_times`。这些字段保持三态显示，标签从错写的「默认」改成「未设置」。
- Ruling: `Roguelike.start_foldartal_list` **既不设默认值也不下发空数组**——内核在 Sami 主题下遇到显式 `[]` 会让 `set_params` 返回 false（任务都无法加入），比「缺省」危险得多。
- Ruling: 文档与源码冲突一律**以源码为准**并改我们的文案：`Recruit.skip_robot`（文档 `@default true`，源码 `find` 且缺省不跳过）、`Recruit.set_time`（文档称仅 `times=0` 生效，源码两条路径都用）、`Roguelike.squad`/`roles`（文档写分队名，源码空串 = 内核自动选）。另外本仓文档原写 `StartUp.start_game_enabled` 默认 True，源码是 `false`，一并修正。
- Ruling: 渠道字段的「生效值」用**导出层 overlay** 暴露（`export_type_schema(model, channel_defaults=...)`，深拷贝后改副本），pydantic 模型与提交流程的 `model_fields_set` 注入语义一字不改；fixture 生成器仍单参调用即拿到内置默认值，保证前端 fixture 与 `/api/tasks/types` 结构一致。
- Ruling: 新增 `scripts/probe_task_defaults.py`，`--model-defaults` 直接把「服务端模型的全默认参数」（补上渠道默认值）喂给真内核 append，作为「预填即下发不会把内核打回 0」的验收入口。
- Ruling: `plan_index` 有默认值后，`Infrast` 自定义换班（`mode=10000`）在校验层面只剩 `filename` 必填；`tests/domain/test_task_models.py` 的合法/非法样本清单随之调整（不是削弱断言，是语义确实变了）。

## Tasks

| Task | 产物 | 结果 |
|---|---|---|
| 1 · 后端默认值与文案修正 | `maa_api/domain/task.py`、`tests/domain/test_task_defaults.py` | 完成：56 个字段补 `default`；`facility` 中文标签挂在 items 上；`tests/domain/test_task_defaults.py` 全绿 |
| 2 · 渠道默认值 overlay | `maa_api/api/routers/tasks.py`、`tests/api/test_task_types_defaults.py` | 完成：深拷贝 overlay + 8 项测试全绿 |
| 3 · 生成物同步 | `web/openapi.json`、`web/src/types/api.d.ts`、`task-types.fixture.json` | 完成：两个 `--check` 均通过 |
| 4 · 前端按默认值显示与预填 | `web/src/features/**` | 完成：物化四个入口、四档状态、控件预填、摘要、提交兜底、设置失效；前端 51 文件 / 502 项全绿（基线 50 / 473） |
| 5 · 文档同步 | docs/04、05、09、12、13、ENVIRONMENT、README | 完成（docs/05 §7.2/§7.3/§7.5/§7.6/§8/§9、docs/09 §7.5、docs/13 §8.17 等） |
| 6 · 验收 | 见下节 | 完成 |

## Task 4 之后我补的裁决

- Ruling: **`riskSummary` 忽略仍等于 schema 默认值的 `x-risk` 字段**。草稿预填默认值后，`Mall.shopping` 与 `Roguelike.investment_enabled` 的内核默认就是 `true`，不改这条判断的话，新建一个什么都不动的采购/肉鸽任务也会弹「确认消耗资源」，等于把确认训练成无脑点过。改的是 `form/serialize.ts`（`sameValue(value, schemaDefault)` 命中就跳过）+ `serialize.test.ts` 新增一条用例。
- Ruling: Task 4 报的**规格 §6.2 自相矛盾**（`default` 档括注写了「或字段无默认值且未设置」）是我写错了，按 docs/09 §7.5 与实现取后者（无默认值且未设置 = `unset`）。规格已就地修正。
- Ruling: `schedules/payload.ts` 的类型断言保留。`openapi-typescript` 会把带 `default` 的属性在生成类型里标成必填（OpenAPI 的 `required` 数组仍是空的，是生成器的既有行为），而定时任务模板本来就允许只写改过的字段；断言处有注释说明，运行时服务端仍按默认值补齐。
- Ruling: `tests/api/test_push_subscriptions_router.py` 的隔离缺陷一并修掉（补 `maa_api.api.deps.get_settings` 的 monkeypatch + 假 settings 的空 `access_token`）——本机 `config.yaml` 一带 token，这 10 个用例就全红，属潜伏的环境依赖，不修则里程碑验收命令永远是红的。

## Verification

- 后端：`.venv/bin/python -m pytest -q` **退出码 0**（collect 1716 项，4 项 hardware skip）。
- 生成物：`scripts/dump_openapi.py --check`、`scripts/generate_task_types_fixture.py --check`、`scripts/generate_api_guide.py --check` 全部 current。
- 前端：`pnpm gen:api:check` / `typecheck` / `test --run`（**51 文件 / 502 项全绿**）/ `build`（含 sw harness）全部退出码 0。
- 集成冒烟：`.venv/bin/python scripts/frontend_smoke.py` → `SMOKE OK`。
- 真内核：`scripts/probe_task_defaults.py --model-defaults`（MAA v6.17.5，不连设备）九种任务类型全默认参数全部被接受（`task_id` 18–26）；对照候选 `{}` 的 StartUp / Recruit / Infrast / Copilot 仍被拒——即 M17 修掉的正是「什么都不改就 502」那三个必填项。
- 环境性事实：修 `test_push_subscriptions_router.py` 之前，本机 `config.yaml` 带 `access_token` 会让那 10 个用例 401；用 `git archive` 到 /tmp（无 `config.yaml`）复现为 10 项全过，据此定位为测试隔离缺陷而非 M17 回归。
