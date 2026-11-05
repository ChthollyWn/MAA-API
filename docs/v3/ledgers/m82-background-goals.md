# M82 后台工单与目标模式（台账）

- 分支：`background-goals`；基点：`dev` = `561ef13`；合并：`dev` 上的 `merge: 后台工单与目标模式（M82，见 docs/v3/13 §8.132–8.142）`；tag：`v3-m82`
- spec：[m82-background-goals](../specs/m82-background-goals.md)（606 行）
- 计划：[m82-background-goals](../plans/m82-background-goals.md)
- 决策记录：[docs/v3/13 §8.132–8.142](../13-决策记录.md)
- 迁移：`0035_goals`（可逆；降级删目标/里程碑，养成计划与定时任务保留）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `0df88eb` + `f8e900e` | 计划入库；§0 1–14；用户确认「显式委托 + goal-review auto」；6 项接缝裁决；基线 `3 failed / 4997 passed` |
| G1（T1+T2） | `c4bb3f3` | `GoalStatus`/`MilestoneStatus`；`goal`/`goal_milestone` 两表 + `ticket.goal_id`（循环外键）；迁移 0035；`GoalsSettings` 两键；错误码 162/161；两仓储 + 工单 `list_by_goal` |
| G2（T3） | `1fa5d55` | `agent/goals/{models,service}.py`（输入/响应模型、`planning_brief/scope`、`GoalService` 全部方法：create/update/review/暂停恢复取消/`_apply_plan`/`_apply_review`/快照/完成判定/复盘 job）；建单入口 `goal_id` |
| G3（T4+T5） | `50117ae` | 三个 goal 事件（15 种）、`goal-review` 规则 + `goal_review_draft`（6 条）、bus 传 `goal_id`；`TicketService.add_report_listener`（commit 后后台任务）；`TicketPushNotifier` |
| G4（T6） | `3b9382e` | 四目标工具（组 goals/不可 REST 见）；`policy` 两条规则（create 放行/update 恢复 CONSUME + fail closed）；总控 tools 24/version 6；工具 104/103 |
| G5（T7） | `230b4f6` | 四端点（201+Location/错误码/分页/Z）；装配 GoalService/TicketPushNotifier/两汇报监听/jobs/两处 close+delattr；14 号文档 176/agent 73 |
| G6（T8） | `4ec76c9` | 目标列表/详情/新建；三分页签、进度/快照/notes/工单、暂停恢复取消、cron 换算、30s 轮询、`ticket_status` 失效；前端 108 文件 **953** 用例 |
| G7（T9） | `874cd80` | 冒烟 `[ok] goals`；`orchestrator_create_goal` 手写 cassette 的 pytest 级回放 |
| G8 | 本台账所在提交 | 02/03/05/11/13/16/README/接缝速查同步；13 §8.132–8.142；本台账；全量验收与合并 |

## 核心实现

- **目标**：`goal`（标题/说明/目标快照/截止/状态/规划工单/养成计划/定时任务/复盘 cron/进度）与 `goal_milestone`；`ticket.goal_id` 与规划工单循环外键。
- **建立**：`create_goal`/POST → 建 `goal`（progress.planning=pending）→ 建 `planner` 规划工单（`origin=goal`、**显式委托**、上限 consume、`goal_id` 回指、订阅 `goal:<id>`）→ 注册复盘 cron job；建单失败保留目标并标 planning=failed。
- **规划结果**：规划工单汇报 ready 时校验 `details.goal_plan` → 整体替换里程碑（第一条 active）→ 关联 Worker 会话建的养成计划与定时任务（不存在的定时任务丢弃并记 notes）；`failed/dropped/invalid_details` 记状态。
- **复盘**：每个 active 目标按 `review_cron` 复盘——确定性把逾期里程碑标 missed、判断完成（计划 completed + 里程碑全完成）、过截止只发一次 `goal.deadline_passed`、发 `goal.review_due`（按分钟去重）→ `goal-review`（auto）研判后建推断委托复盘工单；复盘汇报可更新里程碑或完成目标。
- **状态联动**：完成/暂停/恢复/取消分别联动定时任务（停用/启用/恢复）、养成计划（取消）、复盘 job（pause/resume/remove）、目标未结束工单（取消）；`GOAL_ENDED` 保护。
- **后台推送**：`TicketPushNotifier` 在无可见页面时对 ready/blocked/failed/需决定的 waiting 推 Web Push（同工单 600s 限流、waiting ref 去重、confirmation 不推）。
- **前端**：目标三分页签、详情里程碑/进度/相关工单/notes、新建对话框（cron 换算/只读 fallback）、暂停/恢复/取消确认与 Toast。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | db/domain/settings 986 passed；迁移 SQL 含 goal_milestone；`app ok`；错误码 162/161、表 51 |
| G2 | goals 20 passed；tickets/events 107 passed；`upgrade_plan.session_id` 口径实测同值 |
| G3 | events/tickets/goals 134 passed；KNOWLED_KINDS 15、BUILTIN_RULES 6 |
| G4 | 目录/工具/策略/定义 163 passed；工具 104/103；`agent_smoke` OK |
| G5 | 路由/装配/路由清单 20 passed；openapi/genapi 0；`orchestrator_smoke` SMOKE OK |
| G6 | gen/typecheck/vitest（108 文件 953 用例）/build/frontend_smoke 全 0 |
| G7 | smoke `[ok] goals` 1 次且 `SMOKE OK`；regression/capability 6/6；goals+evals 76 passed |

### 最终验收（G8，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | **3 failed / 5051 passed**；与基线同一组真资源失败，无新增 |
| `scripts/dump_openapi.py --check` | 0（176 operations） |
| `scripts/generate_api_guide.py --check` | 0（新端点后重新生成） |
| 前端五步 | 0；vitest 108 文件 **953** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke` | 均 `SMOKE OK`（orchestrator 含 `[ok] goals`） |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 手动验收（真实总控建目标/复盘事件/后台推送） | **未验证**（无常驻前台服务/真实总控）；自动用例与冒烟覆盖等价逻辑 |

### 计数

- 错误码 160/159→**162/161**；端点 172→**176**（agent 69→73）；业务表 49→**51**；工具 100/99→**104/103**；总控定义 version 5→**6**；配置键 +2（`goals.*`）；迁移 head **`0035_goals`**。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 规划工单显式委托 + goal-review auto | 用户 2026-11-05 明确确认（13 §8.132） |
| R2 | 建单返回二元组解包；mandates 允许 goal explicit；规划工单 source=goal | 实际契约（13 §8.133） |
| R3 | 汇报监听入口 `record_report`、commit 后转发前 | 实际落库点（13 §8.134） |
| R4 | 养成计划按 session_id + Worker 会话回退匹配 | 实测同值，回退兜底（13 §8.135） |
| R5 | 15 事件/6 规则；goal 话题 UUID | 扩展与校验（13 §8.136） |
| R6 | 推送限流/去重/confirmation 不推 | spec §4.5.6（13 §8.137） |
| R7 | 目标工具/策略/总控定义 | 计数与 fail closed（13 §8.138） |
| R8 | 端点/装配/计数 | 13 §8.139 |
| R9 | 前端 cron 换算与只读 fallback | 13 §8.140 |
| R10 | 冒烟/评测（pytest 级 cassette） | harness 限制（13 §8.141） |

## 偏离（已记 13 §8.132–8.142）

- §8.132 两处设计确认；§8.133 建单/委托适配；§8.134 汇报监听；§8.135 计划关联；§8.136 事件规则扩展；§8.137 推送；§8.138 工具策略；§8.139 端点装配计数；§8.140 前端；§8.141 冒烟评测；§8.142 文档与契约。

## 遗留

- 真实环境手动验收未做（真实总控说「月底前把银灰练到精二」→ create_goal/规划工单、两分钟后复盘事件、无可见页面的 Web Push）；自动用例与冒烟覆盖等价逻辑。
- M68 评测 harness 无 orchestrator 驱动，`orchestrator_create_goal` 只在 pytest 级回放（手写 cassette），regression 仍 6 场景。
- 后台推送依赖 VAPID 配置与真实浏览器/PWA；未做端到端推送验证。
- 复盘研判花费受 `triggers.eval_daily_limit` 与 M67 预算限制，真实模型误判率未评估。
