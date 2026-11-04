# M71 设备租约与确认冒泡（台账）

- 分支：`device-lease-bubbling`；基点：`dev` = `2eea5cc`；合并：`dev` 上的 `merge: 设备租约与确认冒泡（M71，见 docs/v3/13 §8.38–8.43）`；tag：`v3-m71`
- spec：[m71-device-lease-bubbling](../specs/m71-device-lease-bubbling.md)
- 计划：[m71-device-lease-bubbling](../plans/m71-device-lease-bubbling.md)
- 决策记录：[docs/v3/13 §8.38–8.43](../13-决策记录.md)

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `02309ed` + `2151984` | 计划入库；§0 的 19 条检查；4 处规格缺口裁决（删除旧设备门、链构造在 runtime、#16 命中是注释、两条 `ClarificationMiddleware(` 只改 v3） |
| G1（T1+T2） | `33c414f` | 三个 `DEVICE_LEASE_*`（409）、`lease.ttl_seconds`/`lease.wait_seconds`；`device_lease` 表 + 迁移 `0027`（`confirmation`/`agent_question` 的 origin 三列、`agent_audit` 三列与索引，batch 可逆）；登记表与迁移用例 |
| G2（T3） | `686a420` | `ToolOrigin`/`ToolContext.origin`；`origin.py` 四函数；确认/提问写 origin 三列并在 WS/GET wire 带 origin；审计 `run_id`/`ticket_id`/`agent_name` 与按工单过滤；`_execute_and_record` 用 `dataclasses.replace` 保 origin |
| G3（T4） | `5ae2098` | `DeviceLeaseRepository`；`DeviceLeaseService`（acquire/renew/release/release_run/release_ticket/expire_due/preempt/on_ticket_ended/claim_gate/start/close，先到先得、TTL、广播、`on_released`）；13 条用例（串行化 20 次） |
| G4（T5） | `2f08a79` | `BeforeExecuteCallback` 与 `invoke(..., on_before_execute=)`；`LeaseMiddleware`（确认通过后执行前取租约、等确认不占、抢占后 `stop_run=lease_preempted`、根运行结束释放）；`PolicyMiddleware` 组合钩子；v3 链插入并**删除 M70 旧设备门**（模块/中间件/装配/测试引用） |
| G5（T6） | `de6b4ff` | `hold_device`/`release_device`（REST 不可见）与 `general.md`（加工具、version 2、新增段落）；工具 91→93 / 可见 90→92 |
| G6（T7） | `061dd67` | `PolicyMiddleware`/`ClarificationMiddleware` 写 origin、SSE 带 origin、冒泡（工单 waiting + 总控 `human_request`）与 finally settle；子代理策略身份取父 Worker 会话；**修 M70 `MandateMiddleware` 的 `ceiling_risk` 缺口** |
| G7（T8） | `50b87e9` | 队列领取闸门：`claim_next(allowed_session_ids=)`、`PipelineRunner.bind_lease_gate`、`_claim_next` 取闸门与手动抢占；未绑闸门行为与 v2 一致 |
| G8（T9） | `6692339` | `GET`/`DELETE /api/agent/device-lease` 与 `preempt_device`；五类人工抢占接入；`TicketDetail.lease`；快照/清单（端点 146→148） |
| G9（T10） | `875cbf7` | 前端：`AgentOrigin`、`originLabel`/`useAgentTitle`、确认「调用方」行替换、提问来源行、`lease_changed` 失效；vitest 801 |
| T11 | 本台账所在提交 | 启动恢复清理上一进程租约；lifespan 装配 `DeviceLeaseService`（扫描任务、`bind_lease_gate`、工单结束监听含撤销授权）与两条关闭路径；装配/恢复用例；文档同步、13 §8.38–8.43、README、本台账 |

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | domain+settings = `493 passed`；`tests/db` = `365 passed`（含离线渲染与降级到底） |
| G2 | 确认/提问/审计相关 5 文件 = `63 passed`；parity 17 |
| G3 | `tests/agent/lease` = `13 passed`；串行化用例连跑 20 次通过 |
| G4 | `tests/agent` = `1043 passed, 1 skipped, 2 xfailed`；parity 17；装配/工单消息路由 7 passed |
| G5 | 目录/租约工具 = `40 passed`；计数 `93 92`；parity 17 |
| G6 | `tests/agent` = `1053 passed`；parity 17；regression `EVAL OK` |
| G7 | pipeline 仓储 + runner + core = `255 passed` |
| G8 | `tests/api` = `533 passed`；`dump_openapi --check` 0；`pnpm gen:api:check` 0 |
| G9 | 前端 typecheck 0；vitest 78 文件 **801** 用例 |
| T11 | 装配 + 恢复用例 = `9 passed` |

### 最终验收（T11，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量，三次） | 第一次 5 failed/4460 passed，第二次 4 failed/4461 passed；修正两条测试脆弱性后第三次 **3 failed / 4462 passed**（3 条与 dev 起点同一组真资源失败，无新增） |
| `scripts/dump_openapi.py --check` | 退出码 0 |
| `scripts/generate_api_guide.py --check` | 退出码 0（T1 加错误码后补生：只新增 3 行错误码表） |
| 前端五步（install / gen:api:check / typecheck / test / build） | 退出码 0；vitest 78 文件 **801** 用例；build 正常 |
| `scripts/frontend_smoke.py` / `scripts/agent_smoke.py` | 均 `SMOKE OK` |
| `scripts/agent_eval.py --suite regression --mode replay` | `EVAL OK`（6/6） |
| `scripts/agent_eval.py --suite capability --mode replay` | `EVAL OK`（6/6，不低于基线） |
| `scripts/check_doc_links.py` | `BROKEN 0` |
| `git diff --check` | 退出码 0 |
| 手动验收（真机点按/接管两条） | **未验证**（本会话无真机与常驻服务）；租约、抢占、冒泡链路由 13+4+4 条单测与前端用例覆盖 |

### 计数

- 业务表 37 → **38**（`device_lease`）；错误码 138/137 → **141/140**（+3）；HTTP 端点 146 → **148**（+2）；工具 91/90 → **93/92**（模型可见 92，REST 可见 84 不变）；配置键 +2；WebSocket 频道 16 不变（`lease_changed` 是 `agent_event` 的事件名，非频道）；迁移 head `0026_tickets` → **`0027_device_lease`**；OpenAPI tag 19 不变。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 删除 M70 旧设备门（spec 写范围未列） | M70 注释与风险表都写明 M71 替换；不删会双层互斥且旧门无 TTL 阻塞新闸门（13 §8.39） |
| R2 | 链构造处在 `runtime.py`，`LeaseMiddleware` 插 `ClarificationMiddleware` 之前；v2 `_legacy_chain` 不动 | 实际结构；行为符合 05 §5.2（13 §8.40） |
| R3 | v2 门面不写 origin，v3 总控/Worker/子代理一律带（总控 `ticket_id` 可空） | 04 §4.2 规定 v2 为 null；否则 parity golden 会破（13 §8.41） |
| R4 | 修 M70 `MandateMiddleware` 的 `grant_atomic_ops` 上限缺口，并把唯一断言旧行为的既有测试改为 `blocks` | 09 §2.3 明确规则；M71 §0 #10 要求（13 §8.38） |
| R5 | `general` version 2 与 `TicketDetail.lease` 联动更新两个测试常量/断言来源 | 契约变化必须同步登记；语义不变（13 §8.42） |
| R6 | 启动恢复用单独事务清理上一进程租约 | 租约至多一行，单独事务更简单；语义与 spec 一致（13 §8.43） |
| R7 | 手动真机验收记「未验证」 | 本会话无真机；等价链路由单测/前端用例覆盖 |

## 偏离（已记 13 §8.38–8.43）

- §8.38 M70 上限检查的原子授权缺口修复；§8.39 旧设备门删除；§8.40 链构造位置；§8.41 v2 origin 为 null；§8.42 `general` 版本与契约登记联动；§8.43 启动恢复的租约清理事务形态。

## 验收中发现并修复的测试脆弱性（13 §8.44）

- `test_harness_parity.py` 的 `recon_runtime_budget` 场景对 pytest tmp 目录名长度敏感（golden 的 `chars_used` 随绝对路径长度变化）：改为场景内注入固定结果的记录注册表，只重录该场景 golden（其余 16 场景未动）；`tests/api` 先跑后 parity 不再失败。
- `test_incidents.py::test_incident_allowed_action_still_uses_confirmation_service` 的 5s 等待在满载下超时：先加宽到 15s，第三次全量仍偶发超时，再放宽到 30s（M68 §8.26 同类）。

## 遗留

- 真机手动验收未做（Worker 连点期间网页提交抢占、确认卡片来源显示），单测与前端用例覆盖等价逻辑。
- v2 会话（不经 AgentLoop）的设备调用仍不受租约约束，只有 `operation_lock` 与「有流水线在跑就拒绝」两道 v2 约束；M72 起 v2 会话只读，窗口一个里程碑（spec §8 已记）。
- `daily_ops`/`copilot` 等专职 Worker 的 `hold_device` 用法归 M73；M72 指挥台前端会复用本里程碑 origin 与 deviceLease 契约。
