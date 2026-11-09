# 评测 ticket 驱动（维护性补丁规格）

> 非里程碑补丁：按维护性改造处理，不占用新里程碑编号，不调整 [12-实施路线与里程碑](../12-实施路线与里程碑.md) 的顺序。M86 与 M87 的评测场景依赖本设施，M88–M98 共用。
> 本文是本次改造的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)，台账写 `docs/v3/ledgers/evals-ticket-driver.md`。
> 用户口径（2026-11-09）：先落地本补丁，在 M86 开工前完成；只改评测设施，不改生产行为。

## 0. 前置检查

本机没有 ripgrep，下表全部用 `grep`；长输出重定向到 `/tmp`，只看尾部与退出码。

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在本分支、工作区干净 | `git status -sb` | 第一行 `## evals-ticket-driver`，没有其他行 |
| 2 | 从 dev 开工并记录基点 | `git rev-parse --short dev HEAD` | 两行相同；2026-11-09 写作时是 `e41ad72`，实际以开工时为准 |
| 3 | 场景基线是 11 个 | `ls tests/evals/scenarios/*.yaml \| wc -l` | `11` |
| 4 | 场景 schema 已有 ticket 入口与人工策略 | `grep -n "class HumanPolicy" maa_api/agent/evals/scenario.py`；`grep -n 'entry.kind == "ticket"' maa_api/agent/evals/scenario.py` | 各 1 行（约 98、278 行） |
| 5 | ticket 入口必填校验已存在 | `grep -n "_TICKET_FIELDS" maa_api/agent/evals/scenario.py` | 2 行（定义与使用） |
| 6 | 驱动表只有 legacy_session | `grep -n "DRIVERS" maa_api/agent/evals/runner.py` | 2 行（定义约 356 行、`__all__`）；没有 `"ticket"` |
| 7 | 跨会话消息排序仍是 session_id | `grep -n "ORDER BY session_id, seq" maa_api/agent/evals/runner.py` | 1 行（约 415 行） |
| 8 | 录制回放按通道记上一条应答 | `grep -n "_last_reply_ids" maa_api/agent/evals/cassette.py` | 3 行（初始化约 962 行、读取约 1000 行、写入约 1008 行） |
| 9 | 建单与汇报入口 | `grep -n "async def open_ticket\|async def submit_report" maa_api/agent/workers/service.py` | 2 行（约 274、502 行） |
| 10 | 提问作答与列表入口 | `grep -n "async def list\|async def answer\|async def skip" maa_api/agent/questions.py` | 3 行（约 172、228、273 行） |
| 11 | 确认列表与裁决入口 | `grep -n "async def resolve\|async def list" maa_api/agent/confirmation.py` | 2 行（约 287、500 行） |
| 12 | 作业尝试仓储 | `grep -n "class CopilotAttemptRepository\|async def append\|async def find\|_ATTEMPT_WRITABLE_FIELDS" maa_api/db/repositories/copilot.py` | 4 行（约 201、234、275、67 行） |
| 13 | 作业站搜索入口 | `grep -n "async def search" maa_api/services/game/copilots.py` | 1 行（约 106 行） |
| 14 | 评测装配点 | `grep -n "app.state.worker_service\|app.state.confirmation_service\|app.state.question_service\|app.state.ticket_service\|app.state.copilot_attempt_service\|app.state.copilot_service" maa_api/main.py` | 各至少 1 行 |
| 15 | 数据库方言允许按自增 id 排序 | `grep -nF "id: int \| None = Field(default=None, primary_key=True)" maa_api/db/models.py` | 1 行（`AgentMessage`，约 543 行） |
| 16 | 评测基线 | `.venv/bin/python -m pytest -o addopts= -q tests/evals > /tmp/eval-base.log 2>&1; echo $?; tail -n 2 /tmp/eval-base.log` | `0`；`51 passed`（2026-11-09 实测，耗时约 53 秒） |
| 17 | 回放基线 | `.venv/bin/python scripts/agent_eval.py --suite all --mode replay > /tmp/eval-base-replay.log 2>&1; echo $?; tail -n 3 /tmp/eval-base-replay.log` | `0`；`regression replay：5/5 通过`、`capability replay：6/6 通过`、末行 `EVAL OK` |
| 18 | 决策记录尾部编号 | `grep -n "^### 8\\.17[6-9]" docs/v3/13-决策记录.md` | 有 §8.176、§8.177、§8.178（M86–M90 升格）；本补丁接在 §8.178 之后 |

第 4、5、6、7、8、9、10、11、12 条不符合时不要自己补，按 §10 停下来问。第 2 条在 spec 升格提交之后重新记录；2026-11-09 的 `dev` 已经是 `e41ad72`（M86–M90 spec 合并提交）。

## 1. 背景与目标

M68 的评测设施只实现 `legacy_session` 入口：场景用 v2 会话发消息，一次一条用户轮次。M69 起工单是 v3 的一等工作单元，M86 的导航场景与 M87 的验证闸门场景都要从「用户建一张工单」开始，还要模拟用户批准确认卡、回答 `ask_user` 提问、提供作业站作业与按顺序的作业结果。这些场景目前无法回放：`DRIVERS` 里没有 `ticket`，场景级 `human`、`World.copilots`、`World.copilot_results` 与 seed 流水线的 `agent_session_id` 都不存在。

做完能验证的结果：

1. 场景 YAML 可以写 `entry.kind: ticket` 与场景级 `human`，加载时校验字段；未知字段报错。
2. `drive_ticket` 用真实 `WorkerService.open_ticket` 建单，轮询该工单 Worker 会话的根运行到终态；确认卡按 `human.confirmations` 批准或拒绝，提问按 `human.questions` 走真实 `QuestionService.answer`，包含 §8.177 的跨重启投递逻辑。
3. Worker 委派的子代理会话（`agent_session.parent_session_id` 指向 Worker 会话）被并入 `recorder.session_ids`，子代理的工具调用进入 `collect_observation`，并按真实发生顺序参与判分。
4. `World.copilots` 造出可被策略与工具读到的 `kind=copilot` 资源，`World.copilot_results` 按尝试顺序决定假尝试结果；作业授权确认卡可以走真实批准链路。
5. 旧的 11 个场景与 cassette 不改，`--suite all --mode replay` 仍然 `5/5` 与 `6/6` 通过。
6. 新设施有独立用例：确认批准、提问作答、子代理会话观察、作业结果注入、超时与失败分类。

## 2. 非目标

- 不改生产代码的行为：`WorkerService`、`TicketService`、`QuestionService`、`ConfirmationService`、作业授权、作业尝试服务与 `main.py` 的装配都不动。
- 不新增端点、错误码、表、迁移、配置键、事件或工具。
- 不新增依赖；不引入 pytest 插件。
- 不改现有 11 个场景 YAML 与 11 个 cassette。
- 不在本补丁里写 M86/M87 的场景或 cassette；它们由各自里程碑的 T 任务写，本补丁只提供驱动与字段。
- 不做前端改动；不写 `live` 模式的新能力。
- 不实现 `orchestrator` 入口的驱动；它仍按「缺驱动」处理。

## 3. 相关文档与代码

- [M86 §0 #16、T11、§3、§10、§11](./m86-nav-graph.md)：`drive_ticket`、`Scenario.human`、`HumanPolicy`、`world.nav` 的依赖口径。
- [M87 §0 #13、T7、§3、§10、§11](./m87-verifier-copilot-loop.md)：`World.copilots`、`World.copilot_results`、seed 流水线 `agent_session_id` 的完整清单。
- [10 §6.4–§6.6、§6.9](../10-可观测与评测.md)：场景字段表、录制文件与假世界说明。
- [13 §8.175–§8.178](../13-决策记录.md)：三次维护性补丁与 M86–M90 升格的实施裁决；§9 待拍板。
- 场景 schema：`maa_api/agent/evals/scenario.py` 的 `HumanPolicy`（98 行）、`Entry`（74 行）、`SeedPipeline`（148 行）、`WorldSeed`（160 行）、`World`（166 行）、`Scenario`（243 行）、`_TICKET_FIELDS`（67 行）、`Scenario._cross_field_rules`（270 行）。
- 运行器：`maa_api/agent/evals/runner.py` 的 `CaseRecorder`（223 行）、`DriveFn`（232 行）、`DRIVERS`（356 行）、`_run_with_timeout`（359 行）、`collect_observation`（399 行）、消息 SQL（410–417 行）、缺驱动分支（662–679 行）。
- 回放：`maa_api/agent/evals/cassette.py` 的 `CassetteTransport.__init__`（951 行）、`_last_reply_ids`（962 行）、`_check_tool_ids`（985 行）、`_message_tool_ids`（824 行）、`check_unused`（1090 行）。
- 假世界：`tests/evals/environment.py` 的 `_open_world`（79 行）、`_seed_world`（216 行）、`FakeWorldEnvironment`（272 行）。
- 建单与汇报：`maa_api/agent/workers/service.py` 的 `OpenedTicket`（115 行）、`WorkerService.open_ticket`（274 行）、`submit_report`（502 行）。
- 工单：`maa_api/agent/tickets/service.py` 的 `TicketService.create`（331 行）、`attach_worker_session`（455 行）、`record_report`（583 行）。
- 确认：`maa_api/agent/confirmation.py` 的 `ConfirmationService.resolve`（287 行）、`list`（500 行）。
- 提问：`maa_api/agent/questions.py` 的 `QuestionService.list`（172 行）、`answer`（228 行）、`skip`（273 行）、`_deliver_if_orphaned`（358 行）。
- 作业：`maa_api/services/game/copilots.py` 的 `CopilotService.search`（106 行）、`TIERS`（44 行）；`maa_api/services/copilot/attempt.py` 的 `run`（275 行）、`record`（444 行）、`COUNTED_OUTCOMES`（91 行）；`maa_api/services/copilot/grant.py` 的 `is_success`（122 行）；`maa_api/db/repositories/copilot.py` 的 `_ATTEMPT_WRITABLE_FIELDS`（67 行）、`append`（234 行）、`update`（247 行）、`find`（275 行）。
- 模型：`maa_api/db/models.py` 的 `AgentMessage.id`（543 行）、`AgentRun.session_id`/`parent_run_id`/`status`（598–609 行）、`AgentSession.parent_session_id`（509 行）、`Pipeline.agent_session_id`（168 行）、`ResourceAsset`（1186 行，`content` 与 `path` 二选一）。
- 枚举：`maa_api/domain/enums.py` 的 `ConfirmationStatus`（77 行）、`QuestionStatus`（84 行）、`AgentRunStatus`（216 行）、`ResourceAssetKind`（225 行）、`AgentSessionKind`（194 行）。
- 装配：`maa_api/main.py` 的 `app.state.session_factory`（465 行）、`copilot_grant_service`/`copilot_result_service`/`copilot_attempt_service`/`copilot_service`（1173–1204 行）、`confirmation_service`（1259 行）、`question_service`（1276 行）、`ticket_service`（1380 行）、`worker_service`（1825–1837 行）。

## 4. 设计

### 4.1 场景 schema

`maa_api/agent/evals/scenario.py` 只加下面这些；`Entry` 与 ticket 入口校验已经在 M68 落地，不改：

```python
#: ``World.copilot_results`` 的合法取值（M87 §4.5 的尝试结果集合）。
COPILOT_RESULTS = (
    "three_star",
    "two_star",
    "clear",
    "failed",
    "sanity_insufficient",
    "not_started",
    "unknown",
)
CopilotResult = Literal[
    "three_star",
    "two_star",
    "clear",
    "failed",
    "sanity_insufficient",
    "not_started",
    "unknown",
]

#: ``World.copilots[*]`` 允许的键；其它键在加载期报错。
_COPILOT_FIELDS = ("id", "title", "stage_id", "stage_code")


class HumanPolicy(BaseModel):
    model_config = _MODEL_CONFIG

    #: ``pending`` 与 ``ignore`` 都是「不处理」，前者是 M86/M87 场景的写法。
    confirmations: Literal["approve", "reject", "ignore", "pending"] = "reject"
    questions: Literal["ignore", "skip"] | AnswerSpec = "ignore"
    interrupt: InterruptSpec | None = None


class SeedPipeline(BaseModel):
    model_config = _MODEL_CONFIG

    title: str | None = None
    status: Literal["completed", "failed", "cancelled"]
    minutes_ago: int = 10
    error_code: str | None = None
    error_message: str | None = None
    #: 流水线归属的会话；为空时不写这一列（M87 的 seed 需要）。
    agent_session_id: str | None = None
    tasks: list[SeedTask]
    logs: list[SeedLog] = Field(default_factory=list)


class World(BaseModel):
    model_config = _MODEL_CONFIG

    core_script: Literal["success", "failure", "stuck", "disconnect"] = "success"
    device_connected: bool = True
    screen: Literal["home", "desktop", "stage", "battle", "settlement"] = "home"
    http: list[HttpFixture] = Field(default_factory=list)
    seed: WorldSeed = Field(default_factory=WorldSeed)
    #: 作业站作业；同时决定 ``search_copilots`` 的返回与可执行的作业资源。
    copilots: list[dict[str, Any]] = Field(default_factory=list)
    #: 每次 ``run_copilot_attempt`` 的结果，按尝试顺序消费。
    copilot_results: list[CopilotResult] = Field(default_factory=list)

    @field_validator("copilots")
    @classmethod
    def _check_copilot_rows(
        cls, value: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        for index, row in enumerate(value):
            unknown = sorted(set(row) - set(_COPILOT_FIELDS))
            if unknown:
                raise ValueError(
                    f"copilots[{index}] 未知字段：{', '.join(unknown)}"
                )
            if not str(row.get("id") or "").strip():
                raise ValueError(f"copilots[{index}] 缺少 id")
            if not str(row.get("stage_id") or "").strip():
                raise ValueError(f"copilots[{index}] 缺少 stage_id")
        return value

    @model_validator(mode="after")
    def _copilot_results_need_copilots(self) -> World:
        if self.copilot_results and not self.copilots:
            raise ValueError("copilot_results 需要同时给 copilots")
        return self


class Scenario(BaseModel):
    model_config = _MODEL_CONFIG

    # ...既有字段不变...
    #: ticket 入口的人工策略；legacy_session 入口仍读 ``turns[].human``。
    human: HumanPolicy = Field(default_factory=HumanPolicy)
```

数据形状：

```json
{
  "entry": {"kind": "ticket", "worker": "general", "title": "导航到基建", "brief": "导航到基建，到了告诉我。", "scope": "在游戏界面里导航到基建"},
  "human": {"confirmations": "approve", "questions": {"answer": ["仍按完成结束"]}},
  "world": {
    "copilots": [{"id": "cp-eval-1-7", "title": "1-7 三星作业", "stage_id": "main_01-07", "stage_code": "1-7"}],
    "copilot_results": ["failed", "three_star"],
    "seed": {"pipelines": [{"title": "旧流水线", "status": "completed", "agent_session_id": "eval-seed-session", "tasks": [{"type_name": "Fight"}]}]}
  }
}
```

- `copilots[*].title` 与 `copilots[*].stage_code` 可省略：`title` 缺省用 `id`，`stage_code` 缺省用 `stage_id`。
- `human.questions` 复用既有 `AnswerSpec`：`{"answer": ["选项 label", "..."]}`，1–6 项；这是 M87 场景已经写定的形状。
- `human.confirmations` 的 `pending` 只对 ticket 驱动有意义；legacy 驱动看到 `pending` 等同于 `ignore`，行为不变。
- `Scenario.human` 缺省是 `HumanPolicy()`，即 `confirmations="reject"`、`questions="ignore"`：与既有 `Turn.human` 的缺省一致，避免改变旧场景语义。

### 4.2 `drive_ticket`

`maa_api/agent/evals/runner.py` 新增：

```python
#: 人工策略轮询间隔；确认与提问都按这个节奏检查。
POLL_SECONDS = 0.05

#: 驱动自己的超时比场景上限早这么多秒，先给出带工单信息的失败。
DRIVE_TIMEOUT_MARGIN_SECONDS = 2.0

#: 运行终态；``waiting_human`` 不算终态。
TERMINAL_RUN_STATUSES = frozenset(
    {"succeeded", "failed", "interrupted", "cancelled"}
)


class TicketNotOpened(RuntimeError):
    """建单失败；消息以 ``ticket_not_opened：`` 开头。"""


class TicketRunTimeout(RuntimeError):
    """根运行在驱动期限内没有到终态；消息以 ``run_timeout：`` 开头。"""


async def drive_ticket(
    eval_app: EvalApp, scenario: Scenario, recorder: CaseRecorder
) -> None:
    """建一张 ticket 工单，轮询 Worker 根运行到终态，并收集子代理会话。"""


async def _apply_human_policy(
    app: Any,
    *,
    ticket_id: str,
    confirmations: str,
    questions: Literal["ignore", "skip"] | AnswerSpec,
    answered: set[str],
) -> None:
    """按场景策略批准确认、回答提问；已处理的提问 id 记进 ``answered``。"""


async def _latest_root_run(app: Any, session_id: str) -> AgentRun | None:
    """该会话最近一条 ``parent_run_id IS NULL`` 的运行。"""


async def _child_session_ids(app: Any, parent_session_id: str) -> list[str]:
    """``parent_session_id = 父会话`` 的子代理会话 id，按创建时间升序。"""


DRIVERS: dict[str, DriveFn] = {
    "legacy_session": drive_legacy_session,
    "ticket": drive_ticket,
}
```

`drive_ticket` 的完整步骤：

```text
1. entry = scenario.entry；四个字段在加载期已校验非空，这里按 str 收窄。
2. 调 app.state.worker_service.open_ticket：
   kind=str(entry.worker), title=str(entry.title), brief=str(entry.brief),
   scope=str(entry.scope), origin="user", strength="explicit",
   source="web", sender="web"。
   抛 AppError 时转 TicketNotOpened(
       f"ticket_not_opened：工单没有建成：{exc.code} {exc.message or ''}")。
3. recorder.session_ids.append(opened.worker_session_id)；
   recorder.turns.append(str(entry.brief))。
4. deadline = time.monotonic() + max(
       scenario.limits.timeout_seconds - DRIVE_TIMEOUT_MARGIN_SECONDS, 1.0)；
   last_status = "none"；answered: set[str] = set()。
5. 循环：
   a. await _apply_human_policy(
          app,
          ticket_id=opened.ticket_id,
          confirmations=scenario.human.confirmations,
          questions=scenario.human.questions,
          answered=answered,
      )。
   b. run = await _latest_root_run(app, opened.worker_session_id)；
      非空时 last_status = str(run.status)。
   c. run 非空且 last_status 在 TERMINAL_RUN_STATUSES 中 → 跳出循环。
   d. 到 deadline → 抛 TicketRunTimeout(
        f"run_timeout：工单 {opened.ticket_id} 在 "
        f"{scenario.limits.timeout_seconds:g} 秒内没有跑到终态"
        f"（最后运行状态：{last_status}）")。
   e. await asyncio.sleep(POLL_SECONDS)。
6. child_ids = await _child_session_ids(app, opened.worker_session_id)；
   逐个追加到 recorder.session_ids，已经存在的不重复追加。
```

`_apply_human_policy` 的步骤：

```text
1. 若 confirmations 是 "approve" 或 "reject"：
   page = await app.state.confirmation_service.list(
       status=ConfirmationStatus.PENDING, size=50)
   对 page["items"] 每一条：
       await app.state.confirmation_service.resolve(
           str(item["id"]),
           approved=confirmations == "approve",
           resolved_by="eval",
           reason="评测脚本的裁决",
       )
2. 若 questions 不是 "ignore"：
   page = await app.state.question_service.list(
       status=QuestionStatus.PENDING, size=50)
   对 page["items"] 每一条：
       origin = item.get("origin") or {}
       如果 origin.get("ticket_id") != ticket_id → 跳过。
       question_id = str(item["question_id"])；已在 answered 中 → 跳过。
       questions == "skip"：
           await app.state.question_service.skip(question_id, answered_by="eval")
       否则是 AnswerSpec：
           await app.state.question_service.answer(
               question_id, selected=list(questions.answer),
               answered_by="eval")
       answered.add(question_id)。
```

实现细节：

- 需要的 import：`from maa_api.domain.errors import AppError`；`from maa_api.domain.enums import ConfirmationStatus, QuestionStatus`；`AgentRun` 与 `AgentSession` 已由 `runner.py` 顶部导入，`asyncio` 与 `time` 也已导入。
- 确认只处理 `pending`，世界里只有这一张工单，不按 `origin.ticket_id` 过滤；列表 `size=50` 足够一次取证。
- 提问必须按 `origin.ticket_id` 过滤：同一次运行里可能还有总控或其它来源的提问，不能替用户回答。
- 提问作答走真实 `QuestionService.answer`，不额外封装投递：`answer()` 内部已经调用 `_deliver_if_orphaned`，覆盖 §8.177 的「等待者不在且来源运行已结束」。
- `answered` 只防重复；提问一旦变成 `answered`/`skipped`，列表里就不会再出现。
- 提问在 Worker 本轮内是阻塞的（`ask_user` 等到答案才返回），所以正常路径不存在「作答后另起一轮」；若来源运行已经结束、`answer()` 触发投递并启动新一轮，驱动不额外等待这一轮，M86/M87 的场景不依赖这种时序。
- 作答的 label 必须在选项里。`QuestionService.answer` 抛 `QUESTION_SELECTION_INVALID` 时不要吞掉，让 `run_case` 记成 `error`，场景作者据此修 cassette 或 `human.questions`。
- 超时先由驱动自己触发，早于 `_run_with_timeout`；外层超时仍保留，用来中断卡住的运行。
- 驱动不等待工单结束后的记忆抽取，也不等待转发给总控的回复轮；它们可能在本例判分之后发生，不能进入 cassette 的必需应答。

### 4.3 录制回放的多会话工具结果校验

M86/M87 的 ticket 场景会先由 Worker 调 `delegate`，子代理在同一段时间里产生自己的模型请求，然后 Worker 带着 `delegate` 的工具结果继续。现有 `CassetteTransport._check_tool_ids` 只记住**通道内上一条**应答的 tool call id：子代理的应答会把这条记录覆盖成空集，Worker 的下一条请求因此报 `mismatch`。这个失败还会被 `run_case` 的 `check_unused()` 掩盖成 `unused`，排查时看不出真实原因。

`maa_api/agent/evals/cassette.py` 改成按「tool call id 是谁发出的」校验，而不是按相邻应答：

```python
class CassetteTransport(_BaseEvalTransport):
    def __init__(self, cassette, *, max_calls, strict=False, channel_override=None):
        # ...既有赋值...
        #: tool call id → 发出它的那条应答的 id 集合（同一批多个调用共享同一个集合）。
        self._issued: dict[str, set[str]] = {}

    def _check_tool_ids(self, call, body, channel):
        messages = _messages(body)
        trailing: list[str] = []
        for message in reversed(messages):
            if message.get("role") != "tool":
                break
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str):
                trailing.append(call_id)
        if trailing:
            expected: set[str] = set()
            for call_id in trailing:
                expected |= self._issued.get(call_id, set())
            if expected != set(trailing):
                message = (
                    f"{channel} 请求的工具结果 id {sorted(set(trailing))} "
                    f"与已发出的调用 {sorted(expected)} 不一致"
                )
                self._fail("mismatch", message)
                return _error_response(message, "eval_mismatch")
        reply_ids = _message_tool_ids(call)
        for call_id in reply_ids:
            self._issued[call_id] = set(reply_ids)
        return None
```

同一文件 `run_case` 的错误顺序改成「先报已记录的失败，再查未用完」：

```python
if isinstance(transport, CassetteTransport):
    if transport.error is not None:
        raise transport.error
    transport.check_unused()
    warnings.extend(transport.warnings)
```

- 单会话的 11 个旧场景：请求末尾的 id 正是上一条应答发出的 id，`expected == trailing`，行为不变。
- 多会话：Worker 的 `call-delegate` 由 Worker 那条应答发出；子代理的应答不影响这条映射；Worker 带 `call-delegate` 的请求仍然通过。
- 同一批多个 tool call：每个 id 都映射到同一个集合，请求末尾的集合与它相等才通过。
- cassette 的消费顺序仍是请求到达顺序；ticket 场景按「探查两条 → Worker/子代理交错的实质调用 → 记忆抽取一条」写，不回退、不重排。

### 4.4 假世界的作业站与作业尝试

`tests/evals/environment.py` 新增两个假服务与两段装配。它们只替换 `app.state` 上的两个服务，不改生产类：

```python
class ScriptedCopilotSearch:
    """``search_copilots`` 的假作业站：返回场景里写死的作业，不访问网络。"""

    def __init__(self, copilots: list[dict[str, Any]]) -> None: ...

    async def search(
        self,
        *,
        stage: str,
        limit: int = 10,
        order: str = "hot",
        only_feasible: bool = True,
        include_content: bool = True,
        compact: bool = False,
    ) -> dict[str, Any]: ...


class ScriptedCopilotAttempt:
    """``run_copilot_attempt`` / ``record_copilot_result`` 的假实现。"""

    def __init__(
        self,
        session_factory: Any,
        copilots: list[dict[str, Any]],
        results: list[str],
    ) -> None: ...

    async def run(
        self,
        *,
        copilot_id: str,
        session_id: str | None = None,
        support: str | None = None,
        stage_id: str | None = None,
        stage_code: str | None = None,
        grant_attempts: int | None = None,
        success: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]: ...

    async def record(
        self, attempt_id: str, outcome: str, evidence: str = ""
    ) -> dict[str, Any]: ...


async def _seed_copilots(app: Any, scenario: Scenario) -> None:
    """把 world.copilots 写成 kind=copilot 的 ResourceAsset 行。"""


def _install_copilot_fakes(app: Any, scenario: Scenario) -> None:
    """按 world 字段替换 app.state.copilot_service / copilot_attempt_service。"""
```

`_seed_copilots` 的行形状：

```python
ResourceAsset(
    id=str(row["id"]),
    kind=ResourceAssetKind.COPILOT.value,
    name=str(row.get("title") or row["id"]),
    content={"actions": []},              # content 与 path 必须二选一
    meta={
        "stage_id": str(row["stage_id"]),
        "stage_code": str(row.get("stage_code") or row["stage_id"]),
        "source": "eval",
    },
    enabled=True,
)
```

- `ResourceAsset` 有 `UniqueConstraint("kind", "name")`：`world.copilots` 里两份作业的 `title` 不能相同；没有 `title` 时用 `id`，天然唯一。
- 策略层 `_copilot_stage` 读 `meta["stage_id"]` 与 `meta["stage_code"]`；确认批准时 `CopilotGrantService.approve` 需要 `stage_id`，所以 seed 必须带它。

`ScriptedCopilotAttempt.run` 的行为：

```text
1. 从 self._results 按顺序取下一个结果；取完抛 RuntimeError(
       f"copilot_results 用完了：第 {len(self._results) + 1} 次 run_copilot_attempt")。
2. 从 self._copilots 找到 copilot_id 对应行；找不到抛 RuntimeError。
3. 读当前会话的有效授权（CopilotGrantService(session_factory).get_active(session_id)），
   取 success 标准（缺省 "three_star"）与 grant_id。
4. counted = outcome in COUNTED_OUTCOMES（从 maa_api.services.copilot.attempt 导入）。
   judged_by = None if outcome == "unknown" else "core"。
   success_flag = counted and is_success(outcome, 标准)（is_success 从 grant.py 导入）。
5. attempt = {
       "id": uuid4, "copilot_id", "grant_id", "session_id",
       "pipeline_id": None, "recognition_pipeline_id": None, "exit_pipeline_id": None,
       "outcome", "judged_by", "counted", "success": success_flag,
       "screenshot_id": None, "evidence": "", "support": support or "none",
       "attempt_number": 本 (session_id, copilot_id) 的第几次,
       "stage_id", "stage_code", "pipeline_status": "completed",
       "started_at"/"finished_at": ISO 字符串,
   }。
6. CopilotAttemptRepository(session).append(copilot_id, attempt) 并 commit。
7. 返回 {**attempt, "attempt_id": id, "remaining": None, "grant": 授权 wire 或 None,
        "grant_status": 授权状态或 None, "actions": [], "action_count": 0,
        "hint": _GOAL_HINTS.get(outcome, "")}。
```

`ScriptedCopilotAttempt.record` 的行为：

```text
1. 只接受 RECORDABLE_OUTCOMES；否则抛 RuntimeError。
2. CopilotAttemptRepository.find(attempt_id)；找不到抛 RuntimeError。
3. 只允许 data["outcome"] == "unknown"；否则抛 RuntimeError。
4. repo.update(asset_id, attempt_id, outcome=..., judged_by="model", counted=True,
              evidence=..., finished_at=ISO)；commit。
5. 返回 {"attempt": 更新后的 dict, "counted": True,
        "success": is_success(outcome, "three_star"), "grant": None,
        "remaining": None, "hint": ""}。
```

`ScriptedCopilotSearch.search` 的返回形状对齐 `CopilotService.search` 的紧凑投影：

```python
{
    "query": {"stage": stage, "stage_id": None, "stage_code": stage,
              "matched_by": "eval", "order": order, "only_feasible": only_feasible,
              "limit": limit, "include_content": include_content, "compact": compact},
    "results": [
        {"site_id": row["id"], "title": title, "uploader": "eval", "views": 0,
         "rating_level": None, "upload_time": None, "stage_name": stage_code,
         "difficulty": None, "feasible": True, "tier": "ok", "missing_count": 0,
         "missing": [], "note": None, "link": None, "operators": []}
        for row in 前 limit 条
    ],
    "total": len(rows), "candidates": len(rows), "site_total": len(rows),
    "site_page": 1, "has_next": False,
    "tiers": {tier: (len(rows) if tier == "ok" else 0) for tier in TIERS},
    "note": None, "hint": None,
    "sources": [], "untrusted": True, "unavailable": [], "region_note": None,
}
```

装配顺序（`_open_world` 内，`TestClient.__enter__()` 之后）：

```text
1. 既有 fake_core_factories 装配照旧。
2. if scenario.world.copilots:
       client.portal.call(_seed_copilots, app, scenario)
       app.state.copilot_service = ScriptedCopilotSearch(scenario.world.copilots)
3. if scenario.world.copilot_results:
       app.state.copilot_attempt_service = ScriptedCopilotAttempt(
           app.state.session_factory, scenario.world.copilots,
           scenario.world.copilot_results)
4. yield EvalApp(...)
```

- 两个假服务挂在 `app.state` 上，随每次 `_open_world` 新建，随 app 销毁；不需要进程级清理，也不改 `default_copilot_service()` 的单例。
- 只给 `copilot_results` 而不给 `copilots` 在加载期就报错；只给 `copilots` 时，`search_copilots` 可用，`run_copilot_attempt` 仍走真实服务（会执行真实流水线），场景作者要同时给结果队列。
- 假尝试**不消耗授权**：`remaining` 固定 `None`，授权次数用不完。M87 的轮次上限由 M87 自己的 `copilot_loop.max_iterations` 与闸门单元测试覆盖，不在评测的假服务里。这是本补丁明确的取舍。

### 4.5 seed 流水线的 `agent_session_id`

`_seed_world`（`tests/evals/environment.py`，216 行）在构造 `Pipeline` 时加一行 `agent_session_id=spec.agent_session_id`。为了让 YAML 里的固定 id 可用，写库前先补一条占位会话：

```text
if spec.agent_session_id:
    如果 session.get(AgentSession, spec.agent_session_id) 为空：
        session.add(AgentSession(
            id=spec.agent_session_id,
            title="评测种子会话",
            kind=AgentSessionKind.WORKER.value,
            model=get_settings().llm.model,
            base_url=get_settings().llm.base_url or None,
        ))
```

- 不补占位会话时，`PRAGMA foreign_keys=ON`（`maa_api/db/session.py` 64 行）会让 seed 直接失败。
- 占位会话的 `kind` 写 `worker`：seed 流水线表达的是「某个 Worker 会话提交的流水线」，M87 的闸门按 Worker 会话取流水线。
- 占位会话没有 `ticket_id`；它只服务 seed，不启动泵，不进入 `recorder.session_ids`。

### 4.6 跨会话消息顺序

`collect_observation`（`maa_api/agent/evals/runner.py`，399 行）的消息 SQL 从

```sql
... WHERE session_id IN (...) ORDER BY session_id, seq
```

改成

```sql
... WHERE session_id IN (...) ORDER BY id
```

原因：`AgentMessage.id` 是 `INTEGER PRIMARY KEY AUTOINCREMENT`（`maa_api/db/models.py` 543 行），跨会话插入顺序就是它；`seq` 只在单个会话内单调。ticket 场景里 Worker、子代理与其它会话的消息交错写入，按 `session_id, seq` 会把每个会话的消息整段排在一起，`tool_sequence` 的 `subsequence` 与 `text_contains` 的 `final` 都会看到错误的先后。改成 `ORDER BY id` 后：

- 单会话旧场景：`id` 顺序与 `seq` 顺序一致，判分不变。
- 多会话：`delegate`、子代理工具调用、Worker 后续调用按真实发生顺序进入 `_pair_tool_calls` 与 `_texts`。
- `messages[*]` 的字段形状不变，不新增 `id` 键；只改排序。

### 4.7 失败分类与超时

| 情形 | `CaseResult.status` | 文本 | 产生处 |
|---|---|---|---|
| `entry.kind` 在 `DRIVERS` 里没有驱动 | `skipped` | `detail.reason = "missing_driver：这个入口还没有实现"` | `run_case` 的缺驱动分支（662 行） |
| `open_ticket` 抛 `AppError` | `error` | `ticket_not_opened：工单没有建成：<code> <message>` | `drive_ticket` |
| 根运行在驱动期限内没有终态 | `error` | `run_timeout：工单 <id> 在 <N> 秒内没有跑到终态（最后运行状态：<status>）` | `drive_ticket` |
| 提问作答不在选项里 | `error` | `AppError：...QUESTION_SELECTION_INVALID...` | `QuestionService.answer` 上抛 |
| `copilot_results` 用完 | `error` | `RuntimeError：copilot_results 用完了：第 N 次 run_copilot_attempt` | `ScriptedCopilotAttempt.run` |
| cassette 不匹配、用完或有多余 | `error` | `mismatch：...`、`exhausted：...`、`unused：...` | `CassetteTransport` |

超时口径：

- 驱动期限 `max(scenario.limits.timeout_seconds - 2.0, 1.0)` 秒；轮询间隔 0.05 秒。
- 外层 `_run_with_timeout` 仍按 `scenario.limits.timeout_seconds` 秒兜底，超时后中断 `recorder.session_ids` 里的会话。
- 驱动期限到点时先抛 `TicketRunTimeout`，保证错误文本里有工单 id 与最后运行状态，而不是只有「超过 N 秒没有结束」。

### 4.8 实现裁决

| # | 冲突或空白 | 裁决 | 理由 |
|---|---|---|---|
| R1 | 用户口径写 `HumanPolicy.confirmations` 默认 `"approve"`；现有代码默认 `"reject"`，且旧场景依赖默认拒绝 | 保留 `"reject"`，把 `"pending"` 加进取值 | 改默认会让没写 `human` 的旧场景从拒绝变批准；M86/M87 都显式写 `approve`，不受影响 |
| R2 | 用户口径写 `HumanPolicy.questions: dict[str, list[str]]`；现有代码是 `AnswerSpec.answer` | 复用 `AnswerSpec`，不新增 dict 形状 | M87 已写 `{"answer": ["仍按完成结束"]}`，与现有结构完全一致；新增 dict 会分叉两套语义 |
| R3 | `entry.kind == "ticket"` 的解析与四个字段校验 | 不改 `Entry` 与 `_cross_field_rules`，只补测试 | M68 已经实现，补丁只消费；改动会扩大范围 |
| R4 | `Scenario.human` 与 `Turn.human` 同名 | 并存：ticket 驱动读前者，legacy 驱动读后者 | 旧场景的 `turns[].human` 不动，ticket 场景不写 `turns` |
| R5 | `World.copilots` 的请求签名是 `list[dict]` | 保持 `list[dict[str, Any]]`，用 `field_validator` 拒绝未知键并校验 `id`/`stage_id` | 保持 M87 已写定的 YAML 形状，同时满足「未知字段报错」 |
| R6 | 假作业尝试是否要模拟授权消耗 | 不模拟；`remaining=None`，授权次数不减少 | 评测要的是结果队列与确认链路；授权次数与轮次上限由 M87 的单元测试覆盖 |
| R7 | 记忆抽取会在工单 `ready`/`failed`/`dropped` 后再发一次模型请求 | ticket 场景的 cassette 末尾固定加一条 `{"text": "{\"items\": []}"}`；`settings.memory.enabled=false` 时不加 | 不加会 `exhausted`/`unused`；`waiting`/`blocked` 不触发结束监听，不加 |
| R8 | 转发给总控的回复轮可能在判分后启动 | 驱动不等待它，也不为它准备 cassette 应答 | 它在 `check_unused` 之后启动；等待它会引入不确定的额外模型调用 |
| R9 | 子代理请求与 Worker 请求交错，现有 cassette 校验报 `mismatch` | 改成「tool call id 由哪条应答发出」的映射校验，并先报 `transport.error` 再查 `unused` | 不修就无法回放 M86/M87 的 `delegate` 场景；旧单会话场景语义不变 |
| R10 | seed 流水线的 `agent_session_id` 在 YAML 里没有现成会话 | seed 时补一条 `kind=worker` 的占位会话 | 外键开着，不补会直接失败；占位会话只服务 seed |
| R11 | 提问作答是否需要最小封装 | 不需要；直接调 `QuestionService.answer` / `skip` | `answer()` 已包含 §8.177 的 `_deliver_if_orphaned`，再包一层会重复投递 |
| R12 | 缺驱动仍是 `skipped` 还是 `error` | 保持 `skipped`，只把原因改成 `missing_driver：...` | 不改变 M68 的既有口径；M87 T7 用 pytest 断言每个场景都有驱动，缺失会在测试里变红 |

## 5. 实施任务

### T1 场景 schema

- 写范围：`maa_api/agent/evals/scenario.py`、`tests/evals/test_ticket_driver.py`（只写解析用例与文件头）
- 步骤：
  1. 在 `tests/evals/test_ticket_driver.py` 写四条解析用例（§6 的 `scenario_*`）：完整 ticket 场景往返、`copilot_results` 缺 `copilots`、`copilots` 未知键、非法 `copilot_results` 值。
  2. 按 §4.1 加 `COPILOT_RESULTS`、`CopilotResult`、`_COPILOT_FIELDS`；`HumanPolicy.confirmations` 加 `"pending"`；`SeedPipeline` 加 `agent_session_id`；`World` 加 `copilots`/`copilot_results` 与两个校验器；`Scenario` 加 `human`；把 `COPILOT_RESULTS`、`CopilotResult` 加进 `__all__`。
  3. 运行验证命令，修到全绿。
- 验证：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k "scenario or copilot_result" > /tmp/eval-t1.log 2>&1; echo $?; tail -n 5 /tmp/eval-t1.log
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_eval_scenario.py > /tmp/eval-t1-old.log 2>&1; echo $?; tail -n 3 /tmp/eval-t1-old.log
```

  两个 `0`；第二条仍是 `6 passed`（2026-11-09 基线）。
- 完成判据：完整字段能读入；未知 `world` 字段、未知 `copilots` 键、缺 `id`/`stage_id`、缺 `copilots` 的 `copilot_results` 都抛 `ScenarioError` 且消息带文件路径。

### T2 多会话回放校验

- 写范围：`maa_api/agent/evals/cassette.py`、`maa_api/agent/evals/runner.py`（只改 `run_case` 的错误顺序）、`tests/evals/test_ticket_driver.py`（加一条 cassette 用例）
- 步骤：
  1. 写用例：三条 agent 应答，第一条发 `call-a`，第二条是另一个会话的纯文本，第三条模拟 Worker 带 `call-a` 工具结果的请求；断言 `_check_tool_ids` 不报 `mismatch`。再加一条反例：请求带从未发出的 `call-x`，断言 `mismatch`。
  2. 按 §4.3 把 `_last_reply_ids` 换成 `_issued`，改 `_check_tool_ids`。
  3. 把 `run_case` 的 `check_unused()` 移到 `transport.error` 之后。
- 验证：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k cassette > /tmp/eval-t2.log 2>&1; echo $?; tail -n 5 /tmp/eval-t2.log
```

  退出码 `0`。
- 完成判据：跨会话工具结果通过；从未发出的 id 仍报 `mismatch`；`unused` 不再掩盖 `mismatch`。

### T3 `drive_ticket`

- 写范围：`maa_api/agent/evals/runner.py`、`tests/evals/test_ticket_driver.py`
- 步骤：
  1. 写用例（§6 的 `ticket_driver_*`）：注册、general 工单到 report、确认批准、提问作答、子代理会话、建单失败、运行超时、缺驱动。
  2. 按 §4.2 加异常、常量、`drive_ticket`、三个私有函数、`DRIVERS` 的 `"ticket"`；按 §4.6 改消息 SQL；按 §4.7 改缺驱动原因。
  3. 运行验证，逐条修。
- 验证：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k "ticket_driver and not copilot" > /tmp/eval-t3.log 2>&1; echo $?; tail -n 8 /tmp/eval-t3.log
```

  退出码 `0`。
- 完成判据：general 工单跑到 `report_submitted`；确认状态是 `approved`；提问状态是 `answered`；`recorder.session_ids` 含 Worker 与子代理两个 id；建单失败与超时的错误文本分别以 `ticket_not_opened：`、`run_timeout：` 开头。

### T4 假世界装配

- 写范围：`tests/evals/environment.py`、`tests/evals/test_ticket_driver.py`
- 步骤：
  1. 写用例（§6 的 `copilot_*`、`seed_*`）：`search_copilots` 返回场景作业；两次 `run_copilot_attempt` 按队列给出 `failed` 与 `three_star`；确认卡批准一次；`_seed_world` 写 `agent_session_id` 并补占位会话。
  2. 按 §4.4 加两个假服务、`_seed_copilots`、`_install_copilot_fakes` 并接到 `_open_world`；按 §4.5 改 `_seed_world`。
  3. 运行验证。
- 验证：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k "copilot or seed" > /tmp/eval-t4.log 2>&1; echo $?; tail -n 8 /tmp/eval-t4.log
```

  退出码 `0`。
- 完成判据：`search_copilots` 的结果含场景 `id`；两次尝试的 `outcome` 依次是 `failed`、`three_star`，第二次 `success=true`；`confirmations.statuses` 是 `["approved"]`；seed 流水线的 `agent_session_id` 与占位会话一致。

### T5 文档、全量验证与台账

- 写范围：`docs/v3/10-可观测与评测.md`、`docs/v3/13-决策记录.md`、`docs/v3/ledgers/evals-ticket-driver.md`（新建）
- 步骤：
  1. 按 §9 更新 10 与 13。13 的编号按落地时的下一个空号，当前是 `§8.179`；由主 agent 确认。
  2. 写台账：分支、基点、合并、spec/plan 链接、T0–T5 表格、Rulings（§4.8 的 R1–R12）、验收结果、备注。
  3. 跑 §7 的全部命令，结果记进台账。
  4. `git merge --no-ff` 回 `dev`，删除分支，补台账的合并提交。
- 验证：§7 全部命令。
- 完成判据：文档断链 `BROKEN 0`；台账含实际基点与合并提交；`dev` 上工作区干净。

## 6. 测试清单

新增 `tests/evals/test_ticket_driver.py`：

| 用例名 | 断言要点 |
|---|---|
| `test_scenario_ticket_human_world_round_trip` | `scenario.human.confirmations=="approve"`、`questions` 是 `AnswerSpec`、`world.copilots[0]["id"]`、`copilot_results==["failed","three_star"]`、`seed.pipelines[0].agent_session_id` 读入 |
| `test_copilot_results_require_copilots` | 只有 `copilot_results` 时 `ScenarioError` 消息含 `copilot_results` 与 `copilots` |
| `test_copilot_unknown_field_is_rejected` | `copilots[0]` 多一个键时 `ScenarioError` 消息含键名与 `copilots[0]` |
| `test_copilot_result_value_is_validated` | `copilot_results=["victory"]` 时 `ScenarioError` 消息含 `victory` |
| `test_ticket_driver_is_registered` | `DRIVERS["ticket"] is drive_ticket` |
| `test_ticket_driver_runs_general_ticket_to_report` | 用例 `passed`；`runs` 有 `agent_name="general"`、`finish_reason="report_submitted"`；`recorder.session_ids[0]` 是 Worker 会话 |
| `test_ticket_driver_approves_confirmation` | Worker 调带 `medicine: 1` 的 `submit_pipeline`；`confirmations.statuses==["approved"]`；`submit_pipeline` 的工具结果是 `ok`，`pipelines.count==1` |
| `test_ticket_driver_answers_question` | `questions.statuses==["answered"]`；提问的 `selection` 含脚本给的 label；运行继续到 report |
| `test_ticket_driver_includes_subagent_session` | `recorder.session_ids` 长度 2；`runs` 有 `agent_name="verifier"` 且 `status="succeeded"`；子代理的工具调用进入观察 |
| `test_ticket_driver_reports_ticket_not_opened` | `status=="error"`；`error` 以 `ticket_not_opened：` 开头 |
| `test_ticket_driver_reports_run_timeout` | `limits.timeout_seconds=3`、`human.questions="ignore"`、Worker 调 `ask_user`；`status=="error"`；`error` 以 `run_timeout：` 开头 |
| `test_missing_driver_is_skipped_with_reason` | `orchestrator` 入口；`status=="skipped"`；`detail.reason` 以 `missing_driver：` 开头 |
| `test_copilot_search_uses_world_rows` | `search_copilots` 的工具结果含 `cp-eval-1-7` 与 `stage_name="1-7"` |
| `test_copilot_results_drive_attempts` | `entry.worker="copilot"`；两次尝试的 `outcome` 依次 `failed`、`three_star`；第二次 `success` 为 true；`confirmations.count==1` 且 `statuses==["approved"]` |
| `test_seed_pipelines_keep_agent_session_id` | `_seed_world` 后流水线的 `agent_session_id` 等于场景值；`agent_session` 有同 id 行 |
| `test_seed_creates_placeholder_session` | 不预建会话时也写出 `kind="worker"`、`title="评测种子会话"` 的占位行，重复 seed 不新增第二行 |

旧 11 个场景的 replay 不变由既有 `tests/evals/test_regression_replay.py` 与 §7 的 `--suite all` 覆盖，不重复写用例。

同一文件里的 cassette 用例：

| 用例名 | 断言要点 |
|---|---|
| `test_cassette_tool_result_from_earlier_reply_passes` | 跨会话的 `call-a` 工具结果通过 `_check_tool_ids` |
| `test_cassette_unknown_tool_result_is_mismatch` | 请求带从未发出的 `call-x` 时 `transport.error.code=="mismatch"` |

## 7. 验收

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py > /tmp/eval-ticket-unit.log 2>&1; echo $?; tail -n 5 /tmp/eval-ticket-unit.log
.venv/bin/python -m pytest -o addopts= -q tests/evals > /tmp/eval-ticket-all.log 2>&1; echo $?; tail -n 5 /tmp/eval-ticket-all.log
.venv/bin/python scripts/agent_eval.py --suite all --mode replay > /tmp/eval-ticket-replay.log 2>&1; echo $?; tail -n 4 /tmp/eval-ticket-replay.log
.venv/bin/python scripts/dump_openapi.py --check > /tmp/eval-ticket-openapi.log 2>&1; echo $?; tail -n 2 /tmp/eval-ticket-openapi.log
.venv/bin/python scripts/check_doc_links.py | tail -n 1
grep -n "async def drive_ticket" maa_api/agent/evals/runner.py
grep -n '"ticket": drive_ticket' maa_api/agent/evals/runner.py
grep -n "human: HumanPolicy" maa_api/agent/evals/scenario.py
grep -n "copilots\|copilot_results" maa_api/agent/evals/scenario.py tests/evals/environment.py | wc -l
grep -n "agent_session_id" tests/evals/environment.py | wc -l
grep -n "ORDER BY id" maa_api/agent/evals/runner.py
```

期望：

- 第 1、2、3、4 条退出码都是 `0`；`tests/evals` 的 `passed` 数不少于 69（基线 51，本补丁新增 18 条）；回放末行 `EVAL OK`，汇总 `regression replay：5/5 通过（100.0%）` 与 `capability replay：6/6 通过（100.0%）`。
- 第 5 条末行 `BROKEN 0`。
- 第 6 条 1 行；第 7 条 1 行；第 8 条 1 行；第 9 条至少 6 行；第 10 条至少 2 行；第 11 条 1 行。
- 全量后端（主 agent 复核）：`.venv/bin/python -m pytest -o addopts= -q > /tmp/eval-ticket-full.log 2>&1; echo $?; tail -n 5 /tmp/eval-ticket-full.log`，除既有 4 条已知失败（3 条真资源 + 1 条时间敏感）外没有新增失败。

## 8. 风险与回滚

- **多会话 cassette 校验**：改的是所有 replay 共用的检查。旧 11 个场景是单会话，`expected == trailing` 恒成立；T2 用例与 `test_regression_replay.py` 同时钉住。
- **记忆抽取应答**：ticket 场景以 `ready`/`failed`/`dropped` 结束时多一条 agent 应答；漏写会让用例 `exhausted`，多写会 `unused`。`waiting`/`blocked` 不加。
- **假尝试与生产语义的差距**：不消耗授权、不提交流水线、不跑识别；只看结果队列与确认链路。M87 的授权次数、轮次上限与闸门由 M87 的单元测试覆盖。
- **`agent_session_id` 的占位会话**：只服务 seed，`kind=worker`、没有 `ticket_id`；如果将来有测试按「所有 worker 会话都有工单」断言，会看到这一条。占位会话标题固定「评测种子会话」，可据此排除。
- **`ORDER BY id`**：单会话顺序不变；多会话顺序变化正是目的。若某个旧场景依赖 `session_id, seq` 的分组顺序（当前没有），会在这里暴露。
- **回滚**：`git revert -m 1 <merge>` 即可；本补丁无迁移、无配置、无表结构、无生产代码改动，回滚不丢数据。

## 9. 文档同步

- [10 §6.4](../10-可观测与评测.md)：`entry.kind` 的 `ticket` 行改为「由 `drive_ticket` 驱动」；字段表加 `human`（场景级）、`human.confirmations` 的 `pending`、`world.copilots`、`world.copilot_results`、`world.seed.pipelines[].agent_session_id`；注明 `turns[].human` 只服务 `legacy_session`。
- [10 §6.5](../10-可观测与评测.md)：补一句「多会话回放的 tool call id 校验按发出它的应答，不按相邻应答」。
- [10 §6.6](../10-可观测与评测.md)：补 ticket 驱动的假世界行为（建单、确认、提问、子代理会话、假作业站、假尝试、记忆抽取应答）。
- [10 §6.9](../10-可观测与评测.md)：把「M72 删除 legacy 入口之前要迁到 ticket」改成「ticket 入口已可用；旧场景迁移由 M72/M73 负责」。
- [13 §8](../13-决策记录.md)：在 §8.178 之后加一条维护性补丁记录（当前下一个空号是 §8.179，由主 agent 确认），写用户口径、实际、R1–R12、规格与台账链接、验证方式。
- 新建 `docs/v3/ledgers/evals-ticket-driver.md`：按 [specs/README §4](./README.md) 的台账模板，补「做了什么」「Rulings I made」「验收」「备注」。
- 02–05、14：不改。本补丁只提供评测设施与文档 10 的字段说明，不新增端点、错误码、表、配置键、工具、事件。计数（工具、端点、错误码、表、配置键、迁移 head、评测场景数）全部不变。

## 10. 停下来问

- M86/M87 要求 `Scenario.human` 的默认值是 `"approve"`，或要求 `questions` 必须是 `dict[str, list[str]]` 且与 `AnswerSpec` 不兼容：这会改旧场景语义，先问。
- `WorkerService.open_ticket` 的签名或 `origin`/`strength` 校验在开工前变了，按本文调用不成立。
- `ConfirmationService.resolve`、`QuestionService.answer/list` 的公开签名在开工前变了，或提问作答不再有 §8.177 的投递。
- `delegate` 场景在按 §4.3 修好后仍然 `mismatch`，或需要给 cassette 增加按会话分文件的新格式。
- 假作业站或假尝试服务无法只靠 `app.state` 注入，需要改生产服务或 `main.py` 装配。
- 需要新增端点、错误码、表、配置键、工具、事件或依赖。
- M86/M87 的 cassette 里探查应答不是两条，或记忆抽取应答的条数与 §4.8 R7 不符。

## 11. 需要补进契约的名字

本补丁的名字只进评测文档，不进 02–05：

| 名字 | 种类 | 用途 | 落点 |
|---|---|---|---|
| `drive_ticket`、`TicketNotOpened`、`TicketRunTimeout` | 评测驱动 | `entry.kind=ticket` 的建单、轮询、人工策略与子代理会话收集 | 10 §6.4、§6.6 |
| `Scenario.human`、`HumanPolicy`（`confirmations` 加 `pending`） | 场景字段 | ticket 场景的确认与提问策略 | 10 §6.4 |
| `World.copilots`、`World.copilot_results`、`CopilotResult` | 场景字段 | 假作业站与按顺序的尝试结果 | 10 §6.4 |
| `SeedPipeline.agent_session_id` | 场景字段 | seed 流水线归属的会话 | 10 §6.4 |
| `ScriptedCopilotSearch`、`ScriptedCopilotAttempt`、`_seed_copilots`、`_install_copilot_fakes` | 测试设施 | `tests/evals/environment.py` 的假服务与装配 | 10 §6.6 |
| cassette 多会话 tool call id 校验 | 行为说明 | 子代理与 Worker 交错时的回放规则 | 10 §6.5 |
