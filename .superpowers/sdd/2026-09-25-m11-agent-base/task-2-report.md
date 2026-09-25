# M11 Task 2：状态与流水线工具 TDD 报告

## 范围与交付

- 新增 status / pipeline 两组 Agent 工具，各自提供 register_tools(registry)，handler 接收已验证的 Pydantic 参数与 ToolContext，返回 JSON 可编码字典。
- 状态组覆盖系统状态、截图、版本、日志、关卡解析和历史掉落/理智统计；流水线组覆盖任务类型、详情、历史、队列、提交、停止、运行中参数修改和取消排队任务。
- 新增 callback 统计模型与迁移：StageDrops 明细、SanityBeforeStage 理智采样在对应展示日志同一事务持久化；统计由结构化表按关卡和时间范围查询。
- set_task_params 复用任务 Pydantic schema 与 RUNTIME_IMMUTABLE；MaaCore 接受更新后才持久化，已结束任务不会被仓储更新。
- 关卡解析通过显式 service adapter 优先使用内核映射，再回退 stages.json。
- 没有注册或实现 run_copilot。未修改 registry.py、policy.py、REST router、前端、OpenAPI 或文档。

## RED 证据

首轮测试先于生产实现编写，执行：

    .venv/bin/python -m pytest -q tests/agent/test_status_tools.py tests/agent/test_pipeline_tools.py tests/agent/test_pipeline_runner_set_params.py tests/services/test_callback_statistics.py

退出码为 1。测试以预期缺失行为失败：状态与流水线工具模块尚不存在、PipelineRunner.set_task_params 尚不存在、结构化 callback 表尚未注册。摘要输出：

    22 failed
    EXIT_CODE=1

后续逐项补充的行为测试也分别先见 RED，再实现：

| 行为 | RED 命令与观察结果 |
|---|---|
| 关卡 code/stageId、本地回退、内核优先 | .venv/bin/python -m pytest -q tests/services/test_stage_resolver.py：3 项失败，提示 stage_resolver 模块缺失，退出码 1。 |
| 仅持久化 SubTaskExtraInfo 消息中的目标 callback | .venv/bin/python -m pytest -q tests/services/test_callback_statistics.py::test_log_flush_persists_structured_callback_data_and_queries_by_stage_and_time：失败；错误消息也被统计，汇总为 3 runs、515 个龙门币而非 2 runs、15 个，退出码 1。 |
| DrGrandet 字段别名更新 | .venv/bin/python -m pytest -q tests/agent/test_pipeline_runner_set_params.py::test_runtime_update_normalizes_field_aliases_without_preserving_stale_value：NameError，缺少别名归一实现，退出码 1。 |
| 拒绝 null 参数更新 | .venv/bin/python -m pytest -q tests/agent/test_pipeline_runner_set_params.py::test_runtime_update_rejects_null_patch_values_before_core：核心收到 {"times": null}，预期 AppError 未抛出，退出码 1。 |
| 终态任务仓储拒绝更新 | .venv/bin/python -m pytest -q tests/agent/test_pipeline_runner_set_params.py::test_task_repository_does_not_change_completed_task_params：仓储返回 True 而不是 False，退出码 1。 |

原始完整输出分别保存在 /tmp/maa-m11-task2-red.log、/tmp/maa-m11-task2-stage-resolver-red.log、/tmp/maa-m11-task2-msg-red.log、/tmp/maa-m11-task2-alias-red.log、/tmp/maa-m11-task2-null-red2.log、/tmp/maa-m11-task2-taskrepo-red.log。

## GREEN 与回归验证

最终执行的相关回归命令：

    .venv/bin/python -m pytest -o addopts='' -q tests/agent/test_status_tools.py tests/agent/test_pipeline_tools.py tests/agent/test_pipeline_runner_set_params.py tests/agent/test_registry_policy.py tests/services/test_callback_statistics.py tests/services/test_stage_resolver.py tests/services/test_callback_translator.py tests/services/test_log_hub.py tests/db/test_models.py tests/db/test_initial_migration.py tests/db/test_migrate.py tests/db/test_repositories_pipeline.py tests/core/test_core_pipeline.py tests/domain/test_task_models.py

退出码 0，实际摘要：

    425 passed in 5.86s
    EXIT_CODE=0

此外 .venv/bin/python -m compileall -q 覆盖所有 Task 2 新增/修改 Python 模块，退出码 0；git diff --check 退出码 0；.venv/bin/alembic heads 输出 0007_agent_callback_statistics (head)。尝试运行 Ruff 时 .venv 未安装 Ruff（No module named ruff），因此未能执行 Ruff 检查。

完整 GREEN 输出：/tmp/maa-m11-task2-final-count.log。

## 自审

- callback 解析依据原始 msg 和嵌套结构字段，不解析 LogEntry.content；畸形统计、无关 callback、错误 message 不进入结构化统计表。
- 日志和统计行通过单个 LogRepository.bulk_insert 事务提交，避免展示事件与结构记录只写入一方。
- 时间统一为 UTC naive DB 值，Agent 输出时间字段为 ISO 格式；统计过滤使用闭区间，并分别验证关卡和时间边界。
- 队列提交明确使用 PipelineSource.AGENT，优先级仍由 QueueService 按现有规则裁定；停止调用 PipelineRunner.request_cancel，仅取消 pending 队列项调用 cancel_pending。
- 热参数先过任务 schema 和运行时不可变字段检查，null/无字段/name 更新被拒绝，核心拒绝时不写库；仓储也以 status=running 条件更新防止终态竞态。
- SQLite 迁移包含两个结构化表及必要复合索引、外键 SET NULL，降级顺序与依赖相反；迁移测试覆盖 upgrade、downgrade、备份前后表结构。
- 工作区有其他任务的并行改动；提交仅包含上述 22 个 Task 2 文件。未暂存的 Task 1/3/4 改动保留。

## 提交

- 提交：8b68fd05fdee6735151195d0f15f439d2a34c5ae — feat: add M11 status and pipeline agent tools
- 实现提交前的自审和 git diff --check 均通过；本报告随后写入本路径。

## Reviewer follow-up：部分参数更新与交叉字段

审查发现 Roguelike 的 partial patch 被额外单独实例化校验：既有 `theme=Sami` 时，只更新 `mode=5` 却被当成默认 `theme=Phantom` 拒绝。修复后只对「持久化参数 + patch」合并态执行完整 Pydantic 校验（保留交叉字段、类型、约束与 extra=forbid）；再从该已验证实例仅导出本次 patch 字段发给内核，既不对 patch-only 执行交叉字段校验，也不把旧字段重复发送。`RUNTIME_IMMUTABLE` 的预检以及提交和 set_task_params 的消耗风险输入形状未变。

### RED

新增真实临时 SQLite、RUNNING Roguelike Task、活动 attempt 与 Core ack fixture 测试，初始记录为 `theme=Sami, mode=1` 并 patch `mode=5`。先运行：

    .venv/bin/python -m pytest -q tests/agent/test_pipeline_runner_set_params.py::test_runtime_roguelike_patch_validates_cross_fields_against_persisted_state

退出码 1，按预期收到 `TASK_PARAM_INVALID: Roguelike.mode=5 仅适用于 Sami 主题（当前 theme="Phantom"）`；失败栈指向对 patch-only 的 `model.model_validate`。

原始输出：`/tmp/maa-m11-task2-cross-field-red.log`。

### GREEN 与回归

部分更新测试全组 GREEN：

    .venv/bin/python -m pytest -q tests/agent/test_pipeline_runner_set_params.py

实际输出 `........ [100%]`，退出码 0。

另外执行覆盖流水线 / 状态工具、策略、结构化回调、关卡解析、核心流水线和任务 schema 的相关回归：

    .venv/bin/python -m pytest -o addopts='' -q tests/agent/test_pipeline_runner_set_params.py tests/agent/test_pipeline_tools.py tests/agent/test_registry_policy.py tests/agent/test_status_tools.py tests/services/test_callback_statistics.py tests/services/test_stage_resolver.py tests/core/test_core_pipeline.py tests/domain/test_task_models.py

实际输出 `232 passed in 3.89s`，退出码 0。日志为 `/tmp/maa-m11-task2-review-focused.log`。

我也重跑了原来的 DB inventory 一揽子命令。它在 Task 5 已并入 `agent_idempotency` 模型后报告 424 passed、4 failed；失败都来自 `tests/db/test_models.py` 对任务表/index/unique/JSON 总清单尚未包含 Task 5 新表。按 reviewer 明确的范围，我没有改 Task 5 的模型/测试文件。

Reviewer follow-up 修复尚待单独提交；此次只涉及 `pipeline_runner.py`、本测试文件与本报告。
