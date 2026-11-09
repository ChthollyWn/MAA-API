# M87 验证闸门与作业研究闭环 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Worker 的 `ready` 加一道机器可核的完成闸门（只统计本工单 Worker 自己的写设备证据，验证员复核兜底，两次不通过后保留用户在提问卡上的放行），并让 `copilot` 工单按五步循环推进、每份作业授权最多 `copilot_loop.max_iterations` 次、用完自动换新一轮，最后用 6 个评测场景把放行、拒绝、复核、放行豁免与两种循环结局钉住。

**Architecture:** 闸门是新模块 `maa_api/agent/workers/verification.py`，挂在 `WorkerService.submit_report` 调 `record_report` 之前，拒绝时返回 `accepted=false` 的工具结果并由 `ReportMiddleware` 识别（不结束运行、不写汇报）；验证员结论由 `DelegationService` 写进子运行 `agent_run.meta["verdict"]`。作业研究侧只改策略/确认/授权服务三处：工单会话预填并夹住 `max_iterations`、旧授权 `remaining==0` 时落 `exhausted` 并弹新卡、批准时夹住用户改大的次数。评测只写 6 个 `entry.kind=ticket` 场景与手写 cassette，驱动由已合入的维护性补丁提供。

**Tech Stack:** FastAPI + SQLModel/Alembic + pytest（异步用例用 `asyncio.run(scenario())`）；React 19 + TypeScript + TanStack Query + Vitest；评测是 JSON-in-YAML 场景 + JSONL 手写 cassette + `httpx.AsyncBaseTransport` 回放。

**Spec:** `docs/v3/specs/m87-verifier-copilot-loop.md`（929 行；计划论据全部出自它，执行者两者一起读）

## Global Constraints

- 分支 `verifier-copilot-loop`，从 `dev`（M86 合并后的提交）新建；完成后 `git merge --no-ff` 回 `dev`，打 tag `v3-m87`，删分支；**不 push**。
- 不使用 worktree；`.venv/`、`config.yaml`、`resource/`、`web/node_modules/` 不进版本库。
- 不新增工具、表、端点、错误码、迁移、事件、中间件位置、依赖；不改 `run_copilot_attempt` 的最长阻塞（`agent.copilot_attempt_timeout_minutes`，默认 20 分钟）；新代码只落在 spec §4.2 列出的文件。
- 计数（M86 合并后的基线，落地后不变）：工具 **107/106**、HTTP 端点 **186（agent 83）**、错误码 **171/170**（`ALL_CODE_COUNT`/`HTTP_BOUND_CODE_COUNT`，`SECTION_COUNT` 22）、业务表 **57**、事件 16。唯一的计数变化：配置键 **142/131 → 143/132**（`SETTING_KEYS`/`_FIELDS`）；评测场景 **13 → 19**；迁移 head 保持 **0041_nav_graph**。
- `verify_before_ready` 只有 `daily_ops`、`copilot` 为 true；`general` 与其余 Worker 为 false，`general.md` 正文与 `version` 一个字都不改。
- 闸门只落 `WorkerService.submit_report`（`maa_api/agent/workers/service.py:502`）调 `TicketService.record_report` 之前；`TicketService.record_report`（`maa_api/agent/tickets/service.py:607`）、重启恢复 `block_tickets_after_restart`（`:832`）、`ReportMiddleware.ensure_report` 的轮末补汇报都不过闸门；`VerificationGate(self._session_factory)` 现建，不新增 `app.state` 装配项。
- 被闸门拒绝的 `report`：`ToolOutcome.status="ok"`、`stop_run=False`、不写 `ticket_report`、工单状态不变、`agent_audit` 照常记这条 `report` 的 `success` 行；`ReportMiddleware` 不得写 `SUBMITTED_KEY`、不得设 `stop_run`/`stop_reason`。
- 闸门只统计本工单 Worker 自己的写设备审计：`agent_audit.session_id` 是本工单 Worker 会话、`agent_name` 等于工单 `kind`、`tool_name in EFFECT_TOOLS`、`status="success"`，且对应 `agent_run` 的 `session_id` 是该 Worker 会话、`agent_name` 等于 `kind`、`parent_run_id IS NULL`。导航员等子代理的点击不算工单效果。
- 机器证据只认两种：最后一次 `submit_pipeline` 之后本工单流水线全部 `completed`；本会话最近一次尝试 `success=true` 且 `judged_by="core"`。`judged_by="model"` 的补判必须验证员复核。
- 失败有界：同一批操作之后两次验证不通过 → `verification_exhausted`，只能 `failed`/`blocked`；保留用户放行（`ask_user` 选项含「仍按完成结束」，`details["verification_waiver"]` 带 `question_id` 或 `{"selection": "仍按完成结束"}`）。
- 授权：工单 Worker 会话里每份授权最多 `copilot_loop.max_iterations` 次；旧授权仍 `active` 且 `remaining==0` 时，下一次 `run_copilot_attempt` 由策略落 `exhausted` 并弹新卡；v2 聊天会话仍抛 `COPILOT_GRANT_EXHAUSTED`、旧行保持 `active`。
- `max_attempts` 只由服务端写进 `grant_copilot_attempts` 的确认 payload；用户 `overrides` 不接受它（`OverridesRequest` 的 `extra="forbid"` 已拒），只从 payload 读。
- 命令：一律 `.venv/bin/python`；本机没有 `rg`，用 `grep -nE`；pytest 带 `-o addopts= -q`；长输出写 `/tmp/m87/`，只看尾部与退出码；主 agent 复核时独占重跑。
- 文案检查只读：`scripts/copy_inventory.py --check` 会重写 `docs/copy-audit/*`，跑前跑后各留一次 `git status --porcelain docs/copy-audit`，不一致先 `git restore --source=HEAD -- docs/copy-audit`，不得把改写带进提交。
- 文档、提示词与提交信息用简体中文；提交风格 `feat(m87): …` / `fix(m87): …` / `docs(m87): …`；13 §8 的编号取落地时的下一空号，由主 agent 确认。
- 不实现评测驱动：`drive_ticket`、`Scenario.human`、`World.copilots`/`copilot_results`、seed 流水线的 `agent_session_id`、`CassetteTransport._issued` 由已合入的维护性补丁 `evals-ticket-driver` 提供；缺失即按 Task 0 停工，不在本里程碑补。

## 开工基线

- `dev` = M86 合并后的提交；Task 0 实测 `git rev-parse --short dev HEAD` 并记台账。tag：`v3-m85` 已在，`v3-m86` 必须在（`git tag -l 'v3-m86'` 有输出）；不在就等 M86，不开工。
- 迁移 head `0041_nav_graph`（M86 建 `nav_node`/`nav_edge`）；本里程碑「迁移：无」，不取下一个空号。
- 实测计数（M86 合并后预期）：工具 107/106、端点 186（agent 83）、错误码 171/170/22、业务表 57、配置 142/131、评测场景 13（11 个原场景 + M86 的 `nav-infrast-navigate-to`、`nav-unknown-stage-remember`；regression 5、capability 8）。spec §0 已声明「若实测不同，按当前代码与测试为准，偏差记台账」。
- `tests/evals` 的 passed 数与 `agent_eval` 的汇总在补丁 + M86 之后会变，**以 Task 0 实测为准**并记台账，作为本里程碑的对比基线；不要照抄 2026-11-09 的旧数字。
- `maa_api/agent/tools/nav.py` 已存在，`navigate_to`/`where_am_i`/`remember_screen` 已注册；`navigator.md` 的 `version` 是 2。M86 台账冻结这三个工具的契约，Task 6 第 2 步按实符号对齐提示词。
- `maa_api/agent/specs/builtin/copilot.md` 的 29/35/39 行仍是「执行前停在该关「开始行动」页」「次数、成功标准、助战以授权为准。用完就停…」「本里程碑的验证靠你自己看截图…」三句（M86 不改这个文件）；Task 6 按这三行删改，若 M86 合并后行号漂移，按句子内容定位，不按行号硬改。
- 维护性补丁已在 `dev`：`DRIVERS["ticket"]`、`Scenario.human`、`World.copilots`/`copilot_results`、`SeedPipeline.agent_session_id`、`CassetteTransport._issued` 都在；`tests/evals/cassettes` 仍是 11 份旧文件。

## §0 前置检查与接缝裁决

把 spec §0 的检查表逐条落成下表（第 1–16 条；命令已按「本机无 rg」写成 `grep`/`grep -nE`）。结果写进 `/tmp/m87/precheck.log` 与台账。

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在本分支、工作区干净 | `git status -sb` | 第一行 `## verifier-copilot-loop`，没有其他行 |
| 2 | M86 已合并 | `git tag -l 'v3-m86'` | `v3-m86` |
| 3 | 四个代理定义存在 | `grep -lE '^name: (verifier\|copilot\|daily_ops\|general)$' maa_api/agent/specs/builtin/*.md \| wc -l` | `4` |
| 4 | 验证员的输出 schema | `test -f maa_api/agent/llm/schemas/verifier_verdict.py && echo ok`；`grep -n 'register_schema("verifier_verdict"' maa_api/agent/llm/schemas/verifier_verdict.py` | `ok`；1 行 |
| 5 | 代理定义模型（M65） | `grep -nE '^class AgentSpecFrontmatter\|^class AgentSpec\(AgentSpecFrontmatter\)' maa_api/agent/specs/schema.py`；`grep -n 'extra="forbid"' maa_api/agent/specs/schema.py` | 2 行；2 行（YAML 头由 `AgentSpecFrontmatter` 校验） |
| 6 | 汇报中间件（M70） | `grep -rn "class ReportMiddleware" maa_api/agent` | 1 行（`maa_api/agent/workers/middleware.py`） |
| 7 | 汇报写入口 | `grep -n "async def submit_report" maa_api/agent/workers/service.py`；`grep -n "TicketReport(" maa_api/agent/tickets/service.py` | 1 行；2 行（`:607` 的 `record_report`、`:832` 的重启恢复） |
| 8 | 委派服务（M70） | `grep -n "class DelegationService" maa_api/agent/workers/delegation.py` | 1 行 |
| 9 | 工单相关列 | `.venv/bin/python -c "from maa_api.db.models import AgentAudit as A, AgentRun as R, AgentSession as S; print(hasattr(A,'ticket_id'), hasattr(A,'agent_name'), hasattr(R,'ticket_id'), hasattr(R,'parent_run_id'), hasattr(S,'parent_session_id'), hasattr(S,'ticket_id'), hasattr(R,'meta'))"` | `True True True True True True True` |
| 10 | 租约续租（M71） | `grep -n "async def renew" maa_api/agent/lease.py`；`grep -n "class LeaseMiddleware" maa_api/agent/lease.py`；`grep -n "_renew_loop" maa_api/agent/lease.py` | 各 1 行 |
| 11 | 导航工具（M86） | `grep -n '"where_am_i"' maa_api/agent/tools/nav.py`；`grep -n '"remember_screen"' maa_api/agent/tools/nav.py`；`grep -n '"navigate_to"' maa_api/agent/tools/nav.py` | 各 1 行 |
| 12 | v2 作业授权入口 | `grep -n "async def _evaluate_copilot_attempt" maa_api/agent/policy.py`；`grep -n "async def _apply_copilot_grant_overrides" maa_api/agent/confirmation.py`；`grep -n "^    async def approve" maa_api/services/copilot/grant.py` | 各 1 行 |
| 13 | 评测 ticket 驱动（补丁） | `grep -n "def drive_ticket" maa_api/agent/evals/runner.py`；`grep -n '"ticket": drive_ticket' maa_api/agent/evals/runner.py`；`grep -n "copilot_results" maa_api/agent/evals/scenario.py tests/evals/environment.py`；`grep -n "copilots" maa_api/agent/evals/scenario.py tests/evals/environment.py`；`grep -n "human: HumanPolicy" maa_api/agent/evals/scenario.py`；`grep -n "human.questions" maa_api/agent/evals/runner.py`；`grep -n 'entry.kind == "ticket"' maa_api/agent/evals/scenario.py`；`grep -n "agent_session_id" tests/evals/environment.py` | 1 行；1 行；至少 2 行；至少 2 行；1 行；至少 1 行；1 行；至少 1 行 |
| 14 | 评测场景与计数 | `ls tests/evals/scenarios/*.yaml \| wc -l`；`.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"`；`.venv/bin/python -c "from maa_api.settings import SETTING_KEYS; from maa_api.services.settings_schema import _FIELDS; print(len(SETTING_KEYS), len(_FIELDS))"` | `13`；`107 106`；`142 131` |
| 15 | 端点、错误码、表的基线 | `grep -n "共 186 条 HTTP 端点" docs/v3/14-现行接口清单.md`；`grep -n "（83 条）" docs/v3/14-现行接口清单.md`；`grep -n "^ALL_CODE_COUNT" tests/domain/test_errors.py`；`grep -n "^HTTP_BOUND_CODE_COUNT" tests/domain/test_errors.py`；`grep -n "^SECTION_COUNT" tests/domain/test_errors.py`；`grep -n "def test_metadata_has_exactly_57_business_tables" tests/db/test_models.py` | 各 1 行；错误码 171/170/22；表 57 |
| 16 | 迁移基线 | `ls maa_api/db/migrations/versions \| grep -E '^[0-9]{4}_' \| sort \| tail -n 1` | `0041_nav_graph.py` |

第 2 条不成立不要开工；第 13 条任一项不成立按 spec §10 停工并汇报，不在本里程碑实现驱动。第 15 条若与 spec 记的 186/83、171/170、57 不同，按当前代码与测试为准并记台账偏差（`tests/db/test_models.py` 的函数名历史上与集合大小不一致过，以 `BUSINESS_TABLES` 集合与断言为准）。

接缝裁决（实现时按这张表，不再另找入口）：

| 接缝 | 做法 | 不做什么 |
|---|---|---|
| `TicketReport` 写入点 | 闸门只加在 `submit_report` 里构造 `ReportInput` 之前；`:607` 的 `record_report` 与 `:832` 的 `block_tickets_after_restart` 都不改 | 不在 `TicketService` 里加闸门，不给 `record_report` 加 ctx/spec 参数 |
| `ReportMiddleware` | `outcome.status == "ok"` 且 `isinstance(outcome.result, Mapping)` 且 `result.get("accepted") is False` 时原样返回（`stop_run` 保持 `False`、`SUBMITTED_KEY` 保持 `None`）；其余照旧写 key 并设 `stop_run=True`/`stop_reason="report_submitted"` | 不改 `after_model` 与 `ensure_report` 的既有语义 |
| frontmatter 校验 | `verify_before_ready` 加在 `AgentSpecFrontmatter`（`AgentSpec` 继承，不重复声明）；`_FRONTMATTER_FIELDS` 由 `model_fields` 推导自动包含；`_check_field_rules` 加「`verify_before_ready and kind != "worker"` → `_invalid(..., "verify_before_ready", "只有 worker 可以为 true")`」 | 不加在 `AgentSpec`；不进 `/api/agent/specs` 的 wire（`DETAIL_FIELDS` 保持 17） |
| `ctx.spec` 替身 | `submit_report` 读标志用 `bool(getattr(ctx.spec, "verify_before_ready", False))`（`test_worker_service.py` 的 `_SPEC` 是 `SimpleNamespace`，没有字段） | 不直接 `ctx.spec.verify_before_ready`，不改既有用例的替身 |
| 旧授权仍 `active` | 只对工单 Worker 会话生效：`_ticket_worker_session` 判 `kind=="worker"`、`parent_session_id is None`、`ticket_id` 非空；命中时先 `mark_exhausted` 再走「没有授权」分支弹新卡；v2 聊天会话保持抛 `COPILOT_GRANT_EXHAUSTED` | 不改 `_active_copilot_grant`；不动 `grant.py` 的 `consume` |
| 次数判据 | 最后一次尝试看 `remaining == 0`（`grant_wire` 算的 attempts−used），不看 `grant_status=="exhausted"`（那一刻行还是 `active`） | 不在 `attempt.py` 里提前改状态 |
| 验证员结论 | 查询 `agent_run` 时按 `ticket_id == ticket.id`、`agent_name == "verifier"`、Python 里判 `isinstance(meta, Mapping) and isinstance(meta["verdict"], Mapping)`，按 `finished_at` 升序；不按 `run.status` 过滤 | 不写 JSON SQL，不递归 `parent_run_id` |
| 验证员步数 | `suggest()` 只列 ≤3 条流水线、≤3 条尝试、原子工具各一行，加固定句式压到 4000 字内，8 步够用 | 不改 verifier 的 `max_steps: 8`、不加工具（05 §3.3 不动） |
| 拒绝形状 | 用工具结果 `accepted=false`（`status="ok"`、`stop_run=False`）；不加错误码、不改 `ReportStatus` | 不抛 `AppError`（抛错会让本批失败、审计记 error） |
| 版本期望 | `test_builtin_workers.py` 的 `WORKERS` 加 `version`，值 `daily_ops 4`、`copilot 4`、`planner 3`、`diagnose 4`、`analyst 3`、`ops 3`；`test_agent_specs_router.py` 的 `EXPECTED_VERSIONS` 改 `daily_ops 4`、`copilot 4`、`verifier 2`，`general` 保持 6、`navigator` 2 | 不改 `test_m70_specs.py` 的断言（它没有版本常量） |
| 补汇报与「没有行」 | 「被拒的那次调用没有写行」只约束被拒的 `report`；`ensure_report` 轮末补的 `progress` 带 `details.auto=true`，不过闸门、不写 `verification` | 不断言「整个用例没有 `ticket_report` 行」 |
| 用户放行 | 保留：两次失败后 `ask_user` 的选项必须有「仍按完成结束」；字符串形状查 `agent_question`，对象形状按 `(session_id, selection, answered_at)` 反查最新一张已回答的卡 | 不新增放行配置键、不自动放行 |
| 评测驱动缺失 | Task 0 第 13 条不成立就停工；场景里不写 `world.seed` 带 `agent_session_id` 的变通来补闸门 | 不在 M87 里实现 `drive_ticket` 或改评测设施 |
| nav 契约漂移 | `navigate_to("stage:{代号}")`、`nearest`、`remember_screen(node_id=…, title=…)` 以 M86 合并后的代码为准；只有提示词里的名字/参数要改时，Task 6 按实符号改并记 13 §8 | 不改 M86 的工具代码；不因此停工（除非契约缺工具或参数语义变了） |
| cassette 时序 | 每条 ticket cassette：header → 2 条落点探查 `{"text":"OK"}` → 实质调用（Worker 与子代理按请求到达顺序交错）→ 结束于 `ready`/`failed`/`dropped` 的再加 1 条记忆抽取 `{"text":"{\"items\": []}"}`；`waiting`/`blocked` 不加 | 不打开 `--strict` 掩盖不一致，不用真实模型录制 |

## Review Focus

1. **闸门误拒**：只读工单、未开启字段的 Worker、非 `ready` 汇报、子代理点过界面但 Worker 只有只读审计——这些都必须放行或不过闸门；T2 的 `test_read_only_audits_are_not_required`、`test_subagent_device_writes_do_not_count`、`test_no_worker_session_is_not_required` 与 T3 的 `test_spec_without_flag_skips_gate`、`test_non_ready_reports_skip_gate` 钉住。
2. **重复写 report**：被拒的 `report` 不能落 `ticket_report`、不能结束运行、不能触发 `report` 事件；T3 的 `test_rejected_ready_keeps_ticket_and_run_going`、`test_rejected_report_does_not_stop_the_run`、`test_rejected_report_continues_to_next_batch` 与 T2 的 `test_running_pipeline_rejects_before_verifier` 钉住。
3. **授权换卡竞态**：旧授权仍 `active` 时换卡必须一次成功（旧行落 `exhausted`、只弹一张新卡），v2 聊天会话不能被带偏；T4 的 `test_ticket_session_exhausted_grant_opens_new_card`、`test_chat_session_exhausted_grant_still_raises` 钉住。
4. **子代理点击计入**：导航员的 `click` 审计 `session_id` 是父 Worker 会话，只按会话过滤会算成工单效果；必须再用 `agent_name` 与 `agent_run` 的 `session_id`/`parent_run_id` 两道归属排除；T2 的 `test_subagent_device_writes_do_not_count` 钉住。
5. **评测 ticket 驱动 cassette 时序**：Worker 的 `delegate` 工具结果在子代理应答之后才发给 Worker，相邻应答校验会报错；`tool_call_id` 占位符必须对得上，`skipped` 不影响退出码会让「驱动缺失」静默通过；T7 的 6 个场景与 `test_every_scenario_has_a_driver` 钉住。

---
### Task 0: 分支、基线、计划入库与 §0 逐条

**Files:**
- Create: `docs/v3/plans/m87-verifier-copilot-loop.md`（把 gitignored 的 `debug/plans/m87-verifier-copilot-loop.md` 移入）、`docs/v3/ledgers/m87-verifier-copilot-loop.md`（先写表头、分支、基点、tag、§0 结果，收尾在 Task 8 补全）
- 不改代码

**Interfaces:**
- Produces: 开工基点、实测基线、§0 结论、首条提交。
- Consumes: `dev`（M86 合并后的提交）、已落地的 `evals-ticket-driver` 补丁。

- [ ] **Step 1: 从 dev 开分支**

```bash
git checkout dev
git checkout -b verifier-copilot-loop
git status -sb
```

Expected：第一行 `## verifier-copilot-loop`，没有其他行（新分支未推送，不带 `...origin/...`）。

- [ ] **Step 2: 记录基点、tag 与迁移 head**

```bash
git rev-parse --short dev HEAD
git tag -l 'v3-m85' 'v3-m86'
ls maa_api/db/migrations/versions | grep -E '^[0-9]{4}_' | sort | tail -n 1
```

Expected：`dev` 与 `HEAD` 是同一提交（M86 合并提交）；`v3-m85` 与 `v3-m86` 两行；`0041_nav_graph.py`。任一不符按 §0 规则处理，不硬开工。

- [ ] **Step 3: 记录实测基线（补丁 + M86 之后）**

```bash
mkdir -p /tmp/m87
.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"
.venv/bin/python -c "from maa_api.settings import SETTING_KEYS; from maa_api.services.settings_schema import _FIELDS; print(len(SETTING_KEYS), len(_FIELDS))"
ls tests/evals/scenarios/*.yaml | wc -l
.venv/bin/python -m pytest -o addopts= -q > /tmp/m87/pytest-base.log 2>&1; echo $?; tail -n 3 /tmp/m87/pytest-base.log
.venv/bin/python -m pytest -o addopts= -q tests/evals > /tmp/m87/eval-base.log 2>&1; echo $?; tail -n 2 /tmp/m87/eval-base.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m87/cap-base.log 2>&1; echo $?; tail -n 4 /tmp/m87/cap-base.log
```

Expected：`107 106`；`142 131`；`13`；全量 pytest 退出码 1，计数为 **3 failed / 5261 passed**（dev 实测；3 条既有真资源失败，无 M87 新增，逐条记台账；M86 合并后以实测为准）；`tests/evals` 退出码 0（passed 数记台账）；capability 退出码 0、`8/8 通过（100.0%）`、末行 `EVAL OK`、`grep -c "跳过"` 为 0。实测不同就按实测记台账，并当作后续基线。

- [ ] **Step 4: 跑 §0 检查表**

按上面「§0 前置检查」表逐条执行，输出并进 `/tmp/m87/precheck.log`；Step 5 建台账时把每条的实测值填进「§0 结果」。第 2 条不成立就等 M86；第 13 条任一项不成立就停工并汇报。

- [ ] **Step 5: 先建台账表头**

按 `docs/v3/specs/README.md` §4 的模板建文件；基点用 Step 2、§0 结果用 Step 4 的实测值，其余各节留到 Task 8 补全：

```bash
mkdir -p docs/v3/ledgers
cat > docs/v3/ledgers/m87-verifier-copilot-loop.md <<'EOF'
# M87 验证闸门与作业研究闭环（台账）

- 分支：`verifier-copilot-loop`；基点：`dev` = `<Step 2 的 commit>`；合并：待补；tag：`v3-m87`
- spec：[m87-verifier-copilot-loop](../specs/m87-verifier-copilot-loop.md)

## §0 结果

（逐条实测值，命令输出见 `/tmp/m87/precheck.log`，待填）

## 做了什么

（Task 8 补全）

## 验证证据

| 命令 | 结果 |
|---|---|
| （Task 8 补全） | |

## Rulings I made

（Task 8 补全）

## 偏离（已记 13 §8.x）

（Task 8 补全）

## 遗留

（Task 8 补全）
EOF
```

- [ ] **Step 6: 把计划移进仓库并提交**

```bash
mv debug/plans/m87-verifier-copilot-loop.md docs/v3/plans/m87-verifier-copilot-loop.md
git add docs/v3/plans/m87-verifier-copilot-loop.md docs/v3/ledgers/m87-verifier-copilot-loop.md
git commit -m "docs(m87): 验证闸门与作业研究闭环实施计划与开工台账"
```

`docs/v3/specs/m87-verifier-copilot-loop.md` 已在升格提交里入库，不重复提交。

---
### Task 1: 代理定义字段与版本（spec T1）

**Files:**
- Modify: `maa_api/agent/specs/schema.py`（`AgentSpecFrontmatter`）、`maa_api/agent/specs/registry.py`（`_check_field_rules`）
- Modify: `maa_api/agent/specs/builtin/daily_ops.md`、`maa_api/agent/specs/builtin/copilot.md`（frontmatter 加 `verify_before_ready: true`，`version: 4`）、`maa_api/agent/specs/builtin/verifier.md`（只改 `version: 2`）
- Test: Create `tests/agent/specs/test_verify_flag.py`；Modify `tests/agent/test_builtin_workers.py`、`tests/api/test_agent_specs_router.py`；只跑不改 `tests/agent/specs/test_m70_specs.py`

**Interfaces:**
- Produces: `AgentSpecFrontmatter.verify_before_ready: bool = False`；`AgentSpecError`（消息含 `verify_before_ready`）；三个定义的新版本。
- Consumes: `AgentSpecRegistry(build_registry(), root=tmp_path)`、`parse_spec_file`、`build_registry`。

- [ ] **Step 1: 写失败测试**

`tests/agent/specs/test_verify_flag.py` 三条（文件头 import `json`、`Path`、`pytest`、`build_registry`、`AgentSpecRegistry`、`AgentSpecFrontmatter`、`AgentSpecError`）：

```python
def test_verify_flag_defaults_false():
    spec = AgentSpecFrontmatter.model_validate(_minimal_frontmatter())
    assert spec.verify_before_ready is False
    specs = _load_specs()
    assert specs["general"].verify_before_ready is False
    assert specs["planner"].verify_before_ready is False

def test_only_workers_may_verify(tmp_path):
    (tmp_path / "spec_a.md").write_text(_subagent_with_flag(), encoding="utf-8")
    with pytest.raises(AgentSpecError, match="verify_before_ready"):
        AgentSpecRegistry(build_registry(), root=tmp_path).load()

def test_builtin_workers_that_verify():
    specs = _load_specs()
    assert specs["daily_ops"].verify_before_ready is True
    assert specs["copilot"].verify_before_ready is True
    for name in ("diagnose", "ops", "analyst", "general"):
        assert specs[name].verify_before_ready is False
```

`_load_specs()` 用 `AgentSpecRegistry(build_registry()).load()`；`_minimal_frontmatter()` 返回 name/title/kind/description/model_role/tools/device/risk_ceiling/max_steps/version 的合法头；`_subagent_with_flag()` 写 `kind: subagent`、`output_schema: subagent_report`、`verify_before_ready: true` 的合法头（`common.md` 缺省回退包内那份，tmp 目录只放 `spec_a.md`）。

- [ ] **Step 2: 跑红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/specs/test_verify_flag.py > /tmp/m87/t1-red.log 2>&1; echo $?; tail -n 6 /tmp/m87/t1-red.log
```

Expected：非 0；`AttributeError: verify_before_ready` 或 `有未知字段 ['verify_before_ready']`。

- [ ] **Step 3: 实现**

1. `AgentSpecFrontmatter` 加 `verify_before_ready: bool = False`；`AgentSpec` 不重复声明。
2. `_check_field_rules` 加：`if spec.verify_before_ready and spec.kind != "worker": raise _invalid(file_name, "verify_before_ready", "只有 worker 可以为 true")`。
3. `daily_ops.md`、`copilot.md` 的 frontmatter 在 `version:` 前加 `verify_before_ready: true`，`version` 改 4；`verifier.md` 只把 `version` 改 2；`general.md` 不动。

- [ ] **Step 4: 改既有版本断言**

`tests/agent/test_builtin_workers.py`：`WORKERS` 每项加 `version`（`daily_ops 4`、`copilot 4`、`planner 3`、`diagnose 4`、`analyst 3`、`ops 3`），断言从 `spec.version == (4 if name == "diagnose" else 3)` 改为 `spec.version == expected["version"]`。

`tests/api/test_agent_specs_router.py`：`EXPECTED_VERSIONS` 里 `daily_ops: 4`、`copilot: 4`、`verifier: 2`；`general` 保持 6、`navigator` 保持 2；`assert len(DETAIL_FIELDS) == 17` 不动。

- [ ] **Step 5: 跑绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/specs tests/agent/test_builtin_workers.py tests/api/test_agent_specs_router.py > /tmp/m87/t1.log 2>&1; echo $?; tail -n 5 /tmp/m87/t1.log
```

Expected：退出码 0（`test_m70_specs.py` 的两条也通过，`general` 工具集合不变）。

- [ ] **Step 6: 提交**

```bash
git add maa_api/agent/specs/schema.py maa_api/agent/specs/registry.py maa_api/agent/specs/builtin/daily_ops.md maa_api/agent/specs/builtin/copilot.md maa_api/agent/specs/builtin/verifier.md tests/agent/specs/test_verify_flag.py tests/agent/test_builtin_workers.py tests/api/test_agent_specs_router.py
git commit -m "feat(m87): verify_before_ready 字段与三个定义的版本"
```

---
### Task 2: 闸门与尝试仓储（spec T2）

**Files:**
- Create: `maa_api/agent/workers/verification.py`
- Modify: `maa_api/db/repositories/copilot.py`（`CopilotAttemptRepository.list_for_session`）
- Test: Create `tests/agent/workers/test_verification_gate.py`、`tests/db/test_copilot_attempt_repository.py`

**Interfaces:**
- Produces: `EFFECT_TOOLS`/`RAW_TOOLS`/`VERIFY_MIN_CONFIDENCE=0.7`/`VERIFY_MAX_ROUNDS=2`/`WAIVER_OPTION="仍按完成结束"`/`MAX_CONTEXT_CHARS=4000`/`MAX_EVIDENCE=10`/`MAX_LISTED_PIPELINES=3`/`MAX_LISTED_ATTEMPTS=3`/`MAX_DETAILS_CHARS=4000`/`VERIFICATION_META_CHARS=120`；`GateDecision`（`report_details()`、`tool_result()`）；`VerificationGate(session_factory).check(*, ticket_id, worker_session_id, claim, details) -> GateDecision`；`record_verdict(*, run_id, verdict, degraded) -> None`；`merge_verification(details, verification) -> dict`；`CopilotAttemptRepository.list_for_session(session_id) -> list[dict]`（`started_at` 升序、保持资源里存的键）。
- Consumes: `Ticket`、`AgentSession`、`AgentRun`、`AgentAudit`、`Pipeline`、`AgentQuestion`、`ResourceAsset`；`tests/fakes/v3_db.make_session_factory`。

- [ ] **Step 1: 写闸门测试（先红）**

`tests/agent/workers/test_verification_gate.py` 顶部写 `_world(tmp_path, *, worker_session=True, audits=(), pipelines=(), attempts=(), verdicts=(), questions=())`：建库后直接写 `Ticket`、`AgentSession(kind="worker", ticket_id=…)`、`AgentRun`、`AgentAudit`、`Pipeline`、`kind="copilot"` 的 `ResourceAsset(meta={"attempts": …})`、`AgentQuestion`；返回 `(gate, ticket_id, worker_session_id)`。用例与断言：

```text
- `test_no_worker_session_is_not_required` / `test_read_only_audits_are_not_required`：前者 worker_session_id 为空或会话 parent_session_id 非空 → accepted、method=="not_required"；后者只有 get_screenshot → not_required
- `test_completed_pipelines_pass_by_machine` / `test_failed_pipeline_requires_verifier`：前者最后一次 submit_pipeline 之后两条 completed → machine、evidence 两条；后者一条 failed → verification_required、delegate["agent"]=="verifier"、context 含流水线 id 前 8 位
- `test_running_pipeline_rejects_before_verifier` / `test_raw_tools_block_machine_evidence`：前者 status=running → verification_required、delegate is None、message 含 wait_pipeline；后者用过 click 即使流水线全 completed 也 verification_required
- `test_core_judged_success_passes` / `test_model_judged_success_needs_verifier`：前者最近尝试 success=True、judged_by=core → machine；后者 judged_by=model → verification_required
- `test_attempt_pipelines_are_excluded` / `test_superseded_retry_pipeline_is_excluded`：前者尝试自己的 failed 流水线不参与检查 → machine；后者被 retry_of_id 取代的 failed 不影响 → machine
- `test_subagent_device_writes_do_not_count` / `test_fresh_passing_verdict_passes`：前者 agent_name=navigator 的 click + 子运行存在 → not_required；后者 finished_at 晚于最后写操作、passed、0.9 → verifier、verdict_run_id 是该运行
- `test_stale_verdict_is_ignored` / `test_low_confidence_counts_as_failure`：前者 finished_at 早于最后写操作 → verification_required；后者 0.6 → verification_failed
- `test_degraded_or_failed_run_verdict_counts` / `test_two_failures_exhaust`：前者 degraded=true 与 status=failed 但 meta 有结论 → 都算失败一次；后者两次失败 → verification_exhausted、delegate is None
- `test_waiver_accepts_answered_question` / `test_waiver_requires_two_failures`：前者选「仍按完成结束」且 answered_at 晚于最后结论 → waiver；后者只有一次失败时带 waiver → waiver_invalid
- `test_waiver_rejects_wrong_session_option_or_time` / `test_waiver_by_selection_after_restart`：前者别的会话 / 选项不符 / 作答过早 各一例 → waiver_invalid；后者 {"selection":"仍按完成结束"} + 晚于失败结论的回答 → waiver
- `test_context_is_capped` / `test_evidence_is_capped`：前者 30 条流水线 → context ≤4000 字且含「另有」；后者 evidence ≤10 条
- `test_record_verdict_keeps_other_meta_keys` / `test_record_verdict_degraded`：前者原有 meta 键保留、verdict 含 passed/confidence/evidence/notes/degraded/recorded_at；后者降级写 passed=False、confidence=0、degraded=True、notes==验证员没有给出合法结论
```

提交前还要断言拒绝结果的键集合恰为 `{"accepted","reason","message","evidence","delegate"}`，且 `report_details()` 的键只可能是 `{"method","verdict_run_id","question_id","evidence"}`。

- [ ] **Step 2: 写仓储测试（先红）**

`tests/db/test_copilot_attempt_repository.py` 两条：`test_list_for_session_filters_and_orders`（两份作业资源，各自 `meta["attempts"]` 混两个会话，按 `started_at` 升序取本会话的）、`test_list_for_session_ignores_non_copilot_assets`（`kind="artifact"` 的资源不参与）。行用 `ResourceAsset(id=…, kind="copilot", name=…, content={"actions": []}, meta={"attempts": [...]})` 直接写库。

- [ ] **Step 3: 跑红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/workers/test_verification_gate.py tests/db/test_copilot_attempt_repository.py > /tmp/m87/t2-red.log 2>&1; echo $?; tail -n 6 /tmp/m87/t2-red.log
```

Expected：非 0；`ModuleNotFoundError: maa_api.agent.workers.verification` 与 `AttributeError: list_for_session`。

- [ ] **Step 4: 实现闸门**

按 §4.2 的公开形状与 §4.5.2 的算法写 `verification.py`；判定顺序、过滤条件、`suggest()` 的拼法与四条 `message` 文案逐字照 spec §4.3/§4.5.2。`check` 的查询全部走一次 `async with session_factory()`：工单与 Worker 会话先校验（任一不符 → `not_required`），再按 `ticket_id`/`session_id`/`agent_name`/`tool_name`/`status` 取审计，再与 `agent_run` 对齐，再取 `list_for_session(worker_session_id)` 与本会话流水线（`agent_session_id == worker_session_id`），最后按 §4.5.2 的 0）–4）分支给出 `GateDecision`。

- [ ] **Step 5: 实现仓储方法**

`list_for_session` 照 `find` 的扫法：`select(ResourceAsset).where(kind == copilot)`，逐份读 `meta["attempts"]`，按 `session_id` 过滤，`started_at` 升序；不改 `meta` 的键。

- [ ] **Step 6: 跑绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/workers/test_verification_gate.py tests/db/test_copilot_attempt_repository.py > /tmp/m87/t2.log 2>&1; echo $?; tail -n 5 /tmp/m87/t2.log
```

Expected：退出码 0；24 + 2 条全部通过。

- [ ] **Step 7: 提交**

```bash
git add maa_api/agent/workers/verification.py maa_api/db/repositories/copilot.py tests/agent/workers/test_verification_gate.py tests/db/test_copilot_attempt_repository.py
git commit -m "feat(m87): 完成闸门与按会话的作业尝试查询"
```

---
### Task 3: 接入汇报与委派（spec T3）

**Files:**
- Modify: `maa_api/agent/workers/service.py`（`submit_report`）、`maa_api/agent/workers/middleware.py`（`ReportMiddleware.wrap_tool`）、`maa_api/agent/workers/delegation.py`（`_delegate_locked`）
- Test: Create `tests/agent/workers/test_report_gate.py`、`tests/agent/workers/test_delegation_verdict.py`；Modify `tests/agent/workers/test_worker_middleware.py`（加一条）

**Interfaces:**
- Consumes: `VerificationGate.check/record_verdict`、`merge_verification`（Task 2）；`ReportInput`、`SUBMITTED_KEY`、`_finish_run`。
- Produces: `submit_report` 在 `ready` 前过闸门并合并 `details.verification`；`ReportMiddleware` 对 `accepted=false` 早退；`DelegationService` 写 `agent_run.meta["verdict"]`。

- [ ] **Step 1: 写汇报闸门测试（先红）**

`tests/agent/workers/test_report_gate.py` 复制 `test_worker_service.py` 的 `_Harness`/`_make_ticket`/`_add_pipeline` 为本文件私有，但不能原样复用（原 helper 会让闸门判成 `not_required`），按下面改造：

- `_make_ticket` 里写死的 `kind="general"` 改成 `kind="daily_ops"`；`_SPEC` 用 `SimpleNamespace(name="daily_ops", ..., verify_before_ready=True)`，`_Registry` 能按 `"daily_ops"` 返回它；`ToolContext.origin` 带 `run_id`/`ticket_id`/`agent="daily_ops"`。`ticket.kind`、`_SPEC.name`、`ToolOrigin.agent` 三处必须统一成 `daily_ops`，否则 §4.5.1 第 3、4 步按 `ticket.kind`/`agent_name` 过滤后得到 `not_required`。
- 原 `_make_ticket`/`_add_pipeline` 不会给 `submit_pipeline` 写 `AgentAudit`，`seed_run` 又写死 `agent_name="test"`。要过闸门的用例必须自己写一条 `AgentAudit(caller=CallerType.INTERNAL.value, ticket_id=ticket_id, session_id=worker_session, agent_name="daily_ops", tool_name="submit_pipeline", status="success", run_id=run_id)`，并写对应的 `AgentRun(id=run_id, root_run_id=run_id, agent_name="daily_ops", session_id=worker_session, parent_run_id=None, ...)`；不要直接拿 `seed_run` 当闸门证据（需要时先 `seed_run` 再改 `agent_name`，或直接写 `AgentRun`）。
- 用 `payload = await service.invoke("report", …)` 调用（`CURRENT_RUN` 指向同一个 `RunContext`、`app.state.worker_service` 是真实服务）。`invoke` 返回 `{"status", "audit_id", "result"}`，工具结果在 `payload["result"]` 里。

```text
test_rejected_ready_keeps_ticket_and_run_going  # payload["result"]["accepted"] is False；TicketReport 计数不变；Ticket.status=="open"；AgentAudit 有 tool_name="report", status="success" 的行；没有 "report" 事件
test_accepted_ready_writes_verification_details # details.verification.method=="machine"
test_non_ready_reports_skip_gate                # failed 汇报直接写行，TicketReport 计数 +1，details 无 verification
test_spec_without_flag_skips_gate               # 另建 name="planner"、无 verify_before_ready 字段的 SimpleNamespace → 直接写行，details 无 verification
test_merged_details_trim_evidence               # 原 details 3900 字 → 合并后 ≤4000，evidence 被裁
test_verification_metadata_survives_full_details# 原 details 正好 4000 字 → method 与 id 仍写入，总数 ≤4120
test_rejected_report_continues_to_next_batch    # spec §5 T3 第 4 步的测试落点：AgentLoop 跑两批假模型，第一批 report(ready) 被拒（payload["result"]["accepted"] is False）、第二批 get_screenshot；断言两批都执行、finish_reason != "report_submitted"、SUBMITTED_KEY 仍 None
```

- [ ] **Step 2: 写委派测试（先红）**

`tests/agent/workers/test_delegation_verdict.py` 照 `test_delegation_service.py` 的 `_Harness` + `monkeypatch.setattr("maa_api.agent.workers.delegation.run_agent", fake_run_agent)`：

```text
test_verifier_output_is_recorded            # meta["verdict"]["passed"] is True、confidence 0.9、evidence 非空、recorded_at 以 Z 结尾、degraded False
test_degraded_verifier_output_is_recorded   # final_text 非法 JSON 且 _structured_retry 抛错 → degraded True、passed False、confidence 0
test_other_subagents_are_not_recorded       # delegate researcher → 运行 meta 没有 verdict
test_record_failure_does_not_break_delegation # monkeypatch VerificationGate.record_verdict 抛错 → delegate 仍返回 result，meta 没有 verdict
```

- [ ] **Step 3: 写中间件测试（先红）**

`tests/agent/workers/test_worker_middleware.py` 加 `test_rejected_report_does_not_stop_the_run`：`call_next` 返回 `ToolOutcome(status="ok", result={"accepted": False, "reason": "verification_required", ...})`，断言 `outcome.stop_run is False`、`outcome.stop_reason is None`、`ctx.state[SUBMITTED_KEY] is None`。

- [ ] **Step 4: 跑红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/workers/test_report_gate.py tests/agent/workers/test_delegation_verdict.py tests/agent/workers/test_worker_middleware.py > /tmp/m87/t3-red.log 2>&1; echo $?; tail -n 8 /tmp/m87/t3-red.log
```

Expected：非 0；被拒的 `report` 仍返回 `report_id`（或 500），`meta` 没有 `verdict`；`test_rejected_report_continues_to_next_batch` 因第二批没有发生而失败。

- [ ] **Step 5: 实现**

1. `submit_report`：在构造 `ReportInput` 前按 §4.5.3 插入闸门；标志读 `bool(getattr(ctx.spec, "verify_before_ready", False))`；拒绝时 `return decision.tool_result()`；放行时 `details = merge_verification(details, decision.report_details())` 并把 `details` 传给 `ReportInput`；`VerificationGate(self._session_factory)` 现建。
2. `ReportMiddleware.wrap_tool`：如「接缝裁决」表；不碰 `ensure_report`。
3. `DelegationService._delegate_locked`：在 `await self._finish_run(...)` 之后、`await self._set_session_status(...)` 之前，`if str(getattr(spec, "name", "")) == "verifier": await VerificationGate(self._session_factory).record_verdict(run_id=run.id, verdict=result, degraded=bool(result.get("degraded")))`，整段包 `try/except Exception` 只 `logger.warning`。

- [ ] **Step 6: 跑绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/workers tests/agent/harness > /tmp/m87/t3.log 2>&1; echo $?; tail -n 5 /tmp/m87/t3.log
```

Expected：退出码 0；既有 `test_ready_report_ends_ticket` 不改断言仍通过；`test_rejected_report_continues_to_next_batch` 与中间件的新用例都通过。

- [ ] **Step 7: 提交**

```bash
git add maa_api/agent/workers/service.py maa_api/agent/workers/middleware.py maa_api/agent/workers/delegation.py tests/agent/workers/test_report_gate.py tests/agent/workers/test_delegation_verdict.py tests/agent/workers/test_worker_middleware.py
git commit -m "feat(m87): ready 汇报过完成闸门，验证员结论落库"
```

---
### Task 4: 作业授权的轮次上限与新一轮（spec T4）

**Files:**
- Modify: `maa_api/settings.py`（`CopilotLoopSettings`、`SETTING_KEYS`、`Settings`、`__all__`）、`maa_api/services/settings_schema.py`、`config.template.yaml`、`maa_api/agent/policy.py`、`maa_api/agent/confirmation.py`、`maa_api/services/copilot/grant.py`
- Test: Create `tests/agent/test_copilot_loop_limit.py`；Modify `tests/test_settings.py`

**Interfaces:**
- Produces: `CopilotLoopSettings(max_iterations: int = 3, ge=1, le=10)`；`SETTING_KEYS["copilot_loop.max_iterations"] == ("copilot_loop", "max_iterations")`；`_FIELDS["copilot_loop.max_iterations"] = dict(label="作业研究每轮最多尝试次数", group="Agent", type="integer", minimum=1, maximum=10, hot_action="none")`；`CopilotGrantService.approve(..., max_attempts: Any = None)`；`CopilotGrantService.mark_exhausted(session_id: str | None) -> dict | None`；策略私有 `_copilot_grant_card(context, name, value, *, limit=None)` 与 `_ticket_worker_session(context) -> str | None`。
- Consumes: `_require_attempts`、`_terminate`、`get_settings()`、`AgentSession`、`CopilotAttemptRepository.find`。

- [ ] **Step 1: 配置项与设置用例（先红）**

`settings.py` 加 `CopilotLoopSettings`（docstring 照 §4.4）与 `Settings.copilot_loop`、`SETTING_KEYS` 一行、`__all__` 的名字；`settings_schema.py` 加 `_FIELDS` 一行；`config.template.yaml` 在 `memory:` 之后、`trace:` 之前加带注释的 `copilot_loop: {max_iterations: 3}` 段。同一次提交加齐，否则 `SETTINGS_SCHEMA` 导入期 `KeyError`。

`tests/test_settings.py`：`test_env_mapping_names_are_pinned` 的整表字面断言加 `"copilot_loop.max_iterations": "MAA_COPILOT_LOOP_MAX_ITERATIONS"`（位置按 `SETTING_KEYS` 的插入顺序）；新增 `test_copilot_loop_max_iterations_defaults_and_bounds`（默认 3；`Settings(copilot_loop={"max_iterations": 0})` 与 `11` 抛校验错；`schema_for("copilot_loop.max_iterations")` 的 minimum/maximum 是 1/10）。

- [ ] **Step 2: 跑红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/test_settings.py > /tmp/m87/t4-red-settings.log 2>&1; echo $?; tail -n 6 /tmp/m87/t4-red-settings.log
```

Expected：非 0；`copilot_loop` 未知键或整表断言多/缺一行。

- [ ] **Step 3: 写授权边界测试（先红）**

`tests/agent/test_copilot_loop_limit.py` 照 `tests/agent/test_confirmation_guard.py` 的 `_database()`（内存库）与 `_context(session, session_id=…)`，用 `ConfirmationService(factory, build_registry(), policy=PolicyEngine())`；需要设置时 `set_settings(Settings(copilot_loop=CopilotLoopSettings(max_iterations=2)))`，`finally` 里 `set_settings(None)`。工单会话的用例要先用 `AgentSession(id=session_id, kind="worker", status=AgentSessionStatus.ACTIVE, ticket_id="ticket-m87")` 种一行：`_ticket_worker_session` 按 `context.session_id` 读 `agent_session`，没有这行就会被当成 v2 聊天会话，永远命中不了换卡分支；聊天会话的用例不种这行。用例：

```text
test_ticket_session_prefill_is_clamped        # grant_attempts=5、上限 3 → payload["attempts"]==3、payload["max_attempts"]==3
test_chat_session_keeps_v2_prefill            # 会话无 ticket_id → payload["attempts"]==5，且 "max_attempts" not in payload
test_approval_clamps_override_above_max       # resolve(overrides={"attempts": 8})、max_attempts=3 → 授权行 attempts==3
test_max_iterations_setting_is_hot            # 上限 2 弹卡 max_attempts==2；改 5 后新会话弹卡 max_attempts==5
test_ticket_session_exhausted_grant_opens_new_card # approve(attempts=1)+consume → 下一次 invoke 得到 awaiting_confirmation，旧行 status=="exhausted"，payload 带 max_attempts
test_chat_session_exhausted_grant_still_raises # 同上但无 ticket_id → AppError.code is COPILOT_GRANT_EXHAUSTED，旧行仍 "active"
```

- [ ] **Step 4: 跑红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_copilot_loop_limit.py > /tmp/m87/t4-red.log 2>&1; echo $?; tail -n 6 /tmp/m87/t4-red.log
```

Expected：非 0；`payload` 没有 `max_attempts`，旧授权场景抛 `COPILOT_GRANT_EXHAUSTED`。

- [ ] **Step 5: 实现策略**

`_evaluate_copilot_attempt` 按 §4.5.5 改三处：抽出 `_copilot_grant_card`（原「没有授权」分支的 payload 构造，工单会话时 `attempts=min(attempts, limit)`、加 `max_attempts=limit`，非工单会话不带 `max_attempts`）；`remaining <= 0` 时先 `_ticket_worker_session(context)`，命中就 `await _grant_service(context).mark_exhausted(session_id)` 再返回 `_copilot_grant_card(...)`，否则原样抛错；`_active_copilot_grant` 不动。`_grant_service(context)` 复用 `_active_copilot_grant` 里「包住 `context.db_session` 的 session factory」写法，不新增构造参数。另在 `policy.py` 顶部补 `from maa_api.settings import get_settings`（当前未导入；`limit = get_settings().copilot_loop.max_iterations` 需要它，漏了会 `NameError`）。

- [ ] **Step 6: 实现授权服务与确认层**

`grant.py`：`approve` 加关键字 `max_attempts: Any = None`（`max_attempts is not None` 时 `limit = _require_attempts(max_attempts)`，再 `resolved_attempts = min(resolved_attempts, limit)`）；新增 `mark_exhausted(session_id)` 调 `self._terminate(session_id, "exhausted", reason="次数用完，用户可批准新一轮")`。`confirmation.py`：`_apply_copilot_grant_overrides` 调 `approve(..., max_attempts=_pick("max_attempts"))`；`overrides` 的三项不动。

- [ ] **Step 7: 跑绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_copilot_loop_limit.py tests/agent/test_copilot_attempt_tools.py tests/agent/test_confirmation_service.py tests/services/test_copilot_grant.py tests/test_settings.py > /tmp/m87/t4.log 2>&1; echo $?; tail -n 5 /tmp/m87/t4.log
```

Expected：退出码 0；v2 既有的作业授权测试一条不改仍通过。

- [ ] **Step 8: 提交**

```bash
git add maa_api/settings.py maa_api/services/settings_schema.py config.template.yaml maa_api/agent/policy.py maa_api/agent/confirmation.py maa_api/services/copilot/grant.py tests/agent/test_copilot_loop_limit.py tests/test_settings.py
git commit -m "feat(m87): 作业授权轮次上限、自动换卡与 max_attempts"
```

---
### Task 5: 前端授权卡（spec T5）

**Files:**
- Modify: `web/src/features/copilots/CopilotGrantCard.tsx`
- Test: `web/src/features/copilots/CopilotGrantCard.test.tsx`（加三条）

**Interfaces:**
- Produces: `CopilotGrantRequest.maxAttempts: number`；`attemptsOf(value, fallback, upper = MAX_ATTEMPTS)`（上界夹到 1–10）；`parseCopilotGrantPayload` 返回 `maxAttempts`，`attempts = min(attemptsOf(payload.attempts ?? args.grant_attempts), maxAttempts)`；下拉框选项 `ATTEMPT_OPTIONS.filter((count) => count <= request.maxAttempts)`；`onChange` 用 `attemptsOf(value, DEFAULT_ATTEMPTS, request.maxAttempts)`。
- Consumes: `MAX_ATTEMPTS`、`DEFAULT_ATTEMPTS`、`ATTEMPT_OPTIONS`、`ConfirmationCard`（不改）。

- [ ] **Step 1: 写失败测试**

`CopilotGrantCard.test.tsx` 三条（复用既有 `request()`/`Harness`）：

```text
it('parses max_attempts and clamps prefill')  // parseCopilotGrantPayload({...GRANT_PAYLOAD, max_attempts: 2, attempts: 5}) → attempts 2、maxAttempts 2
it('limits attempts to max_attempts')         // max_attempts=3 → 下拉框 3 个 option；说明文字含「每轮最多试 3 次」「可以再授权一轮」
it('keeps ten options without max_attempts')  // 无 max_attempts → 10 个 option；说明文字仍是「次数用完授权就结束，需要重新申请。」
```

- [ ] **Step 2: 跑红**

```bash
cd web && pnpm test --run src/features/copilots/CopilotGrantCard.test.tsx > /tmp/m87/t5-red.log 2>&1; echo $?; tail -n 8 /tmp/m87/t5-red.log
```

Expected：非 0；`maxAttempts` 未定义、3 个 option 断言失败。

- [ ] **Step 3: 实现**

按 §4.6 六条改 `CopilotGrantCard.tsx`；`ConfirmationCard.tsx` 不改，只消费解析结果。

- [ ] **Step 4: 跑绿与类型检查**

```bash
cd web && pnpm typecheck && pnpm test --run src/features/copilots > /tmp/m87/t5.log 2>&1; echo $?; tail -n 5 /tmp/m87/t5.log
```

Expected：`0`；新三条与既有用例（含 `CopilotGrantBar.test.tsx`、`CopilotsPage.test.tsx`）全通过。

- [ ] **Step 5: 提交**

```bash
git add web/src/features/copilots/CopilotGrantCard.tsx web/src/features/copilots/CopilotGrantCard.test.tsx
git commit -m "feat(m87): 授权卡按每轮上限截断次数"
```

---
### Task 6: 提示词（spec T6）

**Files:**
- Modify: `maa_api/agent/specs/builtin/copilot.md`（正文 29/35/39 行与末尾追加）、`maa_api/agent/specs/builtin/verifier.md`（末尾追加）、`maa_api/agent/specs/builtin/daily_ops.md`（末尾追加）
- 不改 frontmatter（Task 1 已改版本）

**Interfaces:**
- Produces: 三份定义的正文；除 copilot.md 第 2 步的导航归属按 M86 工具集改写外，与 spec §4.7 逐字一致（差异记 13 §8）。
- Consumes: M86 合并后的 `navigate_to`/`nearest`/`remember_screen` 实符号（§0 第 11 条），且这三个工具只在 `navigator` 的工具集里（05 §3.3）。

- [ ] **Step 1: 跑红（验证命令先失败）**

```bash
grep -n "## 研究循环" maa_api/agent/specs/builtin/copilot.md; echo "research=$?"
grep -n "## 核对完成证据" maa_api/agent/specs/builtin/verifier.md; echo "verify=$?"
grep -n "## 汇报完成" maa_api/agent/specs/builtin/daily_ops.md; echo "daily=$?"
```

Expected：三条 `grep` 都无输出（`research=1`、`verify=1`、`daily=1`）。

- [ ] **Step 2: 改 copilot.md**

删第 39 行整句；把第 35 行整句替换成 §4.7.1 给的新句；第 29 行的位置要求保留；在正文末尾追加 `## 研究循环` 整节（含「什么时候停」与最后一段闸门说明），除下面这一处外逐字照 §4.7.1。

§4.7.1 第 2 步把「导航员报 NAV_NODE_NOT_FOUND 时按 `nearest` 过去、再用 `remember_screen` 记下」写在 copilot 的正文里，但 M86 的 `where_am_i`/`navigate_to`/`remember_screen` 只给 navigator（05 §3.3），copilot 调不到这三个工具。把这一步改写成对 delegate 的 `task`/`context` 的要求：要求导航员在关卡不存在时按 `nearest` 先到最近的容器、手动点进关卡页，并用 `remember_screen(node_id="stage:{代号}", title="<代号>")` 记下；工具名与参数仍按 M86 实符号。这一处与 spec §4.7.1 的差异记 13 §8（Task 8 一并写），其余逐字一致。

- [ ] **Step 3: 改 verifier.md 与 daily_ops.md**

按 §4.7.2、§4.7.3 在各自正文末尾逐字追加两节；`general.md` 不动，任何工具集不动。

- [ ] **Step 4: 跑绿与旧句检查**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/specs > /tmp/m87/t6.log 2>&1; echo $?; tail -n 5 /tmp/m87/t6.log
grep -nE "本里程碑的验证靠你自己|验证靠你自己看截图" maa_api/agent/specs/builtin/copilot.md; echo "grep-exit=$?"
grep -n "执行前停在该关" maa_api/agent/specs/builtin/copilot.md
grep -n "## 研究循环" maa_api/agent/specs/builtin/copilot.md
grep -n "NAV_NODE_NOT_FOUND" maa_api/agent/specs/builtin/copilot.md
grep -n "## 核对完成证据" maa_api/agent/specs/builtin/verifier.md
grep -n "## 汇报完成" maa_api/agent/specs/builtin/daily_ops.md
```

Expected：pytest `0`；旧句无输出（`grep-exit=1`）；后五条各 1 行，且 `NAV_NODE_NOT_FOUND` 那一行要求导航员处理（不写 copilot 自己调导航工具）。

- [ ] **Step 5: 文案只读检查**

```bash
git status --porcelain docs/copy-audit > /tmp/m87/copy-before.txt
.venv/bin/python scripts/copy_inventory.py --check > /tmp/m87/copy.log 2>&1; echo $?
git status --porcelain docs/copy-audit > /tmp/m87/copy-after.txt
diff /tmp/m87/copy-before.txt /tmp/m87/copy-after.txt; echo "diff-exit=$?"
grep -c "文案 AI 腔回潮" /tmp/m87/copy.log
```

Expected：`diff-exit=0`；回潮条数与开工前一致（Task 0 记的基线）。若 `diff-exit=1`，先 `git restore --source=HEAD -- docs/copy-audit` 复原，再人工看新文案。

- [ ] **Step 6: 提交**

```bash
git add maa_api/agent/specs/builtin/copilot.md maa_api/agent/specs/builtin/verifier.md maa_api/agent/specs/builtin/daily_ops.md
git commit -m "docs(m87): 研究循环、验证员核对与每日汇报提示词"
```

---
### Task 7: 评测场景（spec T7）

**Files:**
- Create: `tests/evals/scenarios/m87-daily-machine-pass.yaml`、`m87-daily-failed-pipeline.yaml`、`m87-copilot-two-rounds.yaml`、`m87-copilot-round-exhausted.yaml`、`m87-copilot-model-judged.yaml`、`m87-waiver.yaml`
- Create: `tests/evals/cassettes/` 同名 6 份 `.jsonl`
- Modify: `tests/evals/test_regression_replay.py`（计数 13 → 19；加驱动用例）

**Interfaces:**
- Consumes: `entry.kind="ticket"` 的 `drive_ticket`、`Scenario.human`、`World.copilots`/`copilot_results`、`ScriptedCopilotAttempt`、`copilot_loop.max_iterations`（Task 4）。
- Produces: 6 个场景与 cassette；`test_every_scenario_has_a_driver`。

- [ ] **Step 1: 改测试计数与驱动用例（先红）**

`tests/evals/test_regression_replay.py`：`test_every_scenario_loads_and_has_a_cassette` 的 `len(scenarios)`、`len(ids)` 都从 13 改 19；新增：

```python
def test_every_scenario_has_a_driver() -> None:
    from maa_api.agent.evals.runner import DRIVERS
    for scenario in load_scenarios(SCENARIOS_DIR):
        assert scenario.entry.kind in DRIVERS
    assert "ticket" in DRIVERS
```

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_regression_replay.py > /tmp/m87/t7-red.log 2>&1; echo $?; tail -n 6 /tmp/m87/t7-red.log
```

Expected：非 0；`assert 13 == 19`（其余 regression 用例仍通过）。

- [ ] **Step 2: 写 6 个场景 YAML**

JSON-in-YAML，`id` 与文件名主干一致、只用小写字母/数字/连字符；`suite` 都是 `capability`，`agent` 写对应 Worker；`entry` 四个字段（`worker`/`title`/`brief`/`scope`）写全；`limits.timeout_seconds` 60、`max_model_calls` 30。各场景的 `world`、`human`、`settings` 与 `expect` 按下面写死（`grader` 的匹配器形状照 `tests/evals/scenarios/legacy-daily-submit.yaml`；`outcome_state.runs` 的 `all` 是列表，按顺序做子序列匹配，照 `tests/evals/test_ticket_driver.py:358-370`，每项直接写字段，不要把 `all` 的值写成以 `any` 为键的映射）：

```text
m87-daily-machine-pass：entry.worker="daily_ops"；brief「用一条流水线把今天的日常跑完：Fight 1-7 三次，再领奖励。不要用药。」；world.core_script="success"、screen="home"；
  tool_sequence match="subsequence"：submit_pipeline → wait_pipeline → report；forbid=["delegate"]；max_calls={"report": 1}；
  outcome_state：runs={"all": [{"agent_name": "daily_ops", "status": "succeeded", "finish_reason": "report_submitted"}]}；pipelines={"last": {"status": "completed"}}；model_calls={"lte": 6}
m87-daily-failed-pipeline：入口同前；world.core_script="failure"；
  steps：report → delegate → report；max_calls={"report": 2, "delegate": 1}；
  runs={"all": [{"agent_name": "verifier", "status": "succeeded"}]}；pipelines.last.status="failed"
m87-copilot-two-rounds：entry.worker="copilot"；brief「用作业 cp-eval-1-7 打 1-7，要三星。」；world.core_script="success"、screen="stage"；
  world.copilots=[{"id": "cp-eval-1-7", "title": "1-7 三星作业", "stage_id": "main_01-07", "stage_code": "1-7"}]；world.copilot_results=["failed", "three_star"]；human.confirmations="approve"；
  min_calls={"run_copilot_attempt": 2}；max_calls={"report": 1}；forbid=["delegate"]；
  confirmations={"count": 1, "statuses": ["approved"]}；runs={"all": [{"agent_name": "copilot", "status": "succeeded", "finish_reason": "report_submitted"}]}；model_calls={"lte": 8}
m87-copilot-round-exhausted：入口与 brief 同前；settings={"copilot_loop.max_iterations": 2}；copilot_results=["failed", "failed"]；human.confirmations="approve"；
  steps：run_copilot_attempt → run_copilot_attempt → report{"status": "waiting", "human_action": {"type": "decision", "prompt": "这一轮授权用完了，是否再授权一轮？"}}；min_calls={"run_copilot_attempt": 2}；max_calls={"run_copilot_attempt": 2, "report": 1}；confirmations={"count": 1, "statuses": ["approved"]}
m87-copilot-model-judged：入口同前；copilot_results=["unknown"]、screen="settlement"；human.confirmations="approve"；
  steps：run_copilot_attempt → record_copilot_result → report → delegate → report；max_calls={"record_copilot_result": 1, "report": 2, "delegate": 1}；runs={"all": [{"agent_name": "verifier", "status": "succeeded"}]}
m87-waiver：entry.worker="daily_ops"；brief 同第一条；world.core_script="failure"；human.questions={"answer": ["仍按完成结束"]}；
  steps：report → delegate → report → delegate → report → ask_user → report；min_calls={"report": 4, "delegate": 2, "ask_user": 1}；
  questions={"count": 1, "statuses": ["answered"]}；runs={"all": [{"agent_name": "daily_ops", "status": "succeeded", "finish_reason": "report_submitted"}]}
```

`m87-copilot-round-exhausted` 只回放模型行为：维护性补丁的假尝试不消耗授权、`remaining` 恒为 `None`（13 §8.179 实施裁决 6/8），场景验不了「旧授权落 `exhausted` 并弹新卡」。授权状态机由 Task 4 的 `test_ticket_session_exhausted_grant_opens_new_card` / `test_chat_session_exhausted_grant_still_raises` 单测覆盖，台账「Rulings」写明这一降级。

- [ ] **Step 3: 写 6 份 cassette**

每份第一行 header 照 `legacy-daily-submit.jsonl`（`source: "handwritten"`、`model: "eval-model"`、`agent` 写 Worker、`spec_version: null`）；之后先 2 条 `{"type":"call","channel":"agent","reply":{"text":"OK"}}`；再按调用顺序写实质应答（`tool_calls` 的 `id` 用 `call-submit-1`/`call-wait-1`/`call-report-1`/`call-delegate-1`/`call-attempt-1`/`call-record-1`/`call-ask-1` 这类固定写法，参数引用用 `{{result:<id>:<字段>}}`）；结束于 `ready`/`failed`/`dropped` 的场景末尾加 1 条 `{"text": "{\"items\": []}"}`。各场景条数与调用：

```text
m87-daily-machine-pass（3 + 探查 2 + 记忆 1）：submit_pipeline → wait_pipeline(pipeline_id 占位) → report(ready)
m87-daily-failed-pipeline（6 + 2 + 1）：submit_pipeline → wait_pipeline → report(ready) → delegate(verifier) → verifier {"text": "{\"passed\": false, \"confidence\": 0.2, \"evidence\": [], \"notes\": \"流水线失败，日常没跑完\"}"} → report(failed)
m87-copilot-two-rounds（4 + 2 + 1）：get_screenshot → run_copilot_attempt(copilot_id="cp-eval-1-7", support="none") → run_copilot_attempt 同参数 → report(ready)
m87-copilot-round-exhausted（3 + 2）：run_copilot_attempt ×2 → report(waiting, human_action={"type": "decision", "prompt": "这一轮授权用完了，是否再授权一轮？"})（waiting 不加记忆抽取）
m87-copilot-model-judged（6 + 2 + 1）：run_copilot_attempt → record_copilot_result(attempt_id 占位, outcome="three_star") → report(ready) → delegate(verifier) → verifier {"text": "{\"passed\": true, \"confidence\": 0.9, \"evidence\": [\"截图显示三星\"], \"notes\": \"\"}"} → report(ready)
m87-waiver（11 + 2 + 1）：submit_pipeline → wait_pipeline → report(ready) → delegate → verifier 不通过 → report(ready) → delegate → verifier 不通过 → report(ready) → ask_user(question="验证两次都没过，仍按完成结束吗？", options=[{"label":"仍按完成结束"},{"label":"继续补救"}]) → report(ready, details={"verification_waiver":"{{result:call-ask-1:question_id}}"})
```

`delegate` 的 `task`/`context` 按 §4.3 的 `suggest()` 口径写（`task` 含工单标题与声称，`context` 含流水线与尝试记录）；验证员的应答是 `{"text": "<verifier_verdict 的 JSON 字符串>"}`，两条都不通过时 notes 写「流水线失败，日常没跑完」。

- [ ] **Step 4: 逐个跑新场景并修 id/占位符**

```bash
for id in m87-daily-machine-pass m87-daily-failed-pipeline m87-copilot-two-rounds m87-copilot-round-exhausted m87-copilot-model-judged m87-waiver; do
  .venv/bin/python scripts/agent_eval.py --suite capability --mode replay --scenario "$id" > "/tmp/m87/$id.log" 2>&1; echo "$id=$?"; tail -n 2 "/tmp/m87/$id.log"
done
```

Expected：六行都是 `=0`；每份日志的用例为 `passed`、没有 `skipped`。失败时按日志改 cassette 的 id/占位符/顺序，不打开 `--strict`。

- [ ] **Step 5: 跑全量与 pytest**

```bash
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay --min-pass-rate 1.0 > /tmp/m87/cap.log 2>&1; echo "cap=$?"; tail -n 8 /tmp/m87/cap.log; echo "skip=$(grep -c "跳过" /tmp/m87/cap.log || true)"
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_regression_replay.py > /tmp/m87/t7.log 2>&1; echo "test=$?"; tail -n 5 /tmp/m87/t7.log
```

Expected：`cap=0`、`skip=0`、末行 `EVAL OK`、汇总 `14/14 通过（100.0%）`；`test=0`。

- [ ] **Step 6: 提交**

```bash
git add tests/evals/scenarios/m87-*.yaml tests/evals/cassettes/m87-*.jsonl tests/evals/test_regression_replay.py
git commit -m "test(m87): 完成闸门与作业研究循环的 6 个评测场景"
```

---
### Task 8: 文档同步、全量验收与合并（spec T8 + §7）

**Files:**
- 核对（预期不改）：`docs/v3/05-运行时与代理定义.md`、`docs/v3/02-系统架构.md`、`docs/v3/03-数据模型.md`、`docs/v3/04-接口与事件协议.md`
- Modify: `docs/v3/14-现行接口清单.md`、`docs/v3/16-部署指南.md`、`docs/v3/13-决策记录.md`
- Modify: `docs/v3/ledgers/m87-verifier-copilot-loop.md`（补全验收证据与 Rulings）

**Interfaces:**
- Produces: §9 的已预填项逐条核对通过；剩余 4 处（14 §6.16、16、13 §8、台账）补齐；tag `v3-m87`。
- Consumes: Task 1–7 的全部改动。

- [ ] **Step 1: 核对已预填项，只补缺口**

剩余 4 处：14 §6.16、16、13 §8、台账；前两处本步补，后两处 Step 2 补。以下位置已含 M87 内容，逐条核对后**不改写**：

- 05 §1.1 样例（`:24` 已有 `verify_before_ready: true`）、§1.2 字段表（`:48`）、§3.2 的 `daily_ops`/`copilot` 两行（`:195-196`）、§4.1 的拒绝形状与 `details.verification`/`verification_waiver`（`:225-227`）、§5.2 第 17 位（`:294`）；
- 02 §2.2 的 `VerificationGate` 行（`:115`）、§4.1 的 `workers/` 行（`:163`）；
- 03 §3.2 的 `meta.verdict`（`:101`）、§6.3 的 `details`（`:230`）；
- 04 §6 的 `copilot_loop.max_iterations` 行（`:323`）。

05 §6.1（`:316`）保持现状：该句已是「当前 104 个、模型可见 103 个，M85 收口基线；M86 后 107/106，M87 不变，M88 后 108/107，M89 后 110/109，M90 后 111/110」的累计口径，**不**照 spec T8 第 2 步字面改成「当前 107 个、模型可见 106 个」，否则会覆盖 M88–M90 的增量。这条偏离与 Task 6 的提示词差异一起记 13 §8。

只补剩余两处文档：

1. 14 §6.16 的作业授权段补 `max_attempts` 是确认 payload 的服务端字段、用户 `overrides` 不接受（`OverridesRequest` 的 `extra="forbid"` 已拒）；同时说明 `payload` 的服务端字段在 v2 聊天会话里不出现。
2. 16 在 §10「三个 `agent` 段的键」表后补一段 `copilot_loop.max_iterations`（顶层 `copilot_loop` 段，默认 3，范围 1–10，工单会话里每份作业授权的最多尝试次数，`hot_action="none"`）。

- [ ] **Step 2: 补 13 §8 与台账**

13 §8 末尾按落地时的下一空号补一条实施记录（用户口径、实际、与 spec 的偏离、计数、spec/plan/ledger 链接）。至少写明三条偏离：copilot 提示词第 2 步的导航工具归属按 M86 改为「要求导航员处理」；05 §6.1 保持累计口径、不改成单点 107/106；评测的 round-exhausted 只回放模型行为、授权状态机由 Task 4 单测覆盖。台账按 `docs/v3/specs/README.md` §4 模板补全：分支、基点、tag、§0 结果、T0–T8 表格、Rulings（本计划「接缝裁决」里实际用到的条目 + copilot 导航归属 + round-exhausted 的降级）、验收表（命令与结果）、真机补充验证结果（未验证的写「未验证」）。

- [ ] **Step 3: 跑 §7.1 验收命令**

```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/m87/pytest.log 2>&1; echo $?; tail -n 30 /tmp/m87/pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
( cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build ) > /tmp/m87/web.log 2>&1; echo $?
.venv/bin/python scripts/frontend_smoke.py | tail -n 1
.venv/bin/python scripts/agent_smoke.py | tail -n 1
.venv/bin/python scripts/orchestrator_smoke.py | tail -n 1
.venv/bin/python scripts/copilot_research_smoke.py | tail -n 1
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m87/reg.log 2>&1; echo $?; tail -n 3 /tmp/m87/reg.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay --min-pass-rate 1.0 > /tmp/m87/cap.log 2>&1; echo $?; tail -n 3 /tmp/m87/cap.log; grep -c "跳过" /tmp/m87/cap.log || true
git diff --check; echo $?
grep -n "max_attempts" docs/v3/14-现行接口清单.md
grep -n "copilot_loop.max_iterations" docs/v3/16-部署指南.md
.venv/bin/python scripts/check_doc_links.py --all > /tmp/m87/links.log 2>&1; echo $?; tail -n 3 /tmp/m87/links.log
```

Expected：全量 pytest 与开工前的 `dev` 相比不新增失败（Task 0 基线是 3 failed / 5261 passed，3 条既有真资源失败；M86 合并后以 Task 0 实测为准，起点已有的失败记台账）；OpenAPI 不变（退出码 0）；前端四步 0、`frontend_smoke` 末行 `SMOKE OK`；四个冒烟末行都是 `SMOKE OK`（`copilot_research_smoke.py` 证明 v2 聊天会话口径没回退）；regression `EVAL OK`；capability `EVAL OK`、`14/14 通过（100.0%）`、`跳过` 计数 0；`git diff --check` 0；两条 `grep` 各至少 1 行；断链总数与开工前一致（Task 0 记的基线，今天 4 条全在 `docs/copy-audit/ai-tells-docs.md`）。

- [ ] **Step 4: 合并、tag 与复验**

```bash
git checkout dev
git merge --no-ff verifier-copilot-loop
git tag v3-m87
git branch -d verifier-copilot-loop
```

合并后在 `dev` 上复跑 Step 3 里受影响的命令（全量 pytest、capability、`git diff --check`），把 merge commit 与复验结果补进台账，再提交到 `dev`。汇报给主 agent 时带：`git log --oneline dev -12`、§0 实测值、§7.1 每条的退出码与末行、6 个新场景的 passed 计数、真机项（§7.2）的完成情况。

---
## Self-Review
1. **覆盖规格**：§0（Task 0）、§4.1 数据契约（Task 2/3）、§4.2 文件与公开形状（Task 2/4）、§4.3 拒绝形状（Task 2/3）、§4.4 配置（Task 4）、§4.5.1–§4.5.2 判定（Task 2）、§4.5.3 汇报接入（Task 3）、§4.5.4 结论落库（Task 3）、§4.5.5 循环与授权（Task 4/5/6）、§4.5.6–§4.5.7（Task 2/3 用例）、§4.6 前端（Task 5）、§4.7 提示词（Task 6；copilot 第 2 步的导航归属按 M86 工具集改写，差异记 13 §8）、§5 T1–T8（Task 1–8）、§6 测试清单（逐条落进 Task 1–5 与 7）、§7 验收（Task 8）、§9 文档（Task 8；05 §6.1 保持累计口径是已知偏离）、§11 契约名（Task 8）。没有任务外要求。
2. **步骤扫描**：每个代码步骤给的是签名、过滤条件、文案出处与算法条目（§4.5.2 已由 spec 定死），没有写函数体；每个测试步骤给用例名与断言值；每个版本/计数给固定数字；没有「自行决定」的留白。
3. **类型一致**：`GateDecision.report_details()`、`merge_verification(details, verification)`、`VerificationGate.check/record_verdict`、`CopilotGrantService.approve(max_attempts=…)`/`mark_exhausted`、`CopilotAttemptRepository.list_for_session`、`CopilotGrantRequest.maxAttempts` 在 Task 2/3/4/5 的 Interfaces 与后续步骤里名字、参数、返回一致。
4. **Review Focus**：五条各自落到 T2 的 `test_subagent_device_writes_do_not_count`/`test_read_only_audits_are_not_required`、T3 的 `test_rejected_ready_keeps_ticket_and_run_going`/`test_rejected_report_does_not_stop_the_run`/`test_rejected_report_continues_to_next_batch`、T4 的 `test_ticket_session_exhausted_grant_opens_new_card`/`test_chat_session_exhausted_grant_still_raises`、T7 的 6 个场景与 `test_every_scenario_has_a_driver`；没有只在 Review Focus 里出现、却无测试的模式。
5. **比例**：计划约 755 行，spec 929 行；代码块只有签名、断言、shell 与场景期望，没有实现正文；重复的旧测试代码用「照 `test_worker_service.py` 的 `_Harness` 复制」指代，没有逐段抄。超出 spec 的裁决（均已写进对应 Task 与台账口径）：`getattr(ctx.spec, "verify_before_ready", False)`（既有用例的 `SimpleNamespace` 没有该字段，Task 3）；copilot 提示词第 2 步把导航工具交给导航员处理（Task 6）；05 §6.1 保持累计口径而不改成单点 107/106（Task 8）。
