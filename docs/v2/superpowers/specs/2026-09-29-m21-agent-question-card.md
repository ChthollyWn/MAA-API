# M21 · Agent 主动提问与参数选择卡片

> 关联：[11-Agent模块设计](../../11-Agent模块设计.md) §3.2、§5、§8，[09-前端重构方案](../../09-前端重构方案.md) §6，[13-决策记录](../../13-决策记录.md) §8.26。实施分支 `m21-agent-question-card`，从 `refactor/v2` 新建。

## 1. 目标与用户裁决

用户裁决（2026-09-29）：

1. 「让 agent 自己创建任务时，关于参数的选择，应该主动发送选择卡片，等待用户的选择后开始任务，不是让用户自己发消息回复。」
2. 卡片可见范围：**对话页内 + 本会话待答卡**（刷新/切页回来仍能看到），不动全局「待确认」面板与标题栏胶囊。
3. 用户没回答时：**10 分钟后过期，直接结束本轮**（模型不再有发言机会）。
4. 选项能力：**单选 + 多选**（工具参数带 `multiple`）。

现状：Agent 遇到需要用户裁决的参数（渠道、关卡、次数策略）时只能在回复正文里写「请确认走哪个渠道」，用户必须打一段字回复，模型再解析自然语言。系统提示词里那句「不确定的参数要向用户询问，不要猜测」没有任何机制支撑，于是「询问」就等于「在正文里问」。

本里程碑引入**提问（question）**这个与「人工确认（confirmation）」并列的一等交互：Agent 调用新工具 `ask_user` 发出带选项的卡片，本轮挂起等待；用户点选提交后，选择以工具结果的形式回到模型，同一轮继续执行；超时或用户跳过则本轮直接结束。全部交互落库，刷新页面后待答卡仍在。

## 2. 范围

### 2.1 本里程碑

1. 新工具 `ask_user`（group `interaction`，SAFE，`read_only=False`，`model_visible=True`），参数 `question`、`options[]`、`multiple`。
2. 新表 `agent_question` + 迁移 0015 + `QuestionService`（创建、等待、回答、跳过、超时、重启清理、广播）。
3. 新 REST：`GET /api/agent/questions`、`GET /api/agent/questions/{id}`、`POST /api/agent/questions/{id}`、`POST /api/agent/questions/{id}/skip`。
4. 新 WS 事件 `question_request` / `question_resolved`（仅供前端失效查询，不做全局面板）。
5. Runtime：识别 `ask_user`、发 SSE `question_required`、阻塞等待、把选择回填成 tool 消息；超时/跳过结束本轮并写明原因。
6. 系统提示词：需要用户在若干选项间裁决时**必须**调用 `ask_user`，不得在正文里让用户回复；拿到选项即视为用户裁决，不再二次确认。
7. 前端：对话页提问卡（单选/多选、提交、跳过、超时态）、本会话待答卡（刷新恢复）、输入栏状态行、WS 失效。
8. 生成物与文档同步（OpenAPI、TS 类型、API 指南、docs/04、05、09、11、12、13、README）。

### 2.2 不做

- **不动确认链路**：`ConfirmationService`、`confirmation` 表、`/api/confirmations`、全局待确认面板与标题栏胶囊、Web Push 全部保持不变。提问不是确认：它没有风险等级、不授权任何操作、不占「待确认」计数。
- **不做自由文本答案**：卡片只有选项（用户裁决 §1.4）。选项里没有的答案由模型重新发一张卡或改用正文提问。
- **不做 Web Push 与通知通道**：用户裁决的可见范围只在对话页内。
- **不改自动诊断会话**：`INCIDENT_ALLOWED_TOOLS` 是白名单，天然不含 `ask_user`（无人值守的诊断不该挂起等人）。
- **不改 REST 工具调用语义**：`ask_user` 经 `/api/agent/tools/{name}/invoke` 调用时直接失败（REST 请求没法等 10 分钟），错误信息说明只能由内置 Agent 在会话内使用。

## 3. 设计

### 3.1 工具 `ask_user`

```python
class AskUserOption(BaseModel):        # extra="forbid"
    label: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=200)

class AskUserParams(BaseModel):        # extra="forbid"
    question: str = Field(min_length=1, max_length=500)
    options: list[AskUserOption] = Field(min_length=2, max_length=6)
    multiple: bool = False
```

- description（给模型看）写清三件事：什么时候用（需要用户在若干选项间做决定）；怎么用（把选项写成 label，最多 6 个，不要把答案写进 question）；拿到结果就照做（返回值里的 `selected` 就是用户裁决）。
- 注册在 `tools/interaction.py`，`build_registry()` 里加一组。REST handler 抛 `INVALID_PARAMETER`（400）：「ask_user 只能由内置 Agent 在会话内调用」。
- **`read_only=False`**：提问必须与写操作串行。批次里出现 `ask_user` 时整批走串行路径，排在它后面的 `submit_pipeline` 等用户答完才会执行——这正是「先问再动手」。代价是 `[get_screenshot, ask_user]` 这类批次也串行，可接受。

### 3.2 数据模型 `agent_question`（docs/04 §5.17）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | str(36) PK | uuid |
| `session_id` | str(36) FK `agent_session.id` ON DELETE CASCADE | 归属会话 |
| `tool_call_id` | str(64) NOT NULL | 模型那次 `ask_user` 调用的 id，前端据此挂到工具条上 |
| `question` | text NOT NULL | 问题正文 |
| `options` | JSON NOT NULL | `[{"label": ..., "description": ...}]` |
| `multiple` | bool NOT NULL | 是否多选 |
| `status` | str(16) NOT NULL | `pending` / `answered` / `skipped` / `expired` |
| `selection` | JSON NULL | 选中项 label 列表 |
| `answered_by` | str(32) NULL | `web` / `system` |
| `audit_id` | FK `agent_audit.id` ON DELETE SET NULL | 审计行 |
| `expires_at` | datetime NOT NULL | 创建时间 + `agent.question_timeout_seconds` |
| `created_at` / `answered_at` | datetime | |

索引：`(status, expires_at)`（超时扫描）、`(session_id, status, created_at)`（会话待答列表）。

新增 `QuestionStatus(StrEnum)`：`PENDING` / `ANSWERED` / `SKIPPED` / `EXPIRED`。

### 3.3 `QuestionService`（`maa_api/agent/questions.py`）

与 `ConfirmationService` 同构但独立：落库先行、内存 `asyncio.Event` 只做进程内唤醒、重启清理。

```python
QuestionService(session_factory, *, broadcast=None, timeout_seconds=600, clock=utcnow)
```

- `create(*, session_id, tool_call_id, question, options, multiple, request_id=None) -> (wire, audit_id)`：落 `agent_question`（pending）+ `agent_audit`（pending，`tool_name="ask_user"`，`risk_level=none`，`arguments` 为问题与选项），广播 `question_request`，返回 wire。
- `wait(question_id, *, poll_seconds=0.25) -> wire`：等进程内事件，事件没等到就按 `expires_at` 剩余时间超时；超时后自己调 `expire_due()` 兜底，返回终态 wire。**不抛异常**——终态由调用方判断。
- `answer(question_id, *, selected, answered_by="web") -> wire`：CAS `pending → answered`；校验 `selected` 非空、去重后每一项都在 `options.label` 里、单选时只允许一项；写 `selection` / `answered_at`，审计置 SUCCESS（`result_summary` = 「用户选择：X、Y」），广播 `question_resolved`。
- `skip(question_id, *, answered_by="web") -> wire`：CAS `pending → skipped`，审计置 REJECTED（`result_summary` = 「用户跳过了提问」），广播 `question_resolved`。
- `get` / `list(session_id=None, status=pending, page, size)`。
- `start(scan_interval_seconds=10)` / `close()` / `expire_due()` / `reset_after_restart()`：后台扫描把过期的 pending 置 `expired`（审计 EXPIRED，广播 `question_resolved`）；启动时把所有遗留 pending 直接置 `expired`（进程重启后那一轮已经没了）。

wire 形状（REST 与 SSE 共用）：

```json
{
  "question_id": "...", "session_id": "...", "tool_call_id": "...",
  "question": "...", "options": [{"label": "...", "description": null}],
  "multiple": false, "status": "pending", "selection": null,
  "created_at": "...Z", "expires_at": "...Z", "answered_at": null, "answered_by": null
}
```

### 3.4 REST（docs/05 §6.18）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/agent/questions` | `session_id?`、`status=pending`、`page=1`、`size=20`（1–200）→ `{items,total,page,size}` |
| GET | `/api/agent/questions/{question_id}` | 单条 wire |
| POST | `/api/agent/questions/{question_id}` | body `{selected: ["..."]}` → answered wire |
| POST | `/api/agent/questions/{question_id}/skip` | 无 body → skipped wire |

全部 `Depends(require_auth)`。新错误码（4 条，总数 100 → 104）：

| 错误码 | HTTP | 触发 |
|---|---|---|
| `QUESTION_NOT_FOUND` | 404 | id 不存在 |
| `QUESTION_ALREADY_ANSWERED` | 409 | 已 answered / skipped |
| `QUESTION_EXPIRED` | 409 | 已 expired |
| `QUESTION_SELECTION_INVALID` | 400 | 空选择、选项不在 options 里、单选提交多项 |

### 3.5 Runtime 接入（`runtime.py`）

- `AgentRuntime(session_factory, confirmation_service, *, question_service=None, ...)`；`main.py` 构造并注入，缺失时 `ask_user` 返回 `AGENT_DISABLED`。
- `_execute_tool_batch` 在 hidden/vision 检查之后、`confirmation_service.invoke` 之前拦截 `ask_user`：
  1. 校验参数（`registry.validate`）；
  2. `create()` → `emit("question_required", {tool_call_id, question_id, question, options, multiple, expires_at})`；
  3. `wait()`；
  4. `answered` → 结果体 `{"result": {"status": "answered", "selected": [...]}}`；`expired` / `skipped` → 结果体 `{"result": {"status": "expired"|"skipped"}}` 并让本轮在写完 tool 消息后收尾。
- 终止文案：超时「提问超时未得到回答，本轮结束。你可以重新描述需求，我会重新发起提问。」；跳过「你跳过了这次提问，本轮结束。」；`finish_reason` 记 `question_unanswered`（与 `confirmation_resolved` 同级的正常终态，不是错误）。**文案不带时长**：超时秒数可配置（`agent.question_timeout_seconds`），写死「10 分钟」会在改配置后失真；卡片上的倒计时由 `expires_at` 驱动。
- 结果里的 `audit_id` 用 QuestionService 建的那条，让对话里的工具条与审计页对得上。
- 原地打转检测照旧生效：连续三轮发出完全相同的 `ask_user` 会被判 `stuck_loop`。

### 3.6 前端（docs/09 §6 对话页）

- SSE 新事件 `question_required`：写入本轮 `pendingQuestion`，在本轮工具条下方渲染 `QuestionCard`；`message_end` / `error` 收尾时若仍挂着提问卡，保留本轮（与确认卡同一处理）。
- `QuestionCard`：问题正文 + 选项列表（`multiple` 决定单选/多选）+ 提交（未选中禁用）+ 「跳过」；本地按 `expires_at` 倒计时，到期后整卡禁用并显示「提问已超时，本轮已结束」。
- 本会话待答卡：`GET /api/agent/questions?session_id=…&status=pending`（10 秒轮询，与 pending confirmations 同节奏），渲染在本轮之后；已在本轮内展示的 `question_id` 不重复渲染（与确认卡去重同一套判断）。
- WS `question_request` / `question_resolved` → 失效该查询，多标签页同步。
- 输入栏状态行：等待提问时显示「请先回答上方的问题」；本轮结束后恢复可发送。
- 提交后立即清掉本轮提问卡（后端会接着推 `tool_result` 与后续文本）。

## 4. 影响与兼容

- **本轮多了一种挂起来源**：SSE 连接最多多挂 10 分钟（有保活）。断开页面不影响后端等待；刷新后靠待答卡恢复。
- **审计**：每次 `ask_user` 写一条 `agent_audit`（pending → success/rejected/expired），`tool_name="ask_user"`。
- **数据库**：新增一张表与 4 个错误码；无破坏性变更，无回填。
- **旧会话**：不受影响；重启时遗留 pending 提问直接置 expired。
- **提示词变化**：模型被要求用卡片而不是正文提问；对「开放式」问题（没有候选选项）仍允许正文提问。

## 5. 验收

- `.venv/bin/python -m pytest -q` 退出码 0；
- 新增/更新测试：`tests/agent/test_question_service.py`（创建/等待/回答/跳过/超时/重启清理/校验/审计/广播）、`tests/agent/test_runtime.py`（`ask_user` 发事件并等回答、超时结束本轮、跳过结束本轮、服务缺失报错）、`tests/api/`（四个端点的正常与错误路径）、`tests/agent/test_tool_catalog.py`（43 个工具，`ask_user` 的 risk/read_only/model_visible）；
- `.venv/bin/python scripts/dump_openapi.py --check`、`.venv/bin/python scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` 全部退出码 0；
- 前端新增 `QuestionCard` 测试与 AgentPage 的 `question_required` 用例；
- 行为实测（主 agent）：直接驱动 `QuestionService` + `AgentRuntime` 假 LLM，验证「发卡 → 用户选择 → 同一轮继续并带上选择」与「超时 → 本轮以 `question_unanswered` 收尾」。

## 6. 文档同步

- docs/04：新增 §5.17 `agent_question`（表结构、索引、与 confirmation 的区别）；
- docs/05：§6.18 四个新路由；错误码表加 4 条、总数 104；
- docs/09：对话页增加提问卡与输入栏状态行的说明；
- docs/11：§3.2 工具清单加 `ask_user`；新增 §5.3「Agent 提问」；§8.2 事件表加 `question_required`；§8.4 提示词要点加「用卡片提问」；
- docs/12：新增 M21 条目；
- docs/13：§8.26 记本裁决（三条用户裁决、提问与确认分开的理由、跳过按钮的补充裁决、超时口径、被否方案）；
- docs/README：决策速查表补一行（Agent 提问）。
