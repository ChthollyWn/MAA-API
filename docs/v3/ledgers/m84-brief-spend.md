# M84 每日简报与消耗护栏（台账）

- 分支：`brief-spend`；基点：`dev` = `14be1d6`；合并：`dev` 上的 `merge: 每日简报与消耗护栏（M84，见 docs/v3/13 §8.153–8.162）`；tag：`v3-m84`
- spec：[m84-brief-spend](../specs/m84-brief-spend.md)（881 行）
- 计划：[m84-brief-spend](../plans/m84-brief-spend.md)
- 决策记录：[docs/v3/13 §8.153–8.162](../13-决策记录.md)
- 迁移：`0037_spend_ledger`（可逆；降级只删账本表）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `1cc027a` | 计划入库；§0 1–9/12–18 符合（#10/#11 接缝名不符）；用户确认 R1–R6/B1/Q4/Q7；基线 3 条真资源失败 |
| G1（T1+T2） | `f2fbe37` | `SpendKind`；`spend_ledger` 表 + 迁移 0037；`SPEND_CAP_EXCEEDED` 403（164/163）；`BriefSettings` 四键 + `SpendSettings` 三键 + 模板/测试；14 号文档 |
| G2（T3） | `f293219` | `agent/spend.py`：计划量/日界/用量/判定/预检/预留/改参差额/汇总/拦截记录（rejected 审计）；17 用例 |
| G3（T4） | `480800d` | 队列 `bind_spend_guard`+入队预留（幂等不重复记账）；runner 改参差额；工具传 AGENT；定时任务 `skipped`/立即执行抛；`invoke_tool` 预检 403 |
| G4（T5+T6） | `8985170` | `SpendMiddleware`（Mandate 后/Lease 前、超限审计+error 不弹确认）；简报 `facts.py`/`render.py`（六类待处理、理智估算、模板逐字、4000 字截断） |
| G5（T7） | `1b81eae` | `BriefService`（到点/手动/每日一张/30 秒巡检/30 分钟超时/推送）；`ArtifactService.update` |
| G6（T8） | `e95ac03` | `GET /api/agent/spend`、`POST /api/agent/brief/run`（181/agent 78）；装配 guard/brief/调度/两处关停；01 openapi/类型 |
| G7（T9） | `5554026` | `/more/spend` 页面、导航、待你处理 spend 汇总、设置页「生成今日简报」；前端 111 文件 **965** 用例 |
| G8（T10） | `d60dc96` | `scripts/brief_spend_smoke.py`（六步、`SMOKE OK`） |
| G9 | `dac0c4e`/`fix`/本台账提交 | 测试文件改名消除模块冲突；守卫绑定对替身可选；文档同步；13 §8.153–8.162；本台账；全量验收与合并 |

## 核心实现

- **计划量**：Fight 的 `medicine`/`stone`（不读 `expiring_medicine`）；Recruit 仅 `expedite=true` 时 `times`（不读 `expedite_times`）；非正 int/布尔按 0；只记 >0。日界北京时间 04:00；用量按 `actual`（不写）→ 终态且未开始=0 → `planned`；所有来源计入，只有 agent/scheduled 受限。
- **护栏**：`SpendGuard` 持最内层锁；预检（工具调用前，只读、失败放行、被拦写 rejected 审计）、入队预留（与流水线同一事务、幂等不重复）、改参差额（只计调高、回滚随内核拒绝）；上限 -1/0/正数；任一超额整条拒绝并写拦截记录。
- **接入**：`SpendMiddleware`（Mandate 后 Lease 前）与 REST `invoke_tool` 预检；队列、runner、工具、定时任务全部接上；未绑定守卫时保持 v2 行为。
- **简报**：`BriefService` 每日 `brief.cron` 直接建 analyst 工单（不经事件规则），事实（理智估算/定时任务/活动与卡池/养成与目标/昨天/消耗/六类待处理）落 `report` 产物；汇报 ready 写建议、超时收 `facts_only`；简报全文逐字模板；无可见页面才推 Web Push。
- **接口/前端**：`GET /api/agent/spend`（近 7 天/指定 since）、`POST /api/agent/brief/run`（202）；`/more/spend` 页、待你处理汇总行、设置页生成简报。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | db/domain/settings 1014 passed；迁移 SQL 含 spend_ledger；七个设置默认值正确 |
| G2 | spend 17 passed（计划量/日界/用量/预留/并发/预检/汇总） |
| G3 | queue/schedule/invoke/runner 18 passed；services 全量仅 3 条真资源失败 |
| G4 | brief+harness+workers 121 passed；链顺序 Mandate→Spend→Lease |
| G5 | brief+artifacts 29 passed；每日一张/超时/推送条件 |
| G6 | 路由/装配/domain 508 passed；openapi/gen 0；两路径齐；orchestrator 冒烟 OK |
| G7 | gen/typecheck/vitest（111 文件 965 用例）/build/frontend_smoke 全 0 |
| G8 | brief_spend_smoke 6 个 `[ok]` + `SMOKE OK` |

### 最终验收（G9，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | **3 failed / 5170 passed**（修掉替身绑定问题后）；与基线同一组真资源失败，无新增 |
| `scripts/dump_openapi.py --check` | 0（181 operations） |
| `scripts/generate_api_guide.py --check` | 0（新端点后重新生成） |
| 前端五步 | 0；vitest 111 文件 **965** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke` / `brief_spend_smoke` | 均 `SMOKE OK` |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 手动验收（设置页加急上限后总控提交被拦、设置页生成简报） | **未验证**（无常驻前台服务/真实总控）；自动用例与四个冒烟覆盖等价逻辑 |

### 计数

- 错误码 163/162→**164/163**；端点 179→**181**（agent 76→78）；业务表 52→**53**；配置键 +7；工具/规则/定义数不变；迁移 head **`0037_spend_ledger`**。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 用户确认 R1–R6/B1/Q4/Q7 按 spec | 2026-11-05 用户选择（13 §8.153） |
| R2 | §0 #10/#11 以实际接缝为准；guard 自带 rejected 审计 | Mandate 不写审计（13 §8.154） |
| R3 | 产物 `put(body=bytes)`；新增 `update(content, meta)` | 实际契约（13 §8.155） |
| R4 | 计数与文档同步 | 13 §8.156 |
| R5 | 三检查点落法（预检/入队/差额） | 13 §8.157 |
| R6 | 简报流程与收尾/推送 | 13 §8.158 |
| R7 | 前端页面/汇总/设置控制 | 13 §8.159 |
| R8 | 冒烟脚本六步 | 13 §8.160 |
| R9 | 测试文件改名消除同名冲突 | 13 §8.161 |
| R10 | 通知/IM 边界维持 v2 | 13 §8.162 |

## 偏离（已记 13 §8.153–8.162）

- §8.153 用户确认；§8.154 接缝修正与自写审计；§8.155 产物/正文；§8.156 计数文档；§8.157 检查点；§8.158 简报流程；§8.159 前端；§8.160 冒烟；§8.161 测试改名；§8.162 通知边界。
- 另：`main.py` 的 `bind_spend_guard` 改为 `getattr` 可选调用（测试用替身流水线执行器没有该方法），属实现细节，接入 G9 提交。

## 遗留

- 真实环境手动验收未做（设置页改上限后总控公招加急被拦、待你处理汇总、设置页生成简报与推送）；自动用例与四个冒烟覆盖等价逻辑。
- `actual` 不回填，用量按参数上限计（口径已写页面/简报/文档）；真实内核回调下的实际用量未校验。
- 简报的 analyst 真实模型输出质量未评估（FakeLLM 只验证格式与收尾路径）。
- 排队期间调低上限不影响已入队流水线（R6 的既定代价）。
- `brief.push_im` 只保存不生效，等 M85 接入 IM。
