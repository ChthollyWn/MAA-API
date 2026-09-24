> 本文档定义自研 API 调试台的功能与布局、OpenAPI 文档的质量规范、前端类型的生成流程，以及对外开放 API 的文档化产物。决策依据见 [README 决策速查表](./README.md#决策速查表)，接入层的模块位置见 [02-系统架构设计](./02-系统架构设计.md)。

# 开放 API 与调试台

## 1. 为什么要自研调试台

决策是「自研调试面板集成进新前端，同时保留 FastAPI 原生 `/docs` 作为备用」。这不是对 Swagger UI 的不满，而是 Swagger UI 与本项目的使用场景有几处结构性不匹配。

**移动端不可用。** 前端是移动优先的（[09-前端重构方案](./09-前端重构方案.md)），而 Swagger UI 在手机上基本没法操作 —— 请求体的 textarea 在小屏上只有几行高、响应区要横向滚动、展开一个接口需要多次点击折叠面板。本项目的主要使用姿势是「躺在床上看挂机状态、顺手调一下接口」，Swagger UI 撑不住这个场景。

**每次都要重新 Authorize。** 鉴权是单 `access_token`，Swagger UI 的 Authorize 弹窗状态不跨标签页、刷新后经常需要重填。自研调试台直接复用前端已有的登录态，token 由同一个请求拦截器注入，一次登录之后所有调试请求自动带上（见 [09 §10.2](./09-前端重构方案.md#102-请求拦截器注入)），完全没有 Authorize 这一步。

**请求历史不持久。** Swagger UI 每次刷新就把填过的参数全丢了。而调试 MAA-API 的典型动作是「改一个参数、重发、看日志、再改」，反复十几次。历史持久化把这个循环从「每次重新填整个 body」缩短到「点历史 → 改一个字段 → 重发」。

**看不到调用效果。** 这是最关键的一点。MAA-API 的大部分接口是**异步且有副作用**的：`POST /api/pipelines` 返回 `202` 和一个 `pipeline_id` 就结束了，真正的执行过程全在 MaaCore 子进程里，表现为源源不断的回调日志与状态变更。Swagger UI 只能给你看那个 `202`，而你真正想知道的是「内核收到任务了吗、卡在哪一步了、报了什么错」。自研调试台能和实时日志同屏联动（见 [§3](#3-与实时日志的联动)），这是外挂工具做不到的。

**调试结果可以直接沉淀。** 调好一个请求之后，下一步往往是「让它每天自动跑一次」或者「存下来以后还用」。调试台内建「存为定时任务」与「加入收藏」两个出口，把一次性调试变成可复用的配置。Swagger UI 里调好的请求除了复制 cURL 什么都留不下。

**风格统一。** 深色模式、安全区适配、底部 Tab 导航、中文文案 —— 调试台作为「更多」下的一个页面，和其余页面用同一套组件与主题。切到 Swagger UI 会有明显的断裂感，尤其在手机上。

保留原生 `/docs` 的价值在别处：它是 OpenAPI 契约的独立验证器。如果自研调试台显示某个接口不对，可以用 `/docs` 交叉验证问题在契约还是在调试台。它也是给不熟悉本项目的外部集成方看的标准入口。

## 2. 调试台的功能设计

页面位置 `/more/api-console`，实现代码在 `web/src/features/api-console/`。

### 2.1 接口树

从 `GET /openapi.json` 动态加载，不在前端硬编码任何接口清单。这一点是设计上的硬约束：后端加一个接口，调试台自动出现，不需要任何前端改动。

加载后的处理：按 `tags` 分组（tag 的显示名与描述从 OpenAPI 的 `tags` 顶层数组取，见 [§5.1](#51-tag-metadata)），组内按 path 字典序排。每一项显示 HTTP 方法徽标（GET 蓝 / POST 绿 / PUT 琥珀 / DELETE 红 / PATCH 紫）、path、以及 `summary`。

顶部一个搜索框，同时匹配 path、`summary`、`operationId`，模糊匹配即可（`Fuse.js` 之类不必引入，简单的 `includes` 加小写化足够 —— 接口总数在百量级）。

契约用 `useQuery` 缓存，`staleTime` 设 5 分钟。开发期后端 `--reload` 会频繁改契约，提供一个手动刷新按钮。

被 `include_in_schema=False` 排除的内部路由不会出现在树里，这是期望行为。

### 2.2 请求构造器

选中一个接口后，从 OpenAPI 的 operation 对象派生出四块输入区：

**路径参数。** 从 `parameters` 里 `in: path` 的项生成，全部必填。控件按 schema 类型选（复用 [09 §7.3](./09-前端重构方案.md#73-schema-到控件的映射规则) 的映射规则，不要为调试台写第二套）。`core_id` 这类有稳定默认值的参数预填 `default`。

**Query 参数。** 从 `in: query` 的项生成。每行带一个启用开关 —— 可选参数默认不启用，不发送（而不是发一个空值）。这个区分很重要：`?stage=` 与不带 `stage` 在后端是两种语义。

**Header。** 默认折叠。`Authorization` 由拦截器注入，这里显示为一行只读的 `Bearer ****`（脱敏，带一个「显示」切换）表明它已经带上了，但不允许在此编辑 —— 要换 token 去设置页。允许添加自定义 header，用于测试 `X-Token` 渠道或模拟异常请求。`X-Request-Id` 自动生成并显示，是日志联动的关键（见 [§3](#3-与实时日志的联动)）。

**Body。** 用 CodeMirror 6（`@uiw/react-codemirror` + `@codemirror/lang-json`）做 JSON 编辑器，不用裸 textarea。需要的能力有三项：

- **按 schema 预填骨架。** 打开一个 `POST` 接口时，从 `requestBody` 的 JSON Schema 递归生成一份合法的 JSON 示例 body（必填字段填 `example` 或类型默认值）。可选字段说明显示在编辑器外的字段说明区，不能用 JSON 注释混入 body。这一步让「提交一条流水线」从「翻文档拼 JSON」变成「改几个值」。
- **补全与校验。** 用 `@codemirror/autocomplete` 基于 schema 的 `properties` 提供键名补全；用 `ajv` 或复用前端已有的 Zod 转换（`lib/schema-to-zod.ts`）做实时校验，错误以下划线标出并在编辑器下方列出。发送前若校验不通过，给警告但**不阻止发送** —— 调试台的一个正当用途就是故意发非法请求看后端的错误处理。
- **格式化与折叠。** 格式化快捷键、大对象折叠。MAA 的流水线 body 嵌套三层（pipeline → tasks[] → params{}），没有折叠时在手机上根本看不清结构。

### 2.3 响应展示

发送后展示五部分：

**状态码**，带语义配色（2xx 成功 / 3xx 中性 / 4xx 警告 / 5xx 危险）与标准描述文案（`202 Accepted`）。

**耗时**，客户端测量的往返时间（`performance.now()` 前后差）。若后端返回了 `X-Response-Time-Ms` 头，同时显示服务端处理耗时 —— 两者的差值能区分「后端慢」和「网络慢」。

**响应头**，默认折叠。

**格式化 body**，JSON 用同一个 CodeMirror 实例只读渲染并默认展开两层。图片响应（截图接口）直接渲染缩略图而不是显示二进制。

**错误码高亮。** 后端的错误体格式是固定的：

```json
{
  "error": {
    "code": "ADB_CONNECT_FAILED",
    "message": "无法连接到 127.0.0.1:5555",
    "details": { "adb_path": "/usr/bin/adb", "retries": 3 }
  }
}
```

调试台检测到这个结构时，不只是把 JSON 印出来，而是渲染成一张错误卡：`code` 作为醒目徽标并链接到错误码表（见 [05-API规范与路由清单](./05-API规范与路由清单.md)）、`message` 作为标题、`details` 作为可展开的键值表。这让「看到 `ADB_CONNECT_FAILED` → 知道该去哪」变成一次点击。

### 2.4 请求历史

**结论：历史存 localStorage，收藏落库。** 两者的定位不同，不该用同一种存储。

历史是**本机的、临时的、高频写入的**。一次调试会话可能产生几十条记录，其中绝大多数十分钟后就没有价值。落库意味着每次发请求都多一次写请求，还要为一堆垃圾数据设计清理策略。放 localStorage 零成本、零网络往返、天然按设备隔离（手机上的调试历史不会混进电脑的）。

具体参数：环形缓冲上限 100 条，超出丢弃最早的；单条记录裁剪到 16KB（超长的 body 与 response 截断并标注「已截断」）；key 为 `maa.api-console.history`。这两个限制是为了避开 localStorage 的 5MB 配额 —— 截图接口的响应如果不截断，几条就能把配额吃光，而 localStorage 写满时抛的是 `QuotaExceededError`，会让整个页面的持久化逻辑连带失效。

历史记录的字段：`method`、`path`、已过滤凭据后的路径/query/header/body 参数、状态码、耗时、时间戳、`X-Request-Id`。历史同样不得保存 `Authorization`、`X-Token`、`Cookie` 或 query 的 `token`。列表按时间倒序，每条显示「方法 + path + 状态码 + 相对时间」，点击回填到构造器。

### 2.5 收藏与命名

收藏是**跨设备的、长期的、有意命名的**，所以落库。表 `api_snippet`（归入 [04-数据模型与持久化](./04-数据模型与持久化.md)）：

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `name` | 用户起的名字，如「提交周三剿灭」 |
| `method` / `path` | 目标接口 |
| `path_params` / `query` / `headers` / `body` | JSON 存储，**不含 token** |
| `created_at` / `updated_at` | |

三个必须注意的点：

**不存 token。** 收藏里绝不能包含 `Authorization` 或任何 header 里的凭据。保存时过滤掉鉴权相关 header，发送时由拦截器重新注入。否则换了 token 之后所有收藏失效，更糟的是 token 会以明文躺在数据库里。

**agent 读取收藏留到 M11。** 收藏落库为后续「按上次那套配置再跑一次」提供基础，但 M10 不暴露 agent 读取能力；待 M11 的 `ToolRegistry` 与策略边界就位后再接入。

**「存为定时任务」是收藏的一个特化出口。** 对 `POST /api/pipelines` 这类接口，收藏面板上额外提供「转为定时任务」按钮，跳到 `/more/schedules/new` 并把 body 预填进去。这是调试台价值的最直接体现：调通的请求一键变成长期运行的配置。

### 2.6 cURL 导出

一键复制等价的 `curl` 命令。用于把问题贴给别人、或在没有浏览器的机器上复现。

导出时 token 默认替换为 `$MAA_TOKEN` 占位符而非明文，并在命令前加一行注释提示先 `export MAA_TOKEN=...`。提供一个「包含真实 token」的开关给自用场景，但默认关闭 —— 因为 cURL 命令最常见的用途就是贴给别人看。

### 2.7 环境变量

两个变量：

**Base URL。** 默认空（同源相对路径）。允许填入其他地址，用于从一台机器的调试台去打另一台机器的 API（比如在电脑上调试树莓派上跑的实例）。专用 WebSocket 也从该 URL 推导为对应的 `/api/ws`。跨域调用受现有 loopback/RFC1918 CORS policy 约束；任意 Tailscale FQDN 的可配置白名单留到 M15。

**Token。** 默认复用登录态的 token。允许临时覆盖，用于验证「错误的 token 是否正确返回 401」这类测试。覆盖只在当前会话内有效，不写 localStorage，避免把测试用的坏 token 持久化进去。

两个变量都显示在调试台顶部一行，非默认值时配色高亮 —— 避免「为什么请求都 401」结果是忘了还挂着一个测试 token 这种浪费时间的情况。

## 3. 与实时日志的联动

这是自研调试台相对 Swagger UI 的核心增量：发起请求后，在同一屏看到这次调用在服务端触发了什么。

### 3.1 实现方式

**请求侧打标。** 前端的请求拦截器已经为每个请求生成 `X-Request-Id`（见 [09 §10.2](./09-前端重构方案.md#102-请求拦截器注入)）。调试台记下这次发送用的 id。

**后端侧透传。** 一个中间件读取 `X-Request-Id`（缺失则自己生成），存入 `contextvars.ContextVar`，并在响应头回显同一个值。所有在这个请求上下文里产生的日志，由 `LogHub` 自动附上 `request_id` 字段：

```python
# maa_api/api/middleware.py（概念示意）
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

@app.middleware("http")
async def trace_request(request: Request, call_next):
    rid = request.headers.get("X-Request-Id") or str(uuid4())
    token = request_id_var.set(rid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-Id"] = rid
    response.headers["X-Response-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response
```

`LogHub` 的 handler 在 HTTP 请求日志中读 `request_id_var.get()`。因为用的是 `ContextVar` 而非 `threading.local`，asyncio 的任务切换不会串上下文；请求结束后不把该值传播给异步任务。

**广播与筛选。** `LogHub` 把带 `request_id` 的请求日志正常广播到 WebSocket（[06-实时日志与WebSocket](./06-实时日志与WebSocket.md) 的既有通道，不新增协议）。调试台订阅日志事件，在本地按刚才那个 `request_id` 过滤，渲染成响应区下方的「服务端日志」列表。

### 3.2 跨越请求边界的追踪

`contextvars` 只能覆盖 HTTP 请求的生命周期。而 MAA-API 的重点恰恰在请求返回**之后** —— `POST /api/pipelines` 返回 `202` 时，流水线还没开始跑。

所以联动分两段：

**第一段是请求内日志**，按 `request_id` 过滤，回答「后端收到我的请求后做了什么、参数校验过了吗、落库成功了吗」。

**第二段是关联流水线的后续日志。** 调试台只在响应体带 `pipeline_id` 时按该字段过滤日志，并在界面上分成两个折叠区：「本次请求」和「流水线 #abc123 的执行日志（进行中）」。第二段持续流入直到流水线到达终态。`confirmation_id` 与 `update_id` 不建立自动日志关联。

这就要求 `LogHub` 在广播流水线相关日志时带上 `pipeline_id` 字段 —— 这本来就是日志页按流水线筛选所需的，不是为调试台额外加的。

### 3.3 状态变化的呈现

除日志外，调试台还订阅 `core_status` / `device_status` / `pipeline_status` 频道（[09 §9.5](./09-前端重构方案.md#95-与-tanstack-query-缓存的协作) 的同一批事件），在响应区上方显示一条紧凑的状态时间线：`内核 ready → 设备 connected → 流水线 running(2/6)`。这让「我调了 `POST /api/device/reconnect`，它到底连上了没有」在一屏内有答案，而不需要切到首页看状态卡。

## 4. 移动端的调试台布局

三块内容（接口树、请求构造、响应）在窄屏上不能并列，也不该塞进一个可滚动的长页面 —— 那样每次发送都要滚很远才能看到响应。

### 4.1 移动端：分步 + 抽屉

**第一屏是接口树。** 全屏的搜索框 + 分组折叠列表。这是调试台的入口页。

**选中接口后全屏推入「请求」页**（React Router 的路由跳转，不是抽屉）。用全屏页而不是抽屉的原因：body 编辑需要尽可能多的垂直空间，而软键盘弹出后抽屉的可用高度只剩一两百像素，没法编辑 JSON。页面顶部是返回按钮 + 方法徽标 + path，底部固定一个「发送」按钮（带 `pb-safe`）。

**响应从底部推起为抽屉。** 发送后自动弹出，初始高度约 `60dvh`，可上拉到全屏、下拉收回到只剩一条状态条（显示状态码与耗时）。收回状态下仍能看到结果是否成功，又能继续改请求重发。这个「可折叠但不消失」的形态正是 `vaul` 的 snap points 能力适合的场景。

**「请求 / 响应 / 日志」三态用顶部 segmented control 切换**，发送后自动切到「响应」。日志页签在有服务端日志流入时显示未读数徽标。

面包屑保留在顶部（`接口树 / 流水线 / POST /api/pipelines`），点击可快速回到树。

### 4.2 桌面：三栏

`md` 断点以上切换为经典三栏：左侧接口树（固定 280px，可折叠）、中间请求构造器（自适应）、右侧响应与日志（固定 40%，上下分割）。同一批组件，只换外层布局容器，不写两套业务逻辑。

这个响应式切换用 [09 §7.3](./09-前端重构方案.md#73-schema-到控件的映射规则) 提到的 `ResponsiveSheet` 思路的同一个 `useMediaQuery` hook 驱动，保持整站一致。

### 4.3 M10 验收边界

M10 的移动端验收以浏览器模拟视口覆盖窄屏布局、编辑器、底部发送按钮与响应抽屉为准；真机触控、安全区与软键盘检查作为补充记录。经 Tailscale 的实际访问与任意 Tailscale FQDN 的 CORS 配置属于 M15 验收，不能据此宣称 M10 已完成。

## 5. OpenAPI 文档的质量要求

契约要同时给三类消费者用：人（调试台与 `/docs`）、代码生成器（`openapi-typescript`）、agent（MCP tool 描述可以直接引用接口描述）。任何一处缺失都会在下游放大。

现有代码在这方面基本是空白 —— 路由上没有 `summary`、没有 `response_model`、没有 `responses`，错误体是自定义的 `{code: 10200}` 结构而非标准状态码。以下是重构时的强制规范。

### 5.1 tag metadata

在 `FastAPI()` 构造时声明全部 tag 及其描述，让接口树的分组标题有意义：

```python
OPENAPI_TAGS = [
    {"name": "system",        "description": "服务健康、版本、配置"},
    {"name": "core",          "description": "MaaCore 子进程的生命周期与原子操作"},
    {"name": "device",        "description": "ADB 设备连接、截图、输入"},
    {"name": "pipelines",     "description": "流水线的提交、查询、停止与历史"},
    {"name": "tasks",         "description": "任务类型定义与参数 schema"},
    {"name": "logs",          "description": "三路日志的历史查询"},
    {"name": "schedules",     "description": "定时任务"},
    {"name": "updates",       "description": "内核 / 活动资源 / 游戏本体的热更新"},
    {"name": "agent",         "description": "内置 agent 会话与工具调用"},
    {"name": "confirmations", "description": "高风险操作的人工确认"},
    {"name": "audit",         "description": "调用审计"},
    {"name": "notify",        "description": "通知通道与推送订阅"},
]

app = FastAPI(
    title="MAA-API",
    version=__version__,
    summary="MaaAssistantArknights 内核的 HTTP / WebSocket / MCP 封装",
    description=API_DESCRIPTION,     # 一段 Markdown，见 §8
    openapi_tags=OPENAPI_TAGS,
    lifespan=lifespan,
)
```

`openapi_tags` 的数组顺序决定 `/docs` 里分组的显示顺序，按「先常用、后运维」排。tag 名用小写单数或复数保持一致（此处统一用资源复数，`system` / `core` 除外因为它们不是集合）。

### 5.2 路由级规范

每个路由必须有：`summary`（一行，中文，动宾结构）、docstring 作为 `description`（可多行 Markdown）、`response_model`、显式 `status_code`、以及 `responses` 声明的错误响应。

```python
@router.post(
    "/pipelines",
    tags=["pipelines"],
    status_code=202,
    response_model=PipelineAccepted,
    summary="提交流水线",
    responses={**COMMON_ERRORS, 409: CONFLICT_PIPELINE_RUNNING},
)
async def submit_pipeline(payload: PipelineCreate) -> PipelineAccepted:
    """
    把一组任务作为流水线提交到队列，**异步返回**。

    请求立即返回 `202` 与 `pipeline_id`，实际执行由 `PipelineRunner` 按优先级调度。
    要等待执行结果，订阅 WebSocket 的 `pipeline_status` 频道，
    或轮询 `GET /api/pipelines/{pipeline_id}`。

    未显式指定 `client_type` 的任务会自动套用全局账号渠道默认值。
    """
```

`summary` 是调试台接口树里每一项显示的文字，也是生成的 TS 类型里的 JSDoc 首行。一行说不清的放 docstring。

`responses` 不要逐个路由手写全套。定义一组共享常量，路由用字典展开合并：

```python
# maa_api/api/errors.py
class ErrorDetail(BaseModel):
    code: str = Field(description="可枚举的错误码", examples=["ADB_CONNECT_FAILED"])
    message: str = Field(description="面向人的错误描述")
    details: dict[str, Any] | None = Field(default=None, description="结构化上下文")

class ErrorResponse(BaseModel):
    error: ErrorDetail

COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "token 缺失或无效"},
    422: {"model": ErrorResponse, "description": "请求参数校验失败"},
    500: {"model": ErrorResponse, "description": "服务内部错误"},
}

CONFLICT_PIPELINE_RUNNING = {
    "model": ErrorResponse,
    "description": "已有流水线在运行，需先停止或传 force=true",
    "content": {"application/json": {"example": {
        "error": {"code": "PIPELINE_ALREADY_RUNNING",
                  "message": "流水线 3f2a 正在执行",
                  "details": {"pipeline_id": "3f2a", "started_at": "2026-09-16T14:02:11Z"}},
    }}},
}
```

统一的 `ErrorResponse` 模型让 `openapi-typescript` 生成出一个共享的错误类型，前端的 `api/errors.ts` 可以对它做穷尽式 `switch`。逐路由手写内联 schema 会生成十几个结构相同但名字不同的类型，前端无法统一处理。

错误响应带 `example` 比只带 `model` 有用得多 —— 调试台与 `/docs` 会把示例直接展示出来，接入方不必自己猜 `details` 里有什么。

### 5.3 字段级描述与示例

Pydantic 模型的每个字段带 `description` 与 `examples`：

```python
class FightParams(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"stage": "1-7", "medicine": 2, "series": 0},
        {"stage": "Annihilation", "times": 1},
    ]})

    stage: str | None = Field(
        default=None,
        description="关卡名。留空则识别当前/上次关卡。剿灭作战填 `Annihilation`。不支持运行中设置。",
        examples=["1-7", "CE-6", "S3-2Hard", "Annihilation"],
    )
    series: int | None = Field(
        default=None, ge=-1, le=6,
        description="连战次数。`-1` 禁用切换，`0` 自动取当前可用最大次数，`1`~`6` 指定次数。",
    )
```

这些 `description` 不是写给 OpenAPI 看的额外负担，而是**同一份文案的唯一来源**：它同时驱动前端表单控件的帮助文字（[09 §7.2](./09-前端重构方案.md#72-schema-契约) 的 `help` 字段）、`/docs` 的字段说明、agent tool 的参数描述。现在这些文案散落在 `model/core/task.py` 的 Python docstring 里（格式不可解析）和 `static/index.html` 的 HTML label 里（前端专有），必须收敛到 Pydantic 字段上。

`ge` / `le` 这类约束要写在 `Field` 上而不是在业务代码里手工校验 —— 它们会进 JSON Schema 的 `minimum` / `maximum`，前端据此选择滑块还是数字输入框。

### 5.4 operationId 的稳定命名

FastAPI 默认的 `operationId` 形如 `submit_pipeline_api_pipelines_post`，把函数名和路径糊在一起。`openapi-typescript` 不直接用它，但 `/docs` 的锚点、其他语言的 SDK 生成器、以及 agent 引用某个接口时都会用到，应该显式控制：

```python
def custom_operation_id(route: APIRoute) -> str:
    # 结果形如 pipelines_submit_pipeline
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}_{route.name}"

app = FastAPI(generate_unique_id_function=custom_operation_id, ...)
```

约定一旦定下就**不要再改** —— `operationId` 变更对生成的 SDK 是破坏性的。

### 5.5 `openapi_extra` 的用途

`openapi_extra` 用于 FastAPI 的参数表达不了的东西。两个实际用例：

**代码示例。** 对「提交流水线」「查询状态」这类核心接口，用 `x-codeSamples` 挂上 cURL 与 Python 片段，`/docs` 与 ReDoc 都会渲染它们：

```python
@router.post(
    "/pipelines",
    openapi_extra={"x-codeSamples": [
        {"lang": "cURL", "label": "提交日常流水线", "source": CURL_SUBMIT_PIPELINE},
        {"lang": "Python", "label": "httpx", "source": PY_SUBMIT_PIPELINE},
    ]},
)
```

**幂等性与副作用标注。** 给会消耗游戏内资源的接口打一个自定义扩展 `x-maa-risk: consumable`，给需要人工确认的打 `x-maa-requires-confirmation: true`。调试台读到这些标记时在发送按钮旁加警示，agent 侧的 `PolicyEngine` 读同一份标记做风险判定 —— 一处声明，两处消费。

### 5.6 OpenAPI 覆盖不到的两块

必须明确记录下来，否则「看 `/openapi.json` 就够了」会是错的：

**WebSocket 端点不在 OpenAPI 里。** OpenAPI 3.x 规范不描述 WebSocket，FastAPI 也不会把 `@app.websocket()` 的路由放进 schema。`/api/ws` 的握手方式、鉴权渠道、订阅消息、事件类型全部要手写文档，权威处是 [06-实时日志与WebSocket](./06-实时日志与WebSocket.md)，接入说明里要摘录关键部分。可以在 `app.description` 里加一段指向它，让只看 `/docs` 的人不至于以为没有实时接口。

**MCP 端点不在 OpenAPI 里。** `/mcp` 是 `app.mount()` 挂上的 ASGI 子应用，不产生 path operation。它的契约是 MCP 协议自身的 tool 列表（通过 MCP 的 `tools/list` 发现），权威处是 [11-Agent模块设计](./11-Agent模块设计.md)。

## 6. 前端 TypeScript 类型的生成流程

### 6.1 工具与命令

用 `openapi-typescript@7`（当前 7.13.0）从契约生成类型，配 `openapi-fetch` 做类型安全的调用。不用 `@hey-api/openapi-ts` 之类会生成完整 SDK 的工具 —— 生成的代码量大、有自己的运行时、和 TanStack Query 的集成方式是另一套约定。`openapi-typescript` 只产出**纯类型声明**（零运行时），`openapi-fetch` 是几 KB 的 `fetch` 薄封装，两者组合起来既有端到端类型安全，又不引入额外的架构约束。

```jsonc
// web/package.json
{
  "scripts": {
    "gen:api": "openapi-typescript ./openapi.json -o ./src/types/api.d.ts",
    "gen:api:check": "openapi-typescript ./openapi.json -o node_modules/.tmp/api.d.ts && diff -q node_modules/.tmp/api.d.ts ./src/types/api.d.ts"
  }
}
```

`gen:api:check` 是自己拼的（`openapi-typescript` 没有内置的 check 模式）：重新生成到临时路径再 `diff`，不一致就非零退出。它的用途是 CI 校验（见 [§6.5](#65-后端改接口后的同步流程)）。

7.13.0 新增的 `--read-write-markers` 能把 `readOnly` / `writeOnly` 字段区分开，配合 `openapi-fetch` 的 `Readable` / `Writable` 辅助类型，让「响应里有 `id`、请求体里没有」这种常见形态在类型上也成立。本项目的模型有不少这类字段（`id`、`created_at`），建议开启。

### 6.2 契约从哪里来

**不从运行中的服务拉，从入库的快照文件读。**

后端提供一个不需要启动服务的导出脚本：

```python
# scripts/dump_openapi.py
import json, sys
from pathlib import Path
from maa_api.main import app

out = Path(sys.argv[1] if len(sys.argv) > 1 else "web/openapi.json")
out.write_text(
    json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
```

用 `poetry run python scripts/dump_openapi.py` 生成 `web/openapi.json`。

三个细节都是为了让 diff 可读：`ensure_ascii=False` 保留中文（否则满屏 `\uXXXX`）、`sort_keys=True` 让键序稳定（Python 字典序在不同版本间可能变，不排序会产生大量伪 diff）、结尾换行符避免「文件末尾无换行」的噪声。

选快照而非 `http://127.0.0.1:8002/openapi.json` 的理由：前端构建不依赖后端运行（CI 里不需要起 uvicorn、装 ADB、准备 MaaCore）；契约变更以文本 diff 的形式出现在 PR 里，评审时能直接看到「哪个接口加了什么字段」；`sort_keys` 之后的 diff 是确定性的，不会因为请求时机不同而变化。

导出脚本 import 了 `maa_api.main`，这要求模块 import 阶段不做任何重活。现状不满足 —— `model/core/scheduler.py` 在 import 时就执行 `load_asst()`（含版本校验、下载、解压、ADB 连接）。重构后按 [02 §7](./02-系统架构设计.md) 的规定，所有初始化都进 `lifespan`，import 只做定义，导出脚本才能跑得通。这算是「契约可导出」对后端架构提出的一个附带要求。

### 6.3 何时生成

**手动触发，不在构建前自动跑。**

```bash
# 后端改完接口后，一条命令更新两个产物
poetry run python scripts/dump_openapi.py && (cd web && pnpm gen:api)
```

不做「build 前自动生成」的理由是它会掩盖问题。如果 `pnpm build` 总是先重新生成类型，那么「后端改了接口、前端代码还没跟上」这件事就永远不会让构建失败 —— 类型被悄悄更新成新契约，而使用旧字段的前端代码才开始报错，报错位置离真正的变更点很远。手动生成 + CI 里的 `gen:api:check` 能让这件事在正确的时机、以正确的形式暴露：**契约变了但类型没重新生成** 是一个明确的 CI 失败。

开发期如果嫌两条命令麻烦，可以在后端的 `--reload` 之外另起一个 watch 脚本盯 `maa_api/api/` 目录变化后自动重新导出，但那是可选的便利，不是流程的一部分。

### 6.4 产物是否入版本库

**`openapi.json` 与 `src/types/api.d.ts` 都入库。**

理由有三条。**离线可构建**：克隆仓库后 `pnpm install && pnpm build` 就能出产物，不需要先把后端跑起来。**变更可审**：接口的增删改以 diff 形式出现在 PR 里，是评审「这个破坏性变更前端跟上了吗」的唯一凭据。**CI 简单**：前端流水线不需要 Python 环境。

这与 `static/`（构建产物，不入库，见 [09 §14.3](./09-前端重构方案.md#143-static-加入-gitignore)）的处置相反，因为两者性质不同：`static/` 是**终端产物**，不参与任何后续推理，且体积大、每次改动全量变化；`api.d.ts` 是**契约的编译期表示**，它参与类型检查、diff 可读、体积小。

`api.d.ts` 顶部由工具自动加了「本文件由 openapi-typescript 生成」的标记，评审时据此跳过。同时在 `.gitattributes` 里标记为 `linguist-generated=true`，让它在 GitHub 的 diff 视图里默认折叠。

### 6.5 后端改接口后的同步流程

约定一条硬规则：**契约变更、类型重新生成、前端适配，必须在同一个 commit 或同一个 PR 里完成。**

```
1. 改 maa_api/api/routers/ 下的路由与 Pydantic 模型
2. poetry run python scripts/dump_openapi.py       → web/openapi.json 变化
3. cd web && pnpm gen:api                          → src/types/api.d.ts 变化
4. pnpm typecheck                                  → 所有受影响的调用点报错
5. 按报错逐个修前端
6. 一起提交这三类文件
```

第 4 步是这个流程的全部价值所在。删掉一个字段、改一个枚举值、把可选改必填 —— `tsc` 会精确指出每一处受影响的代码。这是「破坏性重构」这个前提下最重要的一道安全网：既然决策表明确允许破坏性变更、旧接口直接废弃，那就必须有机制保证前端不会悄悄用着已经不存在的字段。

CI 上加两个检查：`pnpm gen:api:check` 确保 `api.d.ts` 与 `openapi.json` 一致（防止只跑了第 2 步忘了第 3 步）；另加一个后端侧的检查，重新导出 `openapi.json` 并与入库版本比对（防止改了路由忘了第 2 步）。两个检查合起来才能堵住整条链。

## 7. 保留 FastAPI 原生 `/docs` 与 `/redoc`

### 7.1 是否需要鉴权保护

**结论：配置了 `access_token` 时，`/docs`、`/redoc`、`/openapi.json` 三者都要保护。**

理由不在于 OpenAPI 本身是机密 —— 接口定义泄露的危害有限。真正的问题是 Swagger UI 是一个**可执行的攻击面**：任何能打开 `/docs` 的人都得到一个现成的图形界面，可以对着所有接口点「Try it out」。虽然请求本身仍会被 401 拦住，但这意味着未鉴权访问者能精确枚举出接口、参数、甚至错误响应的结构，为进一步攻击提供完整地图。对一个有「卸载重装游戏」「清空基建方案」这类破坏性能力的服务，不应该把说明书连同操作台一起公开。

**未配置 `access_token` 时不保护**（此时整个服务本来就是开放的，保护 `/docs` 毫无意义且会阻碍首次上手）。判定逻辑就是「`settings.access_token` 是否为空」。

### 7.2 实现

关掉自动生成的三个端点，换成带鉴权依赖的自定义路由：

```python
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, ...)

@app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(require_token)])
async def openapi_schema():
    return app.openapi()

@app.get("/docs", include_in_schema=False, dependencies=[Depends(require_token)])
async def swagger_ui():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=f"{app.title} · Swagger UI",
        swagger_js_url="/vendor/swagger-ui/swagger-ui-bundle.js",   # 见 §7.3
        swagger_css_url="/vendor/swagger-ui/swagger-ui.css",
    )

@app.get("/redoc", include_in_schema=False, dependencies=[Depends(require_token)])
async def redoc():
    return get_redoc_html(
        openapi_url="/openapi.json",
        title=f"{app.title} · ReDoc",
        redoc_js_url="/vendor/redoc/redoc.standalone.js",
    )
```

`include_in_schema=False` 让这三条路由自己不出现在契约里，否则它们会污染接口树。

**鉴权渠道这里必须靠 cookie。** Swagger UI 是浏览器里的一个页面，它对 `/openapi.json` 的请求是普通的 `fetch`，不会带上任何自定义 header。所以让 `/docs` 可用的唯一办法就是 `access_token` 的 cookie 渠道 —— 用户先在新前端登录（后端下发 `HttpOnly` cookie，见 [09 §10.1](./09-前端重构方案.md#101-token-的录入与存储)），之后访问 `/docs` 时 cookie 自动带上，`/openapi.json` 也自动带上。

这正好解释了为什么决策表要求四渠道传递：Bearer 给前端 fetch、`X-Token` 给外部脚本、query 给 WebSocket 兜底、**cookie 给浏览器直接导航的页面**（`/docs`、`<img src>`、WebSocket 握手）。四个渠道各有不可替代的场景，不是冗余设计。

自研调试台不依赖 cookie，它拉 `/openapi.json` 走的是同一个带拦截器的 `fetch`，注入 Bearer header。

### 7.3 Swagger UI 的静态资源必须本地托管

**这是一个必须处理的问题，否则 `/docs` 在离线环境下完全打不开。** `get_swagger_ui_html` 与 `get_redoc_html` 的默认参数指向 jsDelivr CDN。而本项目的部署形态是私有内网，很可能没有外网出口 —— 此时 `/docs` 会渲染出一个空白页面（HTML 正常返回，但 JS 和 CSS 加载失败）。这和旧前端从 unpkg 拉 Vue、Element Plus 的问题是同一个，[09 §1](./09-前端重构方案.md#1-范围与前提) 已经把「不留 CDN 外链」定为硬要求，这里必须一致。

方案：把 `swagger-ui-dist` 与 `redoc` 的分发文件放进前端工程的 `web/public/vendor/` 下，随 `vite build` 拷进 `static/vendor/`，由 `app.frontend()` 正常托管，后端显式把 `swagger_js_url` 等参数指向本地路径（如上面代码所示）。

两种拿到这些文件的办法：在 `web/package.json` 里把 `swagger-ui-dist` 与 `redoc` 列为 devDependency，加一个 `prebuild` 脚本把需要的几个文件从 `node_modules` 拷到 `public/vendor/`；或者直接把文件提交进 `public/vendor/`（体积约 1.5MB，可接受，且彻底不依赖 npm 安装）。推荐前者，避免二进制资产入库，但要注意 `public/vendor/` 需要加进 `web/.gitignore`。

`/vendor/` 这个路径不能与 SPA 的客户端路由冲突。因为它是 `static/` 下真实存在的文件，`app.frontend()` 会优先当静态文件返回，不会走 `index.html` 兜底，所以只要前端路由不定义 `/vendor/*` 就没问题。

### 7.4 在新前端里的入口

在 `/more/api-console` 页面顶部放两个次级链接：「Swagger UI」与「ReDoc」，`target="_blank"` 打开。位置刻意放在调试台内部而非「更多」的一级列表里 —— 它们是调试台的辅助工具，不是独立功能。链接旁加一行小字说明它们是「标准 OpenAPI 界面，用于交叉验证契约」，避免用户以为这是两个不同的功能入口。

## 8. 对外开放 API 的文档化产物

### 8.1 接入说明应该包含什么

给外部调用者（人类开发者与外部 agent）的接入说明，必须覆盖以下内容。每一项都对应一个「不写就会被问」的问题：

**Base URL 与部署形态。** 默认 `http://<host>:8002`，说明这是单端口单进程、REST 与 WebSocket 与 MCP 共享同一端口、前端也在同一端口。另需说明 HTTPS 部署下 PWA 能力才完整（指向 [09 §12](./09-前端重构方案.md#12-pwa-的安全上下文约束与部署路径)）。

**鉴权。** 单 `access_token`，四种传递方式各自的写法与适用场景：

| 渠道 | 写法 | 适用 |
|---|---|---|
| Bearer | `Authorization: Bearer <token>` | 首选，程序化调用 |
| 自定义头 | `X-Token: <token>` | `Authorization` 被中间层占用时 |
| Query | `?token=<token>` | WebSocket 兜底；**会进服务端 access log，慎用** |
| Cookie | `maa_token=<token>` | 浏览器直接导航的页面 |

**错误码表。** 完整的可枚举错误码、对应 HTTP 状态码、`details` 里会出现什么字段。错误码与状态映射来自 `domain/errors.py`，中文含义来自本文件中的 [05-API规范与路由清单](./05-API规范与路由清单.md) 权威表；接入说明从这两个来源生成而非手抄，避免文案与契约漂移。

**限流说明。** 现在没有限流。但**必须明确写出「当前无限流」并说明后果**，这比不提更有用：外部 agent 在轮询状态时如果不知道没有限流，可能会用 100ms 的间隔猛打 `GET /api/pipelines/current`。所以要给出建议的轮询间隔（状态类 ≥ 2 秒，日志走 WebSocket 而非轮询），并说明如果将来加限流会以 `429` + `Retry-After` 的标准形式返回。

**典型调用序列。** 至少三条完整的端到端流程，每条给出可直接运行的代码：

1. **提交流水线并等待结果。** `POST /api/pipelines` → `202` + `pipeline_id` → 轮询 `GET /api/pipelines/{id}` 直到状态进入终态。要写明终态有哪些（`COMPLETED` / `FAILED` / `CANCELLED`）、建议的轮询间隔、以及「更好的做法是订阅 WebSocket」。
2. **执行原子操作。** 说明流水线运行中调用会得到 `409 PIPELINE_ALREADY_RUNNING`、`force=true` 的语义与审计后果、以及「卡死救援」的正确顺序是先停流水线再操作（[02 §5.3](./02-系统架构设计.md)）。
3. **处理需要人工确认的操作。** 同步调用会阻塞等待、默认超时、超时后返回 `CONFIRMATION_EXPIRED`，调用方应当如何处理（不要立即重试，那只会再触发一次确认请求）。

**WebSocket 接入方式。** 因为它不在 OpenAPI 里（[§5.6](#56-openapi-覆盖不到的两块)），必须完整写：端点 `/api/ws`、鉴权只能走 cookie 或 query、订阅消息的格式、全部事件类型及载荷、心跳约定、`last_seen_id` 的断线补偿机制。摘录自 [06-实时日志与WebSocket](./06-实时日志与WebSocket.md)，正文指向那篇。

**MCP 接入方式。** 这部分等 M12 交付后补入指南；M10 的指南只描述已实现的 REST 与 WebSocket。

**版本与兼容性声明。** 必须明说：当前版本允许破坏性变更，接口不保证向后兼容（这是决策表定下的），外部集成方应当锁定服务版本或做好跟随升级的准备。不写这一条会让接入方产生错误的稳定性预期。

### 8.2 放在哪里

**结论：新增 `docs/14-开放API接入指南.md` 作为唯一正文，调试台内置的「接入指南」页面渲染同一个文件。**

先说为什么不选另外两种做法。**只放 `docs/`** 的问题是它对使用者不可见 —— 一个通过 API 接入的人手上只有一个 URL，不会先去克隆仓库看 Markdown。**只放调试台页面**的问题是内容会变成 TSX 里的硬编码字符串，评审困难、无法在仓库里检索、也不能给不打开浏览器的人看。

所以是一份内容两处呈现：正文写在 `docs/14-开放API接入指南.md`，构建时由 Vite 以 `?raw` 形式引入（`import guide from '../../../docs/14-开放API接入指南.md?raw'`），前端用一个 Markdown 渲染组件展示在 `/more/api-console/guide`。这样单一事实来源在 `docs/` 下，而使用者在浏览器里就能看到。

需要注意 Vite 的 `?raw` 导入路径在 `web/` 之外，得把 `docs/` 加入 `server.fs.allow`（开发期）；构建时因为是编译期内联，不受此限制。如果觉得这个跨目录引用别扭，替代方案是在 `pnpm build` 的 `prebuild` 步骤里把该文件拷进 `web/src/content/`。

两件配套事项：`docs/README.md` 的文档索引表增加 14 号文档条目；`scripts/generate_api_guide.py` 从错误码枚举/状态映射、05 号文档错误说明和 OpenAPI tag 元数据生成标记区块。执行 `python scripts/generate_api_guide.py --check` 可发现生成内容过期，禁止手工编辑标记间内容。

## 9. 与 Agent 模块的边界

调试台与 MCP 是同一套能力的两个前端，服务两类不同的消费者。这个分层关系必须清晰，否则会出现「给 agent 单独做一套接口」的重复建设。

```
                   ┌──────────────┐        ┌──────────────┐
        人 ────────►│  API 调试台  │        │  MCP Server  │◄──────── 外部 agent
                   │  (前端页面)  │        │    /mcp      │      (Claude / Cursor)
                   └──────┬───────┘        └──────┬───────┘
                          │                        │
                          │   ToolRegistry / PolicyEngine（agent 侧额外经过）
                          │                        │
                          ▼                        ▼
                   ┌────────────────────────────────────────┐
                   │   同一套 REST API + 同一套错误码       │
                   │   /api/**  ·  ErrorResponse  ·  审计   │
                   └────────────────────────────────────────┘
                                      │
                                      ▼
                        应用服务层 → MaaCore 子进程
```

**共享的部分。** REST API 的路由与语义、`{"error": {"code", "message", "details"}}` 错误体与错误码枚举、参数 schema（`GET /api/tasks/types` 返回的同一份东西，既驱动调试台的请求构造器，也驱动 MCP tool 的 JSON Schema）、审计落库（两条路径的调用都记 `caller` / `params` / `result` / `duration`，在 `/more/audit` 同一个页面里能看到）。

**调试台独有的。** 面向人的交互：接口树的浏览与搜索、请求历史与收藏、cURL 导出、与实时日志的同屏联动、错误码的可点击跳转。这些对 agent 毫无意义 —— agent 不需要「浏览」接口，它拿到 tool 列表就够了。

**MCP 独有的。** 面向模型的封装：tool 的自然语言描述（让模型知道什么时候该调用它）、`PolicyEngine` 的风险判定与人工确认编排、tool 调用的结果摘要（把冗长的 JSON 压成模型友好的文本）、以及「组合多个 REST 调用成一个语义完整的 tool」这层编排。详见 [11-Agent模块设计](./11-Agent模块设计.md)。

**一条不能破的规则：MCP 的 tool 不绕过 REST 层自己去碰服务层。** 所有 tool 的实现都应当等价于一次 REST 调用（内部可以直接调用同一个服务方法而不真的发 HTTP 请求，但业务语义、参数校验、错误码、审计必须完全一致）。这条规则的价值在两个方向：调试台里测通的东西，agent 调用时行为一定相同；agent 报的错，人可以在调试台里精确复现。如果两条路径有各自的实现分支，「agent 说它失败了但我手动调是好的」这类问题会变得无法排查。

反过来，这也意味着**给 agent 加能力的正确顺序是先加 REST 接口，再包 tool**，而不是直接在 agent 模块里写一个新逻辑。新接口加完后调试台自动可见（因为接口树从 `/openapi.json` 动态加载），可以先用人工调试验证正确性，再包装成 tool 交给模型 —— 这个顺序让每个新能力都有一个可手工验证的中间状态。
