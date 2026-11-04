# M68 Trace 与评测底座（台账）

- 分支：`trace-evals`；基点：`dev` = `dd04721`；合并：`dev` 上的 `merge: Trace 与评测底座（M68，见 docs/v3/13 §8.26–8.27）`；tag：`v3-m68`
- spec：[m68-trace-evals](../specs/m68-trace-evals.md)
- 计划：[m68-trace-evals](../plans/m68-trace-evals.md)
- 决策记录：[docs/v3/13 §8.26–8.27](../13-决策记录.md)
- 来源：继续按 goal 推进 v3。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `74a4af9` | 计划入库；§0 的 18 条检查 |
| T1–T2 | `5c5d087` | `SpanKind` 枚举、`trace_span` 表与迁移 `0025_trace_spans`、`TraceSpanRepository`；`TraceSettings` 四键（两项 config-only）、`_FIELDS`、模板与测试 |
| G2（T3–T4） | `d30045c` | `maa_api/agent/trace/spans.py`（同步 record、后台批量写、失败重试、队列满、close、stats、非 UUID 拒绝、disabled noop）、`main.py` 装配；`TraceMiddleware` 真实现（`agent.run`/`llm.chat`/`tool.execute`/`delegate.subagent`/`confirmation.wait`，无正文与工具参数）；`ModelResponse.model/response_id` |
| G3（T5–T6） | `912bdd7` | `GET /api/agent/runs/{run_id}/spans`（整条 trace、`truncated`、子运行亦返整条）；保留策略第 7 步与 `trace_spans_deleted`；`docs/v3/14` 134 条 |
| G4（T7） | `ce83d38` | `otlp` extra（poetry optional + extras、锁文件、`Dockerfile` 的 `MAA_EXTRAS`）、`trace/otlp.py`、导出队列、16 §12 |
| 附带 | `b12c3c2` | 已知 flake：既有时序用例的等待窗口 2s→5s（13 §8.26） |
| G5（T8–T9） | `6d2f754` | 评测 cassette（replay/record/live 传输层、占位符、指纹、UUID 模板化）、场景 schema（ruamel、文件路径错误）、判分器（运算符、工具序列、outcome 视图、text_contains、llm_judge） |
| T10 | `a7eac40` | 假内核/假设备、`FakeWorldEnvironment`、`runner.py`（replay/record/live、超时、结果落盘）、`scripts/agent_eval.py`；6 条 runner 端到端用例 |
| T11 | `408412a` | 12 个初始场景（6 regression + 6 capability）与手写录制；`test_regression_replay.py`；两个套件的 replay 基线 |
| 附带 | `2904a79` | 评测环境在 `finally` 恢复全局 settings，修掉对后续服务的污染 |
| T12–T13 | `2c8cced` | 前端「运行追踪」（列表、详情树、瀑布、抽屉、3 秒轮询）与「成本」（范围/分组/缓存命中率/预算卡片）两页；vitest 用例 |
| T14 | 本台账所在提交 | 02/04/05/16 契约同步；13 §8.26–8.27；README 状态行；全量验收与合并 |

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| T1–T2 | `tests/db + test_enums` = `387 passed`；`test_settings + test_vapid_settings` = `38 passed`；`tests/api` 同批 = `534 passed` |
| G2 | `tests/agent/trace` = `18 passed`；`tests/agent` = `893 passed, 1 skipped, 2 xfailed`；parity `17 passed`；装配 `2 passed` |
| G3 | spans 路由 + 路由清单 = `12 passed`；retention + copilots = `49 passed`；`dump_openapi --check` 0；`pnpm gen:api:check` 0 |
| G4 | `import opentelemetry.exporter.otlp.proto.http.trace_exporter` → ok；`tests/agent/trace` = `24 passed`；`tests/agent` = `899 passed`；parity 17；`poetry.lock` 只新增 otlp 依赖树（无降级） |
| G5 | `tests/evals` = `39 passed` |
| T10 | `tests/evals` = `45 passed`（含 6 条 runner 端到端） |
| T11 | regression 6/6、capability 6/6，两套件均 `EVAL OK`；`--list` 退出 0 |
| T12–T13 | 前端五步 0；vitest `78 files / 791 tests passed`（新增 13 条）；`frontend_smoke.py` = `SMOKE OK` |

### replay 基线（M68「基线」一节）

| 套件 | 模式 | 通过率 | 结果目录 id |
|---|---|---|---|
| regression | replay | 6/6（100.0%） | `20261104-005453-regression-replay-5fc8` |
| capability | replay | 6/6（100.0%） | `20261104-005507-capability-replay-96fa` |

结果写在 `resource/evals/<id>/result.json`（不进版本库）；`resource/evals/index.jsonl` 追加索引。

### 最终验收（T14，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | `3 failed, 4208 passed, 5 skipped, 2 xfailed`；3 条失败与基线同一组（真资源用例），无新增失败 |
| `scripts/dump_openapi.py --check` | 退出码 0 |
| `scripts/generate_api_guide.py --check` | 退出码 0 |
| 前端五步（install / gen:api:check / typecheck / test / build） | 退出码 0；vitest 78 文件 791 用例全过，build 正常 |
| `scripts/frontend_smoke.py` / `scripts/agent_smoke.py` | 均 `SMOKE OK` |
| `scripts/agent_eval.py --suite regression --mode replay` | 退出码 0，末行 `EVAL OK` |
| `scripts/check_doc_links.py` | `BROKEN 0` |
| `git diff --check` | 退出码 0 |
| 手动验收（浏览器两页、Phoenix 导出、真实密钥 live 评测） | **未验证**（本会话无浏览器与真实密钥）；对应逻辑由单测、冒烟与 replay 基线覆盖 |

### 计数

- 业务表 31 → **32**（`trace_span`）；HTTP 端点 133 → **134**；配置键 +4（config-only 11 → 13）；错误码、工具数（84/83）、WebSocket 频道（14）、OpenAPI tag（19）不变；迁移 head `0024_model_profiles` → **`0025_trace_spans`**。
- 新增测试文件：`tests/db/test_trace_spans_migration.py`、`tests/db/test_repositories_trace.py`、`tests/agent/trace/{test_trace_spans,test_trace_middleware,test_trace_otlp}.py`、`tests/api/test_run_spans_router.py`、`tests/evals/{test_eval_cassette,test_eval_scenario,test_eval_graders,test_eval_runner,test_regression_replay}.py`；前端 `features/runs/*`、`features/costs/*`。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 实现方式：T1–T9、T12–T13 用子代理，T10/T11 由主 agent 原生实现 | M68 后半段子代理基础设施连续无产出失败（一个收尾中断、两个启动即失败）；G4 留下的未提交工作由主 agent 逐项复验后补提交，T10/T11 直接原生完成（记录在案，不影响产物） |
| R2 | 评测环境在 `finally` 恢复 `settings_module.set_settings(old_settings)` | 首轮全量测试发现 eval 用例把全局 settings 留在临时配置上，污染 `item_local_fallback`/`supervisor` 等后续用例（7 failed 中的 4 条）；修复后组合跑恢复全绿 |
| R3 | 初始场景集按附录 A 落地，两处设计按 13 §8.27 调整 | seed schema 只允许终态流水线（B6 改用 stuck 脚本）；B2 的批中中断改 `tool_start` 触发以保证稳定 |
| R4 | 已知 flake 放宽等待窗口（13 §8.26） | 测试夹具时序余量问题，2 秒在整包负载下过紧；断言不变 |
| R5 | replay 下没有录制的评审调用直接 `skipped`（不失败） | 与 spec §4.5.6 一致；capability 套件在 replay 下 6/6 通过，但评审分是跳过的 |
| R6 | record 用例用桩传输层模拟上游 | 避免测试联网；真实 record/live 链路未验证（见遗留） |
| R7 | 场景的 `tool_sequence`/`outcome_state` 断言避开与运算符同名的单键（`last`/`all` 等） | `match_value` 把单键映射当运算符；多键映射按字面匹配，断言改用 `{"count": N, "last": {...}}` 的形态 |

## 偏离（已记 13 §8.26–8.27）

- §8.26 既有时序用例的等待窗口放宽（2s→5s）。
- §8.27 初始场景集的两处设计调整（B6 seed→stuck 脚本；B2 中断点改 `tool_start`）。

## 遗留

- spec §7 的手动验收**未验证**：浏览器里「运行追踪」与「成本」两页、Phoenix 导出、真实密钥 live 评测；对应逻辑由单测、冒烟与 replay 基线覆盖。
- OTLP 导出只验证了本进程内 `InMemorySpanExporter` 的 ReadableSpan 形状，未接真实 Langfuse/Phoenix。
- capability 套件的 `llm_judge` 在 replay 下跳过；真实评审质量要等 live 跑过才算验证。
- record 模式只测了「通过才存、失败不存」的桩逻辑，真实录制未验证。
- `legacy_session` 入口依赖 `AgentRuntime.stream_message`（M72 删除）；M72/M73 的 spec 需要把附录 A 的场景迁到 `ticket` 入口，否则 regression 闸门会失效（spec §8 已记）。
