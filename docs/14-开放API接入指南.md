# 开放 API 接入指南

本指南面向调用 MAA-API 的脚本、服务和浏览器客户端。调试台的「接入指南」页面复用本文件内容；接口字段的权威定义仍是同服务上的 `/openapi.json` 与 `/docs`。

## 1. Base URL 与版本兼容

默认 Base URL 为 `http://<host>:8002`。REST 路由在 `/api` 下，实时日志 WebSocket 为 `/api/ws`。调试台的 Base URL 留空时使用同源相对路径；填写其他地址时，专用 WebSocket 也使用该地址推导出的 `/api/ws`。

本项目处于 v2 重构阶段，接口允许破坏性变更。集成方应固定服务版本，并在升级时检查 `/openapi.json` 的差异。

## 2. 鉴权

`access_token` 配置后，REST 请求按以下任一方式携带 token：

```bash
curl -H 'Authorization: Bearer <token>' http://<host>:8002/api/system/health
curl -H 'X-Token: <token>' http://<host>:8002/api/system/health
curl 'http://<host>:8002/api/system/health?token=<token>'
```

也支持 `maa_token=<token>` cookie。仅凭 cookie 的请求只允许 `GET`、`HEAD` 和 WebSocket 升级；写请求应使用 `Authorization` 或 `X-Token`。query token 可能进入服务端 URL/access log；调试台历史会将其剔除，但仍不适合日常 REST 调用。

未配置 `access_token` 时，服务处于免鉴权模式；可通过 `GET /api/system/health` 的 `auth_enabled` 判断。

## 3. REST 调用与错误处理

成功响应直接返回资源体。创建 API 收藏使用 `201 Created`，响应带 `Location`；删除使用 `204 No Content`。

```bash
curl -X POST http://<host>:8002/api/snippets \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "查询健康状态",
    "method": "GET",
    "path": "/api/system/health"
  }'
```

收藏路由为：

| 方法 | 路径 | 结果 |
|---|---|---|
| GET | `/api/snippets` | 收藏列表 |
| POST | `/api/snippets` | 创建，201 |
| GET | `/api/snippets/{snippet_id}` | 单个收藏 |
| PUT | `/api/snippets/{snippet_id}` | 更新 |
| DELETE | `/api/snippets/{snippet_id}` | 删除，204 |

收藏名称会先 trim，随后按 1–64 字符校验，并区分大小写唯一。不存在返回 404，重名返回 409。收藏及调试历史不会保存 `Authorization`、`X-Token`、`Cookie` 或 query 的 `token`。

非 2xx 响应统一使用以下结构：

```json
{
  "error": {
    "code": "...",
    "message": "中文错误说明",
    "details": {}
  }
}
```

客户端应先处理 HTTP 状态码，再按 `error.code` 决定是否提示、等待状态变化或修正参数。错误码与完整路由表见 [05-API规范与路由清单](./05-API规范与路由清单.md)。日志类需求请使用 WebSocket，避免高频轮询。

### 3.1 限流与轮询

当前没有针对正常业务流量的通用请求限流。现有 `RATE_LIMITED` 只保护鉴权：同一来源 IP 在 60 秒内连续鉴权失败达到默认 10 次后进入 60 秒冷却期；冷却期内该 IP 对非豁免端点的请求均返回 `429` 和 `Retry-After`。一次成功鉴权会清除该 IP 的失败计数。队列已满等业务错误也可能返回 `429`，应结合 `error.code` 区分。

轮询流水线或设备等状态时，建议间隔至少 2 秒，并在重试时加入退避；日志使用 WebSocket 实时订阅，需要补历史时使用日志查询接口，不要高频轮询日志。

## 4. 自动生成的契约摘要

下列两个区块由 `scripts/generate_api_guide.py` 从错误码枚举/状态映射、[05-API规范与路由清单](./05-API规范与路由清单.md) 的权威错误说明和同服务的 `/openapi.json` tag 元数据生成。不要手工编辑标记间内容。

这些表是错误码与 tag 元数据摘要，不是已交付路由清单；错误码枚举与 tag 列表含 agent、人工确认及 MCP 的预留项，不代表这些模块已有可调用 endpoint。M10 当前可调用的 REST 路由以同服务 `/openapi.json` 的 `paths` 为准；此版本没有人工确认 REST 工作流或 MCP Server。

<!-- GENERATED:ERROR-CODES:START -->
| 错误码 | HTTP | 含义与触发场景 |
|---|---:|---|
| `UNAUTHORIZED` | 401 | token 缺失或与 `access_token` 不匹配。四个渠道都没取到有效 token 时返回 |
| `FORBIDDEN` | 403 | 身份有效但动作被策略拒绝：MCP 只读工具集调用了写操作；非同源请求试图仅凭 cookie 执行写操作 |
| `RATE_LIMITED` | 429 | 同一来源 IP 连续鉴权失败超过阈值（默认 10 次/分钟）后的冷却期 |
| `MALFORMED_JSON` | 400 | 请求体不是合法 JSON，或 `Content-Type` 与实际内容不符 |
| `VALIDATION_ERROR` | 422 | Pydantic 字段校验失败。`details.fields` 为 FastAPI 原生的字段级错误数组 |
| `INVALID_PARAMETER` | 400 | 结构合法但语义不成立：未知的设置项 key、互斥参数同时出现、枚举字符串不在取值域 |
| `INVALID_PAGINATION` | 400 | `page < 1`、`size` 超过 200，或同时传了 `page` 与 `after_id` |
| `NOT_FOUND` | 404 | 未被更具体的码覆盖的资源缺失，兜底用 |
| `ENDPOINT_REMOVED` | 410 | 访问了重构前的旧端点，见 §12 |
| `INTERNAL_ERROR` | 500 | 未捕获异常。`details` 只带一个 `trace_id`，堆栈进日志表 |
| `DATABASE_ERROR` | 500 | SQLite 写锁超时、磁盘写满、schema 不匹配 |
| `SERVICE_UNAVAILABLE` | 503 | 服务处于启动中或关闭中，尚未/不再接受业务请求 |
| `CORE_NOT_READY` | 503 | 子进程处于 `STOPPED` / `STARTING`，资源尚未加载完。`details.state` 带具体状态 |
| `CORE_RESTARTING` | 503 | 子进程处于 `RESTARTING`，通常发生在内核热更新期间 |
| `CORE_CRASHED` | 503 | 子进程已崩溃且未恢复。同时用作被中断流水线的 `error_code` |
| `CORE_START_FAILED` | 500 | 连续崩溃达到退避上限进入 `FAILED`，自动重启已停止，需人工介入 |
| `CORE_COMMAND_FAILED` | 502 | 命令投递到子进程但执行失败（如 `AsstAppendTask` 返回 0）。`details.cmd` 带命令类型 |
| `CORE_COMMAND_TIMEOUT` | 504 | 命令在超时内未收到 `CMD_RESULT`。超时不代表子进程已死，仍由心跳判定；命令可能仍在执行，客户端应查状态而非重试 |
| `CORE_NOT_FOUND` | 404 | `core_id` 不存在。首版只有 `default`，为多实例扩展预留 |
| `RESOURCE_LOAD_FAILED` | 500 | `LOAD_RESOURCE` 失败，通常是 MAA 资源目录缺失或损坏 |
| `MAP_LEVEL_KEY_UNAVAILABLE` | 503 | `AsstGetMapLevelKey` 不可用。该 API 属实验性接口，本地内核未导出或调用异常时优雅降级 |
| `DEVICE_NOT_CONNECTED` | 503 | 设备状态机不在已连接态。任务执行前预检失败也报这个 |
| `ADB_CONNECT_FAILED` | 502 | `adb connect` 或 `AsstAsyncConnect` 失败。`details` 带 `address` 与已尝试次数 |
| `ADB_NOT_FOUND` | 500 | 配置的 `adb.path` 不存在或不可执行，属本地环境问题 |
| `ADB_COMMAND_FAILED` | 502 | ADB 原子操作（swipe / 长按 / 输入文本 / 按键）执行失败 |
| `DEVICE_SCAN_FAILED` | 502 | `adb devices` 列举失败，通常是 adb server 未启动 |
| `DEVICE_RESOLUTION_UNSUPPORTED` | 503 | 设备已连上但分辨率不被内核支持，自动化无法进行。`details` 带实际分辨率 |
| `SCREENSHOT_FAILED` | 502 | 内核 `AsstAsyncScreencap` 或 ADB 截屏失败。`details.backend` 指明是哪条通道 |
| `SCREENSHOT_NOT_FOUND` | 404 | 截图 id 不存在 |
| `SCREENSHOT_EXPIRED` | 410 | 记录存在但文件已被保留策略清理（`screenshot.deleted_at` 非空） |
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
| `QUEUE_FULL` | 429 | 待执行流水线数超过上限（默认 50）。带 `Retry-After` |
| `QUEUE_PAUSED` | 409 | 队列被暂停（维护或更新期间）时提交，需先 resume |
| `QUEUE_ITEM_NOT_PENDING` | 409 | 调整优先级或移出队列的目标已不是 `PENDING` 状态 |
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
| `GAME_INSTALL_FAILED` | 502 | `adb install` 返回失败，`details.adb_output` 带原始输出 |
| `GAME_VERSION_UNKNOWN` | 502 | `dumpsys` 解析不出已安装版本，无法做版本对比 |
| `CONFIRMATION_REQUIRED` | 202 | 操作命中消耗类或破坏类策略，已创建确认请求。**唯一出现在 2xx 响应中的码**，位于 202 的正常响应体而非错误体；MCP 同步调用时作为工具结果的 `code` 返回 |
| `CONFIRMATION_NOT_FOUND` | 404 | 确认请求 id 不存在 |
| `CONFIRMATION_EXPIRED` | 409 | 超时未响应，已自动拒绝。默认超时按风险分级：消耗类与破坏类 10 分钟，原子操作会话授权 120 秒 |
| `CONFIRMATION_REJECTED` | 403 | 用户明确拒绝。`details.reason` 带拒绝原因 |
| `CONFIRMATION_ALREADY_RESOLVED` | 409 | 重复批准或拒绝已进入终态的确认请求 |
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
| `SCHEDULE_NOT_FOUND` | 404 | 定时任务 id 不存在 |
| `SCHEDULE_CRON_INVALID` | 400 | cron 表达式 APScheduler 无法解析，或 `timezone` 不是合法 IANA 时区名 |
| `SCHEDULE_NAME_CONFLICT` | 409 | 名称重复，撞 `schedule.name` 唯一约束 |
| `SETTING_KEY_UNKNOWN` | 400 | key 不在 `settings_schema` 内 |
| `SETTING_VALUE_INVALID` | 422 | 值类型或取值范围不符合该项的 schema |
| `SETTING_READONLY` | 403 | 试图修改只能从 `config.yaml` 或环境变量设置的项（`app.access_token`、`app.maa_core_path`、`adb.path`） |
| `SETTING_APPLY_FAILED` | 500 | 值已入库但热生效动作失败，如改完 ADB 地址后重连失败。`details.applied` 标明是否已落库 |
| `NOTIFY_CHANNEL_NOT_FOUND` | 404 | 通道 id 不存在 |
| `NOTIFY_CHANNEL_CONFLICT` | 409 | `(type, name)` 重复 |
| `NOTIFY_CONFIG_INVALID` | 422 | 通道配置不满足该类型的 schema，如 webhook 缺 `url`、bark 缺 `device_key` |
| `NOTIFY_SEND_FAILED` | 502 | 测试发送或实际推送失败。`details` 带上游响应 |
| `RESOURCE_ASSET_NOT_FOUND` | 404 | Copilot 作业 / 基建方案 / 自定义 task 不存在 |
| `RESOURCE_ASSET_CONFLICT` | 409 | `(kind, name)` 重复 |
| `API_SNIPPET_NOT_FOUND` | 404 | API 调试台收藏 id 不存在 |
| `API_SNIPPET_NAME_CONFLICT` | 409 | API 调试台收藏名称重复 |
| `COPILOT_JSON_INVALID` | 422 | 作业 JSON 缺必需字段或结构不符 |
| `INFRAST_PLAN_INVALID` | 422 | 基建方案 JSON 结构不符，或引用了不存在的设施名 |
| `CUSTOM_TASK_INVALID` | 422 | 自定义 task 定义不符合内核 `tasks.json` 的结构约定 |
| `ASSET_TOO_LARGE` | 400 | 单个资源超过 2 MB 上限 |
<!-- GENERATED:ERROR-CODES:END -->

<!-- GENERATED:OPENAPI-TAGS:START -->
| OpenAPI tag | 说明 |
|---|---|
| `system` | 健康检查、服务信息、日志查询 |
| `pipelines` | 流水线提交、查询与取消 |
| `queue` | 队列快照与优先级调整 |
| `tasks` | 任务类型与参数 schema |
| `device` | 设备状态、重连与原子操作 |
| `core` | MaaCore 子进程状态与原生能力 |
| `screenshots` | 截图归档与读取 |
| `updates` | 内核 / 资源 / 游戏三种热更新 |
| `schedules` | 定时任务 |
| `settings` | 可视化配置 |
| `notifications` | 多通道通知 |
| `resources` | Copilot 作业、基建方案、自定义 task |
| `snippets` | API 调试台收藏请求 |
| `agent` | 工具清单、会话与审计 |
| `confirmations` | 高风险操作的人工确认 |
| `ws` | WebSocket 实时通道（仅文档说明，不可在此调试） |
<!-- GENERATED:OPENAPI-TAGS:END -->

## 5. REST 端到端调用

下面的 shell 示例使用 Bash、`curl` 与 Python 3。设置 `BASE_URL`；鉴权已启用时再设置 `TOKEN`。示例会实际执行一场 1-7 战斗，请在已连接且可接受执行任务的设备上运行。

### 5.1 提交流水线并等待完成

`POST /api/pipelines` 返回 `202` 与 `pipeline_id`；随后按该 id 读取详情。状态值为小写的 `pending`、`running`、`completed`、`failed`、`cancelled`；终态是后三者。这里每 2 秒轮询一次。客户端也可改为订阅下方的 `pipeline_status` WebSocket 事件。

```bash
#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8002}"
auth_header="Authorization: Bearer ${TOKEN:-}"

accepted=$(curl -sS -X POST "$BASE_URL/api/pipelines" \
  -H "$auth_header" \
  -H 'Content-Type: application/json' \
  -d '{"title":"API 接入示例","tasks":[{"name":"Fight","stage":"1-7","times":1,"medicine":0,"stone":0}]}')
pipeline_id=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["pipeline_id"])' <<<"$accepted")
printf 'pipeline_id=%s\n' "$pipeline_id"

for ((attempt = 0; attempt < 1800; attempt++)); do
  detail=$(curl -sS "$BASE_URL/api/pipelines/$pipeline_id" -H "$auth_header")
  status=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$detail")
  printf 'status=%s\n' "$status"
  case "$status" in
    completed) break ;;
    failed|cancelled) printf '%s\n' "$detail"; exit 1 ;;
  esac
  sleep 2
done
if [[ "$status" == pending || "$status" == running ]]; then
  printf '流水线仍在运行，停止本地轮询：%s\n' "$pipeline_id" >&2
  exit 1
fi
```

若客户端可能因超时重发提交请求，可为首次请求增加稳定的 `Idempotency-Key` 头；相同 key 与相同内容会复用原流水线，不同内容会返回 `409 IDEMPOTENCY_KEY_CONFLICT`。

### 5.2 执行点击；遇到流水线时先取消并等待

点击使用 MaaCore 通道，不进入队列。流水线运行中默认拒绝，返回 `409 PIPELINE_ALREADY_RUNNING`，`details.pipeline_id` 标明冲突的流水线。建议先读取当前流水线，发起取消并轮询至终态，再重试点击：

```bash
#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8002}"
auth_header="Authorization: Bearer ${TOKEN:-}"

current=$(curl -sS "$BASE_URL/api/pipelines/current" -H "$auth_header")
pipeline_id=$(python3 -c 'import json,sys; p=json.load(sys.stdin).get("pipeline"); print(p["id"] if p else "")' <<<"$current")
if [[ -n "$pipeline_id" ]]; then
  curl -sS -X DELETE "$BASE_URL/api/pipelines/$pipeline_id" -H "$auth_header"
  for ((attempt = 0; attempt < 1800; attempt++)); do
    detail=$(curl -sS "$BASE_URL/api/pipelines/$pipeline_id" -H "$auth_header")
    status=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$detail")
    printf 'pipeline status=%s\n' "$status"
    case "$status" in
      completed|failed|cancelled) break ;;
    esac
    sleep 2
  done
  if [[ "$status" == pending || "$status" == running ]]; then
    printf '取消尚未进入终态；未执行点击。流水线：%s\n' "$pipeline_id" >&2
    exit 1
  fi
fi

curl -i -X POST "$BASE_URL/api/device/click" \
  -H "$auth_header" \
  -H 'Content-Type: application/json' \
  -d '{"x":500,"y":700}'
```

原子操作也支持 `force: true`，它会越过流水线互斥检查；运行中的流水线仍继续，服务端会写强制介入警告日志。应只在明确需要并能接受并发触控冲突时使用。卡死救援也应先停止流水线，确认它已进入终态，再执行点击或其他原子操作（见 [02 §5.3](./02-系统架构设计.md#53-原子操作的并发)）。

## 6. WebSocket 实时通道

浏览器 WebSocket 不能设置 `Authorization` 或 `X-Token`，因此使用 cookie 或 query token：

```javascript
const ws = new WebSocket('ws://<host>:8002/api/ws?token=<token>')

ws.onmessage = (event) => {
  const message = JSON.parse(event.data)
  if (message.type === 'log') {
    console.log(message.data)
    // 持久化已处理的最大 id，重连时作为 last_seen_id 续传。
  } else if (message.type === 'log_batch') {
    console.log('补发日志', message.data.records, 'truncated:', message.data.truncated)
  } else if (message.type === 'pipeline_status') {
    console.log('流水线状态', message.data)
  } else if (message.type === 'subscribed') {
    console.log('订阅已生效', message.data)
  } else if (message.type === 'server_ping') {
    ws.send(JSON.stringify({ type: 'pong', data: { t: message.data.t } }))
  }
}

ws.onopen = () => {
  ws.send(JSON.stringify({
    type: 'subscribe',
    req_id: 'logs-1',
    data: {
      channels: ['log', 'pipeline_status', 'task_status', 'queue_changed', 'core_status', 'device_status', 'update_progress', 'update_available'],
      log_filter: { sources: ['task', 'service'], min_level: 'INFO', pipeline_id: null }
    }
  }))
}
```

WebSocket URL 也可带 `last_seen_id=<最大已处理日志 id>`。查询游标会立即订阅 `log` 并补发 id 更大的日志；之后仍可发送 `subscribe` 设置期望频道。也可省略 URL 游标、改在首次 `subscribe.data.last_seen_id` 中传游标。若未提供游标，只接收新消息。使用 query token 时，token 可能进入访问日志；同源浏览器可先用头部 token 调用 `POST /api/system/auth/cookie` 建立 HttpOnly cookie，再通过 cookie 握手。

所有服务端消息使用 `{type, ts, data}` 信封；请求/应答还可能带 `req_id`。客户端 `subscribe` 的频道名单会整体替换旧订阅；`unsubscribe` 移除指定频道。`log_filter` 可设 `sources`（`task` / `service` / `core`）、`min_level`（`DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`，也接受 `WARN`）与 `pipeline_id`。订阅确认 `subscribed.data` 含实际 `channels`、补发条数 `backfilled` 与截断标记 `truncated`。

M10 当前会发送以下事件：

| `type` | `data` 摘要 |
|---|---|
| `log` | 单条日志：`id`、`source`、`level`、`content`、`pipeline_id`、`task_id`、`request_id`、`logger`、`attachment`。日志来源为 `task`、`service`、`core`。 |
| `log_batch` | 连接补发日志：`records` 为上述日志记录数组，`truncated` 表示环形缓冲是否不足以覆盖游标到当前的整个缺口。 |
| `pipeline_status` | `pipeline_id`、小写 `status`、`source`、`priority`、`progress {total, completed, failed}`、Unix 秒 `started_at` / `finished_at`、`error {code, message}` 或 `null`。 |
| `task_status` | `pipeline_id`、`task_id`、`task_name`、`type_name`、小写 `status`、`retry_count`、`max_retries`、`error`。 |
| `queue_changed` | `running`（当前执行项或 `null`）、`pending`（排队项数组）、`counts {pending, running}` 与 `paused`。每个队列项含 `pipeline_id`、`name`、`source`、`priority`、`status`、`created_at`。 |
| `core_status` | `core_id`、`state`、`pid`、`generation`。 |
| `device_status` | `core_id`、`state`、`address`、`uuid`、`resolution`、`last_connected_at`、`retry`、`last_error`。 |
| `update_progress` | `update_id`、`target`（`core` / `resource` / `game`）、`phase`（`checking` / `downloading` / `verifying` / `waiting_idle` / `applying` / `restarting` / `failed` / `done`）、`percent`、`downloaded`、`total`、`speed`、`eta`、`message`、`error`；部分进度字段可为 `null`。 |
| `update_available` | `targets` 数组，列出有更新的目标及其 `target`、`current`、`latest`；资源/游戏目标可能含 `channel`。没有可用更新时不会发该事件。 |
| `server_shutdown` | 服务关闭通知，`data` 为空对象；随后以 close code `1001` 关闭连接。 |

协议控制帧不需要订阅频道：`server_ping` 的 `data` 为 `{t}`；`subscribed` 回应 `subscribe` / `unsubscribe`，`data` 为 `{channels, backfilled, truncated}`；服务端收到客户端 `ping` 后回 `pong` 并原样回带 `data.t`；格式或频道错误以 `error` 应答，`data` 为 `{code, message}`，若请求带 `req_id` 则应答也带回。客户端每 30 秒发送 `ping` 可检测 RTT；服务端也每 30 秒发 `server_ping`，客户端应如示例回送 `pong` 并原样带回 `data.t`。90 秒无客户端消息或连续两次未响应服务端心跳会关闭连接。鉴权失败 close code 为 `4401`，超过连接数上限为 `4429`；协议连续 10 条非法消息以 `1008` 关闭，慢客户端可能以 `1011` 关闭。

`confirm_request`、`confirm_resolved`、`agent_event` 目前虽被服务端频道校验接受，但 M10 不会发布它们；人工确认和 agent 工作流属于 M11，调用方不能依赖这些事件。

将最新处理的 `log.data.id` 持久化，并在重连时作为 `last_seen_id` 传回。服务端默认的内存环形缓冲为最近 2,000 条日志；若返回 `truncated: true`，用 `GET /api/system/logs?after_id=<旧游标>&before_id=<缓冲最早 id>&order=asc&size=1000` 从数据库补齐，再继续流式接收。`log` 实时事件中的 `id` 单调递增，补发条件为 `id > last_seen_id`。重连退避建议 1、2、4、8 秒递增至 30 秒，并加入抖动。更完整的消息定义见 [06-实时日志与WebSocket §7](./06-实时日志与WebSocket.md#7-websocket-协议)。

请求日志可用 `X-Request-Id` 与调试台请求对应；服务会在响应头回显该值，并暴露 `X-Response-Time-Ms`。当响应体含 `pipeline_id` 时，调试台会继续筛选该流水线的日志。

## 7. 交付范围

本指南只描述 M10 已交付的 REST 与 WebSocket。人工确认 REST 接口/前端工作流和 agent 读取 API 收藏留在 M11；MCP Server 留在 M12。当前版本没有可用的人工确认接口或 MCP 接口；请勿将预留错误码、OpenAPI tag 或 WebSocket 频道名当作功能已交付的证据。
