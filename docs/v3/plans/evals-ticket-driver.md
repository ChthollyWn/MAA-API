# 评测 ticket 驱动 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给评测设施加 `entry.kind=ticket` 驱动：真实建单、轮询 Worker 根运行、按场景策略批准确认与回答提问、把子代理会话并入观察；补 `World.copilots` / `World.copilot_results`、场景级 `human` 与 seed 流水线的 `agent_session_id`。M86 开工前落地，M88–M98 共用。

**Architecture:** 只改 `maa_api/agent/evals/`（scenario、runner、cassette）与 `tests/evals/`（environment、新测试文件）。生产服务一律复用真实实现：`WorkerService.open_ticket` 建单、`ConfirmationService.list/resolve` 处理确认、`QuestionService.list/answer/skip` 处理提问并沿用 §8.177 的投递。假世界只在 `app.state` 上替换 `copilot_service` 与 `copilot_attempt_service`。回放的 tool call id 校验从「通道内上一条应答」改成「哪个应答发出了这个 id」，以支持 Worker 与子代理交错的 ticket 场景。

**Tech Stack:** FastAPI + SQLModel + pytest；`tests/fakes/v3_db.py` 的临时库；`ruamel.yaml` 场景加载；评测传输层是 `httpx.AsyncBaseTransport` 的录制回放。

**Spec:** `docs/v3/specs/evals-ticket-driver.md`

## Global Constraints

- 分支 `evals-ticket-driver`，从 `dev` 新建；完成后 `git merge --no-ff` 回 `dev` 并删除分支；**不 push**。
- 不占里程碑号，不改 [12-实施路线](../12-实施路线与里程碑.md) 的顺序；不新增 tag。
- 不改生产行为：`maa_api/agent/workers/`、`maa_api/agent/tickets/`、`maa_api/agent/questions.py`、`maa_api/agent/confirmation.py`、`maa_api/services/copilot/`、`maa_api/main.py` 一行不改。
- 不新增端点、错误码、表、迁移、配置键、事件、工具、依赖。
- 不改现有 11 个场景 YAML 与 11 个 cassette；不新增场景 YAML 或 cassette 文件（新测试的 cassette 写在 `tmp_path`）。
- 场景 YAML 与文档用简体中文；提交信息风格 `feat(evals): …（维护性补丁，见 docs/v3/13 §8.179）`，最终编号由主 agent 确认。
- 后端命令：`.venv/bin/python -m pytest -o addopts= -q <文件>`；本机没有 `rg`，用 `grep -n`。
- 长输出重定向到 `/tmp`，只看尾部与退出码；主 agent 复核时独占重跑。
- 台账写 `docs/v3/ledgers/evals-ticket-driver.md`，完成后从进度文件复制并补「Rulings I made」。

## 开工基线

- `dev` = `e41ad72`（2026-11-09，`merge: M86–M90 升格为可执行规格`）；`HEAD` = `e41ad72`；`git merge-base dev HEAD` = `e41ad72`。
- M86–M90 的 spec 已从 `docs/v3/specs/todo/` 搬到 `docs/v3/specs/`；本补丁不依赖它们的内容，只在文档里引用路径。
- 13 §8 当前尾部是 `§8.178`（M86–M90 升格），本补丁接 `§8.179`；落地时若已被别的补丁占用，按当时的下一空号，由主 agent 确认。
- 评测基线（2026-11-09 实测）：`tests/evals` 51 passed（约 53 秒）；`agent_eval --suite all --mode replay` 末行 `EVAL OK`，`regression replay：5/5`、`capability replay：6/6`。
- 工作区必须干净；`p4-spec-prep` 的文档改动已经在 `e41ad72` 合入，不要再带进本分支。

## §0 前置检查与接缝裁决

| # | 检查 | 命令 | 期望 |
|---|---|---|---|
| 1 | 分支与工作区 | `git status -sb` | `## evals-ticket-driver`，没有其他行 |
| 2 | 基点 | `git rev-parse --short dev HEAD` | 两行都是 `e41ad72`；不是就记录实际值再开工 |
| 3 | 旧场景数 | `ls tests/evals/scenarios/*.yaml \| wc -l` | `11` |
| 4 | 场景 schema | `grep -n "class HumanPolicy\|class World\|class Scenario" maa_api/agent/evals/scenario.py` | 3 行 |
| 5 | 驱动表 | `grep -n "DRIVERS" maa_api/agent/evals/runner.py` | 2 行；没有 `"ticket"` |
| 6 | 消息排序 | `grep -n "ORDER BY session_id, seq" maa_api/agent/evals/runner.py` | 1 行 |
| 7 | 回放校验 | `grep -n "_check_tool_ids\|_last_reply_ids" maa_api/agent/evals/cassette.py` | 4 行 |
| 8 | 生产入口 | `grep -n "async def open_ticket\|async def submit_report" maa_api/agent/workers/service.py` | 2 行 |
| 9 | 提问与确认 | `grep -n "async def answer\|async def resolve" maa_api/agent/questions.py maa_api/agent/confirmation.py` | 2 行 |
| 10 | 作业仓储 | `grep -n "class CopilotAttemptRepository" maa_api/db/repositories/copilot.py` | 1 行 |

接缝裁决（实现时按这张表，不再另找入口）：

| 接缝 | 做法 | 不做什么 |
|---|---|---|
| 建单 | `app.state.worker_service.open_ticket(kind, title, brief, scope, origin="user", strength="explicit", source="web", sender="web")` | 不直接写 `ticket` / `agent_session` 行 |
| 确认 | `app.state.confirmation_service.list(status=PENDING)` + `resolve(id, approved=...)` | 不写确认表，不绕过 `_apply_copilot_grant_overrides` |
| 提问 | `app.state.question_service.list(status=PENDING)` + `answer` / `skip` | 不自己投递答案；`answer()` 已含 §8.177 |
| 根运行 | 查该 Worker 会话 `parent_run_id IS NULL` 的最新 `agent_run`，轮询 `status` 到终态 | 不按工单状态判完成 |
| 子代理会话 | `agent_session.parent_session_id = Worker 会话`，运行结束后追加到 `recorder.session_ids` | 不递归收集，不改会话表 |
| 作业站 | `app.state.copilot_service = ScriptedCopilotSearch(world.copilots)` | 不访问真实作业站 |
| 作业尝试 | `app.state.copilot_attempt_service = ScriptedCopilotAttempt(...)`；结果按 `world.copilot_results` 消费 | 不提交真实流水线，不消耗授权 |
| 观察顺序 | `agent_message ... ORDER BY id` | 不改 `messages` 字段形状 |
| 回放校验 | `_issued: dict[str, set[str]]` 按 tool call id 溯源 | 不按通道记「上一条」 |

## Review Focus

- **驱动必须真的建单**：`drive_ticket` 用 `open_ticket`，不是拼一个 `Scenario` 假对象；根运行来自 `agent_run`。T3 用例。
- **确认与提问走真实服务**：批准作业授权会写 `copilot_grant` 行；提问答案走 `QuestionService.answer`，包含投递。T3、T4 用例。
- **子代理会话进观察**：`recorder.session_ids` 含 Worker 与子代理；`collect_observation` 的 `runs` 有子代理运行。T3 用例。
- **多会话回放不误报**：Worker 带 `delegate` 工具结果的请求，在子代理应答之后仍通过 `_check_tool_ids`；从未发出的 id 仍报 `mismatch`。T2 用例。
- **旧 11 个场景不变**：`tests/evals` 基线 51 passed 上升；`test_regression_replay.py` 的 5 个 regression 场景全绿，脚本的 capability 回放 6/6 通过。T5 验收。
- **不改生产**：`git diff --stat` 只能出现 `maa_api/agent/evals/` 与 `tests/evals/` 和文档；`maa_api/agent/workers/`、`maa_api/main.py` 为空。T5 验收。
- **cassette 条数**：ticket 场景是「2 条探查 + 实质调用 + 1 条记忆抽取（仅 ready/failed/dropped）」；`waiting`/`blocked` 不加。T3、T4 用例的 cassette 按这个写。

---

### Task 0: 分支、规格与基线

**Files:**
- Create: `docs/v3/specs/evals-ticket-driver.md`（已存在）、`docs/v3/plans/evals-ticket-driver.md`（本文件）
- 不改代码

**Interfaces:**
- Produces: 开工基点、验收基线、本计划的提交。
- Consumes: `dev` = `e41ad72`。

- [ ] **Step 1: 从 dev 开分支并确认干净**

```bash
git checkout dev
git checkout -b evals-ticket-driver
git status -sb
```

Expected：第一行 `## evals-ticket-driver`，没有其他行；`git rev-parse --short dev HEAD` 两行都是 `e41ad72`（或开工时实际基点，记进台账）。

- [ ] **Step 2: 记录基线**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals > /tmp/eval-base.log 2>&1; echo $?; tail -n 2 /tmp/eval-base.log
.venv/bin/python scripts/agent_eval.py --suite all --mode replay > /tmp/eval-base-replay.log 2>&1; echo $?; tail -n 3 /tmp/eval-base-replay.log
```

Expected：`0`、`51 passed`；`0`、`regression replay：5/5 通过`、`capability replay：6/6 通过`、`EVAL OK`。把两个结果 id 记进台账。

- [ ] **Step 3: 提交 spec 与 plan**

```bash
git add docs/v3/specs/evals-ticket-driver.md docs/v3/plans/evals-ticket-driver.md
git commit -m "docs(evals): ticket 驱动规格与实施计划（维护性补丁，见 docs/v3/13 §8.179）"
```

---

### Task 1: 场景 schema

**Files:**
- Modify: `maa_api/agent/evals/scenario.py`
- Test: `tests/evals/test_ticket_driver.py`（新建，只写解析用例与文件头）

**Interfaces:**
- Produces: `COPILOT_RESULTS`、`CopilotResult`、`HumanPolicy.confirmations` 的 `"pending"`、`SeedPipeline.agent_session_id`、`World.copilots`、`World.copilot_results`、`Scenario.human`。
- Consumes: 既有 `AnswerSpec`、`_MODEL_CONFIG`、`Entry`、`Scenario._cross_field_rules`。

- [ ] **Step 1: 写解析测试（先红）**

`tests/evals/test_ticket_driver.py` 的文件头写 `"""..."""` 与 import（`json`、`Path`、`pytest`、`load_scenario`、`ScenarioError`），然后写四条：

- `test_scenario_ticket_human_world_round_trip`：完整 JSON-in-YAML，断言 `scenario.human.confirmations == "approve"`、`scenario.human.questions.answer == ["仍按完成结束"]`、`world.copilots[0]["id"] == "cp-eval-1-7"`、`world.copilot_results == ["failed", "three_star"]`、`world.seed.pipelines[0].agent_session_id == "eval-seed-session"`。
- `test_copilot_results_require_copilots`：只给 `copilot_results`，`load_scenario` 抛 `ScenarioError`，消息含 `copilot_results` 与 `copilots`。
- `test_copilot_unknown_field_is_rejected`：`copilots[0]` 多一个 `"stars": 3`，消息含 `copilots[0]` 与 `stars`。
- `test_copilot_result_value_is_validated`：`copilot_results=["victory"]`，消息含 `victory`。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k "scenario or copilot_result" > /tmp/eval-t1-red.log 2>&1; echo $?; tail -n 6 /tmp/eval-t1-red.log
```

Expected：非 0；错误含 `Extra inputs are not permitted`（`human`）或 `copilots` 相关字段不存在。

- [ ] **Step 3: 改 `scenario.py`**

按 spec §4.1 加常量、`CopilotResult`、`_COPILOT_FIELDS`；`HumanPolicy.confirmations` 加 `"pending"`；`SeedPipeline` 加 `agent_session_id`；`World` 加 `copilots` / `copilot_results` 与 `field_validator` / `model_validator`；`Scenario` 加 `human: HumanPolicy = Field(default_factory=HumanPolicy)`。`Entry`、`EntryKind`、`_TICKET_FIELDS`、`_cross_field_rules` 不动；把 `COPILOT_RESULTS`、`CopilotResult` 加进 `__all__`。

- [ ] **Step 4: 跑解析测试与旧 schema 测试**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k "scenario or copilot_result" > /tmp/eval-t1.log 2>&1; echo $?; tail -n 5 /tmp/eval-t1.log
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_eval_scenario.py > /tmp/eval-t1-old.log 2>&1; echo $?; tail -n 3 /tmp/eval-t1-old.log
```

Expected：两个 `0`；第一条 4 passed；第二条 6 passed。

- [ ] **Step 5: 提交**

```bash
git add maa_api/agent/evals/scenario.py tests/evals/test_ticket_driver.py
git commit -m "feat(evals): ticket 场景的人工策略与假作业世界（维护性补丁，见 docs/v3/13 §8.179）"
```

---

### Task 2: 多会话回放校验

**Files:**
- Modify: `maa_api/agent/evals/cassette.py`、`maa_api/agent/evals/runner.py`（只改 `run_case` 的错误顺序）
- Test: `tests/evals/test_ticket_driver.py`

**Interfaces:**
- Produces: `CassetteTransport._issued`；`_check_tool_ids` 按 tool call id 溯源；`run_case` 先报 `transport.error` 再 `check_unused`。
- Consumes: `_message_tool_ids`、`_messages`、`_error_response`。

- [ ] **Step 1: 写回放测试（先红）**

在测试文件加：

- `test_cassette_tool_result_from_earlier_reply_passes`：构造 `Cassette`（三条 `CassetteCall`：`call-a`、纯文本、`call-b`），`CassetteTransport` 依次喂三个 `httpx.Request`：第一条请求无 tool；第二条请求无 tool（模拟另一个会话）；第三条请求末尾带 `{"role":"tool","tool_call_id":"call-a"}`。断言 `transport.error is None`。
- `test_cassette_unknown_tool_result_is_mismatch`：第三条请求带 `call-x`，断言 `transport.error is not None` 且 `code == "mismatch"`。

用 `httpx.Request("POST", "https://llm.invalid/v1/chat/completions", json=body)` 造请求；body 用 `{"model": "eval-model", "messages": [...], "tools": []}`。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k cassette > /tmp/eval-t2-red.log 2>&1; echo $?; tail -n 6 /tmp/eval-t2-red.log
```

Expected：非 0；第一条用例报 `mismatch`（`_last_reply_ids` 被中间应答覆盖）。

- [ ] **Step 3: 改 `cassette.py`**

`CassetteTransport.__init__` 加 `self._issued: dict[str, set[str]] = {}`；`_check_tool_ids` 按 spec §4.3 改：请求末尾的 id 集合必须等于这些 id 对应的已发出应答 id 集合；应答的每个 tool call id 都登记到 `_issued`。`_last_reply_ids` 删除（不再有读取点）。

- [ ] **Step 4: 改 `run_case` 的错误顺序**

`maa_api/agent/evals/runner.py` 的 `run_case`（约 747 行）：

```python
if isinstance(transport, CassetteTransport):
    if transport.error is not None:
        raise transport.error
    transport.check_unused()
    warnings.extend(transport.warnings)
```

- [ ] **Step 5: 跑新用例与旧回放**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k cassette > /tmp/eval-t2.log 2>&1; echo $?; tail -n 5 /tmp/eval-t2.log
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_eval_cassette.py > /tmp/eval-t2-old.log 2>&1; echo $?; tail -n 3 /tmp/eval-t2-old.log
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_regression_replay.py > /tmp/eval-t2-replay.log 2>&1; echo $?; tail -n 3 /tmp/eval-t2-replay.log
```

Expected：全 `0`；第二条保持原通过数；第三条 5 个 regression 场景全绿。

- [ ] **Step 6: 提交**

```bash
git add maa_api/agent/evals/cassette.py maa_api/agent/evals/runner.py tests/evals/test_ticket_driver.py
git commit -m "fix(evals): 回放按调用来源校验多会话工具结果（维护性补丁，见 docs/v3/13 §8.179）"
```

---

### Task 3: `drive_ticket`

**Files:**
- Modify: `maa_api/agent/evals/runner.py`
- Test: `tests/evals/test_ticket_driver.py`

**Interfaces:**
- Produces: `TicketNotOpened`、`TicketRunTimeout`、`drive_ticket`、`_apply_human_policy`、`_latest_root_run`、`_child_session_ids`；`DRIVERS["ticket"]`；缺驱动原因 `missing_driver：...`；消息 SQL `ORDER BY id`。
- Consumes: `WorkerService.open_ticket`、`ConfirmationService.list/resolve`、`QuestionService.list/answer/skip`、`AgentRun`、`AgentSession`、`ConfirmationStatus`、`QuestionStatus`。

- [ ] **Step 1: 写驱动测试（先红）**

按 spec §6 写 8 条：`test_ticket_driver_is_registered`、`test_ticket_driver_runs_general_ticket_to_report`、`test_ticket_driver_approves_confirmation`（用带 `medicine: 1` 的 `submit_pipeline`，不依赖 T4 的假作业）、`test_ticket_driver_answers_question`、`test_ticket_driver_includes_subagent_session`、`test_ticket_driver_reports_ticket_not_opened`、`test_ticket_driver_reports_run_timeout`、`test_missing_driver_is_skipped_with_reason`。

测试辅助：

```python
def _write_scenario(root: Path, data: dict) -> Path: ...
def _write_cassette(root: Path, scenario_id: str, calls: list[dict]) -> Path: ...
def _run(scenario_data: dict, calls: list[dict], tmp_path: Path) -> CaseResult: ...
```

cassette 的公共前缀固定两条 `{"text": "OK"}`（落点探查）；`ready` 结束的场景末尾加一条 `{"text": "{\"items\": []}"}`（记忆抽取）；`waiting` 结束的场景不加。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k "ticket_driver or missing_driver" > /tmp/eval-t3-red.log 2>&1; echo $?; tail -n 8 /tmp/eval-t3-red.log
```

Expected：非 0；`KeyError: 'ticket'` 或 `ImportError: cannot import name 'drive_ticket'`。

- [ ] **Step 3: 实现 `drive_ticket`**

按 spec §4.2 加常量、两个异常、四个函数与 `DRIVERS` 注册，并把新名字加进 `__all__`；`_apply_human_policy` 用 `ConfirmationStatus.PENDING`、`QuestionStatus.PENDING`，提问按 `origin["ticket_id"]` 过滤；`answered` 去重。所有 DB 访问走 `app.state.session_factory`。

- [ ] **Step 4: 改消息 SQL 与缺驱动原因**

`collect_observation` 的 SQL 改 `ORDER BY id`（spec §4.6）；`run_case` 缺驱动分支的 `detail.reason` 改成 `"missing_driver：这个入口还没有实现"`（`status` 仍是 `skipped`）。

- [ ] **Step 5: 跑驱动测试**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k "ticket_driver or missing_driver" > /tmp/eval-t3.log 2>&1; echo $?; tail -n 8 /tmp/eval-t3.log
```

Expected：`0`，8 passed。特别确认：超时用例约 1 秒结束，`error` 以 `run_timeout：` 开头；建单失败用例以 `ticket_not_opened：` 开头。

- [ ] **Step 6: 跑旧 runner 用例**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_eval_runner.py > /tmp/eval-t3-runner.log 2>&1; echo $?; tail -n 3 /tmp/eval-t3-runner.log
```

Expected：`0`；`test_unsupported_entry_is_skipped` 仍通过（reason 含「入口」）。

- [ ] **Step 7: 提交**

```bash
git add maa_api/agent/evals/runner.py tests/evals/test_ticket_driver.py
git commit -m "feat(evals): ticket 驱动与跨会话观察顺序（维护性补丁，见 docs/v3/13 §8.179）"
```

---

### Task 4: 假世界装配

**Files:**
- Modify: `tests/evals/environment.py`
- Test: `tests/evals/test_ticket_driver.py`

**Interfaces:**
- Produces: `ScriptedCopilotSearch`、`ScriptedCopilotAttempt`、`_seed_copilots`、`_install_copilot_fakes`；`_seed_world` 支持 `agent_session_id` 与占位会话。
- Consumes: `ResourceAsset`、`CopilotAttemptRepository`、`CopilotGrantService.get_active`、`COUNTED_OUTCOMES`、`is_success`、`TIERS`、`get_settings`。

- [ ] **Step 1: 写假世界测试（先红）**

按 spec §6 写 4 条：`test_copilot_search_uses_world_rows`、`test_copilot_results_drive_attempts`、`test_seed_pipelines_keep_agent_session_id`、`test_seed_creates_placeholder_session`。

- `test_seed_*` 用 `tests/fakes/v3_db.py` 的 `make_session_factory(tmp_path / "seed.db")` 与 `SimpleNamespace(state=SimpleNamespace(session_factory=factory))`，直接 `asyncio.run(_seed_world(app, scenario))`，再查 `Pipeline.agent_session_id` 与 `AgentSession` 行。
- `test_copilot_*` 用 `entry.worker="copilot"` 的 ticket 场景跑 `EvalRunner`；`world.copilots=[{"id":"cp-eval-1-7","title":"1-7 三星作业","stage_id":"main_01-07","stage_code":"1-7"}]`，`copilot_results=["failed","three_star"]`，`human.confirmations="approve"`；cassette 是 2 条探查 + 2 次 `run_copilot_attempt` + `report(ready)` + 记忆抽取。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k "copilot or seed" > /tmp/eval-t4-red.log 2>&1; echo $?; tail -n 8 /tmp/eval-t4-red.log
```

Expected：非 0；`ScriptedCopilotSearch` 不存在，或 `run_copilot_attempt` 走了真实服务。

- [ ] **Step 3: 实现假世界**

按 spec §4.4 加两个类与两个函数；`_open_world` 在 `client.__enter__()` 之后、`yield` 之前按 `world` 字段 seed 与替换 `app.state`。按 spec §4.5 改 `_seed_world`：先补占位 `AgentSession`，再写 `Pipeline(agent_session_id=...)`。

- [ ] **Step 4: 跑假世界测试**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_ticket_driver.py -k "copilot or seed" > /tmp/eval-t4.log 2>&1; echo $?; tail -n 8 /tmp/eval-t4.log
```

Expected：`0`，4 passed；`confirmations.statuses == ["approved"]`；两次尝试的 `outcome` 依次 `failed`、`three_star`。

- [ ] **Step 5: 提交**

```bash
git add tests/evals/environment.py tests/evals/test_ticket_driver.py
git commit -m "feat(evals): 假作业站、假尝试与 seed 会话（维护性补丁，见 docs/v3/13 §8.179）"
```

---

### Task 5: 文档、全量验证与台账

**Files:**
- Modify: `docs/v3/10-可观测与评测.md`、`docs/v3/13-决策记录.md`
- Create: `docs/v3/ledgers/evals-ticket-driver.md`

**Interfaces:**
- Produces: 10 §6.4/§6.5/§6.6/§6.9 的字段与行为说明；13 §8.179；台账。
- Consumes: 所有代码改动。

- [ ] **Step 1: 同步 10**

按 spec §9 改：`entry.kind` 的 `ticket` 行、场景字段表、`human` 与 `world.copilots`/`world.copilot_results`/`seed.agent_session_id`、§6.5 的多会话校验说明、§6.6 的 ticket 假世界、§6.9 的入口迁移句。

- [ ] **Step 2: 同步 13**

在 §8.178 之后加 `### 8.179 评测 ticket 驱动（维护性补丁，2026-11-09）`，写用户口径、实际、R1–R12、spec/plan/ledger 链接、验证方式。编号由主 agent 确认。

- [ ] **Step 3: 写台账**

`docs/v3/ledgers/evals-ticket-driver.md` 按 [specs/README §4](../specs/README.md) 模板：分支、基点、合并、tag（无）、spec/plan 链接、T0–T5 表格、Rulings（spec §4.8）、验收表、备注（不改生产、假尝试不消耗授权、记忆抽取应答规则）。

- [ ] **Step 4: 全量验证**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals > /tmp/eval-t5-all.log 2>&1; echo $?; tail -n 5 /tmp/eval-t5-all.log
.venv/bin/python scripts/agent_eval.py --suite all --mode replay > /tmp/eval-t5-replay.log 2>&1; echo $?; tail -n 4 /tmp/eval-t5-replay.log
.venv/bin/python scripts/dump_openapi.py --check > /tmp/eval-t5-openapi.log 2>&1; echo $?; tail -n 2 /tmp/eval-t5-openapi.log
.venv/bin/python scripts/check_doc_links.py | tail -n 1
.venv/bin/python -m pytest -o addopts= -q > /tmp/eval-t5-full.log 2>&1; echo $?; tail -n 5 /tmp/eval-t5-full.log
git diff --stat dev...HEAD
```

Expected：`tests/evals` `0`、passed ≥ 69；回放 `0`、`5/5`、`6/6`、`EVAL OK`；OpenAPI `0`；`BROKEN 0`；全量只有既有 4 条已知失败（3 条真资源 + 1 条时间敏感），没有新增；`git diff --stat` 只出现 `maa_api/agent/evals/`、`tests/evals/`、`docs/v3/`。

- [ ] **Step 5: 提交、合并、补台账**

```bash
git add docs/v3/10-可观测与评测.md docs/v3/13-决策记录.md docs/v3/ledgers/evals-ticket-driver.md
git commit -m "docs: 评测 ticket 驱动文档与台账（维护性补丁，见 docs/v3/13 §8.179）"
git checkout dev
git merge --no-ff evals-ticket-driver
git branch -d evals-ticket-driver
```

合并后把 merge commit 与最终验证结果补进台账，再提交到 `dev`。

---

## Self-Review

- 覆盖规格：§4.1（T1）、§4.2（T3）、§4.3（T2）、§4.4（T4）、§4.5（T4）、§4.6（T3）、§4.7（T3）、§4.8 R1–R12（T1–T5 各自落地，台账集中记录）。
- 关键风险都有用例：跨会话回放（T2）、确认批准与作业结果（T4）、提问作答（T3）、子代理会话（T3）、超时与建单失败（T3）、旧 11 场景（T5）。
- 不改生产、不加新契约：T5 的 `git diff --stat` 与 `dump_openapi.py --check` 钉住。
- 记忆抽取应答：所有 `ready`/`failed`/`dropped` 的测试场景末尾都有一条 `{"text": "{\"items\": []}"}`；`waiting`/`blocked` 的场景没有。
