> 状态：TODO（先搁置，不要照此实施）
> 阶段：P4。领域亮点及之后的规格先搁置，等 P0–P3（M63–M85）落地后再决定是否升为可执行规格。
> 本文件正文是提前写好的草稿，执行者不要从这里开工。

# M87 验证闸门与作业研究闭环（规格）

> 阶段：P4　依赖：M86　分支：`verifier-copilot-loop`　迁移：无
> 本文是 M87 的唯一规格；实施偏离记在 [13 §8](../../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在本分支、工作区干净 | `git status -sb` | 第一行 `## verifier-copilot-loop`，没有其他行 |
| 2 | M86 已合并 | `git tag -l 'v3-m86'` | 输出 `v3-m86` |
| 3 | 四个代理定义存在 | `rg -l "^name: (verifier\|copilot\|daily_ops\|general)$" maa_api/agent/specs/builtin/` | 4 个文件 |
| 4 | 验证员的输出 schema（M70） | `test -f maa_api/agent/llm/schemas/verifier_verdict.json && echo ok` | `ok` |
| 5 | 代理定义模型（M65） | `rg -n "class AgentSpec\b" maa_api/agent/specs/schema.py` | 1 行；记下它是否 `extra="forbid"` |
| 6 | 汇报中间件（M70） | `rg -n "class ReportMiddleware" maa_api/agent` | 1 行；记下文件路径 |
| 7 | 写汇报行的位置（M70） | `rg -n "TicketReport\(" maa_api/agent` | 至少 1 行；记下在哪个函数里 |
| 8 | 委派服务（M70） | `rg -n "class DelegationService" maa_api/agent/workers/delegation.py` | 1 行 |
| 9 | 工单相关列（M69、M71） | `.venv/bin/python -c "from maa_api.db.models import AgentAudit as A, AgentRun as R, AgentSession as S; print(hasattr(A,'ticket_id'), hasattr(R,'ticket_id'), hasattr(S,'ticket_id'), hasattr(R,'meta'))"` | `True True True True` |
| 10 | 租约在长工具调用期间续租（M71） | `rg -n "renew" maa_api/agent/lease.py` | 至少 1 行，且 `LeaseMiddleware` 在工具执行期间调用它 |
| 11 | 导航工具（M86） | `rg -n "\"navigate_to\"" maa_api/agent/tools/nav.py` | 至少 1 行 |
| 12 | v2 作业授权入口 | `rg -n "async def _evaluate_copilot_attempt\|async def _apply_copilot_grant_overrides\|    async def approve" maa_api/agent/policy.py maa_api/agent/confirmation.py maa_api/services/copilot/grant.py` | 3 行 |

第 7 条找到不止一处写汇报行、或第 10 条不成立时，按 §10 停下来问。

## 1. 背景与目标

v3 的 Worker 每轮以 `report` 收尾，`ready` 会把工单置为 `done` 并转给总控（05 §4.1）。`ready` 现在只是模型的自述。v2 的实机复盘里有过两类误报：流水线失败了却说做完了；作业尝试没有记录结果就收尾（[v2/13 §8.80](../../../v2/13-决策记录.md)、`playbook-stage-clear` §7）。M70 已经建了只读的验证员子代理 `verifier`，但还没有人用它把关。

作业研究方面，v2 M29 有作业授权（确认动作 `grant_copilot_attempts`）、执行一次尝试（`run_copilot_attempt`）、模型补判（`record_copilot_result`），但「选作业 → 停到开始行动页 → 执行 → 判结果 → 改作业」的循环完全靠提示词，位置要用户手动摆好（v2 Q41）。

做完能验证的结果：

1. 代理定义新增 `verify_before_ready`；`daily_ops`、`copilot`、`general` 设为 true。这三类工单在本工单做过写设备操作之后汇报 `ready`，必须先过闸门。
2. 闸门按三类依据放行：流水线终态、内核的结算识别、验证员的结论；三者都不满足时 `report` 返回 `accepted=false` 并给出现成的委派参数，工单状态不变。
3. 验证员的结论落在子运行的 `agent_run.meta["verdict"]`；只认最后一次写设备操作之后的结论。
4. 验证失败有界：同一批操作之后两次验证不通过，只能汇报 `failed`、`blocked`，或经用户在提问卡上选「仍按完成结束」放行。
5. 作业研究工单按固定的五步循环推进，第 2 步交给导航员（M86 的 `navigate_to`）；工单会话里每份作业授权最多 `copilot_loop.max_iterations` 次尝试，授权卡上的次数上限随之变化。
6. 评测集新增 6 个场景覆盖放行、拒绝、复核、用户放行与两种循环结局。

## 2. 非目标

- 不新增工具、表、端点、错误码。
- 不验证只读工单（没有写设备操作就不过闸门）。
- 不改 v2 聊天会话的作业授权口径：没有工单的会话仍是 1–10 次、默认 3。
- 不让内核读关卡星数；验证员看现拍截图。
- 不改 `run_copilot_attempt` 的阻塞行为（最长 `agent.copilot_attempt_timeout_minutes`）。

## 3. 相关文档

- [05 §1.2、§3.2、§3.3、§4.1、§4.2、§8](../../05-运行时与代理定义.md)（代理定义字段、copilot 与 verifier、汇报与委派契约、`verifier_verdict`）；[02 §6.1–6.2](../../02-系统架构.md)（时序与确认冒泡）；[04 §6](../../04-接口与事件协议.md)（`copilot_loop.max_iterations`）。
- v2：`rg -n "^### 8.44" docs/v2/13-决策记录.md`（M29 作业授权的状态机、计次与判定表）；`rg -n "^## 9" docs/v2/superpowers/specs/2026-10-09-m25-prts-knowledge.md`（M29 规格）；`docs/v2/superpowers/specs/2026-10-14-m49-stage-clear-playbook.md`（攻克关卡的决策链）；`rg -n "M29" docs/v2/README.md`（作业研究口径与「开始行动页」前置）。
- 既有代码入口：`maa_api/agent/policy.py` 的 `_evaluate_copilot_attempt`（约 211 行）；`maa_api/agent/confirmation.py` 的 `_apply_copilot_grant_overrides`（约 378 行）；`maa_api/services/copilot/grant.py` 的 `CopilotGrantService.approve`（约 327）、`_require_attempts`（约 680）、`get_active`（约 290）；`maa_api/services/copilot/attempt.py` 的 `CopilotAttemptService.run`（约 275，返回里有 `outcome`、`judged_by`、`success`、`remaining`、`grant_status`、`pipeline_id`、`recognition_pipeline_id`、`exit_pipeline_id`）；`maa_api/db/repositories/copilot.py` 的 `CopilotAttemptRepository.find`（约 275，扫 `kind=copilot` 资源的 `meta["attempts"]`）；`maa_api/domain/enums.py` 的 `QuestionStatus`、`PipelineStatus`；`web/src/features/copilots/CopilotGrantCard.tsx` 的 `MAX_ATTEMPTS`、`attemptsOf`、`parseCopilotGrantPayload`。
- 知识条目 `maa_api/agent/knowledge/builtin/playbook-stage-clear.md`（M78 迁成技能后按技能 id 读）。

`grant_copilot_attempts` 是确认动作名，不是工具；v2 没有同名工具。

## 4. 设计

### 4.1 数据模型

无新表、无迁移。用到两个既有 JSON 列，整列赋值：

- `agent_run.meta["verdict"]`（验证员子运行）：`{passed: bool, confidence: float, evidence: [str], notes: str, degraded: bool, recorded_at: "…Z"}`。
- `ticket_report.details["verification"]`（被放行的 ready 汇报）：`{method: "not_required"|"machine"|"verifier"|"waiver", verdict_run_id?: str, question_id?: str, evidence: [str]}`。

### 4.2 模块与文件

| 文件 | 新建/修改 | 职责 |
|---|---|---|
| `maa_api/agent/workers/verification.py` | 新建 | `VerificationGate`、`GateDecision`、常量 |
| `maa_api/agent/specs/schema.py` | 改 | `AgentSpec` 加 `verify_before_ready: bool = False` |
| §0 第 6、7 条找到的汇报代码 | 改 | `ready` 先过闸门 |
| `maa_api/agent/workers/delegation.py` | 改 | 验证员子运行结束时记结论 |
| `maa_api/db/repositories/copilot.py` | 改 | `CopilotAttemptRepository.list_for_session` |
| `maa_api/agent/policy.py` | 改 | 工单会话的授权预填与 `max_attempts` |
| `maa_api/agent/confirmation.py`、`maa_api/services/copilot/grant.py` | 改 | 批准时按 `max_attempts` 夹住次数 |
| `maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml` | 改 | `copilot_loop.max_iterations` |
| `maa_api/agent/specs/builtin/{copilot,verifier,daily_ops,general}.md` | 改 | §4.7 |
| `web/src/features/copilots/CopilotGrantCard.tsx` | 改 | 次数下拉框按 `max_attempts` 截断 |
| `tests/evals/scenarios/m87_*.yaml` | 新建 | 6 个评测场景 |

`verification.py`：

```python
EFFECT_TOOLS = frozenset({"submit_pipeline", "run_copilot_attempt",
                          "click", "swipe", "long_press", "input_text", "key_event"})
RAW_TOOLS = frozenset({"click", "swipe", "long_press", "input_text", "key_event"})
VERIFY_MIN_CONFIDENCE = 0.7
VERIFY_MAX_ROUNDS = 2
WAIVER_OPTION = "仍按完成结束"
MAX_CONTEXT_CHARS = 4000

GateMethod = Literal["not_required", "machine", "verifier", "waiver"]
GateReason = Literal["verification_required", "verification_failed",
                     "verification_exhausted", "waiver_invalid"]

@dataclass(frozen=True, slots=True)
class GateDecision:
    accepted: bool
    method: GateMethod | None
    reason: GateReason | None
    evidence: tuple[str, ...]
    verdict_run_id: str | None = None
    question_id: str | None = None
    delegate: Mapping[str, str] | None = None     # {"agent": "verifier", "task": …, "context": …}

    def report_details(self) -> dict[str, Any]: """写进 ticket_report.details["verification"]。"""
    def tool_result(self) -> dict[str, Any]: """拒绝时 report 工具的返回（§4.3）。"""

class VerificationGate:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None: ...
    async def check(self, *, ticket_id: str, ticket_title: str, worker_session_id: str,
                    claim: str, details: Mapping[str, Any] | None) -> GateDecision: ...
    async def record_verdict(self, *, run_id: str, verdict: Mapping[str, Any] | None,
                             degraded: bool) -> None: ...
```

`CopilotAttemptRepository` 新方法：

```python
async def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
    """扫 kind=copilot 资源的 meta["attempts"]，取 session_id 相同的尝试，按 started_at 升序。"""
```

`CopilotGrantService.approve` 加关键字参数 `max_attempts: Any = None`：给了就按 `_require_attempts` 校验它本身，再把 `resolved_attempts = min(resolved_attempts, max_attempts)`。

### 4.3 接口

没有新端点。变化的是两处返回与一处确认载荷。

`report` 被闸门拒绝时的工具结果（`ToolOutcome.status="ok"`、`stop_run=False`，不写 `ticket_report`，工单状态不变，审计照常记这次调用）：

```json
{
  "accepted": false,
  "reason": "verification_required",
  "message": "这张工单做过写设备的操作，汇报完成前要先核对：按 delegate 委派验证员，通过后再汇报 ready。",
  "evidence": ["流水线 3f2a…（日常）状态 failed：任务 Fight 失败"],
  "delegate": {
    "agent": "verifier",
    "task": "核对工单「日常」是否完成：日常已做完，理智清空",
    "context": "执行记录：\n- 流水线 3f2a…「日常」failed，结束于 2026-11-20T08:12:03Z\n核对方法：get_pipeline 看各任务状态，get_screenshot 看当前画面"
  }
}
```

`reason` 与 `message`：

| reason | message | 带 `delegate` |
|---|---|---|
| `verification_required` | 上面那句 | 是 |
| `verification_failed` | 「验证员认为还没完成：{notes}。能补救就补救后再委派验证员；补救不了就汇报 failed 或 blocked。」 | 是 |
| `verification_exhausted` | 「这批操作之后已经两次验证不通过，只能汇报 failed 或 blocked；用户明确接受现状时，用 ask_user 让用户选「仍按完成结束」，再带 verification_waiver 汇报。」 | 否 |
| `waiver_invalid` | 「verification_waiver 对不上一张已回答、选了「仍按完成结束」的提问卡。」 | 否 |

放行时 `report` 的结果照 M70 原样，汇报行的 `details` 多一个 `verification` 键（§4.1）。

授权确认载荷：工单会话里由策略弹出的 `grant_copilot_attempts` 确认，`payload` 多一个键 `max_attempts`（int，等于当时的 `copilot_loop.max_iterations`）；没有工单的会话不带这个键。

### 4.4 配置项

| 键 | 类型 | 默认 | 范围 | 分组 | config-only | `_FIELDS` |
|---|---|---|---|---|---|---|
| `copilot_loop.max_iterations` | int | 3 | 1–10 | Agent | 否 | `dict(label="作业研究每轮最多尝试次数", group="Agent", type="integer", minimum=1, maximum=10, hot_action="none")` |

`settings.py`：`class CopilotLoopSettings(BaseModel)`（`extra="ignore"`；`max_iterations: int = Field(default=3, ge=1, le=10)`），`Settings` 加 `copilot_loop: CopilotLoopSettings = Field(default_factory=CopilotLoopSettings)`；`SETTING_KEYS` 加 `"copilot_loop.max_iterations": ("copilot_loop", "max_iterations")`；环境变量 `MAA_COPILOT_LOOP_MAX_ITERATIONS`。

`config.template.yaml`：

```yaml
# 作业研究闭环（M87）：工单里每份作业授权最多试几次；用完后由用户决定要不要再授权一轮。
copilot_loop:
  max_iterations: 3
```

### 4.5 行为与算法

#### 4.5.1 哪些工单要过闸门

| Worker | `verify_before_ready` | 计入的写设备操作 | 可直接放行的机器证据 |
|---|---|---|---|
| `daily_ops` | true | `submit_pipeline` | 本工单的流水线全部 `completed` |
| `copilot` | true | `run_copilot_attempt`、`submit_pipeline` | 最近一次尝试 `success=true` 且 `judged_by="core"`；另外提交的流水线全部 `completed` |
| `general` | true | `EFFECT_TOOLS` 全部 | 只用过 `submit_pipeline`、`run_copilot_attempt` 时同上；用过任何原子操作就没有机器证据 |
| 其余 Worker（`planner`、`analyst`、`diagnose`、`ops`） | false | — | 不过闸门 |

「本工单的流水线」：`pipeline.agent_session_id` 属于 `agent_session.ticket_id = 本工单` 的会话（Worker 与它的子代理）。作业尝试自己提交的流水线（尝试记录里的 `pipeline_id`、`recognition_pipeline_id`、`exit_pipeline_id`）不参与流水线检查：打输了的那一次本来就会是 `failed`，它的结论看尝试记录。

`navigate_to` 与 `back_to_home` 不计入：导航本身有逐步校验，回主界面是幂等的复位。

#### 4.5.2 闸门算法

```
check(ticket_id, ticket_title, worker_session_id, claim, details):
    effects = agent_audit 里 ticket_id=本工单、tool_name ∈ EFFECT_TOOLS、status='success' 的行
    if not effects: return accept("not_required")
    last_effect_at = max(e.created_at for e in effects)
    used = {e.tool_name for e in effects}

    # 1. 机器证据
    if not (used & RAW_TOOLS):
        ok = True; evidence = []
        if "run_copilot_attempt" in used:
            latest = list_for_session(worker_session_id) 里 finished_at 最大的尝试
            ok = latest is not None and latest.success and latest.judged_by == "core"
            evidence += [尝试描述]
        others = 本工单流水线 − 尝试流水线
        if "submit_pipeline" in used:
            ok = ok and others and all(p.status == "completed" for p in others)
            evidence += [每条流水线一句]
        if ok: return accept("machine", evidence)

    # 2. 验证员结论（只认最后一次写设备操作之后结束的）
    verdicts = agent_run 里 ticket_id=本工单、agent_name='verifier'、status='succeeded'、
               finished_at > last_effect_at、meta 有 verdict 的行，按 finished_at 升序
    latest = verdicts[-1] if verdicts else None
    if latest and latest.verdict.passed and latest.verdict.confidence >= 0.7:
        return accept("verifier", latest.verdict.evidence, verdict_run_id=latest.id)

    # 3. 用户放行
    waiver = (details or {}).get("verification_waiver")
    if waiver:
        if waiver_valid(waiver, worker_session_id, after=latest.finished_at if latest else last_effect_at):
            return accept("waiver", question_id=waiver)
        return reject("waiver_invalid")

    failed = [v for v in verdicts if not (v.passed and v.confidence >= 0.7)]
    if len(failed) >= VERIFY_MAX_ROUNDS: return reject("verification_exhausted")
    if failed: return reject("verification_failed", delegate=suggest())
    return reject("verification_required", delegate=suggest())
```

`waiver_valid`：`agent_question` 里 `id=waiver`、`session_id=worker_session_id`、`status='answered'`、`selection` 含 `WAIVER_OPTION`、`answered_at > after`，五条都满足。

`suggest()` 生成委派参数：

- `task`：`核对工单「{ticket_title}」是否完成：{claim 前 300 字}`。
- `context`：按下面的顺序拼，总长截到 4000 字；每类最多列 10 条，超出写「另有 N 条未列出」。
  ```
  执行记录：
  - 流水线 {id 前 8 位}「{title}」{status}，结束于 {finished_at}{；失败原因：error_message 前 80 字}
  - 作业尝试 #{attempt_number} 关卡 {stage_code}：{outcome}，判定方 {judged_by}，{计次与否}
  - 原子操作 {工具名} × {次数}
  核对方法：{按 used 选下面几句}
  ```
  核对方法的句子：用过流水线写「get_pipeline 看各任务状态，失败的任务用 get_logs 看原因」；用过作业尝试写「get_screenshot 看关卡页上这一关的通关标记与星数」；用过原子操作写「get_screenshot 看当前画面是否符合声称的结果」。

`evidence` 每条一句可核对的事实，至多 10 条。

#### 4.5.3 汇报路径的改动

在 §0 第 7 条找到的、写 `ticket_report` 行之前插入：

```
if args.status == "ready" and spec.verify_before_ready and ticket_id is not None:
    decision = await gate.check(ticket_id, ticket.title, worker_session_id, claim=args.summary,
                                details=args.details)
    if not decision.accepted:
        return 工具结果 decision.tool_result()，本次调用不结束运行，不写汇报行，不改工单
    args.details = {**(args.details or {}), "verification": decision.report_details()}
```

- `spec` 是当前运行的代理定义；子代理不会调 `report`，所以只有 Worker 走到这里。
- `ReportMiddleware` 在轮末自动补的 progress 汇报不过闸门（它不是 ready）。
- 被拒绝的 `report` 仍是这一批的最后一个调用；模型下一步通常是 `delegate`。`LoopGuardMiddleware` 对连续三批完全相同的 `report` 调用照常判卡死。

#### 4.5.4 记录验证员结论

`DelegationService` 在子运行结束、结构化输出解析完成之后（成功或标为 `degraded` 都算），若被委派的代理是 `verifier`，调用 `gate.record_verdict(run_id=子运行 id, verdict=解析结果, degraded=是否降级)`：

- 正常：`meta["verdict"] = {**verdict, "degraded": false, "recorded_at": now}`。
- 降级：`meta["verdict"] = {"passed": false, "confidence": 0, "evidence": [], "notes": "验证员没有给出合法结论", "degraded": true, "recorded_at": now}`。
- `meta` 整列赋值，保留原有键。写失败只记 warning，委派结果照常返回给父 Worker。

#### 4.5.5 作业研究循环与 M29 的配合

循环由 `copilot` 的提示词驱动（§4.7.1），代码只负责三件事：授权次数的上限、尝试的记录、完成的闸门。

一圈五步：

| 步 | 做什么 | 用到的 v2/v3 能力 |
|---|---|---|
| 1 选或写作业 | `search_copilots` 找现成作业，`save_copilot_draft(site_id=…)` 存副本；找不到就 `get_stage_level_data` 后自己写 `save_copilot_draft(content=…)`；第二圈起带 `parent_id` 改上一版 | M29 作业站与草稿版本链 |
| 2 到开始行动页 | 委派 `navigator`：「把游戏停在关卡 {代号} 自己的「开始行动」页」；导航员用 `navigate_to("stage:{代号}")`，节点不存在时按 M86 的 `nearest` 手动找并 `remember_as` | M86 |
| 3 执行 | `run_copilot_attempt(copilot_id, support)`；第一次触发授权卡 | M29 授权与尝试 |
| 4 判结果 | 内核判定的 `three_star`/`two_star`/`clear`/`failed` 直接采用；`unknown` 时看结算截图 `record_copilot_result`，收尾前要验证员复核 | M29、M57 结算识别；本里程碑闸门 |
| 5 复盘 | 读 `actions` 动作摘要，判断改哪里；不原样重跑同一份 | 提示词 |

授权与循环的关系：

- 作业授权仍是 v2 的会话级授权，绑在 Worker 会话上（`copilot_grant.session_id` = 工单的 Worker 会话）；确认卡经 M71 冒泡到全局面板，标明来自哪张工单。
- 工单会话里，策略预填的次数取 `min(run_copilot_attempt 的 grant_attempts, copilot_loop.max_iterations)`，并在载荷里写 `max_attempts`；批准时次数不能超过 `max_attempts`（用户在卡上选得再大也会被夹住，前端下拉框本身也只给到这个数）。所以「一轮」就是一份授权，最多 `max_iterations` 次计次的尝试。
- 不计次的结果（`not_started`、`sanity_insufficient`、`error`、`interrupted`）按 M29 退还次数，不消耗这一轮。
- 一份授权用完（`grant_status="exhausted"`）还没成功：Worker 汇报 `waiting`，`human_action.type="decision"`，摘要交代试过哪几版、卡在哪、下一版打算怎么改。用户在工单里回复继续后，下一次 `run_copilot_attempt` 自然弹出新的授权卡，开始新一轮。授权卡是授权本身，汇报交代研究过程，两者分开。
- 理智不足按 M29 把授权标 `stopped`，Worker 汇报 `blocked`，不吃药不碎石（v2 Q17）。
- 没有工单的 v2 聊天会话不受 `copilot_loop.max_iterations` 影响。

完成闸门在循环里的位置：最近一次尝试内核判定成功 → 机器证据，直接放行；模型补判成功 → 闸门要求验证员复核，验证员现拍关卡页截图看通关标记；最近一次不成功却汇报 ready（例如用户只让试一次）→ 同样交给验证员判断声称是否属实。

#### 4.5.6 验证失败之后

Worker 拿到 `accepted=false` 后的出路，按顺序：

1. `verification_required`：照 `delegate` 委派验证员，再汇报。
2. `verification_failed`：在委托范围内能补救就补救（例如重跑失败的那个任务），补救之后又有了新的写设备操作，之前的结论自动作废，重新验证；补救不了就汇报 `failed`（说明没做成什么）或 `blocked`（需要用户决定）。
3. `verification_exhausted`：只能汇报 `failed` 或 `blocked`。用户明确接受现状时，用 `ask_user` 发卡，选项里有「仍按完成结束」；用户选了，再 `report(status="ready", details={"verification_waiver": <question_id>})`。
4. 不允许为了过闸门改写摘要后反复重试同一个 `report`。

#### 4.5.7 边界情况

| 输入 | 期望行为 |
|---|---|
| 只读工单汇报 ready | `not_required`，直接放行，`details.verification.method="not_required"` |
| 汇报 `progress`、`waiting`、`blocked`、`failed`、`dropped` | 不过闸门 |
| 验证员结论早于最后一次写设备操作 | 不算数，当作没有结论 |
| 验证员给 `passed=true`、`confidence=0.6` | 算失败的一次 |
| 验证员输出降级 | 记成失败的一次（§4.5.4） |
| 流水线还在 `running` 或 `pending` 时汇报 ready | 机器证据不成立，交给验证员 |
| 工单没有任何流水线但用过 `submit_pipeline`（提交被拒） | 机器证据不成立 |
| `verification_waiver` 指向别的会话的提问 | `waiver_invalid` |
| 用户在工单里直接说「算了就这样」而没有提问卡 | 不放行；Worker 应当发提问卡 |
| v2 聊天会话调用 `run_copilot_attempt` | 预填仍是 1–10、载荷没有 `max_attempts`，行为与 v2 相同 |
| `copilot_loop.max_iterations` 改小时已有一份授权在用 | 已批准的授权不变；新授权按新值 |

### 4.6 前端

只改 `web/src/features/copilots/CopilotGrantCard.tsx`：

- `CopilotGrantRequest` 加 `maxAttempts: number`；`parseCopilotGrantPayload` 读 `payload.max_attempts`，取整后夹到 1–10，缺省为 `MAX_ATTEMPTS`。
- 次数下拉框的选项从 `MIN_ATTEMPTS` 到 `request.maxAttempts`；`attemptsOf` 的上界改用 `maxAttempts`。
- `maxAttempts < MAX_ATTEMPTS` 时字段说明改成「最多 {value.attempts} 次；这张工单每轮最多试 {maxAttempts} 次，用完后可以再授权一轮。」，其余情况保持原文。

### 4.7 提示词与代理定义

frontmatter 改动：`daily_ops.md`、`copilot.md`、`general.md` 加一行 `verify_before_ready: true`；四个文件（含 `verifier.md`）的 `version` 各加 1。工具集都不变。

#### 4.7.1 `copilot.md` 正文

删除正文里「请用户手动把游戏停到开始行动页」一类的句子（M73 从 v2 Q41 迁来的规则，以第 2 步为准），在末尾追加：

```markdown
## 研究循环

一张作业研究工单按下面的五步循环推进，每一圈走完五步再决定下一圈。

1. 选或写作业：先 search_copilots 找现成作业，可行性为 ok 的直接 save_copilot_draft(site_id=…)；都不可行或搜不到，再 get_stage_level_data 看地图与出怪，自己写一份 save_copilot_draft(content=…)。第二圈起在上一版上改，save_copilot_draft 带 parent_id，note 写清改了什么、为什么改。
2. 到开始行动页：委派 navigator，task 写「把游戏停在关卡 <代号> 自己的「开始行动」页」，context 写关卡代号与关卡 id。尝试结束后游戏通常已经回到这一页，先 get_screenshot 确认，确认了就不用再委派。
3. 执行：run_copilot_attempt(copilot_id=…, support=…)。第一次会弹作业授权卡，用户批准的次数就是这一轮最多能试的次数。
4. 判结果：outcome 是 three_star、two_star、clear、failed 时内核已经判定，直接采用；outcome 是 unknown 时，看返回里的结算截图，用 record_copilot_result 补判。
5. 复盘：读返回里的动作摘要，判断是练度不够、时机不对还是位置不对，决定下一圈改哪里。不要原样重跑同一份作业。

什么时候停：

- 达到成功标准：report(ready)，摘要写关卡、最后用的是哪一份作业、试了几次、判定方是谁。
- 这一轮的授权用完还没成功：report(waiting)，human_action 用 decision，写清试过哪几版、卡在哪、下一版准备怎么改，问要不要再授权一轮。不要自己连着申请新授权。
- 理智不足（sanity_insufficient）：report(blocked)，说明理智不够，不要吃药或碎石。
- 连续两次 not_started：委派 navigator 重新确认位置；仍然不行就 report(blocked)，说明停在哪个画面。
- 作业站和自己写的方案都缺干员或练度：report(failed)，写清缺谁、缺多少。

汇报 ready 之前系统会核对完成证据。内核判定的成功直接通过；模型补判的结果要先让验证员复核。report 返回 accepted=false 时，照返回里的 delegate 委派 verifier，验证通过再汇报。
```

#### 4.7.2 `verifier.md` 正文末尾追加

```markdown
## 核对完成证据

你只回答一件事：Worker 声称的结果有没有被证据支持。task 里是声称，context 里是执行记录。

- 截图要现拍：get_screenshot 看当前画面。作业研究看关卡页上这一关的通关标记与星数；其他工单看画面是否与声称一致。
- 流水线看终态：get_pipeline 看 context 里列出的每条流水线和其中任务的状态，失败的任务用 get_logs 看原因。
- 结算识别：context 里 judged_by=core 的尝试是内核判定的，可以作为证据；judged_by=model 的要另用截图确认。
- 证据不足就判 passed=false，在 notes 写缺什么证据、Worker 下一步该补什么。没看到反面证据不等于通过。
- confidence 按证据强度给：现拍截图和流水线终态都对得上给 0.9 以上；只有一项对得上给 0.7 左右；有对不上的给 0.3 以下。
- evidence 每条写一句能核对的事实，例如「流水线 3f2a 状态 completed，4 个任务全部完成」。
```

#### 4.7.3 `daily_ops.md` 与 `general.md` 正文末尾追加

```markdown
## 汇报完成

做过写设备的操作之后汇报 ready，系统会先核对完成证据。流水线全部成功会直接通过；有失败，或者用过点击这类原子操作时，report 会返回 accepted=false 和一个 delegate，照它委派 verifier，通过之后再汇报。验证没通过时，能在委托范围内补救就补救后再验证；补救不了就 report(failed) 或 report(blocked) 说明情况。用户明确表示接受现状时，用 ask_user 发一张卡，选项里放「仍按完成结束」；用户选了它，再 report(ready)，details 里写 verification_waiver=<这张卡的问题 id>。
```

`general.md` 还要在这一节前面加一句：「做作业研究时按作战参谋的研究循环推进（委派 navigator 停到开始行动页、内核判定优先）。」

## 5. 实施任务

### T1 代理定义字段

- 写范围：`maa_api/agent/specs/schema.py`、四个代理定义的 frontmatter、`tests/agent/specs/test_verify_flag.py`
- 步骤：1. `AgentSpec` 加 `verify_before_ready: bool = False`；`kind != "worker"` 且为 true 时加载报错（只有 Worker 汇报）。2. 三个 Worker 的 frontmatter 加 `verify_before_ready: true`，四个文件 `version` +1。
- 验证：`.venv/bin/python -m pytest -q tests/agent/specs > /tmp/m87-t1.log 2>&1; echo $?; tail -n 3 /tmp/m87-t1.log` → `0`
- 完成判据：`AgentSpecRegistry.load()` 正常；三个 Worker 的字段为 true，其余为 false。

### T2 闸门

- 写范围：`maa_api/agent/workers/verification.py`、`maa_api/db/repositories/copilot.py`、`tests/agent/workers/test_verification_gate.py`、`tests/db/test_copilot_attempt_repository.py`
- 步骤：1. `list_for_session`（照 `find` 的扫法）。2. 按 §4.5.1、§4.5.2 实现 `check`，`record_verdict` 按 §4.5.4。查询都用 `async with session_factory() as s`，只读查询不 commit。3. 测试数据直接写 `ticket`、`agent_session`、`agent_audit`、`agent_run`、`pipeline`、`agent_question` 行（`tests/db/conftest.py` 的 `db_session_factory`）。
- 验证：`.venv/bin/python -m pytest -q tests/agent/workers/test_verification_gate.py tests/db/test_copilot_attempt_repository.py > /tmp/m87-t2.log 2>&1; echo $?; tail -n 5 /tmp/m87-t2.log` → `0`
- 完成判据：§6 中 T2 的用例全部通过。

### T3 接入汇报与委派

- 写范围：§0 第 6、7 条找到的文件、`maa_api/agent/workers/delegation.py`、它们各自的测试文件（新用例放 `tests/agent/workers/test_report_gate.py`、`tests/agent/workers/test_delegation_verdict.py`）
- 步骤：1. 汇报路径按 §4.5.3；`VerificationGate` 用 `ctx.app.state.session_factory` 现建。2. 委派按 §4.5.4。3. 用 `FakeLLM` 驱动一个 `daily_ops` 运行：先 `report(ready)` 被拒、再 `delegate(verifier)`、验证员给通过、再 `report(ready)` 被放行。
- 验证：`.venv/bin/python -m pytest -q tests/agent/workers tests/agent/harness > /tmp/m87-t3.log 2>&1; echo $?; tail -n 5 /tmp/m87-t3.log` → `0`
- 完成判据：拒绝时没有 `ticket_report` 行、工单状态不变、运行没有以 `report_submitted` 结束、`agent_audit` 有这次 `report` 的行。

### T4 作业授权的轮次上限

- 写范围：`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`maa_api/agent/policy.py`、`maa_api/agent/confirmation.py`、`maa_api/services/copilot/grant.py`、`tests/test_settings.py`、`tests/agent/test_copilot_loop_limit.py`
- 步骤：1. 配置项按 §4.4。2. `_evaluate_copilot_attempt` 没有有效授权的分支里：用 `AgentSessionRepository(context.db_session).get(context.session_id)` 取会话，`ticket_id` 不为空时 `limit = get_settings().copilot_loop.max_iterations`，`payload["attempts"] = min(payload["attempts"], limit)`、`payload["max_attempts"] = limit`。3. `_apply_copilot_grant_overrides` 把 `_pick("max_attempts")` 传给 `approve(max_attempts=…)`。4. `approve` 按 §4.2 夹住。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_copilot_loop_limit.py tests/agent/test_copilot_attempt_tools.py tests/agent/test_confirmation_service.py tests/test_settings.py > /tmp/m87-t4.log 2>&1; echo $?; tail -n 5 /tmp/m87-t4.log` → `0`
- 完成判据：v2 既有的作业授权测试一条不改仍通过。

### T5 前端授权卡

- 写范围：`web/src/features/copilots/CopilotGrantCard.tsx`、`CopilotGrantCard.test.tsx`
- 步骤：按 §4.6。
- 验证：`cd web && pnpm typecheck && pnpm test --run src/features/copilots > /tmp/m87-t5.log 2>&1; echo $?; tail -n 5 /tmp/m87-t5.log` → `0`
- 完成判据：新用例与既有用例都通过。

### T6 提示词

- 写范围：`maa_api/agent/specs/builtin/{copilot,verifier,daily_ops,general}.md`
- 步骤：按 §4.7 原文粘贴；`rg -n "手动.*开始行动|开始行动.*手动" maa_api/agent/specs/builtin/copilot.md` 应无输出。
- 验证：`.venv/bin/python -m pytest -q tests/agent/specs > /tmp/m87-t6.log 2>&1; echo $?` → `0`；`.venv/bin/python scripts/copy_inventory.py --check | tail -n 1` 不出现新的回潮项（有就改文案，不改基线）
- 完成判据：四份正文与本文一致。

### T7 评测场景

- 写范围：`tests/evals/scenarios/m87_*.yaml`、`tests/evals/cassettes/m87_*.jsonl`
- 步骤：按 M68 的场景格式（`maa_api/agent/evals/scenario.py`）写下面 6 个场景，套件 `capability`，模式 `replay`，假内核按场景给出流水线终态与作业尝试结果：

  | 场景 | 输入与假内核 | 期望 |
  |---|---|---|
  | `m87_daily_machine_pass` | daily_ops「把日常做了」；流水线 completed | `report(ready)` 一次放行，`method=machine`，模型调用 ≤ 4 |
  | `m87_daily_failed_pipeline` | 同上；流水线 failed；验证员判不通过 | 第一次 ready 被拒，委派 verifier，最终 `report(failed)`，工单 `failed` |
  | `m87_copilot_two_rounds` | copilot「三星 1-7」；第一次 failed（core），第二次 three_star（core） | 两次 `run_copilot_attempt`、第二次带新草稿（`parent_id`），一次委派 navigator，ready 放行 `method=machine` |
  | `m87_copilot_round_exhausted` | `copilot_loop.max_iterations=2`；两次都 failed | `report(waiting)`、`human_action.type=decision`，没有第三次尝试 |
  | `m87_copilot_model_judged` | 一次 unknown，模型补判 three_star | ready 第一次被拒，委派 verifier 通过后放行，`method=verifier` |
  | `m87_waiver` | daily_ops；验证员两次不通过；用户在提问卡上选「仍按完成结束」 | 最终 ready 放行，`method=waiver` |

- 验证：`.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m87-eval.log 2>&1; echo $?; tail -n 8 /tmp/m87-eval.log` → `0`，6 个新场景通过
- 完成判据：能力评测通过率不低于 M86 台账记录的基线。

### T8 文档同步

- 写范围：§9 列出的文档
- 验证：`.venv/bin/python scripts/check_doc_links.py | tail -n 1` → `BROKEN 0`

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/specs/test_verify_flag.py` | `test_verify_flag_defaults_false`、`test_only_workers_may_verify`、`test_builtin_workers_that_verify` | 缺省 false；subagent 设 true 加载报错；三个 Worker 为 true |
| `tests/agent/workers/test_verification_gate.py` | `test_no_effects_means_not_required` | 只有只读审计行 → `accepted`、`not_required` |
| 同上 | `test_completed_pipelines_pass_by_machine` | 两条 completed → `machine`，evidence 两条 |
| 同上 | `test_failed_pipeline_requires_verifier` | 一条 failed → `verification_required`，`delegate.agent=verifier`，context 含该流水线 id 前 8 位 |
| 同上 | `test_running_pipeline_is_not_evidence` | 还在 running → 拒绝 |
| 同上 | `test_raw_tools_always_need_verifier` | 用过 click → 即使流水线全成功也拒绝 |
| 同上 | `test_core_judged_success_passes` | 最近一次尝试 `success=true, judged_by=core` → `machine` |
| 同上 | `test_model_judged_success_needs_verifier` | `judged_by=model` → 拒绝 |
| 同上 | `test_attempt_pipelines_are_excluded` | 尝试自己的 failed 流水线不影响机器证据 |
| 同上 | `test_fresh_passing_verdict_passes` | 结论晚于最后写操作、passed、0.9 → `verifier`，带 `verdict_run_id` |
| 同上 | `test_stale_verdict_is_ignored` | 结论早于最后写操作 → `verification_required` |
| 同上 | `test_low_confidence_counts_as_failure` | 0.6 → `verification_failed` |
| 同上 | `test_two_failures_exhaust` | 两次失败 → `verification_exhausted`，没有 `delegate` |
| 同上 | `test_waiver_accepts_answered_question` | 选了 `仍按完成结束`、晚于最后结论 → `waiver` |
| 同上 | `test_waiver_rejects_wrong_session_option_or_time` | 三种不合格各一例 → `waiver_invalid` |
| 同上 | `test_context_is_capped` | 30 条流水线时 context ≤ 4000 字且含「另有」 |
| 同上 | `test_record_verdict_keeps_other_meta_keys`、`test_degraded_verdict_records_failure` | `meta` 原有键保留；降级记 `passed=false, degraded=true` |
| `tests/db/test_copilot_attempt_repository.py` | `test_list_for_session_filters_and_orders` | 两份作业里的尝试按会话筛出、按 `started_at` 升序 |
| `tests/agent/workers/test_report_gate.py` | `test_rejected_ready_keeps_ticket_and_run_going` | 无汇报行、工单 `open`、运行继续、审计有行 |
| 同上 | `test_accepted_ready_writes_verification_details` | 汇报行 `details.verification.method=machine` |
| 同上 | `test_non_ready_reports_skip_gate`、`test_spec_without_flag_skips_gate` | `failed` 汇报不查；`planner` 的 ready 不查 |
| `tests/agent/workers/test_delegation_verdict.py` | `test_verifier_output_is_recorded`、`test_other_subagents_are_not_recorded` | `meta["verdict"]` 写入；`researcher` 不写 |
| `tests/agent/test_copilot_loop_limit.py` | `test_ticket_session_prefill_is_clamped` | `grant_attempts=5`、上限 3 → 载荷 `attempts=3, max_attempts=3` |
| 同上 | `test_chat_session_keeps_v2_prefill` | 没有工单 → `attempts=5`，没有 `max_attempts` |
| 同上 | `test_approval_clamps_override_above_max` | 卡上改成 8 → 授权行 `attempts=3` |
| 同上 | `test_max_iterations_setting_is_hot` | 改设置后下一次预填用新值 |
| `tests/test_settings.py` | 既有整表用例 | ENV_OVERRIDES 加一行；schema 有 1–10 的范围 |
| `web/src/features/copilots/CopilotGrantCard.test.tsx` | `limits attempts to max_attempts`、`keeps ten options without max_attempts` | 下拉框选项数分别为 3 与 10；说明文字按 §4.6 |

## 7. 验收

### 7.1 命令（照 12 §4）

```bash
.venv/bin/python -m pytest -q > /tmp/m87-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m87-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build > /tmp/m87-web.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py | tail -n 1
.venv/bin/python scripts/agent_smoke.py | tail -n 1
.venv/bin/python scripts/orchestrator_smoke.py | tail -n 1
.venv/bin/python scripts/copilot_research_smoke.py | tail -n 1
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m87-reg.log 2>&1; echo $?
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m87-cap.log 2>&1; echo $?
git diff --check; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
```

期望：pytest 不新增失败；OpenAPI 不变（本里程碑没改接口）；冒烟末行都是 `SMOKE OK`（`copilot_research_smoke.py` 是 v2 M29 的冒烟，证明聊天会话口径没回退）；回归评测全部通过，能力评测不低于基线；`BROKEN 0`。

### 7.2 真机补充验证（不作为硬性门禁，结果记台账）

1. 指挥台里说「用作业三星 1-7」，确认建的是 `copilot` 工单；授权卡显示次数上限 3。
2. 观察：委派了导航员、`navigate_to` 停到 1-7 的开始行动页（第一次可能需要手动找并 `remember_as`）、尝试结束后 `report(ready)` 的汇报行 `details.verification.method` 为 `machine`。
3. 让一个日常工单故意失败（例如设备上手动停在非主界面），确认 ready 被拒、验证员被委派、最终汇报 `failed` 或 `blocked`。
4. 台账记录：每个工单的模型调用次数、验证员调用次数、是否出现误放行。

## 8. 风险与回滚

| 风险 | 对策 |
|---|---|
| 验证员误判导致做完的事被拒 | 两次失败后有用户放行；放行要结构化选项，可审计 |
| 闸门让 Worker 原地打转 | 同一批操作之后最多验证两次；`LoopGuardMiddleware` 判卡死 |
| 验证员调用增加成本 | 机器证据能放行时不调验证员；只读工单不过闸门 |
| 授权上限让用户困惑 | 授权卡说明写明「这张工单每轮最多试 N 次」；用完后的 waiting 汇报交代过程 |

回滚：`git revert -m 1 <merge>`；没有迁移。

## 9. 文档同步

- [05 §1.2](../../05-运行时与代理定义.md)：字段表加 `verify_before_ready`（bool，否，只有 `kind=worker` 能为 true）。
- 05 §4.1：补一条「`ready` 先过完成闸门（M87），被拒时不写汇报、运行继续」；§3.2 的 `daily_ops`、`copilot`、`general` 行注明 `verify_before_ready`。
- [04 §6](../../04-接口与事件协议.md)：`copilot_loop.max_iterations` 的说明写成「工单会话里每份作业授权的最多尝试次数」。
- `docs/v3/16-部署指南.md`：补 `copilot_loop.max_iterations` 一行。
- 计数：工具、端点、错误码、表都不变；配置键 +1。

## 10. 停下来问

- §0 第 7 条有不止一处写汇报行，无法确定闸门插在哪里。
- §0 第 10 条不成立：`run_copilot_attempt` 最长阻塞 20 分钟，租约 10 分钟会过期，其他工单可能在尝试中途拿到设备。
- 实现时发现需要新的错误码来表达「验证不通过」。
- 验证员需要 05 §3.3 之外的工具才能核对证据。

## 11. 需要补进契约的名字

| 名字 | 种类 | 用途 |
|---|---|---|
| `verify_before_ready` | 代理定义字段（05 §1.2） | 声明 ready 前要过完成闸门 |
| `VerificationGate`（`maa_api/agent/workers/verification.py`） | 服务（02 §2.2） | 完成闸门 |
| `agent_run.meta["verdict"]` | JSON 字段（03 §3.2） | 验证员结论 |
| `ticket_report.details["verification"]`、`details["verification_waiver"]` | JSON 字段（03 §6.3）与 `report` 参数约定（05 §4.1） | 放行依据与用户放行 |
| `report` 被拒时的结果字段 `accepted`、`reason`、`message`、`evidence`、`delegate` | 工具返回（05 §4.1） | Worker 据此委派验证员 |
| 确认载荷 `max_attempts` | 确认 payload（`grant_copilot_attempts`） | 工单会话的每轮次数上限 |
| `CopilotLoopSettings` | 设置类 | `copilot_loop.*` |
| `CopilotAttemptRepository.list_for_session` | 仓储方法 | 按会话列作业尝试 |
| `tests/evals/scenarios/m87_*.yaml`（6 个） | 评测场景 | §5 T7 |
