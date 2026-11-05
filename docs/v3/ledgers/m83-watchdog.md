# M83 Watchdog（台账）

- 分支：`watchdog`；基点：`dev` = `0e9c447`；合并：`dev` 上的 `merge: Watchdog（M83，见 docs/v3/13 §8.143–8.152）`；tag：`v3-m83`
- spec：[m83-watchdog](../specs/m83-watchdog.md)（289 行）
- 计划：[m83-watchdog](../plans/m83-watchdog.md)
- 决策记录：[docs/v3/13 §8.143–8.152](../13-决策记录.md)
- 迁移：`0036_watchdog`（可逆）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `37630f5` | 计划入库；§0 1–8（#7 设备目录名不符但非目标）；10 项接缝裁决；基线 `3 failed / 5051 passed` |
| G1（T1） | `89a6b86` | `AlertSeverity`/`AlertTargetKind`/`AlertActionStatus`；`watchdog_alert` 表 + 迁移 0036；`WatchdogRecorder`（core callback/pipeline 开始结束进度/`no_sample_yet`）；`checks.py` 六项纯函数 + `deterministic_checks` + `store`（去重/条件消失解除） |
| G2（T2） | `c6f1817` | `WatchdogService`（run_once/interval 自闸/四类动作/通知/中断解析/close）；`WatchdogSettings` 六键 + `_FIELDS`/模板/测试；main `maa-watchdog` 60s job 与两处关闭清理；core/pipeline 记录点；retention 90 天 |
| G3（T3） | `24445a3` | `watchdog.md` 定义（`device: read` 修正）；`watchdog_findings` schema；语义检查（`watchdog_check` 运行/`complete_structured`/≤2 每轮/≤1 每小时/`mark_blocked` 条件/失败跳过）；定义 19 |
| G4（T4 后端） | `6ab06aa` | 三端点（概况/告警分页/解除）+ `AlertOut`/`AlertPage`/`WatchdogStatusOut`；错误码 163/162；14 号文档 179/agent 76；OpenAPI/类型 |
| G5（T4 前端） | `910ed5f` | `/more/watchdog` 值守页（mode/last_run_at/未解除告警/解除/空态）；导航；`watchdog_alert` 失效；待你处理 critical 告警卡「标记已处理」；前端 109 文件 **958** 用例 |
| G6 | 本台账所在提交 | 02/05/13/16/README/接缝速查同步；13 §8.143–8.152；本台账；全量验收与合并 |

## 核心实现

- **巡检**：`maa-watchdog` 每 60 秒触发、按 `watchdog.interval_seconds`（默认 300）自闸；snapshot 只读数据库与进程内记录器。
- **六项确定性检查**（`checks.py` 函数名 = 08 §6.1 check 字符串）：`run_heartbeat_stale`（`heartbeat_at`）、`ticket_stale`（无活动运行/无待处理/订阅流水线空闲/活动超阈值）、`ticket_churn`（60 分钟 ≥12 轮且全 progress）、`pipeline_no_callback`（执行中任务开始后超阈值无 CALLBACK，无采样 `no_sample_yet` 不告警）、`core_not_ready`（状态持续 10 分钟或 failed）、`queue_stuck`（可领取超 10 分钟且队列/内核/设备/租约/执行器条件齐全）。
- **去重与解除**：同 check+目标未解除时只更新 `evidence.latest`、不新建行、不改首次 `created_at`；条件消失自动写 `resolved_at`。
- **动作**：`record` 只记录；`auto` 时 `interrupt_run`（Worker/总控的中断入口 + 30 秒后标记）、`mark_blocked`（blocked 汇报 + 决策 human_action）、`stop_pipeline`（只取消执行中那条）、`restart_core`（仅开关为真且无执行中流水线）、`wake_queue`（唤醒执行器，不取消队列头）；失败 `action_status=failed` + `evidence.error`。
- **通知**：critical 新插入 → WS `watchdog_alert`、总控 `system` 收件箱、无可见页面时 Web Push、待你处理 critical 卡片；warn/info 只进列表。
- **语义检查**：仅本轮被 `ticket_stale`/`ticket_churn` 挑中的工单，每轮≤2、每张每小时≤1；`watchdog_check` 运行 + `watchdog_findings` schema；`mark_blocked` 仅 auto 且同轮确定性挑中；失败跳过、不建诊断工单。
- **保留**：`watchdog_alert` 90 天（`run_retention` + `watchdog_alerts_deleted`）。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | 检查/DB/枚举 603 passed；迁移 SQL 含 watchdog_alert；`app ok` |
| G2 | service/retention 122 passed；六配置默认值正确；`orchestrator_smoke` OK |
| G3 | semantic/specs/router 59 passed；`builtin_spec('watchdog')` → `watchdog watchdog_findings`；`agent_smoke` OK |
| G4 | 端点/domain 497 passed；openapi/genapi 0；三路径齐 |
| G5 | gen/typecheck/vitest（109 文件 958 用例）/build/frontend_smoke 全 0 |

### 最终验收（G6，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | **3 failed / 5118 passed**；与基线同一组真资源失败，无新增 |
| `scripts/dump_openapi.py --check` | 0（179 operations） |
| `scripts/generate_api_guide.py --check` | 0（新端点后重新生成） |
| 前端五步 | 0；vitest 109 文件 **958** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke` | 均 `SMOKE OK` |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 手动验收（改 heartbeat 调 run_once、真实告警推送） | **未验证**（无常驻服务/真实浏览器）；自动用例与冒烟覆盖等价逻辑 |

### 计数

- 错误码 162/161→**163/162**；端点 176→**179**（agent 73→76）；业务表 51→**52**；代理定义 18→**19**；配置键 +6；工具/规则数不变；迁移 head **`0036_watchdog`**。

## 检查项 ↔ 函数名对照（08 §6.1）

| 08 check | `checks.py` 函数 | 级别 |
|---|---|---|
| `run_heartbeat_stale` | `run_heartbeat_stale` | warn |
| `ticket_stale` | `ticket_stale` | warn |
| `ticket_churn` | `ticket_churn` | warn |
| `pipeline_no_callback` | `pipeline_no_callback` | critical |
| `core_not_ready` | `core_not_ready` | critical |
| `queue_stuck` | `queue_stuck` | critical |
| `ticket_no_progress` / `ticket_drift` | 语义检查（`watchdog` 代理） | 代理给出 |

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | `interrupt_run` 经会话反查 Worker/总控中断入口 + 宽限期标记 | 无按 run 的既有入口（13 §8.143） |
| R2 | 语义检查走 `watchdog_check` 运行 + `complete_structured`，不执行工具 | `run_agent` 需要会话（13 §8.144） |
| R3 | 新增 `watchdog_findings` schema；定义 `device: read` | 注册表规则与工具清单（13 §8.145） |
| R4 | 去重不改 `created_at`、只更新 evidence.latest | spec 自相矛盾（13 §8.146） |
| R5 | `watchdog_alert` 90 天保留并入 run_retention | 03 §14（13 §8.147） |
| R6 | 60s job + 内部 interval 自闸；记录点在 core/pipeline 回调出口 | 13 §8.148 |
| R7 | 动作/通知按 spec；重启只出现在开关分支 | 13 §8.149 |
| R8 | 页面落 `features/watchdog/`；待你处理只 critical | 仓库布局与 11 §11（13 §8.150） |
| R9 | 计数与文档同步 | 13 §8.151 |

## 偏离（已记 13 §8.143–8.152）

- §8.143 interrupt_run；§8.144 语义执行路径；§8.145 schema/device；§8.146 去重；§8.147 保留；§8.148 调度/记录点；§8.149 动作/通知；§8.150 前端；§8.151 计数文档；§8.152 检查项对照。

## 遗留

- 真实环境手动验收未做（改 heartbeat 后 `run_once` 的 record 告警、真实 critical 推送/待你处理卡片）；自动用例与冒烟覆盖等价逻辑。
- `auto` 档的有限自愈（中断运行、取消流水线、唤醒队列、重启内核）只经过替身测试，未在真实内核/流水线上验证。
- 记录器为进程内字典，多进程部署时 `pipeline_no_callback` 的采样口径需再评估（单进程假设）。
- 语义检查的真实模型误判率未评估；`watchdog` 定义 `device: read` 与 spec 的 `device: none` 不一致（已记 13）。
