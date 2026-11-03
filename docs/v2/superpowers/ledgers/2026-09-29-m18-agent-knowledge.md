# SDD ledger — plan: docs/superpowers/plans/2026-09-29-m18-agent-knowledge.md

## Setup

- Base：`146317d`（`fix(notify): 崩溃终态通知的重试不再丢掉已改写的那一次，并补三处漏改的通道清单`），分支 `m18-agent-knowledge`。
  该分支原基于 `0381840`（比 `refactor/v2` 落后两个通知相关提交），开工前已 `git rebase refactor/v2` 把计划提交重放到当前 `refactor/v2` 顶端。
- Spec：`docs/superpowers/specs/2026-09-29-m18-agent-knowledge.md`；计划：`docs/superpowers/plans/2026-09-29-m18-agent-knowledge.md`。两者随分支提交 `docs(m18): specify and plan the agent knowledge base`。
- 前置条件核对：计划要求「开工前 `git status` 必须干净」。建分支时 `refactor/v2` 上那批未提交的通知改动，用户已自行提交为 `2acf5a6` / `146317d`，工作区干净，无遗留改动。
- 基线：
  - 后端 `.venv/bin/python -m pytest -q` 退出码 0（`/tmp/m18/baseline-backend.log`；`pytest.ini` 的 `addopts = -q` 不打印统计行，以退出码为准）。
  - 前端 `pnpm test --run` **51 文件 / 506 项全绿**（`/opt/homebrew/bin/node` v23.11.0；PATH 里没有 node/pnpm，跑 pnpm 脚本必须 `PATH=/opt/homebrew/bin:$PATH`，否则 vitest 报 `exec: node: not found`）。
- 内核：捆绑的 MAA v6.17.5（`resource/lib/maa/Darwin`）。
- 依赖真机 / 真实模型的补充检查本轮大概率不可用，结果按实记录。

## Preflight plan scan

计划已给出任务依赖：Task 1 与 Task 4 独立可并行；Task 2、3、5 都改 `maa_api/agent/runtime.py`，串行 2 → 3 → 5；Task 6、7 依赖 1–5；Task 8 依赖 7。主 agent 的复核：

| 任务对 | 共享文件/接口 | 结论 |
|---|---|---|
| 1 ↔ 4 | 无（设备/坐标系 vs db/knowledge/errors） | 可并行。Task 1 若需新错误码会碰 `errors.py`，已核实 `DEVICE_NOT_CONNECTED`/`INVALID_PARAMETER`/`SERVICE_UNAVAILABLE` 均已存在，不需要新增，因此与 Task 4 无文件冲突 |
| 2 ↔ 1 | 无（`pipeline_runner.py`/`tools/pipeline.py`/`runtime.py` vs 设备文件） | 可并行；但 2 的 `tests/agent` 运行会看到 1 的半成品测试，已在 brief 里要求区分 |
| 2 ↔ 4 | 无 | 可并行 |
| 2 → 3 | `runtime.py`、`tools/raw.py` | Task 2 只改原地打转计数，Task 3 改 `_tool_schemas`/`_execute_tool_batch`/`_status_summary`，串行避免冲突 |
| 3 → 5 | `runtime.py`、`tools/__init__.py`、`INCIDENT_ALLOWED_TOOLS` | 串行；Task 3 先固定 `model_visible` 与白名单，Task 5 再加 `read_knowledge` |
| 4 → 5 | `KnowledgeService` 接口、`main.py` 装配 | Task 4 定接口，Task 5 消费；串行 |
| 5 → 7 | OpenAPI / TS / API 指南 | Task 5 改接口，Task 7 统一重新生成并复核 |
| 1 → 7 | `web/openapi.json` | Task 1 也会改错误声明，brief 要求就地重生一次，Task 7 复核 |
| 7 → 8 | 生成的 TS 类型 | Task 8 依赖 7 |

## Rulings I made

主 agent 的裁决：

- Ruling: 分支开工前把 `m18-agent-knowledge` **rebase 到当前 `refactor/v2`**（`0381840` → `146317d`）。计划的前置条件是「refactor/v2 上那批通知改动由用户自行提交或处理」，用户已提交为 `2acf5a6`/`146317d`；里程碑分支必须建立在集成分支当前的顶端，否则最终 `merge --no-ff` 会把两个通知提交当作分支内容。代价：分支的单个文档提交被重放（未 push，无影响）。
- Ruling: 并行度按计划给的依赖图执行，但把 **Task 2 提前与 Task 1、Task 4 并行**（三者文件集不相交：设备/坐标系 vs db+knowledge vs pipeline_runner+tools/pipeline+runtime 的计数段）。Task 2→3→5 仍严格串行。代价：Task 2 的实现者在跑 `tests/agent` 时会看到 Task 1 的半成品测试，已在 brief 里要求区分并在报告里列明。

实现者的裁决（已复核，采纳）：

- Task 1：`/api/device/click` **留在 `atomic.py`**，只把调用目标换成 `DeviceManager.click`，不搬模块（路径、operationId、tag 不变，diff 最小）；`_device_manager` 按仓库既有风格在 `atomic.py` 本地复制一份，不跨 router 导入私有函数。
- Task 1：`click` 只按「分辨率是否已知」判定，不额外要求 `state == connected`（照规格字面）。
- Task 1：换算用 Python `round(px × 720 / 高)`（照计划字面）。风险：Python 是银行家舍入，C++ `ControlScaleProxy` 在恰好 `.5` 时可能差 1；现有证据（2560×1440 的整数倍坐标）分不出舍入方式，已记入台账。
- Task 1：REST 路由不加 docstring（会进 OpenAPI `description`，Task 7 期望 diff 只含错误声明与摘要），改用行内注释。
- Task 1：既有用例 `test_operations_are_delegated_to_adb_and_core_substitutes` 补 `ResolutionGot` 前置，**没有**放宽断言。
- Task 2：只含 `wait_pipeline` 的批次**既不计数也不重置** `last_signature` / `repeats`。若重置，模型「等一次、查一次」交替即可永远清空计数，绕过保险；保持不变时，「等一次/查一次」序列里的 `get_pipeline` 批次仍在第 3 次触发 `stuck_loop`（有对照用例）。
- Task 2：`wait_terminal` 每次轮询开**一次性短会话**（`get(with_tasks=True)` 后立即关闭），绝不使用调用方的 `ctx.db_session`；`poll_seconds` 只作为测试用关键字参数，线上恒为 1 秒。
- Task 2：超时返回最后读到的状态且**不算错误**（工具结果里没有 `error` 字段）；未知 id 立即抛 `PIPELINE_NOT_FOUND`，不等满超时。
- Task 4：**docs/05 §4.11 的三行与「共 101 条」由 Task 4 一并改**，因为 `tests/domain/test_errors.py` 机械解析 docs/05 的十五张错误码表并断言集合、状态码映射与总条数完全一致，不同改则任务书要求的验证命令不可能通过。→ Task 9 只补知识库**端点**行，不要重复加错误码行。
- Task 4：`enabled` 的「默认真」用 Python 侧 `Field(default=True)`，DDL 不带 `server_default`（与 `schedule` / `notify_channel` / `resource_asset` 三张既有表的 `enabled` 同口径）。
- Task 4：`id` 正则照抄规格 `^[a-z0-9][a-z0-9-]{1,47}$`，因此尾随/连续中划线（如 `trailing-`）**合法**，最短 2 字、最长 48 字。
- Task 4：内置 loader **不要求文件名等于 id**（否则「id 重复 fail loud」永远不可达，成死代码）：按文件名排序加载、按 frontmatter 的 id 去重；未知 frontmatter 字段（含 `enabled`）、frontmatter 非映射、缺字段、空正文、目录里没有 `.md` 一律 fail loud。
- Task 4：`render_context()` 空段落一律写「暂无」（规格只对档案写了这条，索引与术语表按同一处理）；多条启用的 `glossary` / `profile` **全部注入**（3,000 字上限算的就是它们的正文合计，只注入第一条会把上限算到模型看不见的内容上）。
- Task 4：`list_entries()` 的排序与索引一致（kind 顺序 playbook/screen/rule/glossary/profile，再按 id），前端分组可直接复用。
- Task 4：覆盖行的 `kind` 恒为 NULL；若库里留着「上一版本自定义行、与新版内置同 id」，`kind` 取内置值（不可覆盖），用户写过的其它列仍优先，`modified=True`。
- Task 4：上限**先判后写**：把本次结果并进全表算出生效状态，通过了才落库，被拒时表里不留半成品；先判启用条目数（50），再判全文合计（3,000）。
- Task 4：字段校验在服务层抛 `VALIDATION_ERROR`(422) 作**兜底**（`details = {entry_id, field, reason}`）；`KnowledgeWrite` 本身不带长度/正则约束，Task 5 的请求模型必须自己加 `pattern` / `max_length` 才能给出 FastAPI 原生 422 形状。
- Task 4：自定义行缺列（外部改库造成的脏数据）抛 `RuntimeError` 而不是 `AppError` —— 那是数据完整性问题，不是调用方的错。
- Task 4：顺带按代码事实修正三处陈旧注释（`models.py`「16 张业务表」→19、`tests/db/conftest.py` 同步、`errors.py`「95/91 条」→「101/100 条」）。
- Task 5：知识库路由**放新文件** `maa_api/api/routers/knowledge.py`（不塞进 777 行的 `agent.py`），但沿用 `tags=["agent"]` —— `tests/test_app_skeleton.py` 与 `scripts/api_smoke.py` 都断言 tag 共 16 个、`operationId` 形如 `{tag}_{name}` 且唯一，复用同一 tag 就不必动这些门禁。四个 `operationId`：`agent_list_knowledge`、`agent_get_knowledge_entry`、`agent_put_knowledge_entry`、`agent_delete_knowledge_entry`。
- Task 5：`_service(request)` 未装配时抛 `SERVICE_UNAVAILABLE`，**不写进 `error_responses`** —— 与既有三个服务（`schedule_service` / `api_snippet_service` 等）的写法一致。REST 的 422 由 `KnowledgeWriteRequest` 自己补 `pattern` / `Literal` / `max_length` / `extra=forbid` 给出，服务层同名校验保留作兜底（落实 Task 4 的裁决）。
- Task 5：`AgentRuntime.knowledge_service` 取服务「构造参数优先、`request.app.state.knowledge_service` 兜底」，与既有 `context_provider` 同款接法；两者都没有时按「读取失败」处理，注入「知识库暂不可用」并记 warning。代价：没装配服务的既有 runtime 用例，system 消息里会多这一行 —— 既有断言都是子串式的，全绿。
- Task 5：`build_model_context(knowledge_context=None)` 时**不插**这一段（保留直接调用 `build_model_context` 的既有语义）；runtime 侧永远传字符串（真实内容或降级文案）。知识库段与状态摘要都在迭代循环**之前**生成一次，循环内所有请求复用（规格「每轮开头生成状态摘要时一并生成」）。
- Task 6：假件复用走**真包** `tests/fakes/fake_llm.py`（新增共用 FakeLLM），不用 `from test_runtime import ...` —— `tests/agent/` 没有 `__init__.py`，按顶层模块名导入，跨文件引用兄弟测试的私有名不可靠；`tests/fakes/__init__.py` 与既有测试一行未改。
- Task 6：场景测试用**生产全量注册表 + 真 PolicyEngine/ConfirmationService/AgentRuntime/QueueService/PipelineRunner/KnowledgeService**，只有模型、MaaCore、设备三个进程边界是假件。「全程没有 `confirmation_required`」因此验证的是真实策略判定，不是空注册表。
- Task 6：`confirmation_timeout_seconds=5` + 启动过期扫描，让确认类回归 5 秒内失败而不是挂 600 秒；`wait_pipeline` 的 `pipeline_id` 用可调用响应当场从上一条工具结果读取（模拟真实模型行为）。
- Task 7：`web/openapi.json` 与 `web/src/types/api.d.ts` 重跑后与 HEAD **逐字节相同**（Task 5 已就地生成过，生成器 `sort_keys=True`、无时间戳，幂等可证）；docs/14 只 +5 行（错误码表 3 行 + §5.2 手写补一段坐标系说明）。
- Task 7 / 主 agent 裁决：三个 device 端点的 summary（`执行单点点击（坐标为设备像素，与截图一致）`等）**违反 docs/05 §11.2「summary 动宾短语、≤12 字、不带标点」**，但满足规格 §4.5「REST 摘要写明坐标系」。**以规格为准**，在 docs/13 §8 记一条例外，不改文案（改它要动后端源码 + 两个生成物，且无测试钉住）。
- 主 agent 裁决：**修掉 `scripts/agent_smoke.py` 的预存在失败**。M13 的 `0ca99ff` 把视觉探针改成「回复里必须含 `blue`」（`_VISION_PROBE_MARKER`），但没同步改脚本里 Fake LLM 的探针回复（仍是 `visual probe ok`），于是 `capabilities/check` 断言 `vision is True` 必然失败。已核实 M18 未改过该脚本（`git diff 146317d..HEAD -- scripts/agent_smoke.py` 为空），属预存在缺陷；把回复改成 `blue` 后 `SMOKE OK`。不修则 Task 10 的验收门槛永远是红的（M17 修 `test_push_subscriptions_router.py` 的同类先例）。提交 `f155626`。

## Tasks

| Task | 产物 | 结果 |
|---|---|---|
| 1 · 点击坐标统一为设备像素 | 设备服务 / REST click / Agent 工具 | 完成，`2e6f6eb` |
| 2 · `wait_pipeline` 与原地打转豁免 | `pipeline_runner.py`、`tools/pipeline.py`、`runtime.py` | 完成，`d48d172` |
| 3 · `trigger_screencap` 隐藏 + 状态摘要加时间 | `registry.py`、`tools/raw.py`、`runtime.py` | 完成，`6ff1823` |
| 4 · 知识库数据层与服务 | `db`、`knowledge/`、`errors.py` | 完成，`ccd2123` |
| 5 · 知识库 REST 与上下文注入 | 路由、`tools/knowledge.py`、`runtime.py`、`main.py` | 完成，`4df877d` |
| 6 · 场景测试 | `tests/agent/test_account_resources_flow.py` | 完成，`6c9f387` |
| 7 · 生成物同步 | `web/openapi.json`、`api.d.ts`、docs/14 | 完成，`3e3abff` |
| 8 · 前端知识库设置页 | `web/src/features/settings/knowledge/` | 完成，`c4d54e2` |
| 9 · 文档同步 | docs 各篇 | 完成，`0f9785e` |
| 10 · 验收（主 agent） | 见下 | 完成 |

## 最终交付

- 分支 `m18-agent-knowledge`，从 `refactor/v2`（`146317d`）新建并重放计划提交；本里程碑全部改动共 **12 个提交**（`c00a9dc` 规格与计划、`ccd2123` Task 4、`2e6f6eb` Task 1、`d48d172` Task 2、`6ff1823` Task 3、`4df877d` Task 5、`6c9f387` Task 6、`3e3abff` Task 7、`f155626` 冒烟修复、`c4d54e2` Task 8、`0f9785e` Task 9、本台账）。
- 交付内容：点击坐标系统一为设备像素（Agent 工具 + REST，服务端一次换算）；`wait_pipeline` 等待原语与原地打转豁免；`trigger_screencap` 对模型隐藏并移出诊断白名单；状态摘要加 `now`；知识库（表 `agent_knowledge` + migration `0014` + 5 条内置条目 + `KnowledgeService` + 四个 REST 端点 + `read_knowledge` 工具 + system 消息注入 + 提示词「工作方法」）；场景测试；前端「Agent 知识库」设置分区与整页编辑器；docs 九篇同步。
- 工具目录 40 → 42（`wait_pipeline`、`read_knowledge`）；新增错误码 3 个（`KNOWLEDGE_NOT_FOUND` 404、`KNOWLEDGE_BUILTIN_READONLY` 409、`KNOWLEDGE_LIMIT_EXCEEDED` 409）；`INCIDENT_ALLOWED_TOOLS` 去掉 `trigger_screencap`、加上 `read_knowledge`。
- 未做（规格 §3.2 明确排除，属第 2、3 步）：截图区域裁剪、`record_observation` 与账号观测表、`lookup_item`、确定性换算工具、MAA `Custom` 任务类型与内置导航 task、`Depot` 任务接入、知识检索、按渠道区分知识。
- 真机与真实模型的补充检查未做（本机无可用的真机与真实模型凭据）：两个问题的读数核对、真机点击坐标换算、内置 `screen-depot` 的页签与凭证位置。

## Task progress

- Task 1：实现者完成并报告（`task-1-report.md`）。主 agent 复核：读过全部源码 diff（`device_service.py` 的 `_device_pixel_bounds` / `_ensure_pixels_within_bounds` / `click` 换算、`atomic.py` 走 `DeviceManager`、`device.py` 与 `raw.py` 的摘要与描述、`asst.py` / `asst_protocol.py` 注释）；亲自复跑 `.venv/bin/python -m pytest -q tests/agent/test_device_raw_tools.py tests/api/test_device_router.py tests/services/test_device_service.py` **退出码 0**；核对全仓只有 `raw.py`（Agent 工具）、`atomic.py`（REST）、`device_service.py`（内核）三处点击入口，前端不调用 `/api/device/click`；核对 `web/openapi.json` 的 diff 只有三类预期变化。提交 `2e6f6eb`。
- Task 2：实现者完成并报告（`task-2-report.md`）。主 agent 复核：读过 `wait_terminal` / `_read_pipeline` / `STUCK_EXEMPT_TOOLS` / `_exempt_from_stuck_loop` 全部 diff，确认每次轮询用一次性短会话、`tasks` 是非映射属性（`populate_existing=True`，会话关闭后可读）；亲自复跑 `.venv/bin/python -m pytest -q tests/agent` **退出码 0**。提交 `d48d172`。
- Task 4：实现者完成并报告（`task-4-report.md`）。主 agent 复核：读过 `KnowledgeService` 全部实现、migration、repository、错误码登记；**用脚本把规格 §4.6 的 5 个 fenced block 与 5 个内置 `.md` 逐字节比对，全部 IDENTICAL**；亲自复跑 `.venv/bin/python -m pytest -q tests/agent/test_knowledge_service.py tests/db tests/domain` **退出码 0**。提交 `ccd2123`（含 docs/05 的错误码表三行，见裁决）。
- Task 3：实现者完成并报告（`task-3-report.md`）。主 agent 复核：读过 `registry.py` / `raw.py` / `runtime.py` 全部 diff，确认派发拒绝分支在 `allowed_tools` 之前且不落审计、`_status_summary` 的 `now` 在两个分支都是第一项且值以服务端时钟为准；亲自复跑 `.venv/bin/python -m pytest -q tests/agent tests/api/test_agent_router.py` **退出码 0**。提交 `6ff1823`。
- 提交切分：Task 1、3 都改了 `maa_api/agent/tools/raw.py`；Task 2、3 都改了 `maa_api/agent/runtime.py`、`tests/agent/test_runtime.py`、`tests/agent/test_tool_catalog.py`。主 agent 用 hunk 级切分（`git apply --cached` 只取属于该任务的 hunk）把四个任务拆成四个提交，提交后工作区干净、`git diff --cached --check` 通过。
- Task 5：实现者完成并报告（`task-5-report.md`）。主 agent 复核：读过新路由 `knowledge.py`、`tools/knowledge.py`、`main.py` 装配（含与 `agent.router` 的路由冲突检查：agent 侧没有 `/api/agent/{x}` 形态的通配路径）、`runtime.py` 的注入与提示词 diff（「工作方法」一段与安全边界补句与规格 §4.3 **逐字一致**）；亲自复跑 `.venv/bin/python -m pytest -q tests/agent tests/api` **退出码 0**；`.venv/bin/python scripts/dump_openapi.py --check` 退出码 0；用脚本核对工具目录 42 个、含 `knowledge` 组、`read_knowledge` 在白名单里、`SYSTEM_PROMPT` 含「工作方法」与两个工具名。提交 `4df877d`。
- Task 6：实现者完成并报告（`task-6-report.md`）。主 agent 复核：读过场景测试的接线与 8 组断言，确认只有模型/MaaCore/设备是假件、环境隔离到 `tmp_path`；亲自复跑 `.venv/bin/python -m pytest -q tests/agent/test_account_resources_flow.py` **退出码 0**。提交 `6c9f387`。
- Task 7：实现者完成并报告（`task-7-report.md`）。主 agent 复核：亲自复跑 `.venv/bin/python scripts/dump_openapi.py --check` 与 `.venv/bin/python scripts/generate_api_guide.py --check`，**均退出码 0**；核对工作区只有 docs/14 一个文件、+5 行。提交 `3e3abff`。
- 预存在缺陷修复：`scripts/agent_smoke.py` 的视觉探针 Fake 回复（见「Rulings I made」）。主 agent 亲自复跑 `.venv/bin/python scripts/agent_smoke.py` → **`SMOKE OK`，退出码 0**。提交 `f155626`。
- Task 8：实现者完成并报告（`task-8-report.md`）。主 agent 复核：读过 `sections.ts` / `router.tsx` / `knowledge/api.ts` 的 diff 与新增模块，确认分区排在 `agent` 之后、两条编辑器路由排在 `:section` 通配之前、启用开关走「GET 详情 → 整条 PUT」、恢复默认/删除都有 `useConfirm` 二次确认；亲自复跑 `pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` **退出码 0**（53 文件 / 542 项，基线 51 / 506），build 末尾 `[sw_harness] SERVICE WORKER OK`。提交 `c4d54e2`。
- Task 9：实现者完成并报告（`task-9-report.md`）。主 agent 复核：读过 docs/11（新增 §6 知识库，**填的是原有编号空缺**，没有重排后续小节，跨文档的「11 §8.x」引用全部仍然成立）、docs/13 §8.23（71 行，十条）、docs/05 §6.3/§6.15、docs/04 §5.16、docs/09 §6.5、docs/12、docs/README、docs/ENVIRONMENT 的 diff；**用脚本核对 docs/05 §6.15 的 16 行与声明「16 条」一致**（原为 12 行写「10 条」，属顺带修正）；亲自复跑全量 pytest（1886 passed, 4 skipped，退出码 0）与两个 `--check`（退出码 0）。提交 `0f9785e`。
- Task 10（主 agent 亲自跑，全部在最终提交 `0f9785e` 上）：
  - `.venv/bin/python -m pytest -o addopts= -q` → **1886 passed, 4 skipped**，退出码 0；
  - `.venv/bin/python scripts/dump_openapi.py --check` → `OpenAPI snapshot is current`，退出码 0；
  - `.venv/bin/python scripts/generate_api_guide.py --check` → `API guide generated sections are current`，退出码 0；
  - `cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` → 全部退出码 0，**53 文件 / 542 项**，`[sw_harness] SERVICE WORKER OK`；
  - `scripts/api_smoke.py` → `SMOKE OK`（退出码 0）；`scripts/frontend_smoke.py` → `SMOKE OK`（退出码 0）；`scripts/agent_smoke.py` → `SMOKE OK`（退出码 0，单独运行两次均绿）。
- **环境性告诫（已实测定位，与 M18 无关）**：三个冒烟脚本在同一个 shell 里串跑时，`agent_smoke.py` 的「仓库 DB/config 指纹未改变」守卫会误报 —— 它比对整个仓库的 `*.db` / `-wal` / `-shm` / `config.yaml`，而本机有外部进程（一个在跑的 MAA-API 实例）持续写 `resource/maa_api.db-wal`。判定方法：在**什么都不跑**的 45 秒里该文件 mtime 自己就从 `1790664240` 变成 `1790664268`；`api_smoke`→`agent_smoke` 串跑可稳定复现，单独跑则绿。守卫与两个脚本在 M18 之前就是这个行为（`git diff 146317d..HEAD -- scripts/agent_smoke.py` 只有视觉探针那一行）。已在 docs/13 §8.23 记一条告诫。
- **发现的既有文档/代码不一致（未改，属本里程碑范围外，留给用户裁决）**：
  1. docs/11 §3.2 把 `delete_schedule` 标成 `CONDITIONAL`，代码里是 `SAFE`（`docs/README` 的白名单与代码一致）；
  2. docs/05 的条数计数陈旧：§6.1 实际 7 行写「6 条」、§6.14 实际 15 行写「13 条」（§6.15 的「10 条」由 Task 9 连带修正为 16 条）；
  3. docs/08 §6.1 对照表「点击 | 坐标系与内核一致」措辞偏松（不算错）。
