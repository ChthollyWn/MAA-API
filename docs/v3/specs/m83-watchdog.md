# M83 Watchdog（规格）

> 阶段：P3　依赖：M82　分支：`watchdog`　迁移：`0036_watchdog`
> 本文是 M83 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 分支干净 | `git status -sb` | `## watchdog` |
| 2 | M82 已合并 | `git tag -l 'v3-m82'` | `v3-m82` |
| 3 | 下一个迁移号 | `ls maa_api/db/migrations/versions/ \| tail -n 3` | 最大号小于 0036；不是就用下一个空号并记 13 §8 |
| 4 | 调度器能加周期任务 | `rg -n "add_job\|scheduler" maa_api/main.py \| head` | 至少 1 行 |
| 5 | 取消流水线的入口 | `rg -n "async def request_cancel" maa_api/services/pipeline_runner.py` | 1 行 |
| 6 | 内核事件入口 | `rg -n "def on\b\|async def on" maa_api/core/client.py \| head` | 记下回调注册点 |
| 7 | 设备重连入口 | `rg -n "def reconnect\|async def reconnect" maa_api/services/device/ \| head` | 记下函数 |
| 8 | 基线 | `.venv/bin/python -m pytest -q tests/services/test_pipeline_runner.py > /tmp/m83-base.log 2>&1; echo $?; tail -n 3 /tmp/m83-base.log` | 记下退出码 |

任何一条对不上，停下来报告，不要发明一套并行的取消或重连接口。

## 1. 背景与目标

运行中的失败现在要等用户来看。Watchdog 按 [08 §6](../08-主动性与通道.md) 做确定性检查，再对少数可疑工单做一次语义检查。默认只记录。`watchdog.mode=auto` 时才执行 08 §6.2 列出的动作。

做完后：

1. 表 `watchdog_alert` 与 [03 §11.5](../03-数据模型.md) 一致。
2. 08 §6.1 的检查项与 §6.2 的动作逐条实现。检查名用 08 的名字，不另起一套。
3. `mode=record`（默认）只写告警，`action_status=recorded`。`mode=auto` 才执行动作。
4. 语义检查只看本轮被 `ticket_stale` 或 `ticket_churn` 挑中的工单，每轮至多 2 张，每张每小时至多 1 次。
5. `critical` 进入待你处理，并作为 `watchdog.alert` 交给总控。没有可见页面时走 M82 已有的 Web Push。重启内核默认不做。

## 2. 非目标

- 不改流水线调度的优先级与租约规则。
- 不诊断失败原因，不建 `diagnose` 工单。语义检查只写告警；`auto` 档下 `mark_blocked` 写的是 blocked 汇报，不是新工单。
- 不新增 IM 推送。IM 留给 M85。本里程碑的对外通知只有 M82 已有的 Web Push。
- 不实现 08 §6 没有列出的检查项，也不要租约过期、设备离线这两项。

## 3. 相关文档

- 表：[03 §11.5](../03-数据模型.md)。
- 端点：[04 §2.4](../04-接口与事件协议.md) 的 M83 行。错误码 `WATCHDOG_ALERT_NOT_FOUND`。
- 配置：[04 §6](../04-接口与事件协议.md) 的 `watchdog.*`。
- 检查、动作、频率：[08 §6](../08-主动性与通道.md)。代理定义：[05 §3.4](../05-运行时与代理定义.md) 的 `watchdog`。
- WebSocket：`agent_event` 的 `watchdog_alert`。

## 4. 设计

### 4.1 数据模型

`watchdog_alert` 与 03 §11.5 逐列一致。枚举：

```python
class AlertSeverity(StrEnum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"

class AlertTargetKind(StrEnum):
    RUN = "run"
    TICKET = "ticket"
    DEVICE = "device"
    CORE = "core"
    QUEUE = "queue"

class AlertActionStatus(StrEnum):
    RECORDED = "recorded"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
```

迁移 `0036_watchdog`。自增主键，与 `agent_message` 的写法相同。

同一 `check` + `target_kind` + `target_id` 在 `resolved_at IS NULL` 时不重复插入。后一次检查只更新 `evidence` 与 `created_at` 不改：保留首次时间，把最新证据写进 `evidence.latest`。

### 4.2 模块与文件

新建：

- `maa_api/agent/watchdog/checks.py`：纯函数，输入快照，输出告警草稿列表。
- `maa_api/agent/watchdog/service.py`：`WatchdogService.run_once`
- `maa_api/agent/watchdog/recorder.py`：进程内记录器，见 §4.5。
- `maa_api/agent/specs/builtin/watchdog.md`
- `maa_api/api/agent/watchdog.py`
- `web/src/features/more/WatchdogPage.tsx`

修改：

- `maa_api/main.py`：lifespan 里注册任务 `maa-watchdog`，间隔 60 秒。任务内部若距上次成功不足 `watchdog.interval_seconds`，直接返回。
- `maa_api/core/client.py`：在已有的事件回调出口调用 `recorder.note_core_callback()`。
- `maa_api/services/pipeline_runner.py`：任务开始、任务结束、进度回调出口调用 `recorder.note_pipeline(pipeline_id)`。

记录器是进程内字典，重启后清空。当前任务开始后还没有任何 `CALLBACK` 时，`pipeline_no_callback` 不告警，`evidence.reason=no_sample_yet`。不要把「还没采样」当成冻结。

回调挂在 `CoreClient.on("CALLBACK", ...)` 上，与 08 §6.1 的说法一致。流水线进度若已经从这条回调进来，不要再另挂一套。

### 4.3 接口

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/api/agent/watchdog` | `{mode, last_run_at, open_count}` |
| GET | `/api/agent/watchdog/alerts` | 函数名 `list_watchdog_alerts`。query：`resolved`（默认 false）、`severity`、`page`、`size`。分页遵守 04 §1，不用 `limit` |
| POST | `/api/agent/watchdog/alerts/{id}/resolve` | 写入 `resolved_at`。不存在则 `WATCHDOG_ALERT_NOT_FOUND` |

`AlertOut`：`id`、`check`、`severity`、`target_kind`、`target_id`、`message`、`evidence`、`action`、`action_status`、`created_at`、`resolved_at`。

### 4.4 配置项

只注册 04 §6 已有的键，分组以 04 为准。不要新增 `run_stale_seconds`、`queue_stale_seconds`、`semantic_max_tickets`。

| 键 | 默认 | 含义 |
|---|---|---|
| `watchdog.enabled` | true | false 时周期任务直接返回 |
| `watchdog.mode` | `record` | `record` 或 `auto`。没有 `act` 这个值 |
| `watchdog.interval_seconds` | 300 | 两次真正执行的最小间隔，范围 60–3600。job 仍每 60 秒触发，间隔未到就返回 |
| `watchdog.allow_core_restart` | false | false 时 `restart_core` 恒为 `skipped` |
| `watchdog.ticket_stale_minutes` | 30 | `ticket_stale` 的阈值，范围 5–1440 |
| `watchdog.device_frozen_minutes` | 10 | `pipeline_no_callback` 的阈值，范围 2–120 |

运行心跳用已有的 `agent.run_stale_seconds`（M66，默认 90，config-only）。队列卡住的 10 分钟、内核不就绪的 10 分钟、`ticket_churn` 的 60 分钟与 12 轮，按 08 §6.1 写死，不做成配置。

### 4.5 行为与算法

**周期**

```
async def run_once(now):
    if not enabled: return
    if last_run_at and now - last_run_at < interval: return
    drafts = deterministic_checks(snapshot(now))
    drafts += semantic_checks(now)   # 只在有模型预算时
    for draft in drafts:
        upsert_open_alert(draft)
        if mode == "auto":
            apply_action(draft)
        else:
            action_status = "recorded"
    last_run_at = now
```

`snapshot` 只读数据库与记录器，不调用模型，不碰设备。

**确定性检查**

逐条对照 08 §6.1，函数名与 `check` 字符串相同。条件消失后把未解除的同目标告警写入 `resolved_at`，不等人去点。

| check | 目标 | 条件 | 级别 | `auto` 时的动作 |
|---|---|---|---|---|
| `run_heartbeat_stale` | run | 本进程的运行 `status=running`，且 `heartbeat_at` 早于 `agent.run_stale_seconds`。其他进程留下的运行交给 M66 的启动恢复，这里不碰 | warn | `interrupt_run`：走 M70 的中断入口；30 秒后仍是 running，就把运行标为 `interrupted` |
| `ticket_stale` | ticket | 工单 `open`，没有活动的 Worker 运行，收件箱没有待处理项，订阅的流水线都不在排队或运行，最近活动早于 `watchdog.ticket_stale_minutes` | warn | `mark_blocked`：写一条 blocked 汇报，`human_action` 问继续还是取消，照常转给总控 |
| `ticket_churn` | ticket | 最近 60 分钟这张工单的 Worker 至少起了 12 轮，期间汇报都是 progress | warn | 只记。语义检查可以建议 `mark_blocked` |
| `pipeline_no_callback` | device | 有流水线在执行，当前任务开始后超过 `watchdog.device_frozen_minutes` 没有 `CALLBACK` | critical | `stop_pipeline`：`PipelineRunner.request_cancel` |
| `core_not_ready` | core | 内核状态为 starting、restarting、crashed 持续 10 分钟以上，或状态为 failed | critical | `restart_core`：仅当 `allow_core_restart` 为真且没有流水线在执行。否则 `action_status=skipped` |
| `queue_stuck` | queue | 有可领取的排队流水线超过 10 分钟，队列没暂停，内核就绪，设备已连接，没有租约，执行器空闲 | critical | `wake_queue`：唤醒执行器。执行器任务已经退出时只记告警，提示重启服务，不取消队列头 |

同一 `check` + 目标在 `resolved_at IS NULL` 时不插第二行，只更新 `evidence`。`evidence` 记下 08 §6.2 要求的事实：运行 id 与心跳时间、工单最近活动、当前任务与最后一次回调时间、内核状态与持续时长、排队数与最早一条的时间。

动作抛异常时 `action_status=failed`，`evidence.error` 记异常类型与消息，不向外抛。

**语义检查**

只在确定性检查写完之后，并且只针对本轮被 `ticket_stale` 或 `ticket_churn` 挑中的工单。每轮至多 2 张，同一工单一小时内至多一次。对每张起一轮 `agent_run`，`kind=watchdog_check`，`session_id` 为空，代理 `watchdog`。输出与 05 §8 一致：

```json
{"items": [{"check": "ticket_no_progress", "target_kind": "ticket", "target_id": "…", "severity": "warn", "message": "……", "suggested_action": "mark_blocked"}]}
```

`check` 只能是 `ticket_no_progress` 或 `ticket_drift`。`suggested_action` 为空表示只记。`mark_blocked` 仅在 `mode=auto` 且这张工单本轮也被确定性检查挑中时执行。模型失败则跳过该工单，不写告警，不建诊断工单。

**通知**

`severity=critical` 且是新插入时：

1. 广播 `agent_event`，`event=watchdog_alert`。08 说的 `watchdog.alert` 是交给总控的那条消息，不是另一个 WebSocket 频道。
2. 往总控收件箱写 `system`，正文是 `message` 加 alert id。
3. 待你处理增加来源 `watchdog_alert`。
4. `ConnectionManager.has_visible_client()` 为假时，调用 M82 已有的 Web Push。找不到这个函数就停下来问，不要新写一套推送。

`warn` 与 `info` 只进告警列表，不进收件箱，不推送。

### 4.6 前端

`/more/watchdog`，标签「值守」。页面上方显示 `mode` 与 `last_run_at`，下方列表显示未解除告警。每行有「解除」按钮，调用 resolve。空列表显示「没有未解除的告警」。

`watchdog_alert` 事件使 `GET /api/agent/watchdog/alerts` 失效。

### 4.7 提示词与代理定义

工具名单与 05 §3.4 的 `watchdog` 行一致。

```yaml
name: watchdog
title: 值守
kind: background
description: 阅读一张工单的最近汇报与状态，判断是否卡住。不操作设备。
model_role: worker
device: none
risk_ceiling: none
delegates: []
output_schema: watchdog_findings
max_steps: 4
version: 1
tools: [get_system_status, get_device_status, get_queue, list_pipelines, get_pipeline, get_logs, get_screenshot, list_tickets, get_ticket]
```

正文：

```
你只判断这张工单有没有实质进展，以及它有没有偏离委托。
不调用会改设备、会提交任务、会改设置的工具。定义里没有的工具不要假设存在。
items 为空表示看不出异常。
check 只能是 ticket_no_progress 或 ticket_drift。
message 用一句陈述句。suggested_action 只在建议把工单标成 blocked 时填 mark_blocked，否则省略。
不要建议新建诊断工单。
```

语义检查的动作由 `WatchdogService.apply_action` 执行，不由模型执行。不要给这份定义加 `report`、`stop_pipeline` 或重启工具。

## 5. 实施任务

### T1 迁移、记录器、确定性检查

- 写范围：模型、迁移、`recorder.py`、`checks.py`、`tests/agent/test_watchdog_checks.py`。
- 步骤：检查函数不读全局状态，快照由测试构造。每项 08 的检查至少一个用例。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_watchdog_checks.py tests/db/test_models.py`，退出码 0。

### T2 服务与动作

- 写范围：`service.py`、`main.py` 的任务注册、内核与流水线的记录点、`tests/agent/test_watchdog_service.py`。
- 步骤：`mode=record` 时断言 `request_cancel` 与中断入口都未被调用。`mode=auto` 且 `pipeline_no_callback` 成立时，断言 `request_cancel` 被调用一次。`queue_stuck` 成立时断言唤醒了执行器，没有取消队列头。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_watchdog_service.py`，退出码 0。

### T3 语义检查与定义

- 写范围：`watchdog.md`、服务里的语义分支、测试。
- 步骤：FakeLLM 返回一条 `suggested_action=mark_blocked`。`mode=auto` 且该工单同时被 `ticket_stale` 挑中时，断言多了一条 blocked 汇报，没有新建 diagnose 工单。同一工单一小时内第二次 `run_once` 不再调用模型。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_watchdog_semantic.py`，退出码 0。

### T4 端点与前端

- 写范围：`api/agent/watchdog.py`、`WatchdogPage.tsx`、待你处理来源、realtime 两处、测试。
- 步骤：resolve 不存在的 id 返回 404 与 `WATCHDOG_ALERT_NOT_FOUND`。
- 验证：`.venv/bin/python -m pytest -q tests/api/test_watchdog.py`；`cd web && pnpm typecheck && pnpm test --run src/features/more`。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/test_watchdog_checks.py` | `test_run_heartbeat_stale` | 用 `heartbeat_at`，不用 `updated_at` |
| 同上 | `test_pipeline_no_callback_without_sample` | `no_sample_yet`，不建告警 |
| 同上 | `test_open_alert_not_duplicated` | 仍是一行 |
| 同上 | `test_condition_cleared_resolves` | 条件消失后 `resolved_at` 非空 |
| `tests/agent/test_watchdog_service.py` | `test_record_mode_does_not_act` | `request_cancel` 与中断次数都是 0 |
| 同上 | `test_auto_stops_frozen_pipeline` | 只取消正在执行的那条 |
| 同上 | `test_auto_wakes_queue_without_cancel` | 队列头仍在 |
| 同上 | `test_restart_skipped_by_default` | `action_status=skipped` |
| 同上 | `test_interval_gate` | 默认 300 秒内第二次 `run_once` 不写新告警 |
| `tests/agent/test_watchdog_semantic.py` | `test_semantic_only_on_flagged_tickets` | 没被确定性检查挑中的工单不调模型 |
| 同上 | `test_mark_blocked_does_not_create_diagnose` | 工单数不变，多一条 blocked 汇报 |
| `tests/api/test_watchdog.py` | `test_resolve_missing` | 404，`WATCHDOG_ALERT_NOT_FOUND` |

## 7. 验收

照 12 §4。另：

1. 把一条本进程运行的 `heartbeat_at` 改到 `agent.run_stale_seconds` 之外，直接调用 `run_once`。`mode=record` 时告警存在且运行状态不变。
2. `allow_core_restart` 默认 false。`rg -n "restart" maa_api/agent/watchdog` 只出现在 `allow_core_restart` 为真的分支里。

## 8. 风险与回滚

- 记录器挂错回调会导致 `pipeline_no_callback` 误报。T1 的 `no_sample_yet` 与「有回调则不告警」必须都在。
- 回滚：revert，`alembic downgrade -1`。周期任务随代码消失。

## 9. 文档同步

- 错误码总数加 1（`WATCHDOG_ALERT_NOT_FOUND`，若 04 已计入则不要再加）。
- 路由数按实际新增的 3 条更新。
- 表数加 1。
- 08 §6 的检查项与 `checks.py` 的函数名在台账里列对照表。

## 10. 停下来问

- 08 §6 的某项依赖数据库里不存在、记录器也补不上的时间戳。
- 中断入口、`request_cancel`、内核重启、执行器唤醒的现有函数语义与 08 §6.2 对不上。
- 想把 `mode` 做成 `act`，或在 `record` 下执行动作。
- 想让语义检查直接建 `diagnose` 工单。
- M82 的 Web Push 函数找不到。
