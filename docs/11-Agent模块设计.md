> 本文档定义 Agent 模块：对外的 MCP Server、统一工具层、高风险操作的人工确认、以及前端内置的对话式 agent。决策依据见 [README 决策速查表](./README.md#决策速查表)，架构位置见 [02-系统架构设计](./02-系统架构设计.md)。

# Agent 模块设计

## 1. 定位与双模式

按决策，agent 能力同时向两个方向开放：

**对外（工具提供方）**：MAA-API 暴露 MCP Server，Claude Desktop、Cursor、Claude Code 等外部 agent 直接接入，把这台服务当工具用。项目本身不需要 LLM，不存 API key。

**对内（调用方）**：前端内置对话界面，用户用自然语言指挥，项目自己调 LLM 并执行 tool-calling 循环。

两条路径**共享同一套工具实现**。这是整个设计的核心约束：工具逻辑只写一遍，放在 `agent/tools/`，由 `ToolRegistry` 统一注册，MCP Server、内置 agent runtime、以及给人用的 REST 接口三者都从它取。

```
                    ┌──────────────────────────────────┐
   外部 agent ──MCP─┤                                  │
   (Claude/Cursor)  │         ToolRegistry             │
                    │  schema 导出 + 执行分发 + 审计    │
   内置 agent ──────┤                                  │
   (AgentRuntime)   └────────────┬─────────────────────┘
                                 │
                    ┌────────────▼─────────────────────┐
                    │        PolicyEngine              │
                    │  风险判定 → 需要确认则挂起        │
                    └────────────┬─────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
   CoreClient              DeviceManager            应用服务层
   (MaaCore 命令)          (ADB 原子操作)         (队列/更新/日志/查询)
```

工具**不直接碰 MaaCore 子进程**，一律经应用服务层。这保证了 agent 的操作与前端手动操作走完全相同的代码路径，包括队列优先级、状态落库、日志广播 —— agent 提交的流水线在前端看起来和手动提交的没有区别，只是 `source` 字段不同。

## 2. 三层能力模型

用户需求里的"通过 maa 内核的原生能力，直接操作游戏和自定义 maa 任务"对应三个不同抽象层次，agent 应当优先用高层、必要时才下沉：

**第一层 · 任务编排（首选）**：提交 MAA 的 9 种任务类型及其参数。这是 MAA 最可靠的能力，内核内部处理了识别、重试、异常分支。agent 90% 的工作应该停在这一层。

**第二层 · 自定义任务**：Copilot 作业 JSON（自动战斗）、自定义基建换班方案、注入自定义 `tasks.json` task 定义。用于 MAA 已有框架能表达但内置配置覆盖不到的需求。

**第三层 · 原子操作（兜底）**：直接点击坐标、滑动、回主界面。用于 MAA 完全未适配的场景（新活动界面、卡在未知弹窗）。这一层没有任何识别与容错，全靠 agent 自己看截图判断，成功率最低、风险最高。

工具描述里会显式写明这个优先级，引导模型不要一上来就点坐标。

### 2.1 第三层的能力边界

这是必须让使用者知道的硬限制，来自对 MaaCore C API 的核实：

| 操作 | 实现路径 | 说明 |
|---|---|---|
| 点击坐标 | `AsstAsyncClick` | MaaCore 原生 |
| 触发截图 | `AsstAsyncScreencap` + `AsstGetImage` | MaaCore 原生 |
| 回主界面 | `AsstBackToHome` | MaaCore 原生，比自己找返回按钮可靠 |
| 滑动 | **ADB**（`adbutils` 的 `swipe`） | MaaCore 无此 API |
| 长按 | **ADB** | MaaCore 无此 API |
| 输入文本 | **ADB**（`input text`） | MaaCore 无此 API |
| 按键（返回/home） | **ADB**（`input keyevent`） | MaaCore 无此 API |
| 识别画面文字 | **无独立接口** | MaaCore 的 OCR 只能通过 task 定义间接触发 |

按决策**不开放任意 `adb shell` 命令**。ADB 相关工具都是参数化的具体动作（`swipe(x1,y1,x2,y2,duration)` 这样），不接受自由文本命令，避免 agent 拿到设备级任意执行权限。

画面理解完全依赖多模态模型看截图，不额外接 OCR。这意味着内置 agent 必须配置支持视觉的模型，纯文本模型只能做任务编排而无法做第三层操作 —— 这个约束会在设置页明确提示。

### 2.2 关卡名解析的平台差异

`AsstGetMapLevelKey` 能把"切城区"、"1-7"、`main_01-07` 这类不同写法互相映射，对解析自然语言指令很有用。但核实发现它**只在 macOS 的 `libMaaCore.dylib` 里导出，Linux 的 `.so` 没有**，且官方标注为实验性 API（详见 [03-MaaCore内核层设计 §1.2](./03-MaaCore内核层设计.md)）。

因此 `resolve_stage` 工具的实现是两级的：优先调内核 API，不可用时回退到从 `resource/lib/maa/*/resource/tasks/Stages/` 目录构建的本地关卡索引。工具返回值里带 `source` 字段标明用了哪条路径，便于排查。这个工具永远不会因为平台不同而消失，只是精度可能下降。

## 3. ToolRegistry 与工具清单

### 3.1 工具定义方式

每个工具是一个带元数据的异步函数，参数用 Pydantic 模型描述以便自动导出 JSON Schema：

```python
@tool(
    name="submit_pipeline",
    group=ToolGroup.PIPELINE,
    risk=Risk.CONDITIONAL,          # 由 PolicyEngine 按参数内容判定
    description=(
        "提交一条 MAA 任务流水线并排队执行。这是让 MAA 干活的首选方式，"
        "优先于直接点击坐标。任务类型与参数用 list_task_types 查询。"
    ),
)
async def submit_pipeline(params: SubmitPipelineParams, ctx: ToolContext) -> SubmitPipelineResult:
    ...
```

`risk` 有三档：`SAFE`（只读，永不需要确认）、`CONDITIONAL`（按参数内容判定）、`DANGEROUS`（总是需要确认）。

`ToolContext` 携带调用方身份（`mcp` / `internal` / `rest`）、会话 id、请求 id，用于审计与确认归属。

### 3.2 完整工具清单

按组划分，`risk` 列标注风险档位。

**状态查询组（`status`）** —— 全部 `SAFE`

| 工具 | 用途 |
|---|---|
| `get_system_status` | 服务、内核、设备三层状态一次性返回 |
| `get_screenshot` | 当前画面。返回临时 URL 或 base64，含分辨率与时间戳 |
| `get_versions` | 内核 / 活动资源 / 游戏本体的当前与最新版本 |
| `get_logs` | 查日志，按来源、级别、时间范围、关联流水线过滤 |
| `resolve_stage` | 关卡名 / code / stageId 互查，见 §2.2 |
| `get_drop_stats` | 从历史库聚合的掉落统计，支持按关卡与时间段 |

**任务编排组（`pipeline`）**

| 工具 | risk | 用途 |
|---|---|---|
| `list_task_types` | SAFE | 9 种任务类型及完整参数 schema、取值范围、中文说明 |
| `get_pipeline` | SAFE | 某条流水线的详情与任务级状态 |
| `list_pipelines` | SAFE | 执行历史，分页 |
| `get_queue` | SAFE | 当前排队情况与优先级 |
| `submit_pipeline` | CONDITIONAL | 提交流水线。含消耗类参数时需确认 |
| `stop_pipeline` | SAFE | 停止当前流水线。刻意设为免确认，这是救援动作 |
| `set_task_params` | CONDITIONAL | 运行中修改任务参数 |
| `cancel_queued` | SAFE | 取消排队中未开始的条目 |

**原子操作组（`raw`）** —— 除安全白名单外需会话级授权；REST/MCP 调用逐次确认

| 工具 | 实现 |
|---|---|
| `click` | MaaCore `AsstAsyncClick` |
| `swipe` | ADB |
| `long_press` | ADB |
| `input_text` | ADB |
| `key_event` | ADB，限定白名单键值（BACK / HOME / ENTER 等） |
| `back_to_home` | MaaCore `AsstBackToHome`。风险降为 `SAFE`，因为它是幂等的复位动作 |
| `trigger_screencap` | MaaCore `AsstAsyncScreencap`，`SAFE` |

**设备组（`device`）**

| 工具 | risk | 用途 |
|---|---|---|
| `get_device_status` | SAFE | 连接状态、地址、分辨率、UUID |
| `reconnect_device` | SAFE | 手动触发重连 |
| `list_devices` | SAFE | 扫描 `adb devices` 可见设备 |

**自定义资源组（`resource`）**

| 工具 | risk | 用途 |
|---|---|---|
| `list_copilots` | SAFE | 已保存的 Copilot 作业 |
| `upload_copilot` | SAFE | 校验并保存作业，不执行 |
| `list_custom_tasks` | SAFE | 已注入的自定义 task 定义 |
| `register_custom_task` | DANGEROUS | 注入自定义 `tasks.json` task，见 §7 |
| `remove_custom_task` | DANGEROUS | 移除并重载资源 |
| `set_infrast_plan` | DANGEROUS | 自定义基建换班方案（覆盖既有方案） |

**运维组（`ops`）**

| 工具 | risk | 用途 |
|---|---|---|
| `check_updates` | SAFE | 检查三个目标是否有新版本 |
| `update_core` | DANGEROUS | 内核热更新，会重启子进程 |
| `update_resource` | DANGEROUS | 活动资源更新 |
| `update_game` | DANGEROUS | 下载并安装游戏 APK |
| `restart_core` | DANGEROUS | 重启 MaaCore 子进程 |

**定时任务组（`schedule`）**

| 工具 | risk | 用途 |
|---|---|---|
| `list_schedules` | SAFE | 定时任务列表 |
| `create_schedule` | CONDITIONAL | 新建定时任务 |
| `update_schedule` | CONDITIONAL | 修改 |
| `delete_schedule` | CONDITIONAL | 删除 |

**确认组（`confirm`）**

| 工具 | risk | 用途 |
|---|---|---|
| `check_confirmation` | SAFE | 查询某个待确认请求的状态，超时兜底用，见 §5.3 |

### 3.3 工具数量对 MCP 的影响

上表共约 38 个工具。这个规模对 MCP 客户端是个实际问题：全部 schema 注入 agent 上下文会占用可观的 token，且选项过多会降低模型的选择准确率。

处理方式是**按组的 scope 控制**。MCP 端点接受 scope 参数，只暴露选中组的工具：

```
https://host:8002/mcp?scopes=status,pipeline,device
```

默认 scope 为 `status,pipeline,device,schedule`，约 20 个工具，覆盖绝大多数使用场景。`raw`、`resource`、`ops` 三组默认不暴露，需要显式开启 —— 这既控制了上下文体积，也构成了一道纵深防御：默认接入的外部 agent 拿不到点击坐标与运维能力。

Scope 同时写进审计记录，便于事后追查某次调用来自哪种配置。

已拍板维持这个设计：scope 由客户端 URL 指定，作用是"展示多少工具"而非限权，任何持有 token 的客户端都能自行开启全部 scope。升级为 API Key 加权限范围被否，因为它与"单 access_token"的决策冲突，且真实的访问边界已由 Tailscale 提供 —— 不在 tailnet 内的设备连端口都摸不到（见 [13-决策记录](./13-决策记录.md) ADR-13），这比应用层的 scope 检查更靠前也更可靠。

## 4. PolicyEngine

`agent/policy.py` 判定一次工具调用是否需要人工确认。判定依据是工具的 `risk` 档位加上参数内容。

### 4.1 消耗类判定

按决策，消耗类操作需要确认。这些操作不会直接花钱，但会消耗游戏内的稀缺资源，误操作的代价不可逆：

| 触发条件 | 涉及参数 |
|---|---|
| 碎石 | `Fight.stone > 0` |
| 使用理智药 | `Fight.medicine > 0`、`Fight.expiring_medicine > 0` |
| 加急许可 | `Recruit.expedite == true` |
| 商店购物 | `Mall.shopping == true` |
| 源石锭投资 | `Roguelike.investment_enabled == true` |

判定在 `submit_pipeline` 与 `set_task_params` 的参数上做深度检查，不是简单看工具名。这意味着"刷 1-7 五次不吃药"不需要确认，而"刷 1-7 直到理智耗尽，允许碎 3 颗石头"需要确认 —— 粒度落在真正有后果的地方。

确认卡片上会明确列出触发原因与具体数值，例如"将碎石 3 颗、使用理智药 2 瓶"，而不是笼统的"agent 想执行 submit_pipeline"。

### 4.2 破坏类判定

按决策，破坏类操作需要确认。这些操作影响配置、软件状态或需要长时间恢复：

清空或覆盖基建换班方案、卸载重装游戏（`update_game`）、重装或更新 MAA 内核（`update_core`、`restart_core`）、活动资源更新（`update_resource`）、注入或移除自定义 task 定义、修改全局配置。

原子操作组（`click` / `swipe` / `long_press` / `input_text` / `key_event`）虽不属于"消耗类"或"破坏类"，但它们绕过了 MAA 的全部识别与容错，点错位置可能触发任意游戏内操作（包括花费资源）。因此归入 `DANGEROUS`。

### 4.3 原子操作走会话级授权

**已拍板：原子操作不逐次确认，改为会话级授权。** 逐次确认会让"卡死救援"这个核心场景不可用——agent 从一个未知界面复位可能需要连续几十次截图与点击，每次弹卡片等于让用户手动操作一遍，agent 的价值就没了。

机制是：会话内**首次**尝试原子操作时弹一次确认，卡片文案明确说明这是一次授权而非单次操作（"允许本次对话在接下来 15 分钟内直接操作游戏界面？期间的每次点击与滑动都会记入审计，你可以随时撤销"）。批准后在该 agent 会话上记录一个授权窗口，窗口内的原子操作直接执行。

四条约束让这个放宽不至于失控：

**授权的粒度是「会话 + 时间窗口」，不是全局开关。** 授权挂在 `agent_session` 上（字段见 [04-数据模型与持久化](./04-数据模型与持久化.md) 的 `agent_session` 表），另一个会话、或同一会话过期后重开，都要重新授权。默认窗口 15 分钟，可在设置页调整，上限 60 分钟。

**只覆盖原子操作，不扩散到其他风险类别。** 授权窗口内，消耗类（碎石、用药、加急、购物、投资）与破坏类（更新、重装、改配置、注入自定义 task）仍然逐次确认。用户批准的是"让 agent 摸屏幕"，不是"让 agent 花钱"。

**随时可撤销，且撤销入口显眼。** 授权生效期间前端顶部常驻一条提示条，显示剩余时间与「立即撤销」按钮；撤销后窗口立即失效，正在挂起的调用按拒绝处理。会话结束（用户关闭对话或切换会话）自动撤销。

**审计不因免确认而降级。** 窗口内每次原子操作照常写 `agent_audit`，并额外记录它是凭哪次授权执行的（`authorized_by` 指向那条 `confirmation` 记录）。这样事后复盘"agent 到底点了什么"时，能完整还原整个授权窗口内的操作序列，以及是谁在什么时候批准了这个窗口。

只有受信任的 `internal` 调用可以使用会话授权。REST 调用没有内置会话，MCP 调用也不继承内部会话；两者的原子操作都逐次确认。`internal` 调用必须带有效且处于 active 状态的会话，缺少、已结束或不存在的会话一律拒绝，不降级为逐次确认。服务启动时清除所有既有授权；会话关闭、切换或删除时撤销授权。M11 先交付会话授权与撤销 API，授权状态提示条归 M13。

### 4.4 免确认的白名单

以下非只读动作刻意设为免确认：

- `stop_pipeline`：停止当前流水线。如果连停止都要确认，agent 发现异常时无法及时刹车。
- `trigger_screencap`：只请求截图，不改游戏状态。
- `back_to_home`：回游戏主界面。幂等，无消耗，是从未知状态复位的标准动作。
- `upload_copilot`：完成作业 schema 校验并保存，不启动战斗。
- `delete_schedule`：删除定时任务定义，不立即运行或消耗资源。

其他只读工具同样不需确认。所有屏幕原子操作均受会话授权或逐次确认约束，截图触发例外。

## 5. 人工确认机制

按决策，确认请求经 WebSocket 推到前端，弹卡片让用户批准或拒绝，超时自动拒绝。

### 5.1 完整时序

```
工具调用进入 ToolRegistry
  → PolicyEngine 判定需要确认
  → 写 confirmation 记录（status=PENDING，含 action、参数、触发原因、过期时间）
  → WS 广播 confirm_request 事件给所有已连接前端
  → 同时经通知通道推送（用户不在页面前也能收到）
  → 工具调用挂起，等待 asyncio.Event

前端弹卡片，展示触发原因与具体影响
  → 用户点批准 → POST /api/confirmations/{id} {approved: true}
  → 唤醒挂起的调用
  → 执行真实操作
  → 审计落库（含确认耗时与批准人）

或用户点拒绝 → 工具返回 CONFIRMATION_REJECTED 错误
或超时未响应 → confirmation 置 EXPIRED，工具返回 CONFIRMATION_EXPIRED 错误
```

确认记录落库而非仅存内存，这样服务重启后前端仍能看到遗留的待确认项（重启时统一置为 `EXPIRED`，避免僵尸记录）。

### 5.2 内置 agent 与 MCP 的超时差异

这两条路径对"挂起等待"的容忍度完全不同，必须分别处理。

先说超时时长本身：**消耗类与破坏类的确认默认 10 分钟，原子操作的会话授权默认 120 秒**，两者都可配置。分级的理由是等待场景不同 —— 高风险操作靠推送触达，用户要听见、解锁、点开应用、读完卡片再决定；而请求原子操作授权时用户正在对话界面前主动让 agent 动手，此刻就在看屏幕。相关的平台约束（WebKit 忽略 `requireInteraction`，通知不常驻）见 [09-前端重构方案 §13.2](./09-前端重构方案.md)。

**内置 agent**：tool-calling 循环跑在服务端自己的 asyncio 任务里，挂起 10 分钟也毫无问题 —— 它只是一个 `await`，不占线程也不占连接。前端的对话界面显示"等待你确认"的状态，用户在同一个界面上批准，体验连贯。

**MCP**：MCP 的 tool call 是同步请求-响应，客户端有自己的超时（Claude Desktop 等客户端的默认值通常是秒级到一分钟，远小于 10 分钟，且不受我们控制）。挂起太久会让客户端先超时断开，而服务端还在等确认，产生状态不一致。超时放长到 10 分钟后这个矛盾更尖锐，所以下面的短阻塞加轮询对 MCP 路径不是优化而是必需。

因此 MCP 路径采用**短阻塞 + 轮询兜底**：

```
MCP tool call
  → 创建 confirmation，WS 广播
  → 短阻塞等待（默认 25 秒）
  → 25 秒内获批 → 正常执行并返回结果（最常见的情况，用户就在手机前）
  → 25 秒未响应 → 不报错，返回结构化的 pending 结果：
      {
        "status": "awaiting_confirmation",
        "confirmation_id": "...",
        "expires_at": "...",
        "hint": "用户尚未确认。请用 check_confirmation 查询结果，或稍后重试。"
      }
  → agent 调 check_confirmation 轮询
  → 在原服务进程中，批准事件唤醒原请求 worker 执行；check_confirmation 只读取确认与审计状态
```

这样 confirmation 记录既是审批凭据也是待执行的操作快照。`check_confirmation` 返回状态和关联审计结果，不会重新执行已批准的 payload。若服务在批准和审计终态提交之间重启，审计会标记为执行结果不确定的 `FAILED`，不自动重放，避免重复副作用。

### 5.3 关于 MCP elicitation

MCP 规范提供了 elicitation（`ctx.elicit()`），服务端可以主动向客户端索要用户输入，看起来正好适合确认场景。但核实 MCP Python SDK 后确认它**不足以作为主方案**：

它需要一条服务端到客户端的 back-channel，而这条通道在几种常见配置下都不存在 —— `stateless_http=True` 会移除它，`json_response=True` 会移除 request-scoped 通道，较新协议版本的连接本身就没有 legacy 通道。此外客户端必须显式声明 elicitation capability（传 `elicitation_callback`），否则服务端会收到 "Client did not declare the form elicitation capability" 而失败。

因为支持度取决于客户端实现与协议版本，都不在我们掌控范围内，所以 elicitation 只作为**可选增强**：检测到当前连接支持时用它提供更好的交互（直接在 agent 客户端里弹确认），不支持时静默回落到 §5.2 的短阻塞加轮询。前端的 WebSocket 确认卡片在两种情况下都可用，是唯一保证可达的渠道。

## 6. MCP Server 实现

### 6.1 SDK 版本注意事项

MCP Python SDK v2 已将高层服务类从 `FastMCP` 改名为 `MCPServer`：

```python
from mcp.server import MCPServer   # v1 是 from mcp.server.fastmcp import FastMCP

mcp = MCPServer("maa-api")

@mcp.tool()      # 注意必须带括号，@mcp.tool 不带括号会报错
def ...
```

实现时以安装版本的文档为准，不要沿用 v1 写法。

### 6.2 挂载到 FastAPI

MCP 的 Streamable HTTP 应用挂在主进程的 FastAPI 上，同端口 8002 的 `/mcp` 路径：

```python
mcp_app = mcp.streamable_http_app(streamable_http_path="/")

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():   # 关键：必须在宿主 lifespan 里进入
        await _startup()
        yield
        await _shutdown()

app = FastAPI(lifespan=lifespan)
app.mount("/mcp", mcp_app)
```

**宿主 app 的 lifespan 必须显式进入 `mcp.session_manager.run()`。** Starlette 不会运行 `Mount` 下子应用的 lifespan，遗漏这一步的症状是运行时报 "Task group is not initialized"，且只在实际发起 MCP 调用时才暴露。这是该集成方式最容易踩的坑。

另外需要配置 `transport_security` 的 `allowed_hosts`。默认的 DNS rebinding 防护会拒绝非预期 Host 头，而本项目要通过局域网 IP 访问（手机连 `192.168.x.x:8002`），不配置会得到 421 Misdirected Request。允许的 host 列表应从配置读取，默认包含 `localhost`、`127.0.0.1` 与当前机器的局域网地址。

### 6.3 stdio 入口的架构约束

按决策要额外提供 stdio 入口供本机 Claude Desktop 使用。这里有一个必须讲清楚的约束：

**stdio MCP server 是由 Claude Desktop 拉起的独立进程，它无法访问主进程的内存。** MaaCore 子进程归属于 FastAPI 主进程，`CoreClient`、`ToolRegistry`、数据库连接都在那里。stdio 进程直接 import 并调用工具实现会创建第二套内核实例，与主进程争抢同一台设备。

因此 `scripts/mcp_stdio.py` 是一个**瘦代理**：它是完整的 MCP stdio server，但每个工具的实现都是转发一次 HTTP 请求到主进程的 REST API。

```
Claude Desktop ──stdio──► mcp_stdio.py ──HTTP──► FastAPI 主进程 ──IPC──► MaaCore 子进程
```

工具 schema 由 stdio 进程启动时从主进程的 `GET /api/agent/tools` 拉取并动态注册，这样新增工具不需要改 stdio 脚本。它需要从环境变量读取主进程地址与 `access_token`。

代价是多一跳 HTTP 与一个常驻进程。收益是 Claude Desktop 的零配置本地接入（不需要用户自己填 URL 与 token，配在 MCP 配置文件里即可）。若使用者能接受手填远程 URL，直接用 Streamable HTTP 更高效。

## 7. 自定义任务

按决策开放到最强层级，共三种形式，风险递增。

### 7.1 参数组合

组合 9 种任务类型及其全部参数，这是最安全也最常用的形式。参数 schema 从 `domain/task.py` 的 Pydantic 模型导出，附中文说明与取值范围，由 `list_task_types` 提供给 agent。

现有 `maa_api/model/core/task.py` 的文档字符串已经写得相当完整（包括每个参数的默认值、取值范围、生效条件、互斥关系），这些信息要完整迁移进 schema 的 description，agent 的参数填写质量直接取决于它。

### 7.2 Copilot 作业

Copilot 是 MAA 的自动战斗协议，用 JSON 描述干员部署序列与技能释放时机。agent 可以上传已校验的作业并列出已保存作业。

作业 JSON 上传前必须做 schema 校验。格式错误的作业不会让内核崩溃（它作为任务参数传入，内核自己会校验），但会导致战斗失败并浪费理智，所以前置校验有实际价值。

执行已保存作业的 `run_copilot` 暂缓：当前 MAA-API 没有把 Copilot JSON 交给 MaaCore 的应用服务，也没有 `FightInput` 作业引用字段。接入真实运行器前必须明确调用 API 和参数契约；不得用普通 Fight 关卡替代，这会使作业内容被忽略。

### 7.3 注入自定义 tasks.json task 定义

这是最强也最危险的一层。MAA 的 `tasks.json` 是一套完整的任务 DSL，支持模板图片匹配、OCR 文本识别、点击偏移、任务跳转、前后置条件等。注入自定义 task 定义意味着 agent 可以扩展 MAA 本身的识别与操作逻辑，用来应对 MAA 尚未适配的新活动。

实现上走 `AsstLoadResource` 的增量资源机制（见 [03-MaaCore内核层设计 §7](./03-MaaCore内核层设计.md)），但必须满足几个安全前提：

**独立目录。** 自定义 task 写入 `resource/maa-layers/custom/`，与两条官方资源通道的落点（同级的 `cache/` 与 `repo/`）分离。这样"清空全部自定义 task"是一次目录删除加资源重载，不会误伤官方资源。这个目录在 `<maa_path>` 之外，因此内核更新不会碰到它——自定义资源是三个增量层里唯一没有远端副本的一层，见 [07-热更新方案 §3.7](./07-热更新方案.md)。

**schema 校验。** 注入前校验 JSON 结构。格式错误的 task 定义可能让 `AsstLoadResource` 失败，而资源加载失败会导致内核不可用 —— 这是能真正搞坏服务的操作。

**加载失败自动回滚。** 注入后触发资源重载，若重载失败或子进程随后崩溃，自动移除刚注入的定义并重新加载，然后返回错误。没有这层保护的话，一次坏注入会让内核进入反复崩溃状态（`CoreSupervisor` 的退避机制会兜住，但服务已不可用）。

**不允许覆盖官方 task 名。** 注入的 task 名必须带约定前缀（如 `Custom_`），避免覆盖官方定义导致 MAA 行为异常且难以排查。

已拍板允许 agent 自主生成并注入 task 定义，归入 `DANGEROUS` 需人工确认，确认卡片展示完整 JSON。备选的"只允许前端上传、agent 只能引用已有的"被否，因为那会削掉"应对 MAA 未适配的新活动"这个目标场景的核心能力。已知不足是长 JSON 在手机上审阅困难，缓解手段是上面那四层防护而非事前人工逐行审阅 —— 确认卡片的实际作用是让用户判断"要不要让它动手"，而非校验 JSON 正确性。

## 8. 内置 Agent Runtime

### 8.1 LLM 接入

按决策统一走 OpenAI 兼容端点，配置三个字段：`base_url`、`api_key`、`model`。这一套能覆盖 OpenAI、DeepSeek、智谱、Ollama（`http://localhost:11434/v1`）以及各类中转站。

配置落 `setting` 表，在设置页可改。`api_key` 属于敏感项，落库时加密存储，接口返回时脱敏。

视觉能力需要模型支持图像输入。设置页提供"能力自检"按钮：发一次带小图的测试请求，确认模型是否支持多模态，不支持时明确提示"该模型无法使用截图相关功能，agent 只能做任务编排"。

### 8.2 tool-calling 循环

```
用户消息入库
  → 构造请求：system prompt + 历史消息（截断策略见 §8.3）+ 当前状态摘要 + tools schema
  → 调 LLM（流式）
  → 有 tool_calls：
      → 并发执行互不依赖的调用（只读类可并发，写类串行）
      → 每个调用经 ToolRegistry → PolicyEngine → 可能挂起等确认
      → 结果回填为 tool 消息
      → 回到第二步，迭代计数 +1
  → 无 tool_calls：输出最终回复，结束
  → 达到 max_iterations（默认 12）：强制结束，告知用户已达上限
```

流式输出经 SSE 推给前端，事件类型区分：文本增量、工具调用开始、工具调用结果、等待确认、迭代结束、错误。前端据此把工具调用轨迹渲染成可折叠的步骤列表，而不是只显示最终文本 —— 用户需要看到 agent 到底做了什么。

只读工具并发、写工具串行是必要的：agent 常常一次请求多个状态查询（截图加设备状态加日志），并发能显著降低延迟；而写操作并发会产生竞态（两个 `submit_pipeline` 同时进队列，顺序不确定）。

### 8.3 上下文管理

游戏截图是 base64 大块内容，多轮对话里累积极快，必须主动管理：

**截图只保留最近一张。** 历史消息中的图像内容在构造请求时替换为文本占位（"[此处曾有一张截图，时间 xx:xx]"），只有最新一张保留实际图像。agent 需要回看旧画面时可以重新截图。

**状态摘要注入。** 每次请求在 system prompt 后追加一段当前状态摘要（内核状态、设备状态、当前流水线与进度、队列长度、最近一次错误），避免 agent 为了知道"现在在干什么"而反复调工具。摘要由服务端生成，成本远低于工具往返。

**历史截断。** 超过 token 预算时从最旧的消息开始丢弃，但保留 system prompt 与最近一次完整的工具调用轮次（避免出现 tool_call 没有对应 tool 结果的残缺结构，这会让部分 API 直接报错）。

**成本记录。** 每次调用记录 prompt/completion token 数入 `agent_message`，会话级累计。设置页可配单会话 token 上限，超限后停止并提示。

### 8.4 系统提示词要点

提示词需要传达的关键信息（实现时写成完整文本，这里列纲要）：

服务的角色与能力边界；三层能力模型的优先级（先任务编排，再自定义，最后原子操作）；MaaCore 无滑动、无独立 OCR 这两个限制；操作游戏前应先截图确认当前画面；做原子操作前应先停止正在运行的流水线（否则会与内核抢控制权，见 [02-系统架构设计 §5.3](./02-系统架构设计.md)）；高风险操作会触发人工确认，被拒绝时不要重试而应询问用户；关卡名应先用 `resolve_stage` 校验再提交；不确定时宁可询问用户而不要猜测参数。

## 9. 审计

按决策全量落库。每次工具调用（无论来自 MCP、内置 agent 还是 REST）写一条 `agent_audit` 记录：调用方类型与身份、会话 id、工具名、完整入参、风险判定结果、是否需要确认与确认结果、执行状态、结果摘要、耗时、错误码。

入参可能含大对象（Copilot 作业 JSON、自定义 task 定义），存储时对超过阈值的字段做截断并保留哈希，完整内容另存到文件。

前端审计页支持按调用方、工具、时间、状态筛选，并能从审计记录跳转到关联的流水线与日志 —— 这是排查"agent 到底干了什么导致这个结果"的主要手段。

审计表的增长速度取决于 agent 使用频率，保留策略与日志表一致，见 [04-数据模型与持久化](./04-数据模型与持久化.md)。

## 10. 目标场景的实现路径

按决策，六个场景全部在范围内。这里说明每个场景依赖哪些工具与机制，验收时逐个走通。

**自然语言变任务。** 主路径：`list_task_types` 了解参数 → `resolve_stage` 校验关卡 → `submit_pipeline` 提交 → 订阅状态。难点在参数推断的准确性，依赖 `task.py` 文档字符串完整迁入 schema description。

**出错自动诊断。** 触发方式有两种：用户主动问"刚才为什么失败了"，或流水线失败时由 `PipelineRunner` 主动发起一次 agent 分析（这需要内置 agent 已配置，且应可在设置里开关）。主路径：`get_pipeline` 拿失败详情 → `get_logs` 拿关联日志 → `get_screenshot` 看当前画面 → 给出结论与建议动作。

**卡死脉救援。** 主路径：`get_screenshot` 判断当前界面 → `stop_pipeline` 停掉正在跑的东西 → `back_to_home` 尝试标准复位 → 无效则 `click` / `swipe` 手动处理 → 再次截图确认 → 恢复原流水线。这个场景最依赖会话级授权（见 §4.2 的待确认项），否则每步都弹确认会让它不可用。

**应对新活动。** 最重的场景。主路径：截图观察界面 → 用原子操作试探性操作 → 若能总结出稳定规律，则 `register_custom_task` 注入 task 定义把它固化下来 → 之后用 `submit_pipeline` 调用该自定义 task。这条路径依赖 §7.3 的全部安全机制。

**外部编排。** 在 Claude Desktop 或 Cursor 里接入 MCP，不经前端对话界面。依赖 §6 的 MCP Server 与 stdio 入口。

**数据总结。** 主路径：`list_pipelines` 加 `get_drop_stats` 拉历史 → 聚合分析 → 输出报告。这个场景不碰设备，纯读，风险最低，可以作为 agent 功能的首个验证场景。

## 11. 已知风险

**幻觉导致的误操作。** 模型可能编造不存在的关卡名或参数值。缓解手段是参数校验前置（Pydantic 模型拒绝非法值）、`resolve_stage` 校验关卡、以及高风险操作的人工确认。但第三层原子操作无法校验 —— 点击坐标 (640, 360) 永远是"合法"的，只是可能点错。这是开放原子操作的固有代价。

**视觉判断的可靠性。** 多模态模型识别游戏界面的准确率未经验证，尤其是密集的小字与相似图标。建议先在只读场景（诊断、报告）验证模型的画面理解能力，再逐步放开操作权限。

**与内核的控制权冲突。** agent 的原子操作和 MaaCore 的自动化会互相干扰。§4.3 把 `stop_pipeline` 设为免确认、提示词要求先停流水线再操作、以及 [02-系统架构设计 §5.3](./02-系统架构设计.md) 的 409 拒绝共同构成防护，但无法完全排除人为绕过（`force=true`）。

**成本失控。** tool-calling 循环的迭代次数、每轮的截图 token 都会放大成本。`max_iterations`、单会话 token 上限、截图只留最近一张三项措施共同控制，但仍建议初期配置较便宜的模型观察实际消耗。

**MCP 客户端行为不可控。** 客户端的超时、重试、并发策略都不在我们掌控内。同一个 tool call 被客户端重试可能导致重复提交，因此 `submit_pipeline` 需要幂等保护（见 [05-API规范与路由清单](./05-API规范与路由清单.md) 的幂等性设计）。
