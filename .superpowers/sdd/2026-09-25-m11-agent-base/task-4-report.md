# Task 4 自审报告：资源、运维与定时任务工具

## 实现

- 新增 `resource.register_tools(registry)`：提供 Copilot 列表/上传、基建方案覆盖、自定义 task 列表/注册/删除。Copilot 上传校验 `stage_name`、`actions` 与干员结构；没有注册 `run_copilot`。resource 组为文档清单的 6 项。
- 完成 `ResourceService`：通过 `ResourceAssetRepository` 持久化；资源小于等于 64 KiB 内联，大资源按 `resource/` 相对路径存盘，单资源上限 2 MiB。基建方案写到独立 custom 增量层并返回可用于 `Infrast.filename` 的路径。
- 自定义 task 校验 JSON 对象结构与 `Custom_` 前缀，聚合写入 `resource/maa-layers/custom/resource/tasks.json`。资源 reload 由构造参数注入；写入或 reload 失败时删除新记录、精确恢复旧文件字节并尽力重载旧资源。移除 task 的 reload 失败会恢复记录和旧文件。
- 新增 `ops.register_tools(registry)`：检查更新并通过 `UpdateService` 提交 core/resource/game 更新；`restart_core` 委托 `AgentOpsService`。
- 新增 `AgentOpsService`：与 `UpdateService._lock` 互斥，等待流水线和队列空闲后暂停队列，在 `CoreSupervisor.acquire_maintenance()` 内重启，按需重连；无论成功或失败，恢复本服务暂停的队列并释放互斥锁。不直接调用 `CoreClient`。
- 新增 `schedule.register_tools(registry)`：列出、新建、修改、删除 schedule。创建/修改沿用 PolicyEngine 对模板消耗参数的确认策略；删除使用 SAFE 策略。

## RED / GREEN 实际记录

首次工具组与资源服务 RED 命令：

```text
.venv/bin/python -m pytest -q -o addopts= tests/agent/test_resource_ops_schedule_tools.py tests/services/test_agent_resource_service.py
10 failed in 0.13s
```

失败均为预期契约缺失：resource/ops/schedule 工具模块尚不存在，`ResourceService` 尚未实现。

AgentOps RED 命令：

```text
.venv/bin/python -m pytest -q -o addopts= tests/services/test_agent_ops_service.py
3 failed in 0.03s
```

失败原因为 `AgentOpsService` 模块缺失。

最终聚焦与相邻契约回归 GREEN 命令：

```text
.venv/bin/python -m pytest -q -o addopts= tests/agent/test_resource_ops_schedule_tools.py tests/services/test_agent_resource_service.py tests/services/test_agent_ops_service.py tests/agent/test_registry_policy.py tests/services/test_update_service.py tests/services/test_schedule_service.py
81 passed in 1.14s
```

资源服务与重启协调测试再次独立运行：

```text
.venv/bin/python -m pytest -q -o addopts= tests/services/test_agent_resource_service.py tests/services/test_agent_ops_service.py
8 passed in 0.13s
```

Python 编译检查和 `git diff --check` 均通过。

## 自审

- 本任务注册 6 个 resource、5 个 ops、4 个 schedule 工具。按 docs/11 §3.2 全部工具共 41 项；扣除已暂缓的 `run_copilot` 后为 40 项。没有公开文档清单外的 `list_infrast_plans` 工具。
- Copilot 作业只校验、保存、列表；没有普通 Fight 代替执行器。
- 自定义 task 的写入、失败回滚与重载均通过 ResourceService 注入接口；本任务没有改 `main.py`，需由 Task 5 lifespan 注入 ResourceService 及 MaaCore 资源 reload callback。
- 本任务没有修改 Task 5 的审计仓储、确认服务、工具目录装配或其他未授权文件；没有修改文档、前端、OpenAPI 或迁移。

## 提交

- Task 4 实现提交 SHA：`35965c7193f2a578f118d069ff4ffddf8b0e0f48`。

## Scoped review fix wave

### 修正

- `update_core` 的 schema 与参数校验现只接受 `stable`，符合 README 已确认的不暴露 beta/alpha 决策。
- Copilot 校验覆盖普通作业与 SSS 作业的必需字段、干员/分组、部署坐标/方向、策略与 stage 结构，并拒绝未知顶层字段及错误嵌套类型。
- 自定义 task 校验按本地 `tasks.json` 字段结构检查标量、数组、坐标与嵌套 JSON；支持 MAA 现有继承型空对象定义、`colorScales` 的数值与区间两种形态。基建方案校验房间、设施、无人机与旧版方案字段。
- Copilot、自定义 task、基建方案非法结构都通过对应领域错误码拒绝。
- `ResourceService` 使用服务级异步锁串行化自定义 task 的读改写、资源 reload 与失败回滚，避免并行的失败操作覆盖已成功变更。
- `AgentOpsService` 在 await 队列 pause 之前记录暂停所有权；pause 抛错或调用取消时，外层 `finally` 会恢复队列。

### RED / GREEN 实际命令

RED：

```text
.venv/bin/python -m pytest -q -o addopts= tests/agent/test_resource_ops_schedule_tools.py::test_update_core_schema_exposes_only_the_confirmed_stable_channel tests/services/test_agent_resource_service.py tests/services/test_agent_ops_service.py -k 'stable_channel or nested_schema or wrong_core_field or invalid_nested_room or concurrent_custom_task or owned_pause'
14 failed, 3 passed, 8 deselected
```

失败对应 beta 仍出现在 schema、错误嵌套结构被接受、reload 并发回滚抹掉成功文件、以及 pause 抛错/取消后队列未恢复。

最终 Task 4 GREEN：

```text
.venv/bin/python -m pytest -q -o addopts= tests/agent/test_resource_ops_schedule_tools.py tests/services/test_agent_resource_service.py tests/services/test_agent_ops_service.py
32 passed in 0.44s
```

编译与差异检查：

```text
.venv/bin/python -m compileall -q maa_api/agent/tools/ops.py maa_api/services/resource_service.py maa_api/services/agent_ops_service.py tests/agent/test_resource_ops_schedule_tools.py tests/services/test_agent_resource_service.py tests/services/test_agent_ops_service.py
git diff --check -- maa_api/agent/tools/ops.py maa_api/services/resource_service.py maa_api/services/agent_ops_service.py tests/agent/test_resource_ops_schedule_tools.py tests/services/test_agent_ops_service.py tests/services/test_agent_resource_service.py
两项均退出码 0
```

用资源仓库现有 JSON 做额外结构检查：Copilot 接受 **74/76**、基建方案 **21/21**、内核 tasks.json 中的对象定义 **3659/3659**。两份被拒绝的 Copilot 文件包含无效方向字符串：`SSS_日达诺夫园区_圣聆初雪+遥+斩业星熊_可充能督战音响.json` 的 `Dowm`，以及 `SSS_玉门市集_YumenMarket_1.json` 的 `Right'`。

较宽回归命令 `.venv/bin/python -m pytest -q -o addopts= tests/agent tests/services/test_update_service.py tests/services/test_schedule_service.py tests/services/test_agent_resource_service.py tests/services/test_agent_ops_service.py` 得到 **162 passed, 1 failed**。唯一失败位于并行 Task 5 文件 `tests/agent/test_confirmation_service.py::test_expiry_cas_loser_does_not_overwrite_or_broadcast_over_concurrent_approval`，该确认 CAS 测试预期 `approved`、实际仍为 `pending`；本轮未改该测试或确认服务。

### 本轮提交

- 待提交后补录。
