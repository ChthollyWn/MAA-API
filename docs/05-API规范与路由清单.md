> MAA-API 对外契约的唯一规范：HTTP 状态码用法、统一错误体与错误码枚举、四渠道鉴权、全量路由清单、任务提交的请求体设计与幂等约定。上游决策见 [README 决策速查表](./README.md#决策速查表)，接入层在整体架构中的位置见 [02-系统架构设计](./02-系统架构设计.md)。

# API 规范与路由清单

## 1. 总则

重构后的 API 遵循四条硬规则，它们共同废弃了现有 `maa_api/model/request/response.py` 那套 `{code: 10200}` 体系。

**语义由 HTTP 状态码承担。** 现有实现无论成败都返回 HTTP 200，真实结果藏在响应体的 `code` 字段里。这让所有中间层（浏览器、`fetch` 的 `response.ok`、反向代理的日志、OpenAPI 生成的客户端、调用方的重试策略）全部失效，每一处都必须先解析 body 才知道发生了什么。新规范下状态码是第一现场。

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

**路径前缀统一 `/api`，Agent 工具调用通过 `/api/agent` 路由提供。** 前端 SPA 占用其余全部路径。

## 2. HTTP 状态码使用规范

| 状态码 | 使用场景 |
|---|---|
| `200 OK` | 读取成功；同步执行完成的写操作（原子点击、发测试通知、批准确认） |
| `201 Created` | 创建了一个可寻址的持久实体（定时任务、通知通道、agent 会话、资源文件、API 收藏），必须带 `Location` 头 |
| `202 Accepted` | 请求已受理但未完成：提交流水线、取消流水线、重启内核、触发更新、重连设备、等待人工确认；发送 Agent 消息规划归 M13 |
| `204 No Content` | 删除成功、批量清理成功，无响应体 |
| `400 Bad Request` | 请求在语义上不成立但不是字段类型问题：JSON 解析失败、未知任务类型、未知设置项、cron 表达式非法、使用了已弃用的参数值 |
| `401 Unauthorized` | token 缺失或不匹配。响应带 `WWW-Authenticate: Bearer` |
| `403 Forbidden` | 身份有效但动作被拒：调用方无权使用该工具、修改只读配置项、人工确认被用户拒绝 |
| `404 Not Found` | 资源不存在，含已被保留策略清理掉的历史记录 |
| `409 Conflict` | 与当前状态冲突：流水线正在运行、更新正在进行、待确认项已处理过、API 收藏名称重复、修改不支持运行时变更的参数 |
| `410 Gone` | 确定曾存在但已永久移除：旧版端点、文件已被清理但记录仍在的截图 |
| `422 Unprocessable Entity` | 结构正确但字段值不合法：Pydantic 校验失败、参数越界、跨字段规则不满足 |
| `429 Too Many Requests` | 队列已满、上游 LLM 限流透传、鉴权失败频率限流。带 `Retry-After` 头 |
| `500 Internal Server Error` | 服务端自身缺陷或本地环境故障：未捕获异常、数据库错误、解压失败、`adb` 可执行文件不存在 |
| `502 Bad Gateway` | 依赖的外部/下游组件明确失败：MaaCore 子进程命令失败、ADB 命令失败、下载源不可达、LLM 端点报错 |
| `504 Gateway Timeout` | 下游未在时限内应答：MaaCore 子进程命令超时、LLM 上游超时。操作可能已执行一半，客户端应查状态而非重试 |
| `503 Service Unavailable` | 依赖暂时不可用、稍后可重试：内核未就绪/重启中/已崩溃、设备未连接、LLM 未配置。带 `Retry-After` 头 |

### 2.1 关于 202

202 在本 API 里出现得比多数项目频繁，因为核心操作天然是异步的：流水线要排队，内核重启要几秒，更新要下载几十兆。202 的响应体是**受理凭据**，通常带上可供轮询的资源 id 和 `Location`：

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

**需要人工确认的 Agent REST 操作同样返回 202。** 确认请求已经创建并正在等人点按钮，这是标准的"已受理，尚未完成"。调用方按 `confirmation_id` 轮询 `/api/confirmations/{id}`，终态详情带执行结果、错误与 `audit_id`。Agent 响应不带 `Location` 头，示例：

```http
HTTP/1.1 202 Accepted
Content-Type: application/json

{
  "code": "CONFIRMATION_REQUIRED",
  "status": "awaiting_confirmation",
  "confirmation_id": "2b7d...",
  "audit_id": 123,
  "expires_at": "2026-09-16T06:32:00Z"
}
```

REST `mode=sync` 会等待确认和执行完成；`mode=async` 立即返回 202 并由确认详情 API 轮询。内置 Agent 调用等待完整确认期限；REST `mode=sync` 阻塞等待，`mode=async` 返回 202 并由确认详情 API 轮询。重启后不重放仍为 pending 的副作用操作。

### 2.2 400 与 422 的分界

界线是"**Pydantic 能不能表达**"。能被字段类型、`ge`/`le`、`Literal`、`model_validator` 拦住的，一律 422，错误码 `VALIDATION_ERROR` 或 `TASK_PARAM_INVALID`，`details` 里带 FastAPI 原生的字段级错误列表。拦不住的语义问题用 400：请求体不是合法 JSON、discriminator 取值不在已知任务类型内、设置项 key 不存在、cron 字符串 APScheduler 解析不了。

这条线的实用价值在于前端：422 一定能定位到具体字段并高亮表单项，400 只能弹一条整体提示。

### 2.3 409 与 422 的分界

422 是"这个请求本身不对"，409 是"这个请求现在不行"。同一个请求体，五分钟后重发可能就成功了的，用 409。因此：任务参数越界是 422，流水线正在运行是 409；cron 非法是 400，定时任务名重复是 409。

### 2.4 502 / 503 / 504 的分界

三者都表示"不是客户端的错"，区别在于下游处于什么状态，以及客户端该怎么处置。

**503 Service Unavailable：依赖暂时不可用，等一等再来。** 内核还在加载资源、设备没连上、LLM 没配置。这类响应带 `Retry-After`，前端会自动重试或显示"等待中"。

**502 Bad Gateway：依赖被调用了，但它失败了。** `AsstAppendTask` 返回 0、`adb shell` 非零退出、下载源返回 500、LLM 端点报 `invalid_api_key`。这类不该自动重试，要把失败原因显示给用户。

**504 Gateway Timeout：依赖被调用了，但没在时限内应答。** MaaCore 子进程命令超时（`CORE_COMMAND_TIMEOUT`）与 LLM 上游超时（`LLM_TIMEOUT`）都归这一类。

区分 504 与 502 的价值在于两者的**后续处置不同**。502 意味着下游给了明确答复"这事我办不成"，原因通常就在响应里，重试大概率还是同样的结果。504 意味着下游没答复，那么操作**可能已经执行了一半**，`CORE_COMMAND_TIMEOUT` 尤其如此：命令已经投递进子进程队列，超时只说明没在预算内收到 `CMD_RESULT`，任务可能正在跑。因此 504 后客户端要做的是查状态（`GET /api/pipelines/current`）而不是重试，盲目重试可能提交两遍。

这个区分同时让监控告警能分开统计"内核执行失败"与"内核卡住"，这两类的根因与处理方式完全不同：前者通常是参数或资源问题，后者往往意味着该重启内核了。

规则：**"下游明确失败"用 502，"下游未在时限内响应"用 504。** 判断依据是有没有收到答复，不是失败得有多严重。据此 `UPDATE_DOWNLOAD_FAILED`（下载中断或返回非 2xx）仍是 502：下载中断是传输层给了明确结果，不是没答复。

### 2.5 框架自动产生的状态码

`405 Method Not Allowed`、`307 Temporary Redirect`（尾斜杠重定向）由 Starlette 产生，不进错误码表。为避免 307 干扰前端与脚本调用方，所有路由**不带尾斜杠**，并设 `redirect_slashes=False`。

## 3. 响应体约定

### 3.1 成功响应不包封套

**结论：2xx 直接返回资源体本身，不包 `{code, message, data}` 这一层。**

理由有三。既然状态码已经承担了成败语义，`code` 字段就是纯冗余，`message` 在成功时永远是 `"success"` 这种无信息量的占位，`data` 则让每一处消费都要多写一次 `.data`。

更实际的理由是生态。FastAPI 的 `response_model` 直接映射资源模型，封套会迫使每个模型都套一层泛型 `Response[T]`，而 OpenAPI 的泛型展开在客户端代码生成器里支持得很差：[10-开放API与调试台](./10-开放API与调试台.md) 要从 OpenAPI 生成 TypeScript 类型，裸模型生成出来就是 `Pipeline`，封套模型生成出来是 `ResponseWrapper_Pipeline_` 且 `data` 是可空的，每处都要断言。工具响应直接使用资源体，多一层封套会增加客户端解析与模型上下文开销。

第三个理由是错误体已经**有**封套（`{"error": {...}}`）。成功裸返回、失败带 `error` 键，两者形状不同反而是好事：`if ("error" in body)` 之外还有状态码可判，不存在歧义。

单值响应也不例外。`GET /api/core/version` 返回 `{"version": "v6.17.3"}` 而不是裸字符串：JSON 顶层给对象，留出后续加字段的空间。

### 3.2 分页

所有列表端点统一：

```json
{ "items": [...], "total": 137, "page": 1, "size": 20 }
```

查询参数固定为 `page`（从 1 开始）与 `size`（默认 20，上限 200）。`page` 或 `size` 越界返回 `400 INVALID_PAGINATION`，不做静默钳制：静默钳制会让前端误以为自己拿到了第 999 页。

日志端点额外支持 `after_id` / `before_id` 游标（配合 `log_entry.id` 的单调性），用于 WebSocket 断线后补齐缺口。游标模式与 `page` 互斥，同时传两者返回 `400 INVALID_PAGINATION`。日志端点还有两处例外：`size` 上限放宽到 1000（导出预览与断线补发会一次性拉取大段记录），且默认值为 100 而非 20。查询语义与 `pipeline_id` 关联方式详见 [06-实时日志与WebSocket §9](./06-实时日志与WebSocket.md)，其中也说明了为什么日志要走游标而不用 `page`。

### 3.3 空值与缺省

响应体中语义上"尚未发生"的字段返回 `null` 而不是省略键（`started_at: null`），这样 TypeScript 的类型是 `string | null`，前端少一层判断。`GET /api/pipelines/current` 在无运行中流水线时返回 `200` + `{"pipeline": null}`，不返回 404："当前没有在跑"是正常状态，不是资源缺失。

## 4. 错误码表

错误码定义在 `maa_api/domain/errors.py` 的 `ErrorCode(StrEnum)` 中，与领域异常一一对应；`maa_api/api/errors.py` 注册异常处理器，把领域异常翻译成上表的状态码与统一错误体。共 112 条，其中 `UPDATE_INTERRUPTED` 只落库不返回，不占 HTTP 表达；`ENDPOINT_REMOVED` 有 410 绑定，但实现不注册旧端点存根，当前没有任何路径会抛出它（见 [§12](#12-废弃端点与迁移)）。

```python
# maa_api/domain/errors.py
class AppError(Exception):
    code: ErrorCode
    http_status: int
    message: str
    details: dict[str, Any] | None
```

每个错误码在代码里绑定固定的 HTTP 状态码，路由层不再自行决定状态码：这保证了同一个错误在不同端点上表现一致。

### 4.1 鉴权

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `UNAUTHORIZED` | 401 | token 缺失或与 `access_token` 不匹配。四个渠道都没取到有效 token 时返回 |
| `FORBIDDEN` | 403 | 身份有效但动作被策略拒绝：非同源请求试图仅凭 cookie 执行写操作 |
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
| `ENDPOINT_REMOVED` | 410 | 重构前旧端点的错误码；实现不注册旧端点存根，当前没有路径会抛出它（见 §12） |
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
| `UNKNOWN_TASK_TYPE` | 400 | `name` 字段不在 9 种 MaaCore 内置任务或 `Copilot` 类型内，discriminated union 匹配失败 |
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
| `CONFIRMATION_REQUIRED` | 202 | 操作命中消耗类或破坏类策略，已创建确认请求。**唯一出现在 2xx 响应中的码**，位于 202 的正常响应体而非错误体；操作等待确认时作为 202 正常响应返回 |
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
| `AGENT_TURN_INTERRUPTED` | 409 | 本轮被用户中断，该工具调用被取消、未完成（M22）。只落在审计的 `error_code` 上，中断端点本身返回 200 + `interrupted` |
| `TOOL_NOT_FOUND` | 404 | `ToolRegistry` 中没有该工具名 |
| `TOOL_ARGS_INVALID` | 422 | 工具参数不满足其 JSON Schema |
| `TOOL_EXECUTION_FAILED` | 500 | 工具实现内部抛出未预期异常。被工具调用的下游错误（内核、设备）按其本身的码原样上抛，不包成这个码 |
| `LLM_NOT_CONFIGURED` | 503 | `base_url` / `api_key` / `model` 三项未配齐 |
| `LLM_REQUEST_FAILED` | 502 | 上游返回非 2xx。`details.upstream_status` 保留；`details.upstream_code` 仅包含已知白名单码 |
| `LLM_TIMEOUT` | 504 | 上游在超时内未返回 |
| `LLM_RATE_LIMITED` | 429 | 上游 429 透传，`Retry-After` 沿用上游值 |
| `LLM_ENCRYPTION_KEY_INVALID` | 503 | 缺少或配置错误的 `MAA_LLM_ENCRYPTION_KEY`，无法解密数据库里的 LLM API 密钥；设置正确的主密钥，或在设置页重新录入 API 密钥 |
| `LLM_CONTEXT_OVERFLOW` | 400 | 会话历史或知识库内容超出模型自身的上下文长度（本地不按字符数预裁剪），需开启新会话或缩短内容 |
| `KNOWLEDGE_NOT_FOUND` | 404 | 知识条目 id 不存在，或（`read_knowledge`）已被停用 |
| `KNOWLEDGE_BUILTIN_READONLY` | 409 | 删除未修改过的内置条目，或修改内置条目的 `kind` |
| `QUESTION_NOT_FOUND` | 404 | 提问 id 不存在 |
| `QUESTION_ALREADY_ANSWERED` | 409 | 重复回答已 answered / skipped 的提问 |
| `QUESTION_EXPIRED` | 409 | 回答已过期的提问 |
| `QUESTION_SELECTION_INVALID` | 400 | 空选择、选项不在 options.label 里、或单选提交多项 |

tool-calling 循环的非错误终态**不是错误**：会话正常结束并返回 200，最后一条 `agent_message.finish_reason` 记为 `stuck_loop`（连续三轮发出完全相同的工具调用，判定为原地打转）或 `max_steps`（调用方显式设了步数上限；默认不设上限），M22 起还有 `interrupted`（用户主动中断了本轮）。把它们做成错误码会让前端丢掉模型已经产出的中间结论。

`AGENT_TURN_INTERRUPTED` 与前面这些错误码**不同层**：它是被中断的那次工具调用落在审计 `error_code` 上的码（[§6.15](#615-agent)），中断端点本身返回 `200` + `interrupted: true`。

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
| `PUSH_NOT_CONFIGURED` | 503 | 未配置 VAPID 私钥，或配置的私钥不可解析。Web Push 端点与投递都不可用 |
| `PUSH_SUBSCRIPTION_INVALID` | 422 | 订阅形状不合法：`endpoint` 不是 https 绝对 URL、`keys.p256dh` 不是 65 octet 未压缩 P-256 点或不在曲线上、`keys.auth` 不是 16 octet |
| `PUSH_SUBSCRIPTION_NOT_FOUND` | 404 | 订阅 id 不存在 |

### 4.14 资源

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `RESOURCE_ASSET_NOT_FOUND` | 404 | Copilot 作业 / 基建方案 / 自定义 task 不存在 |
| `RESOURCE_ASSET_CONFLICT` | 409 | `(kind, name)` 重复 |
| `COPILOT_JSON_INVALID` | 422 | 作业 JSON 缺必需字段或结构不符 |
| `INFRAST_PLAN_INVALID` | 422 | 基建方案 JSON 结构不符，或引用了不存在的设施名 |
| `CUSTOM_TASK_INVALID` | 422 | 自定义 task 定义不符合内核 `tasks.json` 的结构约定 |
| `ASSET_TOO_LARGE` | 400 | 单个资源超过 2 MB 上限 |

### 4.15 API 调试台收藏

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `API_SNIPPET_NOT_FOUND` | 404 | API 调试台收藏 id 不存在 |
| `API_SNIPPET_NAME_CONFLICT` | 409 | API 调试台收藏名称重复 |

### 4.16 外部数据源（M25）

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `EXTERNAL_SOURCE_DISABLED` | 503 | 来源被配置关闭：`sources.enabled` 为 `false`，或该来源自己的开关为 `false`（如 `sources.prts_enabled`）。可以降级的工具（`get_activity_stages`、`get_stage`）改用本机兜底、不抛它；没有本地兜底的查询（`search_prts`、`get_prts_page`）直接抛 |
| `EXTERNAL_SOURCE_UNAVAILABLE` | 503 | 网络错误、连接或读取超时、上游 5xx，或该来源正在熔断冷却中（同一来源连续 3 次失败后暂停 60 秒） |
| `EXTERNAL_SOURCE_RATE_LIMITED` | 429 | 本地令牌桶取不到令牌（最多等 5 秒），或远端返回 429 后的冷却期内（时长取 `Retry-After`，缺省 60 秒、封顶 300 秒） |
| `EXTERNAL_RESPONSE_INVALID` | 502 | 响应解压后超过来源上限、JSON 无效，或结构与来源约定不符（MediaWiki 的 `{"error": {...}}` 错误体也走这一条） |
| `GAME_DATA_NOT_FOUND` | 404 | 来源上确实没有这条数据：PRTS 没有这个页面、关卡或搜索结果 |

状态码口径：来源被关闭与取不到都是「现在没有」，用 503；远端 429 与本地限流都透传 429；下游答复了但答复不能用是 502；来源上没有这条数据是 404。可以降级的工具不抛错，返回降级结果并在 `unavailable` 里列出取不到的字段组（`get_activity_stages` 退回本机活动资源、`get_stage` 只回本机能给的字段，[11 §6.6](./11-Agent模块设计.md)）；`search_prts` 与 `get_prts_page` 没有本地兜底，前两个 503 会直接透出。

### 4.17 账号数据（M27）

| 错误码 | HTTP | 含义与触发场景 |
|---|---|---|
| `ACCOUNT_DATA_NOT_FOUND` | 404 | 账号数据里查无此对象：`GET /api/account/operators/{char_id}` 没有该干员的记录，或账号数据尚未识别过 |
| `UPGRADE_PLAN_NOT_FOUND` | 404 | 养成计划 id 不存在（计划的工具入口是 `list_upgrade_plans` / `cancel_upgrade_plan`；计划的 REST 端点留给后续里程碑） |

两个码都是「这条记录不在库里」，与 `GAME_DATA_NOT_FOUND`（来源上没有）区分：账号数据来自本机识别与手动编辑，不查外部来源。字段级编辑约束（精英化上限、专精前置、模组类型与等级）违反时不用新码，走 §4.2 的 `INVALID_PARAMETER`，`details` 带字段名与合法取值。

## 5. 鉴权设计

### 5.1 四个渠道与优先级

单一 `access_token`，支持四种传递方式。取值按固定优先级，取到第一个非空值即停止，**不做回退尝试**：若 `Authorization` 头存在但值错误，直接判 401，不再去看 query 参数，否则攻击者可以用正确的 query 掩盖错误的头，日志会记混。

| 顺序 | 渠道 | 形式 | 主要使用者 |
|---|---|---|---|
| 1 | `Authorization` 头 | `Authorization: Bearer <token>` | 脚本、curl |
| 2 | `X-Token` 头 | `X-Token: <token>` | 前端 SPA 的 `fetch` 封装 |
| 3 | query 参数 | `?token=<token>` | WebSocket、可分享的调试链接 |
| 4 | cookie | `maa_token=<token>` | WebSocket、PWA 场景下的静态资源 |

排序依据是显式程度与泄露风险。头部是 HTTP 认证的标准位置，排最前。query 参数会进入访问日志、浏览器历史与 `Referer`，泄露面最大，因此排在头部之后，但**必须支持**，因为浏览器的 `WebSocket` 构造器不允许自定义请求头。cookie 排最后是因为它会被浏览器自动附加，存在 CSRF 风险。

针对 cookie 的 CSRF 风险有一条额外限制：**仅凭 cookie 鉴权的请求只允许 `GET`、`HEAD` 与 WebSocket 升级**。写方法（`POST`/`PUT`/`PATCH`/`DELETE`）如果 token 只来自 cookie，返回 `403 FORBIDDEN`。前端 SPA 本来就走 `X-Token`，不受影响；这条限制只挡住"用户在别的网站上被诱导对服务发起写请求"的场景。

CORS 使用显式 loopback 白名单（`http://localhost:8002`、`http://127.0.0.1:8002`）加 RFC1918 局域网 origin 正则，保留 `allow_credentials=True`。M10 在请求允许头中补入 `X-Request-Id`，并暴露 `X-Request-Id`、`X-Response-Time-Ms` 与 `Location`：前两者供调试台关联日志和展示服务端耗时，`Location` 让跨源浏览器调用方读取创建收藏的资源地址；任意 Tailscale FQDN 的白名单配置归 M15。

### 5.2 未配置 token 时的行为

`access_token` 为空或未配置时**全部端点免鉴权**，这是对现有行为的完整保留（现有 `token_auth` 就是 `if access_token and access_token != token` 才拒绝）。项目主要部署在家庭局域网，强制鉴权会显著劣化初次使用体验。

但要把这个状态说清楚，不能让它静默：启动日志打印一行醒目的 `未配置 access_token，API 处于免鉴权模式`；`GET /api/system/health` 的响应体带 `"auth_enabled": false`；前端读到该字段后跳过登录页直接进入应用。原方案还要求在设置页顶部常驻一条警告、并提供一键生成随机 token 的入口，这两项尚未实现（见 [13 §8.12](./13-决策记录.md)）。

### 5.3 豁免鉴权的端点

即使配置了 token，以下端点也不校验：

- `GET /api/system/health`：供外部监控与容器健康检查探测，且只返回状态不返回数据
- `GET /docs`、`GET /redoc`、`GET /openapi.json`：只暴露接口结构，不暴露业务数据。"Try it out" 发出的实际请求仍需 token；自研调试台会自动带上 token，这是它相对原生 `/docs` 的主要优势
- `/static/*`、SPA catch-all、`/manifest.webmanifest`、`/sw.js`：前端资源必须先加载出来，用户才有地方填 token。这是对现有行为的**有意修正**：现在 `GET /` 与 `GET /daily` 都挂了 `token_auth`，意味着没有 token 连页面都打不开，只能靠 URL 带 `?token=` 访问


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
| `4401` | token 缺失或无效 | 清除本机 token 并跳到登录页（`/login?returnTo=…`）重新填写，不重连 |
| `4403` | 仅凭 cookie 鉴权但来源不可信 | 不重连 |
| `4429` | 连接数超限（默认 8 条） | 指数退避后重连 |
| `1012` | 服务端重启 | 立即重连 |

协议细节与事件格式见 [06-实时日志与WebSocket](./06-实时日志与WebSocket.md)。

## 6. 全量路由清单

共 110 条 HTTP 端点（与 web/openapi.json 的 operation 数一致，由 tests/domain/test_route_inventory.py 守着），另有 1 条 WebSocket（§6.2）与 4 条静态与文档入口（§6.17）。`core_id` 查询参数在所有内核相关端点上可选，缺省 `default`，为多实例扩展预留，下表不再逐条重复。

### 6.1 system

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/system/health` | 健康检查。返回内核状态、设备状态、队列长度、`auth_enabled` | 免鉴权 | 200 | — |
| GET | `/api/system/logs` | 历史日志查询 | `source[]`、`level[]`、`pipeline_id`、`task_id`、`logger`、`q`、`since`、`until`、`after_id`、`before_id`、`order`、`page`、`size` | 200 | `INVALID_PAGINATION` |
| GET | `/api/system/logs/export` | 导出为附件下载 | `format`（`txt` / `jsonl`）、其余过滤参数同上 | 200 | `INVALID_PARAMETER` |
| DELETE | `/api/system/logs` | 按条件清理日志 | `source[]`、`before` 必填其一 | 204 | `INVALID_PARAMETER` |
| POST | `/api/system/auth/cookie` | 用头部 token 换取 `HttpOnly` cookie，供 WebSocket 使用 | 无请求体 | 204 | `UNAUTHORIZED` |
| DELETE | `/api/system/auth/cookie` | 服务端清除该 cookie，供前端「登出」时调用（cookie 是 `HttpOnly`，JS 删不掉） | 无请求体；仅凭 cookie 调用返回 403 | 204 | `UNAUTHORIZED`、`FORBIDDEN` |

（6 条）

### 6.2 ws

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/ws` | WebSocket 升级。推送日志、流水线状态、队列变更、内核与设备状态、更新进度、待确认事件 | token 走 query 或 cookie；可选 `last_seen_id` 补发缺口 | 101 | close `4401`/`4429` |

（1 条）

### 6.3 device

设备相关操作分两条通道，`backend` 字段在响应中明示。**`click` 走 MaaCore 的 `AsstAsyncClick`；`swipe` / `long_press` / `input_text` / `key_event` 一律走 ADB**：MaaCore 的 C API 里只有点击，没有滑动、长按、输入文本与按键，这是内核的硬约束。所有原子操作**不入队列**，直接投递；流水线运行中调用返回 `409 PIPELINE_ALREADY_RUNNING`，除非带 `force=true`。M10 手动 REST 路径用 `force=true` 时仅写强制介入警告日志，不创建 `agent_audit` 记录；Agent 工具调用的审计依照 M11 的 Agent 工作流契约。

**坐标统一用设备原始像素（M18）。** `click` / `swipe` / `long_press` 的入参坐标与截图（`GET /api/device/screenshot` 原图、Agent 的 `get_screenshot`）的 `width` / `height` 同一坐标系，响应里回显的也是调用方传入的设备像素。换算只在设备管理器做一次：`click` 调 `AsstAsyncClick` 前按「高 / 720」折成 MaaCore 基准坐标（`core = round(px × 720 / 高)`），`swipe` / `long_press` 本来就走 ADB 设备像素。分辨率取设备快照的 `resolution`：未知时 `click` 返回 `503 DEVICE_NOT_CONNECTED`（提示先重连设备），`swipe` / `long_press` 跳过越界校验、照常执行；坐标超出画面一律 `400 INVALID_PARAMETER`。按旧语义（MaaCore 基准坐标）写的调用方要先按「高 / 720」换算，1280×720 设备上两者相同。

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/device/status` | 设备状态机当前状态、地址、分辨率、UUID、最近连接时间 | | 200 | — |
| POST | `/api/device/reconnect` | 手动触发重连（四时机之一） | `address` 可选，覆盖配置 | 202 | `ADB_CONNECT_FAILED`、`ADB_NOT_FOUND` |
| GET | `/api/device/list` | 扫描 `adb devices` 可见设备，供设置页下拉选择 | `include_common_ports` 默认 false | 200 | `DEVICE_SCAN_FAILED`、`ADB_NOT_FOUND` |
| GET | `/api/device/candidates` | 设备扫描候选清单，与 `/api/device/list` 同源：`current` 当前地址 + `devices[]` 设备列表 + `common_ports[]` 常用端口（docs/08 §7），供设置页做下拉选择 + 手动输入的组合 | `include_common_ports` 默认 false | 200 | `DEVICE_SCAN_FAILED`、`ADB_NOT_FOUND` |
| GET | `/api/device/screenshot` | 取一张实时截图 | `backend`（`core`/`adb`，默认 `adb`）、`format`、`quality`、`archive`（默认 false） | 200 | `SCREENSHOT_FAILED`、`DEVICE_NOT_CONNECTED` |
| POST | `/api/device/click` | 单点点击，MaaCore 通道，坐标为设备像素 | `{x, y, force?}` | 200 | `CORE_NOT_READY`、`DEVICE_NOT_CONNECTED`、`INVALID_PARAMETER`、`PIPELINE_ALREADY_RUNNING`、`SERVICE_UNAVAILABLE` |
| POST | `/api/device/swipe` | 滑动，ADB 通道，坐标为设备像素 | `{x1, y1, x2, y2, duration_ms?, force?}` | 200 | `ADB_COMMAND_FAILED`、`INVALID_PARAMETER`、`PIPELINE_ALREADY_RUNNING`、`SERVICE_UNAVAILABLE` |
| POST | `/api/device/long_press` | 长按，ADB 通道（实现为 duration 较长的 swipe 原地操作），坐标为设备像素 | `{x, y, duration_ms, force?}` | 200 | `ADB_COMMAND_FAILED`、`INVALID_PARAMETER`、`PIPELINE_ALREADY_RUNNING`、`SERVICE_UNAVAILABLE` |
| POST | `/api/device/input_text` | 输入文本，ADB 通道 | `{text, force?}` | 200 | `ADB_COMMAND_FAILED` |
| POST | `/api/device/key_event` | 按键事件，ADB 通道。`key` 为白名单枚举（`BACK`/`HOME`/`ENTER`/`DEL`/`APP_SWITCH`） | `{key, force?}` | 200 | `INVALID_PARAMETER`、`ADB_COMMAND_FAILED` |

**不开放任意 `adb shell`。** 按键用枚举白名单而非透传字符串，是为了守住这条边界：透传 keycode 字符串等价于开放一个受限的 shell。

（10 条）

### 6.4 core

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| POST | `/api/core/back_to_home` | 回游戏主界面，MaaCore 原生能力 | `{force?}` | 200 | `CORE_NOT_READY`、`PIPELINE_ALREADY_RUNNING` |

内核子进程没有独立的 HTTP 端点：状态（`state` / `pid` / `generation`）读 `GET /api/system/health` 的 `core` 段（§6.1），内核与资源版本读 `get_versions` 工具、重启子进程走 `restart_core` 工具，都只在 Agent 侧提供（§6.15）；当前只有一条 `default` 内核，因此也没有内核实例列表端点。关卡互查同理，只剩 `resolve_stage` 工具：优先用 `AsstGetMapLevelKey`，查不到或符号缺失时降级到本地资源索引，而不是报错：该函数位于 `AsstCallerExtra.h`，官方明确标注不属于受支持的公共 API、可能随时变更或移除。

（1 条）

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
| GET | `/api/tasks/types` | 9 种 MaaCore 内置任务与 `Copilot` 的完整参数 schema，供前端生成表单、agent 了解可用参数。M17 起还会读 setting 表，把「当前生效」的渠道默认值写进 `client_type` / `server` 的 `default`（见 §9） | `lang` 默认 `zh` | 200 | — |
| GET | `/api/tasks/types/{type_name}` | 单个类型的 schema（同样带渠道默认值 overlay，不套分页信封） | | 200 | `UNKNOWN_TASK_TYPE` |
| POST | `/api/tasks/validate` | 只校验不提交，返回规范化后的参数与将被注入的默认值 | `{tasks[]}` | 200 | `UNKNOWN_TASK_TYPE`、`TASK_PARAM_INVALID` |
| GET | `/api/tasks/{task_id}` | 单任务详情（状态、重试次数、`params` 与 `raw_params`、耗时） | | 200 | `TASK_NOT_FOUND` |

`POST /api/tasks/validate` 存在的意义是给 agent 一个安全的试错入口：先校验再提交，不必等提交失败后从错误信息里反推参数长什么样。

（4 条）

### 6.8 screenshots

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/screenshots` | 截图列表，分页 | `pipeline_id`、`trigger`、`since`、`page`、`size` | 200 | `INVALID_PAGINATION` |
| GET | `/api/screenshots/{id}` | 返回图像本体。`?as=base64` 时返回 JSON 包装，供 Agent 使用 | | 200 | `SCREENSHOT_NOT_FOUND`、`SCREENSHOT_EXPIRED` |
| GET | `/api/images/{sha256}/thumb` | 长边 320px 的 JPEG 缩略图，日志附件与截图网格用；按内容哈希寻址 | 路径是截图内容的 sha256；响应可长期缓存（`Cache-Control: immutable`） | `200 image/jpeg` | `SCREENSHOT_NOT_FOUND` |
| GET | `/api/images/{sha256}/full` | 原图 JPEG，点开大图时用；按内容哈希寻址 | 同上 | `200 image/jpeg` | `SCREENSHOT_NOT_FOUND` |

（4 条）

### 6.9 updates

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/updates/status` | 三个目标的当前版本 vs 最新版本对比，带 `has_update`；`resource` 下按两条通道分别给出 | `refresh` 默认 false（用缓存）；`refresh=true` 即「立即检查更新」，绕过 5 分钟缓存 | 200 | `UPDATE_MANIFEST_UNAVAILABLE`、`GAME_VERSION_UNKNOWN` |
| POST | `/api/updates/core` | 更新 MaaCore。下载后停子进程、覆盖、重启，HTTP 服务不中断 | `{channel?, force?, version?}` | 202 | `UPDATE_ALREADY_RUNNING`、`ALREADY_LATEST_VERSION`、`UPDATE_BLOCKED_BY_PIPELINE` |
| POST | `/api/updates/core/rollback` | 回滚 MaaCore：在维护窗口内把当前目录与 `<target>.backup` 对调，重启并验证 READY，任一步失败就对调回来 | `{force_interrupt?}`：默认等队列排空（30 分钟），`true` 打断运行中的流水线、需确认策略放行 | 202 | `UPDATE_ALREADY_RUNNING`、`UPDATE_ROLLBACK_FAILED`、`UPDATE_BLOCKED_BY_PIPELINE`、`UPDATE_QUEUE_BUSY_TIMEOUT`、`SERVICE_UNAVAILABLE`、`FORBIDDEN` |
| POST | `/api/updates/resource` | 更新活动资源。`channel` 选通道，默认两条都更 | `{channel?, force?}`：`channel` 取 `ota` / `repo` / `all`，默认 `all` | 202 | `UPDATE_ALREADY_RUNNING`、`UPDATE_MANIFEST_UNAVAILABLE`、`UPDATE_DISK_INSUFFICIENT`、`INVALID_PARAMETER` |
| POST | `/api/updates/game` | 更新游戏本体：下载 APK + `adb install` 覆盖安装 | `{channel?, force?}`（`channel` 区分官服/B 服包） | 202 | `GAME_INSTALL_FAILED`、`DEVICE_NOT_CONNECTED` |
| GET | `/api/updates` | 更新历史，分页 | `target`、`status`、`page`、`size` | 200 | `INVALID_PAGINATION` |
| GET | `/api/updates/{id}` | 单条更新的进度与日志，供轮询 | | 200 | `UPDATE_NOT_FOUND` |
| POST | `/api/updates/{id}/retry` | 重放一条失败的更新：沿用原记录的 `target` 与参数（含已下载的暂存文件），新记录带 `_retry_of` 指回原条目 | 无请求体；只受理 `FAILED` 状态，其余状态一律按 `UPDATE_NOT_CANCELLABLE` 拒绝 | 202 | `UPDATE_NOT_FOUND`、`UPDATE_NOT_CANCELLABLE`、`UPDATE_ALREADY_RUNNING` |
| DELETE | `/api/updates/{id}` | 取消进行中的更新。已进入覆盖/重启阶段则拒绝 | | 202 | `UPDATE_NOT_FOUND`、`UPDATE_NOT_CANCELLABLE` |

进度不靠轮询也能拿：更新过程的 `phase` 与 `progress` 同时经 WebSocket 广播，`GET /api/updates/{id}` 是给不方便用 WS 的调用方（脚本）留的。

活动资源有两条来源不同的通道（见 [07-热更新方案 §3](./07-热更新方案.md)），**但没有为它们拆出独立端点**。`POST /api/updates/resource` 用 `channel` 参数区分，取值对应 `ResourceChannel` 枚举（见 [04-数据模型与持久化 §4](./04-数据模型与持久化.md)）：

- `all`（默认）：两条通道依次检查并更新，任一有更新就触发一次重载
- `ota`：只更通道 A 的活动 task 定义，秒级完成
- `repo`：只更通道 B 的数据文件与模板图，需要下载约 13 MB 压缩包、解压出约 107 MB

不拆端点的理由与 `update_record.target` 不拆通道是同一条：三种更新共用一把互斥锁，同一时刻只允许一条 `target='resource'` 的记录在跑。两个端点会让调用方误以为可以并发触发，实际仍会撞 `UPDATE_ALREADY_RUNNING`。`channel='repo'` 才可能返回 `UPDATE_DISK_INSUFFICIENT`：通道 A 只有 15 KB，预检没有意义。

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

（9 条）

### 6.10 schedules

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/schedules` | 定时任务列表，带 `next_run_at` 与上次执行结果 | `enabled` 可选过滤 | 200 | — |
| POST | `/api/schedules` | 新建。`template` 结构等同 `POST /api/pipelines` 的 `tasks` | `{name, cron, timezone?, template, enabled?, ...}` | 201 | `SCHEDULE_CRON_INVALID`、`SCHEDULE_NAME_CONFLICT`、`TASK_PARAM_INVALID` |
| GET | `/api/schedules/{id}` | 详情，含最近 N 次执行记录 | | 200 | `SCHEDULE_NOT_FOUND` |
| PUT | `/api/schedules/{id}` | 全量替换 | 同 POST | 200 | `SCHEDULE_NOT_FOUND`、`SCHEDULE_CRON_INVALID` |
| PATCH | `/api/schedules/{id}` | 局部更新，主要用于启用开关 | `{enabled?, cron?, priority?}` | 200 | `SCHEDULE_NOT_FOUND` |
| DELETE | `/api/schedules/{id}` | 删除 | | 204 | `SCHEDULE_NOT_FOUND` |
| POST | `/api/schedules/{id}/run` | 立即执行一次，按 `template` 提交流水线 | `{priority?}`：默认沿用 schedule 的优先级 | 202 | `SCHEDULE_NOT_FOUND`、`QUEUE_FULL`、`PIPELINE_ALREADY_RUNNING` |

`template` 在创建与更新时就走一遍完整的任务校验，不等到触发时才报错：定时任务的失败最难被发现，配置阶段拦住是唯一有效的时机。

（7 条）

### 6.11 settings

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/settings` | 读取生效中的全部配置，敏感项脱敏为 `"***"`，并标注每项来自哪一层（env/db/yaml/default） | `group` 可选 | 200 | — |
| PUT | `/api/settings` | 批量写入并热生效。敏感项传 `"***"` 或空串视为不变更 | `{items: {key: value}}` | 200 | `SETTING_KEY_UNKNOWN`、`SETTING_VALUE_INVALID`、`SETTING_READONLY`、`SETTING_APPLY_FAILED`、`LLM_ENCRYPTION_KEY_INVALID` |
| GET | `/api/settings/schema` | 配置项元信息：中文标签、分组、类型、取值范围、枚举可选值、是否敏感、热生效动作 | | 200 | — |
| POST | `/api/settings/reset` | 清除指定项的数据库覆盖，回落到 `config.yaml` 或默认值 | `{keys[]}` | 200 | `SETTING_KEY_UNKNOWN` |

`PUT` 的部分成功要处理明确：值已入库但热生效失败时返回 `500 SETTING_APPLY_FAILED`，`details.applied = true` 告诉前端"配置已保存，但重连设备失败"，前端提示用户手动重连而不是让他以为没保存上。

Agent 设置包含 `agent.auto_diagnose_failures`，true/false 控制流水线与空闲内核故障的自动诊断；默认 true，更新值必须为 JSON boolean。

（4 条）

### 6.12 API 调试台收藏

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/snippets` | 收藏列表，按 `updated_at` 倒序 | | 200 | — |
| POST | `/api/snippets` | 创建收藏 | `{name, method, path, path_params?, query?, headers?, body?}`；名称 trim 后 1–64 字符 | 201 | 409（名称重复）、422 |
| GET | `/api/snippets/{snippet_id}` | 读取单个收藏 | | 200 | 404 |
| PUT | `/api/snippets/{snippet_id}` | 全量更新收藏 | 同 POST | 200 | 404、409（名称重复）、422 |
| DELETE | `/api/snippets/{snippet_id}` | 删除收藏 | | 204 | 404 |

`headers` 不能包含 `Authorization`、`Cookie`、token、API key、password 或 secret 类凭据，`query` 不能包含 `token`；服务端写入和读取时再次过滤，避免调用方绕过前端或读取历史脏数据。名称唯一性区分大小写。创建响应带 `Location: /api/snippets/{snippet_id}`。

（5 条）

### 6.13 notifications

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/notifications/channels` | 通道列表，`config` 脱敏，带最近发送状态 | `type`、`enabled` | 200 | — |
| POST | `/api/notifications/channels` | 新建通道 | `{type, name, config, events[], enabled?}` | 201 | `NOTIFY_CONFIG_INVALID`、`NOTIFY_CHANNEL_CONFLICT` |
| PUT | `/api/notifications/channels/{id}` | 更新通道 | 同 POST | 200 | `NOTIFY_CHANNEL_NOT_FOUND`、`NOTIFY_CONFIG_INVALID` |
| DELETE | `/api/notifications/channels/{id}` | 删除通道 | | 204 | `NOTIFY_CHANNEL_NOT_FOUND` |
| POST | `/api/notifications/channels/{id}/test` | 发送测试消息，同步等结果（超时 10 秒） | `{event?}`：默认用 `pipeline_completed` 的样例数据 | 200 | `NOTIFY_SEND_FAILED`、`NOTIFY_CHANNEL_NOT_FOUND` |
| GET | `/api/notifications/vapid-public-key` | 取 Web Push 的 VAPID 公钥，作为前端 `applicationServerKey` | | 200 | `PUSH_NOT_CONFIGURED` |
| GET | `/api/notifications/push-subscriptions` | 订阅列表，**不回显** `endpoint` 全文与 `p256dh`/`auth` | `include_disabled`（默认 false） | 200 | — |
| POST | `/api/notifications/push-subscriptions` | 登记订阅，请求体为 `PushSubscription.toJSON()` 的形状 | `{endpoint, keys:{p256dh, auth}}` | 201（同 endpoint 重放为 200） | `PUSH_SUBSCRIPTION_INVALID` |
| DELETE | `/api/notifications/push-subscriptions/{id}` | 删除订阅 | | 204 | `PUSH_SUBSCRIPTION_NOT_FOUND` |
| POST | `/api/notifications/push-subscriptions/delete` | 按 `endpoint` 注销当前浏览器的订阅（浏览器注销时手上只有 endpoint，而列表不回显它） | `{endpoint}` | 204 | — |

测试发送用 200 同步返回而不是 202，因为用户点"测试"就是要立刻知道配置对不对；异步受理会让他还得去别处查结果。

Web Push 订阅按 `endpoint` 幂等：浏览器对同一个 Service Worker 重复订阅拿到的是同一个 endpoint，重放注册更新既有行并返回 200 而不是新增一条。列表只返回 `id`、`endpoint_host`、`user_agent`、时间与失效状态：`endpoint` 全文本身就是投递凭据，`p256dh`/`auth` 更是解密推送所必需，三者与凭据同级。推送服务返回 404/410 时订阅被标记失效（保留行供设置页展示），重新订阅会清除该标记。注销提供了两个入口：按 id，以及按 endpoint（`endpoint` 放在请求体里而不是 URL，避免它进入访问日志；找不到对应订阅同样返回 204，因为注销是幂等操作）。

（10 条）

### 6.14 resources

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/resources/items` | 读取当前 MaaCore `resource/item_index.json`，供 `Fight.drops` 按名称搜索 | | `200 [{item_id, name, icon_url}]` | `RESOURCE_LOAD_FAILED` |
| GET | `/api/resources/items/icon` | 返回物品索引中登记的图标 | `item_id` | `200 image/png` | `RESOURCE_ASSET_NOT_FOUND`、`RESOURCE_LOAD_FAILED` |
| GET | `/api/resources/copilots` | Copilot 作业元数据列表（不含作业 JSON），供流水线和 schedule 编辑器选择 `asset_id` | `page`、`size`、`q` | `200 {items:[{id,name,meta,...}],total,page,size}` | `SERVICE_UNAVAILABLE` |

`GET /api/resources/items` 返回按名称（忽略大小写）与 `item_id` 排序的完整数组；`item_id` 是索引对象的 key，`name` 来自对应记录。`icon_url` 是同源 `/api/resources/items/icon?item_id=...` URL；索引记录的 `icon` 所指 PNG 不存在、不可读或路径不在 `resource/template/items/` 内时返回 `null`。图标端点仅允许读取索引中登记且解析后仍位于该目录的 PNG。索引缺失、不可读或结构无效时返回统一 JSON 错误 `RESOURCE_LOAD_FAILED`（500）。响应缓存按索引文件元信息失效；端点仅读本地文件，不加载 MaaCore，也不访问网络。

资源的写入侧没有 HTTP 端点，只在 Agent 侧以工具形式提供：`upload_copilot`（校验并保存作业 JSON）、`set_infrast_plan`、`list_custom_tasks` / `register_custom_task` / `remove_custom_task`（增量注入 `tasks.json`），经 `POST /api/agent/tools/{name}/invoke` 调用（§6.15）。内核只认磁盘上的资源文件：自定义 task 的注入必须经由 `LOAD_RESOURCE` 生效，因此这一层在 `ResourceService` 内部完成「合并启用中的增量目录 + 重载」，注册与移除由服务级锁串行化，重载失败时回滚改动并返回 `RESOURCE_LOAD_FAILED`（重载要把整条加载链从头走一遍，命令超时预算 300 秒，见 [02-系统架构设计 §3.4](./02-系统架构设计.md)）；基建方案与作业 JSON 是任务执行时读取的数据文件，写盘即生效，不触发重载。

（3 条）

### 6.15 agent

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/agent/tools` | 工具清单与 JSON Schema，含 `risk_level` 标注 | `risk_level` 可选过滤 | 200 | `AGENT_DISABLED` |
| POST | `/api/agent/tools/{name}/invoke` | REST 调用方直接执行工具，使用共享 `ToolRegistry` | `{arguments, mode?}`：`mode=sync` 阻塞等结果，`mode=async` 立即返回 | 200 / 202 | `TOOL_NOT_FOUND`、`TOOL_ARGS_INVALID`、`CONFIRMATION_REQUIRED`（202） |
| GET | `/api/agent/sessions` | 会话列表，按 `last_message_at` 倒序 | `page`、`size` | 200 | — |
| POST | `/api/agent/sessions` | 新建会话，快照当前 `model` 与 `base_url`；默认模型 `deepseek-flash` | `{title?}` | 201 | `LLM_NOT_CONFIGURED`、`LLM_ENCRYPTION_KEY_INVALID` |
| POST | `/api/agent/capabilities/check` | 检查当前模型的 function calling 与视觉输入能力并缓存结果 | 无 body | 200 | `LLM_NOT_CONFIGURED`、`LLM_ENCRYPTION_KEY_INVALID` |
| GET | `/api/agent/sessions/{id}` | 会话详情与 token 消耗统计，含本轮是否仍在执行（`turn_running`） | | 200 | `AGENT_SESSION_NOT_FOUND` |
| DELETE | `/api/agent/sessions/{id}` | 删除会话，消息级联删除 | | 204 | `AGENT_SESSION_NOT_FOUND` |
| GET | `/api/agent/sessions/{id}/messages` | 消息与工具调用轨迹，按 `seq` 升序 | `after_seq`、`page`、`size` | 200 | `AGENT_SESSION_NOT_FOUND` |
| POST | `/api/agent/sessions/{id}/messages` | 发送自然语言消息，执行 tool-calling 循环并以 SSE 返回轨迹 | `{content}` | 200 `text/event-stream` | `AGENT_SESSION_BUSY`、`LLM_NOT_CONFIGURED`、`LLM_ENCRYPTION_KEY_INVALID`、`LLM_CONTEXT_OVERFLOW` |
| POST | `/api/agent/sessions/{id}/interrupt` | 中断该会话正在执行的一轮（M22）。幂等；没有活动轮次也返回 200 | 无 body | 200 | `AGENT_SESSION_NOT_FOUND`、`AGENT_DISABLED` |
| DELETE | `/api/agent/sessions/{id}/atomic-grant` | 撤销该会话的原子操作授权，挂起中的调用按拒绝处理 | | 204 | `AGENT_SESSION_NOT_FOUND` |
| GET | `/api/agent/audits` | 审计列表 | `caller`、`tool_name`、`status`、`risk_level`、`since`、`page`、`size` | 200 | `INVALID_PAGINATION` |
| GET | `/api/agent/audits/{id}` | 单条审计详情，含完整参数与结果摘要 | | 200 | `NOT_FOUND` |
| GET | `/api/agent/knowledge` | Agent 知识库条目列表（`items` + `total`），不含正文 | | 200 | — |
| GET | `/api/agent/knowledge/{entry_id}` | 条目详情，含 `body`；内置条目另附 `builtin` 原文 | `entry_id` 匹配 `^[a-z0-9][a-z0-9-]{1,47}$` | 200 | `KNOWLEDGE_NOT_FOUND`、`VALIDATION_ERROR` |
| PUT | `/api/agent/knowledge/{entry_id}` | 写入完整的生效内容：新建自定义条目，或修改 / 停用内置条目 | `{kind, title, when_to_use, body, enabled?}`（`title` ≤ 32 字、`when_to_use` ≤ 120 字、`body` ≤ 20,000 字） | 200 | `KNOWLEDGE_BUILTIN_READONLY`、`VALIDATION_ERROR` |
| DELETE | `/api/agent/knowledge/{entry_id}` | 删除自定义条目，或撤销对内置条目的修改（恢复默认） | | 204 | `KNOWLEDGE_NOT_FOUND`、`KNOWLEDGE_BUILTIN_READONLY`、`VALIDATION_ERROR` |

LLM 配置（`base_url` / `api_key` / `model`）不在 agent 组下开独立端点，它是 `setting` 表的三个 key，走 `/api/settings`。默认模型为 DeepSeek Flash（`deepseek-flash`），API Key 由服务端用部署环境变量 `MAA_LLM_ENCRYPTION_KEY` 加密后写入 SQLite；端点仍可由用户配置覆盖。

`POST /api/agent/capabilities/check` 以小型工具调用和测试图片分别检查 function calling 与视觉输入。首次发送消息前会自动补做一次缓存检查；工具调用失败则停止对话，视觉失败只禁用截图和原子屏幕操作，并保留文本对话。结果按 `(model, base_url)` 缓存在进程内存：探查成功的结果不过期，探查本身没跑通的结果只保鲜 60 秒后重新探查，见 [11 §8.1](./11-Agent模块设计.md)。`degraded_reason` 直接说明失败原因：能力确实缺失时说明缺哪项；上游拒绝探查请求（模型标识不存在、鉴权失败等）时带上已脱敏、截断的上游 `error.message`、HTTP 状态与上游错误码；请求根本没到达上游（TLS 证书校验失败、域名解析失败、连接被拒、流中断）时带上同类结论与已脱敏、截断的原文，见 [13 §8.13](./13-决策记录.md) 与 [13 §8.30](./13-决策记录.md)。

`POST /api/agent/sessions/{id}/messages` 以 `message_start`、`text_delta`、`tool_start`、`tool_result`、`confirmation_required`、`question_required`、`session_title`、`interrupted`、`message_end` 和 `error` 事件流式返回结果。每个会话同一时间只运行一轮；客户端断开不会回滚已提交的工具副作用。

**中断本轮（M22）。** `POST /api/agent/sessions/{id}/interrupt` 幂等：返回 `{"session_id", "interrupted", "turn_running"}`，其中 `interrupted` 表示这次调用是否真的停掉了一轮，该会话没有活动轮次时是 `false`（200，不是错误）；`turn_running` 是返回这一刻该会话是否仍有活动轮次（正常收尾后为 `false`）。中断会停掉 LLM 流、撤回本轮还在等确认或在跑的工具、把挂起的提问置 `cancelled`，然后落一条 `finish_reason="interrupted"` 的助手消息、推 SSE `interrupted` 并释放会话锁（返回后即可发下一条消息）。两条边界：**不动流水线与队列**，要停在跑的流水线仍走显式的 `DELETE /api/pipelines/{id}`（或 Agent 的 `stop_pipeline`），已入队的任务一律保留；**已批准并已在执行的工具可能已经产生副作用且无法回滚**，中断只保证不再等它、不再有后续工具。中断是**主动**动作：客户端断网或关页面（SSE 被动断开）仍不取消本轮，这一轮会在后台继续执行，重开页面后靠 `GET /api/agent/sessions/{id}` 的 `turn_running` 恢复停止入口（前端行为见 [09 §6.4](./09-前端重构方案.md#64-agent-agent)）。

`agent.auto_diagnose_failures` 默认开启。流水线终态失败和内核崩溃各按持久 incident key 最多创建一个可继续对话的诊断会话；关闭设置后不自动调用模型。自动诊断只暴露安全查询与恢复工具，仍遵循原子操作会话授权和资源消耗/破坏操作逐次确认策略。

**知识库端点**（M18）由独立的路由文件实现，但沿用 `agent` tag（`operationId` 仍是 `agent_*`）。内置条目是随代码发布的只读 Markdown，数据库只存用户的改动与自定义条目；`source`（`builtin` / `custom`）与 `modified` 由读侧的合并结果给出，供设置页显示「内置 / 已修改 / 自定义」徽标。`PUT` 收的是**完整的生效内容**而不是增量，与内置完全一致时等价于「恢复默认」；字段格式与长度不合法是 `422 VALIDATION_ERROR`，删除未修改过的内置条目或修改内置条目的 `kind` 是 `409 KNOWLEDGE_BUILTIN_READONLY`。启用条目数与术语表加档案的正文合计数不设上限（M20 用户裁决：注入多少以模型自身的上下文窗口为准，装不下时由上游拒绝并返回 `400 LLM_CONTEXT_OVERFLOW`）。写操作记一条服务日志，不进 Agent 审计；Agent 侧只有只读的 `read_knowledge` 工具，没有任何写通道。

**Agent 提问端点**（M21）同样由独立的路由文件实现并沿用 `agent` tag（`operationId` 仍是 `agent_*`）：提问是会话交互的一部分，与知识库端点同一取舍。它与确认链路完全分开，路由表在 [§6.18](#618-agent-提问)。

（17 条）

### 6.16 confirmations

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/confirmations` | 确认请求列表，默认只返回 `pending` | `status`、`page`、`size` | 200 | — |
| GET | `/api/confirmations/{id}` | 详情，含 `payload` 与命中的策略说明 | | 200 | `CONFIRMATION_NOT_FOUND` |
| POST | `/api/confirmations/{id}` | 批准或拒绝 | `{approved: bool, reason?}` | 200 | `CONFIRMATION_NOT_FOUND`、`CONFIRMATION_ALREADY_RESOLVED`、`CONFIRMATION_EXPIRED` |

用单个 `POST /api/confirmations/{id}` 带布尔字段，而不是 `/approve` 与 `/reject` 两个端点，原因是前端弹卡片的两个按钮共用一份请求逻辑，且拒绝必须能带 `reason`。返回 200 而非 202：批准这个动作本身是同步完成的（状态落库 + 唤醒等待方），被批准的动作是否完成则由它自己的资源去表达。

**会话级原子操作授权复用这同一套确认流程**，不另起端点。授权请求是一条 `action` 为 `grant_atomic_ops` 的确认记录，`payload` 为 `{"session_id": "...", "window_seconds": 900}`；批准它的"执行"就是在 `agent_session` 上写入授权窗口。撤销走上面的 `DELETE /api/agent/sessions/{id}/atomic-grant`，不是再发一条确认。这样前端只需实现一种确认卡片，只是文案按 `action` 区分（机制见 [11-Agent模块设计 §4.3](./11-Agent模块设计.md)）。

`GET /api/agent/sessions/{id}` 的响应带 `atomic_grant: {granted, expires_at, grant_id} | null`，供前端在页面刷新后恢复授权提示；WebSocket 的 `atomic_grant_changed` 事件用于刷新会话详情。

（3 条）

### 6.17 静态资源与文档

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| GET | `/docs` · `/redoc` · `/openapi.json` | FastAPI 原生文档，作为自研调试台的备用 | 豁免 |
| GET | `/static/{path:path}` | Vite 构建产物的静态挂载 | 豁免 |
| GET | `/` | SPA 入口，返回 `static/index.html` | 豁免 |
| GET | `/{path:path}` | SPA catch-all，非 `/api`、`/static` 前缀的路径一律回落到 `index.html`，由前端路由接管 | 豁免 |

catch-all 必须是**最后注册**的路由，且显式排除 `/api`、`/static` 两个前缀，否则拼错的 API 路径会返回一份 HTML 而不是 404 JSON：这种错误在前端调试时极难定位。

（4 条）

### 6.18 Agent 提问

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/agent/questions` | 提问列表，默认只返回 `pending` | `session_id`、`status`、`page`、`size`（1–200，默认 20） | 200 | `INVALID_PAGINATION` |
| GET | `/api/agent/questions/{question_id}` | 单条提问的完整状态 | | 200 | `QUESTION_NOT_FOUND` |
| POST | `/api/agent/questions/{question_id}` | 提交选择，被挂起的那一轮继续执行 | `{selected: ["..."]}`：选中项的 label 列表，去重后每一项都必须在 `options` 里，单选只允许一项 | 200 | `QUESTION_NOT_FOUND`、`QUESTION_ALREADY_ANSWERED`、`QUESTION_EXPIRED`、`QUESTION_SELECTION_INVALID` |
| POST | `/api/agent/questions/{question_id}/skip` | 跳过这次提问，本轮结束 | 无 body | 200 | `QUESTION_NOT_FOUND`、`QUESTION_ALREADY_ANSWERED`、`QUESTION_EXPIRED` |

四个端点都 `Depends(require_auth)`。返回的是**单条 wire**（`question_id`、`session_id`、`tool_call_id`、`question`、`options[{label, description}]`、`multiple`、`status`、`selection`、`created_at`、`expires_at`、`answered_at`、`answered_by`，时间为 `...Z`），与 SSE `question_required` 的载荷同源；列表用统一的分页信封 `{items, total, page, size}`，`status` 传 `pending` 之外的取值可以看历史提问。提问服务未启动时四个端点都返回 `503 AGENT_DISABLED`。

**提问不是确认**（[04 §5.17](./04-数据模型与持久化.md)）：这条链路没有「批准 / 拒绝」，只有「答 / 跳过 / 过期」；不授权任何操作，也不占「待确认」计数，因此前端不为它做全局面板与 Web Push。`question_id` 指向的是模型那次 `ask_user` 调用留下的记录（见 [11 §5.3](./11-Agent模块设计.md)）。

错误码的口径值得写清，它决定了前端在竞态下看到什么：

- 已 `answered` / `skipped` 的提问再提交是 `409 QUESTION_ALREADY_ANSWERED`（多标签页同时点选、刷新后重放都会命中），已 `expired` 是 `409 QUESTION_EXPIRED`。「你来晚了」不能说成「你选错了」，所以状态判定排在参数校验之前。
- 时间上已过期（`expires_at < now`）但状态还是 `pending` 的提问，在返回 `409 QUESTION_EXPIRED` 之前**就地收敛成终态**并广播；否则它会一直挂在待答列表里，用户点了只拿到一个错。
- 空选择、选项不在 `options.label` 里、单选提交多项是 `400 QUESTION_SELECTION_INVALID`，`details.options` 带上合法的 label 列表供前端兜底渲染。`selected` 因此声明成必填的字符串数组而不是 `min_length=1`：空数组是「没有选择」这个业务错误，加字段级约束会让同一种错误在 400 与 422 之间漂移。
- 超时或跳过后本轮以 `finish_reason="question_unanswered"` 结束，这是正常终态而不是错误（同 [§4.11](#411-agent-与-llm) 的 `stuck_loop` / `max_steps` 口径）。等待时长由 `agent.question_timeout_seconds` 配置，默认 600 秒，落在那条提问的 `expires_at` 上。

（4 条）

### 6.19 外部数据源

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/sources/status` | 各外部来源的状态快照 | 无 | 200 | `UNAUTHORIZED` |
| DELETE | `/api/sources/cache` | 清空全部或单个来源的内存缓存 | `source`（可选，来源 kind，如 `prts`） | 200 | `INVALID_PARAMETER`、`UNAUTHORIZED` |

`GET /api/sources/status` 每个来源返回 `kind`、`label`、`enabled`（总开关关闭时全为 `false`）、`rate_per_minute`、`last_success_at`、`last_error_at` / `last_error_code` / `last_error_message`、`consecutive_failures`、`cooldown_until` / `cooldown_seconds`、`requests`、`bytes_read`、`retries`，以及 `cache`（`entries`、`bytes`、`hits`、`misses`、`hit_rate`、`evictions`），供设置页「外部数据源」分区与排查使用。

`DELETE /api/sources/cache` 不传 `source` 时清空全部来源，返回 `{source, cleared}`（`cleared` 是清掉的条数）；`source` 不是已登记的来源时返回 `400 INVALID_PARAMETER`，`details.known` 给出可用的取值。缓存只存在进程内存，重启即清空。

两个端点都 `Depends(require_auth)`。**本组只有状态与缓存清理，没有检索代理**（Q25）：Agent 的工具调用入口沿用既有机制，工具目录照常出现在 `GET /api/agent/tools`，不让本服务变成 PRTS 的代理。M25 只登记 `prts` 一个来源，企鹅物流、作业站与拆包数据在各自里程碑加入。来源开关、限流、缓存时长与是否走代理都由 `sources` 段控制，见 [§4.16](#416-外部数据源m25)、[11 §6.6](./11-Agent模块设计.md) 与 [15 §7](./15-部署指南.md)。

（2 条）

### 6.20 账号数据

| 方法 | 路径 | 用途 | 请求要点 | 成功 | 主要错误码 |
|---|---|---|---|---|---|
| GET | `/api/account/summary` | 账号数据概要与是否过期 | 无 | 200 | `UNAUTHORIZED` |
| GET | `/api/account/operators` | 干员表，默认只返回已拥有 | `query`（名字片段）、`owned_only`（默认 `true`，`false` 时连未拥有的一起列出）、`rarity_min`（1–6）、`profession`、`elite_min`（0–2）、`limit`（1–200，默认 50）、`offset` | 200 | `UNAUTHORIZED` |
| GET | `/api/account/operators/{char_id}` | 单名干员的完整记录（编辑抽屉打开时读） | 无 | 200 | `ACCOUNT_DATA_NOT_FOUND`、`UNAUTHORIZED` |
| PATCH | `/api/account/operators/{char_id}` | 手动编辑干员数据 | 只接受可编辑字段，未知字段 422：`own`、`elite`、`level`、`potential`、`skill_level`、`masteries`、`modules`、`source`（`manual` / `agent`，默认 `manual`） | 200 | `ACCOUNT_DATA_NOT_FOUND`、`INVALID_PARAMETER`、`VALIDATION_ERROR`、`UNAUTHORIZED` |
| GET | `/api/account/operator-diffs` | 识别覆盖手动值的差异 | `include_dismissed`（默认 `false`）、`limit`（1–500，默认 200） | 200 | `UNAUTHORIZED` |
| DELETE | `/api/account/operator-diffs` | 把差异标记为已知 | `{ids: [int]}` 逐条，或省略＝全部 | 200 | `UNAUTHORIZED` |
| GET | `/api/account/depot` | 仓库识别结果，按分类分组 | `category`、`query`、`limit`（1–500，默认 100）、`offset` | 200 | `UNAUTHORIZED` |

七个端点都 `Depends(require_auth)`。列表用 `{items, total, limit, offset}`（§3.2 的 offset 口径，与 `page`/`size` 的两套分页并存），单条响应给出干员的全量字段与 `field_sources`（各字段来自 `recognition` / `manual` / `agent`）。

干员行的字段是 `char_id`、`name`、`rarity`、`profession`、`own`、`elite`、`level`、`potential`、`skill_level`、`masteries`（`{"1": 0..3, "2": …, "3": …}`）、`modules`（模组类型 `X` / `Y` / `D` / `A` → 等级 1–3）、`field_sources`、`recognized_at`、`updated_at`；仓库行的字段是 `item_id`、`name`、`count`、`category`、`recognized_at`。

`PATCH` 的字段级约束由服务层判定（[§4.17](#417-账号数据m27)）：精英化上限按稀有度（1–2★ 精零、3★ 精一、4–6★ 精二）、专精要求精二且技能等级 7、模组要求精二且等级 1–3。识别结果的合并规则见 [11 §6.6](./11-Agent模块设计.md)与规格 §7.3：识别只覆盖「是否拥有、精英化、等级、潜能」，技能等级、专精、模组只由手动或 Agent 写入；被识别覆盖掉的手动值记一条差异，页面顶部提示，可逐条或全部「知道了」。`agent.box_stale_days`（默认 14）与 `agent.depot_stale_days`（默认 3）是两个过期阈值，见 [§4.16](#416-外部数据源m25) 同级的设置分组与附录 B。

`source` 只接受 `manual` 与 `agent`：识别写入固定标 `recognition`，客户端不能借这个字段把识别结果伪装成手动值，所以传 `recognition` 是 422（Pydantic 枚举校验）。

单条 `GET` 是编辑抽屉的读口（附录 C 只列了六条，这一条是实现补的读端点），与列表项同形。

（7 条）

## 7. 任务提交的请求体设计

### 7.1 废弃扁平 `TaskRequest`

现有 `maa_api/model/request/request.py` 把 9 种任务的全部参数塞进一个类，约 70 个 `Optional` 字段，再用 `to_task()` 里的 if 链分发。这个设计有四个不可接受的后果：

提交 `Fight` 时可以传 `theme="Sami"`，校验器不会拦，参数被静默丢弃。字段在类里重复定义（`times` 出现两次，后一个覆盖前一个）。OpenAPI 文档里所有任务类型看起来都接受全部 70 个参数，前端与 agent 无法据此生成正确的表单或工具 schema。还有几个末尾带逗号的字段声明（`tools_to_craft: Optional[list[str]] = None,`）让默认值变成了元组 `(None,)`。

新设计按任务类型分离模型，用 Pydantic 的 **discriminated union**，discriminator 沿用现有的 `name` 字段：保持这个字段名可以让现有前端的请求体结构基本不变，降低迁移成本。

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

    enable: bool | None = MaaField(default=True, label="启用本任务")

    def to_core_params(self) -> dict[str, Any]:
        """导出为投递给 AsstAppendTask 的参数字典。"""
        return self.model_dump(
            exclude={"name"},
            exclude_none=True,      # 显式 null 与「没有已知默认值且未设置」的字段不下发
            by_alias=True,          # DrGrandet 等非 snake_case 字段用 MAA 原名
        )
```

`extra="forbid"` 是这次重构最重要的一处收紧：它把"参数写错了但没人告诉我"变成一个立刻可见的 422。

`exclude_none=True` 保留了现有 `Task.__init__` 里 `{k: v for k, v in params.items() if v is not None}` 的行为，但位置更合理：放在导出环节而不是构造环节，模型本身仍能区分"没设置"与"设为 null"。

**M17 起字段带的是真实内核默认值。** `maa_api/domain/task.py` 里 56 个字段写了 `default=`（`enable` 在基类上声明一次、覆盖 9 种任务类型），取值逐字段来自 MAA v6.17.5 的内核源码、官方文档与真内核实测三条独立证据链（逐字段表见 `docs/superpowers/specs/2026-09-28-m17-task-param-defaults.md` §4），不再一律 `default=None`。因为默认值不再是 `None`，`exclude_none=True` 的**语义随之改变**：默认值会**显式出现在下发快照里**，这正是"预填即下发"的服务端对应物，代价是 MAA 以后调整默认值时我们不再自动跟随。显式传 `null` 仍然表示"不下发该参数，由内核决定"，这条三态语义一个字没改。

**但不是所有字段都有默认值。** 四类字段刻意保持 `default is None`，行为与 M17 之前完全一样（界面仍按三态显示、提交时不下发）：语义是"无限"的（`Fight.times`、`Roguelike.starts_count` / `investments_count`）、语义是"空 = 不启用"的（`Fight.stage`、`StartUp.account_name`、`Roguelike.squad` / `roles` / `core_char`、`Infrast.filename` 等）、条件默认值随 `mode` / `theme` 变化的（`Roguelike.use_foldartal`、`check_collapsal_paradigms`、`difficulty`、`expected_collapsal_paradigms`）、服务端校验器禁止"设了但不生效"的（`Recruit.expedite_times`）。

**其中三个默认值是服务端的约定，不是内核默认值。** `Recruit.select` = `[1, 4, 5]`、`Recruit.confirm` = `[2, 3, 4, 5]`、`Infrast.facility` = 七个设施，这三个字段内核**必填但无默认值**，缺任何一个 `AsstAppendTask` 都返回 0；取值沿用旧前端 `createTasks` 的日常预设与 `resource/daily_task_template.json`。真内核实测（`scripts/probe_task_defaults.py`，append 不需要连设备）的结论是：`StartUp` 必须给 `client_type`、`Recruit` 必须同时给 `select` 与 `confirm`、`Infrast` 必须给 `facility`。这三个字段的 `description` 里必须注明"本服务约定"。

### 7.3 三个代表性模型

`StartUpInput` 展示最简形态与渠道字段：

```python
class StartUpInput(TaskInputBase):
    name: Literal["StartUp"] = "StartUp"

    client_type: CLIENT_TYPES | None = MaaField(
        default=None,   # 渠道字段：模型里保持 None，生效值由端点 overlay 写进 schema（§9）
        label="客户端版本", group="账号",
        description="未指定时套用全局渠道默认值（默认 Bilibili）",
    )
    start_game_enabled: bool | None = MaaField(
        default=False, label="自动启动客户端", group="账号",
    )
    account_name: str | None = MaaField(
        default=None, label="切换账号", group="账号",   # 空 = 内核自动，不设默认值
        description="仅支持切换至已登录账号，按登录名模糊查找，需在已登录账号中唯一",
        examples=["123****4567", "4567", "张三"],
    )
```

`FightInput` 展示取值范围、消耗类参数与别名：

```python
class FightInput(TaskInputBase):
    name: Literal["Fight"] = "Fight"

    stage: str | None = MaaField(
        default=None, label="关卡名",   # 空 = 打当前/上次关卡，不设默认值
        description="留空则识别当前/上次关卡。剿灭作战须填 Annihilation；"
                    "可在结尾加 Normal/Hard 切换标准与磨难难度。不支持运行中设置",
        examples=["1-7", "S3-2", "Annihilation", "CE-6Hard"],
    )
    medicine: int | None = MaaField(default=0, label="最大理智药数", group="资源消耗", ge=0)
    expiring_medicine: int | None = MaaField(
        default=0, label="最大48小时内过期理智药数", group="资源消耗", ge=0,
    )
    stone: int | None = MaaField(default=0, label="最大碎石数", group="资源消耗", ge=0)
    times: int | None = MaaField(default=None, label="指定次数", ge=1)   # 语义是"无限"，不设默认值
    series: int | None = MaaField(
        default=1, label="连战次数", ge=-1, le=6, widget="select",
        enum_labels={"-1": "禁用切换", "0": "自动选择最大可用次数",
                     "1": "1 次", "2": "2 次", "3": "3 次", "4": "4 次", "5": "5 次", "6": "6 次"},
    )
    drops: dict[str, int] | None = MaaField(
        default={}, label="指定掉落数量", group="高级",
        description="key 为 item_id（见 resource/item_index.json），任一达到即停止",
    )
    report_to_penguin: bool | None = MaaField(default=False, label="汇报企鹅数据", group="数据上报")
    penguin_id: str | None = MaaField(default=None, label="企鹅数据 ID", group="数据上报")   # 空 = 不启用，不设默认值
    server: SERVERS | None = MaaField(
        default=None,   # 渠道字段：模型里保持 None，生效值由端点 overlay 写进 schema（§9）
        label="服务器", group="账号",
        description="影响掉落识别与上传，未指定时套用全局默认值（CN）",
    )
    client_type: CLIENT_TYPES | None = MaaField(
        default=None,   # 渠道字段：同上；显式留空则关闭崩溃后重启续刷
        label="客户端版本", group="账号",
        description="用于游戏崩溃后重启并连回继续刷；留空则不启用该功能",
    )
    dr_grandet: bool | None = MaaField(
        default=False, label="节省理智碎石模式", group="资源消耗",
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
        default=0, label="换班工作模式", widget="select",
        enum_labels={"0": "默认换班（单设施最优解）",
                     "10000": "自定义换班（读取用户配置）",
                     "20000": "一键轮换"},
    )
    facility: list[Literal["Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm",
                           "Processing", "Training"]] | None = (
        MaaField(default=["Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"],
                 label="要换班的设施（有序）",
                 description="不支持运行中设置。内核必填且无默认值，"
                             "这里的默认值是本服务的约定（七个设施按标准顺序）；"
                             "M28 起枚举补上加工站与训练室，默认值不变")
    )
    drones: Literal["_NotUse", "Money", "SyntheticJade", "CombatRecord",
                    "PureGold", "OriginStone", "Chip"] | None = MaaField(
        default="_NotUse", label="无人机用途", description="mode=10000 时该字段被忽略",
    )
    threshold: float | None = MaaField(default=0.3, label="工作心情阈值", ge=0.0, le=1.0)
    replenish: bool | None = MaaField(default=False, label="源石碎片自动补货")
    dorm_notstationed_enabled: bool | None = MaaField(default=False, label='启用宿舍"未进驻"选项')
    dorm_trust_enabled: bool | None = MaaField(default=False, label="剩余位置填入信赖未满干员")
    filename: str | None = MaaField(
        default=None, label="自定义配置路径", group="自定义换班",   # 仅 mode=10000 必填，不设默认值
        description="仅 mode=10000 时生效且必填。不支持运行中设置",
    )
    plan_index: int | None = MaaField(
        default=0, label="方案序号", group="自定义换班", ge=0,
        description="仅 mode=10000 时生效且必填。不支持运行中设置",
    )

    @model_validator(mode="after")
    def _check_custom_mode(self):
        if self.mode == 10000 and (self.filename is None or self.plan_index is None):
            raise ValueError("自定义换班模式（mode=10000）必须同时提供 filename 与 plan_index")
        return self
```

三个示例里带 `default=` 的字段都取自 M17 的逐字段默认值表；仍是 `default=None` 的只有两类：刻意不设默认值的字段（`account_name`、`stage`、`times`、`penguin_id`、`filename`）与渠道字段（`client_type` / `server`，模型里保持 `None`，生效值由 §9 的 overlay 写进 schema）。

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

`source` **不在请求体里**，由服务端按调用入口判定：REST 路由 → `manual`，内置 Agent → `agent`，`ScheduleService` → `scheduled`。让客户端自报来源等于让 agent 可以伪装成手动操作抢到最高优先级，优先级体系就失效了。

`priority` 允许显式指定，但只能调低不能调高到超出来源的默认值：agent 不能把自己提到 0。越权时不报错，静默钳制到来源默认值并在审计里记录，因为这是一个 agent 可能因不理解规则而反复触发的边界。

### 7.5 需要在模型里显式编码的约束

`task.py` 的文档字符串是参数的权威来源，但它记录的取值范围、枚举域与跨字段规则目前只存在于注释里。下表是必须落进 Pydantic 声明的部分，其余字段只需正确的类型与可选性。

| 类型 | 需编码的约束 |
|---|---|
| `StartUp` | `client_type` 枚举（6 值） |
| `CloseDown` | `client_type` 枚举；文档标注"必选，填空则不执行"，实际由全局默认值注入保证非空 |
| `Fight` | `series` 取 `[-1, 6]`；`medicine`/`expiring_medicine`/`stone`/`times` 非负；`server` 枚举 4 值；`client_type` 枚举；`drops` 为 `dict[str, int]`；`DrGrandet` 别名 |
| `Recruit` | `select`/`confirm` 为 `list[int]`，元素取 `[1, 6]`；`extra_tags_mode` 取 `{0, 1, 2}`；`recruitment_time` 为 `dict[str, int]`（**键是字符串**，JSON 对象键不能是整数，现有 `dict[int, int]` 的声明在 JSON 层面不成立）；`expedite_times` 仅 `expedite=true` 时有意义；`set_time` 在 `times>0` 的招募与仅计算两条路径都会用到（文档写"仅 `times=0` 时生效"，与源码不符） |
| `Infrast` | `mode` 取 `{0, 10000, 20000}`；`facility` 元素为 9 值枚举（M28 补 `Processing` / `Training`，默认仍是 7 个）；`drones` 为 7 值枚举；`threshold` 取 `[0, 1]`；`mode=10000` 时 `filename` 必填；`plan_index` 模型层仍有默认值 `0`，但**请求体里没给这个键**时执行器读方案文件、按本机时区与各班 `period` 选当前班次（M28 的 Q45），都不覆盖时报 `TASK_PARAM_INVALID` |
| `Mall` | 无数值范围约束；`buy_first`/`blacklist` 为商品中文名字符串列表，不做枚举（商店商品会随活动变化，枚举会过期） |
| `Award` | 全为布尔，无范围约束 |
| `Roguelike` | `theme` 枚举 6 值 `{Phantom, Mizuki, Sami, Sarkaz, JieGarden, BlackFlow}`；`mode` 取 `{0, 1, 2, 3, 4, 5, 6, 7, 10001, 20001, 30001}`，**`2` 已弃用**，传入时 400 `TASK_PARAM_DEPRECATED`，`3` 为开发中同样拒绝，其余模式按主题收口（对齐上游 `RoguelikeConfig.h::is_valid_mode`：通用 0/1/4/6/7，`5` 仅 `Sami`、`10001` 仅 `Sarkaz`、`20001` 仅 `JieGarden`、`30001` 仅 `BlackFlow`，BlackFlow 只接受 0/1/30001）；`difficulty` 取 `≥ -1`（`-1` 表示不指定）；`start_with_elite_two` 仅 `mode=4` 有效（M28 补）；`mode=20001` 必须给 `find_playTime_target`（1–3），反向也要求它只在 `mode=20001` 下出现；`start_foldartal_list` / `first_floor_foldartal` / `use_foldartal` / `check_collapsal_paradigms` 仅 `Sami` 生效（`start_foldartal_list` 还要求 `mode=4`）；`expected_collapsal_paradigms` 要求 `Sami` 且 `mode=5`；`double_check_collapsal_paradigms` 要求 `Sami` 且 `check_collapsal_paradigms=true`（该字段未显式给时按 `mode=5` 判定）；`only_start_with_elite_two` 要求 `mode=4` 且 `start_with_elite_two=true`；`use_nonfriend_support` 要求 `use_support=true`；`refresh_trader_with_dice` 仅 `Mizuki` 生效；`collectible_mode_shopping` / `collectible_mode_squad` / `collectible_mode_start_list` 仅 `mode=4` 生效；`investment_with_more_score` 仅 `mode=1`；`monthly_squad_auto_iterate` / `monthly_squad_check_comms` 仅 `mode=6`；`deep_exploration_auto_iterate` 仅 `mode=7`；`blackflow_strategy` / `blackflow_cultivation_target` 仅 `BlackFlow`；`stop_at_final_boss` 不适用于 `Phantom` |
| `Reclamation` | `theme` 枚举 `{Fire, Tales}`；`mode` 取 `{0, 1}`；`increment_mode` 取 `{0, 1}`；`num_craft_batches` ≥ 1 |

主题相关的跨字段规则一律实现为 `model_validator` 并抛 `TASK_PARAM_INVALID`，而不是静默忽略。原因是"设了但不生效"是用户最常见的困惑来源：`Roguelike` 在 `mode=4` 之外设了 `collectible_mode_start_list`（M28 新增的凹开局参数）却毫无反应，没有报错的话根本无从察觉。

### 7.6 对现有实现的五处修正

这些不是设计偏好，是现有代码里的实际缺陷，新模型必须修正。

**默认值的口径在 M17 改过。** M17 起，凡内核有默认值的字段都在模型里写真实的 `default`，并随 JSON Schema 暴露给前端预填（来源是 MAA v6.17.5 源码、官方文档与真内核实测三条独立证据链，逐字段见 `docs/superpowers/specs/2026-09-28-m17-task-param-defaults.md` §4）；只有四类字段刻意保持 `None`：语义是"无限"的、语义是"空 = 不启用"的、条件默认值随主题/模式变化的、服务端校验器禁止"设了但不生效"的（见 §7.2）。用户 2026-09-28 的裁决与代价见 [13 §8.17](./13-决策记录.md)。下面与默认值有关的两条修正按这个新口径收尾。

**`ReclamationTask` 的 params 字典用变量当键。** 现有代码写的是 `params = {enable: enable, theme: theme, ...}`，键是变量的**值**而不是字符串字面量。实际效果是：全部参数为 `None` 时，字典退化成 `{None: None}` 这一个条目；有值时键变成参数值本身（`{"Fire": "Fire"}`）。这个任务类型目前实际上完全不可用。新模型通过 `model_dump()` 生成参数字典，结构性地消除了这类错误。

**`InfrastTask` 的 `failename` 拼写错误。** 参数名与 params 键都是 `failename`，而文档字符串写的是 `filename`，MaaCore 认的也是 `filename`。自定义换班模式因此从未生效过。新模型统一用 `filename`，不为拼写错误保留兼容别名。

**`AwardTask` 的默认值与文档冲突。** `TaskRequest` 里 `mail`/`recruit`/`orundum`/`mining`/`specialaccess` 的默认值都是 `True`，而 `task.py` 文档字符串明确写"默认为 False"（只有 `award` 默认 True）。文档字符串是对的：内核默认值就是 `award=true`、其余五项 `false`，新模型照此逐个写 `default=`（`award` 为 `True`，五项为 `False`）。M17 之前这一段给出的处置是"这些字段一律 `default=None`，不下发则由内核用自己的默认值，服务端不预设"，这条已被用户 2026-09-28 的裁决取代（见 [13 §8.17](./13-决策记录.md)）。

**`InfrastTask.threshold` 的默认值不一致。** 函数签名写 `threshold: float = 0.5`，文档字符串写"默认 0.3"。**已确认以 0.3 为准**，签名里的 0.5 是错的，新模型写 `default=0.3`。M17 之前这一段给出的处置是"仍不设服务端默认值（`default=None`），把默认值的决定权交还内核"，理由是服务端预设默认值会在内核调整默认行为时产生偏差；这条理由被 2026-09-28 的裁决覆盖：预填即下发，接受"内核改默认值我们不再自动跟随"的代价，0.3 现在既写在 `description` 里，也是请求体的实际默认（见 [13 §8.17](./13-决策记录.md)）。

**`times` 字段在 `TaskRequest` 中重复声明。** 第 12 行与第 24 行各有一个 `times: Optional[int] = None`，Python 类定义里后者覆盖前者。按类型分离后这个问题自然消失：`Fight` 与 `Recruit` 各有独立的 `times`，语义也不同（战斗次数 vs 招募次数），共用一个字段本身就是错的。

## 8. `GET /api/tasks/types` 的 schema 导出

这个端点有两类消费者，需求恰好一致：前端用它动态生成任务配置表单，agent 用它了解某个任务类型有哪些参数可用。共用一份 schema 保证两边永远不会看到不同的参数集。

schema 里的 `default` 有两种来源：绝大多数字段是**静态**的内核默认值（MAA v6.17.5，逐字段依据见 §7.2）；`client_type` 与 `server` 的 `default` 是**动态**的：每次请求用 `load_channel_defaults` 从 `setting` 表读**当前生效**的渠道默认值（默认 `Bilibili` / `CN`）注入响应，因为前端要按生效值预填并显式下发。overlay 只改这两个端点响应里的 schema 副本，pydantic 模型与提交流程的默认语义不变（见 §9）。

### 8.1 导出实现

```python
# maa_api/api/routers/tasks.py
from pydantic import TypeAdapter

_ADAPTERS = {m.model_fields["name"].default: m for m in get_args(get_args(TaskInput)[0])}


def export_type_schema(
    model: type[BaseModel],
    channel_defaults: ChannelDefaults = _BUILTIN_CHANNEL_DEFAULTS,
) -> dict[str, Any]:
    schema = _overlay_channel_defaults(     # client_type / server 的 default 换成当前生效值
        model.model_json_schema(ref_template="#/$defs/{model}"), channel_defaults
    )
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
            "default": 1,
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
  "total": 10,
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

`x-enum-labels` 也挂在**数组元素**的子 schema 上。`Infrast.facility` 的中文设施名（制造站 / 贸易站 / 发电站 / 控制中枢 / 会客室 / 办公室 / 宿舍 / 加工站 / 训练室，M28 起后两项）写在 `properties.facility.items` 里，而不是 `properties.facility` 顶层：前端渲染多选类字段时要下钻一层取这张映射表。

取值范围直接用 JSON Schema 原生关键字承载：`minimum` / `maximum`（来自 `ge` / `le`）、`enum`（来自 `Literal`）、`minItems` / `maxItems`。这样前端可以直接把它喂给 `react-hook-form` 之类的库做客户端校验，不必解析自定义字段。

`description` 取 Pydantic `Field` 的 `description`，内容直接摘自 `task.py` 的文档字符串：那些说明（"剿灭作战必须输入 Annihilation"、"仅在 use_support 为 True 时有效"）是项目里最有价值的领域知识，必须出现在表单的提示文案和 agent 的工具描述里，不能停留在源码注释。

**跨字段规则无法用 JSON Schema 干净地表达**，因此额外给出 `x-depends-on`：

```json
"expedite_times": {
  "type": "integer",
  "x-label": "加急次数",
  "x-depends-on": { "expedite": true }
}
```

前端据此在 `expedite` 未开启时禁用该项；agent 从工具描述里读到同样的约束。服务端的 `model_validator` 仍是最终防线：客户端校验只是体验优化，不是安全边界。

## 9. 全局渠道默认值的注入位置

按决策，渠道默认 **Bilibili（B 服）**，任务未显式指定 `client_type` 时自动套用，允许单任务覆盖。`Fight` 与 `Recruit` 的 `server` 字段同理（默认 `CN`）。

注意区分**模型里的默认值**与**schema 里的默认值**：`client_type` / `server` 在 pydantic 模型里仍是 `default=None`，schema 注入的语义完全不变；`GET /api/tasks/types` 与 `/api/tasks/types/{type_name}` 响应里那个"当前生效"的 `default` 只是给前端预填用的 overlay（见 §8）。

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

调用点在 `services/queue_service.submit()`，三条提交路径（REST、定时任务、Agent）全部经过它。

**为什么不在 Pydantic 的 validator 里做。** 默认值来自 `setting` 表，validator 里取不到异步数据库会话；更根本的是，模型必须能如实表达"用户没设这个字段"，一旦 validator 填了值，`model_fields_set` 就失去了区分能力，`raw_params` 也就记不准原始提交了。

**为什么不在 `core/worker.py` 里做。** 子进程不应该知道任何业务默认值，它的职责只是把参数转成 JSON 投给 `AsstAppendTask`。更要紧的是，落库的 `task.params` 必须与内核实际收到的完全一致，如果注入发生在子进程里，数据库里存的就是一份不完整的记录，排查"为什么跑的是官服"时会查无实据。

**为什么不在路由层做。** 路由层做就得做三遍，定时任务与 Agent 迟早会漏一处。

`model_fields_set` 是这段逻辑的关键，它区分了三种情况：字段未出现在请求 JSON 里（注入默认值）、显式传 `null`（不注入，不下发该参数，由内核决定）、显式传具体值（原样使用）。第二种情况对 `Fight.client_type` 有实际意义：文档明确说该字段留空则关闭"崩溃后重启续刷"功能，用户可能就是想关掉它。

`raw_params` 走 `exclude_unset=True`，记录的正是 `model_fields_set` 里的字段，所以"显式 `null`"与"没提这个字段"在库里长得不一样。M17 之后默认值会进 `params` 快照，`raw_params` 的这条口径没有变：它只记客户端真的提交过什么（前端把默认值物化进提交体后，通常也就一并提交了）。

`ChannelDefaults` 从 `SettingService` 读取（`channel.client_type`、`channel.server`），带进程内缓存并在设置变更时失效。定时任务在**触发时**读取当前默认值而不是创建时快照，**未显式带这两个字段**的流水线/定时任务因此会跟随后续渠道设置变更。

M17 之后这条跟随有了限定：前端会把当时生效的渠道值预填并显式下发，所以**新建**的流水线/定时任务在提交那一刻就冻结了渠道值，之后改渠道设置不会再影响它们：这是"预填即下发"已知且用户确认的代价（见 [13 §8.17](./13-决策记录.md)）。M17 之前创建、请求体里没有这两个字段的旧流水线不受影响，仍然跟随。

## 10. 幂等性与并发

### 10.1 需要幂等保护的端点

| 端点 | 保护方式 |
|---|---|
| `POST /api/pipelines` | 可选 `Idempotency-Key` 头，落 `pipeline.idempotency_key` 唯一列 |
| `POST /api/pipelines/{id}/retry` | 同上 |
| `POST /api/agent/tools/{name}/invoke` | 可选 `Idempotency-Key`，命中则返回首次的审计结果 |
| `POST /api/updates/{target}` | 不用 header，靠 `update_record` 的部分唯一索引（同 target 只允许一条 `running`） |
| `POST /api/schedules/{id}/run` | 该 schedule 已有 `PENDING`/`RUNNING` 实例且 `skip_if_running` 为真时返回 409 `PIPELINE_ALREADY_RUNNING` |
| `POST /api/confirmations/{id}` | 状态机保证，重复处理返回 `409 CONFIRMATION_ALREADY_RESOLVED` |
| `POST /api/core/restart` | 已处于 `RESTARTING` 时返回 409 |
| `POST /api/resources/reload` | `checksum` 未变化时返回 202 但不实际重载 |

`Idempotency-Key` 的处理规则：客户端生成（推荐 UUID，长度 1–64 字符），服务端在 24 小时窗口内有效。命中已有记录时，**比对请求体哈希**：一致则返回首次响应（Agent 调用关联首次 `audit_id`），不一致则返回 `409 IDEMPOTENCY_KEY_CONFLICT`。流水线 key 落在 pipeline 唯一列；Agent key 与首次响应快照落 `agent_idempotency`，并以 `(caller, key)` 唯一约束兜底并发写入。后一种情况通常意味着客户端的 key 生成有 bug，静默接受会掩盖问题。

为什么流水线提交需要这道保护：移动端是首要目标设备，弱网下用户点"开始"没有立即反馈会重复点击；PWA 的离线重放也可能重发同一请求。没有幂等保护的后果是队列里出现两条一模一样的日常任务，第二条跑起来会在已经做完的关卡上浪费理智。

### 10.2 天然幂等的端点

所有 `GET`、`PUT /api/settings`（全量覆盖语义）、`PUT /api/schedules/{id}`、`PUT /api/notifications/channels/{id}`、全部 `DELETE`（重复删除返回 204 而非 404：目标状态已达成，报错没有意义）。

`PATCH /api/queue/{pipeline_id}` 也是幂等的，但受 `QUEUE_ITEM_NOT_PENDING` 约束：条目一旦开跑，优先级就不再可改。

### 10.3 并发写入的收敛

**唯一写者原则。** 流水线与任务的状态只由 `PipelineRunner` 写入，REST 端点只做两件事：读状态、投递意图。`DELETE /api/pipelines/{id}` 不直接把状态改成 `CANCELLED`，而是置一个取消标记并向内核投 `STOP`，由 `PipelineRunner` 观察到后统一收尾。这样避免了"REST 刚写完 CANCELLED，Runner 又写回 COMPLETED"的竞态。

**状态流转靠条件更新兜底。** 所有终态流转写成 `UPDATE ... WHERE id = ? AND status = ?`，用影响行数判断是否被接受，返回 0 行则转成 409。细节见 [04-数据模型与持久化](./04-数据模型与持久化.md) 的仓储层章节。

**原子操作与流水线的冲突。** 流水线运行中调用 `POST /api/device/*` 或 `/api/core/back_to_home` 返回 `409 PIPELINE_ALREADY_RUNNING`；M10 手动 REST 请求带 `force=true` 则执行，并写强制介入警告日志，但该 REST 路径不创建 `agent_audit` 记录。Agent 工具调用的审计按 M11 Agent 工作流契约处理。Agent 的"卡死救援"场景应遵循的序列是：先 `DELETE /api/pipelines/{id}` 停止流水线，再做原子操作。这条序列写进工具描述里。

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
    {"name": "snippets", "description": "API 调试台收藏请求"},
    {"name": "agent", "description": "工具清单、会话与审计"},
    {"name": "confirmations", "description": "高风险操作的人工确认"},
    {"name": "ws", "description": "WebSocket 实时通道（仅文档说明，不可在此调试）"},
]
```

`ws` 无法在 Swagger UI 中调试，但仍在文档里保留占位条目说明协议与鉴权方式，否则读文档的人会以为服务没有实时通道。

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

旧端点**不保留任何存根**：`maa_api/main.py` 自 M3 起一律不注册 `router/adb`、`router/maa`、`router/template`，`tests/api/test_app_skeleton.py` 还断言这些路径必须不存在。所以访问旧端点拿到的是**统一错误体的 404**（`/api` 前缀）或 SPA catch-all 的 `index.html`（非 `/api` 前缀），不是 410。下表只是**迁移对照**：告诉从旧版本升级的调用方，每个旧能力搬到了哪里。

> 早期设计曾打算用 410 `ENDPOINT_REMOVED` 存根区分「接口没了」与「路径拼错了」，实现时否掉了：留一批只返回错误的路由会让路由表与鉴权面长期背着一层历史包袱，而旧前端已随 `static/` 一起废弃，没有需要过渡的调用方。`ENDPOINT_REMOVED` 因此是一个没有任何路径能抛出的错误码。

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

三处语义变化需要调用方注意。`GET /api/maa/pipeline` 原本返回内存里唯一的那条流水线（含全部日志与 base64 截图），新的 `GET /api/pipelines/current` 只返回流水线与任务状态，日志与截图各有独立端点：把三类数据塞进一个响应会让这个高频轮询接口传输几百 KB。

`DELETE /api/maa/pipeline` 不带 id，因为只可能有一条；新接口必须带 id，因为队列里可能有多条。想取消"当前那条"需要先读 `GET /api/pipelines/current` 拿 id。

旧的 daily 接口读写的是 `resource/daily_task.json` 整个文件，新接口是标准 CRUD 资源。一次性迁移由数据迁移脚本完成，见 [04-数据模型与持久化](./04-数据模型与持久化.md) 的迁移章节。
