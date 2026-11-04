# M69 工单领域模型（台账）

- 分支：`ticket-domain`；基点：`dev` = `94fc14d`；合并：`dev` 上的 `merge: 工单领域模型（M69，见 docs/v3/13 §8.28–8.29）`；tag：`v3-m69`
- spec：[m69-ticket-domain](../specs/m69-ticket-domain.md)
- 计划：[m69-ticket-domain](../plans/m69-ticket-domain.md)
- 决策记录：[docs/v3/13 §8.28–8.29](../13-决策记录.md)

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `90a54b1` | 计划入库；§0 的 11 条检查；`mandate.source` 宽度按 spec §11 裁决为 str16 |
| T1 | `50e08eb` | 七个枚举（含 `TicketStatus.is_terminal`）、`TICKET_NOT_FOUND`/`TICKET_ENDED`、`docs/v3/14` §4 两行、计数 135/134 |
| T2 | `f8f476b` | 五张表（`ticket`/`mandate`/`ticket_report`/`thread_subscription`/`agent_inbox`）与三处外键列；迁移 `0026_tickets`（batch 重建三张已有表、可逆）；`test_models.py` 登记表；迁移用例；另钉死 0023 历史列清单（13 §8.29） |
| T3 | `2441e7b` | 五个仓储 + `get_active_orchestrator`（`MandateRepository` 无更新方法）；`tests/fakes/v3_db.py`；12 条仓储用例 |
| T4 | `0e77873` | `mandates.py`/`reports.py`/`threads.py` 纯函数（`build_mandate` 校验、`policy_version`、汇报契约、话题键 10 种前缀）；13 条用例 |
| T5 | `89cb761` | `InboxService`（put/claim/drop_pending/pending_count/add_listener，双层并发保证）、`ThreadRouter`（订阅/发布/到总控）；28 条用例（并发领取连跑 20 次） |
| T6 | `ca3327a` | `TicketService` 12 方法、三个结果 dataclass、`ticket_status_payload`、`block_tickets_after_restart`；状态机与边界逐行用例（19 条） |
| T7 | `78b6c94` + `811ae77` | 三个只读端点与 8 个响应模型；`docs/v3/14` agent 小节 +3 行（137 条）；快照与 `api.d.ts` 重生成；503 错误码按 spec §4.3 用 `AGENT_DISABLED`（初版误用 `SERVICE_UNAVAILABLE`，修复提交） |
| T8 | `e42c8ac` | `ticket_status` WebSocket 频道（后端 CHANNELS + 订阅用例）、前端 `keys.ts`/`events.ts`/`query-bridge.ts` 三处与用例 |
| T9 | `a33a722` | 收件箱保留 30 天（`agent_inbox_deleted`）；`RunRecovery` 提交前把受影响工单改 blocked 并补 `forwarded=false` 汇报；lifespan 第 6 步装配三服务 + `forward_pending_reports` |
| T10 | 本台账所在提交 | 03 §6.2 改 str16；13 §8.28–8.29；`docs/v3/15` 与 `api.d.ts` 重生；README 状态行；全量验收与合并 |

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| T1 | `tests/domain` = `453 passed` |
| T2 | `tests/db` = `349 passed`（含初始迁移、可重复升级、降级到底再升级、离线渲染） |
| T3 | `test_repositories_ticket.py` = `12 passed`；`tests/db` = `361 passed` |
| T4 | `tests/agent/tickets` = `13 passed`；`policy_version()` = `1ee9713e265b`（两次一致） |
| T5 | `tests/agent/tickets` = `28 passed`；并发领取连跑 20 次全过 |
| T6 | `tests/agent/tickets` = `47 passed` |
| T7 | 路由 + 路由清单 + app skeleton = `41 passed`；`dump_openapi --check` 0；`pnpm gen:api:check` 0；137 个 operation、tags 19 |
| T8 | `tests/api/test_ws.py` = `12 passed`；前端 typecheck 0；vitest 78 文件 792 用例 |
| T9 | retention + tickets + assembly = `68 passed`；工单路由 `7 passed` |

### 最终验收（T10，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | `3 failed, 4321 passed, 5 skipped, 2 xfailed`；3 条失败与 dev 起点同一组（真资源用例），无新增 |
| `scripts/dump_openapi.py --check` | 退出码 0 |
| `scripts/generate_api_guide.py --check` | 退出码 0（T1 加错误码后先补生了 `docs/v3/15`，见 Ruling） |
| 前端五步（install / gen:api:check / typecheck / test / build） | 退出码 0；vitest 78 文件 792 用例；build 正常（含 `SERVICE WORKER OK`） |
| `scripts/frontend_smoke.py` / `scripts/agent_smoke.py` | 均 `SMOKE OK` |
| `scripts/agent_eval.py --suite regression --mode replay` | 退出码 0，末行 `EVAL OK`（6/6） |
| `scripts/check_doc_links.py` | `BROKEN 0` |
| `git diff --check` | 退出码 0 |
| 手动验收（curl 空列表、浏览器 WS 帧含 `ticket_status`） | **未验证**（本会话没有常驻服务与浏览器）；等价逻辑由 `test_tickets_router.py`（空列表形状）与 `test_ws.py`（订阅 `ticket_status` 回 `subscribed`）覆盖 |

### 计数

- 业务表 32 → **37**；错误码 133/132 → **135/134**；HTTP 端点 134 → **137**；WebSocket 频道 14 → **15**（`ticket_status`）；迁移 head `0025_trace_spans` → **`0026_tickets`**；工具 84 / 模型可见 83 不变；OpenAPI tag 19 不变。
- 新增测试文件 10 个：`tests/db/test_tickets_migration.py`、`test_repositories_ticket.py`、`tests/agent/tickets/{test_ticket_mandates,test_ticket_reports,test_thread_keys,test_inbox_service,test_thread_router,test_ticket_service,test_ticket_recovery}.py`、`tests/api/test_tickets_router.py`；新增 `tests/fakes/v3_db.py`。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | `mandate.source` 列宽按 spec §4.1/§11 取 **str16**，T10 把 03 §6.2 从 str24 改成 str16（13 §8.28） | 列宽测试把所有 `source` 列钉死 16 位；`EventSource` 最长 11 字符；spec 明确申请 |
| R2 | T2 顺带钉死 `AGENT_RUN_0023_COLUMNS` 历史列清单（13 §8.29） | 0026 给 `agent_run` 加 `ticket_id` 后，0023 历史迁移用例原实现会随当前 metadata 漂移；断言语义不变 |
| R3 | 工单端点 503 错误码按 spec §4.3 用 `AGENT_DISABLED`，修正 T7 初版的 `SERVICE_UNAVAILABLE` | spec 逐端点列出了错误码；初版是我给实现子代理的口径，验收时按 spec 校正 |
| R4 | `TicketService.detail()` 假定工单必有委托（create 保证），T7 测试用 `service.create` 造数 | 避免 seed 出无委托的非法工单；M70 起所有工单都由 `create` 产生 |
| R5 | M68 收尾顺带发现的两处全局状态问题在 M69 验收前已修：`generate_api_guide` 未重生、`api.d.ts` 未随 503 错误码重生 | 两者都在 T10 的验收里暴露并补生；不改变产物语义 |
| R6 | 手动验收两项（常驻服务 curl、浏览器 WS 帧）记「未验证」 | 本会话无常驻服务与浏览器；对应逻辑已由自动化用例覆盖 |

## 偏离（已记 13 §8.28–8.29）

- §8.28 `mandate.source` 列宽收窄为 str16（03 §6.2 同步）。
- §8.29 0023 历史迁移用例钉死 `agent_run` 列清单。

## 遗留

- 手动验收两项未做（见上）；M70 起总控/Worker 会用这些服务，联调时再在真实服务上过一遍 curl 与 WS 帧。
- 工单页面与写端点（发消息、中断、取消）归 M70/M72；设备租约字段 `TicketDetail.lease` 归 M71；事件表与 `source_trigger_id` 归 M81。
- 收件箱转消息（`inbox` 项的 system/user 映射）是 M70 的消费方，本里程碑只保证数据与服务正确。
- 单测里的 Service 均注入构造依赖；M70 装配真实总控/Worker 时需按 02 §5 顺序复用 T9 的装配点。
