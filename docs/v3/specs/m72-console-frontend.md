# M72 指挥台前端（规格）

> 阶段：P1　依赖：M70、M71　分支：`console-frontend`　迁移：无
> 本文是 M72 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在 dev 上新建的分支，工作区干净 | `git status -sb` | `## console-frontend`，没有改动 |
| 2 | M70、M71 已合并 | `git tag -l 'v3-m70' 'v3-m71'` | 两行 |
| 3 | 依赖的端点都在 OpenAPI 快照里 | 附录 A 的脚本 A1 | 末行 `MISSING 0` |
| 4 | 04 命名的响应模型进了快照 | 附录 A 的脚本 A2 | 7 行都不是 `MISSING`；把属性名抄进台账，T9、T10 的适配函数按它核对 |
| 5 | 服务端登记了两个新频道 | `rg -n '"agent_run"\|"ticket_status"' maa_api/api/ws.py` | 至少两处匹配 |
| 6 | 确认与提问的 wire 带 origin（M71） | `rg -n '"origin"' maa_api/agent/confirmation.py maa_api/agent/questions.py` | 两个文件都有匹配 |
| 7 | M70 接收 `mentions` 的写入点 | `rg -n "mentions" maa_api/api/routers maa_api/agent/orchestrator maa_api/agent/workers maa_api/agent/tickets` | 能找到请求模型字段与写用户消息的函数；把文件与函数名写进台账，T3 改它们 |
| 8 | M70 的 v3 消息响应模型 | `rg -n "inbox_id" maa_api/api/schemas maa_api/api/routers/orchestrator.py maa_api/api/routers/tickets.py` | 找到一个 Pydantic 模型类与构造它的函数；名字写进台账，T2 改它 |
| 9 | 工单与收件箱的表类 | `rg -n "class Ticket\(SQLModel, table=True\)\|class AgentInbox\(SQLModel, table=True\)" maa_api/db/models.py` | 两行 |
| 10 | 四个 v2 写端点还在 | `rg -n "^async def (create_session\|check_capabilities\|stream_session_message\|interrupt_session_turn)\(" maa_api/api/routers/agent.py` | 四行 |
| 11 | `stream_message` 还在，且只有路由与测试调用 | `rg -nw "stream_message" maa_api` | `runtime.py` 的定义一处、`routers/agent.py` 的调用一处；别处还有调用时停下来问 |
| 12 | 前端引用面 | `rg -ln "capabilities/check\|streamMessage" web/src --glob '!web/src/types/**'` | `AgentPage.tsx`、`AgentPage.test.tsx`、`stream.ts`，以及（M67 没改过的话）`AgentCapabilityCheck.tsx`、`settings.test.tsx`；记下后两者在不在，T11 第 7 步据此执行或跳过 |
| 13 | M67、M68 的前端落点 | `rg -n "more/runs\|more/costs\|settings/models" web/src/routes/router.tsx web/src/components/layout/nav.ts web/src/features/settings/sections.ts; rg -n "runs\|modelRoles\|specs" web/src/api/keys.ts` | 记进台账；T5 与 T9 按结果二选一 |
| 14 | 测试驱动的文件名没被占用 | `test -e tests/fakes/legacy_turn.py && echo exists \|\| echo absent` | `absent` |
| 15 | 后端基线 | `.venv/bin/python -m pytest -q > /tmp/m72-base.log 2>&1; echo $?; tail -n 5 /tmp/m72-base.log` | 记录退出码与失败用例 |
| 16 | 前端基线 | `cd web && pnpm install --frozen-lockfile > /tmp/m72-pnpm.log 2>&1 && pnpm test --run > /tmp/m72-vitest-base.log 2>&1; echo $?; cd ..` | 记录退出码 |

## 1. 背景与目标

M70、M71 交付了总控、Worker、工单、SSE v3 与 WS 新频道，前端还是 v2 的多会话 Agent 页。本里程碑把产品形态切到指挥台。做完后：

1. 首页 `/` 是指挥台：「待你处理」列出确认、提问、等你处理与受阻的工单；「总控会话」流式显示总控的回复，确认卡、提问卡就地处理，能停止本轮、能重新开始；「进行中的工单」列出 `status=open` 的工单。
2. `/tickets` 列工单，`/tickets/:ticketId` 看汇报时间线、Worker 消息（子代理嵌套在委派调用下）、活动树、委托、设备租约，并能给 Worker 发消息。
3. `/more/agents` 只读列出代理定义，详情页给出工具、委派、模型角色与提示词正文。
4. 底部 Tab 是「指挥台、工单、任务、日志、更多」，仍是 5 个；v2 首页搬到 `/more/status`。
5. v2 Agent 页成为 `/more/sessions` 只读查看器，`/agent` 与 `/agent/*` 重定向过去；后端删除四个 v2 写端点与 `AgentRuntime.stream_message`，v2 行为测试改由测试驱动跑。
6. 输入栏支持 `/stop`、`/tickets`、`/help` 与五类 @ 提及，提及在服务端展开成【引用】段。
7. WS 的 `agent_run`、`ticket_status`、`agent_event.lease_changed` 驱动 query 失效，断线重连后补读。
8. 12 §4 的门禁、`agent_smoke.py`、`frontend_smoke.py` 全部通过。

## 2. 非目标

- 计划面板（M75）、`/compact` 与压缩分隔线（M74）、产物胶囊与视图卡（M75、M90）、记忆提议与观察记录与 Watchdog 告警进「待你处理」（M79、M81、M83）、语音（M97）、IM 消息的来源标记（M85）。
- 不改全局待确认面板 `ConfirmationDock` 的口径（只列确认），不改 v2 设计系统与 `Page` 框架。
- 不新增表、端点、错误码、配置项、工具、依赖；不删任何错误码。
- 诊断改建工单、`legacy_chat` 退场是 M73 的事。

## 3. 相关文档

- [11 前端指挥台](../11-前端指挥台.md) 全文；[04](../04-接口与事件协议.md) §2.2、§3、§4；[05](../05-运行时与代理定义.md) §2.4、§2.5、§7；[02](../02-系统架构.md) §6.2、§6.3、§7；[03](../03-数据模型.md) §3.1、§6.5、§6.6、§7.1。
- v2 前端方案：`rg -n "^### 6.4\|^### 6.6\|^### 8.1\|^### 8.4" docs/v2/09-前端重构方案.md`，读 Agent 页、全局待确认、安全区、虚拟键盘四节。
- [文案规范](../../copy-audit/规范-文案规范.md)。
- 既有代码入口（行号是 2026-11-02 的近似值）：

| 位置 | 符号 | 用途 |
|---|---|---|
| `web/src/features/agent/AgentPage.tsx` | `send`（约 378）、SSE 分派（403–515）、`stopTurn`（约 296）、`invalidateTurnQueries`（约 280） | 实时副本、停止与收尾的做法照搬到 `use-conversation.ts` |
| `web/src/features/agent/stream.ts` | `parseSseFrame`（10–25）、`streamMessage`（31–74） | 前者复用；后者改成 `streamSse` |
| `web/src/features/agent/ConfirmationCard.tsx` | `ConfirmationCard`（约 405），props 在 26–35 | 原样复用 |
| `web/src/features/agent/QuestionCard.tsx` | `QuestionCard`（约 41），props 在 30–38 | 原样复用 |
| `web/src/features/agent/MessageViews.tsx` | `UserBubble`、`AssistantText`、`TranscriptEntryView`、`LiveTurn`、`QuickPrompts` | 前三个复用 |
| `web/src/features/agent/transcript.ts` | `buildTranscript`（约 36）、`liveToolView`（约 89）、`ToolView` | 复用 |
| `web/src/features/agent/messages.ts` | `loadMessageWindow`（约 61）、`prependPage`、`windowItems`、`earlierPageNumber` | 改成泛型后复用 |
| `web/src/features/agent/Composer.tsx` | `ComposerProps`（7–28） | 加三个可选 props |
| `web/src/features/agent/format.ts` | `TOOL_LABELS`（109–186）、`toShownError`（约 297）、`parseQuestionOptions`（约 100） | 补工具名；复用 |
| `web/src/features/agent/StatusAlerts.tsx` | `ErrorAlert`（约 40） | 复用 |
| `web/src/features/confirmations/ConfirmationsPanel.tsx` | `pendingConfirmationsQuery`（约 35）、`Confirmation`（15–25） | 「待你处理」共用这份缓存 |
| `web/src/features/settings/knowledge/api.ts` | `knowledgeListQuery` | @ 提及的知识候选 |
| `web/src/features/account/use-debounced-value.ts` | `useDebouncedValue` | 干员候选防抖 |
| `web/src/realtime/events.ts`、`query-bridge.ts` | `ServerEvent`（37–56）、`REALTIME_STATE_CHANNELS`（57–70）、`handleServerEvent`（47–136）、`invalidateBatched`（9–20） | 加频道与失效 |
| `web/src/api/keys.ts` | `keys.agent`（40–58） | 加 key |
| `web/src/components/layout/nav.ts`、`SideNav.tsx`、`layout.test.tsx` | `primaryNav`、`secondaryNav`、桌面与手机导航用例（64–90） | 改导航 |
| `web/src/routes/router.tsx` | 懒加载（12–27）、`routeConfig`（104–142） | 改路由 |
| `web/src/routes/more/index.tsx` | `operations`、`system`（33–34） | 按组渲染 |
| `web/src/features/settings/SchemaSection.tsx` | 「查看设备状态」链接（约 40） | 改指 `/more/status` |
| `maa_api/api/routers/agent.py` | `session_wire`（约 305）、`message_wire`（约 341）、`list_sessions`（约 658）、`create_session`（约 682）、`check_capabilities`（约 716）、`stream_session_message`（约 733）、`interrupt_session_turn`（约 804）、`list_session_messages`、`_LockedSessionStreamingResponse` | 删四个写端点、补 wire |
| `maa_api/db/repositories/agent.py` | `AgentSessionRepository.list_page`（约 86） | 加旧会话筛选 |
| `maa_api/agent/runtime.py` | `AgentRuntime.stream_message`（约 718） | 删除，搬进测试驱动 |
| `maa_api/agent/knowledge/__init__.py` | `KnowledgeService.read_for_agent`（约 869） | 展开知识提及 |
| `maa_api/services/account/box.py` | `OperatorBoxService.get_operator`（约 141），装配为 `app.state.box_service` | 展开干员提及 |
| `maa_api/services/stage_resolver.py` | `StageResolver.resolve`（约 49） | 展开关卡提及；取法同 `agent/tools/status.py` 的 `resolve_stage` |
| `maa_api/db/repositories/pipeline.py` | `PipelineRepository.get(pipeline_id, with_tasks=True)`（约 67） | 展开流水线提及 |
| `scripts/agent_smoke.py` | `_SmokeLLM`（约 43）、`_install_internal_invoke_probe`（约 267）、`_check_builtin_conversation`（约 363）、会话创建（约 790） | 改冒烟 |
| `scripts/frontend_smoke.py` | `main`（约 29） | 加检查 |

## 4. 设计

### 4.1 数据模型

不改表、不加迁移。三处 wire 只加字段，不改已有字段：

| 位置 | 新字段 | 取值 |
|---|---|---|
| v2 `session_wire` | `kind`、`archived_at` | `agent_session` 的列（M66），时间带 `Z` |
| v2 `message_wire` | `run_id`、`inbox_id`、`inbox_kind`、`inbox_thread_key` | 前两个是 `agent_message` 的列；后两个是 `inbox_id` 指向的 `agent_inbox.kind`、`thread_key`，`inbox_id` 为空时都是 `null` |
| v3 消息响应模型（前置检查 #8 的类） | `inbox_kind: InboxKind \| None = None`、`inbox_thread_key: str \| None = None` | 同上 |

### 4.2 模块与文件

后端：

```python
# maa_api/db/repositories/agent.py（改）
class AgentSessionRepository(BaseRepository):
    async def list_page(
        self, *, page: int = 1, size: int = 20, legacy_only: bool = False
    ) -> Page[AgentSession]:
        """legacy_only=True 时只列 chat、incident 两类，加上 archived_at 非空的 orchestrator。
        排序不变；计数语句带同样的条件。"""

async def load_inbox_meta(
    session: AsyncSession, inbox_ids: Collection[int]
) -> dict[int, tuple[str, str | None]]:
    """收件箱项 id → (kind, thread_key)。空集合直接返回 {}，不发查询。"""
```

```python
# maa_api/api/routers/agent.py（改）
def session_wire(session: AgentSession) -> dict[str, Any]: ...      # 末尾加 kind、archived_at
def message_wire(
    message: AgentMessage, inbox: tuple[str, str | None] | None = None
) -> dict[str, Any]: ...                                             # 末尾加 run_id、inbox_id、inbox_kind、inbox_thread_key
# list_sessions 改调 list_page(..., legacy_only=True)
# list_session_messages 先 load_inbox_meta(本页 inbox_id 集合)，再逐条 message_wire(item, meta.get(item.inbox_id))
# 删除：create_session、check_capabilities、stream_session_message、interrupt_session_turn 四个路由函数，
#       以及只被它们用到的 SessionCreateRequest、SessionMessageRequest、_LockedSessionStreamingResponse
```

```python
# maa_api/agent/mentions.py（新）
"""用户消息里 @ 提及的服务端展开（05 §2.5）：写入用户消息时附在正文后面。"""
REFERENCE_HEADER = "【引用】"
MAX_MENTIONS = 8
MAX_ITEM_CHARS = 400
READ_FAILED = "（读取失败）"
TYPE_LABELS = {"ticket": "工单", "knowledge": "知识", "operator": "干员", "stage": "关卡", "pipeline": "流水线"}

class MentionLike(Protocol):
    type: str
    id: str
    label: str

@dataclass(frozen=True)
class MentionContext:
    app_state: Any                                   # request.app.state
    session_factory: async_sessionmaker[AsyncSession]

async def expand_ticket(mention: MentionLike, ctx: MentionContext) -> str: ...
async def expand_knowledge(mention: MentionLike, ctx: MentionContext) -> str: ...
async def expand_operator(mention: MentionLike, ctx: MentionContext) -> str: ...
async def expand_stage(mention: MentionLike, ctx: MentionContext) -> str: ...
async def expand_pipeline(mention: MentionLike, ctx: MentionContext) -> str: ...

EXPANDERS: dict[str, Callable[[MentionLike, MentionContext], Awaitable[str]]] = {
    "ticket": expand_ticket, "knowledge": expand_knowledge, "operator": expand_operator,
    "stage": expand_stage, "pipeline": expand_pipeline,
}

async def render_reference_block(mentions: Sequence[MentionLike] | None, ctx: MentionContext) -> str:
    """按 (type, id) 去重、只展开前 8 个；返回 "\n\n【引用】\n- 行\n- 行"，没有提及返回 ""。
    单个展开函数抛任何异常都写成「{类型}「{label}」（读取失败）」并记 warning，不向外抛。"""
```

展开函数的输出（每行在块里前缀 `- `，超过 400 字截成 399 字加 `…`）：

| 类型 | 数据来源 | 成功时 | 读不到时 |
|---|---|---|---|
| `ticket` | `await session.get(Ticket, mention.id)` | `工单「{title}」（{status}）：{summary}`；`summary` 为空写 `还没有汇报` | `工单「{label}」（读取失败）` |
| `knowledge` | `await app_state.knowledge_service.read_for_agent(mention.id)` | `知识「{title}」（{id}）：{when_to_use}。正文节选：{body 前 200 字，换行换成空格}` | `知识「{label}」（读取失败）` |
| `operator` | `await app_state.box_service.get_operator(mention.id)` | `干员 {name}（{char_id}）：持有 {是\|否}，精英化 {elite}，等级 {level}，潜能 {potential}，技能等级 {skill_level}，专精 {masteries}，模组 {modules}，练度同步于 {recognized_at}`；`None` 写 `未知`；专精 `{"1":2,"2":0,"3":3}` 写 `2/0/3`；模组 `{"X":2}` 写 `X2`、空映射写 `无` | `ACCOUNT_DATA_NOT_FOUND` 写 `干员 {label}：账号数据里没有这名干员`；其他异常写 `干员「{label}」（读取失败）` |
| `stage` | `getattr(app_state, "stage_resolver", None) or StageResolver(getattr(app_state, "core_registry", None))`，再 `await resolver.resolve(mention.id)` | `candidates_total == 1`：`关卡 {code}（{name}，{stage_id}）`；大于 1：`关卡 {label}：有 {n} 个同名候选（前 3 个 stage_id 用、连接），需要时用 stageId 再查`；为 0：`关卡 {label}：本地关卡索引里找不到` | `关卡「{label}」（读取失败）` |
| `pipeline` | `PipelineRepository(session).get(mention.id, with_tasks=True)` | `流水线 {title 或「无标题」}（{id 前 8 位}，{status}）：{完成数}/{task_count} 个任务完成；创建于 {created_at 的 ISO 带 Z}`，有 `error_message` 时再接 `；错误：{error_message}` | `流水线「{label}」（读取失败）` |

提及超过 8 个时块末多一行 `- 另有 {n} 条引用没有展开`。

M70 写入点（前置检查 #7 找到的函数）改为：先算 `block = await render_reference_block(payload.mentions, MentionContext(request.app.state, <该函数已在用的 session_factory>))`，再把 `payload.content + block` 写成用户消息。工单消息给总控收件箱写的那条 `system` 摘要（02 §6.3，截到 200 字）用 `payload.content` 原文，不带【引用】段。

```python
# tests/fakes/legacy_turn.py（新，只给测试用）
async def stream_legacy_turn(runtime: Any, session_id: str, content: str, request: Any) -> AsyncIterator[str]:
    """M71 时 AgentRuntime.stream_message 的实现原样搬来，self 换成 runtime。
    用来继续驱动 v2 行为测试；生产代码里已经没有这条路径。"""
```

`maa_api/agent/specs/common.md`（改）：加一条，见 §4.7。

前端文件（路径都在 `web/src/` 下）：

| 路径 | 新建或修改 | 职责 |
|---|---|---|
| `api/keys.ts` | 改 | 加 §4.6 的 key |
| `realtime/events.ts`、`realtime/query-bridge.ts` | 改 | 新频道与失效（§4.5.7） |
| `features/agent/stream.ts` | 改 | `streamMessage` 换成 `streamSse` |
| `features/agent/Composer.tsx` | 改 | 加 `placeholder`、`label`、`interceptKeyDown` |
| `features/agent/messages.ts` | 改 | 泛型化，行为不变 |
| `features/agent/format.ts` | 改 | `TOOL_LABELS` 补 9 个工具 |
| `features/agent/MessageViews.tsx` | 改 | 导出 `TypingIndicator`；删 `LiveTurn`、`QuickPrompts`、`QUICK_PROMPTS` |
| `features/agent/StatusAlerts.tsx` | 改 | 删 `GrantBar`、`CapabilityAlert` |
| `features/agent/types.ts` | 改 | `AgentSession` 加 `kind?`、`archived_at?`；`AgentQuestion` 加 `origin?`（形状同下行）；删 `StreamingTurn`、`TurnSegment`、`CapabilityResult` |
| `features/confirmations/ConfirmationsPanel.tsx` | 改 | `Confirmation` 接口加 `origin?: { ticket_id: string \| null; ticket_title: string \| null; agent: string \| null; run_id: string \| null } \| null`，其余不动 |
| `features/agent/SessionList.tsx` | 改 | 归档的总控会话显示徽章「已归档的总控」 |
| `features/agent/AgentPage.tsx` → `LegacySessionsPage.tsx` | `git mv` 后改 | 只读查看器（§4.6.6） |
| `features/agent/AgentPage.test.tsx` → `LegacySessionsPage.test.tsx` | `git mv` 后改 | 见 §6 |
| `features/console/conversation/types.ts` | 新 | 会话共用类型 |
| `features/console/conversation/sse.ts` | 新 | `toSseV3Event` |
| `features/console/conversation/live-turn.ts` | 新 | 实时副本 reducer 与历史去重 |
| `features/console/conversation/references.ts` | 新 | 拆【引用】段 |
| `features/console/conversation/transcript-v3.ts` | 新 | 消息 → 渲染条目 |
| `features/console/conversation/card-resolvers.ts` | 新 | 确认、提问处理后的失效与本地摘除 |
| `features/console/conversation/use-conversation.ts` | 新 | 消息窗口、实时副本、发送、排队、停止 |
| `features/console/conversation/ConversationView.tsx` | 新 | 历史 + 实时副本 + 排队气泡 |
| `features/console/conversation/LiveTurnView.tsx` | 新 | 实时副本 |
| `features/console/conversation/UserMessage.tsx`、`InboxEventCard.tsx`、`DelegateToolBar.tsx`、`ReportCallout.tsx`、`TicketLinkChip.tsx` | 新 | 条目组件 |
| `features/console/conversation/commands.ts`、`composer-assist.ts`、`mention-sources.ts` | 新 | 斜杠命令与 @ 提及 |
| `features/console/conversation/ComposerAssist.tsx`、`HelpCard.tsx`、`ConversationComposer.tsx` | 新 | 输入栏与面板 |
| `features/console/api.ts`、`attention.ts`、`use-attention.ts` | 新 | 指挥台数据 |
| `features/console/AttentionList.tsx`、`AttentionPanel.tsx`、`AttentionSheet.tsx`、`AttentionSummaryBar.tsx`、`ActiveTicketsPanel.tsx`、`ActiveTicketsStrip.tsx`、`ConsolePage.tsx` | 新 | 指挥台页面 |
| `features/console/test-utils.tsx` | 新 | 指挥台与工单页测试共用的假后端 |
| `features/tickets/api.ts`、`format.ts` | 新 | 工单数据与文案 |
| `features/tickets/TicketsPage.tsx`、`TicketDetailPage.tsx`、`TicketSummaryCard.tsx`、`MandateCard.tsx`、`ReportTimeline.tsx`、`ActivityTree.tsx`、`LeaseIndicator.tsx`、`TicketStatusBadge.tsx` | 新 | 工单页面 |
| `features/agents/api.ts`、`format.ts`、`AgentsPage.tsx`、`AgentDetailPage.tsx` | 新 | 代理定义页 |
| `components/layout/nav.ts`、`SideNav.tsx`、`layout.test.tsx` | 改 | 导航 |
| `routes/router.tsx`、`routes/more/index.tsx`、`routes/more/more.test.tsx`、`routes/dashboard/index.tsx` | 改 | 路由与「更多」 |
| `features/settings/SchemaSection.tsx`、`settings.test.tsx`、`AgentCapabilityCheck.tsx` | 改 | 链接与能力检查 |

### 4.3 接口

删除的端点（04 §2.2 的 M72 行）与删除后的响应：

| 方法与路径 | 删除后 |
|---|---|
| POST `/api/agent/sessions` | 405（同路径的 GET 仍在） |
| POST `/api/agent/sessions/{session_id}/messages` | 405（同路径的 GET 仍在） |
| POST `/api/agent/sessions/{session_id}/interrupt` | 404，错误码 `NOT_FOUND` |
| POST `/api/agent/capabilities/check` | 404，错误码 `NOT_FOUND` |

`GET /api/agent/sessions` 只返回旧会话（§4.2）。前端用到的端点全部是 04 已有的，见 §4.6.2 的查询表。SSE v3 的事件与字段照 04 §3，WS 新频道照 04 §4.1。v2 的 agent 路由返回 dict，不进 OpenAPI 的响应形状，所以 §4.1 的 v2 wire 改动不改 `web/openapi.json`；删端点与 v3 消息模型加字段会改，按 12 §4 第 3 项重新生成。

### 4.4 配置项

无。

### 4.5 行为与算法

#### 4.5.1 SSE 归一

```ts
// features/console/conversation/sse.ts
export type SseV3Event =
  | { type: 'run_start'; runId: string; parentRunId: string | null; agent: string; kind: string; ticketId: string | null }
  | { type: 'text_delta'; runId: string; text: string }
  | { type: 'tool_start'; runId: string; toolCallId: string; name: string; arguments: unknown }
  | { type: 'tool_result'; runId: string; toolCallId: string; name: string; auditId: number | null; raw: Record<string, unknown> }
  | { type: 'confirmation_required'; runId: string; toolCallId: string; confirmationId: string; origin: Origin | null }
  | { type: 'question_required'; runId: string; toolCallId: string; question: AgentQuestionPrompt; origin: Origin | null }
  | { type: 'subagent_start'; runId: string; parentRunId: string; agent: string; task: string }
  | { type: 'subagent_end'; runId: string; status: string; summary: string }
  | { type: 'report'; runId: string; ticketId: string; reportId: number; status: string; summary: string; humanAction: HumanAction | null }
  | { type: 'ticket_created'; runId: string | null; ticketId: string; title: string }
  | { type: 'interrupted'; runId: string }
  | { type: 'run_end'; runId: string; finishReason: string }
  | { type: 'error'; code: string; message: string }
  | { type: 'unknown'; name: string }

export function toSseV3Event(name: string, data: unknown): SseV3Event
```

规则：`data` 不是对象按 `{}` 处理；字符串字段一律经 `stringValue`，数字字段不是 number 就给 `null` 或 `0`；`question.options` 经 `parseQuestionOptions`；`origin` 是对象时取 `ticket_id`、`ticket_title`、`agent`、`run_id` 四个字段（缺的写 `null`），否则 `null`；`error` 的 `message` 缺省 `本轮回复失败，请重试。`。除 `ticket_created`、`error` 外，`run_id` 为空的事件一律归成 `unknown`。名字不在上面的事件（含 M74 的 `compaction`、M75 的 `plan_updated`、`artifact_created`）归成 `unknown`。

#### 4.5.2 实时副本 reducer

```ts
// features/console/conversation/live-turn.ts
export type LiveSegment =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; toolCallId: string }
  | { kind: 'report'; reportId: number; status: string; summary: string; humanAction: HumanAction | null }
  | { kind: 'ticket'; ticketId: string; title: string }

export interface LiveRun {
  runId: string
  parentRunId: string | null
  agent: string
  observedStart: boolean
  status: 'running' | 'succeeded' | 'interrupted' | 'failed'
  finishReason: string | null
  segments: LiveSegment[]
  tools: Record<string, ToolTrace>
  delegateRuns: Record<string, string>   // delegate 的 toolCallId → 子运行 id
  task: string | null                    // 子运行的任务原文
  summary: string | null                 // subagent_end 的摘要
}

export interface LiveTurn {
  scopeKey: string                       // 'orchestrator' 或 'ticket:<id>'
  userText: string
  baseMessageId: number                  // 发送时已加载历史的最后一条 id，没有历史为 0
  rootOrder: string[]
  runs: Record<string, LiveRun>
  pendingConfirmation: { confirmationId: string; runId: string; toolCallId: string; origin: Origin | null } | null
  pendingQuestion: (AgentQuestionPrompt & { runId: string; origin: Origin | null }) | null
  running: boolean
  interrupted: boolean
  error: ShownError | null
}

export function createLiveTurn(scopeKey: string, userText: string, baseMessageId: number): LiveTurn
export function reduceLiveTurn(turn: LiveTurn, event: SseV3Event): LiveTurn   // 纯函数，不改入参
export function finishLiveTurn(turn: LiveTurn): LiveTurn                      // 只把 running 置 false
export function liveRunSets(turn: LiveTurn): { full: Set<string>; partial: Set<string> }
export function filterLiveHistory<T extends Pick<V3Message, 'id' | 'role' | 'run_id'>>(messages: readonly T[], turn: LiveTurn | null): T[]
export function userMessageArrived(messages: readonly V3Message[], text: string, baseMessageId: number): boolean
```

| 事件 | 规则 |
|---|---|
| 任何带 `runId` 的事件，运行还不存在（`run_start`、`subagent_start` 除外） | 新建 `observedStart=false`、`parentRunId=null`、`agent=''` 的运行，追加到 `rootOrder` |
| `run_start` | 运行不存在才新建（`observedStart=true`）；`parentRunId` 为空或父运行不在 `runs` 里时追加到 `rootOrder` |
| `text_delta` | 最后一段是文字就拼接，否则新增文字段 |
| `tool_start` | `tools[toolCallId]` 不存在才写入 `{toolCallId, name, arguments}` 并追加工具段（防重复） |
| `tool_result` | 写 `tools[id].result = raw`、`auditId`；`name==='delegate'` 且 `raw.result.run_id` 是字符串时 `delegateRuns[id] = run_id`；`pendingConfirmation.toolCallId === id` 时清掉它；`name==='ask_user'` 且 `raw.result.question_id === pendingQuestion.question_id` 时清掉它 |
| `confirmation_required` | 写 `pendingConfirmation`，对应工具写 `confirmationId` |
| `question_required` | 写 `pendingQuestion` |
| `subagent_start` | 新建子运行（`observedStart=true`、`task`）；父运行不在 `runs` 里时它进 `rootOrder`；否则在父运行里按工具段顺序找第一个 `name==='delegate'`、`arguments.agent===agent`、`arguments.task===task` 且不在 `delegateRuns` 值里的调用，写 `delegateRuns[toolCallId] = 子运行 id` |
| `subagent_end` | 子运行 `status`：`succeeded` → `succeeded`；`interrupted`、`cancelled` → `interrupted`；其他 → `failed`；写 `summary` |
| `report` | 该运行追加汇报段 |
| `ticket_created` | 追加工单段到 `runId` 对应的运行；没有就追加到 `rootOrder` 的最后一个运行；都没有就忽略 |
| `interrupted` | 该运行 `status='interrupted'`；它在 `rootOrder` 里时 `running=false`、`interrupted=true`，两张挂起卡清空 |
| `run_end` | `finishReason` 为 `interrupted`、`recovered_after_restart`、`cancelled` → `interrupted`；`error`、`stuck_loop`、`budget_exceeded`、`context_overflow` → `failed`；其余 → `succeeded` |
| `error` | `error = {message, code}` |
| `unknown` | 原样返回 |

```ts
export function filterLiveHistory(messages, turn) {
  if (!turn) return [...messages]
  const { full, partial } = liveRunSets(turn)
  return messages.filter((message) => {
    if (message.role === 'user' || !message.run_id) return true
    if (full.has(message.run_id)) return false
    return !(partial.has(message.run_id) && message.id > turn.baseMessageId)
  })
}

export function userMessageArrived(messages, text, baseMessageId) {
  const wanted = text.trim()
  return messages.some((message) => message.id > baseMessageId && message.role === 'user'
    && !isInboxEvent(message) && splitReferenceBlock(message.content ?? '').text.trim() === wanted)
}
// isInboxEvent(m) = m.inbox_id != null && m.inbox_kind !== 'user_message'（transcript-v3.ts 导出）
```

#### 4.5.3 发送、排队与停止（`use-conversation.ts`）

```ts
export interface ConversationEndpoints {
  scopeKey: string
  messagesKey: QueryKey
  stateKey: QueryKey                                     // 总控：orchestrator.state；工单：tickets.detail(id)
  streamPath: string
  fetchPage: (page: number, signal?: AbortSignal) => Promise<PageEnvelope<V3Message>>
  interrupt: () => Promise<void>
}
export function orchestratorEndpoints(): ConversationEndpoints
export function ticketEndpoints(ticketId: string): ConversationEndpoints   // 消息带 include_subagents=true

export interface ConversationController {
  history: V3Message[]                 // 已加载的完整窗口
  visible: V3Message[]                 // filterLiveHistory 之后
  query: UseQueryResult<MessageWindow<V3Message>>
  hasEarlier: boolean
  loadEarlier: (beforeApply?: () => void) => void
  loadingEarlier: boolean
  earlierError: unknown
  live: LiveTurn | null
  showOptimisticUser: boolean
  queued: readonly { localId: string; text: string }[]
  send: (input: { content: string; mentions: MentionDraft[] }) => Promise<boolean>
  stop: () => void
  stopping: boolean
  canStop: boolean                     // live?.running || turnRunning || stopping
  error: ShownError | null
  clearError: () => void
  confirmationResolver: ConfirmationResolver
  onQuestionResolved: (questionId: string) => void
}
export function useConversation(endpoints: ConversationEndpoints, options: { turnRunning: boolean; enabled: boolean }): ConversationController
```

```
send({content, mentions}):
  text = content.trim()；空就返回 false
  body = {content: text, mentions: mentions 映射成 {type, id, label}}
  若 live?.running 为真（排队）：
     记 {localId, text, base = history 最后一条 id ?? 0} 进 queued
     try  await streamSse(streamPath, body, new AbortController().signal, () => {}, {detach: true})；返回 true
     catch e：从 queued 移除这一条；setError(toShownError(e))；返回 false
  否则（直播）：
     base = history 最后一条 id ?? 0；generation = ++generationRef；controller = new AbortController()；abortRef = controller
     setError(null)；setLive(createLiveTurn(scopeKey, text, base))
     try  await streamSse(streamPath, body, controller.signal,
              (name, data) => setLive(t => t && t.scopeKey === scopeKey ? reduceLiveTurn(t, toSseV3Event(name, data)) : t))
          setLive(t => t ? finishLiveTurn(t) : t)
     catch e：
          若 controller.signal.aborted 且 stoppedRef 不是 controller：返回 false（换页、换工单，什么都不做）
          若没 abort：shown = toShownError(e)；setError(shown)；setLive(t => t ? {...finishLiveTurn(t), error: shown} : t)；failed = true
     finally：
          若 abortRef === controller：abortRef = null
          stoppedHere = stoppedRef === controller；若是则 stoppedRef = null
          若 controller.signal.aborted 且不是 stoppedHere：直接结束
          await Promise.all([invalidate(messagesKey), invalidate(stateKey)])
          若 generation === generationRef：setLive(t => t?.pendingConfirmation || t?.pendingQuestion ? t : null)
     返回 !failed

stop()：mutation 调 endpoints.interrupt()；成功后：
  若 abortRef 有值：stoppedRef = abortRef；abortRef.abort()
  setLive(t => t ? {...t, running: false, interrupted: true, pendingConfirmation: null, pendingQuestion: null} : t)
  await invalidate(messagesKey、stateKey)；setLive(t => t?.pendingConfirmation || t?.pendingQuestion ? t : null)
  失败：setError(toShownError(e))
```

- `showOptimisticUser = live !== null && !userMessageArrived(history, live.userText, live.baseMessageId)`。
- `queued` 里的项在 `userMessageArrived(history, item.text, item.base)` 为真时由 effect 移除。
- `scopeKey` 变了（换工单）时，在 `useLayoutEffect` 里 abort 并清空 `live`、`queued`、`error`；卸载时 abort。
- `query.isPending` 为真时 `send` 不执行（首屏历史还没到，没有可靠的 `base`）。
- 消息窗口复用 `loadMessageWindow`，每页 `MESSAGE_PAGE_SIZE`（200）。
- 总控会话还没读到（`enabled=false`）时不发消息查询。

#### 4.5.4 历史条目（`transcript-v3.ts`）

```ts
export type EventKind = 'report' | 'thread_event' | 'system' | 'trigger'
export interface EventLink { to: string; label: string }
export type ConversationEntry =
  | { kind: 'user'; key: string; message: V3Message; text: string; references: string[] }
  | { kind: 'event'; key: string; message: V3Message; eventKind: EventKind; body: string; link: EventLink | null }
  | { kind: 'assistant'; key: string; message: V3Message }
  | { kind: 'tool'; key: string; tool: ToolView; subRunId: string | null }
  | { kind: 'report'; key: string; status: string; summary: string; humanAction: HumanAction | null }

export function isInboxEvent(message: Pick<V3Message, 'inbox_id' | 'inbox_kind'>): boolean
export function buildConversationEntries(
  messages: readonly V3Message[],
  options: { mainSessionId: string | null; currentTicketId?: string },
): { entries: ConversationEntry[]; subRuns: Map<string, V3Message[]> }
```

1. `mainSessionId` 非空时，`session_id` 不等于它的消息按 `run_id` 收进 `subRuns`，不进 `entries`。
2. 其余消息先过 v2 `buildTranscript` 的配对逻辑（调用与结果合成一条），再按下面改写：
   - `role=user` 且 `isInboxEvent`：事件条目。`eventKind` 取 `inbox_kind`；为空时按正文前缀：`【工单汇报】` → `report`，`【事件】` → `thread_event`，`【系统】` → `system`，都不是 → `system`。`body` 是去掉前缀与首尾空白的正文。`link`：话题键 `ticket:<id>` 且 `<id> !== currentTicketId` → `{to: '/tickets/<id>', label: '打开工单'}`；`pipeline:<id>` → `{to: '/tasks/history/<id>', label: '查看流水线'}`；其他 → `null`。
   - `role=user` 其他：`splitReferenceBlock(content)` 拆出 `text` 与 `references`。
   - 工具名为 `report` 的调用：汇报条目，`status`、`summary`、`human_action` 取自调用参数，不再画工具条。
   - 工具名为 `delegate` 的调用：`subRunId` 取结果里 `result.run_id`（字符串才算），没有结果时为 `null`。

```ts
// references.ts
export const REFERENCE_HEADER = '【引用】'
export function splitReferenceBlock(content: string): { text: string; references: string[] }
// 在最后一次出现的 "\n\n【引用】\n" 处切开；后半段按行取以 "- " 开头的行并去掉 "- "；找不到就是 {text: content, references: []}
```

#### 4.5.5 待你处理的合并

```ts
// features/console/attention.ts
export type AttentionItem =
  | { kind: 'confirmation'; id: string; confirmation: Confirmation; origin: Origin | null }
  | { kind: 'question'; id: string; question: AgentQuestion; origin: Origin | null }
  | { kind: 'ticket'; id: string; ticket: TicketView }

export function buildAttentionItems(input: {
  confirmations: readonly Confirmation[]
  questions: readonly AgentQuestion[]
  tickets: readonly TicketView[]
  excludeIds?: ReadonlySet<string>
}): AttentionItem[]
export function originLabel(origin: Origin | null, caller: Confirmation['caller'] | undefined, specTitles: ReadonlyMap<string, string>): string
```

1. `coveredRefs` = 全部确认 id ∪ 全部提问 id；`coveredTickets` = 全部确认、提问的 `origin.ticket_id`（含被 `excludeIds` 排除的那些）。
2. 确认、提问里 id 在 `excludeIds` 的不出列表。
3. 工单 `human_action.ref_id` 在 `coveredRefs` 里、或工单 id 在 `coveredTickets` 里的不出列表。
4. 排序键：确认 0、提问 1、`blocked` 工单 2、`waiting` 工单 3；同组内确认与提问按 `expires_at` 升序（读不出的排最后），工单按 `updatedAt` 降序。
5. `originLabel`：有 `origin.ticket_id` → `来自工单「{ticket_title ?? '未命名'}」· {specTitles.get(agent) ?? agent}`；否则 `caller.kind==='rest'` → `来自 REST 调用`；其他 → `来自旧会话`。

#### 4.5.6 斜杠命令与 @ 提及

```ts
// commands.ts
export type SlashCommandName = 'stop' | 'tickets' | 'help'
export interface SlashCommand { name: SlashCommandName; label: string; description: string }
export const SLASH_COMMANDS: readonly SlashCommand[] = [
  { name: 'stop', label: '/stop', description: '停止本轮' },
  { name: 'tickets', label: '/tickets', description: '打开工单列表' },
  { name: 'help', label: '/help', description: '查看命令与引用' },
]
export function matchSlashCommands(draft: string): SlashCommand[]      // 草稿以 / 开头且没有空白时按前缀匹配，否则 []
export function parseSlashCommand(draft: string): SlashCommand | null  // draft.trim() 恰好等于某个 label 时返回它

// composer-assist.ts
export type MentionType = 'ticket' | 'knowledge' | 'operator' | 'stage' | 'pipeline'
export interface MentionDraft { type: MentionType; id: string; label: string }
export interface AssistToken { trigger: '@' | '/'; query: string; start: number; end: number }
export const STAGE_QUERY = /^[A-Za-z0-9][A-Za-z0-9-]{0,15}$/
export const MAX_MENTIONS = 8
export function findAssistToken(value: string, caret: number): AssistToken | null
//   '/'：value.slice(0, caret) 匹配 /^\/[a-z]*$/ → {trigger:'/', query: value.slice(1, caret), start: 0, end: caret}
//   '@'：value.slice(0, caret) 匹配 /(^|\s)@([^\s@]{0,24})$/ → start = caret - 组2长度 - 1，end = caret
export function insertMention(value: string, token: AssistToken, label: string): { value: string; caret: number }
//   value.slice(0, token.start) + '@' + label + ' ' + value.slice(token.end)；caret 落在插入的空格之后
export function activeMentions(value: string, mentions: readonly MentionDraft[]): MentionDraft[]
//   只留 value 里还包含 '@' + label 的；按 type+id 去重；最多 MAX_MENTIONS 个

// mention-sources.ts
export interface MentionCandidate extends MentionDraft { detail: string }
export function useMentionCandidates(type: MentionType, query: string, enabled: boolean): { items: MentionCandidate[]; isPending: boolean; error: unknown }
```

| 类型 | 查询 | 本地筛选与标签 |
|---|---|---|
| `ticket` | `keys.agent.tickets.list({ page: 1, size: 20 })`，`GET /api/agent/tickets?page=1&size=20` | 标题包含查询（不分大小写）；`label`=标题，`detail`=状态文案 |
| `knowledge` | `knowledgeListQuery` | 只留 `enabled`；id 或标题包含查询；`label`=标题，`detail`=id |
| `operator` | `keys.agent.mentionCandidates('operator', q)`，`GET /api/account/operators?query=q&owned_only=false&limit=8`；`q = useDebouncedValue(query, 200)`，`q` 为空不发请求 | `label`=`name`，`detail`=持有 / 未持有 |
| `stage` | 不发请求 | `STAGE_QUERY` 匹配时给一个候选 `{id: query, label: query, detail: '关卡'}` |
| `pipeline` | `keys.pipelines.list({ page: 1, size: 10 })`，`GET /api/pipelines?page=1&size=10` | 标题或 id 前缀包含查询；`label`=标题，没有标题时 `流水线 {id 前 8 位}`；`detail`=状态 |

输入栏（`ConversationComposer`）：`onValueChange` 时用 `textareaRef.current.selectionStart` 调 `findAssistToken`；有 token 时在 `overlay` 里渲染 `ComposerAssist`（命令列表或提及候选），`interceptKeyDown` 接管 ↑ ↓（移动高亮）、Enter 与 Tab（选中）、Esc（关闭）。提交时先 `parseSlashCommand(draft)`：

- `stop`：`controller.canStop` 为真就 `controller.stop()`，否则状态行显示 `现在没有在跑的一轮`（3 秒后消失）。
- `tickets`：`navigate('/tickets')`。
- `help`：在输入栏上方显示 `HelpCard`，再次输入或按 Esc 关闭。

命令执行后清空草稿，不发消息。其余情况调 `controller.send({content: draft, mentions: activeMentions(draft, mentions)})`；返回 `false` 且草稿为空时把原文放回（v2 口径）。

#### 4.5.7 WebSocket 到失效（`query-bridge.ts`）

```ts
case 'agent_run': {
  const data = event.data
  const orchestrator = queryClient.getQueryData<{ session_id?: string }>(keys.agent.orchestrator.state())
  const isOrchestrator = data.kind === 'orchestrator_turn' || (Boolean(data.session_id) && data.session_id === orchestrator?.session_id)
  if (isOrchestrator) {
    if (data.event !== 'message_created') invalidateBatched(queryClient, keys.agent.orchestrator.state())
    if (data.event !== 'run_started') invalidateBatched(queryClient, keys.agent.orchestrator.messages())
  }
  if (data.ticket_id) {
    if (data.event !== 'message_created') {
      invalidateBatched(queryClient, keys.agent.tickets.detail(data.ticket_id))
      invalidateBatched(queryClient, keys.agent.tickets.runs(data.ticket_id))
    }
    if (data.event !== 'run_started') invalidateBatched(queryClient, keys.agent.tickets.messagesAll(data.ticket_id))
  }
  break
}
case 'ticket_status':
  invalidateBatched(queryClient, keys.agent.tickets.lists())
  if (event.data.event !== 'ticket_created') invalidateBatched(queryClient, keys.agent.tickets.detail(event.data.ticket_id))
  if (event.data.event === 'report_created') invalidateBatched(queryClient, keys.agent.tickets.reports(event.data.ticket_id))
  break
// agent_event 分支追加：event.data.event === 'lease_changed' → invalidateBatched(queryClient, keys.agent.deviceLease())
// subscribed 分支追加：orchestrator.all()、tickets.lists()、deviceLease()
```

#### 4.5.8 边界情况

| 输入 | 期望 |
|---|---|
| 发送后服务端先转发一个已在跑的运行（没有它的 `run_start`） | 该运行进 `rootOrder`，`observedStart=false`，块前一行小字「接着处理之前的消息」；历史里它 id 大于 `base` 的非用户消息隐藏 |
| 直播中窗口重新聚焦，历史重读 | 完整观察的运行在历史里不出现，页面上只有一份 |
| 确认在别的页面处理掉（`tool_result` 到达） | 卡片撤下；流结束后实时副本照常丢弃 |
| 流结束时确认还挂着 | 实时副本保留，卡片留在原位 |
| 总控忙时再发一条 | 第二条只提交不读流，显示「排队中」气泡；进历史后气泡消失 |
| 连续两次发送完全相同的文字 | 排队气泡可能比预期早消失；可以接受，记为已知限制 |
| SSE 中途断网 | 本轮错误显示在实时副本之外；失效后历史接上；草稿为空时放回原文 |
| 工单已结束时发送 | 409 `TICKET_ENDED`：错误「工单已结束，不能再发消息」，输入栏随详情刷新换成提示 |
| 打开不存在的工单 | 404 `TICKET_NOT_FOUND`：`ErrorState` 标题「没有这个工单」，按钮「返回工单列表」 |
| `/stop` 时没有在跑的一轮 | 不发请求，状态行提示「现在没有在跑的一轮」 |
| 提及的标签被删掉一部分 | 发送时这条提及不带 |
| 后端以后加了前端不认识的 SSE 事件 | 忽略，不报错 |

### 4.6 前端

#### 4.6.1 路由与导航

```tsx
// routes/router.tsx（新增懒加载与路由；其余不变）
const ConsolePage = lazy(() => import('@/features/console/ConsolePage'))
const TicketsPage = lazy(() => import('@/features/tickets/TicketsPage'))
const TicketDetailPage = lazy(() => import('@/features/tickets/TicketDetailPage'))
const AgentsPage = lazy(() => import('@/features/agents/AgentsPage'))
const AgentDetailPage = lazy(() => import('@/features/agents/AgentDetailPage'))
const LegacySessionsPage = lazy(() => import('@/features/agent/LegacySessionsPage'))

/** v2 的 /agent 深链（含 ?session_id=）落到只读查看器。 */
function AgentRedirect() {
  const location = useLocation()
  return <Navigate to={{ pathname: '/more/sessions', search: location.search, hash: location.hash }} replace />
}
// children：
//   { index: true, element: page(<ConsolePage />) }
//   { path: 'tickets', element: page(<TicketsPage />) }
//   { path: 'tickets/:ticketId', element: page(<TicketDetailPage />) }
//   { path: 'agent/*', element: <AgentRedirect /> }        // 取代原来的 page(<AgentPage />)
//   { path: 'more/status', element: page(<DashboardPage />) }
//   { path: 'more/agents', element: page(<AgentsPage />) }
//   { path: 'more/agents/:name', element: page(<AgentDetailPage />) }
//   { path: 'more/sessions', element: page(<LegacySessionsPage />) }
// 删掉 AgentPage 的懒加载声明
```

```ts
// components/layout/nav.ts
export type NavGroup = 'agent' | 'ops' | 'system'
export const NAV_GROUP_TITLES: Record<NavGroup, string> = { agent: 'Agent', ops: '运维', system: '系统' }
export const primaryNav: readonly NavItem[] = [
  { to: '/', label: '指挥台', icon: Bot, end: true },
  { to: '/tickets', label: '工单', icon: Ticket },
  { to: '/tasks', label: '任务', icon: ListChecks },
  { to: '/logs', label: '日志', icon: ScrollText },
  { to: '/more', label: '更多', icon: LayoutGrid },
]
export const secondaryNav: readonly (NavItem & { description: string; group: NavGroup })[] = [
  { to: '/more/agents', label: '代理定义', icon: Network, description: '各代理的提示词、工具、模型与风险上限', group: 'agent' },
  { to: '/more/sessions', label: '旧会话', icon: History, description: '指挥台上线前的会话与归档的总控会话', group: 'agent' },
  // M67、M68 已加的条目（前置检查 #13）保留，补 group: 'agent'
  { to: '/more/status', label: '运行状态', icon: Activity, description: '设备、内核、队列与设备画面', group: 'ops' },
  // 热更新、账号数据、作业、Agent 审计、API 调试台：原样保留，补 group: 'ops'
  // 设置：原样保留，补 group: 'system'
]
```

`SideNav` 在主入口下按 `NAV_GROUP_TITLES` 的顺序分组渲染（组标题是 `<p>`，样式沿用现在的「系统」标题）；「更多」页把 `operations`、`system` 两个筛选换成按组的三个 `ListGroup`，组内为空不渲染。品牌链接的 `aria-label` 仍是 `MAA-API 首页`。`routes/dashboard/index.tsx` 的 `Page` 改成 `title="运行状态" back="/more"`，其余不动。

#### 4.6.2 query key 与查询

```ts
// api/keys.ts 的 agent 下新增（M68 已定义 runs.detail 时复用，不重复）
orchestrator: {
  all: () => [...root, 'agent', 'orchestrator'] as const,
  state: () => [...root, 'agent', 'orchestrator', 'state'] as const,
  messages: () => [...root, 'agent', 'orchestrator', 'messages'] as const,
},
tickets: {
  all: () => [...root, 'agent', 'tickets'] as const,
  lists: () => [...root, 'agent', 'tickets', 'list'] as const,
  list: (filters: Readonly<Record<string, unknown>>) => [...root, 'agent', 'tickets', 'list', filters] as const,
  scope: (ticketId: string) => [...root, 'agent', 'tickets', 'detail', ticketId] as const,
  detail: (ticketId: string) => [...root, 'agent', 'tickets', 'detail', ticketId, 'summary'] as const,
  reports: (ticketId: string) => [...root, 'agent', 'tickets', 'detail', ticketId, 'reports'] as const,
  messagesAll: (ticketId: string) => [...root, 'agent', 'tickets', 'detail', ticketId, 'messages'] as const,
  messages: (ticketId: string, includeSubagents: boolean) => [...root, 'agent', 'tickets', 'detail', ticketId, 'messages', includeSubagents] as const,
  runs: (ticketId: string) => [...root, 'agent', 'tickets', 'detail', ticketId, 'runs'] as const,
},
runs: { detail: (runId: string) => [...root, 'agent', 'runs', 'detail', runId] as const },
specs: {
  all: () => [...root, 'agent', 'specs'] as const,
  detail: (name: string) => [...root, 'agent', 'specs', 'detail', name] as const,
},
modelRoles: () => [...root, 'agent', 'model-roles'] as const,     // M67 已有就复用
deviceLease: () => [...root, 'agent', 'device-lease'] as const,
attentionQuestions: () => [...root, 'agent', 'questions', 'pending', 'all'] as const,
mentionCandidates: (type: string, query: string) => [...root, 'agent', 'mentions', type, query] as const,
```

| 查询（文件） | key | 端点与参数 | 刷新 |
|---|---|---|---|
| `orchestratorStateQuery`（console/api.ts） | `orchestrator.state()` | GET `/api/agent/orchestrator` | 10 秒轮询 + WS |
| 总控消息窗口（use-conversation） | `orchestrator.messages()` | GET `/api/agent/orchestrator/messages?page&size=200` | WS + 发送收尾 |
| `activeTicketsQuery`（console/api.ts） | `tickets.list({status:['open'],page:1,size:20})` | GET `/api/agent/tickets?status=open&page=1&size=20` | WS |
| `attentionTicketsQuery`（console/api.ts） | `tickets.list({status:['waiting','blocked'],page:1,size:50})` | GET `/api/agent/tickets?status=waiting&status=blocked&page=1&size=50` | WS |
| `attentionQuestionsQuery`（console/api.ts） | `attentionQuestions()` | GET `/api/agent/questions?status=pending&page=1&size=50` | 10 秒轮询 + WS |
| 确认 | `pendingConfirmationsQuery`（v2，`keys.agent.pendingConfirmations()`） | GET `/api/confirmations?status=pending&page=1&size=20` | WS（v2） |
| `ticketListInfiniteQuery(filter)`（tickets/api.ts） | `tickets.list({status, size:20})` | GET `/api/agent/tickets?status=…&page&size=20` | WS |
| `ticketDetailQuery(id)` | `tickets.detail(id)` | GET `/api/agent/tickets/{ticket_id}` | 10 秒轮询 + WS |
| 工单消息窗口（use-conversation） | `tickets.messages(id, true)` | GET `/api/agent/tickets/{ticket_id}/messages?include_subagents=true&page&size=200` | WS + 发送收尾 |
| `ticketReportsInfiniteQuery(id)` | `tickets.reports(id)` | GET `/api/agent/tickets/{ticket_id}/reports?page&size=20` | WS |
| `ticketRunsQuery(id)` | `tickets.runs(id)` | GET `/api/agent/runs?ticket_id=…&root_only=true&page=1&size=50` | WS |
| `runDetailQuery(runId)` | `runs.detail(runId)` | GET `/api/agent/runs/{run_id}` | 展开时读，`staleTime` 5 秒 |
| `deviceLeaseQuery`（tickets/api.ts） | `deviceLease()` | GET `/api/agent/device-lease` | 15 秒轮询 + WS |
| `specsQuery`（agents/api.ts） | `specs.all()` | GET `/api/agent/specs` | `staleTime: Infinity` |
| `specDetailQuery(name)` | `specs.detail(name)` | GET `/api/agent/specs/{name}` | `staleTime: Infinity` |
| `modelRolesQuery` | `modelRoles()` | GET `/api/agent/model-roles` | 默认 |

所有查询函数写成 `if (error) throw toApiError(error, response)`；分页信封用 `PageEnvelope`。工单列表的多值 `status` 直接传数组，openapi-fetch 序列化成重复参数。

#### 4.6.3 会话组件

```ts
// conversation/types.ts
export type ConversationScope = { kind: 'orchestrator' } | { kind: 'ticket'; ticketId: string }
export type InboxKind = 'user_message' | 'report' | 'thread_event' | 'system' | 'trigger'
export interface V3Message extends AgentMessage {   // 四个字段都可选：v2 消息窗口的 AgentMessage 可以直接传进来
  run_id?: string | null
  inbox_id?: number | null
  inbox_kind?: InboxKind | null
  inbox_thread_key?: string | null
}
export interface Origin { ticket_id: string | null; ticket_title: string | null; agent: string | null; run_id: string | null }
export interface HumanAction { type: 'confirmation' | 'question' | 'decision'; prompt: string; ref_id: string | null }

// ConversationView.tsx：只收数据，不依赖 useConversation，旧会话查看器也能用
export interface ConversationViewProps {
  scope: ConversationScope
  history: readonly V3Message[]      // 已经过 filterLiveHistory 的可见历史
  mainSessionId: string | null
  specTitles: ReadonlyMap<string, string>
  live?: LiveTurn | null
  showOptimisticUser?: boolean
  queued?: readonly { localId: string; text: string }[]
  resolver?: ConfirmationResolver    // 不传时实时副本里的卡片不渲染（只读场景）
  onQuestionResolved?: (questionId: string) => void
  earlier?: { hasEarlier: boolean; loading: boolean; error: unknown; onLoad: () => void }
  error?: ShownError | null
  emptyState?: ReactNode
  leftovers?: ReactNode              // 工单页：本工单刷新后仍挂着的确认卡、提问卡
}
// LiveTurnView.tsx
export interface LiveTurnViewProps {
  turn: LiveTurn
  showUser: boolean
  specTitles: ReadonlyMap<string, string>
  resolver: ConfirmationResolver
  onQuestionResolved: (questionId: string) => void
}
// UserMessage.tsx
export interface UserMessageProps { text: string; references: readonly string[]; messageId?: number; queued?: boolean }
// InboxEventCard.tsx
export interface InboxEventCardProps { entry: Extract<ConversationEntry, { kind: 'event' }> }
// DelegateToolBar.tsx
export interface DelegateToolBarProps { tool: ToolView; agentTitle: string | null; messageCount: number; children: ReactNode }
// ReportCallout.tsx
export interface ReportCalloutProps { status: string; summary: string; humanAction: HumanAction | null }
// TicketLinkChip.tsx
export interface TicketLinkChipProps { ticketId: string; title: string; created?: boolean }
// card-resolvers.ts
export interface ConfirmationResolver {
  resolve: (id: string, approved: boolean, overrides?: CopilotGrantOptions) => void
  busyFor: (id: string) => 'approve' | 'reject' | null
  error: ShownError | null
  clearError: () => void
}
export function useResolveConfirmation(onResolved?: (id: string) => void): ConfirmationResolver
export function useResolveQuestion(onResolved?: (id: string) => void): (questionId: string) => void
// ConversationComposer.tsx
export interface ConversationComposerProps {
  scope: ConversationScope
  controller: ConversationController
  docked: boolean
  textareaRef: RefObject<HTMLTextAreaElement | null>
  overlay?: ReactNode                // 「回到底部」按钮；提及与命令面板由组件自己叠加
  disabledReason?: string            // 工单已结束时传入：不渲染输入栏，改渲染这句提示
  seed?: { text: string; nonce: number }   // 快捷开场：nonce 变化时用 text 替换草稿并聚焦，不发送
}
```

- `useResolveConfirmation`：POST `/api/confirmations/{confirmation_id}`，body `{approved, overrides?}`；成功失效 `keys.agent.pendingConfirmations()`、`[...keys.agent.confirmations(), id]`、`keys.agent.audits()`，再调 `onResolved(id)`；409 时同样失效。
- `useResolveQuestion`：返回的函数从 `attentionQuestions()` 缓存里摘掉该项、失效 `keys.agent.pendingQuestions()`，再调 `onResolved`。
- `ConversationView` 渲染顺序：`earlier.hasEarlier` 时的「加载更早的消息」按钮（加载中文案 `正在加载…`，失败 `读取更早的消息失败：{原因}`）→ 历史条目（事件卡、用户气泡、Markdown、工具条、汇报条、委派条）→ 实时副本 → 排队气泡（`UserMessage queued`，徽章「排队中」）→ `leftovers` → 错误（v2 `ErrorAlert`）。工具条下的工单胶囊规则见 11 §3.3。指挥台与工单页传 `controller.visible`、`controller.live` 等；`earlier` 由 `controller.hasEarlier`、`loadingEarlier`、`earlierError`、`loadEarlier(scroll.rememberPositionBeforePrepend)` 拼成。
- `DelegateToolBar`：`ToolCallBar` 之下一个 `Disclosure`（默认折叠），标题 `子代理过程（{agentTitle ?? '子代理'}，{messageCount} 条消息）`，内容是子运行的条目（历史用 `subRuns.get(subRunId)` 建条目；实时用子 `LiveRun` 的段）。
- `LiveTurnView`：可选的乐观气泡 → 按 `rootOrder` 渲染每个运行（未观察到开始的运行先画一行 `接着处理之前的消息`）→ 挂起的确认卡（上方一行 `等待你确认，操作还没有执行。`，与 v2 相同）→ 提问卡 → 运行中且没有挂起卡时 `TypingIndicator`。卡片带 `origin` 时上方再加一行 `originLabel`。

#### 4.6.4 指挥台

```ts
// use-attention.ts
export interface AttentionState {
  items: AttentionItem[]
  isPending: boolean
  error: unknown
  refetch: () => void
  resolver: ConfirmationResolver
  onQuestionResolved: (questionId: string) => void
}
export function useAttention(options: { excludeIds: ReadonlySet<string>; onQuestionResolved?: (id: string) => void }): AttentionState
// AttentionList.tsx
export interface AttentionListProps { attention: AttentionState; specTitles: ReadonlyMap<string, string> }
// AttentionPanel.tsx（桌面侧栏）
export interface AttentionPanelProps { attention: AttentionState; specTitles: ReadonlyMap<string, string> }
// AttentionSheet.tsx（手机）
export interface AttentionSheetProps { open: boolean; onOpenChange: (open: boolean) => void; attention: AttentionState; specTitles: ReadonlyMap<string, string> }
// AttentionSummaryBar.tsx
export interface AttentionSummaryBarProps { count: number; onOpen: () => void }
// ActiveTicketsPanel.tsx、ActiveTicketsStrip.tsx
export interface ActiveTicketsProps { tickets: readonly TicketView[]; leaseTicketId: string | null; specTitles: ReadonlyMap<string, string>; isPending: boolean; error: unknown; onRetry: () => void }
```

`ConsolePage`：

- 桌面：`<Page title="指挥台" width="full" hideConfirmationEntry actions={menu} className="h-[calc(100dvh_-_var(--nav-offset)_-_1.5rem)] min-h-[28rem]" contentClassName="flex min-h-0 flex-col">`，内容是两列网格 `grid-cols-[minmax(0,1fr)_22rem]`：左列 `<section aria-label="总控会话">`（卡片，消息区自己滚动，`ConversationComposer docked={false}` 在卡片底部）；右列 `<aside aria-label="指挥台侧栏">` 依次是 `AttentionPanel`、`ActiveTicketsPanel`。
- 手机：`<Page title="指挥台" hideConfirmationEntry actions={menu} toolbar={…}>`，工具条里是 `AttentionSummaryBar`（`items.length > 0` 时）与 `ActiveTicketsStrip`（有进行中的工单时），两者都没有时不传 `toolbar`；正文是会话，`ConversationComposer docked`；`AttentionSheet` 由汇总行打开，`items` 变为 0 时自动关闭。
- `menu` 是 `ActionMenu`（`label="指挥台操作"`）：`重新开始会话`（描述 `当前总控会话归档，进行中的工单不受影响`）、`旧会话`（去 `/more/sessions`）、`运行状态`（去 `/more/status`）。
- `excludeIds` = 实时副本挂着的确认 id 与提问 id。
- 历史为空且没有实时副本时显示快捷开场：标题 `你可以这样开始`，三个按钮 `把日常做了`、`设备连上了吗`、`上次那条流水线为什么失败`，下方一行 `会消耗资源或改动游戏的操作，都会先问过你再执行。`；点按钮把 `seed` 设成 `{ text: 按钮文字, nonce: Date.now() }`，只填进输入栏并聚焦。进行中工单空状态的按钮 `写下要做的事` 调 `textareaRef.current?.focus()`。
- 滚动跟随复用 `useConversationScroll`（桌面传消息区容器，手机传 null）：`headKey` 是第一条历史 id；`tailKey` 由最后一条可见历史 id、实时副本的段数与文字总长、挂起卡 id、排队条数、错误文案、消息查询状态拼成（与 v2 `tailKey` 同法）；`resetKey` 是 `scopeKey`。

#### 4.6.5 工单与代理定义

```ts
// tickets/format.ts
export type TicketStatusValue = 'open' | 'waiting' | 'blocked' | 'done' | 'failed' | 'cancelled'
export interface TicketView { id: string; title: string; kind: string; status: TicketStatusValue; origin: string; summary: string | null; lastReportStatus: string | null; humanAction: HumanAction | null; createdAt: string | null; updatedAt: string | null; endedAt: string | null }
export interface MandateView { scope: string; strength: string; source: string; sender: string | null; riskCeiling: string; createdAt: string | null }
export interface ReportView { id: number; status: string; summary: string; humanAction: HumanAction | null; details: Record<string, unknown> | null; createdAt: string | null }
export interface LeaseView { held: boolean; ticketId: string | null; holderAgent: string; purpose: string; expiresAt: string | null; waiters: number }
export interface RunNode { runId: string; agent: string; kind: string; status: string; finishReason: string | null; steps: number; promptTokens: number; cachedTokens: number; completionTokens: number; costMicros: number; currency: string | null; startedAt: string | null; finishedAt: string | null; children: RunNode[] }
export interface TicketDetailView { ticket: TicketView; mandate: MandateView | null; reports: ReportView[]; workerSessionId: string | null; turnRunning: boolean }
export function toTicketView(raw: unknown): TicketView          // id 取 id，缺了取 ticket_id；其余字段名照 03 §6.1
export function toTicketDetailView(raw: unknown): TicketDetailView  // 读 ticket、mandate、reports（缺了读 recent_reports）、worker_session_id、turn_running
export function toReportView(raw: unknown): ReportView
export function toLeaseView(raw: unknown): LeaseView             // GET /api/agent/device-lease 的 {held, lease?, waiters}
export function toRunNode(raw: unknown): RunNode                 // run_id 缺了取 id；agent_name 缺了取 agent；children 递归
export const TICKET_STATUS_LABELS: Record<TicketStatusValue, string>
export const REPORT_STATUS_LABELS: Record<string, string>
export const RUN_STATUS_LABELS: Record<string, string>
export const RUN_KIND_LABELS: Record<string, string>
export const FINISH_REASON_LABELS: Record<string, string>
export const ORIGIN_LABELS: Record<string, string>
export const STRENGTH_LABELS: Record<string, string>
export const RISK_LABELS: Record<string, string>
export function formatCost(micros: number, currency: string | null): string   // CNY → ¥0.0123；其他 → 0.0123 USD；0 → —
export function formatDuration(startedAt: string | null, finishedAt: string | null, now?: number): string  // 1 分 05 秒；没结束按 now 算
```

文案取值（与 11 §11 一致）：工单状态 `open 进行中`、`waiting 等你处理`、`blocked 受阻`、`done 已完成`、`failed 失败`、`cancelled 已取消`；汇报状态 `progress 进展`、`waiting 等你处理`、`ready 已完成`、`blocked 受阻`、`failed 失败`、`dropped 已放弃`；运行状态 `running 运行中`、`waiting_human 等你处理`、`succeeded 已完成`、`failed 失败`、`interrupted 已停止`、`cancelled 已取消`；运行种类 `orchestrator_turn 总控一轮`、`worker_turn Worker 一轮`、`subagent 子代理`、`trigger_eval 事件研判`、`compaction 摘要压缩`、`memory_extract 记忆整理`、`title 标题`、`watchdog_check Watchdog 巡检`、`eval_case 评测`、`legacy_turn 旧会话一轮`；终止原因 `stop 正常结束`、`report_submitted 已汇报`、`question_unanswered 提问没有回答`、`confirmation_rejected 确认被拒绝`、`confirmation_expired 确认已过期`、`stuck_loop 重复调用被叫停`、`max_steps 达到步数上限`、`budget_exceeded 超出成本预算`、`context_overflow 上下文装不下`、`interrupted 被停止`、`cancelled 工单已取消`、`lease_preempted 设备被接管`、`error 出错`、`recovered_after_restart 服务重启时停止`；来源 `user 你发起`、`trigger 事件`、`goal 目标`、`watchdog Watchdog`、`system 系统`；强度 `explicit 你明确要求的`、`inferred 系统推断的`；风险上限 `none 只做无需确认的操作`、`consume 最高到消耗类操作`、`destructive 最高到破坏类操作`。表里没有的取值原样显示。

组件 props：

```ts
export interface TicketStatusBadgeProps { status: string }                                   // 颜色：open primary、waiting warning、blocked danger、done success、failed danger、cancelled neutral
export interface TicketSummaryCardProps { detail: TicketDetailView; specTitles: ReadonlyMap<string, string>; children?: ReactNode }  // children 放就地的确认卡、提问卡
export interface MandateCardProps { mandate: MandateView }
export interface ReportTimelineProps { ticketId: string; onCompose?: () => void }             // 空状态按钮调 onCompose
export interface ActivityTreeProps { ticketId: string; specTitles: ReadonlyMap<string, string> }
export interface LeaseIndicatorProps { ticketId: string; lease: LeaseView | undefined; holderTitle: string | null }
```

- `TicketsPage`：`Page title="工单"`；工具条 `SegmentedControl`（`ariaLabel="工单范围"`，选项 `active 进行中`、`ended 已结束`、`all 全部`），写 `?status=`（`replace`）；列表行是整行链接（`Link`，高不低于 56px）；底部 `加载更多`。
- `TicketDetailPage`：`useParams().ticketId`；`ticketDetailQuery`；`useConversation(ticketEndpoints(id), { turnRunning: detail.turnRunning, enabled: true })`；`mainSessionId = detail.workerSessionId`。桌面两列（左会话卡、右侧栏依次 `TicketSummaryCard`、`LeaseIndicator`、`ReportTimeline`、`ActivityTree`、`MandateCard`）；手机工具条是状态行与 `SegmentedControl`（`ariaLabel="工单视图"`，`messages 消息`、`reports 汇报`、`activity 活动`，写 `?tab=`）。`leftovers`：从 `pendingConfirmationsQuery` 与 `attentionQuestionsQuery` 里挑 `origin.ticket_id === ticketId` 且不是实时副本里那张的，渲染 `ConfirmationCard`、`QuestionCard`。操作菜单 `ActionMenu`（`label="工单操作"`）：`停止本轮`（`canStop` 时）、`取消工单`（未结束时，先确认）、`查看运行追踪`（去 `/more/runs?ticket_id=<id>`，前置检查 #13 显示 M68 的路由不同就用 M68 的）。工单已结束时 `disabledReason="工单已结束，不能再发消息"`，提示旁边一个 `在指挥台继续` 链接去 `/`。
- `ActivityTree`：根运行列表，每行一个展开按钮（`aria-expanded`），展开后读 `runDetailQuery` 并递归渲染 children（缩进 16px，深度 ≤ 3）；节点一行显示 `{代理中文名} · {种类}`、状态徽章、终止原因，第二行 `{steps} 步 · 输入 {prompt}（缓存 {cached}）/ 输出 {completion} token · {formatCost} · {formatDuration}`；节点末尾链接 `查看追踪` 去 `/more/runs/<runId>`。
- `LeaseIndicator`：`lease.held && lease.ticketId === ticketId` → `正在使用设备 · {purpose} · 剩余 {mm:ss}`，按钮 `释放设备`（`DELETE /api/agent/device-lease`，先确认，成功 Toast `已释放设备`，失效 `deviceLease()`）；`lease.held` 且是别的工单 → `设备正被工单「{holderTitle ?? '其他工单'}」使用`（`holderTitle` 来自那个工单的详情查询）；不持有 → 不渲染。
- `AgentsPage`：`Page title="代理定义" back="/more"`；`Section` 的 `hint` 是 `代理定义随服务版本发布，只能查看。`；按种类四个 `ListGroup`（`总控`、`Worker`、`子代理`、`后台代理`），行 `ListItem title={title} description={description} trailing={'v' + version} to={'/more/agents/' + name}`。
- `AgentDetailPage`：`Page title={title} back="/more/agents"`；区块 `基本信息`（`代码名` 等宽、`种类`、`模型角色`、`实际模型` = `modelRolesQuery` 里同角色的 `档案 {profile} · {model}`、`版本`）、`设备与风险`（设备 `none 不使用设备`、`read 只读设备画面`、`exclusive 独占设备`；风险上限同上表）、`可委派`（链接到各自详情）、`工具`（`{n} 个`，`Chip` 列表，文字 `toolLabel(name) ?? name`，`title` 属性是原名）、`预加载技能`（为空时整块不渲染）、`提示词正文`（`Disclosure` 默认折叠，等宽 `pre`）。404 时 `EmptyState` 标题 `没有这个代理定义`，描述 `返回列表重新选择`，按钮 `返回代理定义`。

#### 4.6.6 旧会话查看器

`LegacySessionsPage` 在 v2 `AgentPage` 上删掉：`draft`、`turn`、`turnError`、`capability`、`createSession`、`checkCapabilities`、`resolveConfirmation`、`resolveQuestion`、`stopTurn`、`revokeGrant`、`send`、`pendingConfirmations`、`pendingQuestions` 两个查询、`Composer`、`QuickPrompts`、`GrantBar`、`CapabilityAlert` 及相关 refs 与 effect；会话详情查询去掉 `refetchInterval`。保留会话列表、`?session_id=`、删除会话、分页（v2 `useMessageWindow`）与滚动。消息渲染改用 `ConversationView`：`history` 传 `useMessageWindow` 的 `items`，`mainSessionId=null`，`scope={ kind: 'orchestrator' }`，不传 `live`、`resolver`，`earlier` 由 `useMessageWindow` 的 `hasEarlier`、`loadingEarlier`、`earlierError`、`loadEarlier` 拼成。这样归档总控会话里的收件箱消息也是事件卡片。页面标题缺省 `旧会话`，`back="/more"`。正文顶部 `InlineAlert tone="info"`：标题 `这些会话只能查看`，正文 `指挥台上线前的会话，以及重新开始前的总控会话。`，`action` 是按钮 `前往指挥台`（`Link` 到 `/`）。没有会话时 `EmptyState`：`还没有旧会话` / `新的会话在指挥台进行` / 按钮 `前往指挥台`。操作菜单只剩手机上的 `会话列表` 按钮。

#### 4.6.7 文案

| 位置 | 原文 |
|---|---|
| 指挥台输入栏 | 占位 `告诉总控要做什么`；读屏标签 `发送给总控` |
| 工单输入栏 | 占位 `给 Worker 发消息`；读屏标签 `发送给 Worker` |
| 状态行 | `正在停止本轮…`、`请先处理待确认的操作`、`请先回答等待中的提问`、`总控正在处理…`、`Worker 正在处理…`、`本轮已停止`、`总控正在处理收到的消息`、`Worker 正在后台执行`、`现在没有在跑的一轮` |
| 区块标题 | `待你处理`（描述 `{n} 件`）、`总控会话`、`进行中的工单`（描述 `{n} 个`）、`汇报`、`活动`、`委托`、`消息` |
| 汇总行 | `{n} 件事等你处理` |
| 空状态 | 只读列表只有标题（与 v2 全局待确认面板的 `还没有待确认的操作` 同一做法）：`还没有要你处理的事`、`还没有运行记录`。能开始的列表带第二行与按钮：`还没有进行中的工单` / `在总控会话里说出要做的事` / 按钮 `写下要做的事`（聚焦输入栏）；`还没有工单` / `在指挥台告诉总控要做什么` / 按钮 `前往指挥台`；`还没有汇报` / `Worker 每轮结束时写一条` / 按钮 `去发消息` |
| 按钮 | `停止本轮`、`取消工单`、`释放设备`、`打开工单`、`全部工单`、`重新开始`、`前往指挥台`、`在指挥台继续`、`加载更多`、`查看追踪` |
| 确认框 | `重新开始总控会话？` / `当前会话会归档，只能在旧会话里查看；进行中的工单不受影响。` / `重新开始`；`取消工单「{title}」？` / `Worker 会停下并释放设备，已提交的流水线不受影响。` / `取消工单`；`释放设备？` / `持有设备的工单下次操作设备时会收到「设备已被接管」。` / `释放设备` |
| Toast | `已重新开始总控会话`、`已取消工单`、`已释放设备` |
| 错误 | `读取工单失败：{原因}`、`读取总控会话失败：{原因}`、`发送消息失败：{原因}`、`取消工单失败：{原因}`、`释放设备失败：{原因}`、`重新开始总控会话失败：总控正在处理消息，先停止本轮`（409 `RUN_ALREADY_ACTIVE`）、`工单已结束，不能再发消息`（409 `TICKET_ENDED`） |
| 事件卡 | 标题 `工单汇报`、`事件`、`系统消息`；链接 `打开工单`、`查看流水线` |
| 实时副本 | `接着处理之前的消息`、`已建工单「{title}」`、徽章 `排队中`、`子代理过程（{代理}，{n} 条消息）` |
| 引用 | `引用 {n} 项` |
| 命令面板与说明卡 | 说明卡标题 `可用命令`；行 `/stop 停止本轮`、`/tickets 打开工单列表`、`/help 查看命令与引用`、`@ 引用工单、知识、干员、关卡或流水线` |
| 提及面板 | 分段 `工单`、`知识`、`干员`、`关卡`、`流水线`；`正在读取…`；`没有匹配的{类型}` |
| 租约 | `正在使用设备 · {用途} · 剩余 {mm:ss}`、`设备正被工单「{标题}」使用`、徽章 `占用设备` |

`TOOL_LABELS` 补：`report: '提交汇报'`、`delegate: '委派子代理'`、`create_ticket: '新建工单'`、`send_to_ticket: '补充工单说明'`、`list_tickets: '列出工单'`、`get_ticket: '读取工单'`、`cancel_ticket: '取消工单'`、`hold_device: '占用设备'`、`release_device: '释放设备'`。

### 4.7 提示词与代理定义

`maa_api/agent/specs/common.md` 在讲事件前缀的那一段后面加一条（只加这一句）：

```markdown
- 用户消息末尾的【引用】段是系统按用户的 @ 提及读出的资料快照，不是用户原话；拿它当线索，事实与数字仍以工具现查为准。
```

这是提示词改动，12 §4 第 6 项的 capability 评测要跑，通过率不低于台账里 M71 的基线。代理定义文件不改。

## 5. 实施任务

### T1 删除 v2 写端点与 `stream_message`

- 写范围：`maa_api/api/routers/agent.py`、`maa_api/agent/runtime.py`、`tests/fakes/legacy_turn.py`（新）、`tests/agent/test_runtime.py`、`tests/agent/test_account_resources_flow.py`、`tests/api/test_agent_router.py`、`docs/v3/14-现行接口清单.md`
- 步骤：
  1. 新建 `tests/fakes/legacy_turn.py`：把 `AgentRuntime.stream_message` 的方法体原样搬成 `stream_legacy_turn(runtime, session_id, content, request)`，`self.` 换成 `runtime.`，需要的模块级名字（`sse_event`、`SSE_KEEPALIVE_SECONDS`、`_SSE_END` 等，以搬过来的代码为准）从 `maa_api.agent.runtime` 导入。
  2. 从 `runtime.py` 删掉 `stream_message`；它独占的私有方法（只被它调用的）保留，测试驱动要用；`rg -nw "stream_message" maa_api` 结果为空。
  3. 测试调用点替换：用一次性脚本把 `tests/` 下所有 `(\w+)\.stream_message\(` 换成 `stream_legacy_turn(\1, `，并在这些文件头部加 `from tests.fakes.legacy_turn import stream_legacy_turn`。
  4. `routers/agent.py` 删除 §4.3 的四个路由函数与只被它们用到的请求模型、`_LockedSessionStreamingResponse`。
  5. `tests/api/test_agent_router.py`：删除 `test_session_message_endpoint_streams_named_sse_events`、`test_session_message_response_releases_lock_if_response_start_send_fails`、`test_session_message_keeps_lock_until_detached_turn_finishes_after_midstream_disconnect`、`test_session_message_route_releases_lock_when_configuration_check_is_cancelled`、`test_interrupt_endpoint_is_idempotent_and_reports_turn_running`、`test_interrupt_route_stops_a_real_runtime_turn`；`test_agent_rest_contract_includes_sessions_confirmations_and_read_only_messages` 里经 POST 建会话的地方改为用 `AgentSessionRepository(...).create(AgentSession(title="test", model="m", base_url="http://x"))` 直接建，删掉发消息与能力检查的断言；新增 `test_removed_v2_session_write_routes`（§6）。
  6. `docs/v3/14-现行接口清单.md` 的 agent 小节删掉这四行，改该小节的「（N 条）」与 §6 开头的总数句（各减 4）。
  7. `.venv/bin/python scripts/dump_openapi.py`。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_runtime.py tests/agent/test_account_resources_flow.py tests/api/test_agent_router.py tests/domain/test_route_inventory.py > /tmp/m72-t1.log 2>&1; echo $?; tail -n 5 /tmp/m72-t1.log` → 退出码 0。
- 完成判据：四个端点不在 `set(app.openapi()['paths'])` 里（GET 同路径的除外）；v2 行为测试数量不减。

### T2 旧会话列表与 wire 字段

- 写范围：`maa_api/db/repositories/agent.py`、`maa_api/api/routers/agent.py`、前置检查 #8 找到的 v3 消息模型文件及构造函数、`tests/api/test_agent_router.py`、M70 的消息路由测试文件（`rg -l "orchestrator/messages" tests` 找到的那个）
- 步骤：按 §4.1、§4.2 实现 `list_page(legacy_only=...)`、`load_inbox_meta`、`session_wire`、`message_wire` 与 v3 模型的两个新字段；v3 消息构造函数同样先批量 `load_inbox_meta` 再填字段。
- 验证：`.venv/bin/python -m pytest -q tests/api/test_agent_router.py $(rg -l "orchestrator/messages" tests) > /tmp/m72-t2.log 2>&1; echo $?` → 0。
- 完成判据：§6 的 T2 用例通过；`dump_openapi.py --check` 在重新生成后通过。

### T3 提及展开

- 写范围：`maa_api/agent/mentions.py`（新）、前置检查 #7 找到的写入点、`maa_api/agent/specs/common.md`、`tests/agent/test_mentions.py`（新）、M70 的总控与工单消息路由测试文件
- 步骤：按 §4.2 的表实现五个展开函数与 `render_reference_block`；接进写入点；改 `common.md`。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_mentions.py $(rg -l "orchestrator/messages|tickets/.*/messages" tests) > /tmp/m72-t3.log 2>&1; echo $?` → 0；`.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m72-eval.log 2>&1; echo $?; tail -n 5 /tmp/m72-eval.log` → 通过率不低于 M71 基线。
- 完成判据：§6 的 T3 用例通过。

### T4 冒烟脚本

- 写范围：`scripts/agent_smoke.py`、`scripts/frontend_smoke.py`
- 步骤：
  1. `_SmokeLLM.stream_chat_completion` 改成按请求分派：工具列表里有名为 `capability_probe` 的函数 → 回探查调用块；消息里有 `image_url` 内容 → 回 `blue`；其余 → 从剩下的回复队列里取下一项。构造函数里两条探查回复移成单独属性。
  2. `_check_builtin_conversation` 改成总控：`GET /api/agent/orchestrator` 拿 `session_id`；`POST /api/agent/orchestrator/messages`（`{"content": "请简短确认本轮 smoke 对话。"}`）返回 `text/event-stream`，正文里 `event: run_start`、`event: text_delta`、`event: run_end` 依次首次出现，且含 `Fake LLM 已完成本轮 smoke 对话。`；`GET /api/agent/orchestrator/messages?page=1&size=20` 的最后两条角色是 `user`、`assistant`；`GET /api/agent/runs?session_id=<sid>&page=1&size=5` 的第一条 `prompt_tokens == 11`、`completion_tokens == 6`；`POST /api/agent/capabilities/check` 返回 404。打印行改成 `[ok] 总控 Fake LLM 对话、SSE v3 事件顺序、消息持久化与运行用量`。
  3. `_install_internal_invoke_probe` 里加冒烟专用路由 `POST /__smoke/legacy-sessions`：用与同函数里相同的 session factory 建一条 `AgentSession(title=…, model="smoke", base_url="http://smoke.invalid")`，返回 201 `{"session_id": …}`；原来 `client.post("/api/agent/sessions", …)` 的地方改调它。
  4. `_check_failure_incident` 里对 `len(fake_llm.requests)` 的绝对计数改成相对计数：进入该步骤时记 `before`，断言新增请求至少 1 条且最后一条带工具列表。
  5. `frontend_smoke.py`：深链检查加 `/tickets/smoke-ticket`、`/more/sessions`、`/more/agents/orchestrator`、`/agent?session_id=x`，都与根文档内容相同；加 `POST /api/agent/capabilities/check` 与 `POST /api/agent/sessions/x/interrupt` 返回 404 JSON（错误码 `NOT_FOUND`）、`POST /api/agent/sessions` 返回 405；加「构建产物里有一个 JS 文件含 `指挥台`」。
- 验证：`.venv/bin/python scripts/agent_smoke.py > /tmp/m72-smoke.log 2>&1; echo $?; tail -n 3 /tmp/m72-smoke.log` → 0，末行 `SMOKE OK`。`frontend_smoke.py` 在 T12 跑（要先构建前端）。
- 完成判据：`agent_smoke.py` 通过。

### T5 前端基础

- 写范围：`web/src/api/keys.ts`、`web/src/realtime/events.ts`、`web/src/realtime/query-bridge.ts`、`web/src/realtime/query-bridge.test.ts`、`web/src/features/agent/stream.ts`、`Composer.tsx`、`messages.ts`、`format.ts`
- 步骤：
  1. 按 §4.6.2 加 key（前置检查 #13 显示已有的就跳过那一项）。
  2. `events.ts` 加 `AgentRunEvent`、`TicketStatusEvent` 两个接口（字段照 04 §4.1）、`ServerEvent` 两个成员、`REALTIME_STATE_CHANNELS` 两项。
  3. `query-bridge.ts` 按 §4.5.7 加分支。
  4. `stream.ts`：`streamMessage` 改名 `streamSse(path, body, signal, onEvent, options = {})`，请求地址用 `path`、请求体 `JSON.stringify(body)`；`options.detach` 为真时在 `response.ok` 之后 `await response.body?.cancel()` 并返回。
  5. `Composer.tsx` 加 §4.2 的三个可选 props，缺省值保持 v2 文案；`onKeyDown` 开头 `if (interceptKeyDown?.(event)) return`。
  6. `messages.ts` 按 `T extends { id: number } = AgentMessage` 泛型化五个导出与两个类型，函数体不变。
  7. `format.ts` 补 9 个工具名。
- 验证：`cd web && pnpm typecheck > /tmp/m72-t5.log 2>&1 && pnpm test --run src/realtime src/features/agent/messages.test.ts > /tmp/m72-t5-test.log 2>&1; echo $?; cd ..` → 0（此时 `AgentPage.tsx` 还引用 `streamMessage`，先在它里面临时改调 `streamSse` 让类型检查通过，T11 会整体改掉）。
- 完成判据：§6 的 query-bridge 新用例通过。

### T6 会话核心

- 写范围：`web/src/features/console/conversation/` 下 `types.ts`、`sse.ts`、`live-turn.ts`、`references.ts`、`transcript-v3.ts`、`card-resolvers.ts`、`use-conversation.ts`、`ConversationView.tsx`、`LiveTurnView.tsx`、`UserMessage.tsx`、`InboxEventCard.tsx`、`DelegateToolBar.tsx`、`ReportCallout.tsx`、`TicketLinkChip.tsx` 与它们的测试；`web/src/features/agent/MessageViews.tsx`（导出 `TypingIndicator`）
- 步骤：按 §4.5.1–§4.5.4、§4.6.3 实现。纯函数先写测试。
- 验证：`cd web && pnpm test --run src/features/console/conversation > /tmp/m72-t6.log 2>&1; echo $?; cd ..` → 0。
- 完成判据：§6 的 `sse.test.ts`、`live-turn.test.ts`、`transcript-v3.test.ts` 全部通过。

### T7 输入辅助

- 写范围：`conversation/commands.ts`、`composer-assist.ts`、`mention-sources.ts`、`ComposerAssist.tsx`、`HelpCard.tsx`、`ConversationComposer.tsx` 与测试
- 步骤：按 §4.5.6 实现；`ConversationComposer` 内部持有 `draft` 与 `mentions` 状态，状态行按 §4.6.7 的优先级计算（正在停止 > 本地这一轮在跑：确认 > 提问 > 处理中 > 本轮已停止 > 后台在跑）；发送按钮：`busy = (live?.running && !draft.trim()) || stopping`，`onStop = canStop ? stop : undefined`。
- 验证：`cd web && pnpm test --run src/features/console/conversation/composer-assist.test.ts > /tmp/m72-t7.log 2>&1; echo $?; cd ..` → 0。
- 完成判据：命令与提及的纯函数用例通过。

### T8 指挥台

- 写范围：`web/src/features/console/` 下 `api.ts`、`attention.ts`、`use-attention.ts`、`AttentionList.tsx`、`AttentionPanel.tsx`、`AttentionSheet.tsx`、`AttentionSummaryBar.tsx`、`ActiveTicketsPanel.tsx`、`ActiveTicketsStrip.tsx`、`ConsolePage.tsx`、`test-utils.tsx`、`attention.test.ts`、`ConsolePage.test.tsx`
- 步骤：
  1. `test-utils.tsx`：从 `AgentPage.test.tsx` 抄 `json`、`event`、`sse`、`heldStream`，写 `createConsoleApi(options)`，按服务端语义模拟 §4.6.2 表里的全部端点，记录请求（`method`、`path`、`query`、`body`），每个端点可由 options 覆盖；再写 `makeV3Message`、`makeTicket`、`makeTicketDetail`、`makeRun`、`renderAt(path, routes)`。
  2. 按 §4.5.5、§4.6.4 实现页面。
  3. 把 §6 标注「移植」的用例从 `AgentPage.test.tsx` 改写到 `ConsolePage.test.tsx`：事件名换成 SSE v3（`message_start` → `run_start`，`message_end` → `run_end`，其余同名），每个事件带 `run_id`；去重断言改成「同一句回复在页面上只出现一次」。
- 验证：`cd web && pnpm test --run src/features/console > /tmp/m72-t8.log 2>&1; echo $?; cd ..` → 0。
- 完成判据：§6 的指挥台用例全部通过。

### T9 工单页

- 写范围：`web/src/features/tickets/` 全部文件与测试
- 步骤：按 §4.6.5 实现；适配函数按前置检查 #4 抄下的属性名核对，名字不同只改适配函数并记进台账。
- 验证：`cd web && pnpm test --run src/features/tickets > /tmp/m72-t9.log 2>&1; echo $?; cd ..` → 0。
- 完成判据：§6 的工单用例全部通过。

### T10 代理定义页

- 写范围：`web/src/features/agents/` 全部文件与测试
- 步骤：按 §4.6.5 实现。
- 验证：`cd web && pnpm test --run src/features/agents > /tmp/m72-t10.log 2>&1; echo $?; cd ..` → 0。
- 完成判据：§6 的代理定义用例通过。

### T11 导航、路由、旧会话与设置

- 写范围：`components/layout/nav.ts`、`SideNav.tsx`、`layout.test.tsx`、`routes/router.tsx`、`routes/more/index.tsx`、`routes/more/more.test.tsx`、`routes/dashboard/index.tsx`、`features/agent/` 下 §4.2 表列出的文件、`features/settings/SchemaSection.tsx`、`settings.test.tsx`、`AgentCapabilityCheck.tsx`
- 步骤：
  1. 按 §4.6.1 改导航、侧栏、「更多」页、路由与运行状态页。
  2. `git mv web/src/features/agent/AgentPage.tsx web/src/features/agent/LegacySessionsPage.tsx`，`git mv web/src/features/agent/AgentPage.test.tsx web/src/features/agent/LegacySessionsPage.test.tsx`。
  3. 按 §4.6.6 改查看器；按 §4.2 表改 `types.ts`、`SessionList.tsx`、`MessageViews.tsx`、`StatusAlerts.tsx`。
  4. 查看器测试：保留 §6 列出的用例（改名照表），删除其余；补三个新用例。
  5. `SchemaSection.tsx` 的 `{ to: '/', label: '查看设备状态' }` 改 `/more/status`，`settings.test.tsx` 约 427 行的断言同步。
  6. `layout.test.tsx` 与 `more.test.tsx` 按 §6 改。
  7. 前置检查 #12 显示 `AgentCapabilityCheck.tsx` 仍调 `capabilities/check` 时：改调 `POST /api/agent/model-profiles/{name}/probe`（`name='default'`），结果三枚徽章 `工具调用：可用|不可用`、`视觉输入：可用|不可用|未知`、`结构化输出：可用|不可用|未知`（值为 `null` 写未知）与一行 `响应 {latency_ms} ms`；按钮文案 `检查模型能力` 与失败文案不变；`settings.test.tsx` 里对应断言改成新端点与新文案。否则跳过并记台账。
- 验证：`cd web && pnpm typecheck > /tmp/m72-t11-tc.log 2>&1 && pnpm test --run > /tmp/m72-t11.log 2>&1; echo $?; cd ..` → 0；`rg -n "streamMessage\|capabilities/check\|AgentPage" web/src --glob '!web/src/types/**'` → 没有输出。
- 完成判据：全量 vitest 通过。

### T12 全量验证、文档与台账

- 写范围：`docs/v3/11-前端指挥台.md`（只在实现与它不一致时改）、`docs/v3/13-决策记录.md` §8（有偏离时）、`docs/v3/ledgers/m72-console-frontend.md`（新）
- 步骤：按 §7 跑全部命令；写台账（含 capability 评测的通过率、前置检查 #4 抄下的属性名、#12/#13 的分支选择）。
- 验证：§7 全部通过。
- 完成判据：12 §4 的十项都满足。

## 6. 测试清单

后端：

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/api/test_agent_router.py` | `test_removed_v2_session_write_routes` | POST `/api/agent/sessions` 405；POST `/api/agent/sessions/x/messages` 405；POST `/api/agent/sessions/x/interrupt` 404 且 `error.code == "NOT_FOUND"`；POST `/api/agent/capabilities/check` 404；`set(app.openapi()['paths'])` 里没有后两条路径，`/api/agent/sessions` 下没有 `post` |
| 同上 | `test_legacy_session_list_hides_v3_sessions` | 库里有 chat、incident、orchestrator（活动）、orchestrator（已归档）、worker、subagent 各一条；列表只返回 chat、incident 与已归档的 orchestrator，`total == 3`，每项带 `kind` |
| 同上 | `test_message_wire_exposes_run_and_inbox_fields` | 一条普通消息四个新字段为 `null`（`run_id` 有值时照写）；一条 `inbox_id` 指向 `kind="report"`、`thread_key="ticket:t1"` 的收件箱项，wire 里 `inbox_kind == "report"`、`inbox_thread_key == "ticket:t1"` |
| M70 消息路由测试文件 | `test_orchestrator_messages_carry_inbox_kind_and_thread_key` | v3 列表里收件箱消息的两个新字段正确，普通用户消息为 `null` |
| `tests/agent/test_mentions.py` | `test_render_reference_block_formats_each_type` | 五类各一个提及（假 `knowledge_service`、`box_service`、`stage_resolver`，真库里的工单与流水线）；输出以 `\n\n【引用】\n` 开头，五行分别以 `- 工单「`、`- 知识「`、`- 干员 `、`- 关卡 `、`- 流水线 ` 开头 |
| 同上 | `test_failed_expansion_reads_as_read_failed` | 展开函数抛异常、工单不存在时那一行是 `（读取失败）`，其余行照常，函数不抛 |
| 同上 | `test_operator_not_in_account_data` | `ACCOUNT_DATA_NOT_FOUND` 写成 `干员 {label}：账号数据里没有这名干员` |
| 同上 | `test_stage_candidates_and_not_found` | 两个候选时写候选数与前三个 stageId；零个时写 `本地关卡索引里找不到` |
| 同上 | `test_mentions_are_deduplicated_and_capped` | 同一 (type, id) 两次只展开一次；10 个不同提及只展开 8 个，末行 `- 另有 2 条引用没有展开` |
| 同上 | `test_empty_mentions_return_empty_block` | `None` 与 `[]` 都返回 `""` |
| 同上 | `test_long_line_is_truncated` | 知识正文很长时该行 400 字、以 `…` 结尾 |
| M70 总控消息路由测试文件 | `test_orchestrator_message_stores_expanded_mentions` | 带一个知识提及发送；读回的用户消息正文以原文开头、含 `【引用】` 与 `知识「` |
| M70 工单消息路由测试文件 | `test_ticket_message_summary_excludes_reference_block` | 带提及发工单消息；总控收件箱那条 `system` 项的摘要不含 `【引用】` |

前端（vitest，路径相对 `web/src/`）：

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `realtime/query-bridge.test.ts` | `invalidates the orchestrator state and messages when an orchestrator run finishes` | `run_finished`（`kind=orchestrator_turn`）失效 `orchestrator.state()` 与 `orchestrator.messages()` |
| 同上 | `routes message_created by the cached orchestrator session id` | 缓存里有总控 `session_id`，`kind` 为空的 `message_created` 只失效总控消息 |
| 同上 | `invalidates the ticket scope queries for worker runs` | `ticket_id` 非空时失效该工单的详情、运行与消息，不失效 `tickets.scope` 整片 |
| 同上 | `refreshes ticket lists, detail and reports on ticket_status events` | 三个事件各自的失效集合 |
| 同上 | `refreshes the device lease on lease_changed` | 失效 `deviceLease()` |
| 同上 | `reloads v3 conversation state after a reconnect` | `subscribed` 额外失效 `orchestrator.all()`、`tickets.lists()`、`deviceLease()` |
| `features/console/conversation/sse.test.ts` | `normalizes every SSE v3 event into a typed event` | 04 §3 表里 M70 的 14 个事件逐个转换 |
| 同上 | `turns unknown or run-less events into no-ops` | 未知名字与缺 `run_id` 的 `text_delta` 都是 `unknown` |
| 同上 | `reads origin from confirmation and question events` | `origin` 四个字段 |
| `features/console/conversation/live-turn.test.ts` | `appends text deltas and tool calls to the run they belong to` | 两个运行交错的事件各归各 |
| 同上 | `marks runs joined midway as partial` | 没有 `run_start` 的运行 `observedStart=false` 且进 `rootOrder` |
| 同上 | `nests a subagent run under the matching delegate call` | 两个并行 `delegate` 按 agent 与 task 分别配对 |
| 同上 | `re-keys a delegate call by the run_id in its result` | 结果里的 `run_id` 覆盖配对 |
| 同上 | `clears pending cards when their results arrive` | 确认与 `ask_user` 的结果分别清卡 |
| 同上 | `stops the turn and drops pending cards on interrupted` | 根运行被中断时 `running=false`、两张卡清空 |
| 同上 | `records report and ticket segments` | 汇报段与工单段 |
| 同上 | `filters history produced by fully observed runs only` | `filterLiveHistory` 的三条规则（含用户消息从不隐藏） |
| 同上 | `hides the optimistic bubble once the persisted user message arrives` | `userMessageArrived` 忽略【引用】段与收件箱消息 |
| `features/console/conversation/transcript-v3.test.ts` | `renders inbox messages as event entries` | `inbox_kind` 与前缀两种来源；链接规则；当前工单不出链接 |
| 同上 | `splits the reference block off user messages` | `text` 与 `references` |
| 同上 | `groups subagent messages under the delegate call` | `mainSessionId` 之外的消息进 `subRuns`，`delegate` 条目带 `subRunId` |
| 同上 | `renders report calls as report entries` | `report` 调用不出工具条 |
| `features/console/conversation/composer-assist.test.ts` | `detects a slash command only at the start of the draft` | `findAssistToken` 与 `matchSlashCommands` |
| 同上 | `detects an @ token before the caret` | 词首才算；24 字以内 |
| 同上 | `inserts a mention and keeps only mentions still in the text` | `insertMention` 与 `activeMentions`（去重、上限 8） |
| 同上 | `parses exact slash commands and leaves other slashes as text` | `/stop` 是命令，`/stop 一下` 不是 |
| `features/console/attention.test.ts` | `orders confirmations, questions, blocked and waiting tickets` | 排序键 |
| 同上 | `drops a ticket whose confirmation or question is already listed` | `ref_id` 与 `origin.ticket_id` 两种去重 |
| 同上 | `excludes the cards shown in the live turn` | `excludeIds` |
| `features/console/ConsolePage.test.tsx` | `shows the orchestrator history with inbox event cards and ticket links` | 事件卡标题与「打开工单」链接；用户气泡的「引用 1 项」 |
| 同上 | `streams a reply and shows it exactly once after the live copy is dropped` | 流结束、历史到达后同一句只出现一次 |
| 同上 | `waits for an inline confirmation, then shows the reply exactly once after the live copy is dropped` | 移植 AG2 |
| 同上 | `keeps the live copy on screen until the refreshed history has arrived` | 移植 AG2 |
| 同上 | `keeps the turn and its card when the stream ends while the confirmation is still pending` | 移植 AG2 |
| 同上 | `renders one copy of the turn when history refreshes while the stream is still live` | 移植 M59，判据是运行 id |
| 同上 | `drops the live copy when the pending confirmation was resolved outside this page` | 移植 M59 |
| 同上 | `keeps a pending confirmation when an unrelated tool reports its result` | 移植 M59 |
| 同上 | `asks through a card inside the live turn and keeps the same turn going after the answer` | 移植 M21 |
| 同上 | `ends the turn with the server closing text when the question times out` | 移植 M21 |
| 同上 | `keeps the confirmation wording when a confirmation and a question are pending at once` | 移植 M21 |
| 同上 | `keeps a failed turn visible after the live copy is dropped and links LLM errors to the settings` | 移植 AG2 |
| 同上 | `puts the draft back when the message cannot be sent` | 移植 |
| 同上 | `fills the composer from a quick prompt without sending it` | 移植 AG5：历史为空时三个快捷开场，点选只填输入栏 |
| 同上 | `sends with Enter on a mouse-and-keyboard device and keeps Shift+Enter for a new line` | 移植 AG4 |
| 同上 | `treats Enter as a new line on touch devices and sends only from the button` | 移植 AG4 |
| 同上 | `turns the send button into a stop button during a turn and wraps the turn up after stopping` | 移植 M22，端点换成 `/api/agent/orchestrator/interrupt` |
| 同上 | `restores the stop entry from turn_running when the page is reopened` | 移植 M22，数据来自 `GET /api/agent/orchestrator` |
| 同上 | `ends the turn without an error when another client interrupts it` | 移植 M22 |
| 同上 | `shows the media cards a live turn produced, then the same cards rebuilt from history` | 移植 M23 |
| 同上 | `marks a run joined midway and shows its later output once` | 部分观察的运行：块前小字，历史刷新不重复 |
| 同上 | `queues a second message while a reply is still streaming` | 第二条请求立即断开读流；「排队中」气泡在进历史后消失 |
| 同上 | `shows the ticket created by the orchestrator inside the live turn` | `ticket_created` → 「已建工单「日常」」链接到 `/tickets/<id>` |
| 同上 | `lists confirmations, questions and waiting tickets with their origin` | 桌面侧栏三类卡片与来源行，顺序正确 |
| 同上 | `opens the attention sheet from the summary bar on phones` | 汇总行文案、面板打开、处理完最后一张自动收起 |
| 同上 | `lists open tickets and marks the one holding the device` | 「占用设备」徽章 |
| 同上 | `runs /stop, /tickets and /help locally without sending a message` | 三个命令都不产生 POST 消息请求 |
| 同上 | `sends selected @ mentions with the message` | 请求体 `mentions` 只含正文里还在的提及 |
| 同上 | `resets the orchestrator session after confirmation` | 确认框、POST reset、Toast；409 时就地提示 |
| `features/tickets/format.test.ts` | `labels ticket, report and run statuses in Chinese` | 各表取值；未知值原样 |
| 同上 | `formats run cost and duration` | `formatCost`、`formatDuration` |
| `features/tickets/TicketsPage.test.tsx` | `lists active tickets with status, worker title and the last summary` | 默认 `status=open,waiting,blocked` |
| 同上 | `switches between active, ended and all tickets` | 分段写 `?status=`，请求参数随之变化 |
| 同上 | `loads more tickets until the total is reached` | 按 `total` 停止 |
| 同上 | `shows an empty state with a link to the console` | 文案与链接 |
| `features/tickets/TicketDetailPage.test.tsx` | `shows the summary, mandate and human action of a waiting ticket` | 概要、委托、就地的确认卡 |
| 同上 | `renders worker messages with subagent runs nested under delegate calls` | 折叠标题与展开后的子代理消息 |
| 同上 | `streams a worker reply after sending a message in the ticket` | POST 到工单消息端点；实时副本与汇报条 |
| 同上 | `disables the composer when the ticket has ended` | 没有输入框；提示与「在指挥台继续」 |
| 同上 | `shows the report timeline newest first` | 顺序与流水线链接 |
| 同上 | `expands the activity tree and links each run to its trace` | 展开读运行详情；链接 `/more/runs/<id>` |
| 同上 | `shows the lease held by this ticket and releases it after confirmation` | DELETE device-lease、Toast |
| 同上 | `cancels the ticket after confirmation` | POST cancel、Toast；409 时显示错误 |
| 同上 | `drops the live copy of another ticket after navigation` | 移植 M59 |
| `features/agents/agents.test.tsx` | `groups agent definitions by kind` | 四组与版本 |
| 同上 | `shows tools, delegates and the prompt of one definition` | 工具中文名、委派链接、实际模型、提示词默认折叠 |
| 同上 | `shows a not-found state for an unknown definition` | 404 文案与返回按钮 |
| `features/agent/LegacySessionsPage.test.tsx` | 保留并改名：`shows the history with collapsed tool evidence (AG1)`（原 AG1 首例去掉授权与能力部分）、`shows a standalone tool result that has no matching call as a collapsed bar (AG1)`、`names the knowledge and pipeline tools in Chinese in the transcript (M18)`、`reads the last page of a long conversation first and loads earlier pages above it, keeping the scroll position (AG3)`、`switches sessions from the session sheet and writes ?session_id= (AG5)`、`deletes a session only after confirmation (AG5)`、`shows a get_screenshot archive from history as an attachment inside its tool card (M23)`、`rebuilds media cards from history (M23)`（原 M23 首例只留历史部分） | 断言照原用例 |
| 同上 | `explains that old sessions are read-only and links to the console` | 说明与「前往指挥台」 |
| 同上 | `has no composer, new-session or stop entry` | 没有输入框、没有「新建会话」与「停止本轮」 |
| 同上 | `renders archived orchestrator sessions with inbox event cards` | 徽章「已归档的总控」；事件卡 |
| `components/layout/layout.test.tsx` | `shows five labelled destinations in the bottom tab bar on phones` | `{指挥台:'/', 工单:'/tickets', 任务:'/tasks', 日志:'/logs', 更多:'/more'}`，数量 5，指挥台 `aria-current` |
| 同上 | `switches to a sidebar that also lists the secondary destinations on desktop` | 标签换成 `指挥台、工单、任务、日志、更多、代理定义、旧会话、运行状态、热更新、Agent 审计、API 调试台、设置` |
| `routes/more/more.test.tsx` | `groups the secondary destinations and no longer lists schedules` | 组标题 `Agent`、`运维`、`系统`；新入口的链接 |
| 同上 | `redirects the old agent address to the read-only sessions page` | `/agent?session_id=s1` 落到 `/more/sessions?session_id=s1` |
| `features/settings/settings.test.tsx` | 原「查看设备状态」用例 | 链接改为 `/more/status`；T11 第 7 步执行时能力检查用例改成 probe 端点 |

v2 `AgentPage.test.tsx` 里没有出现在上表的用例（新建会话、会话标题、会话列表刷新、token 计数、409 `AGENT_SESSION_BUSY`）随写端点一起删除。

## 7. 验收

```bash
.venv/bin/python -m pytest -q > /tmp/m72-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m72-pytest.log
.venv/bin/python scripts/dump_openapi.py && (cd web && pnpm gen:api) && .venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/m72-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck > /tmp/m72-tc.log 2>&1 && pnpm test --run > /tmp/m72-vitest.log 2>&1 && pnpm build > /tmp/m72-build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/m72-fsmoke.log 2>&1; echo $?; tail -n 1 /tmp/m72-fsmoke.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m72-asmoke.log 2>&1; echo $?; tail -n 1 /tmp/m72-asmoke.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m72-eval-reg.log 2>&1; echo $?
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m72-eval-cap.log 2>&1; echo $?
.venv/bin/python scripts/generate_api_guide.py --check; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
git diff --check; echo $?
```

期望：pytest 与 dev 起点相比不新增失败；其余退出码 0；两个冒烟末行 `SMOKE OK`；capability 通过率不低于 M71 基线；断链 `BROKEN 0`。M73 的 `orchestrator_smoke.py` 还不存在，不跑。

补充检查（不作硬性验证，结果记台账）：本机起服务、配好 LLM，手机宽度打开 `/`：Tab 五项；说「设备连上了吗」看到流式回复；说「把日常做了」看到「已建工单」胶囊，进工单页能看到 Worker 的消息与汇报；`/agent?session_id=<v2 会话 id>` 落到旧会话查看器。

## 8. 风险与回滚

- 实时副本去重出错导致重复或丢内容：判据集中在 `filterLiveHistory` 与 `userMessageArrived` 两个纯函数，有单测；移植的 M59 用例覆盖历史刷新。
- M70 的 SSE 在接入时重放已发出的 `text_delta`：文字会重复。§10 要求停下来问。
- 删除 `stream_message` 漏掉调用方：前置检查 #11 与 T1 的 `rg` 兜底；测试驱动保住 v2 行为覆盖。
- `GET /api/agent/sessions` 只列旧会话后，有别的代码依赖它看全部会话：全量测试会暴露，出现时停下来问。
- 回滚：`git revert -m 1 <merge>`；没有迁移。

## 9. 文档同步

- `docs/v3/14-现行接口清单.md`：agent 小节删 4 行，小节计数与总数各减 4。错误码、工具、表的数量不变。
- 实现与 [11](../11-前端指挥台.md) 不一致的地方改 11 并在 [13 §8](../13-决策记录.md) 记一条。
- 「需要补进契约的名字」一节交给主 agent 合并进 02–05，本里程碑不直接改 02–05。

## 10. 停下来问

- 前置检查 #3、#4、#5、#6 有缺项（M70、M71 没按 04 落地）。
- `rg -nw "stream_message" maa_api` 显示路由之外还有调用方。
- M70 的总控或工单消息流在接入时会重放当前运行已经发出的事件（`text_delta` 会重复）。
- `POST /api/agent/model-profiles/default/probe` 不被允许（404 或 409），而 T11 第 7 步需要它。
- capability 评测通过率低于 M71 基线。
- 改 `list_sessions` 之后有非本里程碑的测试失败。
- 要新增端点、错误码、配置项或依赖才能完成某一步。

## 需要补进契约的名字

| 名字 | 用途 | 应补进 |
|---|---|---|
| 消息 wire 字段 `inbox_kind`、`inbox_thread_key` | 事件卡片的分类与链接 | 04 §2.2（`list_orchestrator_messages`、`list_ticket_messages` 的响应）、03 §6.6 的说明 |
| v2 `session_wire` 的 `kind`、`archived_at`；`message_wire` 的 `run_id`、`inbox_id`、`inbox_kind`、`inbox_thread_key` | 旧会话查看器 | 04 §2.2 的 M72 行 |
| `GET /api/agent/sessions` 只列 chat、incident 与归档的总控会话 | 旧会话查看器 | 04 §2.2 的 M72 行、02 §7 |
| `maa_api/agent/mentions.py`：`render_reference_block`、`EXPANDERS`、`MentionContext`、各类的输出格式 | 提及展开 | 02 §4.1、05 §2.5 |
| `tests/fakes/legacy_turn.py::stream_legacy_turn` | 继续驱动 v2 行为测试 | 02 §4.3 |
| 前端目录 `features/console/conversation/` | 指挥台、工单页、旧会话查看器共用的会话组件 | 02 §4.2 |
| `keys.agent.orchestrator`、`tickets`、`runs.detail`、`specs`、`modelRoles`、`deviceLease`、`attentionQuestions`、`mentionCandidates` | query key | 11 §6.3（已写） |
| `common.md` 新增的【引用】说明 | 提示词分层第 2 段 | 05 §7 |
| 04 §2.2 写的「05 §4.4」应为「05 §2.5」 | 契约笔误 | 04 §2.2 |

## 附录 A：前置检查脚本

A1（端点）：

```bash
.venv/bin/python - <<'EOF'
import json
paths = json.load(open("web/openapi.json"))["paths"]
need = [
    ("get", "/api/agent/orchestrator"), ("get", "/api/agent/orchestrator/messages"),
    ("post", "/api/agent/orchestrator/messages"), ("post", "/api/agent/orchestrator/interrupt"),
    ("post", "/api/agent/orchestrator/reset"), ("get", "/api/agent/tickets"),
    ("get", "/api/agent/tickets/{ticket_id}"), ("get", "/api/agent/tickets/{ticket_id}/reports"),
    ("get", "/api/agent/tickets/{ticket_id}/messages"), ("post", "/api/agent/tickets/{ticket_id}/messages"),
    ("post", "/api/agent/tickets/{ticket_id}/interrupt"), ("post", "/api/agent/tickets/{ticket_id}/cancel"),
    ("get", "/api/agent/device-lease"), ("delete", "/api/agent/device-lease"),
    ("get", "/api/agent/runs"), ("get", "/api/agent/runs/{run_id}"),
    ("get", "/api/agent/specs"), ("get", "/api/agent/specs/{name}"),
    ("get", "/api/agent/model-roles"), ("post", "/api/agent/model-profiles/{name}/probe"),
]
missing = [f"{m.upper()} {p}" for m, p in need if m not in paths.get(p, {})]
print("\n".join(missing))
print(f"MISSING {len(missing)}")
EOF
```

A2（响应模型）：

```bash
.venv/bin/python - <<'EOF'
import json
schemas = json.load(open("web/openapi.json"))["components"]["schemas"]
for name in ["TicketSummary", "TicketDetail", "RunSummary", "RunDetail", "SpecSummary", "SpecDetail", "Mention"]:
    props = sorted((schemas.get(name) or {}).get("properties", {}))
    print(name, props if props else "MISSING")
EOF
```
