# M81 事件源与两级过滤（台账）

- 分支：`triggers`；基点：`dev` = `65c30b2`；合并：`dev` 上的 `merge: 事件源与两级过滤（M81，见 docs/v3/13 §8.121–8.131）`；tag：`v3-m81`
- spec：[m81-triggers](../specs/m81-triggers.md)（899 行）
- 计划：[m81-triggers](../plans/m81-triggers.md)
- 决策记录：[docs/v3/13 §8.121–8.131](../13-决策记录.md)
- 迁移：`0034_triggers`（可逆；降级丢事件/规则覆盖/观察记录）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `23a2891` | 计划入库；§0 1–14；10 项接缝裁决；基线 `3 failed / 4902 passed` |
| G1（T1+T2） | `ab2f904` | 四枚举；三错误码（160/159）；`TriggersSettings` 三键；`agent_trigger`/`trigger_rule`/`trigger_observation` + `ticket.source_trigger_id`；迁移 0034；三仓储 |
| G2（T3+T4） | `b8b9f6e` | `kinds.py`（12 种事件/四校验/摘要/attention）、`rules.py`（合并/条件/校验/统计）、`builtin_rules.py`（5 条内置 + 草稿）、`evaluator.py`（trigger_eval 运行/事件卡）；`trigger_evaluator.md` 与 `trigger_decision` schema；定义 18 |
| G3（T5） | `f3b02f2` | `TriggerService`（publish 锁/dedupe/rejected → `_process` 六步 → fallback/建单/交总控/观察）；建单入口五参；收件箱幂等与三行 trigger 渲染；`deliver_to_subscribers` |
| G4（T6） | `b5c69a9` | `wiring.py`（Pipeline/Device/Core/Update/Sanity）；FinishHookFanout 换 triggers；incidents 建单改发事件；main 装配/recover/start_jobs/两处 close；M73 16 用例新旧对照 |
| G5（T7+T8） | `998a4fd` | 定时规则 job 增删/`_fire_rule`/理智 30 分钟 job；retention 两新键；6 个 REST 端点（172/agent 69）；OpenAPI/类型 |
| G6（T9） | `4047d5d` | 事件规则页三分页签（规则/待审阅/事件记录）、RuleEditor、建议标签/统计/gate hint、`observation_created` 失效；前端 107 文件 **950** 用例 |
| G7（T10） | `69c4d3a` | `orchestrator_smoke` 新增 `[ok] triggers`；三条 `trigger_eval_*` 场景用手写 cassette 的 pytest 级回放 |
| G8 | 本台账所在提交 | 02/03/04/11/13/16/README 同步；`agent_smoke` 适配；13 §8.121–8.131；本台账；全量验收与合并 |

## 核心实现

- **事件入口**：`TriggerService.publish` 唯一入表；每话题 asyncio 锁 + `dedupe_key` 唯一索引；不合法事件（未知 kind/来源不符/前缀不符/payload>16KiB）单独入表 `rejected`；`recover` 启动补处理 24 小时内未完成事件、超期置 `expired_after_restart`。
- **两级过滤**：订阅直通（Worker 收件箱/唤醒）→ 规则（`RuleSet.match`：内置优先、用户按 id、来源/种类/条件/gate）→ 冷却 → 研判（`trigger_eval` 运行、`trigger_decision` schema、每日限额、信号量 2）→ off/observe/auto 分支；无法处理时按「交总控」集合；所有终点写一次 `finish`。
- **内置规则**：`diagnose-pipeline-failure`/`diagnose-core-crash`（auto、不研判、受 `agent.auto_diagnose_failures` 总闸）、`sanity-full`/`device-unavailable`/`update-available`（observe，带冷却与研判提示）。
- **观察与统计**：observe 写 `trigger_observation` 并广播 `observation_created`；规则列表 30 天统计与升/降档建议（good 比例、坏结局比例）。
- **接线**：FinishHookFanout→publisher（含提交者工单幂等补订阅）、core crash→`core.crashed`、core failed→`core.failed`、设备状态变化→三类事件、更新广播→`system.update_available`、理智估算 30 分钟 job、定时规则 cron job。
- **前端**：三分页签、切档 PUT 完整内容、覆盖恢复默认/删除、观察审阅（判断对了/不该处理）、事件记录去向徽章、空态。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | db/domain/settings 969 passed；迁移 SQL 含 agent_trigger；`app ok` |
| G2 | events/specs/specs-router 75 passed；KNOWN_KINDS 12、BUILTIN_RULES 5；定义 18；`agent_smoke` OK |
| G3 | bus 17 passed；tickets/orchestrator 64 passed；events 40 passed；`tests/agent` 1403 passed |
| G4 | wiring/incidents/assembly 31 passed；`tests/agent` 1411 passed；`orchestrator_smoke` SMOKE OK |
| G5 | jobs/retention/router/route_inventory 33 passed；openapi/genapi 0；agent 69/total 172 |
| G6 | gen/typecheck/vitest（107 文件 950 用例）/build/frontend_smoke 全 0 |
| G7 | smoke `[ok] triggers` 1 次且 `SMOKE OK`；regression 6/6；events 53 passed |

### 最终验收（G8，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | **3 failed / 4997 passed**；与基线同一组真资源失败，无新增 |
| `scripts/dump_openapi.py --check` | 0（172 operations） |
| `scripts/generate_api_guide.py --check` | 0（新端点后重新生成） |
| 前端五步 | 0；vitest 107 文件 **950** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke` | 均 `SMOKE OK`（orchestrator 含 `[ok] triggers`；agent_smoke 为 M81 适配后重跑） |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（regression 6/6、capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 手动验收（真实失败流水线/事件页操作/定时规则） | **未验证**（无常驻前台服务）；自动用例与冒烟覆盖等价逻辑 |

### 计数

- 错误码 157/156→**160/159**；端点 166→**172**（agent 63→69）；业务表 46→**49**；代理定义 17→**18**；配置键 +3（`triggers.*`）；工具不变（100/99）；迁移 head **`0034_triggers`**。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | `deliver_to_subscribers` 做薄封装；events 包落点 | 仓库已有等价 `deliver`（13 §8.121） |
| R2 | 建单入口加五个可选参数 + 惰性 worker provider | 推断委托与 brief 需要（13 §8.122） |
| R3 | 收件箱加 ref 幂等查询；trigger 渲染拆三行 | 恢复与格式（13 §8.123） |
| R4 | 诊断服务接 boot_id | 跨重启去重（13 §8.124） |
| R5 | 两处 M73 用例意图变化 | dedupe 与订阅直通（13 §8.125） |
| R6 | PipelineEventPublisher 补提交者订阅 | 保 M70 竞态兜底（13 §8.126） |
| R7 | 规则 DTO/内置 title/诊断草稿落点 | 实现细节（13 §8.127） |
| R8 | 端点直读仓储 | T8 写范围（13 §8.128） |
| R9 | 前端新建规则必填 id 等 | spec 字段表遗漏（13 §8.129） |
| R10 | 冒烟/评测适配与 cassette 结论 | 测试设施（13 §8.131） |

## 偏离（已记 13 §8.121–8.131）

- §8.121 名字/包落点；§8.122 建单入口；§8.123 收件箱；§8.124 boot_id；§8.125 M73 意图变化；§8.126 提交者订阅兜底；§8.127 DTO/内置规则/草稿；§8.128 端点直读仓储；§8.129 前端；§8.130 计数/文档；§8.131 冒烟/评测。

## 遗留

- 真实环境手动验收未做（真实失败流水线 → 事件记录/诊断工单、事件页切档与审阅、cron 定时规则）；自动用例与冒烟覆盖等价逻辑。
- M68 评测 harness 没有事件研判入口/判分项，三条 `trigger_eval_*` 场景只在 pytest 级回放；扩展 harness 留待后续里程碑或 M98 实验框架。
- 研判花费依赖 `trigger_eval` 模型档案与每日上限；真实模型下的误判率未评估。
- 全量满载时 `test_incidents`（60s 等待）偶发超时，单跑/重跑通过。
