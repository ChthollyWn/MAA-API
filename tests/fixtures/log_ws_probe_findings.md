# M4-01 前置实测结论：WS 路由与关闭码、跨线程入队、IPC 事件载荷、asst.log tail 语义

本文件由 `scripts/probe_log_ws.py` 生成（重跑覆盖）。所有实验都在临时目录与进程内完成：不联网、不 dlopen MaaCore、不触碰真实 `resource/maa_api.db` 与 `config.yaml`、不依赖真机。实测环境：`fastapi=0.141.1`, `httpx=0.27.2`, `platform=darwin`, `pydantic=2.11.10`, `python=3.13.3`, `sqlalchemy=2.0.54`, `sqlmodel=0.0.42`, `starlette=1.6.0`, `uvicorn=0.53.0`。

## 必读结论（给 M4 实现卡）

1. **WS 卡 verify 只能查模块级 `router.routes[*].path`**（`True`）：`app.openapi()['paths']` = ['/api/probe-http'] 不含 WS 路径，`fastapi.routing.iter_route_contexts(app.routes)` 对被 include 的 WS 路由只给 `path=''` 的上下文对象（实测 2 条，`type(...).__name__ == 'RouteContext'`、`has_tags=True`）；模块级 router 的 `['/api/ws', '/api/ws-auth-fail', '/api/probe-http']` 才可靠。推荐写成 `sorted(r.path for r in maa_api.api.ws.router.routes if isinstance(r, fastapi.routing.APIWebSocketRoute))`。

2. **鉴权失败走 accept → close(4401)**，TestClient 侧 `WebSocketDisconnect.code == 4401`；而**连不存在的 WS 路径同样是 `WebSocketDisconnect`，code 是 1000**（把一个已注册的 HTTP 路径当 WS 连也得到 1000）—— 写测试时不能拿「抛了 WebSocketDisconnect」或 code=1000 当路由缺失的证据，路由存在性只能静态断言模块级 `router.routes`（见第 1 条）。

3. **`LogHub.offer` 必须用 `loop.call_soon_threadsafe` 投递**，不能直接 `asyncio.Queue.put_nowait`：裸线程 put 在无其它唤醒源时（实测 0.5s 窗口内）完全不唤醒循环，消费者可能无限期挂着；`call_soon_threadsafe` 实测延迟 0.32 ms。

4. **`LogRecord.ts` 取 handler 内的到达时刻**：`CoreClient.on()` 的 handler 只收到 payload，LOG 的键集是 ['content', 'level']、CALLBACK 是 ['details', 'msg']，envelope `ts` 到不了 LogHub（只有 `supervisor.note_event` 拿得到）。若一定要用 IPC ts，得改 `CoreClient` 的分派把 envelope 一并交给 handler —— 那是 M1 模块的改动，M4 卡要有意识。

5. **asst.log tail 照 docs/06 §5.4 的判据写，但 `_drain` 的半行回退不能用 `len(line.encode('utf-8'))`**：`errors="replace"` 下含非法字节的半行编码后更长，实测会多退到前一行的中间（读到重复内容）或抛 `ValueError: negative seek position`。正确做法是读之前先记 `tell()`，半行时 seek 回该位置。

6. **本机没有任何 WS 依赖**：`websockets` / `wsproto` 都装不上（缺包），`websocat` 不存在，uvicorn 因此解析不出 WS 协议类（`AutoWebSocketsProtocol is None`），真实 uvicorn 会拒绝升级（裸 socket 实测 status line = `HTTP/1.1 404 Not Found`）。冒烟默认模式只能用 `fastapi.testclient`；advisory 的裸 socket 模式在装上 `websockets` 之前不可用。

## 1. ws_route_hidden_from_openapi_and_route_contexts

**结论**：WS 路由既不进 `app.openapi()['paths']`，也不出现在 `iter_route_contexts(app.routes)` 的 path 里；被 include 的 WS 路由只留下一个 `path=''` 的上下文对象。模块级 `router.routes[*].path` 才给得出 `/api/ws`。

**原始数据**：

```json
{
  "app_route_types": [
    "Route",
    "Route",
    "Route",
    "Route",
    "_IncludedRouter"
  ],
  "app_routes_path_attribute_errors": [
    "_IncludedRouter: '_IncludedRouter' object has no attribute 'path'"
  ],
  "included_router_private_original_router_paths": [
    "/api/ws",
    "/api/ws-auth-fail",
    "/api/probe-http"
  ],
  "iter_route_contexts": [
    {
      "has_tags": false,
      "name": "openapi",
      "path": "/openapi.json",
      "type": "RouteContext"
    },
    {
      "has_tags": false,
      "name": "swagger_ui_html",
      "path": "/docs",
      "type": "RouteContext"
    },
    {
      "has_tags": false,
      "name": "swagger_ui_redirect",
      "path": "/docs/oauth2-redirect",
      "type": "RouteContext"
    },
    {
      "has_tags": false,
      "name": "redoc_html",
      "path": "/redoc",
      "type": "RouteContext"
    },
    {
      "has_tags": true,
      "name": "",
      "path": "",
      "type": "RouteContext"
    },
    {
      "has_tags": true,
      "name": "",
      "path": "",
      "type": "RouteContext"
    },
    {
      "has_tags": true,
      "name": "probe_http",
      "path": "/api/probe-http",
      "type": "RouteContext"
    }
  ],
  "module_router_paths": [
    "/api/ws",
    "/api/ws-auth-fail",
    "/api/probe-http"
  ],
  "module_router_route_types": [
    "APIRoute",
    "APIWebSocketRoute"
  ],
  "openapi_paths": [
    "/api/probe-http"
  ],
  "recommended_verify_result": [
    "/api/ws",
    "/api/ws-auth-fail"
  ],
  "ws_context_placeholder_count": 2,
  "ws_context_placeholder_type": "RouteContext",
  "ws_paths_hidden_from_iter_route_contexts": true,
  "ws_paths_hidden_from_openapi": true
}
```

**对 M4 实现的影响**：WS 卡的 verify 一律写成 `sorted(r.path for r in maa_api.api.ws.router.routes if isinstance(r, fastapi.routing.APIWebSocketRoute))`（或直接断言模块级 `router.routes` 的 path 集合），**不要**用 `{r.path for r in app.routes}`（`_IncludedRouter` 没有 `.path`，M3 已实测必炸），也不要用 `set(app.openapi()['paths'])`（WS 路由根本不在 spec 里）。`_IncludedRouter.original_router.routes` 虽然能挖出路径，但那是私有属性，不要写进门禁。

## 2. ws_close_4401_surfaces_as_disconnect_code

**结论**：`accept()` 之后 `close(code=4401)`，TestClient 侧 `with client.websocket_connect(...)` 在 `receive_text()` 处抛 `WebSocketDisconnect`，`code == 4401`。

**原始数据**：

```json
{
  "accept_then_close_4401": {
    "code": 4401,
    "path": "/api/ws-auth-fail",
    "raised": "WebSocketDisconnect",
    "reason": ""
  }
}
```

**对 M4 实现的影响**：鉴权失败用 accept → `close(code=4401)` 的写法可以在用例里断言 `exc.code == 4401`（docs/05 §5.4 的四个 close code 表就是可测契约），不必去读 HTTP 状态码。

## 3. missing_ws_path_disconnect_code_1000

**结论**：连一个没有注册的 WS 路径，TestClient 抛出的同样是 `WebSocketDisconnect`，但 `code == 1000`。这个 1000 **不能**当作「路由存在」的证据：把一个**已注册的 HTTP 路径**当 WS 连，实测同样是 `code == 1000`（`missing_code_equals_http_route_control=True`）。另一方面，服务端在 `accept()` 之前 `close(1008)` 时，TestClient 侧**确实**能读到 `code == 1008`（不会像真机浏览器那样只剩 HTTP 403），所以 1000 只表示「这次握手没成功」，与路由是否存在无关。

**原始数据**：

```json
{
  "http_route_used_as_ws_control": {
    "code": 1000,
    "path": "/api/http-only",
    "raised": "WebSocketDisconnect",
    "reason": ""
  },
  "missing_code_equals_http_route_control": true,
  "missing_route": {
    "code": 1000,
    "path": "/api/ws-does-not-exist",
    "raised": "WebSocketDisconnect",
    "reason": ""
  },
  "reject_before_accept_control": {
    "code": 1008,
    "path": "/api/ws-reject-before-accept",
    "raised": "WebSocketDisconnect",
    "reason": ""
  }
}
```

**对 M4 实现的影响**：WS 用例里凡是要证明「鉴权失败」的地方，断言必须是 `exc.code == 4401` 而不是「抛了异常」；反过来，负向用例要证明路由缺失也不能靠 code（1000 与「HTTP 路径被当 WS 连」撞在一起），只能靠模块级 `router.routes[*].path` 的静态断言（见第 1 条）。另外：docs/06 §7.1 说「握手阶段返回 HTTP 403 时客户端拿不到原因」——那是**真实浏览器 + 真实服务端**的行为；TestClient 走 ASGI 直连，pre-accept 的 close code 反而可见（实测 1008）。但 M4 的鉴权失败仍应照 docs/05 §5.4 用 accept → `close(4401)`，这样真机与用例行为一致。

## 4. testclient_lifespan_needs_context_manager

**结论**：`TestClient(app)` 直接 `.get()` 可以拿到 200，但 lifespan 的 enter/exit 都不执行；只有 `with TestClient(app) as client:` 才触发 enter（退出时触发 exit）。

**原始数据**：

```json
{
  "after_context": {
    "lifespan_events": [
      "enter",
      "exit"
    ]
  },
  "inside_context": {
    "lifespan_events": [
      "enter"
    ],
    "status": 200
  },
  "without_context": {
    "lifespan_events": [],
    "status": 200
  }
}
```

**对 M4 实现的影响**：WS 卡的用例必须用 with 上下文（否则 LogHub 后台任务、`session_factory` 等 lifespan 资源都不存在）。`tests/api/conftest.py` 的 `make_client(app)` 已经 `__enter__`，照它写即可。

## 5. extract_token_ws_query_precedes_cookie

**结论**：`maa_api.api.deps.extract_token(websocket)` 在真实 WS 握手上能同时取到 query 与 cookie，优先级 query > cookie（两者都给时返回 query；query 为空串时落到 cookie）。

**原始数据**：

```json
{
  "client_cookie_jar_only": {
    "channel": null,
    "client_cookie": "cookie-token-value",
    "cookies_kwarg": null,
    "headers": null,
    "url": "/api/ws-token",
    "value": null
  },
  "cookie_via_cookies_kwarg": {
    "channel": "cookie",
    "client_cookie": null,
    "cookies_kwarg": {
      "maa_token": "cookie-token-value"
    },
    "headers": null,
    "url": "/api/ws-token",
    "value": "cookie-token-value"
  },
  "cookie_via_header": {
    "channel": "cookie",
    "client_cookie": null,
    "cookies_kwarg": null,
    "headers": {
      "connection": "upgrade",
      "cookie": "maa_token=cookie-token-value",
      "sec-websocket-key": "testserver==",
      "sec-websocket-version": "13"
    },
    "url": "/api/ws-token",
    "value": "cookie-token-value"
  },
  "empty_query_falls_through_to_cookie": {
    "channel": "cookie",
    "client_cookie": null,
    "cookies_kwarg": null,
    "headers": {
      "connection": "upgrade",
      "cookie": "maa_token=cookie-token-value",
      "sec-websocket-key": "testserver==",
      "sec-websocket-version": "13"
    },
    "url": "/api/ws-token?token=",
    "value": "cookie-token-value"
  },
  "no_credential": {
    "channel": null,
    "client_cookie": null,
    "cookies_kwarg": null,
    "headers": null,
    "url": "/api/ws-token",
    "value": null
  },
  "query_beats_cookie": {
    "channel": "query",
    "client_cookie": null,
    "cookies_kwarg": null,
    "headers": {
      "connection": "upgrade",
      "cookie": "maa_token=cookie-token-value",
      "sec-websocket-key": "testserver==",
      "sec-websocket-version": "13"
    },
    "url": "/api/ws-token?token=query-token-value",
    "value": "query-token-value"
  },
  "query_only": {
    "channel": "query",
    "client_cookie": null,
    "cookies_kwarg": null,
    "headers": null,
    "url": "/api/ws-token?token=query-token-value",
    "value": "query-token-value"
  }
}
```

**对 M4 实现的影响**：WS 卡直接复用 `extract_token(websocket)`（它本来就是 `Request | WebSocket` 双签名），不要另写一套。写用例时 cookie 有两条可用路径：`headers={'cookie': f'{COOKIE_NAME}=...'}` 或 per-request 的 `client.websocket_connect(url, cookies={COOKIE_NAME: ...})`（实测两者都能落到 cookie 渠道）；**client 级 cookie jar 不行**（`client.cookies.set(..., domain='testserver')` 实测不进 WS 握手 scope）。

## 6. cross_thread_put_nowait_does_not_wake_loop

**结论**：事件循环在另一条线程里 `await queue.get()` 且没有任何定时器/IO 时，主线程 `queue.put_nowait(item)` **不会**唤醒循环：实测 500ms 窗口内消费者收不到（`delivered_within_window=false`），之后显式 `call_soon_threadsafe(lambda: None)` 才把积压的条目送达（说明条目没丢，只是没人唤醒循环）。改用 `loop.call_soon_threadsafe(queue.put_nowait, item)` 时同一场景毫秒级送达。另外，循环开 debug 时裸 put 会直接在生产者线程抛 `RuntimeError: Non-thread-safe operation invoked on an event loop other than the current one`，且条目再也送不到消费者（getter future 的 callback 没被调度）。

**原始数据**：

```json
{
  "call_soon_threadsafe": {
    "delivered_after_explicit_wakeup": true,
    "delivered_within_window": true,
    "item_lost": false,
    "latency_ms": 0.32,
    "loop_debug": false,
    "loop_exception_contexts": [],
    "mode": "threadsafe",
    "put_error": null,
    "window_s": 0.5
  },
  "raw_put_nowait": {
    "delivered_after_explicit_wakeup": true,
    "delivered_within_window": false,
    "item_lost": false,
    "latency_ms": null,
    "loop_debug": false,
    "loop_exception_contexts": [],
    "mode": "raw",
    "put_error": null,
    "window_s": 0.5
  },
  "raw_put_nowait_loop_debug": {
    "delivered_after_explicit_wakeup": false,
    "delivered_within_window": false,
    "item_lost": true,
    "latency_ms": null,
    "loop_debug": true,
    "loop_exception_contexts": [
      "Task was destroyed but it is pending!"
    ],
    "mode": "raw",
    "put_error": "RuntimeError: Non-thread-safe operation invoked on an event loop other than the current one",
    "window_s": 0.5
  },
  "wait_for_control": {
    "raw": {
      "item": "item",
      "mode": "raw",
      "producer_put_at_ms": 150.0,
      "timeout_s": 1.0,
      "wait_elapsed_ms": 1002.3,
      "woken_by_put": false
    },
    "threadsafe": {
      "item": "item",
      "mode": "threadsafe",
      "producer_put_at_ms": 150.0,
      "timeout_s": 1.0,
      "wait_elapsed_ms": 158.8,
      "woken_by_put": true
    }
  }
}
```

**对 M4 实现的影响**：`LogHub.offer` 的 WS 广播出口必须 `loop.call_soon_threadsafe(...)`（或让 `offer` 只在 loop 线程被调），绝不能把裸 `asyncio.Queue.put_nowait` 暴露给 logging handler / tailer 线程。顺带：`asyncio.Queue` 本身不是线程安全的，跨线程投递只走这一个入口。

## 7. deque_maxlen_thread_appends_keep_length

**结论**：8 线程 × 5000 次 append 到 `deque(maxlen=500)`：最终长度恒为 500，全程没有任何时刻超过 maxlen，也没有异常。

**原始数据**：

```json
{
  "appends_per_thread": 5000,
  "elapsed_ms": 7.2,
  "errors": [],
  "final_length": 500,
  "maxlen": 500,
  "observed_over_length": [],
  "threads": 8,
  "total_appends": 40000,
  "unsynchronized_id_counter_control": {
    "duplicates": 0,
    "final_value": 800000,
    "per_thread": 200000,
    "threads": 4,
    "total": 800000,
    "unique": 800000
  }
}
```

**对 M4 实现的影响**：docs/06 §6.1 的环形缓冲可以放心用 `deque(maxlen=ring_size)` + 多线程 `append`，不需要额外加锁；`id` 自增与 `append` 分离（先加锁取 id，再 append），顺序不影响环形缓冲的正确性。附加记录：无锁 `n += 1` 的对照在本机 3.13.3 上没复现重复（见原始数据的 `unsynchronized_id_counter_control`），但这只是当前 GIL 的表现，不构成去掉 `_seq_lock` 的理由。

## 8. handler_payload_lacks_envelope_ts

**结论**：用生产代码造出的 LOG / CALLBACK 事件信封都是 `{type, ts, payload}`；经 `CoreClient._handle_event` 分派后，`on()` 注册的 handler 收到的是 **payload 本身**，键集分别是 ['content', 'level'] 与 ['details', 'msg']，**没有** envelope `ts`。只有 `supervisor.note_event` 收得到完整信封。

**原始数据**：

```json
{
  "callback_event_envelope": {
    "details_keys": [
      "async_call_id",
      "details",
      "uuid",
      "what"
    ],
    "keys": [
      "payload",
      "ts",
      "type"
    ],
    "payload_keys": [
      "details",
      "msg"
    ],
    "ts": 1789571620.279976
  },
  "handler_payload_is_envelope_payload": {
    "CALLBACK": true,
    "LOG": true
  },
  "handler_received": {
    "CALLBACK": {
      "has_ts_key": false,
      "keys": [
        "details",
        "msg"
      ]
    },
    "LOG": {
      "has_ts_key": false,
      "keys": [
        "content",
        "level"
      ]
    }
  },
  "implication": "handler 只收 payload，envelope ts 到不了 LogHub；LogRecord.ts 只能用 handler 内的到达时刻，除非 M4 改 CoreClient 的 on()/分派把 envelope 一并交给 handler",
  "log_event_envelope": {
    "keys": [
      "payload",
      "ts",
      "type"
    ],
    "payload": {
      "content": "probe.log_ws.subprocess: 子进程日志桥接样本",
      "level": "INFO"
    },
    "ts": 1789571620.279822
  },
  "supervisor_note_event_has_ts": true,
  "supervisor_note_event_keys": [
    "payload",
    "ts",
    "type"
  ]
}
```

**对 M4 实现的影响**：M4 的 `LogHub` 从 handler 拿不到 IPC 事件的 `ts`：`LogRecord.ts` 只能取 handler 内的到达时刻（`time.time()`），这会引入「子进程写事件 → 主进程消费线程 → loop 分派」的排队延迟（正常 <1ms，洪峰时可到几十 ms，但不影响排序与补发，因为 id 由 LogHub 分配）。如果业务上要求 ts 等于子进程事件时间，必须单独开一张卡改 `CoreClient`（例如给 handler 传 `(payload, ts)` 或新增 `on_event`），M4 不应偷偷改 M1 的公开签名。

## 9. asst_log_tail_rotation_truncation_half_line_replace

**结论**：五条 tail 语义全部按 docs/06 §5.4 的判据成立：①首次打开 seek 到末尾不回放历史；②`os.rename` 轮转后新 `asst.log` 的 `st_ino` 变化、旧句柄仍能把 rename 前写入的尾部读到 EOF；③截断时 `st_ino` 不变而 `st_size < 上次 offset`；④没有换行符的半行不消费（偏移停在行首，补上换行后才消费）；⑤`errors="replace"` 读非法 UTF-8 字节不抛、替换成 U+FFFD（`errors="strict"` 对照抛 UnicodeDecodeError）。

**原始数据**：

```json
{
  "append_visible": {
    "consumed": [
      "L3"
    ],
    "offset": 9
  },
  "doc_seekback_trap": {
    "control_correct_seekback": {
      "consumed": [
        "xxxxxxxxxx�"
      ],
      "raised": null
    },
    "drift_case": {
      "events": [
        {
          "event": "open",
          "inode": 129332357,
          "offset": 3,
          "seek_to_end": true
        },
        {
          "encoded_length": 13,
          "event": "half_line",
          "position_before_read": 3,
          "seek_target": 1,
          "tell_after_read": 14
        }
      ],
      "position_after_poll": 1,
      "raised": null,
      "reread_next": "1\n"
    },
    "negative_seek_case": {
      "events": [
        {
          "encoded_length": 6,
          "event": "half_line",
          "position_before_read": 0,
          "seek_error": "ValueError: negative seek position -2",
          "seek_target": -2,
          "tell_after_read": 4
        }
      ],
      "raised": "ValueError: negative seek position -2"
    }
  },
  "first_open_skips_existing": {
    "consumed": [],
    "existing_lines": [
      "L1",
      "L2"
    ],
    "offset": 6
  },
  "half_line": {
    "consumed_after_newline": [
      "partial"
    ],
    "consumed_without_newline": [],
    "file_size": 7,
    "half_line_events": [
      {
        "event": "open",
        "inode": 129332355,
        "offset": 0,
        "seek_to_end": true
      },
      {
        "encoded_length": 7,
        "event": "half_line",
        "position_before_read": 0,
        "seek_target": 0,
        "tell_after_read": 7
      }
    ],
    "offset_after_poll": 0
  },
  "non_utf8": {
    "consumed_with_replace": [
      "good",
      "�� bad"
    ],
    "contains_replacement_char": true,
    "strict_control_error": "UnicodeDecodeError: invalid start byte at byte 5"
  },
  "old_handle_after_rename": {
    "read_after_rename": "B\n",
    "second_read": ""
  },
  "rotation": {
    "consumed": [
      "L4",
      "N1",
      "N2"
    ],
    "inode_changed": true,
    "new_file_consumed": [
      "N1",
      "N2"
    ],
    "new_inode": 129332351,
    "old_inode": 129332350,
    "tail_before_rotate_consumed": true
  },
  "truncation": {
    "consumed_after_truncate": [
      "T-after"
    ],
    "inode_unchanged": true,
    "offset_before": 30,
    "size_after": 8,
    "size_lt_offset": true
  }
}
```

**对 M4 实现的影响**：tail 实现照 docs/06 §5.4 的 `_poll_once` 判据写（inode 变化 + 尺寸回退两条都要）。**唯一必须改的一处**：`_drain` 的半行回退不能照抄 `seek(tell() - len(line.encode('utf-8')))` —— `errors="replace"` 下非法字节会让重新编码的长度大于实际读入的字节数，实测多退到前一行中间（下一次读到重复的行片段）或在文件头部直接 `ValueError: negative seek position`。正确做法：读之前 `position_before = fp.tell()`，半行时 `fp.seek(position_before)`（探针对照已验证）。

## 10. bulk_insert_backfills_ids_after_commit

**结论**：临时 sqlite 上 `LogRepository.bulk_insert` 两批写入后，入参 `LogEntry` 实例的 `id` 已回填为 [1, 2, 3] 与 [4, 5]，与库内行一致且严格单调；空序列返回 0；会话是 `expire_on_commit=False`，提交后字段仍可读。整库建在 `tempfile` 临时目录里，出 `TemporaryDirectory` 即删（`temp_db_removed_with_tempdir=True`）。

**原始数据**：

```json
{
  "batch1": {
    "count": 3,
    "ids": [
      1,
      2,
      3
    ]
  },
  "batch2": {
    "count": 2,
    "ids": [
      4,
      5
    ]
  },
  "db_ids": [
    1,
    2,
    3,
    4,
    5
  ],
  "empty_batch": 0,
  "expire_on_commit": false,
  "readable_after_commit": "a0",
  "temp_db_removed_with_tempdir": true
}
```

**对 M4 实现的影响**：广播不能等落库：`LogRecord.id` 按 docs/06 §2 由 `LogHub` 内存自增分配（WS 推送立刻带稳定 id，前端据此补发）。探针这条结论保证的是另一半：攒批落库时入参 `LogEntry.id` 在提交后已经回填、严格单调且与库内一致，所以「落库侧的 id 与内存 id 对齐/续号（服务重启后从库内 max+1 继续）」不会踩到「提交后 id 还是 None」的坑，不需要额外 flush + refresh。

## 11. ws_client_tooling_inventory_local

**结论**：本机没有任何**能装进本项目 .venv** 的真实 WS 客户端/服务端实现：`websockets`、`wsproto`、`websocket`（websocket-client）、`httpx_ws`、`aiohttp` 全部 import 失败，`websocat`/`wscat` 不存在（`wsdump` 命令存在，但属于系统 Python 的 site-packages，`.venv` 里 import `websocket` 仍失败）；uvicorn 0.53.0 的 `AutoWebSocketsProtocol` 解析为 `None`，起真实 uvicorn 用裸 socket 发 WS 握手被拒（实测 status line = `HTTP/1.1 404 Not Found`，日志 `['WARNING:  Unsupported upgrade request.', 'WARNING:  No supported WebSocket library detected. Please use "pip install \'uvicorn[standard]\'", or install \'websockets\' or \'wsproto\' manually.']`）。可用手段只剩 `fastapi.testclient.TestClient`（进程内 ASGI，无 socket）与标准库 `socket`（裸握手，当前会被 uvicorn 拒）。

**原始数据**：

```json
{
  "executables": {
    "curl": "/usr/bin/curl",
    "nc": "/usr/bin/nc",
    "socat": null,
    "websocat": null,
    "wscat": null,
    "wsdump": "/Library/Frameworks/Python.framework/Versions/3.13/bin/wsdump"
  },
  "packages": {
    "aiohttp": {
      "available": false,
      "error": "ModuleNotFoundError: No module named 'aiohttp'"
    },
    "httpx_ws": {
      "available": false,
      "error": "ModuleNotFoundError: No module named 'httpx_ws'"
    },
    "uvicorn": {
      "available": true,
      "version": "0.53.0"
    },
    "websocket": {
      "available": false,
      "error": "ModuleNotFoundError: No module named 'websocket'"
    },
    "websockets": {
      "available": false,
      "error": "ModuleNotFoundError: No module named 'websockets'"
    },
    "wsproto": {
      "available": false,
      "error": "ModuleNotFoundError: No module named 'wsproto'"
    }
  },
  "smoke_advisory_mode": "标准库 socket 手写握手；本机 uvicorn 没有 WS 协议实现，真实 uvicorn 会拒绝升级，advisory 模式当前不可用（除非装 websockets 或 wsproto）",
  "smoke_default_mode": "fastapi.testclient.TestClient（进程内 ASGI，无 socket）",
  "stdlib_socket_available": true,
  "testclient_available": true,
  "uvicorn_websockets": {
    "AutoWebSocketsProtocol": null,
    "loopback_upgrade_probe": {
      "port": 57630,
      "request_path": "/api/ws",
      "response_head": [
        "HTTP/1.1 404 Not Found",
        "date: Wed, 16 Sep 2026 15:13:40 GMT",
        "server: uvicorn",
        "content-length: 22",
        "content-type: application/json"
      ],
      "sec_websocket_accept_present": false,
      "status_line": "HTTP/1.1 404 Not Found",
      "upgraded": false,
      "uvicorn_output": [
        "WARNING:  Unsupported upgrade request.",
        "WARNING:  No supported WebSocket library detected. Please use \"pip install 'uvicorn[standard]'\", or install 'websockets' or 'wsproto' manually."
      ]
    },
    "resolved": false
  }
}
```

**对 M4 实现的影响**：WS 卡的冒烟脚本默认模式（TestClient）可以照 M3 的 `scripts/api_smoke.py` 写；`--serve` 那一档如果真起 uvicorn，WS 升级在没有 `websockets`/`wsproto` 的情况下必然失败（实测 404 + `Unsupported upgrade request.` + `No supported WebSocket library detected.`），所以要么把 `websockets` 加进 `pyproject.toml`（M4 依赖卡决定），要么 advisory 档只做「端点存在性 + 裸 socket 被拒的现状记录」，不要把 WS 实时连通写进必过项。另外 `wsdump` 虽然存在于系统 Python 路径，但不能作为 .venv 内的冒烟手段。

## 12. required_checks 一览

| check | 结果 |
|---|---|
| `ws_route_hidden_from_openapi_and_route_contexts` | ✅ true |
| `ws_close_4401_surfaces_as_disconnect_code` | ✅ true |
| `missing_ws_path_disconnect_code_1000` | ✅ true |
| `testclient_lifespan_needs_context_manager` | ✅ true |
| `extract_token_ws_query_precedes_cookie` | ✅ true |
| `cross_thread_put_nowait_does_not_wake_loop` | ✅ true |
| `deque_maxlen_thread_appends_keep_length` | ✅ true |
| `handler_payload_lacks_envelope_ts` | ✅ true |
| `asst_log_tail_rotation_truncation_half_line_replace` | ✅ true |
| `bulk_insert_backfills_ids_after_commit` | ✅ true |
| `ws_client_tooling_inventory_local` | ✅ true |

`probe_ok = True`（11 条 required_checks 全 true 且探针自身无异常）。

## 13. 环境守卫与探针自身错误

真实库/配置未被触碰：`real_db_untouched=True`、`real_config_untouched=True`、`settings_cache_loaded=False`（false = 没读过 config.yaml）、未联网、未 dlopen MaaCore。注意 `real_db=None`：仓库根当前**没有** `resource/maa_api.db`，探针也没有创建它（第 10 条的库整建在临时目录里，用完即删）。

```json
{
  "after": {
    "real_config": [
      435,
      1789525362475264922,
      121789638
    ],
    "real_db": null,
    "settings_cache_loaded": false
  },
  "before": {
    "real_config": [
      435,
      1789525362475264922,
      121789638
    ],
    "real_db": null,
    "settings_cache_loaded": false
  },
  "maacore_dlopen": false,
  "network_used": false,
  "real_config_untouched": true,
  "real_db_untouched": true,
  "settings_cache_loaded": false
}
```

探针自身错误：无。

## 14. 复现方式

```bash
.venv/bin/python scripts/probe_log_ws.py
```

重跑覆盖 `tests/fixtures/log_ws_probe_result.json` 与 `tests/fixtures/log_ws_probe_findings.md`（后者含时间戳/端口等不稳定字段，不要直接 diff）。
