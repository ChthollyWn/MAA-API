# M75 工作记忆、外置与计划模式（台账）

- 分支：`workspace-plan`；基点：`dev` = `34ffd3f`；合并：`dev` 上的 `merge: 工作记忆、外置与计划模式（M75，见 docs/v3/13 §8.65–8.72）`；tag：`v3-m75`
- spec：[m75-workspace-plan](../specs/m75-workspace-plan.md)（287 行）
- 计划：[m75-workspace-plan](../plans/m75-workspace-plan.md)
- 决策记录：[docs/v3/13 §8.65–8.72](../13-决策记录.md)
- 迁移：`0029_artifacts_plans`（可逆）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `079a92b` | 计划入库；§0 七条；19 处规格/布局差异裁决（路径、链位、钩子、保留、配置、计数） |
| G1（T1） | `b743cab` | 四个枚举；`Artifact`/`RunPlan` 表与 `0029` 迁移（含部分唯一索引）；4 个错误码（`ARTIFACT_*`/`PLAN_*`，146/145） |
| G2（T2） | `2bed746` | `ArtifactService`（256 KiB DB / 文件 `.part`+`os.replace` / >16 MiB 413 / sha256 / pin / cleanup）；`SpillMiddleware`（名单/无 `read_artifact` 不外置/阈值/指针/preview/同时替换 content+result）；`read_artifact`/`write_note` 工具 |
| G3（T3） | `754ad22` | `PlanService`（状态机/8 位 hex 步骤 id/同刻一个 in_progress）；`PlanMiddleware`（批准前白名单在策略之前、`PLAN_APPROVAL_REQUIRED`、尾部 plan 段、`pending_human_action`）；`update_plan` 工具（96/95）；两个计划端点；`ensure_report` 待批准改发 waiting |
| G4（T4） | `56423c2` | 产物四个端点（152 端点）；三个配置键（`context.spill_threshold_chars`、`PlanSettings`）；尾部 `notes` 段；保留并入 `run_retention`（`artifacts_deleted`）；事件生产 `artifact_created`/`plan_updated` |
| G5（T5） | `6c8d926` | 前端 `PlanPanel`（批准/退回，请求中禁用、原因非空）与 `ArtifactList`（抽屉/截断下载）；事件失效；`common.md` 三句；7 个 Worker 加三工具并 version+1 |
| T5b | `b560ed3` | §7 两条脚本化验收：20000 字工具结果 → 下一次请求为指针（含 artifact_id、不含全文）；4 步计划批准前 `submit_pipeline` 被拦、批准后成功 |
| G6 | 本台账所在提交 | 文档同步（02/03/05/13/15）、README、本台账；全量验收与合并 |

## 核心实现

- **外置**：工具成功后 `SpillMiddleware` 按 06 §6.1 名单/阈值判断；指针 `{spilled, artifact_id, tool, total_chars, preview(前 3000+后 1000), next_offset=3000, hint}`；替换 `ToolOutcome.content` 与 `.result`，审计由内层先记录（既有 2 KB 截断契约保留，锁「原文前缀而非指针」）。
- **产物**：≤256 KiB 存 DB（`content`），更大写 `resource/artifacts/<id>`（`.part`+`os.replace`）；>16 MiB 413；`read_artifact` 按 offset/limit 续读、末尾 `next_offset=null`、跨工单 404；`write_note` 新建/覆盖、>20000 字工具错误 `note_too_long`。
- **计划**：4 步（`plan.complex_min_steps` 默认 4）待批准，3 步直接 active；只改状态/备注不重审，新增步骤/改标题重审，全部 done/cancelled → done，工单结束 → abandoned；同刻最多一个 in_progress；待批准时工具结果带 `human_action`、本轮结束由 `ensure_report` 发一条 waiting 汇报；批准/退回写 Worker 收件箱并唤醒。
- **尾部**：`plan` 段（`PlanMiddleware.before_model`）与 `notes` 段（`DurableContextMiddleware`，只列 `{id,title}`），无则不插。
- **事件**：`artifact_created`（put）、`plan_updated`（状态变更），前端按会话 plan / 产物列表前缀失效。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | `tests/db tests/domain` 849 passed；`create_app()` ok |
| G2 | spill/artifacts/tool_catalog/链序 38 passed；parity 17；计数 95/94 |
| G3 | plans/路由/workers 48 passed；`tests/agent` 1112 passed；parity 17；96/95 |
| G4 | workspace/tail/artifacts/retention/settings 74 passed；`tests/api tests/agent` 1664 passed；openapi/genapi 0 |
| G5 | 定义/工具 20 passed；typecheck 0；tickets 25 passed；全 web 99 文件 893 用例 |
| T5b | 脚本化验收 1 passed；spill/plans 16 passed；未发现生产缺陷 |

### 最终验收（G6，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | 见「合并后复验」；与 dev 起点（3 failed / 4515 passed）相比不新增失败 |
| `scripts/dump_openapi.py --check` | 0（152 operations） |
| `scripts/generate_api_guide.py --check` | 0（新端点后重新生成） |
| 前端五步 | 0；vitest 99 文件 **893** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke` | 均 `SMOKE OK` |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 手动验收（真实 LLM 长输出外置、计划批准） | **未验证**（无常驻服务/真实 LLM）；脚本化用例覆盖等价链路 |

### 计数

- 业务表 39→**41**（`artifact`、`run_plan`）；错误码 142/141→**146/145**（+4）；HTTP 端点 146→**152**（+6）；配置键 +3；工具 93→**96**（模型可见 92→**95**，48 可重取不变）；定义 16 不变（7 个 Worker version+1）；迁移 head **`0029_artifacts_plans`**。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 路径按实际仓库布局改写 | spec 写的是计划中的目录结构（13 §8.65） |
| R2 | 外置同时替换 content 与 result | SSE 契约；审计按既有 2 KB 截断（13 §8.66） |
| R3 | 待批准汇报由 `ensure_report` 改发 waiting（只一条） | `on_run_end` 逆序，避免 progress+waiting 双报（13 §8.67） |
| R4 | 保留并入 `run_retention` 而非独立 job | 避免双口径（13 §8.68） |
| R5 | 配置键落点（ContextSettings + 新 PlanSettings） | 04 §6 键名与分组（13 §8.69） |
| R6 | 子代理与总控定义不动，只改 7 个 Worker | §4.7 目标语义（13 §8.70） |
| R7 | 事件生产与前端按前缀失效 | 载荷无 ticket_id 的合理选择（13 §8.71） |
| R8 | 定义 version/工具元组与计数联动同步测试 | 事实变化（13 §8.72） |
| R9 | 真实 LLM 手动验收记「未验证」 | 无服务/真实 key；脚本化验收覆盖 |

## 偏离（已记 13 §8.65–8.72）

- §8.65 布局适配；§8.66 外置替换 content+result 与审计截断；§8.67 待批准汇报协作；§8.68 保留并入；§8.69 配置落点；§8.70 子代理不动；§8.71 事件与前端失效；§8.72 版本与计数联动。

## 遗留

- 真实 LLM 手动验收未做（长输出外置后模型按指针续读、计划批准的真实交互）；脚本化用例覆盖等价链路。
- 前端 `artifact_created` 无 ticket_id，只能整片失效产物列表（数据量增大后可优化为按会话/工单精确失效）。
- `PLAN_APPROVAL_REQUIRED` 仅作工具结果字符串，未进 `ErrorCode`（按 spec §9）。
