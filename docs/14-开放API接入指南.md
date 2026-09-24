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

也支持 `maa_token=<token>` cookie。仅凭 cookie 的请求只允许 `GET`、`HEAD` 和 WebSocket 升级；写请求应使用 `Authorization` 或 `X-Token`。query token 会进入 URL 相关日志与历史，不适合日常 REST 调用。

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

## 4. 自动生成的契约摘要

下列两个区块由后续文档工具从错误码枚举与 OpenAPI tag 元数据生成。M10 只预留稳定标记，当前权威来源分别是 [05-API规范与路由清单](./05-API规范与路由清单.md) 与同服务的 `/openapi.json`；不要手工编辑标记间内容。

<!-- GENERATED:ERROR-CODES:START -->
<!-- GENERATED:ERROR-CODES:END -->

<!-- GENERATED:OPENAPI-TAGS:START -->
<!-- GENERATED:OPENAPI-TAGS:END -->

## 5. WebSocket 日志

浏览器 WebSocket 不能设置 `Authorization` 或 `X-Token`，因此使用 cookie 或 query token：

```javascript
const ws = new WebSocket('ws://<host>:8002/api/ws?token=<token>')

ws.onmessage = (event) => {
  const message = JSON.parse(event.data)
  if (message.type === 'log') console.log(message.data)
}

ws.onopen = () => {
  ws.send(JSON.stringify({
    type: 'subscribe',
    req_id: 'logs-1',
    data: { channels: ['log'] }
  }))
}
```

请求日志可用 `X-Request-Id` 与调试台请求对应；服务会在响应头回显该值，并暴露 `X-Response-Time-Ms`。当响应体含 `pipeline_id` 时，调试台会继续筛选该流水线的日志。重连、订阅过滤、心跳和 `last_seen_id` 补发协议见 [06-实时日志与WebSocket](./06-实时日志与WebSocket.md)。

## 6. 后续交付

人工确认工作流和 agent 读取 API 收藏均留在 M11。M12 将交付 MCP Server；在此之前，本指南只覆盖已实现的 REST 与 WebSocket 接入。
