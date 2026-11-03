# M66 运行树与持久化运行（台账）

- 分支：`durable-runs`；基点：`dev` = `7d9e044`；合并：`dev` 上的 `merge: 运行树与持久化运行（M66，见 docs/v3/13 §8.12–8.19）`；tag：`v3-m66`
- spec：[m66-durable-runs](../specs/m66-durable-runs.md)
- 计划：[m66-durable-runs](../plans/m66-durable-runs.md)
- 决策记录：[docs/v3/13 §8.12–8.19](../13-决策记录.md)
- 来源：继续按 goal 推进 v3；执行方式 subagent-driven（每任务一个全新子代理实现、主 agent 逐任务复跑验证）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `d9d2d05` | 计划入库；§0 的 11 条检查全过；§0.9 两条「停下来问」由主 agent 先行裁决（A1 两码并存 / A2 生命周期归发起方） |
| T1 | `fb26964` | 三个枚举；`AgentSession.kind/parent_session_id/archived_at`、`AgentMessage.run_id`、新表 `AgentRun`（部分唯一索引等 3 索引）；迁移 `0023_agent_runs`（batch 重建两表、显式 `copy_from`、`sqlite_sequence` stash/restore、incident 回填、可逆降级）；测试常量按 §4.1.4 |
| T2 | `251c745` | 错误码 `RUN_NOT_FOUND`(404) / `RUN_ALREADY_ACTIVE`(409)；计数 127/126；`docs/v3/14` §4.11 两行；04 §5 记 A1；config-only 配置 `agent.run_heartbeat_seconds`(15)/`agent.run_stale_seconds`(90) 与模板/测试同步；重生成 `docs/v3/15` |
| T3 | `c88c9a4` | `AgentRunRepository`（10 方法）、`maa_api/agent/runs/`（`RunService` 全部方法 + 心跳任务 + 失联接管；`RunRecovery.recover/recover_run`；`_unpaired_tool_calls`）；18 条 service 用例 |
| T4 | `7190762` | `main.py` 装配 `boot_id`/`RunService`/`app.state.run_service`/条件 `bind_run_service`/启动 `RunRecovery.recover()`/关闭 close；`build_model_context` 加 `role='system'` → `【系统】` user 分支；活动/装配用例 |
| T5 | `0d73113` | 门面 11 项改造：`run_service` 参数、`bind_run_service`、`acquire_session` 查库、`_begin/_end_legacy_run`/`_forget_run`、`_LegacyRunTracker`、锁与中断改按 `run_id`、删 `_active_turns`、`_produce_turn`/`run_incident` 终态先落库、`_persist_message` 写 `run_id`/`trigger_seq`、`RunContext` 用真实 run id、`incidents.py` 会话 kind；12 条 legacy run 用例 |
| T6 | `830b875` | `RunSummary`/`RunDetail`/`RunPage` 与 `GET /api/agent/runs`、`GET /api/agent/runs/{run_id}`；`docs/v3/14` §6.15 两行、123→125、（20→22 条）；重生成 openapi 与 `api.d.ts` |
| T7 | `a6cca10` | `agent_smoke.py` 加运行记录三步检查；`test_retention_keeps_agent_runs` |
| T8 | 本台账所在提交 | 02 §6.5/§3、03 §3.3、05 §2.2 同步；13 §8.12–8.19；v3 README 状态行；全量验收与合并 |

## 验证证据

### 迁移（T1，真实库副本预演）

- 预演：`resource/maa_api.db` 副本升级前 82 会话（`incident_key` 非空 36）、消息 2177、revision 0022；
- `upgrade head` 后：`kind` 为 `chat 46 / incident 36`（incident 数 = 升级前非空数）、消息 2177 不变、`fk []`、`integrity ('ok',)`、`sqlite_sequence(agent_message)=2226`、revision 0023；
- 追加降级预演：`downgrade 0022` 后无 `agent_run`、会话/消息数与序列不变、消息 id 集合一致；再升级恢复 0023，结果同前。
- 用例：`tests/db/test_agent_runs_migration.py` 7 条通过（含升级、序列保持、外键 ondelete、部分唯一索引、降级形状、离线 `--sql`、重复升降级）。

### 逐任务门禁

| 任务 | 结果 |
|---|---|
| T1 | `tests/db tests/domain/test_enums.py` = `354 passed`；迁移文件 7 passed |
| T2 | `test_errors + test_settings + test_vapid_settings + test_api_guide_generator` = `93 passed`；`generate_api_guide --check` current；`docs/v3/15` diff 仅新两行 |
| T3 | `test_run_service.py` = `18 passed`；`tests/db` = `299 passed` |
| T4 | 恢复/装配/app_skeleton/assembly 四文件 = `36 passed`；parity `17 passed` |
| T5 | `tests/agent/runs + test_runtime + test_incidents + test_recon_delegate + test_agent_router` = `169 passed`；parity `17 passed`；wiring `2 passed`；`tests/agent tests/api` 全量 `1312 passed, 1 skipped, 2 xfailed`；`grep -rn '_active_turns' maa_api` 无输出 |
| T6 | 三个测试文件 = `45 passed`；`dump_openapi --check` 0；`pnpm gen:api:check` 0；openapi 里 `agent_list_runs`/`agent_get_run` 共 2 条 |
| T7 | `agent_smoke.py` = `SMOKE OK` 且含 `[ok] 运行记录：对话与自动诊断各一条已结束的运行`；retention = `13 passed` |

### 最终验收（T8，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -o addopts= -q` | `3 failed, 4043 passed, 5 skipped, 2 xfailed in 245.42s`；3 条失败与基点同一组（真资源用例） |
| `scripts/dump_openapi.py --check` | 退出码 0 |
| `scripts/generate_api_guide.py --check` | 退出码 0（`docs/v3/15` current） |
| 前端五步（install / gen:api:check / typecheck / test / build） | 退出码 0；vitest 74 文件 773 用例全过 |
| `scripts/frontend_smoke.py` | `SMOKE OK` |
| `scripts/agent_smoke.py` | `SMOKE OK`（含运行记录新检查） |
| `scripts/check_doc_links.py` | `BROKEN 0` |
| `git diff --check` | 退出码 0 |
| 手动检查（本机服务 + 确认卡 + 重启恢复） | **未验证**（本会话没有浏览器/设备环境）；由下面的验证证据替代：恢复逻辑由 `test_run_recovery.py` 6 条 + `test_run_recovery_wiring.py` 2 条两次 lifespan 用例覆盖，`test_legacy_runs.py` 的 409/中断/确认等待状态切换由单测覆盖 |

### 计数

- 错误码 124→… 本里程碑从 M65 的 125/124 到 **127/126**；`SECTION_COUNT` 20 不变。
- HTTP 端点 123 → **125**（`### 6.15 agent` 20 → **22 条**）。
- 业务表 28 → **29**（`agent_run`）；`EXPECTED_INDEXES` 31 → **36**；FK CASCADE 4 → 5、SET NULL 24 → 27；TEXT 17 → 18；JSON 34 → 35；UUID 主键表 13 → 14；枚举 +3。
- `SETTING_KEYS` +2、config-only 9 → 11；工具 84 / 模型可见 83 不变；OpenAPI tag 19、WS 频道 14 不变；迁移 head `0022_skland_only_operators` → **`0023_agent_runs`**。
- 新增测试文件 5 个：`tests/db/test_agent_runs_migration.py`、`tests/agent/runs/test_run_service.py`、`test_run_recovery.py`、`test_legacy_runs.py`、`tests/api/test_runs_router.py`、`tests/api/test_run_recovery_wiring.py`（6 个）。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | §0.9 第一条（0.9a 无输出）：`RUN_ALREADY_ACTIVE` 与 `AGENT_SESSION_BUSY` 并存，v3 端点用前者、v2 发消息端点继续后者，04 §5 补说明（13 §8.18） | v2 错误契约不能变；新码留给 v3 端点；spec 自己也给了「两者并存」的做法，只需主 agent 确认 |
| R2 | §0.9 第二条（0.9b 无输出）：运行生命周期归发起方，`AgentLoop` 不建/结束运行（13 §8.12） | 发起方持锁、在循环外写失败说明与中断消息；消息要带 run_id |
| R3 | T5 的 `_LegacyRunTracker` 改成「先切状态、再转发 emit」（13 §8.19） | emit-first 在 v2 测试的 `StaticPool` 单连接下会丢提问作答提交（稳定复现）；SSE 事件名/顺序/字段不变 |
| R4 | `spec_version` 写内置定义哈希（不为 NULL） | 能拿到 `builtin_spec(...).spec_version`；spec §4.2.5 允许二者择一，选信息量更大的 |
| R5 | T4 的 wiring 第一条用例在 T5 时改成默认 app，覆盖真实 `bind_run_service` | T4 时门面还没有该方法，只能用可绑定替身；T5 补齐后改用真实装配 |
| R6 | T6 在 §6 的 8 条用例外多加了 `test_run_routes_report_503_without_a_service` | 覆盖 §4.3.2 的 503 分支；不改既有断言，属追加 |
| R7 | T3 的 `test_list_runs_filters_by_session_and_root_only` 先 finish 根运行再建同会话子运行 | 部分唯一索引禁止同会话两条活动运行；断言与 spec 相同，仅建数据顺序不同 |
| R8 | 手动检查三项记「未验证」 | 本会话没有浏览器/真机；恢复与状态机由单测 + 冒烟覆盖，spec §7 允许做不了就写未验证 |
| R9 | 错误码与 §4 表在 T2 一次做，T6 只做 §6 路由 | spec 的 T2/T6 分工；避免重复改 |

## 偏离（已记 13 §8.12–8.19）

- §8.12 运行生命周期归发起方（A2）。
- §8.13 重启恢复顺序：先补 tool 桩再写 system 提示；喂模型转 `【系统】` user。
- §8.14 用户消息不写 run_id，运行用 trigger_seq 指向它。
- §8.15 `agent.run_stale_seconds` 的用途与阈值（≥3 倍心跳）。
- §8.16 `agent_run.finish_reason` 宽 32、测试按表例外。
- §8.17 侦察子代理与会话标题不记运行（M70 再统一）。
- §8.18 `RUN_ALREADY_ACTIVE` 与 `AGENT_SESSION_BUSY` 并存（A1）。
- §8.19 `_LegacyRunTracker` 先切状态、再转发事件。

## 遗留

- spec §7 的三项手动检查（本机服务看运行、确认卡时 `waiting_human`、重启后 `interrupted/recovered_after_restart` 与恢复提示、再发一条能答）**未验证**；单测与冒烟覆盖了同逻辑，但真实浏览器/设备链路仍建议用户手动过一遍。
- 侦察子代理与会话标题不记运行（13 §8.17），M70 的通用委派再统一。
- `finish` 写库失败只 warning、孤儿在下次 `acquire_session`/`start` 时收尾；本进程心跳停了但任务还在的运行留给 M83 Watchdog。
- 前端无界面改动，只重生 `web/openapi.json` 与 `web/src/types/api.d.ts`。
