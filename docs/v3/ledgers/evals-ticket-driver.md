# 评测 ticket 驱动（维护性补丁台账）

- 分支：`evals-ticket-driver`（待主 agent 合并后删除）；基点：`dev` = `e41ad72`（M86–M90 升格已合入）；合并：待主 agent 合并；tag：无（维护性补丁，不新增里程碑）
- spec：[evals-ticket-driver](../specs/evals-ticket-driver.md)
- 计划：[evals-ticket-driver](../plans/evals-ticket-driver.md)
- 决策记录：[docs/v3/13 §8.179](../13-决策记录.md)
- 用户口径（2026-11-09）：先落地评测设施补丁，在 M86 开工前完成；只改评测设施，不改生产行为。M86 的导航场景与 M87 的验证闸门场景都要从「用户建一张工单」开始，还要模拟用户批准确认卡、回答 `ask_user` 提问、提供作业站作业与按顺序的作业结果。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `b717e42`、`810a22b` | 规格与实施计划入库；`810a22b` 补 spec §4.8 的 R1–R12 与计划的接缝裁决。基线（2026-11-09）：`tests/evals` 51 passed（53.79s）；`agent_eval --suite all --mode replay` 末行 `EVAL OK`，`regression 5/5`、`capability 6/6` |
| T1 | `6395635` | 场景 schema：`HumanPolicy.confirmations` 加 `pending`、`SeedPipeline.agent_session_id`、`World.copilots`/`copilot_results`（含 `_known_copilot_results` 等三个校验器）、`Scenario.human`、`COPILOT_RESULTS`/`CopilotResult`；4 条解析用例 |
| T2 | `7e25944` | 多会话回放校验：`CassetteTransport._issued` 按 tool call id 溯源，`run_case` 先报 `transport.error` 再查 `check_unused`；2 条 cassette 用例 |
| T3 | `46b9f79`、`4dd24d8`、`b9e6c77` | `drive_ticket`、`TicketNotOpened`/`TicketRunTimeout`、`_apply_human_policy`/`_latest_root_run`/`_child_session_ids`、`DRIVERS["ticket"]`、消息 SQL `ORDER BY id`、缺驱动原因 `missing_driver：`；8 条驱动用例。`46b9f79` 给 M86/M87 spec 补 ticket cassette 的记忆抽取口径；`b9e6c77` 把 spec §4.2 的确认列表键改成实际 wire 的 `confirmation_id` |
| T4 | `a669f59` | 假世界：`ScriptedCopilotSearch`、`ScriptedCopilotAttempt`、`_seed_copilots`、`_install_copilot_fakes`、`_open_world` 装配；`_seed_world` 支持 `agent_session_id` 并补占位会话；4 条假世界用例。`tests/evals` 基线 51 → 69 passed |
| T5 | 本台账所在提交 | 文档 10 §6.4/§6.5/§6.6/§6.9、13 §8.179、本台账；全量验证 |

## Rulings I made

### 规格裁决（spec §4.8 R1–R12）

| # | 裁决 | 理由 |
|---|---|---|
| R1 | `HumanPolicy.confirmations` 保留默认 `reject`，新增 `pending` | 改默认会让没写 `human` 的旧场景从拒绝变批准；M86/M87 都显式写 `approve` |
| R2 | `HumanPolicy.questions` 复用 `AnswerSpec`，不新增 `dict[str, list[str]]` | M87 已写 `{"answer": ["仍按完成结束"]}`；两套形状会分叉语义 |
| R3 | `Entry` 与 ticket 四字段校验沿用 M68，不改，只补测试 | M68 已实现；改它会扩大本补丁范围 |
| R4 | `Scenario.human` 与 `turns[].human` 并存：ticket 驱动读前者，`legacy_session` 读后者 | 旧场景的 `turns[].human` 不动，ticket 场景不写 `turns` |
| R5 | `World.copilots` 保持 `list[dict]`，用校验器拒绝未知键并校验 `id`/`stage_id` | 保持 M87 已写定的 YAML 形状，同时未知字段报错 |
| R6 | 假作业尝试不模拟授权消耗，`remaining=None` | 评测要结果队列与确认链路；授权次数与轮次上限由 M87 单元测试覆盖 |
| R7 | `ready`/`failed`/`dropped` 的 ticket 场景末尾加一条记忆抽取应答 `{"text": "{\"items\": []}"}`；`settings.memory.enabled=false` 时不加，`waiting`/`blocked` 不加 | 不加会 `exhausted`，加错会 `unused`；`waiting`/`blocked` 不触发结束监听 |
| R8 | 工单结束转发总控的回复轮不等待、不准备 cassette 应答 | 它在 `check_unused` 之后启动，等待会引入不确定的额外模型调用 |
| R9 | cassette 工具结果校验改成按 tool call id 溯源，且先报 `transport.error` 再查 `check_unused` | 不修无法回放 M86/M87 的 `delegate` 场景；旧单会话语义不变，`unused` 不再掩盖 `mismatch` |
| R10 | seed 流水线的 `agent_session_id` 先补 `kind=worker` 占位会话 | 外键开着，不补 seed 直接失败；占位会话只服务 seed |
| R11 | 提问直接调 `QuestionService.answer`/`skip`，不额外封装 | `answer()` 已含 §8.177 的 `_deliver_if_orphaned`，再包一层会重复投递 |
| R12 | 缺驱动仍是 `skipped`，原因改成 `missing_driver：这个入口还没有实现` | 不改变 M68 的既有口径；M87 T7 用 pytest 断言每个场景都有驱动 |

### 实现补充（T1–T5 落地时）

| # | 裁决 | 理由 | 代价 |
|---|---|---|---|
| S1 | 确认列表的实际键是 `confirmation_id`，不是 `id`；spec §4.2 已同步改正 | `ConfirmationService.list` 的 wire 就是这个名字 | 无 |
| S2 | `run_case` 对 `TicketNotOpened`/`TicketRunTimeout` 原样透传 `str(exc)`；其它异常仍是 `类名：消息` | §4.7 的失败文本是稳定口径，不叠加类名 | 无 |
| S3 | 超时用例总耗时约 4 秒：`open_ticket` 首轮探查含 1 秒 sleep，驱动自身期限 `max(timeout_seconds - 2, 1)` 不变 | 驱动先于外层超时给出带工单 id 与最后运行状态的错误 | 用例比计划预估慢约 3 秒 |
| S4 | `World` 增加 `_known_copilot_results`（`mode="before"` 校验器） | 非法 `copilot_results` 的报错消息要含非法值；`COPILOT_RESULTS` 与 `CopilotResult` Literal 按 spec §4.1 保留 | 多一个校验器 |
| S5 | 测试模块级改 `DRIVERS` 用 `try/finally` 恢复 | 捕获驱动写入的会话 id；不改生产代码 | xdist 并行下不安全，是已知限制（评测测试当前不用 xdist） |
| S6 | `maa_api/agent/evals/__init__.py` 不导出 `drive_ticket`、`TicketNotOpened`、`TicketRunTimeout` | 它们是评测内部设施，由 `runner.py` 的 `__all__` 公开；主 agent 裁定 | 外部只依赖 `runner` 或测试直接 import |
| S7 | ticket cassette 前两条固定探查 `{"text": "OK"}`；`ready`/`failed`/`dropped` 末尾再加一条记忆抽取；每少/多一条分别报 `exhausted`/`unused` | 落点探查与记忆抽取都是真实模型调用，必须占位 | 场景作者要按顺序写固定条数 |
| S8 | 假尝试不消耗真实授权、不提交真实流水线，`remaining` 恒为 `None`；`record` 只接受 `RECORDABLE_OUTCOMES` 且只改判 `unknown` 的尝试 | 评测只看结果队列与确认链路；真实授权与轮次上限由 M87 单元测试覆盖 | 与生产语义有差距，已在 spec §8 记录 |
| S9 | seed 的 `agent_session_id` 先插占位 `AgentSession`（`kind=worker`、标题「评测种子会话」），重复 seed 不新增第二行 | 外键开着；占位会话表达「某个 Worker 会话提交的流水线」 | 按「所有 worker 会话都有工单」断言的测试会看到这一条，标题可排除 |

## 验收

| 检查 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -o addopts= -q tests/evals` | 0；**69 passed**，1 warning，86.90s（基线 51 → 本补丁 +18） |
| `.venv/bin/python scripts/agent_eval.py --suite all --mode replay` | 0；`regression replay：5/5 通过（100.0%）`、`capability replay：6/6 通过（100.0%）`、末行 `EVAL OK`；结果 `20261109-103638-regression-replay-c58f`、`20261109-103657-capability-replay-3488` |
| `.venv/bin/python scripts/dump_openapi.py --check` | 0；`OpenAPI snapshot is current: web/openapi.json` |
| `.venv/bin/python scripts/check_doc_links.py` | 末行 `BROKEN 0` |
| `.venv/bin/python -m pytest -o addopts= -q` | 1；**4 failed / 5260 passed / 5 skipped / 2 xfailed**，624.69s；4 条全是既有失败，无新增 |
| `git diff --stat dev...HEAD` | 只含 `maa_api/agent/evals/`、`tests/evals/`、`docs/v3/`；生产代码、迁移、配置、前端零改动 |

全量 pytest（本分支 T5 提交前实测）：`4 failed, 5260 passed, 5 skipped, 2 xfailed, 24 warnings in 624.69s`。4 条失败与既有基线一致，没有新增失败：

- `tests/agent/test_incidents.py::test_incident_allowed_action_still_uses_confirmation_service`（时间敏感）
- `tests/services/test_activity_index.py::test_real_resource_pack_reports_the_current_activity_and_its_stages`（真资源）
- `tests/services/test_activity_index.py::test_real_resource_pack_disambiguates_a_repeated_code`（真资源）
- `tests/services/test_stage_layout.py::test_ambiguous_code_picks_the_first_stage_id_and_counts_candidates`（真资源）

基线参考：`docs/v3/ledgers/remove-spend-legacy-sessions.md` 记 3 条真资源失败，`questions-no-expiry.md` 记第 4 条时间敏感用例；本分支全量结果与这两份台账一致。

## 已知失败（非本补丁引入）

- `tests/services/test_activity_index.py` 2 条 + `tests/services/test_stage_layout.py` 1 条：依赖 `resource/` 真资源数据，m63 起的既有基线。
- `tests/agent/test_incidents.py::test_incident_allowed_action_still_uses_confirmation_service`：既有时间敏感用例（等 `confirm_request` 事件最多 1 秒），全量负载下偶发超时；见 [questions-no-expiry 台账](./questions-no-expiry.md)。

## 偏离（已记 13 §8.179）

- 实现补充裁决 S1–S9 是本补丁相对 spec 的全部偏离，已记 [13 §8.179](../13-决策记录.md)；spec 本身已同步确认列表键 `confirmation_id` 与 M86/M87 的 cassette 记忆抽取口径。

## 遗留

- `tests/evals/test_ticket_driver.py` 模块级改 `DRIVERS` 后用 `try/finally` 恢复，xdist 并行下不安全；当前 `tests/evals` 不用 xdist，后续并行跑要先改测试设施。
- `ScriptedCopilotAttempt.record` 没有 spec §6 的独立用例；`run` 的 `success` 参数不参与判定（照 spec §4.4 伪代码）。M87 若要用 `record_copilot_result`，需要在 M87 自己的场景里覆盖。
- 假尝试不消耗授权、不提交真实流水线，是评测设施与生产语义的已知差距；真实授权次数、轮次上限与闸门由 M87 单元测试覆盖。

## 备注

- 本补丁只改评测设施：`maa_api/agent/evals/`、`tests/evals/`、`docs/v3/`；不新增端点、错误码、表、迁移、配置键、工具、事件或依赖。计数（工具、端点、错误码、表、配置键、迁移 head、评测场景数）全部不变，旧 11 个场景与 11 个 cassette 未改。
- 假尝试不消耗真实授权、不提交真实流水线，`remaining` 恒为 `None`；授权次数、轮次上限与闸门由 M87 的单元测试覆盖。
- 记忆抽取应答：ticket cassette 前两条固定探查 `{"text": "OK"}`；以 `ready`/`failed`/`dropped` 结束的场景末尾再加一条 `{"text": "{\"items\": []}"}`；`waiting`/`blocked` 不加。每少/多一条分别报 `exhausted`/`unused`。
- `drive_ticket` 的超时用例约 4 秒：`open_ticket` 首轮探查含 1 秒 sleep；驱动自身期限比场景上限早 2 秒（最少 1 秒）。
- 分支提交到 `a669f59` 为止；T5 文档与台账提交后由主 agent 独立复核、合并与删除分支，合并提交与最终验证结果由主 agent 补记。
