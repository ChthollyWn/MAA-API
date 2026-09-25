# M11 Task 5：确认、审计与 REST API

## 交付

- 增加 Agent REST 路由：工具清单与 schema、sync/async invoke、会话分页/创建/详情/删除、会话消息只读分页、原子授权撤销、审计过滤列表/详情。没有挂载 `POST /api/agent/sessions/{id}/messages`。
- 增加确认列表/详情/批准或拒绝路由。REST sync 与内置 Agent 等到确认和执行完成，REST async 立即返回 202；MCP 通过独立 helper 最多短等 25 秒。批准后的确认详情回查执行结果、错误码与 `audit_id`；批准动作失败后仍广播一次终态事件。
- 为确认服务增加启动清理、10 秒到期扫描、关闭时 worker 收尾；重启时将全部 pending 确认置为 expired、同步审计终态并清除持久化授权。批准确认但 audit 仍 pending、以及普通 async 调用的 pending audit 均标为 `FAILED / SERVICE_UNAVAILABLE` 并说明结果不确定，防止重启后重复执行。授权授予/撤销/过期通过 `agent_event` 的 `atomic_grant_changed` envelope 广播。授权撤销拒绝仍待审批的会话 grant 请求。
- `check_confirmation` 加入工具 registry，用于查询确认与执行详情，不执行持久化 payload。
- Agent REST 审计保存当前 `request_id`；设置增加确认超时（默认 600 秒）、grant 确认超时（默认 120 秒）与授权窗口（默认 15 分钟、1–60 分钟）。
- 新增 `0008_agent_invoke_idempotency` 与 `agent_idempotency` 仓储。REST `Idempotency-Key` 长度限制为 64，记录 24 小时有效，按 `(caller, key)` 唯一；相同请求重放首次响应，不同哈希返回 `409 IDEMPOTENCY_KEY_CONFLICT`。同进程同 key 请求串行化，数据库唯一约束作为兜底。request id 进 `agent_audit`，幂等状态不放进审计表。
- 更新 docs/04、docs/05、docs/13 §8、OpenAPI 快照与前端生成类型。

## TDD 记录

实现前的失败测试先验证了缺失契约：

- `expire_all_pending` 与会话/消息分页测试以仓储方法缺失失败；补方法后相同用例通过。
- Agent 设置测试因 `Settings.agent` 缺失而失败；加入 AgentSettings 和 schema 元数据后通过。
- REST 合同测试因 Agent router 未注册而失败；接入 router、main lifespan 后通过。
- 工具清单测试因 `check_confirmation` 未注册而失败；注册共享服务 helper 后通过。
- 批准后执行失败的终态事件测试先因缺少 `confirm_resolved` 广播失败；worker 改为在审计终态落库后广播一次后通过。
- 已批准 orphan confirmation 恢复测试先因 helper 不接受调用上下文而失败；增加持久 payload 恢复路径并传递上下文后通过。
- 工具清单风险过滤测试先观察到 `risk_level=destructive` 返回 0 项；将静态 ToolRisk 映射到 none/consume/destructive 并支持旧标签别名后通过。
- Request Changes audit-link 顺序测试先因 `ConfirmationService.invoke()` 未接受 `on_audit_created` 而失败；加入调用 handler 前持久 audit id 的回调后通过。
- Review Changes 修复的 RED/GREEN：CAS 输家测试先观察到未发事件但持久行未通过 fake 并发提交切到 winner 状态；修正测试竞争注入后确认代码仅读取 winner 状态且不改审计/广播。启动批准孤儿/关停 pending 审计测试先观察到 pending 状态残留；启动/关停收尾改成 fail closed 后通过。空白 key 测试先观察到 `"   "` 绕过幂等保护；返回 `INVALID_PARAMETER` 后通过。audit-link 回调测试先观察到无预约清理方法；启动清理无 `audit_id` 预约后通过。审计链接回调顺序测试验证 audit id 落库后才运行 handler；并发 loser 测试验证等待超时后明确返回 `202 in_progress` 与已知 audit id。

## 验证

最终聚焦命令：

```text
.venv/bin/python -m pytest -q -o addopts='' tests/agent/test_confirmation_service.py tests/agent/test_tool_catalog.py tests/api/test_agent_router.py tests/api/test_app_skeleton.py tests/db/test_repositories_agent_audit.py tests/db/test_migrate.py tests/test_settings.py tests/agent/test_pipeline_runner_set_params.py tests/agent/test_device_raw_tools.py
125 passed, 1 warning in 4.21s
```

唯一 warning 是现有 Starlette 对 `httpx` TestClient 的弃用提示。

其他检查：

```text
.venv/bin/python -m compileall -q <Task 5 Python paths>   # exit 0
git diff --check                                           # exit 0
.venv/bin/python scripts/dump_openapi.py --check           # OpenAPI snapshot is current
cd web && pnpm gen:api:check                                # OpenAPI types are up to date
```

运行时核 / 真机检查未作为硬性验证；Task 5 测试全部使用临时 SQLite 与 fake handlers。

## Rulings I made

- `risk_level` 工具清单标注采用 PolicyDecision 的 none/consume/destructive 档位；同时保留 `risk` 静态 ToolRisk 字段，过滤器接受 safe/conditional/dangerous 别名。
- invoke 幂等哈希覆盖工具名、完整 arguments 与 mode；同一 key 的不同执行模式视为不同请求。
- 为保证带副作用工具的 at-most-once，服务启动或关闭时不会重放任何 pending audit。待确认记录置为 expired、其关联审计同步置为 expired；已批准但 audit 仍 pending、以及无确认的 async audit 标为 `FAILED / SERVICE_UNAVAILABLE` 并说明执行状态不确定；`check_confirmation` 只读终态结果。授权记录不会在重启后恢复，孤立 idempotency reservation（尚未链接审计）会清理。幂等 audit id 在 invoke service 回调中、handler 前持久化；并发 key loser 有界等待审计关联/原响应，仍在处理时返回 `202 in_progress` 与可重试提示。

## 评审后文档补齐

Task 2 最终集成评审发现 docs/06 已引用结构化统计表，但 docs/04 尚无 `stage_drop` / `sanity_observation` 表结构与索引说明。按评审意见补齐 docs/04 §5.3a 与 §6 索引行，并在 docs/13 §8.2 记录 migration 0007 的既有实现契约与 Task 2 验证证据；没有更改 Task 2 代码。

## Request Changes 修复轮

- expiry POST 竞争输家只根据数据库 CAS 后的真值返回，不更新审计、不广播、不唤醒。
- 为避免 side effect 与终态审计提交之间进程崩溃后重复执行，`reset_after_restart` 对所有 pending audit fail closed；批准确认若审计仍 pending，也标记为执行结果不确定的失败，`check_confirmation` 纯读取。
- 关闭时先取消 worker，再将未决确认过期、将其他 pending audit 标为执行结果不确定的失败。幂等 reservation 在副作用前由 invoke service callback 持久化 audit_id；启动时清理无 audit_id 的预约。
- 空白 `Idempotency-Key` 返回 `INVALID_PARAMETER`；数据库唯一冲突输家轮询有限次数，仍处理中返回显式 `202 in_progress` 和 audit_id（若已关联）。
- RED/GREEN 命令与结果见本修复轮交付摘要。

## Final review fix：重启后幂等响应收敛

Task 5 定向终审发现：审计 id 已经链接到幂等记录、但首次响应快照尚未提交时进程重启，原实现会在 24 小时内一直返回 `202 in_progress`。修复如下：

- migration `0009_agent_idempotency_mode` 持久化首次调用 `sync` / `async` 模式；旧行可为空。
- async 首次响应（直接受理或等待确认的 202）由 `on_audit_created` 在创建 worker 之前写入幂等记录。
- sync 完整成功/错误响应通过 `on_audit_terminal` 与审计终态在同一 SQLAlchemy session 事务落库。
- 拒绝、过期、会话授权撤销、服务启动/关闭时的 fail-closed 终态都收敛相关同步幂等响应。启动时对已关联但无快照的旧行：async 根据审计/确认重建首次 202；sync 依据终态恢复错误响应；完整成功摘要不可用或历史行缺少 mode 时返回稳定的 `SERVICE_UNAVAILABLE` 与 `audit_id`，不伪造完整结果，也不重放副作用。
- 验证覆盖 sync success/error 的相同响应重放及 handler 只执行一次；启动恢复 confirmed async 202、拒绝/不确定错误、截断结果与 legacy mode 缺失；migration backup 保留 0008 schema、升级后出现 `request_mode`。
- 终审后追加用例注入冗余首次响应写失败，证明成功审计已有快照时不会删除链接预约、同 key 重试不会再次运行 handler。主 agent 定向回归：`.venv/bin/python -m pytest -q -o addopts= tests/agent/test_confirmation_service.py tests/agent/test_registry_policy.py tests/agent/test_resource_ops_schedule_tools.py tests/agent/test_device_raw_tools.py tests/api/test_agent_router.py tests/db/test_models.py tests/db/test_migrate.py tests/db/test_repositories_agent_audit.py` — **198 passed**，随后全套 pytest 重跑退出码 0。
