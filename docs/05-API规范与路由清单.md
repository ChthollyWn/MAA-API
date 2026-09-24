> 本文档是 MAA-API 对外契约的唯一规范：HTTP 状态码用法、统一错误体与错误码枚举、四渠道鉴权、全量路由清单、任务提交的请求体设计与幂等约定。上游决策见 [README 决策速查表](./README.md#决策速查表)，接入层在整体架构中的位置见 [02-系统架构设计](./02-系统架构设计.md)。

# API 规范与路由清单

## 1. 总则

重构后的 API 遵循四条硬规则，它们共同废弃了现有 `maa_api/model/request/response.py` 那套 `{code: 10200}` 体系。

**语义由 HTTP 状态码承担。** 现有实现无论成败都返回 HTTP 200，真实结果藏在响应体的 `code` 字段里。这让所有中间层——浏览器、`fetch` 的 `response.ok`、反向代理的日志、OpenAPI 生成的客户端、MCP 客户端的重试策略——全部失效，每一处都必须先解析 body 才知道发生了什么。新规范下状态码是第一现场。

**错误体只有一种形状。** 任何非 2xx 响应的 body 都是：

```json
{
  "error": {
    "code": "ADB_CONNECT_FAILED",
    "message": "ADB 连接失败：127.0.0.1:5555 无响应",
    "details": { "address": "127.0.0.1:5555", "attempts": 3 }
  }
}
```

`code` 是可枚举的大写下划线字符串，前端据此决定行为（是否弹重连按钮、是否可重试）；`message` 是中文,直接展示给用户；`details` 可选，放结构化上下文，前端可以不认识它但排查时有用。**不返回堆栈**，服务端异常的细节进日志表。

**成功响应裸返回资源体，不包封套。** 理由见 §3.1。

**路径前缀统一 `/api`，MCP 端点为 `/mcp`。** 前端 SPA 占用其余全部路径。

## 2. HTTP 状态码使用规范

| 状态码 | 使用场景 |
|---|---|
| `200 OK` | 读取成功；同步执行完成的写操作（原子点击、发测试通知、批准确认） |
| `201 Created` | 创建了一个可寻址的持久实体（定时任务、通知通道、agent 会话、资源文件），必须带 `Location` 头 |
| `202 Accepted` | 请求已受理但未完成：提交流水线、取消流水线、重启内核、触发更新、重连设备、发送 agent 消息、等待人工确认 |
| `204 No Content` | 删除成功、批量清理成功，无响应体 |
| `400 Bad Request` | 请求在语义上不成立但不是字段类型问题：JSON 解析失败、未知任务类型、未知设置项、cron 表达式非法、使用了已弃用的参数值 |
| `401 Unauthorized` | token 缺失或不匹配。响应带 `WWW-Authenticate: Bearer` |
| `403 Forbidden` | 身份有效但动作被拒：调用方无权使用该工具、修改只读配置项、人工确认被用户拒绝 |
| `404 Not Found` | 资源不存在，含已被保留策略清理掉的历史记录 |
| `409 Conflict` | 与当前状态冲突：流水线正在运行、更新正在进行、待确认项已处理过、修改不支持运行时变更的参数 |
| `410 Gone` | 确定曾存在但已永久移除：旧版端点、文件已被清理但记录仍在的截图 |
| `422 Unprocessable Entity` | 结构正确但字段值不合法：Pydantic 校验失败、参数越界、跨字段规则不满足 |
| `429 Too Many Requests` | 队列已满、上游 LLM 限流透传、鉴权失败频率限流。带 `Retry-After` 头 |
| `500 Internal Server Error` | 服务端自身缺陷或本地环境故障：未捕获异常、数据库错误、解压失败、`adb` 可执行文件不存在 |
| `502 Bad Gateway` | 依赖的外部/下游组件明确失败：MaaCore 子进程命令失败、ADB 命令失败、下载源不可达、LLM 端点报错 |
| `504 Gateway Timeout` | 下游未在时限内应答：MaaCore 子进程命令超时、LLM 上游超时。操作可能已执行一半，客户端应查状态而非重试 |
| `503 Service Unavailable` | 依赖暂时不可用、稍后可重试：内核未就绪/重启中/已崩溃、设备未连接、LLM 未配置。带 `Retry-After` 头 |

### 2.1 关于 202

202 在本 API 里出现得比多数项目频繁，因为核心操作天然是异步的：流水线要排队，内核重启要几秒，更新要下载几十兆。202 的响应体是**受理凭据**，带上可供轮询的资源 id 和 `Location`：

```http
HTTP/1.1 202 Accepted
Location: /api/pipelines/8f3c1e2a-...
Content-Type: application/json

{
  "id": "8f3c1e2a-...",
  "status": "pending",
  "queue_position": 2,
  "estimated_start_at": null
}
```

调用方拿到 202 之后有两条路：轮询 `Location`，或订阅 WebSocket 等状态事件（推荐，见 [06-实时日志与WebSocket](./06-实时日志与WebSocket.md)）。

**需要人工确认的 agent 操作同样返回 202。** 这是一个刻意的选择：确认请求已经被创建、已经推给前端、正在等人点按钮，这是标准的"已受理，尚未完成"，用 403 或 409 都会让调用方误以为请求被否决了。响应体形状不同，用 `status` 区分：

```http
HTTP/1.1 202 Accepted
Location: /api/confirmations/2b7d...

{
  "status": "pending_confirmation",
  "confirmation": {
    "id": "2b7d...",
    "action": "submit_pipeline",
    "risk_level": "consume",
    "reason": "任务参数 stone=3 命中消耗类操作，需人工确认",
    "expires_at": "2026-09-16T06:32:00Z"
  }
}
```

MCP 的同步阻塞式调用不会看到这个 202——它在服务端内部等待确认结果，超时后收到 `CONFIRMATION_EXPIRED`，被拒后收到 `CONFIRMATION_REJECTED`。两种语义（异步受理 / 同步阻塞）都提供，是既定决策。

### 2.2 400 与 422 的分界

界线是"**Pydantic 能不能表达**"。能被字段类型、`ge`/`le`、`Literal`、`model_validator` 拦住的，一律 422，错误码 `VALIDATION_ERROR` 或 `TASK_PARAM_INVALID`，`details` 里带 FastAPI 原生的字段级错误列表。拦不住的语义问题——请求体不是合法 JSON、discriminator 取值不在已知任务类型内、设置项 key 不存在、cron 字符串 APScheduler 解析不了——用 400。

这条线的实用价值在于前端：422 一定能定位到具体字段并高亮表单项，400 只能弹一条整体提示。

### 2.3 409 与 422 的分界

422 是"这个请求本身不对"，409 是"这个请求现在不行"。同一个请求体，五分钟后重发可能就成功了的，用 409。因此：任务参数越界是 422，流水线正在运行是 409；cron 非法是 400，定时任务名重复是 409。

### 2.4 502 / 503 / 504 的分界

三者都表示"不是客户端的错"，区别在于下游处于什么状态，以及客户端该怎么处置。

**503 Service Unavailable —— 依赖暂时不可用，等一等再来。** 内核还在加载资源、设备没连上、LLM 没配置。这类响应带 `Retry-After`，前端会自动重试或显示"等待中"。

**502 Bad Gateway —— 依赖被调用了，但它失败了。** `AsstAppendTask` 返回 0、`adb shell` 非零退出、下载源返回 500、LLM 端点报 `invalid_api_key`。这类不该自动重试，要把失败原因显示给用户。

**504 Gateway Timeout —— 依赖被调用了，但没在时限内应答。** MaaCore 子进程命令超时（`CORE_COMMAND_TIMEOUT`）与 LLM 上游超时（`LLM_TIMEOUT`）都归这一类。

区分 504 与 502 的价值在于两者的**后续处置不同**。502 意味着下游给了明确答复"这事我办不成"，原因通常就在响应里，重试大概率还是同样的结果。504 意味着下游没答复，那么操作**可能已经执行了一半**——`CORE_COMMAND_TIMEOUT` 尤其如此：命令已经投递进子进程队列，超时只说明没在预算内收到 `CMD_RESULT`，任务可能正在跑。因此 504 后客户端要做的是查状态（`GET /api/pipelines/current`）而不是重试，盲目重试可能提交两遍。

这个区分同时让监控告警能分开统计"内核执行失败"与"内核卡住"，这两类的根因与处理方式完全不同：前者通常是参数或资源问题，后者往往意味着该重启内核了。

规则：**"下游明确失败"用 502，"下游未在时限内响应"用 504。** 判断依据是有没有收到答复，不是失败得有多严重。据此 `UPDATE_DOWNLOAD_FAILED`（下载中断或返回非 2xx）仍是 502 —— 下载中断是传输层给了明确结果，不是没答复。

### 2.5 框架自动产生的状态码

`405 Method Not Allowed`、`307 Temporary Redirect`（尾斜杠重定向）由 Starlette 产生，不进错误码表。为避免 307 干扰前端与 MCP 客户端，所有路由**不带尾斜杠**，并设 `redirect_slashes=False`。

## 3. 响应体约定

### 3.1 成功响应不包封套

**结论：2xx 直接返回资源体本身，不包 `{code, message, data}` 这一层。**

理由有三。既然状态码已经承担了成败语义，`code` 字段就是纯冗余，`message` 在成功时永远是 `"success"` 这种无信息量的占位，`data` 则让每一处消费都要多写一次 `.data`。

更实际的理由是生态。FastAPI 的 `response_model` 直接映射资源模型，封套会迫使每个模型都套一层泛型 `Response[T]`，而 OpenAPI 的泛型展开在客户端代码生成器里支持得很差——[10-开放API与调试台](./10-开放API与调试台.md) 要从 OpenAPI 生成 TypeScript 类型，裸模型生成出来就是 `Pipeline`，封套模型生成出来是 `ResponseWrapper_Pipeline_` 且 `data` 是可空的，每处都要断言。MCP 的 tool 返回值同理：工具的结构化输出直接就是资源体，多一层封套要么传给模型造成 token 浪费，要么在工具实现里手工剥掉。

第三个理由是错误体已经**有**封套（`{"error": {...}}`）。成功裸返回、失败带 `error` 键，两者形状不同反而是好事：`if ("error" in body)` 之外还有状态码可判，不存在歧义。

单值响应也不例外。`GET /api/core/version` 返回 `{"version": "v6.17.3"}` 而不是裸字符串——JSON 顶层给对象，留出后续加字段的空间。

### 3.2 分页

所有列表端点统一：

```json
{ "items": [...], "total": 137, "page": 1, "size": 20 }
```

查询参数固定为 `page`（从 1 开始）与 `size`（默认 20，上限 200）。`page` 或 `size` 越界返回 `400 INVALID_PAGINATION`，不做静默钳制——静默钳制会让前端误以为自己拿到了第 999 页。

日志端点额外支持 `after_id` / `before_id` 游标（配合 `log_entry.id` 的单调性），用于 WebSocket 断线后补齐缺口。游标模式与 `page` 互斥，同时传两者返回 `400 INVALID_PAGINATION`。日志端点还有两处例外：`size` 上限放宽到 1000（导出预览与断线补发会一次性拉取大段记录），且默认值为 100 而非 20。查询语义与 `pipeline_id` 关联方式详见 [06-实时日志与WebSocket §9](./06-实时日志与WebSocket.md)，那里同时说明了为什么日志应当一律走游标而不用 `page`。

### 3.3 空值与缺省

响应体中语义上"尚未发生"的字段返回 `null` 而不是省略键（`started_at: null`），这样 TypeScript 的类型是 `string | null` 而非 `string | undefined`，前端少一层判断。`GET /api/pipelines/current` 在无运行中流水线时返回 `200` + `{"pipeline": null}`，不返回 404——"当前没有在跑"是正常状态，不是资源缺失。

## 4. 错误码表

错误码定义在 `maa_api/domain/errors.py` 的 `ErrorCode(StrEnum)` 中，与领域异常一一对应；`maa_api/api/errors.py` 注册异常处理器，把领域异常翻译成上表的状态码与统一错误体。共 92 条，其中 `UPDATE_INTERRUPTED` 只落库不返回，不占 HTTP 表达。

```python
# maa_api/domain/errors.py
class AppError(Exception):
    code: ErrorCode
    http_status: int
    message: str
    details: dict[str, Any] | None
```

每个错误码在代码里绑定固定的 HTTP 状态码，路由层不再自行决定状态码——这保证了同一个错误在不同端点上表现一致。

### 4.1 鉴权

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `UNAUTHORIZED` | 401 | token 缺失或与 `access_token` 不匹配。四个渠道都没取到有效 token 时返回 |
| `FORBIDDEN` | 403 | 身份有效但动作被策略拒绝：MCP 只读工具集调用了写操作；非同源请求试图仅凭 cookie 执行写操作 |
| `RATE_LIMITED` | 429 | 同一来源 IP 连续鉴权失败超过阈值（默认 10 次/分钟）后的冷却期 |

### 4.2 参数与请求格式

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `MALFORMED_JSON` | 400 | 请求体不是合法 JSON，或 `Content-Type` 与实际内容不符 |
| `VALIDATION_ERROR` | 422 | Pydantic 字段校验失败。`details.fields` 为 FastAPI 原生的字段级错误数组 |
| `INVALID_PARAMETER` | 400 | 结构合法但语义不成立：未知的设置项 key、互斥参数同时出现、枚举字符串不在取值域 |
| `INVALID_PAGINATION` | 400 | `page < 1`、`size` 超过 200，或同时传了 `page` 与 `after_id` |

### 4.3 通用

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `NOT_FOUND` | 404 | 未被更具体的码覆盖的资源缺失，兜底用 |
| `ENDPOINT_REMOVED` | 410 | 访问了重构前的旧端点，见 §12 |
| `INTERNAL_ERROR` | 500 | 未捕获异常。`details` 只带一个 `trace_id`，堆栈进日志表 |
| `DATABASE_ERROR` | 500 | SQLite 写锁超时、磁盘写满、schema 不匹配 |
| `SERVICE_UNAVAILABLE` | 503 | 服务处于启动中或关闭中，尚未/不再接受业务请求 |

### 4.4 内核状态

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `CORE_NOT_READY` | 503 | 子进程处于 `STOPPED` / `STARTING`，资源尚未加载完。`details.state` 带具体状态 |
| `CORE_RESTARTING` | 503 | 子进程处于 `RESTARTING`，通常发生在内核热更新期间 |
| `CORE_CRASHED` | 503 | 子进程已崩溃且未恢复。同时用作被中断流水线的 `error_code` |
| `CORE_START_FAILED` | 500 | 连续崩溃达到退避上限进入 `FAILED`，自动重启已停止，需人工介入 |
| `CORE_COMMAND_FAILED` | 502 | 命令投递到子进程但执行失败（如 `AsstAppendTask` 返回 0）。`details.cmd` 带命令类型 |
| `CORE_COMMAND_TIMEOUT` | 504 | 命令在超时内未收到 `CMD_RESULT`。超时不代表子进程已死，仍由心跳判定；命令可能仍在执行，客户端应查状态而非重试 |
| `CORE_NOT_FOUND` | 404 | `core_id` 不存在。首版只有 `default`，为多实例扩展预留 |
| `RESOURCE_LOAD_FAILED` | 500 | `LOAD_RESOURCE` 失败，通常是 MAA 资源目录缺失或损坏 |
| `MAP_LEVEL_KEY_UNAVAILABLE` | 503 | `AsstGetMapLevelKey` 不可用。该 API 属实验性接口，本地内核未导出或调用异常时优雅降级 |

### 4.5 设备连接

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `DEVICE_NOT_CONNECTED` | 503 | 设备状态机不在已连接态。任务执行前预检失败也报这个 |
| `ADB_CONNECT_FAILED` | 502 | `adb connect` 或 `AsstAsyncConnect` 失败。`details` 带 `address` 与已尝试次数 |
| `ADB_NOT_FOUND` | 500 | 配置的 `adb.path` 不存在或不可执行，属本地环境问题 |
| `ADB_COMMAND_FAILED` | 502 | ADB 原子操作（swipe / 长按 / 输入文本 / 按键）执行失败 |
| `DEVICE_SCAN_FAILED` | 502 | `adb devices` 列举失败，通常是 adb server 未启动 |
| `DEVICE_RESOLUTION_UNSUPPORTED` | 503 | 设备已连上但分辨率不被内核支持，自动化无法进行。`details` 带实际分辨率 |

### 4.6 截图

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `SCREENSHOT_FAILED` | 502 | 内核 `AsstAsyncScreencap` 或 ADB 截屏失败。`details.backend` 指明是哪条通道 |
| `SCREENSHOT_NOT_FOUND` | 404 | 截图 id 不存在 |
| `SCREENSHOT_EXPIRED` | 410 | 记录存在但文件已被保留策略清理（`screenshot.deleted_at` 非空） |

### 4.7 流水线与任务

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `PIPELINE_NOT_FOUND` | 404 | id 不存在，或已被 90 天/500 条保留策略清理 |
| `PIPELINE_ALREADY_RUNNING` | 409 | 流水线运行中执行原子操作（未带 `force=true`），或试图启动第二条流水线 |
| `PIPELINE_NOT_CANCELLABLE` | 409 | 目标流水线已处于终态，无可取消 |
| `PIPELINE_EMPTY` | 400 | 提交的 `tasks` 数组为空 |
| `PIPELINE_TOO_MANY_TASKS` | 400 | 单条流水线任务数超过 32 |
| `TASK_NOT_FOUND` | 404 | 任务 id 不存在 |
| `UNKNOWN_TASK_TYPE` | 400 | `name` 字段不在 9 种任务类型内，discriminated union 匹配失败 |
| `TASK_PARAM_INVALID` | 422 | 跨字段规则不满足：`mode=10000` 缺 `filename`、`mode=5` 但 `theme != "Sami"`、`series` 越界 |
| `TASK_PARAM_DEPRECATED` | 400 | 使用了内核已弃用的参数值，如 `Roguelike.mode=2` |
| `TASK_NOT_RUNTIME_MUTABLE` | 409 | 对运行中的任务修改了标注"不支持运行中设置"的参数（`stage`、`facility`、`shopping` 等） |
| `IDEMPOTENCY_KEY_CONFLICT` | 409 | 同一 `Idempotency-Key` 被用于内容不同的两次提交 |

### 4.8 队列

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `QUEUE_FULL` | 429 | 待执行流水线数超过上限（默认 50）。带 `Retry-After` |
| `QUEUE_PAUSED` | 409 | 队列被暂停（维护或更新期间）时提交，需先 resume |
| `QUEUE_ITEM_NOT_PENDING` | 409 | 调整优先级或移出队列的目标已不是 `PENDING` 状态 |

### 4.9 三种热更新

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `UPDATE_NOT_FOUND` | 404 | 更新记录 id 不存在 |
| `UPDATE_ALREADY_RUNNING` | 409 | 同一 `target` 已有进行中的更新，由部分唯一索引在库层拦住 |
| `UPDATE_BLOCKED_BY_PIPELINE` | 409 | 队列非空或有流水线运行中，且请求未带 `force=true` |
| `ALREADY_LATEST_VERSION` | 409 | 当前已是最新版本且未带 `force=true` |
| `UPDATE_MANIFEST_UNAVAILABLE` | 502 | 版本清单接口不可达（内核版本 API、OTA 资源清单、游戏版本接口） |
| `UPDATE_DOWNLOAD_FAILED` | 502 | 下载中断或返回非 2xx |
| `UPDATE_CHECKSUM_MISMATCH` | 502 | 下载产物校验和不符，判定为传输损坏或源被污染 |
| `UPDATE_EXTRACT_FAILED` | 500 | 解压或覆盖文件失败，通常是磁盘空间或权限问题 |
| `UPDATE_ROLLBACK_FAILED` | 500 | 更新失败后回滚也失败，内核目录可能处于不一致状态，必须人工处理 |
| `UPDATE_NOT_CANCELLABLE` | 409 | 更新已进入不可中断阶段（覆盖文件、重启内核）后请求取消 |
| `UPDATE_DISK_INSUFFICIENT` | 500 | 下载前的磁盘预检不通过，按 §2 归入"本地环境故障"。`details` 带 `required_bytes` 与 `available_bytes`，让用户自己判断要清理多少 |
| `UPDATE_QUEUE_BUSY_TIMEOUT` | 409 | 等待队列空闲超时（默认 30 分钟）且请求未带 `force_interrupt=true` |
| `UPDATE_INTERRUPTED` | — | 服务被强杀导致更新中断。只作为 `update_record.error_code` 落库，由启动时的残留记录清理写入，不对应任何 HTTP 响应 |
| `GAME_INSTALL_FAILED` | 502 | `adb install` 返回失败，`details.adb_output` 带原始输出 |
| `GAME_VERSION_UNKNOWN` | 502 | `dumpsys` 解析不出已安装版本，无法做版本对比 |

### 4.10 人工确认

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `CONFIRMATION_REQUIRED` | 202 | 操作命中消耗类或破坏类策略，已创建确认请求。**唯一出现在 2xx 响应中的码**，位于 202 的正常响应体而非错误体；MCP 同步调用时作为工具结果的 `code` 返回 |
| `CONFIRMATION_NOT_FOUND` | 404 | 确认请求 id 不存在 |
| `CONFIRMATION_EXPIRED` | 409 | 超时未响应，已自动拒绝。默认超时按风险分级：消耗类与破坏类 10 分钟，原子操作会话授权 120 秒 |
| `CONFIRMATION_REJECTED` | 403 | 用户明确拒绝。`details.reason` 带拒绝原因 |
| `CONFIRMATION_ALREADY_RESOLVED` | 409 | 重复批准或拒绝已进入终态的确认请求 |

### 4.11 Agent 与 LLM

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `AGENT_DISABLED` | 503 | Agent 模块未启用 |
| `AGENT_SESSION_NOT_FOUND` | 404 | 会话 id 不存在 |
| `AGENT_SESSION_BUSY` | 409 | 该会话上一轮 tool-calling 循环尚未结束 |
| `TOOL_NOT_FOUND` | 404 | `ToolRegistry` 中没有该工具名 |
| `TOOL_ARGS_INVALID` | 422 | 工具参数不满足其 JSON Schema |
| `TOOL_EXECUTION_FAILED` | 500 | 工具实现内部抛出未预期异常。被工具调用的下游错误（内核、设备）按其本身的码原样上抛，不包成这个码 |
| `LLM_NOT_CONFIGURED` | 503 | `base_url` / `api_key` / `model` 三项未配齐 |
| `LLM_REQUEST_FAILED` | 502 | 上游返回非 2xx。`details.upstream_status` 与 `details.upstream_code` 透传 |
| `LLM_TIMEOUT` | 504 | 上游在超时内未返回 |
| `LLM_RATE_LIMITED` | 429 | 上游 429 透传，`Retry-After` 沿用上游值 |
| `LLM_CONTEXT_OVERFLOW` | 400 | 会话历史超出模型上下文窗口，需新建会话或裁剪历史 |

tool-calling 循环达到步数上限**不是错误**：会话正常结束，最后一条 `agent_message.finish_reason` 记为 `max_steps`，接口返回 200。把它做成错误码会让前端丢掉模型已经产出的中间结论。

### 4.12 定时任务

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `SCHEDULE_NOT_FOUND` | 404 | 定时任务 id 不存在 |
| `SCHEDULE_CRON_INVALID` | 400 | cron 表达式 APScheduler 无法解析，或 `timezone` 不是合法 IANA 时区名 |
| `SCHEDULE_NAME_CONFLICT` | 409 | 名称重复，撞 `schedule.name` 唯一约束 |

### 4.13 设置与通知

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `SETTING_KEY_UNKNOWN` | 400 | key 不在 `settings_schema` 内 |
| `SETTING_VALUE_INVALID` | 422 | 值类型或取值范围不符合该项的 schema |
| `SETTING_READONLY` | 403 | 试图修改只能从 `config.yaml` 或环境变量设置的项（`app.access_token`、`app.maa_core_path`、`adb.path`） |
| `SETTING_APPLY_FAILED` | 500 | 值已入库但热生效动作失败，如改完 ADB 地址后重连失败。`details.applied` 标明是否已落库 |
| `NOTIFY_CHANNEL_NOT_FOUND` | 404 | 通道 id 不存在 |
| `NOTIFY_CHANNEL_CONFLICT` | 409 | `(type, name)` 重复 |
| `NOTIFY_CONFIG_INVALID` | 422 | 通道配置不满足该类型的 schema，如 webhook 缺 `url`、bark 缺 `device_key` |
| `NOTIFY_SEND_FAILED` | 502 | 测试发送或实际推送失败。`details` 带上游响应 |

### 4.14 资源

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `RESOURCE_ASSET_NOT_FOUND` | 404 | Copilot 作业 / 基建方案 / 自定义 task 不存在 |
| `RESOURCE_ASSET_CONFLICT` | 409 | `(kind, name)` 重复 |
| `COPILOT_JSON_INVALID` | 422 | 作业 JSON 缺必需字段或结构不符 |
| `INFRAST_PLAN_INVALID` | 422 | 基建方案 JSON 结构不符，或引用了不存在的设施名 |
| `CUSTOM_TASK_INVALID` | 422 | 自定义 task 定义不符合内核 `tasks.json` 的结构约定 |
| `ASSET_TOO_LARGE` | 400 | 单个资源超过 2 MB 上限 |

## 5. 鉴权设计

### 5.1 四个渠道与优先级

单一 `access_token`，支持四种传递方式。取值按固定优先级，取到第一个非空值即停止，**不做回退尝试**——若 `Authorization` 头存在但值错误，直接判 401，不再去看 query 参数，否则攻击者可以用正确的 query 掩盖错误的头，日志会记混。

| 顺序 | 渠道 | 形式 | 主要使用者 |
|---|---|---|---|
| 1 | `Authorization` 头 | `Authorization: Bearer <token>` | MCP 客户端、脚本、curl |
| 2 | `X-Token` 头 | `X-Token: <token>` | 前端 SPA 的 `fetch` 封装 |
| 3 | query 参数 | `?token=<token>` | WebSocket、可分享的调试链接 |
| 4 | cookie | `maa_token=<token>` | WebSocket、PWA 场景下的静态资源 |

排序依据是显式程度与泄露风险。头部是 HTTP 认证的标准位置，排最前。query 参数会进入访问日志、浏览器历史与 `Referer`，泄露面最大，因此排在头部之后——但**必须支持**，因为浏览器的 `WebSocket` 构造器不允许自定义请求头。cookie 排最后是因为它会被浏览器自动附加，存在 CSRF 风险。

针对 cookie 的 CSRF 风险有一条额外限制：**仅凭 cookie 鉴权的请求只允许 `GET`、`HEAD` 与 WebSocket 升级**。写方法（`POST`/`PUT`/`PATCH`/`DELETE`）如果 token 只来自 cookie，返回 `403 FORBIDDEN`。前端 SPA 本来就走 `X-Token`，不受影响；这条限制只挡住"用户在别的网站上被诱导对本服务发起写请求"的场景。

CORS 配置必须同步收紧。现有 `main.py` 里 `allow_origins=["*"]` 与 `allow_credentials=True` 并存，这个组合浏览器会直接拒绝（通配符不允许携带凭据），等于现在的 CORS 配置实际上是失效的。新配置改为显式 origin 白名单（默认 `http://localhost:8002` 加局域网地址，可在设置页添加），保留 `allow_credentials=True`。

### 5.2 未配置 token 时的行为

`access_token` 为空或未配置时**全部端点免鉴权**，这是对现有行为的完整保留（现有 `token_auth` 就是 `if access_token and access_token != token` 才拒绝）。本项目主要跑在家庭局域网，强制鉴权会显著劣化初次使用体验。

但要把这个状态说清楚，不能让它静默：启动日志打印一行醒目的 `未配置 access_token，API 处于免鉴权模式`；`GET /api/system/health` 的响应体带 `"auth_enabled": false`；前端读到该字段后在设置页顶部常驻一条警告，并在设置页提供一键生成随机 token 的入口。

### 5.3 豁免鉴权的端点

即使配置了 token，以下端点也不校验：

- `GET /api/system/health` —— 供外部监控与容器健康检查探测，且只返回状态不返回数据
- `GET /docs`、`GET /redoc`、`GET /openapi.json` —— 只暴露接口结构，不暴露业务数据。"Try it out" 发出的实际请求仍需 token；自研调试台会自动带上 token，这是它相对原生 `/docs` 的主要优势
- `/static/*`、SPA catch-all、`/manifest.webmanifest`、`/sw.js` —— 前端资源必须先加载出来，用户才有地方填 token。这是对现有行为的**有意修正**：现在 `GET /` 与 `GET /daily` 都挂了 `token_auth`，意味着没有 token 连页面都打不开，只能靠 URL 带 `?token=` 访问

`/mcp` 不豁免。

### 5.4 WebSocket 的鉴权

浏览器的 `WebSocket` 构造器**不支持自定义请求头**，这是 WHATWG 规范的限制，没有变通办法。因此 WS 只能用渠道 3（query）或渠道 4（cookie）：

```javascript
// 同源部署时推荐：先用 X-Token 调一次登录接口换取 HttpOnly cookie，WS 不带任何参数
const ws = new WebSocket(`ws://${location.host}/api/ws`);

// 跨源或无 cookie 场景：token 走 query
const ws = new WebSocket(`ws://${host}/api/ws?token=${token}`);
```

推荐前者：`POST /api/system/auth/cookie` 用头部 token 换一个 `HttpOnly; SameSite=Lax` 的 `maa_token` cookie，之后 WS 握手自动携带，token 不出现在 URL 里。这条端点归入 system 组。

鉴权失败的告知方式要注意 Starlette 的行为：在 `accept()` 之前拒绝连接，客户端只能看到一个 HTTP 403，拿不到原因。因此实现上**先 `accept()`，再按需 `close(code=...)`**，前端可以从 close code 判断该做什么：

| close code | 含义 | 前端处置 |
|---|---|---|
| `4401` | token 缺失或无效 | 跳到设置页要求填 token，不重连 |
| `4403` | 仅凭 cookie 鉴权但来源不可信 | 不重连 |
| `4429` | 连接数超限（默认 8 条） | 指数退避后重连 |
| `1012` | 服务端重启 | 立即重连 |

协议细节与事件格式见 [06-实时日志与WebSocket](./06-实时日志与WebSocket.md)。

### 5.5 MCP 的鉴权

Streamable HTTP 传输走标准 `Authorization: Bearer`，与 REST 完全一致。MCP 客户端（Claude Desktop、Cursor）都支持在服务器配置里声明请求头。会话通过 `Mcp-Session-Id` 头维持，该 id 只做会话关联，**不作为凭据**——每个请求都要独立校验 token。

stdio 入口（`scripts/mcp_stdio.py`）不经网络，token 从 `config.yaml` 或环境变量读取后在进程内直接调用服务层，跳过 HTTP 鉴权。它的安全边界是操作系统的进程与文件权限：能运行这个脚本的人本来就能读 `config.yaml`。

## 6. 全量路由清单

共 96 条 API 端点，加 4 条静态与文档入口。`core_id` 查询参数在所有内核相关端点上可选，缺省 `default`，为多实例扩展预留，下表不再逐条重复。

### 6.1 system

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/system/health` | 健康检查。返回内核状态、设备状态、队列长度、`auth_enabled` | 免鉴权 | 200 | — |
| GET | `/api/system/info` | 服务版本、内核版本、平台、启动时间、数据库大小 | | 200 | — |
| GET | `/api/system/stats` | 首页概览：今日流水线数、成功率、待确认数、最近失败原因 Top N | `days` 默认 7 | 200 | `INVALID_PARAMETER` |
| GET | `/api/system/logs` | 历史日志查询 | `source[]`、`level[]`、`pipeline_id`、`task_id`、`logger`、`q`、`since`、`until`、`after_id`、`before_id`、`order`、`page`、`size` | 200 | `INVALID_PAGINATION` |
| GET | `/api/system/logs/export` | 导出为附件下载 | `format`（`txt` / `jsonl`）、其余过滤参数同上 | 200 | `INVALID_PARAMETER` |
| DELETE | `/api/system/logs` | 按条件清理日志 | `source[]`、`before` 必填其一 | 204 | `INVALID_PARAMETER` |
| POST | `/api/system/auth/cookie` | 用头部 token 换取 `HttpOnly` cookie，供 WebSocket 使用 | 无请求体 | 204 | `UNAUTHORIZED` |

（6 条）

### 6.2 ws

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/ws` | WebSocket 升级。推送日志、流水线状态、队列变更、内核与设备状态、更新进度、待确认事件 | token 走 query 或 cookie；可选 `last_seen_id` 补发缺口 | 101 | close `4401`/`4429` |

（1 条）

### 6.3 device

设备相关操作分两条通道，`backend` 字段在响应中明示。**`click` 走 MaaCore 的 `AsstAsyncClick`；`swipe` / `long_press` / `input_text` / `key_event` 一律走 ADB**——MaaCore 的 C API 里只有点击，没有滑动、长按、输入文本与按键，这是内核的硬约束。所有原子操作**不入队列**，直接投递；流水线运行中调用返回 `409 PIPELINE_ALREADY_RUNNING`，除非带 `force=true`（会被审计标记为强制介入）。

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/device/status` | 设备状态机当前状态、地址、分辨率、UUID、最近连接时间 | | 200 | — |
| POST | `/api/device/reconnect` | 手动触发重连（四时机之一） | `address` 可选，覆盖配置 | 202 | `ADB_CONNECT_FAILED`、`ADB_NOT_FOUND` |
| GET | `/api/device/list` | 扫描 `adb devices` 可见设备，供设置页下拉选择 | | 200 | `DEVICE_SCAN_FAILED`、`ADB_NOT_FOUND` |
| GET | `/api/device/screenshot` | 取一张实时截图 | `backend`（`core`/`adb`，默认 `adb`）、`format`、`quality`、`archive`（默认 false） | 200 | `SCREENSHOT_FAILED`、`DEVICE_NOT_CONNECTED` |
| POST | `/api/device/click` | 单点点击，MaaCore 通道 | `{x, y, force?}` | 200 | `CORE_NOT_READY`、`PIPELINE_ALREADY_RUNNING` |
| POST | `/api/device/swipe` | 滑动，ADB 通道 | `{x1, y1, x2, y2, duration_ms?, force?}` | 200 | `ADB_COMMAND_FAILED`、`PIPELINE_ALREADY_RUNNING` |
| POST | `/api/device/long_press` | 长按，ADB 通道（实现为 duration 较长的 swipe 原地操作） | `{x, y, duration_ms, force?}` | 200 | `ADB_COMMAND_FAILED` |
| POST | `/api/device/input_text` | 输入文本，ADB 通道 | `{text, force?}` | 200 | `ADB_COMMAND_FAILED` |
| POST | `/api/device/key_event` | 按键事件，ADB 通道。`key` 为白名单枚举（`BACK`/`HOME`/`ENTER`/`DEL`/`APP_SWITCH`） | `{key, force?}` | 200 | `INVALID_PARAMETER`、`ADB_COMMAND_FAILED` |

**不开放任意 `adb shell`。** 按键用枚举白名单而非透传字符串，是为了守住这条边界——透传 keycode 字符串等价于开放一个受限的 shell。

（9 条）

### 6.4 core

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/cores` | 内核实例列表。首版固定返回一条 `default` | | 200 | — |
| GET | `/api/core/status` | 子进程状态机状态、pid、启动世代号、连续崩溃次数、心跳延迟 | | 200 | `CORE_NOT_FOUND` |
| POST | `/api/core/restart` | 重启子进程 | `{force?}`：非 force 时队列非空则拒绝 | 202 | `UPDATE_BLOCKED_BY_PIPELINE`、`CORE_START_FAILED` |
| GET | `/api/core/version` | 内核版本号（`AsstGetVersion`），带缓存 | | 200 | `CORE_NOT_READY` |
| POST | `/api/core/back_to_home` | 回游戏主界面，MaaCore 原生能力 | `{force?}` | 200 | `CORE_NOT_READY`、`PIPELINE_ALREADY_RUNNING` |
| POST | `/api/core/screencap` | 触发内核截图并归档，返回 `screenshot` 资源 | `{pipeline_id?, trigger?}` | 201 | `SCREENSHOT_FAILED`、`CORE_NOT_READY` |
| GET | `/api/core/tasks` | 内核队列中的 task id 列表（`AsstGetTasksList`），调试用 | | 200 | `CORE_NOT_READY` |
| GET | `/api/core/map_level_key` | 关卡名互查。**实验性 API**，不可用时降级 | `key` 必填 | 200 | `MAP_LEVEL_KEY_UNAVAILABLE` |

`GET /api/core/map_level_key` 包装的 `AsstGetMapLevelKey` 位于 `AsstCallerExtra.h`，官方明确标注不属于受支持的公共 API、可能随时变更或移除。封装层必须在符号缺失时返回 `503 MAP_LEVEL_KEY_UNAVAILABLE` 而不是崩溃，前端把该功能做成可选增强而非必需依赖。

（8 条）

### 6.5 pipelines

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| POST | `/api/pipelines` | 提交流水线，入队 | `{tasks[], title?, priority?, notify_on_finish?}`；可选 `Idempotency-Key` 头 | 202 | `PIPELINE_EMPTY`、`UNKNOWN_TASK_TYPE`、`TASK_PARAM_INVALID`、`QUEUE_FULL`、`QUEUE_PAUSED` |
| GET | `/api/pipelines` | 历史列表，分页 | `status`、`source`、`schedule_id`、`since`、`page`、`size` | 200 | `INVALID_PAGINATION` |
| GET | `/api/pipelines/current` | 当前运行中的流水线（含任务进度）。无运行中时返回 `{"pipeline": null}` | | 200 | — |
| GET | `/api/pipelines/{id}` | 详情，含任务列表与统计 | | 200 | `PIPELINE_NOT_FOUND` |
| DELETE | `/api/pipelines/{id}` | 取消。运行中则先 `STOP` 内核再置终态；仅排队中则直接出队 | | 202 | `PIPELINE_NOT_FOUND`、`PIPELINE_NOT_CANCELLABLE` |
| POST | `/api/pipelines/{id}/retry` | 以 `raw_params` 重放为一条新流水线，`retry_of_id` 指回原条目 | `{priority?}` | 202 | `PIPELINE_NOT_FOUND`、`QUEUE_FULL` |
| GET | `/api/pipelines/{id}/tasks` | 任务列表（不分页，单流水线上限 32 个任务） | | 200 | `PIPELINE_NOT_FOUND` |
| GET | `/api/pipelines/{id}/logs` | 该流水线的日志，默认 `order=asc`（回放执行过程自然从头看） | `source[]`、`level[]`、`after_id`、`order`、`page`、`size` | 200 | `PIPELINE_NOT_FOUND` |
| GET | `/api/pipelines/{id}/screenshots` | 该流水线的截图列表（含已过期条目，带 `deleted_at`） | | 200 | `PIPELINE_NOT_FOUND` |

路由声明顺序有个实现约束：`/api/pipelines/current` 必须写在 `/api/pipelines/{id}` **之前**，否则 `current` 会被当成一个 id 走进详情端点并返回 404。`/api/tasks/types` 与 `/api/tasks/{task_id}` 同理。

（9 条）

### 6.6 queue

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/queue` | 队列快照：运行中 1 条 + 待执行列表（按 `priority, created_at` 排序），带来源与优先级 | | 200 | — |
| DELETE | `/api/queue` | 清空全部待执行条目，置 `CANCELLED`。不影响运行中的流水线 | `source?` 可只清某来源 | 204 | — |
| PATCH | `/api/queue/{pipeline_id}` | 调整待执行条目的优先级，实现插队 | `{priority}` | 200 | `PIPELINE_NOT_FOUND`、`QUEUE_ITEM_NOT_PENDING` |
| POST | `/api/queue/pause` | 暂停出队。运行中的流水线跑完后不再取新的 | | 200 | — |
| POST | `/api/queue/resume` | 恢复出队 | | 200 | — |

队列**不抢占**：更高优先级的新提交不会打断正在执行的流水线。想立刻插队必须显式 `DELETE /api/pipelines/{当前id}` 停掉当前流水线，前端把这一步做成明确的按钮而非隐式行为。`PATCH` 只影响尚未开始的条目。

（5 条）

### 6.7 tasks

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/tasks/types` | 9 种任务类型的完整参数 schema，供前端生成表单、agent 了解可用参数 | `lang` 默认 `zh` | 200 | — |
| GET | `/api/tasks/types/{type_name}` | 单个类型的 schema | | 200 | `UNKNOWN_TASK_TYPE` |
| POST | `/api/tasks/validate` | 只校验不提交，返回规范化后的参数与将被注入的默认值 | `{tasks[]}` | 200 | `UNKNOWN_TASK_TYPE`、`TASK_PARAM_INVALID` |
| GET | `/api/tasks/{task_id}` | 单任务详情（状态、重试次数、`params` 与 `raw_params`、耗时） | | 200 | `TASK_NOT_FOUND` |

`POST /api/tasks/validate` 存在的意义是给 agent 一个安全的试错入口：它可以先校验再提交，而不是提交失败后从错误信息里反推参数长什么样。

（4 条）

### 6.8 screenshots

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/screenshots` | 截图列表，分页 | `pipeline_id`、`trigger`、`since`、`page`、`size` | 200 | `INVALID_PAGINATION` |
| GET | `/api/screenshots/{id}` | 返回图像本体。`?as=base64` 时返回 JSON 包装，供 agent 与 MCP 使用 | | 200 | `SCREENSHOT_NOT_FOUND`、`SCREENSHOT_EXPIRED` |

（2 条）

### 6.9 updates

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/updates/status` | 三个目标的当前版本 vs 最新版本对比，带 `has_update`；`resource` 下按两条通道分别给出 | `refresh` 默认 false（用缓存）；`refresh=true` 即「立即检查更新」，绕过 5 分钟缓存 | 200 | `UPDATE_MANIFEST_UNAVAILABLE`、`GAME_VERSION_UNKNOWN` |
| POST | `/api/updates/core` | 更新 MaaCore。下载后停子进程、覆盖、重启，HTTP 服务不中断 | `{channel?, force?, version?}` | 202 | `UPDATE_ALREADY_RUNNING`、`ALREADY_LATEST_VERSION`、`UPDATE_BLOCKED_BY_PIPELINE` |
| POST | `/api/updates/resource` | 更新活动资源。`channel` 选通道，默认两条都更 | `{channel?, force?}`：`channel` 取 `ota` / `repo` / `all`，默认 `all` | 202 | `UPDATE_ALREADY_RUNNING`、`UPDATE_MANIFEST_UNAVAILABLE`、`UPDATE_DISK_INSUFFICIENT`、`INVALID_PARAMETER` |
| POST | `/api/updates/game` | 更新游戏本体：下载 APK + `adb install` 覆盖安装 | `{channel?, force?}`（`channel` 区分官服/B 服包） | 202 | `GAME_INSTALL_FAILED`、`DEVICE_NOT_CONNECTED` |
| GET | `/api/updates` | 更新历史，分页 | `target`、`status`、`page`、`size` | 200 | `INVALID_PAGINATION` |
| GET | `/api/updates/{id}` | 单条更新的进度与日志，供轮询 | | 200 | `UPDATE_NOT_FOUND` |
| DELETE | `/api/updates/{id}` | 取消进行中的更新。已进入覆盖/重启阶段则拒绝 | | 202 | `UPDATE_NOT_FOUND`、`UPDATE_NOT_CANCELLABLE` |

进度不靠轮询也能拿：更新过程的 `phase` 与 `progress` 同时经 WebSocket 广播，`GET /api/updates/{id}` 是给不方便用 WS 的调用方（脚本、MCP）留的。

活动资源有两条来源不同的通道（见 [07-热更新方案 §3](./07-热更新方案.md)），**但没有为它们拆出独立端点**。`POST /api/updates/resource` 用 `channel` 参数区分，取值对应 `ResourceChannel` 枚举（见 [04-数据模型与持久化 §4](./04-数据模型与持久化.md)）：

- `all`（默认）：两条通道依次检查并更新，任一有更新就触发一次重载
- `ota`：只更通道 A 的活动 task 定义，秒级完成
- `repo`：只更通道 B 的数据文件与模板图，需要下载约 13 MB 压缩包、解压出约 107 MB

不拆端点的理由与 `update_record.target` 不拆通道是同一条：三种更新共用一把互斥锁，同一时刻只允许一条 `target='resource'` 的记录在跑。两个端点会让调用方误以为可以并发触发，实际仍会撞 `UPDATE_ALREADY_RUNNING`。`channel='repo'` 才可能返回 `UPDATE_DISK_INSUFFICIENT`——通道 A 只有 15 KB，预检没有意义。

`GET /api/updates/status` 的 `resource` 段相应地按通道分列，两条通道的"版本"本来就不是同一种东西：

```json
{
  "resource": {
    "has_update": true,
    "channels": {
      "ota": {
        "has_update": false,
        "last_synced_at": "2026-09-16T03:11:20+08:00",
        "last_checked_at": "2026-09-16T07:02:44+08:00"
      },
      "repo": {
        "has_update": true,
        "current": "2026-09-05 11:08:36.000",
        "latest": "2026-09-14 04:36:14.000",
        "last_checked_at": "2026-09-16T07:02:44+08:00"
      }
    }
  }
}
```

通道 A 不给 `current` / `latest`，因为它的版本基准是 HTTP `ETag`，对用户没有展示价值，能回答"有没有更新"和"上次同步于何时"就够了；通道 B 给的是 `version.json` 的 `last_updated` 原值。外层 `has_update` 是两条通道的或，前端的"一键更新"按钮据此点亮。两条通道各自的数据来源是 `resource_asset` 表里对应的行（见 [04-数据模型与持久化 §5.13](./04-数据模型与持久化.md)），不读 MAA 目录里的 `version.json`。

（7 条）

### 6.10 schedules

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/schedules` | 定时任务列表，带 `next_run_at` 与上次执行结果 | `enabled` 可选过滤 | 200 | — |
| POST | `/api/schedules` | 新建。`template` 结构等同 `POST /api/pipelines` 的 `tasks` | `{name, cron, timezone?, template, enabled?, ...}` | 201 | `SCHEDULE_CRON_INVALID`、`SCHEDULE_NAME_CONFLICT`、`TASK_PARAM_INVALID` |
| GET | `/api/schedules/{id}` | 详情，含最近 N 次执行记录 | | 200 | `SCHEDULE_NOT_FOUND` |
| PUT | `/api/schedules/{id}` | 全量替换 | 同 POST | 200 | `SCHEDULE_NOT_FOUND`、`SCHEDULE_CRON_INVALID` |
| PATCH | `/api/schedules/{id}` | 局部更新，主要用于启用开关 | `{enabled?, cron?, priority?}` | 200 | `SCHEDULE_NOT_FOUND` |
| DELETE | `/api/schedules/{id}` | 删除 | | 204 | `SCHEDULE_NOT_FOUND` |
| POST | `/api/schedules/{id}/run` | 立即执行一次，按 `template` 提交流水线 | `{priority?}`：默认沿用 schedule 的优先级 | 202 | `SCHEDULE_NOT_FOUND`、`QUEUE_FULL` |

`template` 在创建与更新时就走一遍完整的任务校验，不等到触发时才报错——定时任务的失败最难被发现，配置阶段拦住是唯一有效的时机。

（7 条）

### 6.11 settings

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/settings` | 读取生效中的全部配置，敏感项脱敏为 `"***"`，并标注每项来自哪一层（env/db/yaml/default） | `group` 可选 | 200 | — |
| PUT | `/api/settings` | 批量写入并热生效。敏感项传 `"***"` 或空串视为不变更 | `{items: {key: value}}` | 200 | `SETTING_KEY_UNKNOWN`、`SETTING_VALUE_INVALID`、`SETTING_READONLY`、`SETTING_APPLY_FAILED` |
| GET | `/api/settings/schema` | 配置项元信息：中文标签、分组、类型、取值范围、枚举可选值、是否敏感、热生效动作 | | 200 | — |
| POST | `/api/settings/reset` | 清除指定项的数据库覆盖，回落到 `config.yaml` 或默认值 | `{keys[]}` | 200 | `SETTING_KEY_UNKNOWN` |

`PUT` 的部分成功要处理明确：值已入库但热生效失败时返回 `500 SETTING_APPLY_FAILED`，`details.applied = true` 告诉前端"配置已保存，但重连设备失败"，前端提示用户手动重连而不是让他以为没保存上。

（4 条）

### 6.12 notifications

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/notifications/channels` | 通道列表，`config` 脱敏，带最近发送状态 | `type`、`enabled` | 200 | — |
| POST | `/api/notifications/channels` | 新建通道 | `{type, name, config, events[], enabled?}` | 201 | `NOTIFY_CONFIG_INVALID`、`NOTIFY_CHANNEL_CONFLICT` |
| PUT | `/api/notifications/channels/{id}` | 更新通道 | 同 POST | 200 | `NOTIFY_CHANNEL_NOT_FOUND`、`NOTIFY_CONFIG_INVALID` |
| DELETE | `/api/notifications/channels/{id}` | 删除通道 | | 204 | `NOTIFY_CHANNEL_NOT_FOUND` |
| POST | `/api/notifications/channels/{id}/test` | 发送测试消息，同步等结果（超时 10 秒） | `{event?}`：默认用 `pipeline_completed` 的样例数据 | 200 | `NOTIFY_SEND_FAILED`、`NOTIFY_CHANNEL_NOT_FOUND` |

测试发送用 200 同步返回而不是 202，因为用户点"测试"就是要立刻知道配置对不对；异步受理会让他还得去别处查结果。

（5 条）

### 6.13 resources

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/resources/items` | 读取当前 MaaCore `resource/item_index.json`，供 `Fight.drops` 按名称搜索 | | `200 [{item_id, name, icon_url}]` | `RESOURCE_LOAD_FAILED` |
| GET | `/api/resources/items/icon` | 返回物品索引中登记的图标 | `item_id` | `200 image/png` | `RESOURCE_ASSET_NOT_FOUND`、`RESOURCE_LOAD_FAILED` |
| GET | `/api/resources/copilots` | Copilot 作业列表 | `page`、`size`、`q` | 200 | — |
| POST | `/api/resources/copilots` | 上传作业 JSON。校验结构并解析出关卡名存入 `meta` | `{name, description?, content}` | 201 | `COPILOT_JSON_INVALID`、`RESOURCE_ASSET_CONFLICT`、`ASSET_TOO_LARGE` |
| GET | `/api/resources/copilots/{id}` | 作业详情，含完整内容 | | 200 | `RESOURCE_ASSET_NOT_FOUND` |
| DELETE | `/api/resources/copilots/{id}` | 删除 | | 204 | `RESOURCE_ASSET_NOT_FOUND` |
| GET | `/api/resources/infrast-plans` | 自定义基建方案列表 | | 200 | — |
| POST | `/api/resources/infrast-plans` | 上传基建方案。解析方案数量供 `Infrast.plan_index` 校验 | `{name, description?, content}` | 201 | `INFRAST_PLAN_INVALID`、`RESOURCE_ASSET_CONFLICT` |
| GET | `/api/resources/infrast-plans/{id}` | 方案详情 | | 200 | `RESOURCE_ASSET_NOT_FOUND` |
| DELETE | `/api/resources/infrast-plans/{id}` | 删除 | | 204 | `RESOURCE_ASSET_NOT_FOUND` |
| GET | `/api/resources/custom-tasks` | 自定义 task 定义列表 | | 200 | — |
| POST | `/api/resources/custom-tasks` | 新增增量注入的 `tasks.json` task 定义 | `{name, description?, content, enabled?}` | 201 | `CUSTOM_TASK_INVALID`、`RESOURCE_ASSET_CONFLICT` |
| GET | `/api/resources/custom-tasks/{id}` | 详情 | | 200 | `RESOURCE_ASSET_NOT_FOUND` |
| DELETE | `/api/resources/custom-tasks/{id}` | 删除 | | 204 | `RESOURCE_ASSET_NOT_FOUND` |
| POST | `/api/resources/reload` | 把启用中的自定义 task 合并为增量资源目录，投递 `LOAD_RESOURCE` 命令重载内核资源 | `{force?}` | 202 | `RESOURCE_LOAD_FAILED`、`CORE_NOT_READY`、`UPDATE_BLOCKED_BY_PIPELINE` |

`GET /api/resources/items` 返回按名称（忽略大小写）与 `item_id` 排序的完整数组；`item_id` 是索引对象的 key，`name` 来自对应记录。`icon_url` 是同源 `/api/resources/items/icon?item_id=...` URL；索引记录的 `icon` 所指 PNG 不存在、不可读或路径不在 `resource/template/items/` 内时返回 `null`。图标端点仅允许读取索引中登记且解析后仍位于该目录的 PNG。索引缺失、不可读或结构无效时返回统一 JSON 错误 `RESOURCE_LOAD_FAILED`（500）。响应缓存按索引文件元信息失效；端点仅读本地文件，不加载 MaaCore，也不访问网络。

`POST /api/resources/reload` 是自定义任务能力的落地点：内核只认磁盘上的资源文件，增量注入必须经由 `LOAD_RESOURCE` 生效。`checksum` 没变时非 `force` 请求直接返回 202 且不实际重载，避免无谓的资源重加载（重载要把整条加载链从头走一遍，命令超时预算 300 秒，见 [02-系统架构设计 §3.4](./02-系统架构设计.md)）。

（13 条）

### 6.14 agent

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/agent/tools` | 工具清单与 JSON Schema，含 `risk_level` 标注 | `risk_level` 可选过滤 | 200 | `AGENT_DISABLED` |
| POST | `/api/agent/tools/{name}/invoke` | REST 调用方直接执行工具，与 MCP 共用 `ToolRegistry` | `{arguments, mode?}`：`mode=sync` 阻塞等结果，`mode=async` 立即返回 | 200 / 202 | `TOOL_NOT_FOUND`、`TOOL_ARGS_INVALID`、`CONFIRMATION_REQUIRED`（202） |
| GET | `/api/agent/sessions` | 会话列表，按 `last_message_at` 倒序 | `page`、`size` | 200 | — |
| POST | `/api/agent/sessions` | 新建会话，快照当前 `model` 与 `base_url` | `{title?}` | 201 | `LLM_NOT_CONFIGURED` |
| GET | `/api/agent/sessions/{id}` | 会话详情与 token 消耗统计 | | 200 | `AGENT_SESSION_NOT_FOUND` |
| DELETE | `/api/agent/sessions/{id}` | 删除会话，消息级联删除 | | 204 | `AGENT_SESSION_NOT_FOUND` |
| GET | `/api/agent/sessions/{id}/messages` | 消息与工具调用轨迹，按 `seq` 升序 | `after_seq`、`page`、`size` | 200 | `AGENT_SESSION_NOT_FOUND` |
| POST | `/api/agent/sessions/{id}/messages` | 发送用户消息，触发 tool-calling 循环。增量结果经 WebSocket 流式推送 | `{content}` | 202 | `AGENT_SESSION_BUSY`、`LLM_NOT_CONFIGURED`、`LLM_CONTEXT_OVERFLOW` |
| DELETE | `/api/agent/sessions/{id}/atomic-grant` | 撤销该会话的原子操作授权，挂起中的调用按拒绝处理 | | 204 | `AGENT_SESSION_NOT_FOUND` |
| GET | `/api/agent/audits` | 审计列表 | `caller`、`tool_name`、`status`、`risk_level`、`since`、`page`、`size` | 200 | `INVALID_PAGINATION` |
| GET | `/api/agent/audits/{id}` | 单条审计详情，含完整参数与结果摘要 | | 200 | `NOT_FOUND` |

LLM 配置（`base_url` / `api_key` / `model`）不在 agent 组下开独立端点，它是 `setting` 表的三个 key，走 `/api/settings`。这样设置页只有一处配置入口。

（10 条）

### 6.15 confirmations

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/confirmations` | 确认请求列表，默认只返回 `pending` | `status`、`page`、`size` | 200 | — |
| GET | `/api/confirmations/{id}` | 详情，含 `payload` 与命中的策略说明 | | 200 | `CONFIRMATION_NOT_FOUND` |
| POST | `/api/confirmations/{id}` | 批准或拒绝 | `{approved: bool, reason?}` | 200 | `CONFIRMATION_NOT_FOUND`、`CONFIRMATION_ALREADY_RESOLVED`、`CONFIRMATION_EXPIRED` |

用单个 `POST /api/confirmations/{id}` 带布尔字段，而不是 `/approve` 与 `/reject` 两个端点，原因是前端弹卡片的两个按钮共用一份请求逻辑，且拒绝必须能带 `reason`。返回 200 而非 202：批准这个动作本身是同步完成的（状态落库 + 唤醒等待方），被批准的动作是否完成则由它自己的资源去表达。

**会话级原子操作授权复用这同一套确认流程**，不另起端点。授权请求是一条 `action` 为 `grant_atomic_ops` 的确认记录，`payload` 为 `{"session_id": "...", "window_seconds": 900}`；批准它的"执行"就是在 `agent_session` 上写入授权窗口。撤销走上面的 `DELETE /api/agent/sessions/{id}/atomic-grant`，不是再发一条确认。这样前端只需实现一种确认卡片，只是文案按 `action` 区分（机制见 [11-Agent模块设计 §4.3](./11-Agent模块设计.md)）。

`GET /api/agent/sessions/{id}` 的响应带 `atomic_grant: {granted, expires_at, grant_id} | null`，供前端在页面刷新后恢复授权提示条——WebSocket 的 `atomic_grant_changed` 事件只能通知变化，刷新后的初始状态必须能从 REST 拿到。

（3 条）

### 6.16 mcp

MCP 采用 Streamable HTTP 传输，在同一个路径上用三个方法承载不同职责，这是协议规定的形状。

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| POST | `/mcp` | 发送 JSON-RPC 请求/通知。含 `initialize`、`tools/list`、`tools/call` | `Authorization: Bearer`；`Mcp-Session-Id` 头（初始化后） | 200 | `UNAUTHORIZED`、`TOOL_NOT_FOUND` |
| GET | `/mcp` | 打开 SSE 流接收服务端主动推送的消息 | 同上 | 200 | `UNAUTHORIZED` |
| DELETE | `/mcp` | 显式终止 MCP 会话 | `Mcp-Session-Id` 必填 | 204 | `UNAUTHORIZED` |

MCP 层的错误有两种表达：协议级错误（鉴权失败、会话无效）走 HTTP 状态码与 JSON-RPC error；工具执行错误作为 tool 结果的结构化内容返回，`isError=true` 且 body 带本文的 `code` / `message` / `details`，因为工具失败对模型而言是需要读懂并调整策略的信息，而不是传输层故障。stdio 入口不占用 HTTP 路由。

（3 条）

### 6.17 静态资源与文档

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| GET | `/docs` · `/redoc` · `/openapi.json` | FastAPI 原生文档，作为自研调试台的备用 | 豁免 |
| GET | `/static/{path:path}` | Vite 构建产物的静态挂载 | 豁免 |
| GET | `/` | SPA 入口，返回 `static/index.html` | 豁免 |
| GET | `/{path:path}` | SPA catch-all，非 `/api`、`/mcp`、`/static` 前缀的路径一律回落到 `index.html`，由前端路由接管 | 豁免 |

catch-all 必须是**最后注册**的路由，且显式排除 `/api`、`/mcp`、`/static` 三个前缀，否则拼错的 API 路径会返回一份 HTML 而不是 404 JSON——这种错误在前端调试时极难定位。

（4 条）

## 7. 任务提交的请求体设计

### 7.1 废弃扁平 `TaskRequest`

现有 `maa_api/model/request/request.py` 把 9 种任务的全部参数塞进一个类，约 70 个 `Optional` 字段，再用 `to_task()` 里的 if 链分发。这个设计有四个不可接受的后果：

提交 `Fight` 时可以传 `theme="Sami"`，校验器不会拦，参数被静默丢弃。字段在类里重复定义（`times` 出现两次，后一个覆盖前一个）。OpenAPI 文档里所有任务类型看起来都接受全部 70 个参数，前端与 agent 无法据此生成正确的表单或工具 schema。还有几个末尾带逗号的字段声明（`tools_to_craft: Optional[list[str]] = None,`）让默认值变成了元组 `(None,)`。

新设计按任务类型分离模型，用 Pydantic 的 **discriminated union**，discriminator 沿用现有的 `name` 字段——保持这个字段名可以让现有前端的请求体结构基本不变，降低迁移成本。

### 7.2 基类与字段装饰

```python
# maa_api/domain/task.py
from typing import Annotated, Any, Literal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


def MaaField(
    *,
    label: str,
    group: str = "基础",
    widget: str | None = None,
    enum_labels: dict[str, str] | None = None,
    **kwargs: Any,
):
    """在标准 Field 之上附加前端渲染所需的中文元信息。"""
    extra: dict[str, Any] = {"x-label": label, "x-group": group}
    if widget:
        extra["x-widget"] = widget
    if enum_labels:
        extra["x-enum-labels"] = enum_labels
    return Field(**kwargs, json_schema_extra=extra)


CLIENT_TYPES = Literal["Official", "Bilibili", "txwy", "YoStarEN", "YoStarJP", "YoStarKR"]
SERVERS = Literal["CN", "US", "JP", "KR"]


class TaskInputBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",             # 传了不属于本类型的参数直接 422，不再静默丢弃
        populate_by_name=True,      # 允许用 Python 字段名或 MAA 原名提交
        validate_assignment=True,
    )

    enable: bool | None = MaaField(default=None, label="启用本任务")

    def to_core_params(self) -> dict[str, Any]:
        """导出为投递给 AsstAppendTask 的参数字典。"""
        return self.model_dump(
            exclude={"name"},
            exclude_none=True,      # 未设置的参数不下发，由内核用自己的默认值
            by_alias=True,          # DrGrandet 等非 snake_case 字段用 MAA 原名
        )
```

`extra="forbid"` 是这次重构最重要的一处收紧：它把"参数写错了但没人告诉我"变成一个立刻可见的 422。

`exclude_none=True` 保留了现有 `Task.__init__` 里 `{k: v for k, v in params.items() if v is not None}` 的行为，但位置更合理——放在导出环节而不是构造环节，模型本身仍能区分"没设置"与"设为 null"。

### 7.3 三个代表性模型

`StartUpInput` 展示最简形态与渠道字段：

```python
class StartUpInput(TaskInputBase):
    name: Literal["StartUp"] = "StartUp"

    client_type: CLIENT_TYPES | None = MaaField(
        default=None, label="客户端版本", group="账号",
        description="未指定时套用全局渠道默认值（默认 Bilibili）",
    )
    start_game_enabled: bool | None = MaaField(
        default=None, label="自动启动客户端", group="账号",
    )
    account_name: str | None = MaaField(
        default=None, label="切换账号", group="账号",
        description="仅支持切换至已登录账号，按登录名模糊查找，需在已登录账号中唯一",
        examples=["123****4567", "4567", "张三"],
    )
```

`FightInput` 展示取值范围、消耗类参数与别名：

```python
class FightInput(TaskInputBase):
    name: Literal["Fight"] = "Fight"

    stage: str | None = MaaField(
        default=None, label="关卡名",
        description="留空则识别当前/上次关卡。剿灭作战须填 Annihilation；"
                    "可在结尾加 Normal/Hard 切换标准与磨难难度。不支持运行中设置",
        examples=["1-7", "S3-2", "Annihilation", "CE-6Hard"],
    )
    medicine: int | None = MaaField(default=None, label="最大理智药数", group="资源消耗", ge=0)
    expiring_medicine: int | None = MaaField(
        default=None, label="最大48小时内过期理智药数", group="资源消耗", ge=0,
    )
    stone: int | None = MaaField(default=None, label="最大碎石数", group="资源消耗", ge=0)
    times: int | None = MaaField(default=None, label="指定次数", ge=1)
    series: int | None = MaaField(
        default=None, label="连战次数", ge=-1, le=6, widget="select",
        enum_labels={"-1": "禁用切换", "0": "自动选择最大可用次数",
                     "1": "1 次", "2": "2 次", "3": "3 次", "4": "4 次", "5": "5 次", "6": "6 次"},
    )
    drops: dict[str, int] | None = MaaField(
        default=None, label="指定掉落数量", group="高级",
        description="key 为 item_id（见 resource/item_index.json），任一达到即停止",
    )
    report_to_penguin: bool | None = MaaField(default=None, label="汇报企鹅数据", group="数据上报")
    penguin_id: str | None = MaaField(default=None, label="企鹅数据 ID", group="数据上报")
    server: SERVERS | None = MaaField(
        default=None, label="服务器", group="账号",
        description="影响掉落识别与上传，未指定时套用全局默认值（CN）",
    )
    client_type: CLIENT_TYPES | None = MaaField(
        default=None, label="客户端版本", group="账号",
        description="用于游戏崩溃后重启并连回继续刷；留空则不启用该功能",
    )
    dr_grandet: bool | None = MaaField(
        default=None, label="节省理智碎石模式", group="资源消耗",
        description="在碎石确认界面等待，直到当前 1 点理智恢复完成后再碎石",
        validation_alias=AliasChoices("DrGrandet", "dr_grandet"),
        serialization_alias="DrGrandet",
    )
```

`dr_grandet` 的别名处理是必须的：MaaCore 认的键名是 `DrGrandet`，但 Python 侧不应出现大驼峰字段。`validation_alias` 让两种写法都能提交，`serialization_alias` 保证下发时用 MAA 原名。

`InfrastInput` 展示跨字段规则：

```python
class InfrastInput(TaskInputBase):
    name: Literal["Infrast"] = "Infrast"

    mode: Literal[0, 10000, 20000] | None = MaaField(
        default=None, label="换班工作模式", widget="select",
        enum_labels={"0": "默认换班（单设施最优解）",
                     "10000": "自定义换班（读取用户配置）",
                     "20000": "一键轮换"},
    )
    facility: list[Literal["Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"]] | None = (
        MaaField(default=None, label="要换班的设施（有序）",
                 description="不支持运行中设置")
    )
    drones: Literal["_NotUse", "Money", "SyntheticJade", "CombatRecord",
                    "PureGold", "OriginStone", "Chip"] | None = MaaField(
        default=None, label="无人机用途", description="mode=10000 时该字段被忽略",
    )
    threshold: float | None = MaaField(default=None, label="工作心情阈值", ge=0.0, le=1.0)
    replenish: bool | None = MaaField(default=None, label="源石碎片自动补货")
    dorm_notstationed_enabled: bool | None = MaaField(default=None, label='启用宿舍"未进驻"选项')
    dorm_trust_enabled: bool | None = MaaField(default=None, label="剩余位置填入信赖未满干员")
    filename: str | None = MaaField(
        default=None, label="自定义配置路径", group="自定义换班",
        description="仅 mode=10000 时生效且必填。不支持运行中设置",
    )
    plan_index: int | None = MaaField(
        default=None, label="方案序号", group="自定义换班", ge=0,
        description="仅 mode=10000 时生效且必填。不支持运行中设置",
    )

    @model_validator(mode="after")
    def _check_custom_mode(self):
        if self.mode == 10000 and (self.filename is None or self.plan_index is None):
            raise ValueError("自定义换班模式（mode=10000）必须同时提供 filename 与 plan_index")
        return self
```

### 7.4 联合类型与提交请求体

```python
TaskInput = Annotated[
    StartUpInput | CloseDownInput | FightInput | RecruitInput | InfrastInput
    | MallInput | AwardInput | RoguelikeInput | ReclamationInput,
    Field(discriminator="name"),
]


class PipelineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskInput] = Field(min_length=1, max_length=32)
    title: str | None = Field(default=None, max_length=64)
    priority: int | None = Field(default=None, ge=0, le=2)
    notify_on_finish: bool = True
```

`source` **不在请求体里**，由服务端按调用入口判定：REST 路由 → `manual`，MCP 与内置 agent → `agent`，`ScheduleService` → `scheduled`。让客户端自报来源等于让 agent 可以伪装成手动操作抢到最高优先级，优先级体系就失效了。

`priority` 允许显式指定，但只能调低不能调高到超出来源的默认值——agent 不能把自己提到 0。越权时不报错，静默钳制到来源默认值并在审计里记录，因为这是一个 agent 可能因不理解规则而反复触发的边界。

### 7.5 需要在模型里显式编码的约束

`task.py` 的文档字符串是参数的权威来源，但它记录的取值范围、枚举域与跨字段规则目前只存在于注释里。下表是必须落进 Pydantic 声明的部分，其余字段只需正确的类型与可选性。

| 类型 | 需编码的约束 |
|---|---|
| `StartUp` | `client_type` 枚举（6 值） |
| `CloseDown` | `client_type` 枚举；文档标注"必选，填空则不执行"，实际由全局默认值注入保证非空 |
| `Fight` | `series` 取 `[-1, 6]`；`medicine`/`expiring_medicine`/`stone`/`times` 非负；`server` 枚举 4 值；`client_type` 枚举；`drops` 为 `dict[str, int]`；`DrGrandet` 别名 |
| `Recruit` | `select`/`confirm` 为 `list[int]`，元素取 `[1, 6]`；`extra_tags_mode` 取 `{0, 1, 2}`；`recruitment_time` 为 `dict[str, int]`（**键是字符串**，JSON 对象键不能是整数，现有 `dict[int, int]` 的声明在 JSON 层面不成立）；`expedite_times` 仅 `expedite=true` 时有意义；`set_time` 仅 `times=0` 时生效 |
| `Infrast` | `mode` 取 `{0, 10000, 20000}`；`facility` 元素为 7 值枚举；`drones` 为 7 值枚举；`threshold` 取 `[0, 1]`；`mode=10000` 时 `filename` 与 `plan_index` 必填 |
| `Mall` | 无数值范围约束；`buy_first`/`blacklist` 为商品中文名字符串列表，不做枚举（商店商品会随活动变化，枚举会过期） |
| `Award` | 全为布尔，无范围约束 |
| `Roguelike` | `theme` 枚举 `{Phantom, Mizuki, Sami, Sarkaz}`；`mode` 取 `{0, 1, 4, 5}`，**`2` 已弃用**，传入时 400 `TASK_PARAM_DEPRECATED`，`3` 为开发中同样拒绝；`difficulty` 非负且仅非 `Phantom` 主题生效；`mode=5` 要求 `theme="Sami"`；`start_foldartal_list`/`first_floor_foldartal`/`use_foldartal`/`check_collapsal_paradigms` 仅 `Sami` 生效；`only_start_with_elite_two` 要求 `mode=4` 且 `start_with_elite_two=true`；`use_nonfriend_support` 要求 `use_support=true`；`refresh_trader_with_dice` 仅 `Mizuki` 生效 |
| `Reclamation` | `theme` 枚举 `{Fire, Tales}`；`mode` 取 `{0, 1}`；`increment_mode` 取 `{0, 1}`；`num_craft_batches` ≥ 1 |

主题相关的跨字段规则一律实现为 `model_validator` 并抛 `TASK_PARAM_INVALID`，而不是静默忽略。原因是"设了但不生效"是用户最常见的困惑来源——`Roguelike` 在 `Phantom` 主题下设了 `difficulty` 却毫无反应，没有报错的话根本无从察觉。

### 7.6 对现有实现的五处修正

这些不是设计偏好，是现有代码里的实际缺陷，新模型必须修正。

**`ReclamationTask` 的 params 字典用变量当键。** 现有代码写的是 `params = {enable: enable, theme: theme, ...}`，键是变量的**值**而不是字符串字面量。实际效果是：全部参数为 `None` 时，字典退化成 `{None: None}` 这一个条目；有值时键变成参数值本身（`{"Fire": "Fire"}`）。这个任务类型目前实际上完全不可用。新模型通过 `model_dump()` 生成参数字典，结构性地消除了这类错误。

**`InfrastTask` 的 `failename` 拼写错误。** 参数名与 params 键都是 `failename`，而文档字符串写的是 `filename`，MaaCore 认的也是 `filename`。自定义换班模式因此从未生效过。新模型统一用 `filename`，不为拼写错误保留兼容别名。

**`AwardTask` 的默认值与文档冲突。** `TaskRequest` 里 `mail`/`recruit`/`orundum`/`mining`/`specialaccess` 的默认值都是 `True`，而 `task.py` 文档字符串明确写"默认为 False"（只有 `award` 默认 True）。文档字符串是权威来源，新模型这些字段一律 `default=None`，不下发则由内核用自己的默认值，服务端不预设。

**`InfrastTask.threshold` 的默认值不一致。** 函数签名写 `threshold: float = 0.5`，文档字符串写"默认 0.3"。**已确认以 0.3 为准**，签名里的 0.5 是错的。但新模型仍不设服务端默认值（`default=None`），把默认值的决定权交还内核——服务端预设默认值会在内核调整默认行为时产生偏差。0.3 这个值只出现在 OpenAPI 的 `description` 与前端占位提示里，作为"内核当前默认值"的说明，不作为请求体的实际默认。

**`times` 字段在 `TaskRequest` 中重复声明。** 第 12 行与第 24 行各有一个 `times: Optional[int] = None`，Python 类定义里后者覆盖前者。按类型分离后这个问题自然消失：`Fight` 与 `Recruit` 各有独立的 `times`，语义也不同（战斗次数 vs 招募次数），共用一个字段本身就是错的。

## 8. `GET /api/tasks/types` 的 schema 导出

这个端点有两类消费者，需求恰好一致：前端用它动态生成任务配置表单，agent 用它了解某个任务类型有哪些参数可用。共用一份 schema 保证两边永远不会看到不同的参数集。

### 8.1 导出实现

```python
# maa_api/api/routers/tasks.py
from pydantic import TypeAdapter

_ADAPTERS = {m.model_fields["name"].default: m for m in get_args(get_args(TaskInput)[0])}


def export_type_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema(ref_template="#/$defs/{model}")
    return {
        "name": model.model_fields["name"].default,
        "label": TASK_LABELS[model.model_fields["name"].default],   # "刷理智"
        "description": (model.__doc__ or "").strip(),
        "schema": schema,               # 标准 JSON Schema，含 x-* 扩展
        "groups": _collect_groups(schema),
        "runtime_immutable": RUNTIME_IMMUTABLE[...],                # ["stage"]
    }
```

响应结构：

```json
{
  "items": [
    {
      "name": "Fight",
      "label": "刷理智",
      "description": "刷指定关卡，支持理智药与碎石",
      "runtime_immutable": ["stage"],
      "groups": ["基础", "资源消耗", "账号", "数据上报", "高级"],
      "schema": {
        "type": "object",
        "properties": {
          "series": {
            "anyOf": [{ "type": "integer", "maximum": 6, "minimum": -1 }, { "type": "null" }],
            "default": null,
            "x-label": "连战次数",
            "x-group": "基础",
            "x-widget": "select",
            "x-enum-labels": { "-1": "禁用切换", "0": "自动选择最大可用次数", "1": "1 次" }
          }
        },
        "additionalProperties": false
      }
    }
  ],
  "total": 9,
  "page": 1,
  "size": 9
}
```

### 8.2 中文标签与取值范围的承载方式

标准 JSON Schema 没有"显示标签"的概念，`title` 通常被 Pydantic 用作字段名的美化形式。因此中文元信息走 `x-` 前缀的自定义关键字（JSON Schema 规范允许未知关键字，校验器会忽略它们，所以 schema 仍然是合法的、可被任何标准校验器使用的）：

| 关键字 | 作用 | 前端用法 |
|---|---|---|
| `x-label` | 中文标签 | 表单项的 label |
| `x-group` | 分组名 | 折叠面板分区，避免 23 个参数平铺 |
| `x-widget` | 控件提示（`select` / `stepper` / `switch` / `tags` / `textarea`） | 选择渲染组件 |
| `x-enum-labels` | 枚举值到中文的映射 | 下拉选项的显示文案 |

取值范围直接用 JSON Schema 原生关键字承载：`minimum` / `maximum`（来自 `ge` / `le`）、`enum`（来自 `Literal`）、`minItems` / `maxItems`。这样前端可以直接把它喂给 `react-hook-form` 之类的库做客户端校验，不必解析自定义字段。

`description` 取 Pydantic `Field` 的 `description`，内容直接摘自 `task.py` 的文档字符串——那些说明（"剿灭作战必须输入 Annihilation"、"仅在 use_support 为 True 时有效"）是本项目最有价值的领域知识，必须出现在表单的提示文案和 agent 的工具描述里，不能停留在源码注释。

**跨字段规则无法用 JSON Schema 干净地表达**，因此额外给出 `x-depends-on`：

```json
"expedite_times": {
  "type": "integer",
  "x-label": "加急次数",
  "x-depends-on": { "expedite": true }
}
```

前端据此在 `expedite` 未开启时禁用该项；agent 从工具描述里读到同样的约束。服务端的 `model_validator` 仍是最终防线——客户端校验只是体验优化，不是安全边界。

## 9. 全局渠道默认值的注入位置

按决策，渠道默认 **Bilibili（B 服）**，任务未显式指定 `client_type` 时自动套用，允许单任务覆盖。`Fight` 与 `Recruit` 的 `server` 字段同理（默认 `CN`）。

注入发生在**校验之后、落库之前**的唯一一个函数里：

```python
# maa_api/domain/task.py
def normalize(task: TaskInput, defaults: ChannelDefaults) -> NormalizedTask:
    raw = task.model_dump(exclude={"name"}, exclude_unset=True, by_alias=True)
    params = task.to_core_params()

    # 只在"用户完全没提这个字段"时注入，显式传 null 视为刻意留空
    if "client_type" in type(task).model_fields and "client_type" not in task.model_fields_set:
        params["client_type"] = defaults.client_type          # 默认 "Bilibili"
    if "server" in type(task).model_fields and "server" not in task.model_fields_set:
        params["server"] = defaults.server                   # 默认 "CN"

    return NormalizedTask(
        type_name=task.name,
        task_name=TASK_LABELS[task.name],
        params=params,
        raw_params=raw,
    )
```

调用点在 `services/queue_service.submit()`，三条提交路径（REST、定时任务、agent/MCP）全部经过它。

**为什么不在 Pydantic 的 validator 里做。** 默认值来自 `setting` 表，validator 里取不到异步数据库会话；更根本的是，模型必须能如实表达"用户没设这个字段"，一旦 validator 填了值，`model_fields_set` 就失去了区分能力，`raw_params` 也就记不准原始提交了。

**为什么不在 `core/worker.py` 里做。** 子进程不应该知道任何业务默认值，它的职责只是把参数转成 JSON 投给 `AsstAppendTask`。更要紧的是，落库的 `task.params` 必须与内核实际收到的完全一致，如果注入发生在子进程里，数据库里存的就是一份不完整的记录，排查"为什么跑的是官服"时会查无实据。

**为什么不在路由层做。** 路由层做就得做三遍，定时任务与 MCP 迟早会漏一处。

`model_fields_set` 是这段逻辑的关键，它区分了三种情况：字段未出现在请求 JSON 里（注入默认值）、显式传 `null`（不注入，不下发该参数，由内核决定）、显式传具体值（原样使用）。第二种情况对 `Fight.client_type` 有实际意义——文档明确说该字段留空则关闭"崩溃后重启续刷"功能，用户可能就是想关掉它。

`ChannelDefaults` 从 `SettingService` 读取（`channel.client_type`、`channel.server`），带进程内缓存并在设置变更时失效。定时任务在**触发时**读取当前默认值而不是创建时快照，这样改了渠道之后所有定时任务自动跟随。

## 10. 幂等性与并发

### 10.1 需要幂等保护的端点

| 端点 | 保护方式 |
|---|---|
| `POST /api/pipelines` | 可选 `Idempotency-Key` 头，落 `pipeline.idempotency_key` 唯一列 |
| `POST /api/pipelines/{id}/retry` | 同上 |
| `POST /api/agent/tools/{name}/invoke` | 可选 `Idempotency-Key`，命中则返回首次的审计结果 |
| `POST /api/updates/{target}` | 不用 header，靠 `update_record` 的部分唯一索引（同 target 只允许一条 `running`） |
| `POST /api/schedules/{id}/run` | 该 schedule 已有 `PENDING`/`RUNNING` 实例时返回 409（`skip_if_running` 为真时） |
| `POST /api/confirmations/{id}` | 状态机保证，重复处理返回 `409 CONFIRMATION_ALREADY_RESOLVED` |
| `POST /api/core/restart` | 已处于 `RESTARTING` 时返回 409 |
| `POST /api/resources/reload` | `checksum` 未变化时返回 202 但不实际重载 |

`Idempotency-Key` 的处理规则：客户端生成（推荐 UUID），服务端在 24 小时窗口内有效。命中已有记录时，**比对请求体哈希**——一致则返回原次的 202 与原 `pipeline_id`，不一致则返回 `409 IDEMPOTENCY_KEY_CONFLICT`。后一种情况通常意味着客户端的 key 生成有 bug，静默接受会掩盖问题。

为什么流水线提交需要这道保护：移动端是首要目标设备，弱网下用户点"开始"没有立即反馈会重复点击；PWA 的离线重放也可能重发同一请求。没有幂等保护的后果是队列里出现两条一模一样的日常任务，第二条跑起来会在已经做完的关卡上浪费理智。

### 10.2 天然幂等的端点

所有 `GET`、`PUT /api/settings`（全量覆盖语义）、`PUT /api/schedules/{id}`、`PUT /api/notifications/channels/{id}`、全部 `DELETE`（重复删除返回 204 而非 404——目标状态已达成，报错没有意义）。

`PATCH /api/queue/{pipeline_id}` 也是幂等的，但受 `QUEUE_ITEM_NOT_PENDING` 约束：条目一旦开跑，优先级就不再可改。

### 10.3 并发写入的收敛

**唯一写者原则。** 流水线与任务的状态只由 `PipelineRunner` 写入，REST 端点只做两件事：读状态、投递意图。`DELETE /api/pipelines/{id}` 不直接把状态改成 `CANCELLED`，而是置一个取消标记并向内核投 `STOP`，由 `PipelineRunner` 观察到后统一收尾。这样避免了"REST 刚写完 CANCELLED，Runner 又写回 COMPLETED"的竞态。

**状态流转靠条件更新兜底。** 所有终态流转写成 `UPDATE ... WHERE id = ? AND status = ?`，用影响行数判断是否被接受，返回 0 行则转成 409。细节见 [04-数据模型与持久化](./04-数据模型与持久化.md) 的仓储层章节。

**原子操作与流水线的冲突。** 流水线运行中调用 `POST /api/device/*` 或 `/api/core/back_to_home` 返回 `409 PIPELINE_ALREADY_RUNNING`；带 `force=true` 则执行，但 `agent_audit.forced` 记为真。agent 的"卡死救援"场景应遵循的序列是：先 `DELETE /api/pipelines/{id}` 停止流水线，再做原子操作——这条序列写进工具描述里，而不是靠 agent 自己推断。

**WebSocket 与 REST 的一致性。** 两者读的是同一份数据库状态，WS 事件只是变更通知，不携带权威状态。前端收到 `pipeline_status_changed` 后若需完整数据应重新拉取，避免乱序的事件覆盖了较新的状态。

## 11. OpenAPI 文档的组织

### 11.1 tag 分组

`tags_metadata` 与路由分组一一对应，顺序即 `/docs` 里的展示顺序，按使用频率而非字母排：

```python
TAGS = [
    {"name": "system", "description": "健康检查、服务信息、日志查询"},
    {"name": "pipelines", "description": "流水线提交、查询与取消"},
    {"name": "queue", "description": "队列快照与优先级调整"},
    {"name": "tasks", "description": "任务类型与参数 schema"},
    {"name": "device", "description": "设备状态、重连与原子操作"},
    {"name": "core", "description": "MaaCore 子进程状态与原生能力"},
    {"name": "screenshots", "description": "截图归档与读取"},
    {"name": "updates", "description": "内核 / 资源 / 游戏三种热更新"},
    {"name": "schedules", "description": "定时任务"},
    {"name": "settings", "description": "可视化配置"},
    {"name": "notifications", "description": "多通道通知"},
    {"name": "resources", "description": "Copilot 作业、基建方案、自定义 task"},
    {"name": "agent", "description": "工具清单、会话与审计"},
    {"name": "confirmations", "description": "高风险操作的人工确认"},
    {"name": "ws", "description": "WebSocket 实时通道（仅文档说明，不可在此调试）"},
]
```

`ws` 与 `mcp` 无法在 Swagger UI 中调试，但仍在文档里保留占位条目说明协议与鉴权方式，否则读文档的人会以为服务没有实时通道。

### 11.2 summary 与 description 的中文规范

`summary` 用**动宾短语**，不超过 12 字，不带标点：`提交流水线`、`取消流水线`、`重连设备`、`获取任务类型 schema`。它会出现在 Swagger UI 的折叠标题与生成的客户端方法注释里，长了就被截断。

`description` 至少覆盖三件事：这个端点做什么、有什么副作用或前置条件、失败时可能返回哪些错误码。副作用尤其重要，读者不该需要读源码才知道 `POST /api/updates/core` 会重启内核子进程。写法示例：

```python
@router.post(
    "/api/pipelines",
    status_code=202,
    summary="提交流水线",
    description=(
        "把一组 MAA 任务作为一条流水线提交到队列。\n\n"
        "- 提交即入队，**不代表已开始执行**。出队顺序为 `priority ASC, created_at ASC`\n"
        "- `source` 由服务端按调用入口判定，请求体中指定无效\n"
        "- 未显式指定 `client_type` 的任务会套用全局渠道默认值（默认 Bilibili）\n"
        "- 可带 `Idempotency-Key` 头做重复提交保护，窗口 24 小时\n"
    ),
    responses=error_responses(
        "PIPELINE_EMPTY", "UNKNOWN_TASK_TYPE", "TASK_PARAM_INVALID",
        "QUEUE_FULL", "QUEUE_PAUSED", "CORE_NOT_READY",
    ),
)
```

`error_responses()` 是一个从错误码枚举生成 `responses` 字典的辅助函数，按状态码归并并填入示例错误体。手写每个端点的 `responses` 必然会与错误码表脱节；从同一份枚举生成则改一处就全同步。

### 11.3 示例值

每个请求模型都通过 `model_config["json_schema_extra"]["examples"]` 给至少一个**可直接执行**的示例。这一点对 agent 特别关键：从 `/api/tasks/types` 拿到 schema 之后，一个完整的示例比二十条字段描述更能让模型正确构造第一次调用。

```python
class PipelineCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "title": "日常",
                    "tasks": [
                        {"name": "StartUp", "start_game_enabled": True},
                        {"name": "Infrast", "mode": 0,
                         "facility": ["Mfg", "Trade", "Control", "Power", "Reception", "Office", "Dorm"]},
                        {"name": "Fight", "stage": "1-7", "series": 0},
                        {"name": "Recruit", "select": [4, 5], "confirm": [3, 4, 5], "times": 4},
                        {"name": "Mall", "shopping": True, "blacklist": ["加急许可", "家具零件"]},
                        {"name": "Award", "award": True},
                        {"name": "CloseDown"},
                    ],
                }
            ]
        },
    )
```

### 11.4 operation_id 规范化

FastAPI 默认的 `operation_id` 形如 `post_tasks_api_maa_pipeline_post`，生成出的客户端方法名难以入眼。统一改写成 `{tag}_{函数名}`：

```python
def custom_operation_id(route: APIRoute) -> str:
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}_{route.name}"


app = FastAPI(generate_unique_id_function=custom_operation_id, redirect_slashes=False)
```

生成的 TypeScript 客户端方法就是 `pipelines_create`、`device_reconnect` 这样的形状。[10-开放API与调试台](./10-开放API与调试台.md) 的类型生成依赖这条约定。

## 12. 废弃端点与迁移

旧端点全部保留路由存根，返回 `410 ENDPOINT_REMOVED`，`details.replaced_by` 指向新端点。这比直接删掉（返回 404）强得多：404 让调用方以为自己拼错了路径，410 则明确告知"这个接口没了，去这里"。存根在下一个大版本移除。

| 旧端点 | 新端点 |
|---|---|
| `POST /api/maa/pipeline` | `POST /api/pipelines` |
| `GET /api/maa/pipeline` | `GET /api/pipelines/current` |
| `DELETE /api/maa/pipeline` | `DELETE /api/pipelines/{id}` |
| `GET /api/maa/daily` | `GET /api/schedules` |
| `PUT /api/maa/daily` | `PUT /api/schedules/{id}` |
| `POST /api/maa/daily/execute` | `POST /api/schedules/{id}/run` |
| `GET /api/adb/screenshot` | `GET /api/device/screenshot` |
| `GET /daily` | 前端路由 `/#/schedules`，由 SPA catch-all 处理 |

三处语义变化需要调用方注意。`GET /api/maa/pipeline` 原本返回内存里唯一的那条流水线（含全部日志与 base64 截图），新的 `GET /api/pipelines/current` 只返回流水线与任务状态，日志与截图各有独立端点——把三类数据塞进一个响应会让这个高频轮询接口传输几百 KB。

`DELETE /api/maa/pipeline` 不带 id，因为只可能有一条；新接口必须带 id，因为队列里可能有多条。想取消"当前那条"需要先读 `GET /api/pipelines/current` 拿 id。

旧的 daily 接口读写的是 `resource/daily_task.json` 整个文件，新接口是标准 CRUD 资源。一次性迁移由数据迁移脚本完成，见 [04-数据模型与持久化](./04-数据模型与持久化.md) 的迁移章节。
