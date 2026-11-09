# M87 验证闸门与作业研究闭环（规格）

> 阶段：P4　依赖：M86（直接）、评测 ticket 驱动维护性补丁（本分支开工前已落地）　分支：`verifier-copilot-loop`　迁移：无
> 本文是 M87 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

本机没有 ripgrep，下表全部用 `grep`；交替匹配写成 `grep -E`。长输出重定向到 `/tmp`，只看尾部与退出码。

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在本分支、工作区干净 | `git status -sb` | 第一行 `## verifier-copilot-loop`，没有其他行 |
| 2 | M86 已合并 | `git tag -l 'v3-m86'` | 输出 `v3-m86` |
| 3 | 四个代理定义存在 | `grep -lE '^name: (verifier\|copilot\|daily_ops\|general)$' maa_api/agent/specs/builtin/*.md \| wc -l` | `4` |
| 4 | 验证员的输出 schema（M70） | `test -f maa_api/agent/llm/schemas/verifier_verdict.py && echo ok`；`grep -n 'register_schema("verifier_verdict"' maa_api/agent/llm/schemas/verifier_verdict.py` | `ok`；1 行 |
| 5 | 代理定义模型（M65） | `grep -nE '^class AgentSpecFrontmatter\|^class AgentSpec\(AgentSpecFrontmatter\)' maa_api/agent/specs/schema.py`；`grep -n 'extra="forbid"' maa_api/agent/specs/schema.py` | 2 行；2 行（记下：YAML 头由 `AgentSpecFrontmatter` 校验） |
| 6 | 汇报中间件（M70） | `grep -rn "class ReportMiddleware" maa_api/agent` | 1 行（`maa_api/agent/workers/middleware.py`） |
| 7 | 汇报写入口（M70） | `grep -n "async def submit_report" maa_api/agent/workers/service.py`；`grep -n "TicketReport(" maa_api/agent/tickets/service.py` | 1 行；2 行（607 `record_report`、832 重启恢复）；闸门放在 `submit_report` 调 `record_report` 之前，832 不过闸门 |
| 8 | 委派服务（M70） | `grep -n "class DelegationService" maa_api/agent/workers/delegation.py` | 1 行 |
| 9 | 工单相关列（M69、M71） | `.venv/bin/python -c "from maa_api.db.models import AgentAudit as A, AgentRun as R, AgentSession as S; print(hasattr(A,'ticket_id'), hasattr(A,'agent_name'), hasattr(R,'ticket_id'), hasattr(R,'parent_run_id'), hasattr(S,'parent_session_id'), hasattr(S,'ticket_id'), hasattr(R,'meta'))"` | `True True True True True True True` |
| 10 | 租约在长工具调用期间续租（M71） | `grep -n "async def renew" maa_api/agent/lease.py`；`grep -n "class LeaseMiddleware" maa_api/agent/lease.py`；`grep -n "_renew_loop" maa_api/agent/lease.py` | 各 1 行；`LeaseMiddleware` 在工具执行期间续租 |
| 11 | 导航工具（M86） | `grep -n '"where_am_i"' maa_api/agent/tools/nav.py`；`grep -n '"remember_screen"' maa_api/agent/tools/nav.py`；`grep -n '"navigate_to"' maa_api/agent/tools/nav.py` | 各 1 行 |
| 12 | v2 作业授权入口 | `grep -n "async def _evaluate_copilot_attempt" maa_api/agent/policy.py`；`grep -n "async def _apply_copilot_grant_overrides" maa_api/agent/confirmation.py`；`grep -n "^    async def approve" maa_api/services/copilot/grant.py` | 各 1 行 |
| 13 | 评测 ticket 驱动（维护性补丁） | `grep -n "def drive_ticket" maa_api/agent/evals/runner.py`；`grep -n '"ticket": drive_ticket' maa_api/agent/evals/runner.py`；`grep -n "copilot_results" maa_api/agent/evals/scenario.py tests/evals/environment.py`；`grep -n "copilots" maa_api/agent/evals/scenario.py tests/evals/environment.py`；`grep -n "human: HumanPolicy" maa_api/agent/evals/scenario.py`；`grep -n "human.questions" maa_api/agent/evals/runner.py`；`grep -n 'entry.kind == "ticket"' maa_api/agent/evals/scenario.py`；`grep -n "agent_session_id" tests/evals/environment.py` | 1 行；1 行；至少 2 行；至少 2 行；1 行；至少 1 行；1 行；至少 1 行 |
| 14 | 评测场景与计数基线 | `ls tests/evals/scenarios/*.yaml \| wc -l`；`.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"`；`.venv/bin/python -c "from maa_api.settings import SETTING_KEYS; from maa_api.services.settings_schema import _FIELDS; print(len(SETTING_KEYS), len(_FIELDS))"` | `13`；`107 106`；`142 131` |
| 15 | 端点、错误码、表的基线 | `grep -n "共 186 条 HTTP 端点" docs/v3/14-现行接口清单.md`；`grep -n "（83 条）" docs/v3/14-现行接口清单.md`；`grep -n "^ALL_CODE_COUNT" tests/domain/test_errors.py`；`grep -n "^HTTP_BOUND_CODE_COUNT" tests/domain/test_errors.py`；`grep -n "^SECTION_COUNT" tests/domain/test_errors.py`；`grep -n "def test_metadata_has_exactly_57_business_tables" tests/db/test_models.py` | 各 1 行；错误码 171/170/22；表 57 |
| 16 | 迁移基线（本里程碑不新增迁移） | `ls maa_api/db/migrations/versions \| grep -E '^[0-9]{4}_' \| sort \| tail -n 1` | `0041_nav_graph.py` |

表格单元里的 `\|` 是 Markdown 转义，复制到 shell 时去掉反斜杠。

第 13 条不成立时按 §10 停下来：评测 ticket 驱动是 M87 的前置设施，不在本文件的任务里实现。第 2 条不成立时不要开工。第 15 条的 186/83、171/170、57 是 M86 合并后的值；若实测不同，按当前代码与测试为准，在台账记下偏差。

## 1. 背景与目标

v3 的 Worker 每轮以 `report` 收尾，`ready` 把工单置为 `done` 并转给总控（[05 §4.1](../05-运行时与代理定义.md)）。`ready` 现在只是模型自述。v2 的实机复盘里有过两类误报：流水线失败了却说做完了；作业尝试没有记录结果就收尾（[v2/13 §8.80](../../v2/13-决策记录.md)、`playbook-stage-clear` §7）。M70 已经建了只读的验证员子代理 `verifier`，但还没有人用它把关。

作业研究方面，v2 M29 有作业授权（确认动作 `grant_copilot_attempts`）、执行一次尝试（`run_copilot_attempt`）、模型补判（`record_copilot_result`），但「选作业 → 停到开始行动页 → 执行 → 判结果 → 改作业」的循环完全靠提示词，位置要用户手动摆好（v2 Q41）。

做完能验证的结果：

1. 代理定义的新字段 `verify_before_ready` 加在 `AgentSpecFrontmatter`；只有 `daily_ops`、`copilot` 为 true，`general` 与其余 Worker 为 false。
2. 闸门只统计**本工单 Worker 自己**运行产生的写设备审计：`agent_audit.session_id` 是本工单的 Worker 会话、`agent_audit.agent_name` 等于工单的 `kind`、对应 `agent_run` 也属于该 Worker 会话且不是子运行。导航员子代理的点击不进统计。
3. 闸门按三类依据放行：流水线终态、内核判定的作业结果、最后一次写设备操作之后结束的验证员结论；三类都不成立时 `report` 返回 `accepted=false`，本批不结束运行，工单状态不变。
4. 验证员的结论落在子运行的 `agent_run.meta["verdict"]`；失败有界：同一批操作之后两次验证不通过，只能汇报 `failed`、`blocked`，或经用户在提问卡上选「仍按完成结束」放行。
5. 作业研究工单按固定五步循环推进，第 2 步交给导航员（M86 的 `navigate_to`）；工单会话里每份作业授权最多 `copilot_loop.max_iterations` 次尝试。一轮用完时，工单会话在**下一次** `run_copilot_attempt` 把旧授权落 `exhausted` 并由确认卡开始新一轮；v2 聊天会话仍抛 `COPILOT_GRANT_EXHAUSTED`。
6. 评测集新增 6 个场景覆盖放行、拒绝、复核、用户放行与两种循环结局；场景走维护性补丁提供的 `entry.kind=ticket` 驱动。

## 2. 非目标

- 不新增工具、表、端点、错误码；不新增迁移；不新增中间件位置。
- 不验证只读工单（没有写设备审计就不过闸门）。
- `general` 不纳入默认闸门；本里程碑不改 `general.md`，不改它的 `version`。
- 不改 v2 聊天会话的作业授权口径：没有工单的会话仍是 1–10 次、默认 3，次数用完仍抛错。
- 不让内核读关卡星数；验证员看现拍截图。
- 不改 `run_copilot_attempt` 的阻塞行为（最长 `agent.copilot_attempt_timeout_minutes`，默认 20 分钟）。
- 不在评测里重复覆盖界面导航（M86 的评测与冒烟已覆盖）；M87 的 6 个场景只覆盖闸门与作业循环。
- 不改评分器 `scenario.py`/`graders.py` 的既有字段语义；ticket 入口、场景级人工策略与假作业结果由维护性补丁提供。

## 3. 相关文档

- [05 §1.1、§1.2](../05-运行时与代理定义.md)：定义文件样例与字段表，`verify_before_ready` 加在这里。
- 05 §3.2：`daily_ops`、`copilot` 的 Worker 行；05 §3.3：验证员的 7 个只读工具与 `max_steps: 8`。
- 05 §4.1：`report` 参数、状态映射、自动补汇报；05 §4.2：`delegate` 的 `task`/`context` 上限。
- 05 §5.2 第 17 位：`ReportMiddleware` 的位置与职责；05 §8：`verifier_verdict` 的 pydantic 模型。
- [02 §2.2](../02-系统架构.md)：服务表，`VerificationGate` 加在这里；02 §4.1：`workers/` 目录；02 §5：装配（本里程碑不新增 `app.state` 属性）；02 §6.1–§6.2：工单时序与确认冒泡。
- [03 §3.2](../03-数据模型.md)：`agent_run.meta`；03 §6.3：`ticket_report.details`。
- [04 §6](../04-接口与事件协议.md)：`copilot_loop.max_iterations` 一行（M87 已预置，填说明列）。
- [11 §3.2、§6.2](../11-前端指挥台.md)：待你处理里的确认卡与实时副本；本次只改授权卡组件。
- 评测设施：`maa_api/agent/evals/scenario.py`、`maa_api/agent/evals/runner.py`、`tests/evals/environment.py`、`tests/evals/scenarios/legacy-daily-submit.yaml`（场景文件是 JSON-in-YAML，可手写 cassette）。
- v2：`grep -n "^### 8.44" docs/v2/13-决策记录.md`（M29 作业授权的状态机、计次与判定表）；`grep -n "^## 9" docs/v2/superpowers/specs/2026-10-09-m25-prts-knowledge.md`（M29 规格）；`docs/v2/superpowers/specs/2026-10-14-m49-stage-clear-playbook.md`（攻克关卡的决策链）；`grep -n "M29" docs/v2/README.md`（作业研究口径与「开始行动页」前置）。
- 技能文件 `maa_api/agent/skills/builtin/playbook-stage-clear/SKILL.md`（M78 从知识条目迁来，第 2 步的位置要求与它一致）。
- 既有代码入口：`maa_api/agent/policy.py` 的 `_evaluate_copilot_attempt`（约 260 行）；`maa_api/agent/confirmation.py` 的 `_apply_copilot_grant_overrides`（约 407 行）；`maa_api/services/copilot/grant.py` 的 `CopilotGrantService.approve`（约 327 行）、`_require_attempts`（约 680 行）、`get_active`（约 290 行）、`_terminate`（约 523 行）；`maa_api/services/copilot/attempt.py` 的 `CopilotAttemptService.run`（约 275 行，返回 `outcome`、`judged_by`、`success`、`remaining`、`grant_status`、`pipeline_id`、`recognition_pipeline_id`、`exit_pipeline_id`、`screenshot_id`）；`maa_api/db/repositories/copilot.py` 的 `CopilotAttemptRepository.find`（约 275 行）；`maa_api/domain/enums.py` 的 `QuestionStatus`、`PipelineStatus`、`ReportStatus`；`web/src/features/copilots/CopilotGrantCard.tsx` 的 `MAX_ATTEMPTS`（约 20 行）、`attemptsOf`（约 52 行）、`parseCopilotGrantPayload`（约 74 行）、`ATTEMPT_OPTIONS`（约 94 行）、次数说明（约 133 行）。
- 维护性补丁（M88–M98 共用，M87 开工前单独落地）：`entry.kind=ticket` 的 `drive_ticket`（建单、轮询根运行到终态、按 `scenario.human.confirmations` 批准确认、按 `scenario.human.questions` 回答提问、把子代理会话写进 `recorder.session_ids`）、场景级 `human: HumanPolicy`、`World.copilots`、`World.copilot_results`、seed 流水线带 `agent_session_id`。M87 只写 6 个场景，不实现驱动。

`grant_copilot_attempts` 是确认动作名，不是工具；v2 没有同名工具。

## 4. 设计

### 4.1 数据模型

无新表、无迁移。用到两个既有 JSON 列，整列赋值：

- `agent_run.meta["verdict"]`（验证员子运行）：`{passed: bool, confidence: float, evidence: [str], notes: str, degraded: bool, recorded_at: "…Z"}`。
- `ticket_report.details["verification"]`（被放行的 ready 汇报）：`{method: "not_required"|"machine"|"verifier"|"waiver", verdict_run_id?: str, question_id?: str, evidence: [str]}`。

`details["verification_waiver"]` 是 `report` 的参数（写在 `details` 里），两种形状：

- 字符串：同一轮 `ask_user` 返回的 `question_id`。
- 对象 `{"selection": "仍按完成结束"}`：重启后收到「用户回答了之前的提问…」那种没有 `question_id` 的投递时用；闸门按 `(session_id, selection, answered_at)` 反查最新一张已回答的卡。

`details.verification` 只由闸门写：模型自己在 `details` 里传同名键会被闸门结果覆盖；没有过闸门的 Worker 不写这个键。`record_report` 对终态汇报会把 `details` 合并进 `ticket.result`，所以 `ready` 之后 `ticket.result.verification` 也会出现。这是接受的行为，字段值就是上面的 `verification`。

### 4.2 模块与文件

| 文件 | 新建/修改 | 职责 |
|---|---|---|
| `maa_api/agent/workers/verification.py` | 新建 | `VerificationGate`、`GateDecision`、常量、`merge_verification` |
| `maa_api/agent/specs/schema.py` | 改 | `AgentSpecFrontmatter` 加 `verify_before_ready: bool = False` |
| `maa_api/agent/specs/registry.py` | 改 | `_check_field_rules` 拒绝非 Worker 设为 true |
| `maa_api/agent/workers/service.py` | 改 | `submit_report` 在 `ready` 前过闸门、合并 `verification` |
| `maa_api/agent/workers/middleware.py` | 改 | `ReportMiddleware` 见 `accepted=false` 时不结束运行 |
| `maa_api/agent/workers/delegation.py` | 改 | 验证员子运行结束后写 `meta["verdict"]` |
| `maa_api/db/repositories/copilot.py` | 改 | `CopilotAttemptRepository.list_for_session` |
| `maa_api/agent/policy.py` | 改 | 工单会话的授权预填与 `max_attempts`；次数用完后的新一轮 |
| `maa_api/agent/confirmation.py`、`maa_api/services/copilot/grant.py` | 改 | 批准时按 `max_attempts` 夹住次数；`mark_exhausted` |
| `maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml` | 改 | `copilot_loop.max_iterations` |
| `maa_api/agent/specs/builtin/{copilot,daily_ops,verifier}.md` | 改 | §4.7 |
| `web/src/features/copilots/CopilotGrantCard.tsx` | 改 | 次数下拉框按 `max_attempts` 截断 |
| `tests/evals/scenarios/m87-*.yaml`、`tests/evals/cassettes/m87-*.jsonl` | 新建 | 6 个评测场景与手写 cassette |

`verification.py` 的公开形状：

```python
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

EffectTool = Literal[
    "submit_pipeline", "run_copilot_attempt", "click", "swipe",
    "long_press", "input_text", "key_event",
]
GateMethod = Literal["not_required", "machine", "verifier", "waiver"]
GateReason = Literal[
    "verification_required", "verification_failed",
    "verification_exhausted", "waiver_invalid",
]

EFFECT_TOOLS: frozenset[str] = frozenset({
    "submit_pipeline", "run_copilot_attempt",
    "click", "swipe", "long_press", "input_text", "key_event",
})
RAW_TOOLS: frozenset[str] = frozenset({
    "click", "swipe", "long_press", "input_text", "key_event",
})
VERIFY_MIN_CONFIDENCE = 0.7
VERIFY_MAX_ROUNDS = 2
WAIVER_OPTION = "仍按完成结束"
MAX_CONTEXT_CHARS = 4000
MAX_EVIDENCE = 10
MAX_LISTED_PIPELINES = 3
MAX_LISTED_ATTEMPTS = 3
MAX_DETAILS_CHARS = 4000
VERIFICATION_META_CHARS = 120

@dataclass(frozen=True, slots=True)
class GateDecision:
    accepted: bool
    method: GateMethod | None = None
    reason: GateReason | None = None
    evidence: tuple[str, ...] = ()
    verdict_run_id: str | None = None
    question_id: str | None = None
    message: str | None = None
    delegate: Mapping[str, str] | None = None

    def report_details(self) -> dict[str, Any]:
        """写进 ``ticket_report.details["verification"]``。"""

    def tool_result(self) -> dict[str, Any]:
        """拒绝时 ``report`` 工具的返回（§4.3）。"""

class VerificationGate:
    def __init__(self, session_factory: Any) -> None: ...

    async def check(
        self,
        *,
        ticket_id: str,
        worker_session_id: str,
        claim: str,
        details: Mapping[str, Any] | None,
    ) -> GateDecision:
        """按 §4.5.2 判定一次 ``ready`` 是否可以放行。"""

    async def record_verdict(
        self,
        *,
        run_id: str,
        verdict: Mapping[str, Any] | None,
        degraded: bool,
    ) -> None:
        """把验证员结论写进 ``agent_run.meta["verdict"]``；失败只记 warning。"""

def merge_verification(
    details: Mapping[str, Any] | None,
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    """把 ``verification`` 并进 ``details``；按 §4.5.3 裁剪 evidence。"""
```

`CopilotAttemptRepository` 新方法：

```python
async def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
    """扫 ``kind=copilot`` 资源的 ``meta["attempts"]``，取 ``session_id`` 相同的记录，
    按 ``started_at`` 升序；每项保持资源里存的键。"""
```

`CopilotGrantService` 的新增与改动：

```python
async def approve(
    self, session_id: str, *, stage_id: str, ...,
    max_attempts: Any = None,
) -> dict[str, Any]:
    """``max_attempts`` 非空时先按 ``_require_attempts`` 校验它，再取
    ``resolved_attempts = min(resolved_attempts, max_attempts)``。"""

async def mark_exhausted(self, session_id: str | None) -> dict[str, Any] | None:
    """把当前 ``active`` 授权落 ``exhausted``（工单会话的新一轮用）。"""
```

`list_for_session` 是仓储内部方法，`CopilotLoopSettings` 只是 `copilot_loop.*` 的实现类，两者与 `tests/evals/scenarios/m87-*.yaml` 一样不进 02–05 的契约表。

`policy.py` 的改动集中在一处：`_evaluate_copilot_attempt` 抽出 `_copilot_grant_card` 构造「没有授权」的 `PolicyDecision`，供首次授权与工单会话次数用完两处复用；新增私有 `_ticket_worker_session(context) -> str | None`，只有会话行满足 `kind == "worker"`、`parent_session_id IS NULL`、`ticket_id` 非空时才返回会话 id。

### 4.3 接口

没有新端点。变化的是 `report` 的拒绝结果与 `grant_copilot_attempts` 的确认载荷。

`report` 被闸门拒绝时的工具结果（`ToolOutcome.status="ok"`、`stop_run=False`，本次调用不写 `ticket_report`，工单状态不变，审计照常记这次调用）：

```json
{
  "accepted": false,
  "reason": "verification_required",
  "message": "这张工单做过写设备的操作，汇报完成前要先核对：按 delegate 委派验证员，通过后再汇报 ready。",
  "evidence": ["流水线 3f2a1c88「日常」failed，结束于 2026-11-20T08:12:03Z"],
  "delegate": {
    "agent": "verifier",
    "task": "核对工单「日常」是否完成：日常已做完，理智清空",
    "context": "执行记录：\n- 流水线 3f2a1c88「日常」failed，结束于 2026-11-20T08:12:03Z\n核对方法：get_pipeline 看各任务状态，失败的任务用 get_logs 看原因"
  }
}
```

`delegate` 键总是存在；没有可给出的委派时是 `null`。`reason` 与 `message`：

| reason | message | 带 `delegate` |
|---|---|---|
| `verification_required` | 上面那句 | 是；本工单还有流水线在 `pending`/`running` 时改用「本工单还有流水线在运行（{id 前 8 位}「{title}」{status}）。先用 wait_pipeline 查到终态，再汇报 ready。」且 `delegate` 为 `null` |
| `verification_failed` | 「验证员认为还没完成：{notes 前 200 字；为空时写「验证员没有给出通过结论」}。能补救就补救后再委派验证员；补救不了就汇报 failed 或 blocked。」 | 是 |
| `verification_exhausted` | 「这批操作之后已经两次验证不通过，只能汇报 failed 或 blocked；用户明确接受现状时，用 ask_user 让用户选「仍按完成结束」，再带 verification_waiver 汇报。」 | 否 |
| `waiver_invalid` | 「用户放行只在同一批操作已有两次验证不通过之后可用；verification_waiver 还要对得上一张已回答、选了「仍按完成结束」、晚于最后一次写设备操作与最后一次验证的提问卡。」 | 否 |

放行时 `report` 的结果照 M70 原样（`report_id`、`ticket_status`、`forwarded`），汇报行的 `details` 多一个 `verification` 键（§4.1）。

授权确认载荷：工单会话里由策略弹出的 `grant_copilot_attempts` 确认，`payload` 多一个键 `max_attempts`（int，1–10，等于当时的 `copilot_loop.max_iterations`）；没有工单的会话不带这个键。

### 4.4 配置项

| 键 | 类型 | 默认 | 范围 | 分组 | config-only | `_FIELDS` |
|---|---|---|---|---|---|---|
| `copilot_loop.max_iterations` | int | 3 | 1–10 | Agent | 否 | `dict(label="作业研究每轮最多尝试次数", group="Agent", type="integer", minimum=1, maximum=10, hot_action="none")` |

`settings.py`：

```python
class CopilotLoopSettings(BaseModel):
    """作业研究闭环（M87）：工单会话里每份作业授权的最多尝试次数。"""

    model_config = ConfigDict(extra="ignore")

    max_iterations: int = Field(default=3, ge=1, le=10)
```

`Settings` 加 `copilot_loop: CopilotLoopSettings = Field(default_factory=CopilotLoopSettings)`；`SETTING_KEYS` 加 `"copilot_loop.max_iterations": ("copilot_loop", "max_iterations")`；`ENV_OVERRIDES` 由 `SETTING_KEYS` 推导，新增的名字是 `MAA_COPILOT_LOOP_MAX_ITERATIONS`，`tests/test_settings.py` 的整表字面断言补这一行。

`config.template.yaml`：

```yaml
# 作业研究闭环（M87）：工单里每份作业授权最多试几次；用完后由用户决定要不要再授权一轮。
copilot_loop:
  max_iterations: 3
```

### 4.5 行为与算法

#### 4.5.1 哪些工单要过闸门

| Worker | `verify_before_ready` | 计入的写设备审计 | 可直接放行的机器证据 |
|---|---|---|---|
| `daily_ops` | true | `submit_pipeline` | 最后一次 `submit_pipeline` 之后提交的本工单流水线全部 `completed` |
| `copilot` | true | `run_copilot_attempt`、`submit_pipeline` | 本会话最近一次尝试 `success=true` 且 `judged_by="core"`；另外提交的流水线全部 `completed` |
| `general` 与其余 Worker | false | 不统计 | 不过闸门 |

**只统计本工单 Worker 自己**。判定顺序：

1. 取工单行 `ticket`；`worker_session_id = ticket.worker_session_id`。
2. 取 `agent_session` 行并校验 `id == worker_session_id == ticket.worker_session_id`、`kind == "worker"`、`parent_session_id IS NULL`、`ticket_id == ticket.id`。任一条不满足就当作没有可识别的 Worker 会话，返回 `not_required`。这一条排除子代理会话（子代理会话的 `parent_session_id` 非空），也排除「汇报来自别的会话」的错绑。
3. 写设备审计如下取，四条全部满足：

```text
AgentAudit.ticket_id   == ticket.id
AgentAudit.session_id  == ticket.worker_session_id
AgentAudit.agent_name  == ticket.kind            # daily_ops / copilot
AgentAudit.tool_name   in EFFECT_TOOLS
AgentAudit.status      == "success"
```

4. 再把 `AgentAudit.run_id` 与 `agent_run` 对齐：`run.session_id == ticket.worker_session_id`、`run.agent_name == ticket.kind`、`run.parent_run_id IS NULL`。导航员的审计虽然 `session_id` 是父 Worker 会话（策略身份复用），但 `agent_name == "navigator"` 且 `run.session_id` 是导航员子会话，两道条件都会把它排除。

`navigate_to`、`where_am_i`、`back_to_home`、`hold_device`、`release_device`、`reconnect_device`、`set_task_params`、`stop_pipeline`、`send_screenshot` 不计入：导航有逐步校验，回主界面是幂等复位，其余不是「声称的结果」。

#### 4.5.2 闸门算法

```
check(ticket_id, worker_session_id, claim, details):
    ticket, ws = 读工单与会话；ws 不符合 §4.5.1 第 2 步 → accept("not_required")
    effects = §4.5.1 第 3、4 步的审计行，按 created_at 升序
    if not effects: return accept("not_required")
    used = {e.tool_name for e in effects}
    attempts = list_for_session(worker_session_id)          # §4.2，按 started_at 升序
    attempt_pipeline_ids = {a.pipeline_id, a.recognition_pipeline_id, a.exit_pipeline_id
                            for a in attempts} − {None}
    all_pipelines = 本工单 Worker 会话提交的流水线
    superseded = {p.retry_of_id for p in all_pipelines if p.retry_of_id}
    all_pipelines = [p for p in all_pipelines
                     if p.id not in attempt_pipeline_ids and p.id not in superseded]
    last_effect_at = max(
        [e.created_at for e in effects],
        [a.finished_at for a in attempts if a.finished_at],
        [p.finished_at for p in all_pipelines if p.finished_at],
    )

    # 0) 有非终态的本工单流水线：先拒绝，不委派
    running = [p for p in all_pipelines if p.status in {"pending", "running"}]
    if running: return reject("verification_required", running[...], delegate=None)

    # 1) 机器证据
    if not (used & RAW_TOOLS):
        ok = True; evidence = []
        if "run_copilot_attempt" in used:
            latest = attempts[-1] if attempts else None
            ok = latest is not None and bool(latest.get("success")) \
                 and latest.get("judged_by") == "core"
            evidence += [尝试描述(latest) if latest else "没有找到本工单的作业尝试记录"]
        if "submit_pipeline" in used:
            last_submit = max(e.created_at for e in effects
                              if e.tool_name == "submit_pipeline")
            submitted = [p for p in all_pipelines if p.created_at >= last_submit]
            ok = ok and bool(submitted) and all(p.status == "completed" for p in submitted)
            evidence += [流水线描述(p) for p in submitted[:3]]
        if ok: return accept("machine", evidence[:10])

    # 2) 验证员结论：只认 meta 里有 verdict 的运行，不按 run.status 过滤
    verdicts = 本工单的 agent_name='verifier' 运行，meta 有 verdict，
               且 finished_at > last_effect_at，按 finished_at 升序
    latest = verdicts[-1] if verdicts else None
    if latest and 通过(latest): return accept("verifier", latest.evidence, verdict_run_id=latest.id)

    # 3) 用户放行：只有两次验证不通过之后才可用
    failed = [v for v in verdicts if not 通过(v)]
    waiver = (details or {}).get("verification_waiver")
    if waiver is not None:
        if len(failed) >= VERIFY_MAX_ROUNDS and waiver_valid(waiver, worker_session_id,
                                                            after=max(last_effect_at, failed[-1].finished_at)):
            return accept("waiver", question_id=waiver 的提问 id)
        return reject("waiver_invalid")

    # 4) 失败有界
    if len(failed) >= VERIFY_MAX_ROUNDS:
        return reject("verification_exhausted", evidence=failed[-1].evidence)
    if failed:
        return reject("verification_failed", evidence=failed[-1].evidence,
                      delegate=suggest(ticket, claim, used, all_pipelines, attempts))
    return reject("verification_required", evidence=…,
                  delegate=suggest(ticket, claim, used, all_pipelines, attempts))
```

- `通过(v)`：`v.passed is True` 且 `v.confidence >= VERIFY_MIN_CONFIDENCE`。`degraded=true` 的结论按 `passed=false` 记，算一次失败。
- `waiver_valid` 两种形状：
  - 字符串：`agent_question.id == waiver`、`session_id == worker_session_id`、`status == "answered"`、`selection` 含 `WAIVER_OPTION`、`answered_at > after`，五条全部满足。
  - 对象：只允许 `{"selection": WAIVER_OPTION}`；按 `session_id`、`status="answered"`、`answered_at > after` 取最新一条，`selection` 含 `WAIVER_OPTION` 即通过；取不到就失败。
- `suggest()` 生成委派参数，总长不超过 4000 字：
  - `task`：`核对工单「{ticket.title}」是否完成：{claim 前 300 字}`。
  - `context` 按下面顺序拼；流水线最多 3 条、尝试最多 3 条，超出的写「另有 N 条未列出」；`used` 里的原子工具按调用次数各写一行：

```text
执行记录：
- 流水线 {id 前 8 位}「{title}」{status}，结束于 {finished_at}{；失败原因：error_message 前 80 字}
- 作业尝试 #{attempt_number} 关卡 {stage_code}：{outcome}，判定方 {judged_by}，{计次/不计次}
- 原子操作 {工具名} × {次数}
核对方法：{按 used 选下面几句}
```

  核对方法的句子：用过 `submit_pipeline` 写「get_pipeline 看各任务状态，失败的任务用 get_logs 看原因」；用过 `run_copilot_attempt` 写「get_screenshot 看关卡页上这一关的通关标记与星数」；用过原子操作写「get_screenshot 看当前画面是否符合声称的结果」。
- `evidence` 每条一句可核对的事实，至多 10 条。

#### 4.5.3 汇报路径的改动

`WorkerService.submit_report`（`maa_api/agent/workers/service.py`）在构造 `ReportInput` 之前插入：

```python
details = getattr(params, "details", None)
if (
    status is ReportStatus.READY
    and ctx.spec.verify_before_ready
    and ticket_id
):
    decision = await VerificationGate(self._session_factory).check(
        ticket_id=ticket_id,
        worker_session_id=ctx.session_id or "",
        claim=params.summary,
        details=details,
    )
    if not decision.accepted:
        return decision.tool_result()
    details = merge_verification(details, decision.report_details())
```

- `ctx.spec` 是当前运行的代理定义；`report` 只允许 Worker 调用（`tools/report.py` 的 `require_run(kinds={"worker"})`），子代理不会走到这里。
- 被拒的 `report` 仍是这一批的最后一个调用，但本批**不**以 `report_submitted` 结束；模型下一步通常是 `delegate`。`LoopGuardMiddleware` 对连续三批完全相同的 `report` 调用照常判卡死。
- `ReportMiddleware.wrap_tool` 在 `outcome.status == "ok"` 时先看结果：结果是 `Mapping` 且 `result.get("accepted") is False` 时直接返回，不写 `SUBMITTED_KEY`、不设 `stop_run`、不设 `stop_reason`。其余结果照原逻辑写 `SUBMITTED_KEY`、`stop_run=True`、`stop_reason="report_submitted"`。
- `ReportMiddleware.ensure_report` 在轮末自动补的 `progress`/`waiting`/`blocked` 不过闸门：它调 `TicketService.record_report`，不经过 `submit_report`。所以「被拒时没有 `ticket_report` 行」只约束被拒的那次调用；轮末补的 `progress` 行带 `details.auto=true`。
- `merge_verification`：先 `merged = {**details, "verification": {**verification, "evidence": list(evidence)}}`；序列化后超过 `MAX_DETAILS_CHARS` 时从 `evidence` 尾部逐条删除，仍超过就删掉 `evidence` 键。模型给的 `details` 仍受 `ReportParams` 的 4000 字校验；闸门追加的 `method` 与 id 是服务端可信元数据，固定部分不超过 120 字，允许在「原 `details` 已占满 4000 字」时把总数带到 4120 字以内。

#### 4.5.4 记录验证员结论

`DelegationService._delegate_locked` 在 `finalize_output` 与 `_finish_run` 之后、`_set_session_status` 之前：

```python
if str(getattr(spec, "name", "")) == "verifier":
    await VerificationGate(self._session_factory).record_verdict(
        run_id=run.id,
        verdict=result,
        degraded=bool(result.get("degraded")),
    )
```

- 正常：`meta["verdict"] = {**verdict, "degraded": false, "recorded_at": now}`。
- 降级：`meta["verdict"] = {"passed": False, "confidence": 0, "evidence": [], "notes": "验证员没有给出合法结论", "degraded": True, "recorded_at": now}`。
- `meta` 整列赋值并保留原有键（例如已开启的工具组、已加载的技能）。运行不存在或写失败只记 warning，委派结果照常返回给父 Worker。

#### 4.5.5 作业研究循环与 M29 的配合

循环由 `copilot` 的提示词驱动（§4.7.1），代码只负责三件事：授权次数的上限、尝试的记录、完成的闸门。

一圈五步：

| 步 | 做什么 | 用到的 v2/v3 能力 |
|---|---|---|
| 1 选或写作业 | `search_copilots` 找现成作业，`feasible=true` 且 `tier="ok"` 的用 `save_copilot_draft(site_id=…)` 存副本；找不到就 `get_stage_level_data` 后自己写 `save_copilot_draft(content=…)`；第二圈起带 `parent_id` 改上一版 | M29 作业站与草稿版本链 |
| 2 到开始行动页 | 委派 `navigator`：「把游戏停在关卡 {代号} 自己的「开始行动」页」；导航员用 `navigate_to("stage:{代号}")`，目标不存在时按 M86 的 `nearest` 手动找并用 `remember_screen(node_id="stage:{代号}", title=…)` 记下 | M86 |
| 3 执行 | `run_copilot_attempt(copilot_id, support)`；第一次触发授权卡 | M29 授权与尝试 |
| 4 判结果 | 内核判定的 `three_star`/`two_star`/`clear`/`failed` 直接采用；`unknown` 时看 `screenshot_id`（`annotate_screenshot(screenshot_id=…)`）再用 `record_copilot_result` 补判；收尾前要验证员复核 | M29、M57 结算识别；本里程碑闸门 |
| 5 复盘 | 读返回里的 `actions` 动作摘要，判断改哪里；不原样重跑同一份 | 提示词 |

授权与循环的关系：

- 作业授权仍是 v2 的会话级授权，绑在 Worker 会话上（`copilot_grant.session_id` 等于工单的 Worker 会话）；确认卡经 M71 冒泡到全局面板，标明来自哪张工单。
- 工单会话里，策略预填的次数取 `min(run_copilot_attempt 的 grant_attempts, copilot_loop.max_iterations)`，并在载荷里写 `max_attempts`；批准时次数不能超过 `max_attempts`（用户在卡上选得再大也会被夹住，前端下拉框本身也只给到这个数）。「一轮」就是一份授权，最多 `max_iterations` 次计次的尝试。
- 不计次的结果（`not_started`、`sanity_insufficient`、`error`、`interrupted`）按 M29 退还次数，不消耗这一轮。
- 一份授权用完（这次尝试的返回里 `remaining == 0`）还没成功：Worker 汇报 `waiting`，`human_action.type="decision"`，摘要交代试过哪几版、卡在哪、下一版打算怎么改。用户在工单里回复继续后，**下一次** `run_copilot_attempt` 触发策略；工单会话把旧授权落 `exhausted` 并返回 `grant_copilot_attempts` 确认，用户确认后开始新一轮。
- 授权卡是授权本身，汇报交代研究过程，两者分开。
- 理智不足按 M29 把授权标 `stopped`，Worker 汇报 `blocked`，不吃药不碎石（v2 Q17）。
- 没有工单的 v2 聊天会话不受 `copilot_loop.max_iterations` 影响；次数用完仍抛 `COPILOT_GRANT_EXHAUSTED`。

授权状态机（下半段的两条转移是本次新增的）：

```text
active ─┬──某次尝试达到成功标准──▶ succeeded
        ├──用户撤销──────────────▶ revoked
        ├──用户中断 / 理智不足───▶ stopped
        ├──有效期到──────────────▶ expired
        ├──服务重启──────────────▶ interrupted
        └──used == attempts（下一次 run_copilot_attempt，工单会话）
               ──▶ exhausted ──用户批准新卡──▶ active（新行，used=0）

active ──used == attempts（下一次 run_copilot_attempt，v2 聊天会话）
        ──▶ 抛 COPILOT_GRANT_EXHAUSTED，行保持 active
```

改哪几个函数：

| 函数 | 改动 |
|---|---|
| `maa_api/agent/policy.py::_evaluate_copilot_attempt` | `remaining <= 0` 时先判会话：工单 Worker 会话 → `await CopilotGrantService(...).mark_exhausted(session_id)`，再走「没有授权」分支弹新卡；否则照旧抛 `COPILOT_GRANT_EXHAUSTED` |
| `maa_api/agent/policy.py::_copilot_grant_card`（新私有函数） | 由「没有授权」分支抽出：构造 `attempts`（按 `max_iterations` 夹住）、`max_attempts`、`success`、`support`、`session_id`、`stage_id`、`stage_code`、`tool_name`、`target_kind` |
| `maa_api/agent/policy.py::_ticket_worker_session`（新私有函数） | 读 `agent_session`，只在 `kind == "worker"`、`parent_session_id is None`、`ticket_id` 非空时返回会话 id |
| `maa_api/services/copilot/grant.py::CopilotGrantService.mark_exhausted` | 调 `_terminate(session_id, "exhausted", reason="次数用完，用户可批准新一轮")` |
| `maa_api/services/copilot/grant.py::CopilotGrantService.approve` | 加关键字参数 `max_attempts`，按 §4.2 夹住 |
| `maa_api/agent/confirmation.py::_apply_copilot_grant_overrides` | `max_attempts = _pick("max_attempts")`，传给 `approve(max_attempts=…)`；用户 `overrides` 里不允许出现它（`OverridesRequest` 的 `extra="forbid"` 已经拒绝） |
| `maa_api/agent/policy.py` 的 `_active_copilot_grant` | 不改；`remaining` 由 `grant_wire` 算好 |

完成闸门在循环里的位置：最近一次尝试内核判定成功 → 机器证据，直接放行；模型补判成功 → 闸门要求验证员复核，验证员现拍关卡页截图看通关标记；最近一次不成功却汇报 ready（例如用户只让试一次）→ 同样交给验证员判断声称是否属实。

#### 4.5.6 验证失败之后

Worker 拿到 `accepted=false` 后的出路，按顺序：

1. `verification_required`：照 `delegate` 委派验证员，再汇报。若 `delegate` 是 `null`（流水线未终态），先 `wait_pipeline`。
2. `verification_failed`：在委托范围内能补救就补救（例如重跑失败的那个任务），补救之后又有了新的写设备操作，之前的结论自动作废，重新验证；补救不了就汇报 `failed`（说明没做成什么）或 `blocked`（需要用户决定）。
3. `verification_exhausted`：只能汇报 `failed` 或 `blocked`。用户明确接受现状时，用 `ask_user` 发卡，选项里必须有「仍按完成结束」；用户选了，再 `report(status="ready", details={"verification_waiver": <question_id>})`。重启后收到「用户回答了之前的提问…」而没有 `question_id` 时，写 `details={"verification_waiver": {"selection": "仍按完成结束"}}`。
4. 不允许为了过闸门改写摘要后反复重试同一个 `report`。

#### 4.5.7 边界情况

| 输入 | 期望行为 |
|---|---|
| 只读工单汇报 ready | `not_required`，直接放行，`details.verification.method="not_required"` |
| 汇报 `progress`、`waiting`、`blocked`、`failed`、`dropped` | 不过闸门 |
| `general`、`planner` 这类未开启字段的 Worker 汇报 ready | 不过闸门，`details` 不写 `verification` |
| 工单没有 Worker 会话、会话 `parent_session_id` 非空，或 `worker_session_id` 与工单上记录的不一致 | `not_required` |
| 导航员子代理点过界面，本工单 Worker 只有只读审计 | `not_required`（不计入工单效果） |
| 验证员结论早于最后一次写设备操作 | 不算数，当作没有结论 |
| 验证员给 `passed=true`、`confidence=0.6` | 算失败的一次 |
| 验证员输出降级 | 记成失败的一次（§4.5.4） |
| 验证员结论所在运行是 `failed`/`cancelled`，但 `meta` 有 `verdict` | 照常参与判定（不按 `run.status` 过滤） |
| 流水线还在 `running` 或 `pending` 时汇报 ready | `verification_required`，`delegate=null`，先查到终态 |
| 工单没有任何流水线但用过 `submit_pipeline`（提交被拒） | 机器证据不成立，交给验证员 |
| 用过 `click`/`swipe`/`long_press`/`input_text`/`key_event` | 机器证据不成立，交给验证员 |
| 尝试自己的失败流水线 | 不参与流水线检查；结论看尝试记录 |
| 被 `retry_of_id` 取代的旧流水线 | 不参与流水线检查 |
| `verification_waiver` 出现在两次验证不通过之前 | `waiver_invalid` |
| `verification_waiver` 指向别的会话的提问、选项不含「仍按完成结束」、或作答早于最后一次写设备操作 | `waiver_invalid` |
| 用户在工单里直接说「算了就这样」而没有提问卡 | 不放行；Worker 应当发提问卡 |
| v2 聊天会话调用 `run_copilot_attempt` 且次数已用完 | 预填仍是 1–10、载荷没有 `max_attempts`，抛 `COPILOT_GRANT_EXHAUSTED` |
| `copilot_loop.max_iterations` 在已有授权期间改小 | 已批准的授权不变；新授权按新值 |

### 4.6 前端

只改 `web/src/features/copilots/CopilotGrantCard.tsx`：

- `CopilotGrantRequest` 加 `maxAttempts: number`（解析结果里总是有值，缺省等于 `MAX_ATTEMPTS`）。
- `attemptsOf(value, fallback = DEFAULT_ATTEMPTS, upper = MAX_ATTEMPTS)` 加上界参数，上界夹到 1–10。
- `parseCopilotGrantPayload` 先算 `const maxAttempts = attemptsOf(payload.max_attempts, MAX_ATTEMPTS)`，再算 `attempts: Math.min(attemptsOf(payload.attempts ?? args.grant_attempts), maxAttempts)`，返回里带 `maxAttempts`。
- 次数下拉框的选项是 `ATTEMPT_OPTIONS.filter((count) => count <= request.maxAttempts)`；`onChange` 用 `attemptsOf(value, DEFAULT_ATTEMPTS, request.maxAttempts)`。
- 次数说明：`request.maxAttempts < MAX_ATTEMPTS` 时写「最多 {value.attempts} 次；这张工单每轮最多试 {request.maxAttempts} 次，用完后可以再授权一轮。」，否则保持「最多 {value.attempts} 次；次数用完授权就结束，需要重新申请。」。
- `ConfirmationCard` 不改（它只把 `parseCopilotGrantPayload` 的结果与用户改值传给卡片）。

### 4.7 提示词与代理定义

frontmatter 改动（正文由 §4.7.1–§4.7.3 改）：

| 文件 | 改动 |
|---|---|
| `maa_api/agent/specs/builtin/daily_ops.md` | 加一行 `verify_before_ready: true`；`version` 3 → 4 |
| `maa_api/agent/specs/builtin/copilot.md` | 加一行 `verify_before_ready: true`；`version` 3 → 4 |
| `maa_api/agent/specs/builtin/verifier.md` | `version` 1 → 2 |
| `maa_api/agent/specs/builtin/general.md` | 不改 |

`general` 保持默认 false，本里程碑不动它的正文与版本。三份被改定义的正文除下面写明的删除、替换与追加，其余逐字不动。

#### 4.7.1 `copilot.md` 正文

删除第 39 行整句：

```text
本里程碑的验证靠你自己看截图与结算。verifier 可以委派，但它的结论仍要你写进汇报。闭环迭代留给后续里程碑。
```

把第 35 行整句：

```text
次数、成功标准、助战以授权为准。用完就停，用 report 报 ready 汇总每次尝试与判定方。
```

替换为：

```text
次数、成功标准、助战以授权为准。这一轮次数用完还没成功时，按下面的研究循环处理，不要自己再申请授权。
```

第 29 行的位置要求保留不动（「执行前停在该关「开始行动」页」）。在正文末尾追加：

```markdown
## 研究循环

一张作业研究工单按五步循环推进，每一圈走完五步再决定下一圈。

1. 选或写作业：先 search_copilots 找现成作业，feasible=true 且 tier=ok 的直接 save_copilot_draft(site_id=…)；都不可行或搜不到，再 get_stage_level_data 看地图与出怪，自己写一份 save_copilot_draft(content=…)。第二圈起在上一版上改，save_copilot_draft 带 parent_id，note 写清改了什么、为什么改。
2. 到开始行动页：委派 navigator，task 写「把游戏停在关卡 <代号> 自己的「开始行动」页」，context 写关卡代号与关卡 id。导航员报 NAV_NODE_NOT_FOUND 时按 details 里的 nearest 先到最近的容器，再手动点进关卡页并用 remember_screen(node_id="stage:<代号>", title="<代号>") 记下。一次尝试结束后游戏通常已经回到这一页，先 get_screenshot 确认，确认了就不用再委派。
3. 执行：run_copilot_attempt(copilot_id=…, support=…)。第一次会弹作业授权卡，用户批准的次数就是这一轮最多能试的次数。
4. 判结果：outcome 是 three_star、two_star、clear、failed 时内核已经判定，直接采用；outcome 是 unknown 时，看返回里的 screenshot_id，用 annotate_screenshot(screenshot_id=…) 放大核对，再用 record_copilot_result 补判。
5. 复盘：读返回里的 actions 动作摘要，判断是练度不够、时机不对还是位置不对，决定下一圈改哪里。不要原样重跑同一份作业。

什么时候停：

- 达到成功标准：report(ready)，摘要写关卡、最后用的是哪一份作业、试了几次、判定方是谁。
- 这一轮授权用完（返回里 remaining 为 0）还没成功：report(waiting)，human_action 用 decision，写清试过哪几版、卡在哪、下一版准备怎么改，问要不要再授权一轮。用户回复后，下一次 run_copilot_attempt 会先弹新的授权卡；不要自己撤销旧授权，也不要绕过授权卡。
- 理智不足（sanity_insufficient）：report(blocked)，说明理智不够，不要吃药或碎石。
- 连续两次 not_started：委派 navigator 重新确认位置；仍然不行就 report(blocked)，说明停在哪个画面。
- 作业站和自己写的方案都缺干员或练度：report(failed)，写清缺谁、缺多少。

汇报 ready 之前系统会核对完成证据。内核判定成功且本工单流水线都完成时直接通过；模型补判的结果要先让验证员复核。report 返回 accepted=false 时，照返回里的 delegate 委派 verifier，验证通过再汇报。
```

#### 4.7.2 `verifier.md` 正文末尾追加

```markdown
## 核对完成证据

你只回答一件事：Worker 声称的结果有没有被证据支持。task 里是声称，context 里是执行记录。

- 截图要现拍：get_screenshot 看当前画面。作业研究看关卡页上这一关的通关标记与星数；其他工单看画面是否与声称一致。
- 流水线看终态：get_pipeline 看 context 里列出的每条流水线与其中任务的状态，失败的任务用 get_logs 看原因。
- 结算识别：context 里 judged_by=core 的尝试是内核判定的，可以作为证据；judged_by=model 的要另用截图确认。
- 证据不足就判 passed=false，在 notes 写缺什么证据、Worker 下一步该补什么。没看到反面证据不等于通过。
- confidence 按证据强度给：现拍截图与流水线终态都对得上给 0.9 以上；只有一项对得上给 0.7 左右；有对不上的给 0.3 以下。
- evidence 每条写一句能核对的事实，例如「流水线 3f2a1c88 状态 completed，4 个任务全部完成」。
```

#### 4.7.3 `daily_ops.md` 正文末尾追加

```markdown
## 汇报完成

做过写设备的操作之后汇报 ready，系统会先核对完成证据。流水线全部成功会直接通过；有失败，或者用过点击这类原子操作时，report 会返回 accepted=false 和一个 delegate，照它委派 verifier，通过之后再汇报。验证没通过时，能在委托范围内补救就补救后再验证；补救不了就 report(failed) 或 report(blocked) 说明情况。用户明确表示接受现状时，用 ask_user 发一张卡，选项里放「仍按完成结束」；用户选了它，再 report(ready)，details 里写 verification_waiver=<这张卡的问题 id>。
```

## 5. 实施任务

### T1 代理定义字段与版本

- 写范围：`maa_api/agent/specs/schema.py`、`maa_api/agent/specs/registry.py`、`maa_api/agent/specs/builtin/{daily_ops,copilot,verifier}.md` 的 frontmatter、`tests/agent/specs/test_verify_flag.py`（新建）、`tests/agent/test_builtin_workers.py`、`tests/api/test_agent_specs_router.py`、`tests/agent/specs/test_m70_specs.py`（只跑，不改断言）。
- 步骤：
  1. `AgentSpecFrontmatter` 加 `verify_before_ready: bool = False`；`AgentSpec` 继承，不重复声明。
  2. `registry.py` 的 `_check_field_rules` 加：`spec.verify_before_ready and spec.kind != "worker"` 时 `raise _invalid(file_name, "verify_before_ready", "只有 worker 可以为 true")`。
  3. `daily_ops.md`、`copilot.md` 加 `verify_before_ready: true`，`version` 改 4；`verifier.md` 只把 `version` 改 2。`general.md` 不动。正文留给 T6。
  4. `tests/agent/test_builtin_workers.py` 的 `WORKERS` 每项加 `version`，断言从 `(4 if name == "diagnose" else 3)` 改为 `spec.version == expected["version"]`；值：`daily_ops` 4、`copilot` 4、`planner` 3、`diagnose` 4、`analyst` 3、`ops` 3。
  5. `tests/api/test_agent_specs_router.py` 的 `EXPECTED_VERSIONS`：`daily_ops` 4、`copilot` 4、`verifier` 2；`general` 保持 6（M86 不给它导航工具，也不改它的版本），`navigator` 由 M86 改成 2。
  6. `test_m70_specs.py` 没有版本常量；它断言 `general` 的工具集合，M86 没有给 `general` 加工具，所以本任务不需要改它，只跑它确认 M87 的 frontmatter 改动没有破坏加载与集合相等。
- 验证：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/specs tests/agent/test_builtin_workers.py tests/api/test_agent_specs_router.py > /tmp/m87-t1.log 2>&1; echo $?; tail -n 5 /tmp/m87-t1.log
```

  退出码 0。
- 完成判据：`AgentSpecRegistry.load()` 正常；`daily_ops`、`copilot` 的 `verify_before_ready` 为 true，`general`、`planner`、`analyst`、`diagnose`、`ops` 为 false；三个文件的版本按上表；`test_m70_specs.py` 通过。

### T2 闸门与尝试仓储

- 写范围：`maa_api/agent/workers/verification.py`（新建）、`maa_api/db/repositories/copilot.py`、`tests/agent/workers/test_verification_gate.py`（新建）、`tests/db/test_copilot_attempt_repository.py`（新建）。
- 步骤：
  1. 按 §4.2 定义常量、`GateDecision`、`VerificationGate`、`merge_verification`。
  2. `CopilotAttemptRepository.list_for_session` 照 `find` 的扫法：`select(ResourceAsset).where(kind == copilot)`，逐份读 `meta["attempts"]`，按 `session_id` 过滤，按 `started_at` 升序。
  3. `check` 按 §4.5.2 实现：Worker 会话校验、审计过滤（§4.5.1 第 3、4 步）、流水线列表与 `retry_of_id` 排除、尝试流水线排除、非终态拒绝、机器证据、验证员结论、放行与失败计数。`agent_run.meta` 是 JSON 列，verdict 在 Python 里过滤（`isinstance(meta, Mapping) and isinstance(meta.get("verdict"), Mapping)`），不写 JSON SQL。
  4. `suggest()` 按 §4.5.2 拼 `task`/`context`，总长截到 4000 字，流水线与尝试各最多 3 条，超出写「另有 N 条未列出」。
  5. `record_verdict` 合并 `meta` 后整列赋值；运行不存在或写失败只记 warning，不抛。
  6. 测试用 `tests/fakes/v3_db.py` 的 `make_session_factory(tmp_path / "m87-gate.db")` 建库；直接写 `ticket`、`agent_session`、`agent_run`、`agent_audit`、`pipeline`、`agent_question`、`resource_asset` 行。
- 验证：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/workers/test_verification_gate.py tests/db/test_copilot_attempt_repository.py > /tmp/m87-t2.log 2>&1; echo $?; tail -n 5 /tmp/m87-t2.log
```

  退出码 0。
- 完成判据：§6 里 `test_verification_gate.py` 与 `test_copilot_attempt_repository.py` 的用例全部通过。

### T3 接入汇报与委派

- 写范围：`maa_api/agent/workers/service.py`、`maa_api/agent/workers/middleware.py`、`maa_api/agent/workers/delegation.py`、`tests/agent/workers/test_report_gate.py`（新建）、`tests/agent/workers/test_delegation_verdict.py`（新建）、`tests/agent/workers/test_worker_middleware.py`（加一条用例）。
- 步骤：
  1. `submit_report` 按 §4.5.3 插入闸门与 `merge_verification`；`VerificationGate(self._session_factory)` 现建，不新增 `app.state` 装配。
  2. `ReportMiddleware.wrap_tool` 识别 `accepted=false` 并原样返回（§4.5.3）。
  3. `DelegationService` 按 §4.5.4 写验证员结论。
  4. 用 `tests/fakes/fake_llm.py` 的 `FakeLLM` 驱动一个 `daily_ops` 运行：第一次 `report(ready)` 被拒，第二次委派验证员，第三次 `report(ready)` 放行。
- 验证：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/workers tests/agent/harness > /tmp/m87-t3.log 2>&1; echo $?; tail -n 5 /tmp/m87-t3.log
```

  退出码 0。
- 完成判据：被拒的那次 `report` 没有写 `ticket_report` 行；工单状态不变；被拒的这一批没有以 `report_submitted` 结束（运行继续到下一批）；`agent_audit` 有这次 `report` 的 `success` 行；放行后汇报行 `details.verification.method` 有值。

### T4 作业授权的轮次上限与新一轮

- 写范围：`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`maa_api/agent/policy.py`、`maa_api/agent/confirmation.py`、`maa_api/services/copilot/grant.py`、`tests/test_settings.py`、`tests/agent/test_copilot_loop_limit.py`（新建）。
- 步骤：
  1. 配置项按 §4.4；`_FIELDS` 与 `SETTING_KEYS` 同一次提交加齐，否则 `SETTINGS_SCHEMA` 在导入期 `KeyError`。
  2. `_evaluate_copilot_attempt` 没有有效授权的分支：用 `_ticket_worker_session(context)` 判会话；是工单 Worker 会话时 `limit = get_settings().copilot_loop.max_iterations`，`payload["attempts"] = min(payload["attempts"], limit)`、`payload["max_attempts"] = limit`；否则保持 v2 载荷。
  3. 次数用完的分支：`remaining <= 0` 时，会话是工单 Worker 会话就 `mark_exhausted` 后返回新的授权卡决策；否则抛 `COPILOT_GRANT_EXHAUSTED`。
  4. `_apply_copilot_grant_overrides` 把 `_pick("max_attempts")` 传给 `approve(max_attempts=…)`；`approve` 先 `_require_attempts(max_attempts)` 再夹住 `resolved_attempts`。
  5. `tests/test_settings.py` 的 `test_env_mapping_names_are_pinned` 加 `"copilot_loop.max_iterations": "MAA_COPILOT_LOOP_MAX_ITERATIONS"`；再加默认值与 1–10 范围的用例。
- 验证：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_copilot_loop_limit.py tests/agent/test_copilot_attempt_tools.py tests/agent/test_confirmation_service.py tests/services/test_copilot_grant.py tests/test_settings.py > /tmp/m87-t4.log 2>&1; echo $?; tail -n 5 /tmp/m87-t4.log
```

  退出码 0。
- 完成判据：v2 既有的作业授权测试一条不改仍通过；工单会话在次数用完后弹第二张卡；v2 聊天会话仍抛 `COPILOT_GRANT_EXHAUSTED`。

### T5 前端授权卡

- 写范围：`web/src/features/copilots/CopilotGrantCard.tsx`、`web/src/features/copilots/CopilotGrantCard.test.tsx`。
- 步骤：按 §4.6。
- 验证：

```bash
cd web && pnpm typecheck && pnpm test --run src/features/copilots > /tmp/m87-t5.log 2>&1; echo $?; tail -n 5 /tmp/m87-t5.log
```

  退出码 0。
- 完成判据：新用例与既有用例都通过；没有 `max_attempts` 的载荷仍是 1–10 共 10 个选项、说明文字不变。

### T6 提示词

- 写范围：`maa_api/agent/specs/builtin/{copilot,verifier,daily_ops}.md` 的正文。
- 步骤：
  1. 按 §4.7.1 删除 `copilot.md` 第 39 行整句，替换第 35 行整句，末尾追加「研究循环」一节。第 29 行的位置要求保留。
  2. 按 §4.7.2 在 `verifier.md` 末尾追加「核对完成证据」一节。
  3. 按 §4.7.3 在 `daily_ops.md` 末尾追加「汇报完成」一节。
  4. 不改 `general.md`，不改任何工具集。
- 验证：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/specs > /tmp/m87-t6.log 2>&1; echo $?; tail -n 5 /tmp/m87-t6.log
grep -nE "本里程碑的验证靠你自己|验证靠你自己看截图" maa_api/agent/specs/builtin/copilot.md; echo "grep-exit=$?"
grep -n "执行前停在该关" maa_api/agent/specs/builtin/copilot.md
grep -n "## 研究循环" maa_api/agent/specs/builtin/copilot.md
grep -n "## 核对完成证据" maa_api/agent/specs/builtin/verifier.md
grep -n "## 汇报完成" maa_api/agent/specs/builtin/daily_ops.md
```

  第一条 pytest 退出码 0；`grep` 旧句无输出（`grep-exit=1`）；后四条各 1 行。
- 文案回潮只做只读检查，运行前后都留 `docs/copy-audit` 的状态，不得把 `copy_inventory.py --check` 的改写带进提交：

```bash
git status --porcelain docs/copy-audit > /tmp/m87-copy-before.txt
.venv/bin/python scripts/copy_inventory.py --check > /tmp/m87-copy.log 2>&1; echo $?
git status --porcelain docs/copy-audit > /tmp/m87-copy-after.txt
diff /tmp/m87-copy-before.txt /tmp/m87-copy-after.txt; echo "diff-exit=$?"
grep -c "文案 AI 腔回潮" /tmp/m87-copy.log
```

  `diff-exit=0`；回潮条数与开工前一致（2026-11-09 的 HEAD 是 4 条，记录在台账）。若 `diff-exit=1`，先 `git restore --source=HEAD -- docs/copy-audit` 复原，再检查新文案。
- 完成判据：三份正文与本文一致；旧句不再出现；三节标题各一处；`copy-audit` 无新增改动。

### T7 评测场景

- 写范围：`tests/evals/scenarios/m87-*.yaml`（6 个新建）、`tests/evals/cassettes/m87-*.jsonl`（6 个新建）、`tests/evals/test_regression_replay.py`（场景数断言与驱动断言）。
- 步骤：
  1. 按 `tests/evals/scenarios/legacy-daily-submit.yaml` 的 JSON-in-YAML 格式写 6 个场景；`id` 与文件名主干一致，只用小写字母、数字与连字符。入口都是 `entry.kind="ticket"`，四个字段 `worker`/`title`/`brief`/`scope` 写全。`suite` 都是 `capability`，`agent` 写对应 Worker 名。
  2. 场景级 `human` 用维护性补丁提供的字段：`{"confirmations": "approve"}` 批准授权卡；`{"questions": {"answer": ["仍按完成结束"]}}` 回答放行提问。
  3. 作业场景的 `world` 用补丁提供的字段 `copilots` 与 `copilot_results`。`copilots` 在 `m87-copilot-*` 里写 `[{"id": "cp-eval-1-7", "title": "1-7 三星作业", "stage_id": "main_01-07", "stage_code": "1-7"}]`；`copilot_results` 按尝试顺序给结果，取值是 `three_star`、`two_star`、`clear`、`failed`、`sanity_insufficient`、`not_started`、`unknown`。
  4. cassette 手写，格式照 `tests/evals/cassettes/legacy-daily-submit.jsonl`：首行 `header`，`source` 写 `"handwritten"`；之后每个模型应答一行 `{"type": "call", "channel": "agent", "reply": ...}`。每条 cassette 的前两条是 ticket 运行的动态上下文落点探查（system 开头、没有 tools），应答 `{"text": "OK"}`；下面的实质调用序列接在它们后面。工具参数里引用前一次结果的字段用占位符 `{{result:<tool_call_id>:<字段>}}`（例如 `attempt_id`、`pipeline_id`、`question_id`）；每个 `call` 的 `tool_calls[*].id` 与下一次请求里的 tool result id 必须一致。以 `ready`/`failed`/`dropped` 结束的 ticket 场景，实质调用之后还有一条记忆抽取应答 `{"type": "call", "channel": "agent", "reply": {"text": "{\"items\": []}"}}`（不计入上面的调用条数；`waiting`/`blocked` 结束的不加）。
  5. 6 个场景的内容：

     **`m87-daily-machine-pass`**：`entry.worker="daily_ops"`，brief「用一条流水线把今天的日常跑完：Fight 1-7 三次，再领奖励。不要用药。」，`world.core_script="success"`、`screen="home"`。cassette 三次调用：`submit_pipeline` → `wait_pipeline`（`pipeline_id` 用占位符）→ `report(status="ready")`。期望：`tool_sequence` 按序含这三个调用，`forbid=["delegate"]`，`max_calls={"report":1}`；`outcome_state` 的 `runs.all` 里有一条 `agent_name="daily_ops"`、`status="succeeded"`、`finish_reason="report_submitted"`，`pipelines.last.status="completed"`，`model_calls <= 6`（两条探查应答计入）。

     **`m87-daily-failed-pipeline`**：入口同前，`world.core_script="failure"`。cassette 六次调用：`submit_pipeline` → `wait_pipeline` → `report(status="ready")` → `delegate(agent="verifier")` → 验证员应答 `{"passed": false, "confidence": 0.2, "evidence": [], "notes": "流水线失败，日常没跑完"}` → `report(status="failed")`。期望：`tool_sequence` 按序含 `report(ready)`、`delegate(verifier)`、`report(failed)`；`max_calls={"report":2,"delegate":1}`；`runs.all` 里有一条 `agent_name="verifier"`、`status="succeeded"`；`pipelines.last.status="failed"`。

     **`m87-copilot-two-rounds`**：`entry.worker="copilot"`，brief「用作业 cp-eval-1-7 打 1-7，要三星。」，`world.core_script="success"`、`screen="stage"`、`copilots` 同第 3 步、`copilot_results=["failed","three_star"]`，`human.confirmations="approve"`。cassette 四次调用：`get_screenshot` → `run_copilot_attempt(copilot_id="cp-eval-1-7", support="none")` → `run_copilot_attempt` 同参数再跑一次 → `report(status="ready")`。期望：`run_copilot_attempt` 至少 2 次、`report` 恰好 1 次、`delegate` 0 次；`runs.all` 里有一条 `agent_name="copilot"`、`finish_reason="report_submitted"`；`confirmations.count=1` 且 `statuses` 含 `approved`；`model_calls <= 8`（两条探查应答计入）。

     **`m87-copilot-round-exhausted`**：入口与 brief 同前，`settings={"copilot_loop.max_iterations": 2}`，`copilot_results=["failed","failed"]`，`human.confirmations="approve"`。cassette 三次调用：`run_copilot_attempt` × 2 → `report(status="waiting", human_action={"type":"decision"})`。期望：`run_copilot_attempt` 恰好 2 次、`report` 1 次、`human_action.type="decision"`；`confirmations.count=1` 且 `statuses` 含 `approved`；没有第三次尝试。

     **`m87-copilot-model-judged`**：入口同前，`copilot_results=["unknown"]`、`screen="settlement"`，`human.confirmations="approve"`。cassette 六次调用：`run_copilot_attempt` → `record_copilot_result(attempt_id=占位符, outcome="three_star")` → `report(status="ready")` → `delegate(agent="verifier")` → 验证员应答 `{"passed": true, "confidence": 0.9, "evidence": ["截图显示三星"], "notes": ""}` → `report(status="ready")`。期望：`record_copilot_result` 1 次、`report(ready)` 2 次、`delegate(verifier)` 1 次；`runs.all` 里有验证员且 `status="succeeded"`。

     **`m87-waiver`**：`entry.worker="daily_ops"`，brief 同第一条，`world.core_script="failure"`，`human.questions={"answer": ["仍按完成结束"]}`。cassette 十一次调用：`submit_pipeline` → `wait_pipeline` → `report(ready)` → `delegate(verifier)` → 验证员不通过 → `report(ready)` → `delegate(verifier)` → 验证员不通过 → `report(ready)`（这一次拿到 `verification_exhausted`）→ `ask_user(question="验证两次都没过，仍按完成结束吗？", options=[{"label":"仍按完成结束"},{"label":"继续补救"}])` → `report(status="ready", details={"verification_waiver":"{{result:<ask_user 的 id>:question_id}}"})`。期望：`report(ready)` 4 次、`delegate` 2 次、`ask_user` 1 次；`questions` 的 `count=1`、`statuses=["answered"]`；`runs.all` 有一条 `agent_name="daily_ops"`、`finish_reason="report_submitted"`。

  6. `tests/evals/test_regression_replay.py` 的 `test_every_scenario_loads_and_has_a_cassette` 把计数改为 `19`（M86 合并后该值是 13，对应 11 个原场景 + M86 的 2 个；若开工时仍是 11，说明 M86 的 T10 没有落地，按 §10 停工）。`assert len(scenarios) == 19` 与 `assert len(ids) == 19` 两处都要改。同文件加 `test_every_scenario_has_a_driver`：`from maa_api.agent.evals.runner import DRIVERS`，对每个场景断言 `scenario.entry.kind in DRIVERS`，并断言 `"ticket" in DRIVERS`。这条用例让「驱动缺失」在 pytest 里变红，而不是在评测里变成 `skipped`。
- 验证：

```bash
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay --min-pass-rate 1.0 > /tmp/m87-cap.log 2>&1; echo "cap=$?"; tail -n 8 /tmp/m87-cap.log; echo "skip=$(grep -c "跳过" /tmp/m87-cap.log || true)"
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_regression_replay.py > /tmp/m87-t7.log 2>&1; echo "test=$?"; tail -n 5 /tmp/m87-t7.log
```

  期望 `cap=0`、`test=0`、`skip=0`；`/tmp/m87-cap.log` 末行是 `EVAL OK`，汇总里 `14/14 通过（100.0%）`。
- 完成判据：19 个场景文件都加载且有 cassette；6 个新场景全部 `passed`、没有 `skipped`；能力套件通过率 100%，不低于 M86 台账记录的基线。

### T8 文档同步

- 写范围：§9 列出的文档与计数；不改 02–05 的代码契约以外的内容。
- 步骤：
  1. 按 §11 的「需要补进契约的名字」表把每个名字补进对应小节。
  2. 05 §6.1 的数量句改成「当前 107 个、模型可见 106 个」。
  3. 04 §6 的 M87 行在「说明」列写「工单会话里每份作业授权的最多尝试次数」；16 号文档补 `copilot_loop.max_iterations` 一行。
  4. 14 §6.16 的作业授权段补 `max_attempts` 是确认 payload 的服务端字段，用户 `overrides` 不能带它。
  5. 13 §8 末尾补一条实施记录（编号按落地时的顺序），写清与本文的偏离与计数。
- 验证：

```bash
.venv/bin/python scripts/check_doc_links.py --all > /tmp/m87-links.log 2>&1; echo $?; tail -n 3 /tmp/m87-links.log
grep -rn "verify_before_ready" docs/v3/05-运行时与代理定义.md docs/v3/02-系统架构.md
grep -rn "copilot_loop.max_iterations" docs/v3/04-接口与事件协议.md docs/v3/16-部署指南.md config.template.yaml
```

  第一条退出码 0；断链总数与开工前一致（2026-11-09 的 HEAD 是 4 条，全部在 `docs/copy-audit/ai-tells-docs.md`，不在基线文件里）；第二条至少 2 行；第三条至少 3 行。
- 完成判据：§9 列出的每一处都已更新；计数与 §9 一致；断链不新增。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/specs/test_verify_flag.py` | `test_verify_flag_defaults_false` | 不带字段的 `AgentSpecFrontmatter` 为 false；`general`、`planner` 为 false |
| `tests/agent/specs/test_verify_flag.py` | `test_only_workers_may_verify` | 子代理定义写 `verify_before_ready: true` 时 `AgentSpecRegistry.load()` 抛 `AgentSpecError`，消息含字段名 |
| `tests/agent/specs/test_verify_flag.py` | `test_builtin_workers_that_verify` | `daily_ops`、`copilot` 为 true；`diagnose`、`ops`、`analyst`、`general` 为 false |
| `tests/agent/workers/test_verification_gate.py` | `test_no_worker_session_is_not_required` | 工单没有 `worker_session_id`，或会话 `parent_session_id` 非空 → `accepted`、`not_required` |
| `tests/agent/workers/test_verification_gate.py` | `test_read_only_audits_are_not_required` | 只有 `get_screenshot` 这类只读审计 → `not_required` |
| `tests/agent/workers/test_verification_gate.py` | `test_completed_pipelines_pass_by_machine` | 最后一次 `submit_pipeline` 之后两条 `completed` → `machine`，evidence 两条 |
| `tests/agent/workers/test_verification_gate.py` | `test_failed_pipeline_requires_verifier` | 一条 `failed` → `verification_required`，`delegate.agent="verifier"`，context 含 id 前 8 位 |
| `tests/agent/workers/test_verification_gate.py` | `test_running_pipeline_rejects_before_verifier` | 还在 `running` → `verification_required`，`delegate is None`，message 含「wait_pipeline」 |
| `tests/agent/workers/test_verification_gate.py` | `test_raw_tools_block_machine_evidence` | 用过 `click` → 即使流水线全成功也 `verification_required` |
| `tests/agent/workers/test_verification_gate.py` | `test_core_judged_success_passes` | 最近一次尝试 `success=true, judged_by="core"` → `machine` |
| `tests/agent/workers/test_verification_gate.py` | `test_model_judged_success_needs_verifier` | `judged_by="model"` → `verification_required` |
| `tests/agent/workers/test_verification_gate.py` | `test_attempt_pipelines_are_excluded` | 尝试自己的 `failed` 流水线不参与流水线检查 |
| `tests/agent/workers/test_verification_gate.py` | `test_superseded_retry_pipeline_is_excluded` | 被 `retry_of_id` 取代的 `failed` 流水线不影响机器证据 |
| `tests/agent/workers/test_verification_gate.py` | `test_subagent_device_writes_do_not_count` | 导航员审计与子运行存在、Worker 只有只读审计 → `not_required` |
| `tests/agent/workers/test_verification_gate.py` | `test_fresh_passing_verdict_passes` | 结论晚于最后写操作、`passed=True`、0.9 → `verifier`，带 `verdict_run_id` |
| `tests/agent/workers/test_verification_gate.py` | `test_stale_verdict_is_ignored` | 结论早于最后写操作 → `verification_required` |
| `tests/agent/workers/test_verification_gate.py` | `test_low_confidence_counts_as_failure` | 0.6 → `verification_failed` |
| `tests/agent/workers/test_verification_gate.py` | `test_degraded_or_failed_run_verdict_counts` | `degraded=true` 与 `run.status="failed"` 但 `meta` 有结论 → 都算失败一次 |
| `tests/agent/workers/test_verification_gate.py` | `test_two_failures_exhaust` | 两次失败 → `verification_exhausted`，`delegate is None` |
| `tests/agent/workers/test_verification_gate.py` | `test_waiver_accepts_answered_question` | 选了「仍按完成结束」、晚于最后结论 → `waiver` |
| `tests/agent/workers/test_verification_gate.py` | `test_waiver_requires_two_failures` | 只有一次失败时带 waiver → `waiver_invalid` |
| `tests/agent/workers/test_verification_gate.py` | `test_waiver_rejects_wrong_session_option_or_time` | 别的会话、选项不符、作答过早三种各一例 → `waiver_invalid` |
| `tests/agent/workers/test_verification_gate.py` | `test_waiver_by_selection_after_restart` | 传 `{"selection":"仍按完成结束"}` 且存在晚于失败结论的回答 → `waiver` |
| `tests/agent/workers/test_verification_gate.py` | `test_context_is_capped` | 30 条流水线时 context ≤ 4000 字且含「另有」 |
| `tests/agent/workers/test_verification_gate.py` | `test_evidence_is_capped` | evidence 至多 10 条 |
| `tests/agent/workers/test_verification_gate.py` | `test_record_verdict_keeps_other_meta_keys` | 原有 `meta` 键保留，新增 `verdict` 字段齐全 |
| `tests/agent/workers/test_verification_gate.py` | `test_record_verdict_degraded` | 降级写成 `passed=false, confidence=0, degraded=true` |
| `tests/db/test_copilot_attempt_repository.py` | `test_list_for_session_filters_and_orders` | 两份作业里的尝试按会话筛出、按 `started_at` 升序 |
| `tests/db/test_copilot_attempt_repository.py` | `test_list_for_session_ignores_non_copilot_assets` | `kind != copilot` 的资源不参与 |
| `tests/agent/workers/test_report_gate.py` | `test_rejected_ready_keeps_ticket_and_run_going` | 没有新汇报行、工单仍是 `open`、运行继续、`agent_audit` 有 `report` 的 `success` 行 |
| `tests/agent/workers/test_report_gate.py` | `test_accepted_ready_writes_verification_details` | 汇报行 `details.verification.method="machine"` |
| `tests/agent/workers/test_report_gate.py` | `test_non_ready_reports_skip_gate` | `failed` 汇报不查闸门，直接写行 |
| `tests/agent/workers/test_report_gate.py` | `test_spec_without_flag_skips_gate` | `planner` 的 ready 不过闸门，`details` 不含 `verification` |
| `tests/agent/workers/test_report_gate.py` | `test_merged_details_trim_evidence` | 原 `details` 接近 4000 字时 evidence 被裁到总数不超过 4000 |
| `tests/agent/workers/test_report_gate.py` | `test_verification_metadata_survives_full_details` | 原 `details` 正好 4000 字时仍写入 `method` 与 id，总数不超过 4120 |
| `tests/agent/workers/test_worker_middleware.py` | `test_rejected_report_does_not_stop_the_run` | `accepted=false` 时 `stop_run=False`、`SUBMITTED_KEY` 仍是 `None`、本轮不以 `report_submitted` 收尾 |
| `tests/agent/workers/test_delegation_verdict.py` | `test_verifier_output_is_recorded` | `meta["verdict"]` 写入，含 `recorded_at` |
| `tests/agent/workers/test_delegation_verdict.py` | `test_degraded_verifier_output_is_recorded` | 降级也写，`degraded=True` |
| `tests/agent/workers/test_delegation_verdict.py` | `test_other_subagents_are_not_recorded` | `researcher` 的运行不写 `verdict` |
| `tests/agent/workers/test_delegation_verdict.py` | `test_record_failure_does_not_break_delegation` | 写 `meta` 抛异常时委派结果照常返回 |
| `tests/agent/test_copilot_loop_limit.py` | `test_ticket_session_prefill_is_clamped` | `grant_attempts=5`、上限 3 → 载荷 `attempts=3, max_attempts=3` |
| `tests/agent/test_copilot_loop_limit.py` | `test_chat_session_keeps_v2_prefill` | 没有工单 → `attempts=5`，没有 `max_attempts` |
| `tests/agent/test_copilot_loop_limit.py` | `test_approval_clamps_override_above_max` | 卡上改成 8、`max_attempts=3` → 授权行 `attempts=3` |
| `tests/agent/test_copilot_loop_limit.py` | `test_max_iterations_setting_is_hot` | 改设置后下一次预填用新值 |
| `tests/agent/test_copilot_loop_limit.py` | `test_ticket_session_exhausted_grant_opens_new_card` | 旧授权 `attempts=1, used=1` → 决策要确认，旧行 `status="exhausted"`，载荷带 `max_attempts` |
| `tests/agent/test_copilot_loop_limit.py` | `test_chat_session_exhausted_grant_still_raises` | 无工单会话 → 抛 `COPILOT_GRANT_EXHAUSTED`，旧行仍是 `active` |
| `tests/test_settings.py` | `test_env_mapping_names_are_pinned` | 整表含 `copilot_loop.max_iterations` 一行 |
| `tests/test_settings.py` | `test_copilot_loop_max_iterations_defaults_and_bounds` | 默认 3；`0` 与 `11` 都抛校验错；`schema_for` 的 minimum/maximum 是 1/10 |
| `tests/agent/test_builtin_workers.py` | `test_six_workers_match_contract` | 六个 Worker 的 `version` 与 `WORKERS` 表一致（`daily_ops` 4、`copilot` 4） |
| `tests/api/test_agent_specs_router.py` | `test_list_specs_returns_the_nineteen_builtin_definitions` | `EXPECTED_VERSIONS` 含 `daily_ops: 4`、`copilot: 4`、`verifier: 2`；`DETAIL_FIELDS` 仍是 17 |
| `tests/agent/specs/test_m70_specs.py` | `test_m70_specs_load_and_pass_consistency_rules`、`test_general_tools_equal_legacy_chat_minus_delegate_recon_plus_worker_tools` | 定义加载与 `general` 工具集合不变（该文件没有版本常量，只跑不改断言） |
| `web/src/features/copilots/CopilotGrantCard.test.tsx` | `limits attempts to max_attempts` | `max_attempts=3` 时下拉框 3 个选项，说明文字按 §4.6 |
| `web/src/features/copilots/CopilotGrantCard.test.tsx` | `keeps ten options without max_attempts` | 没有 `max_attempts` 时仍是 10 个选项、原文案不变 |
| `web/src/features/copilots/CopilotGrantCard.test.tsx` | `parses max_attempts and clamps prefill` | `max_attempts=2, attempts=5` → `attempts=2`、`maxAttempts=2` |
| `tests/evals/test_regression_replay.py` | `test_every_scenario_loads_and_has_a_cassette` | 19 个场景、19 个 id、每份 cassette 存在 |
| `tests/evals/test_regression_replay.py` | `test_regression_scenario_passes` | regression 套件的场景照旧全部通过 |
| `tests/evals/test_regression_replay.py` | `test_every_scenario_has_a_driver` | 每个场景的 `entry.kind` 都在 `DRIVERS` 里；`ticket` 在 `DRIVERS` 里（缺驱动不会静默成 `skipped`） |

## 7. 验收

### 7.1 命令（照 12 §4）

```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/m87-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m87-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
( cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build ) > /tmp/m87-web.log 2>&1; echo $?
.venv/bin/python scripts/frontend_smoke.py | tail -n 1
.venv/bin/python scripts/agent_smoke.py | tail -n 1
.venv/bin/python scripts/orchestrator_smoke.py | tail -n 1
.venv/bin/python scripts/copilot_research_smoke.py | tail -n 1
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m87-reg.log 2>&1; echo $?; tail -n 3 /tmp/m87-reg.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay --min-pass-rate 1.0 > /tmp/m87-cap.log 2>&1; echo $?; tail -n 3 /tmp/m87-cap.log; grep -c "跳过" /tmp/m87-cap.log || true
git diff --check; echo $?
.venv/bin/python scripts/check_doc_links.py --all > /tmp/m87-links.log 2>&1; echo $?; tail -n 3 /tmp/m87-links.log
```

期望：全量 pytest 与开工前的 `dev` 相比不新增失败（起点已有的失败记台账）；OpenAPI 不变；前端四步通过、`frontend_smoke` 末行 `SMOKE OK`；四个冒烟末行都是 `SMOKE OK`（`copilot_research_smoke.py` 是 v2 M29 的冒烟，证明聊天会话口径没有回退）；regression 评测 `EVAL OK`；capability 评测 `EVAL OK`、汇总 `14/14 通过（100.0%）`、`跳过` 计数为 0；`git diff --check` 退出码 0；断链总数与开工前一致（今天 4 条，全在 `docs/copy-audit/ai-tells-docs.md`）。

### 7.2 真机补充验证（不作为硬性门禁，结果记台账）

1. 指挥台里说「用作业三星 1-7」，确认建的是 `copilot` 工单；授权卡显示次数上限 3（`copilot_loop.max_iterations` 默认值）。
2. 观察：委派了导航员、`navigate_to` 停到 1-7 的开始行动页（`nearest` 回退时手动找并用 `remember_screen` 记下）、尝试结束后 ready 汇报行 `details.verification.method="machine"`、`ticket.result.verification` 可见。
3. 让一个日常工单故意失败（设备上手动停在非主界面），确认 ready 被拒、验证员被委派、最终汇报 `failed` 或 `blocked`。
4. 让一轮授权用完：确认汇报 `waiting`、`human_action.type="decision"`；用户回复继续后，下一次 `run_copilot_attempt` 弹第二张授权卡；同一操作在没有工单的 v2 聊天会话里仍报 `COPILOT_GRANT_EXHAUSTED`。
5. 两次验证不通过后，用 `ask_user` 出「仍按完成结束」，选后 ready 放行，汇报行 `details.verification.method="waiver"`；另外验证重启后作答走 `{"selection": "仍按完成结束"}` 的放行路径。
6. 台账记录：每张工单的模型调用次数、验证员调用次数、是否出现误放行、第二张卡的实际路径。

## 8. 风险与回滚

| 风险 | 对策 |
|---|---|
| 验证员误判导致做完的事被拒 | 两次失败后有用户放行；放行要结构化选项，可审计 |
| 闸门让 Worker 原地打转 | 同一批操作之后最多验证两次；`LoopGuardMiddleware` 判卡死；被拒的 report 不结束运行但也不写汇报 |
| 验证员调用增加成本 | 机器证据能放行时不调验证员；只读工单与未开启字段的 Worker 不过闸门 |
| 授权上限让用户困惑 | 授权卡说明写明「这张工单每轮最多试 N 次」；用完后的 waiting 汇报交代过程 |
| 老授权一直是 `active` 挡住新一轮 | 工单会话的下一次 `run_copilot_attempt` 主动落 `exhausted` 并弹卡；v2 聊天会话保持抛错，不改口径 |
| 手写 cassette 与工具调用 id 漂移 | 每个场景先按 §5 T7 的调用序列写 cassette，再跑 `agent_eval`；失败时按日志修正 id 与占位符，不打开 `--strict` 掩盖 |
| 评测依赖的补丁接口漂移 | §0 第 13 条逐项检查；不符合就按 §10 停工，不在 M87 里实现驱动 |

回滚：`git revert -m 1 <merge>`；没有迁移。`copilot_loop.max_iterations` 从 `SETTING_KEYS` 移除后，旧 `config.yaml` 里的同名字段按未知键忽略。

## 9. 文档同步

- [05 §1.1、§1.2](../05-运行时与代理定义.md)：样例 frontmatter 补 `verify_before_ready: true`；字段表加一行（bool，否，默认 false，只有 `kind=worker` 能为 true）。
- 05 §3.2：`daily_ops`、`copilot` 的工具列后注明 `verify_before_ready: true`；`general` 行不动。
- 05 §4.1：`report` 契约补拒绝形状 `accepted`/`reason`/`message`/`evidence`/`delegate`；补「`ready` 先过完成闸门（M87），被拒时不写汇报、本批不以 `report_submitted` 结束、运行继续」；补 `details.verification`、`details.verification_waiver` 两种形状；补确认载荷 `max_attempts`。
- 05 §5.2 第 17 位：`ReportMiddleware` 的职责补「见 `accepted=false` 时不结束运行、不写 `SUBMITTED_KEY`」。
- 05 §6.1：数量句改为「当前 107 个、模型可见 106 个」。
- [02 §2.2](../02-系统架构.md)：服务表加 `VerificationGate`（`maa_api/agent/workers/verification.py`，M87）；02 §4.1 的 `workers/` 行补 `verification.py`。02 §5 不加装配项。
- [03 §3.2](../03-数据模型.md)：`agent_run.meta` 的说明补 `verdict` 形状；03 §6.3：`ticket_report.details` 的说明补 `verification` 与 `verification_waiver`。
- [04 §6](../04-接口与事件协议.md)：`copilot_loop.max_iterations` 的「说明」列写「工单会话里每份作业授权的最多尝试次数」。
- [14 §6.16](../14-现行接口清单.md)：作业授权段补 `max_attempts` 是确认 payload 的服务端字段（用户 `overrides` 不接受它）。
- [16-部署指南](../16-部署指南.md)：补 `copilot_loop.max_iterations` 一行（默认 3，范围 1–10）。
- [13 §8](../13-决策记录.md) 末尾：补一条实施记录，写清与本文的偏离。
- 评测 ticket 驱动维护性补丁：M87 的 6 个场景依赖它在开工前落地；补丁的 spec、plan、ledger 不在本文范围，M87 只登记它提供的 `drive_ticket`、场景级 `human`、`World.copilots`、`World.copilot_results` 与带 `agent_session_id` 的 seed 流水线。
- 计数：工具 107/106、端点 186（agent 83）、错误码 171/170、业务表 57 不变；配置键 `SETTING_KEYS`/`_FIELDS` 从 142/131 变为 143/132；评测场景 13 → 19；迁移「无」。

## 10. 停下来问

- §0 第 13 条不成立：维护性补丁没有提供 `drive_ticket`、场景级 `human`、提问作答、`World.copilots`/`World.copilot_results` 或 seed 流水线的 `agent_session_id`。
- M86 合并后的 `navigate_to`、`nearest`、`remember_screen` 契约与 §4.5.5 第 2 步写的不一致。
- 实现时发现需要新的错误码来表达「验证不通过」，或需要改 `ReportStatus` 的取值。
- 验证员需要 05 §3.3 之外的写工具或更多步数（`max_steps` 只有 8）。
- 工单会话的旧授权落 `exhausted` 会影响 v2 聊天会话的行为，或 `approve` 的 `max_attempts` 需要暴露给 `overrides`。
- 评测的手写 cassette 需要真实模型录制才能通过（说明 §0 的 `--mode replay` 前提不成立）。

## 11. 需要补进契约的名字

| 名字 | 种类 | 用途 | 补进 |
|---|---|---|---|
| `verify_before_ready` | 代理定义字段（bool，默认 false，仅 `kind=worker` 可为 true） | 声明 ready 前要过完成闸门 | 05 §1.1、§1.2；`AgentSpecFrontmatter` 与 `_check_field_rules` |
| `VerificationGate` | 服务（`maa_api/agent/workers/verification.py`） | 完成闸门、验证员结论落库 | 02 §2.2、§4.1 |
| `agent_run.meta["verdict"]` | JSON 字段（`{passed, confidence, evidence, notes, degraded, recorded_at}`） | 验证员结论 | 03 §3.2 |
| `ticket_report.details["verification"]` | JSON 字段（`{method, verdict_run_id?, question_id?, evidence}`） | 放行依据 | 03 §6.3、05 §4.1 |
| `ticket_report.details["verification_waiver"]` | `report` 参数（字符串或 `{"selection": "仍按完成结束"}`） | 用户放行 | 03 §6.3、05 §4.1 |
| `report` 的拒绝结果 `accepted`/`reason`/`message`/`evidence`/`delegate` | 工具返回 | Worker 据此委派验证员；被拒时本批不结束运行，运行以非 `report_submitted` 的原因收尾 | 05 §4.1、§5.2 第 17 位 |
| 确认载荷 `max_attempts` | 确认 payload（`grant_copilot_attempts`） | 工单会话里每轮次数上限 | 05 §4.1、14 §6.16 |
| `copilot_loop.max_iterations` 的行为说明 | 配置键说明 | 每份作业授权的最多尝试次数 | 04 §6 说明列、16 部署指南 |

落地后的累计计数：工具 **107/106**、HTTP 端点 **186**（agent 83）、错误码 **171/170**、业务表 **57**、配置键 **143/132**（`SETTING_KEYS`/`_FIELDS`）、评测场景 **19**、迁移 **无**。
