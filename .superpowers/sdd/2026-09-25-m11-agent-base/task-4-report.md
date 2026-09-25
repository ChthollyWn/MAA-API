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
