# 计划批准上指挥台（维护性补丁台账）

- 分支：`console-plan-approval`；基点：`dev` = `175401e`；合并：`d4cc796`（`dev` 上的 `merge: 计划批准上指挥台（维护性补丁，见 docs/v3/13 §8.173）`）；tag：无（维护性补丁，不新增里程碑）
- spec：[console-plan-approval](../specs/console-plan-approval.md)
- 计划：[console-plan-approval](../plans/console-plan-approval.md)
- 决策记录：[docs/v3/13 §8.173](../13-决策记录.md)
- 本任务**无迁移、无后端改动**。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `c46cd3f` | 规格与实施计划入库（非里程碑补丁，不改 12 的路线与顺序） |
| T1 | `308712a` | `plan-format.ts`（PlanPanel 中文与色调抽取）；`attention.ts` 新增 plan 分支、`plans`/`suppressedTicketIds` 入参与新排序（blocked 4 → plan 5 → waiting 6 → spend 7）；`use-attention-plans.ts` 类型 |
| T2 | `9a289ba` | `PlanApprovalCard`：标题、步骤、来源工单、批准/退回（原因 ≤ 200 字、trim 非空、可取消） |
| T3 | `ca62c8f` | `useAttentionPlans`（详情 → 会话计划两段查询、plan 项与抑制集合、失败回落）；`usePlanApprovalResolver`（POST 审批、写回计划缓存、失效工单详情/列表、Toast、错误）；`AttentionList` plan 分支与顶部错误；`AttentionState.planResolver`；测试设施 `makePlan` |
| T4 | 本台账所在提交 | 11 §1/§3.2/§4.2/§10/§11 同步、13 §8.173、spec 签名修订、台账 |

## Rulings I made

| # | 裁决 | 理由 | 代价 |
|---|---|---|---|
| R1 | 不新增后端端点/字段：指挥台先读 `GET /api/agent/tickets/{id}` 拿 `worker_session_id`，再读 `GET /api/agent/sessions/{session_id}/plan` | 工单列表项没有 `worker_session_id`，计划端点只认会话 id；只对 `waiting+decision` 工单多发一次请求，数量很少且复用现有查询缓存。避免改对外契约（04/14/15、OpenAPI、生成类型） | 每个待批准计划多一次详情请求；若服务端以后给列表项补 `worker_session_id`，可去掉这次请求 |
| R2 | 计划卡排在 blocked 工单之后、waiting 工单之前 | 计划待批准是需要决策、会阻塞 Worker 的事，比普通 waiting 更值得先看；保留既有类型相对顺序 | 与 11 原排序句不同，已在 11 §3.2 同步 |
| R3 | `PlanResolver.resolve(workerSessionId, ticketId, input)` 比 spec 原签名多 `ticketId` | 审批后要精确失效 `tickets.detail(ticketId)`；用 `tickets.all()` 会连带重读消息、汇报、运行等查询 | 内部签名与 spec 初稿不同，已在 spec §4.3 与 13 §8.173 修正 |
| R4 | 计划读取失败（详情无会话、计划接口报错、plan 为 null）不报错、不抑制，回落现有工单卡 | 「待你处理」不因附加数据源失败而丢失事项；用户仍能进工单页（第二入口） | 计划读不到时指挥台无法就地批准，需要进工单页 |
| R5 | 计划已不处于 `awaiting_approval`（刚批准/退回）时抑制该工单卡，而不是显示工单卡 | 审批后工单 `human_action` 要等 Worker 下一轮汇报才清；显示旧工单卡会是一张点了 409 的死卡 | Worker 若长时间不汇报，该工单在这段时间不占「待你处理」；可从工单列表进入 |
| R6 | 退回原因上限 200 字、trim 非空才可提交 | 与后端 `PlanApprovalRequest.reason` 的 `max_length=200` 一致；退回需要可执行的修改说明 | 无 |
| R7 | 来源行「来自工单「标题」· Worker 中文名」由 `AttentionList` 统一渲染（与确认/提问一致），卡片内只放「打开工单」 | 复用既有来源行写法与 `specTitles`，卡片保持展示组件 | 无 |

## 验证证据

| 命令 | 结果 |
|---|---|
| `cd web && pnpm gen:api:check` | `OpenAPI types are up to date.`（exit 0） |
| `cd web && pnpm typecheck` | 通过（exit 0） |
| `cd web && pnpm test --run` | **114 files / 993 tests passed**（本次新增 14 条计划用例，含复审后补的失败路径） |
| `cd web && pnpm build` | `built in ~0.5s`（exit 0） |
| `.venv/bin/python scripts/frontend_smoke.py` | 末行 `SMOKE OK` |
| `.venv/bin/python -m pytest -q` | **3 failed / 其余通过**：`test_activity_index` 2 条 + `test_stage_layout` 1 条，均为真资源数据漂移的**既有基线**（M85 台账记录「3 条真资源数据漂移」）；`git diff 175401e..HEAD -- maa_api tests` 为空，无新增失败 |
| `.venv/bin/python scripts/check_doc_links.py` | 末行 `BROKEN 0` |
| `git diff --check` | 干净（无输出） |

补充检查（不作硬性验证）：桌面与 375px 宽度各走一遍「plan awaiting → 指挥台批准/退回」——本次用 jsdom 集成用例覆盖桌面侧栏与手机 Sheet 的批准/退回/失败路径，未做真机手动检查。

合并后 `dev` 复验：`pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` 全绿（114 files / 993 tests）；`scripts/frontend_smoke.py` → `SMOKE OK`；`check_doc_links.py` → `BROKEN 0`；`git diff --check` 干净。

## Final review（独立复审与修复）

独立 reviewer（fresh context）复审 `175401e..610c0b9` 后结论 **With fixes**；已按意见做一轮修复，先 RED 后 GREEN：

- Important 1：409 时计划卡消失会让列表变空、手机 Sheet 自动收起，错误提示丢失。修复：`AttentionList` 的 resolver 错误块提到空态之前；`ConsolePage` 的 Sheet 自动收起排除 `planResolver.error`。测试 `keeps the plan error visible after the last item disappears`、`keeps the phone attention sheet open when the plan approval fails` RED→GREEN。
- Important 2：失败路径测试不足。补：计划 404、plan 为 null、ref 不匹配三种回落；审批 500 后重试；批准成功后同一工单不出 ticket 卡（死卡不变量）；409 提示可见性（上条）。
- Minor：无会话候选的 disabled 计划查询不再计入 `isPending`（测试 `does not stay pending when a decision ticket has no worker session` RED→GREEN）；去掉不会命中的 `useMemo` 与多余类型断言；11 与 spec 的失败提示措辞同步。
- 复审列出但本次不改的（记遗留）：初始加载先显示工单卡再切计划卡（失败与加载都回退，不丢事项）；计划进入 done/abandoned 后 GET 返回 null 时回退工单卡（spec §4.1 的取舍，等后端计划接口语义变化再处理）；来源行工单标题不可点（确认/提问同样，既有偏差）；PlanPanel 退回原因无 200 上限（本次要求 PlanPanel 行为不变，后端 `max_length=200` 兜底）；多张计划卡共享一个 mutation（与既有确认 resolver 同构）。

## 偏离（已记 13 §8.173）

- 计划批准从工单页主入口改为指挥台主入口、工单页保留为第二入口；无其它契约变化。

## 遗留

- 非计划的 `decision`（重启打断、Watchdog、失败补 `blocked`、Worker 自报）仍没有后端处置端点，仍走工单卡 → 工单页。
- 观察记录汇总行仍未接入「待你处理」（M81 既有偏离，与本次无关）。
- 指挥台待确认/待提问/待批准列表仍是各查询第一页（确认 size=20、提问/工单 size=50）；本次未扩大分页。
