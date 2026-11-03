# M75 工作记忆、外置与计划模式（规格）

> 阶段：P2　依赖：M74　分支：`workspace-plan`　迁移：`0029_artifacts_plans`
> 本文是 M75 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 分支干净 | `git status -sb` | `## workspace-plan` |
| 2 | M74 已合并 | `git tag -l 'v3-m74'` | `v3-m74` |
| 3 | 下一个迁移号 | `ls maa_api/db/migrations/versions/ \| tail -n 3` | 最大号小于 0029；不是就用下一个空号并记进 13 §8 |
| 4 | 压缩检查点已在 | `rg -n "class CompactionCheckpoint" maa_api/db/models/` | 1 行 |
| 5 | 工具输出仍整段进上下文 | `rg -n "def _append_tool\|role=.tool" maa_api/agent/loop.py \| head` | 记下函数名，本里程碑在它返回前插入外置 |
| 6 | 前端工单页 | `test -f web/src/features/tickets/TicketPage.tsx && echo ok` | `ok` |
| 7 | 基线 | `.venv/bin/python -m pytest -q tests/agent/ > /tmp/m75-base.log 2>&1; echo $?; tail -n 3 /tmp/m75-base.log` | 记下退出码 |

## 1. 背景与目标

压缩（M74）解决历史过长。单次工具输出过长、多步计划散落在对话里，仍然会把上下文撑满，也会让总控看不到 Worker 的中间结论。本里程碑按 [06 §6、§7](../06-上下文工程.md) 做三件事：

1. 超长工具输出整段外置为产物，上下文里只留指针。
2. Worker 用 `write_note` 记中间结论，用 `read_artifact` 按偏移续读。
3. 多步工作先写计划。步骤数达到阈值时要用户批准，批准前只能调用只读工具。

## 2. 非目标

- 不生成图表、表单、仪表盘（M90）。`ArtifactKind` 里的 `chart`、`table`、`form`、`dashboard` 本里程碑不写入。
- 不改压缩算法与摘要提示词。
- 总控没有 `update_plan`、`write_note`、`read_artifact`。总控读产物走既有工单详情里的产物列表，不经过模型工具。
- 不改工具目录里已有工具的返回形状。外置发生在中间件，工具函数本身仍返回完整结果。

## 3. 相关文档

- 表：[03 §8.2、§8.3](../03-数据模型.md)。保留：[03 §14](../03-数据模型.md) 的 `artifact` 行。
- 端点与错误码：[04 §2.2](../04-接口与事件协议.md) 的 M75 行，[04 §5](../04-接口与事件协议.md) 的 `ARTIFACT_*`、`PLAN_*`。
- 中间件与工具：[05 §2.2](../05-运行时与代理定义.md) 第 9、10 项，[05 §3.3](../05-运行时与代理定义.md) 的 `write_note`、`read_artifact`、`update_plan`。
- 行为全文：[06 §6、§7](../06-上下文工程.md)。配置键：[04 §6](../04-接口与事件协议.md) 的 `context.spill_threshold_chars`、`plan.approval_mode`、`plan.complex_min_steps`。
- WebSocket：`agent_event` 的 `plan_updated`、`artifact_created`（04 §4.2）。

## 4. 设计

### 4.1 数据模型

`artifact`、`run_plan` 的列、索引与 03 §8.2、§8.3 逐列一致。枚举：

```python
class ArtifactKind(StrEnum):
    NOTE = "note"
    SPILL = "spill"
    REPORT = "report"
    CHART = "chart"
    TABLE = "table"
    FILE = "file"
    FORM = "form"
    DASHBOARD = "dashboard"

class ArtifactStorage(StrEnum):
    DB = "db"
    FILE = "file"

class PlanStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    ACTIVE = "active"
    DONE = "done"
    ABANDONED = "abandoned"

class PlanItemStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"
```

`run_plan.items` 的元素：`{id: str, title: str, status: PlanItemStatus, note: str}`。`id` 由服务生成，8 位十六进制，同一计划内唯一。

部分唯一索引的写法与 M66 的 `uq_agent_run_session_id_active` 相同：`sqlite_where=text("status IN ('draft','awaiting_approval','active')")`。

迁移 `0029_artifacts_plans` 只建这两张表。`downgrade` 按依赖反序删除。

### 4.2 模块与文件

新建：

- `maa_api/agent/artifacts/service.py`：`ArtifactService`
- `maa_api/agent/plans/service.py`：`PlanService`
- `maa_api/agent/loop/spill.py`：`SpillMiddleware`
- `maa_api/agent/loop/plan.py`：`PlanMiddleware`
- `maa_api/agent/tools/workspace.py`：三个工具的实现
- `maa_api/api/agent/artifacts.py`、`maa_api/api/agent/plans.py`
- `web/src/features/tickets/PlanPanel.tsx`、`web/src/features/tickets/ArtifactList.tsx`

`app.state.artifact_service`、`app.state.plan_service` 在 `main.py` 的 lifespan 里、`agent_loop` 创建之后挂上。保留任务挂在已有的调度器上，名字 `maa-artifact-retention`，每天 03:40。

### 4.3 接口

路径、函数名、状态码、错误码与 04 §2.2 的 M75 行一致，不要另起路径。响应模型放 `maa_api/api/schemas/agent_v3.py`（04 §1）。operationId 为 `agent_<函数名>`。

| 方法与路径 | 函数名 | 行为 |
|---|---|---|
| GET `/api/agent/artifacts` | `list_artifacts` | query：`ticket_id`、`session_id`、`kind`、`page`、`size`。分页形状见 04 §1 |
| GET `/api/agent/artifacts/{artifact_id}` | `get_artifact` | 元数据，正文最多 64 KiB；超出截断并带 `truncated: true`。404 `ARTIFACT_NOT_FOUND` |
| GET `/api/agent/artifacts/{artifact_id}/content` | `download_artifact` | 原始字节，`Content-Type` 取 `mime` |
| PUT `/api/agent/artifacts/{artifact_id}/pin` | `pin_artifact` | `{pinned: bool}` |
| GET `/api/agent/sessions/{session_id}/plan` | `get_plan` | 200 当前未结束计划，没有则为 `null` |
| POST `/api/agent/sessions/{session_id}/plan/approval` | `approve_plan` | `{approved: bool, reason?}`。`approved=false` 把状态退回 `draft`。不在待批准则 409 `PLAN_NOT_AWAITING_APPROVAL` |

`ArtifactOut`：`id`、`ticket_id`、`session_id`、`kind`、`title`、`mime`、`size_bytes`、`storage`、`pinned`、`created_at`。列表不返回正文。

`PlanOut`：`id`、`session_id`、`ticket_id`、`title`、`items`、`status`、`approved_by`、`approved_at`。

控制台登录用户可以经这两个 GET 读任意产物。Worker 工具 `read_artifact` 的工单范围限制只约束模型，不约束 HTTP。

### 4.4 配置项

三个键的元数据与 04 §6 一致。`spill_threshold_chars` 范围 1000–100000，默认 8000。`complex_min_steps` 范围 2–20，默认 4。`approval_mode` 取值 `always`、`complex`、`never`，默认 `complex`。分组以 04 的「Agent」为准；04 写的是 Agent，不要另建「上下文」分组。

### 4.5 行为与算法

**写入产物**

```
def put(self, *, kind, title, body: bytes, mime, session_id, ticket_id, run_id, meta, pinned=False) -> Artifact:
    if len(body) > 16 * 1024 * 1024: raise ApiError(ARTIFACT_TOO_LARGE)  # 413
    sha = sha256(body).hexdigest()
    if len(body) <= 256 * 1024:
        storage, content, path = "db", body.decode("utf-8", "replace"), None
    else:
        写 resource/artifacts/<id>.part ，再 os.replace 为 <id>
        storage, content, path = "file", None, f"artifacts/<id>"
```

**外置**

`SpillMiddleware.wrap_tool` 在工具成功返回后：

1. 工具名属于不外置名单则原样返回。名单与 06 §6.1 一致：`get_operator_box`、`get_logs`、`read_file`、`search_code`、`list_files`、`read_artifact`、`report`、`delegate`、`ask_user`、`update_plan`、`write_note`，以及返回里带图像字段的工具（`get_screenshot`、`send_screenshot`、`annotate_screenshot`）。
2. 当前代理定义的 `tools` 不含 `read_artifact` 时不外置。
3. 把结果 `json.dumps(ensure_ascii=False)` 后计字符。不超过 `context.spill_threshold_chars`（默认 8000）则不外置。
4. 超过则 `put(kind="spill", title=f"{tool} 的输出", ...)`，返回 06 §6.1 的指针对象。`preview` 为前 3000 字加后 1000 字。审计参数与结果仍记完整原文，在工具函数返回后、中间件替换之前记。

**续读**

`read_artifact(artifact_id, offset=0, limit=None)`：`limit` 缺省与上限都是阈值。返回形状见 06 §6.3。读到末尾 `next_offset` 为 null。会话所属工单与产物的 `ticket_id` 都不空且不相等，或者产物 `session_id` 与当前会话不相等且当前代理不是总控，返回 `ARTIFACT_NOT_FOUND`。总控没有这个工具，所以实现里「总控可读全部」只留给 HTTP。

**笔记**

`write_note(title, content, artifact_id=None)`：`content` 超过 20000 字返回工具错误 `note_too_long`（不是新的 HTTP 错误码，写在工具结果的 `error` 字段）。`artifact_id` 为空则新建 `kind=note`、`mime=text/markdown`。非空则必须是本工单已有的 note，否则 `ARTIFACT_NOT_FOUND`；更新 `content`、`sha256`、`updated_at`。`durable.notes` 在动态尾部只列 `{id, title}`，由 `DynamicTail` 在 M74 的尾部组装处追加 `notes` 段。

**计划状态机**

与 06 §7.1 的图一致。`update_plan(title, items)` 每次提交完整列表。规则：

| 条件 | 结果状态 |
|---|---|
| 没有未结束计划，且需要批准 | `awaiting_approval` |
| 没有未结束计划，且不需要批准 | `active`，第一项若全是 pending 则把第一项改为 `in_progress` |
| 已有 `active`，只改状态、备注或 cancelled | 保持 `active` |
| 已有 `active`，新增步骤或改了某步标题，且需要批准 | `awaiting_approval` |
| 已有 `awaiting_approval` 或 `draft` | 按是否需要批准进入 `awaiting_approval` 或 `active` |
| 调用方把全部步骤标 done 或 cancelled | `done` |
| 工单进入 `done`、`failed`、`cancelled` | 未结束计划改为 `abandoned` |

「需要批准」：`plan.approval_mode=always` 恒真；`never` 恒假；`complex`（默认）在步骤数 ≥ `plan.complex_min_steps`（默认 4）时为真。

同一时刻最多一项 `in_progress`，违反时工具返回 `plan_invalid`，不写库。

待批准时，工具结果里带 `human_action: {type: "decision", ref_id: plan_id, prompt: "批准计划「<title>」？"}`。本轮结束时若计划仍待批准、工单的 `human_action.ref_id` 不是这个 plan_id，`PlanMiddleware.after_agent` 代交一条 `report(status=waiting, summary=等待批准计划, human_action=...)`。

批准前允许的工具：`read_only=True` 的工具，加上 `update_plan`、`write_note`、`read_artifact`、`ask_user`、`report`，以及 `delegate` 的目标 `device != exclusive`。其余返回工具错误，`error` 为 `PLAN_APPROVAL_REQUIRED`。这个字符串要补进 04 §5，HTTP 409，见 §10。HTTP 的 approve 成功后往 Worker 收件箱写一条 `system`：「用户已批准计划 <id>」或「用户退回了计划 <id>：<comment>」，并唤醒该会话。

动态尾部 `plan` 段的文本格式与 06 §7.2 的示例一致，由 `PlanMiddleware` 在 `wrap_model` 时写入尾部，不写入历史消息。

**保留**

`kind=spill` 且 `pinned=false` 且 `created_at` 早于 30 天的行删除；文件存储时同时删 `resource/artifacts/<id>`。笔记与报告不自动删。

### 4.6 前端

工单页右侧增加两个块。工单详情里已有 `worker_session_id`（04 §2.2 的 `get_ticket`）。

- `GET /api/agent/sessions/{worker_session_id}/plan` 渲染 `PlanPanel`：标题、状态、步骤列表。状态为 `awaiting_approval` 时显示批准与退回。退回把 `approved=false` 和原因发给 `approve_plan`。
- `GET /api/agent/artifacts?ticket_id=` 渲染 `ArtifactList`：标题、种类、大小、时间。点击打开抽屉，拉 `get_artifact` 显示正文；`truncated=true` 时提供下载，走 `download_artifact`。

`agent_event` 的 `plan_updated`、`artifact_created` 使这两个查询失效。事件名加入 `web/src/realtime/events.ts` 与 `query-bridge.ts`。

批准按钮在请求未返回时禁用。退回原因空着不能提交。

### 4.7 提示词与代理定义

`common.md` 增加三句，`version` 不单列（它没有 version 字段，改动记在台账）：

```
工具结果里 spilled 为 true 时，正文只有预览。要看其余部分就 read_artifact，从 next_offset 续读。
多步工作先 update_plan。计划待批准时不要调用会改设备或会提交任务的工具。
中间结论用 write_note 记下。笔记不在上下文里，需要时按 durable.notes 的 id 去读。
```

给这些 Worker 的 `tools` 追加三个名字，顺序加在 `report` 之前：`update_plan`、`write_note`、`read_artifact`。名单：`general`、`daily_ops`、`copilot`、`planner`、`diagnose`、`analyst`、`ops`。总控与子代理不加；子代理没有 `read_artifact`，因此它们的工具输出不外置（06 §6.1）。每个被改的定义 `version` 加 1。

工具计数：M70 收口时模型可见工具数为 N（以 `tests/agent/test_tool_catalog.py` 当时的断言为准，M74 不加工具）。本里程碑模型可见工具数改为 N+3。三个新工具 `rest_visible=false`，不进 OpenAPI 的工具清单端点以外的 REST。

## 5. 实施任务

### T1 迁移与模型

- 写范围：`maa_api/db/models/artifact.py`、`maa_api/db/models/run_plan.py`、`maa_api/domain/enums.py`、`maa_api/db/migrations/versions/0029_artifacts_plans.py`、`tests/db/test_models.py`、`tests/db/test_initial_migration.py`。
- 步骤：按 §4.1。`BUSINESS_TABLES` 加 `artifact`、`run_plan`。索引名加入 `EXPECTED_INDEXES`。
- 验证：`.venv/bin/python -m pytest -q tests/db/test_models.py tests/db/test_initial_migration.py`，退出码 0。

### T2 产物服务与外置

- 写范围：`artifacts/service.py`、`loop/spill.py`、`tools/workspace.py` 的 `read_artifact` 与 `write_note`、对应测试。
- 步骤：按 §4.5。中间件插在 M74 已排好的链上，序号 10，位于截断中间件之后、审计之前记完整结果。读 `maa_api/agent/loop/__init__.py` 里现有顺序再插入，不改其他中间件的相对顺序。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_spill.py tests/agent/test_artifacts.py`，退出码 0。

### T3 计划

- 写范围：`plans/service.py`、`loop/plan.py`、`update_plan` 工具、`api/agent/plans.py`、测试。
- 步骤：状态机按 §4.5 的表。批准前的工具过滤放在 `PlanMiddleware.wrap_tool`，在策略引擎之前返回，这样不会误弹确认。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_plans.py`，退出码 0。

### T4 端点、尾部、保留

- 写范围：`api/agent/artifacts.py`、动态尾部组装、`main.py` 的保留任务、`tests/api/test_agent_workspace.py`。
- 步骤：端点按 §4.3。尾部增加 `plan` 与 `notes` 两段，没有计划或笔记时这两段省略。保留任务按 §4.5。
- 验证：`.venv/bin/python -m pytest -q tests/api/test_agent_workspace.py tests/agent/test_dynamic_tail.py`，退出码 0。

### T5 前端与定义

- 写范围：`PlanPanel.tsx`、`ArtifactList.tsx`、`TicketPage.tsx`、`events.ts`、`query-bridge.ts`、七个 Worker 与四个子代理的定义、`common.md`、`test_tool_catalog.py`。
- 步骤：按 §4.6、§4.7。组件测试：待批准时两个按钮可见；批准后状态变为进行中（用 MSW 或现有 fetch mock）。
- 验证：`cd web && pnpm typecheck && pnpm test --run src/features/tickets`。

### T6 文档计数

- 写范围：见 §9。
- 步骤：改计数，跑错误码与路由测试。
- 验证：`.venv/bin/python -m pytest -q tests/domain/test_errors.py tests/domain/test_route_inventory.py tests/agent/test_tool_catalog.py`，退出码 0。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/test_spill.py` | `test_long_output_becomes_pointer` | 指针字段、审计仍是全文 |
| 同上 | `test_short_output_unchanged` | 不建产物 |
| 同上 | `test_agent_without_read_artifact_does_not_spill` | 总控或旧定义 |
| 同上 | `test_read_artifact_pages` | offset、next_offset、末尾为 null |
| 同上 | `test_other_ticket_artifact_is_404` | `ARTIFACT_NOT_FOUND` |
| `tests/agent/test_plans.py` | `test_complex_plan_waits_for_approval` | 4 步进入 awaiting_approval |
| 同上 | `test_short_plan_activates` | 3 步直接 active |
| 同上 | `test_blocked_tool_before_approval` | `submit_pipeline` 得到 `PLAN_APPROVAL_REQUIRED` |
| 同上 | `test_status_only_edit_does_not_reapprove` | 已批准计划改状态仍 active |
| 同上 | `test_rewrite_title_reapproves` | 改标题后回到 awaiting_approval |
| 同上 | `test_reject_returns_to_draft` | `approve_plan` 传 `approved=false` 后状态为 draft |
| 同上 | `test_ticket_end_abandons_plan` | abandoned |
| `tests/api/test_agent_workspace.py` | `test_file_storage_over_256kib` | path、sha256、内容不在列里 |
| 同上 | `test_over_16mib_is_413` | `ARTIFACT_TOO_LARGE` |
| `tests/agent/test_dynamic_tail.py` | `test_plan_segment_tracks_item_status` | 文本含「进行中」 |

## 7. 验收

照 12 §4。另：

1. 脚本化用例：FakeLLM 让 Worker 调用返回 20000 字的工具，下一次模型请求的 tool 消息含 `artifact_id` 且不含那 20000 字。
2. 同一用例里 4 步计划在批准前调用 `submit_pipeline` 失败，批准后成功。
3. `cd web && pnpm gen:api:check` 通过。

## 8. 风险与回滚

- 外置若发生在审计之前，审计会丢掉全文。T2 的测试 `test_long_output_becomes_pointer` 专门锁住顺序。
- 回滚：revert 合并提交，再 `alembic downgrade -1`。`resource/artifacts/` 里的文件可留，不引用即无害。

## 9. 文档同步

- `PLAN_APPROVAL_REQUIRED` 只出现在工具结果的 `error` 字段，不进 `ErrorCode`，也不改错误码总数。HTTP 侧只用 04 已有的 `PLAN_NOT_FOUND`、`PLAN_NOT_AWAITING_APPROVAL`、`ARTIFACT_NOT_FOUND`、`ARTIFACT_TOO_LARGE`。
- 05 §3.2 的 Worker 工具行追加三个工具名。
- `tests/domain/test_errors.py` 的计数、`tests/domain/test_route_inventory.py` 的路由数、`tests/agent/test_tool_catalog.py` 的模型可见数同步。
- 表数加 2。

## 10. 停下来问

- 下一个迁移号不是 0029。
- 审计中间件的位置让你无法在替换前记下全文。
- 想把 `PLAN_APPROVAL_REQUIRED` 做成与 04 现有码不同的另一套字符串。
- 想给总控也加上 `update_plan`。
