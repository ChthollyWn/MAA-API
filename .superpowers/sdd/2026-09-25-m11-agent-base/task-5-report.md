# M11 Task 5：确认、审计与 REST API

## 交付

- 增加 Agent REST 路由：工具清单与 schema、sync/async invoke、会话分页/创建/详情/删除、会话消息只读分页、原子授权撤销、审计过滤列表/详情。没有挂载 `POST /api/agent/sessions/{id}/messages`。
- 增加确认列表/详情/批准或拒绝路由。REST sync 与内置 Agent 等到确认和执行完成，REST async 立即返回 202；MCP 通过独立 helper 最多短等 25 秒。批准后的确认详情回查执行结果、错误码与 `audit_id`；批准动作失败后仍广播一次终态事件。
- 为确认服务增加启动清理、10 秒到期扫描、关闭时 worker 收尾；重启时将全部 pending 确认置为 expired、同步审计终态并清除持久化授权。授权授予/撤销/过期通过 `agent_event` 的 `atomic_grant_changed` envelope 广播。授权撤销拒绝仍待审批的会话 grant 请求。
- `check_confirmation` 加入工具 registry，能查询确认详情，并在发现已批准但审计仍 pending 的孤立调用时用持久化 payload 恢复执行。
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

## 验证

最终聚焦命令：

```text
.venv/bin/python -m pytest -q -o addopts='' tests/agent/test_confirmation_service.py tests/agent/test_tool_catalog.py tests/api/test_agent_router.py tests/api/test_app_skeleton.py tests/db/test_repositories_agent_audit.py tests/db/test_migrate.py tests/test_settings.py
91 passed, 1 warning in 3.57s
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
- 重启时 approved 但 audit 仍 pending 的确认保留为可由 `check_confirmation` 恢复；pending 确认统一失效。
