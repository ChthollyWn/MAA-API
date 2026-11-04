# M68 Trace 与评测底座 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地运行追踪（`trace_span` 表 + `SpanRecorder` 后台批量写 + `TraceMiddleware` 生成 `agent.run`/`llm.chat`/`tool.execute`/`delegate.subagent`/`confirmation.wait` 树 + `GET /api/agent/runs/{run_id}/spans` + OTLP 导出 extra + 保留清理）、评测底座（`scripts/agent_eval.py`、cassette 录制回放、场景 schema 与判分器、假世界、12 个初始场景与 replay 基线）、前端「运行追踪」与「成本」两页。

**Architecture:** G1 表/枚举/迁移/仓储 + 设置项 → G2 `SpanRecorder` 与装配、`TraceMiddleware` 与模型响应字段 → G3 追踪端点 + 保留策略 → G4 OTLP extra 与导出 → G5 cassette 传输层 + 场景 schema/判分器 → G6 假世界、runner、脚本、12 个场景与基线 → G7 前端两页 → T14 文档、全量验收与合并。

**Tech Stack:** SQLModel + Alembic（0025）、asyncio 批量写、OpenTelemetry SDK（`otlp` extra）、pydantic（场景 schema）、httpx 传输层 monkeypatch、React 19 / TanStack Query / vitest。

**Spec:** [m68-trace-evals](../specs/m68-trace-evals.md)（890 行；每个任务照 spec 对应小节做）

## 开工基线（2026-11-03，§0 已跑）

- 分支 `trace-evals`，基点 `dev` = `dd04721`（M67 合并），工作区干净；tag `v3-m63`…`v3-m67`。
- 迁移 head `0024_model_profiles`；`TraceMiddleware` 空实现已在 `harness/builtin_middleware.py`；六个核心类型在 `harness/context.py`；门面已走 `AgentLoop`；`spec_version` 已在定义 schema；`RunService` 与两条运行端点就位；`agent_run` 表就位。
- `client_factory_provider` 在 `llm/roles.py`（3 处）与 `main.py`（2 处）；模型用量/成本/档案（M67）就位；`/api/agent/costs` 已在 OpenAPI。
- 本里程碑的名字都不存在；v2 的 `capability_probe`/`tool_start`/`confirmation_required`/`question_required` 事件名都在（13 处）。
- `pyproject.toml` 是 **`[tool.poetry]`** 形态 → T7 用 `optional = true` + `[tool.poetry.extras]`；`poetry` 在 PATH；`docs/v3/16` §12 可选依赖约定已在。
- 后端基线：`3 failed, 4043 passed, 5 skipped, 2 xfailed`（另加 M67 的 71 条新用例；以 §0.18 实测为准，之后不新增失败）。
- 已知 flake：整包负载下 `tests/agent/test_incidents.py::test_incident_allowed_action_still_uses_confirmation_service` 偶发 0.25s 轮询超时（单跑通过，与 M68 无关）。

## Global Constraints

- **v2 行为不变**：M65 golden parity 继续 `17 passed`；v2 的 SSE 事件名/字段/顺序、`tests/agent/test_*.py`、`tests/api/test_agent_router.py` 断言不改。
- **追踪不阻塞运行**：`SpanRecorder.record` 是同步调用、不 await 写库；写库失败只计数重试；队列满丢弃并计数。
- **不记录内容**：span 属性不带消息正文、推理内容、工具参数与结果；`agent_eval` 的结果只进 `resource/evals/`（不进版本库）。
- **评测确定性**：replay 模式不联网、不起服务、不跑真实 LLM；初始 12 个场景的 regression 套件必须全过；`agent_eval.py` 末行 `EVAL OK`。
- **新依赖只有一个 extra**：`otlp` 的 `opentelemetry-sdk` 与 `opentelemetry-exporter-otlp-proto-http`，锁文件按 16 §12 重建；除 `test_exporter_sends_readable_spans_with_ids` 外不 importorskip。
- **计数**：业务表 31 → 32（`trace_span`）；HTTP 端点 133 → 134；配置键 +4（config-only +2）；错误码、工具数、WS 频道不变。
- 不 push；提交 `<类型>(m68): <中文摘要>`；最后合并回 `dev` 打 `v3-m68` tag。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，命令行不加 `-q`；长输出重定向 `/tmp/m68/`。

## Review Focus

1. **Span 树与成本一致性**：`agent.run` 为根，`llm.chat` 的 `cost_micros` 之和 = `agent_run.cost_micros` = `cost_ledger` 之和；子运行的父是委派的 `tool.execute` span；跨 trace 的父被忽略。
2. **批量写与关闭**：`SpanRecorder` 的 flush 批量、失败重试、队列满丢弃、`close()` 冲刷、非 UUID trace id 拒绝；`api/app` 装配顺序（span_recorder 在 M67 四件之后）。
3. **评测链**：cassette 回放逐条校验（tool_call_id、占位符、耗尽/未用、指纹漂移）；scenario 校验错误带文件路径；grader 每个运算符；runner 的 replay/record/live 与结果落盘。
4. **OTLP extra 可装可退**：不配 `trace.otlp_endpoint` 或没装 extra 时导出禁用（警告一次），全量测试照过；装 extra 后 `InMemorySpanExporter` 用例能跑。
5. **前端两页**：运行追踪树 + 瀑布 + 轮询停止 + span 抽屉；成本页四分组、缓存命中率、预算告警与不限；界面不出现代码名。

---

### Task 0: 分支、§0 前置检查与计划入库（已完成）

- [x] 建分支 `trace-evals`，跑 §0 的 18 条检查。
- [x] 提交本计划：`docs(m68): M68 Trace 与评测底座实施计划`。

### Task 1: 表、枚举、迁移与仓储 + 设置项（spec T1、T2）

**Files:** `maa_api/domain/enums.py`、`maa_api/db/models.py`、新建 `maa_api/db/migrations/versions/0025_trace_spans.py`、新建 `maa_api/db/repositories/trace.py`、`tests/db/test_models.py`、`tests/domain/test_enums.py`、新建 `tests/db/test_trace_spans_migration.py`、`tests/db/test_repositories_trace.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`tests/test_vapid_settings.py`

- [ ] 按 §4.1（`SpanKind` 枚举、`TraceSpan` 表与三索引、CASCADE 外键、autoincrement）写模型与迁移 0025；按 §4.2.5 写 `TraceRepository`（`list_by_trace` 排序/limit、`purge_before` 分批 commit）。
- [ ] 按 §4.4 加 4 个配置键（其中 2 个 config-only）、`_FIELDS`、模板与测试同步。
- [ ] 验证：`pytest tests/db tests/domain/test_enums.py tests/test_settings.py tests/test_vapid_settings.py` → 0。
- [ ] 提交：`feat(m68): trace_span 表、迁移与追踪设置项`

### Task 2: SpanRecorder 与装配 + TraceMiddleware（spec T3、T4）

**Files:** 新建 `maa_api/agent/trace/{__init__,spans}.py`、`maa_api/main.py`、`maa_api/agent/harness/builtin_middleware.py`、`maa_api/agent/harness/context.py`、`maa_api/agent/harness/chunks.py`、新建 `tests/agent/trace/test_trace_spans.py`、`test_trace_middleware.py`

- [ ] 按 §4.2.1/§4.5.1 写 `SpanRecorder`（同步 record、后台批量 flush、失败重试、队列满、close、stats、非 UUID 拒绝、disabled noop）；按 §4.2.5 装配到 `app.state.span_recorder`（`exporter=None`，T7 接）。
- [ ] 按 §4.2.2/§4.2.3 前两条把 `TraceMiddleware` 实现成真：`agent.run` 根 span、`llm.chat`、`tool.execute`、`delegate.subagent`、`confirmation.wait`（从审计派生）；`ModelResponse` 加耗时/用量字段（`chunks.py` 填）；属性白名单，不带正文与参数。
- [ ] 验证：`pytest tests/agent` 与基线比不新增失败；parity `17 passed`；`test_trace_middleware.py` 的树形/成本一致/legacy chat 用例全过。
- [ ] 提交：`feat(m68): span 记录器与追踪中间件`

### Task 3: 追踪端点与保留策略（spec T5、T6）

**Files:** `maa_api/api/routers/runs.py`、新建 `tests/api/test_run_spans_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14-现行接口清单.md`、`maa_api/services/retention_service.py`、`tests/services/test_retention_service.py`

- [ ] `GET /api/agent/runs/{run_id}/spans` 返回整条 trace（子运行 id 也返回整条；`truncated`；未知 404 `RUN_NOT_FOUND`；鉴权）；重生成快照与 `api.d.ts`；`docs/v3/14` §6 agent 小节 +1 行与两处计数。
- [ ] 保留策略加 trace_span 清理（`trace.retention_days`，新键 `trace_spans_deleted`，既有键语义不变）。
- [ ] 验证：`pytest tests/api/test_run_spans_router.py tests/domain/test_route_inventory.py tests/services/test_retention_service.py tests/services/test_retention_copilots.py` → 0；`dump_openapi --check` 0；`pnpm gen:api:check` 0。
- [ ] 提交：`feat(m68): 追踪端点与保留清理`

### Task 4: OTLP 导出与 extra（spec T7）

**Files:** 新建 `maa_api/agent/trace/otlp.py`、`maa_api/agent/trace/spans.py`（导出队列）、`maa_api/main.py`（`build_exporter`）、`pyproject.toml`、`poetry.lock`、`Dockerfile`、`docs/v3/16-部署指南.md` §12、新建 `tests/agent/trace/test_trace_otlp.py`

- [ ] 按 16 §12 的 poetry extra 约定加依赖并重建锁文件，装进 `.venv`；`Dockerfile` 加 `ARG MAA_EXTRAS` 与安装分支；16 §12 补用法。
- [ ] 按 §4.2.4 写 `otlp.py`（`parse_headers`、GenAI 命名、Langfuse/Phoenix 属性、缺 extra 只警告），接进 `SpanRecorder` 的导出队列。
- [ ] 验证：`python -c "import opentelemetry.exporter.otlp.proto.http.trace_exporter"` → 0；`pytest tests/agent/trace` → 0；全量测试照过。**OTLP 依赖装不上或锁文件重建失败：停下来报告，其余任务不阻塞**。
- [ ] 提交：`feat(m68): OTLP 导出与 otlp extra`

### Task 5: 录制文件与传输层 + 场景 schema 与判分器（spec T8、T9）

**Files:** 新建 `maa_api/agent/evals/{__init__,cassette,scenario,graders}.py`、新建 `tests/evals/{__init__,test_eval_cassette,test_eval_scenario,test_eval_graders}.py`

- [ ] 按 §4.2.6/§4.5.2/§4.5.3 写 cassette（`expand_reply`、占位符、回放检查、指纹、录制模板化、`CassetteTransport`、max_calls）。
- [ ] 按 §4.2.6 写 scenario schema（pydantic、校验错误带文件路径）与 graders（运算符、序列、forbid、计数、outcome 视图、text_contains、llm_judge、case 状态规则）。
- [ ] 验证：`pytest tests/evals` → 0。
- [ ] 提交：`feat(m68): 评测 cassette、场景 schema 与判分器`

### Task 6: 假世界、runner、脚本与 12 个场景（spec T10、T11）

**Files:** 新建 `tests/fakes/fake_core.py`、`tests/evals/environment.py`、`maa_api/agent/evals/runner.py`、`scripts/agent_eval.py`、`tests/evals/test_eval_runner.py`、`tests/evals/scenarios/*.yaml`（12）、`tests/evals/cassettes/*.jsonl`（12）、`tests/evals/fixtures/`、`tests/evals/test_regression_replay.py`

- [ ] 按 §4.2.7/§4.2.8/§4.5.4/§4.5.7 写假内核、`FakeWorldEnvironment`、runner（replay/record/live、超时、结果落盘、变体汇总）、`agent_eval.py` 脚本。
- [ ] 按附录 A 写 12 个场景与手写录制文件，跑 replay 全过；两个套件的结果 id 写进台账「基线」。
- [ ] 验证：`pytest tests/evals` → 0；`scripts/agent_eval.py --list` 0；`--suite regression --mode replay` 末行 `EVAL OK`；`--suite capability --mode replay` 0。
- [ ] 提交：`feat(m68): 评测运行器、假世界与初始场景集`

### Task 7: 前端运行追踪与成本（spec T12、T13）

**Files:** `web/src/api/keys.ts`、`web/src/routes/router.tsx`、`web/src/components/layout/nav.ts`、新建 `web/src/features/runs/*`、新建 `web/src/features/costs/*`

- [ ] 按 §4.6 写「运行追踪」列表页（根运行、ticket_id 过滤、轮询、空状态）与详情页（树 + 瀑布 + span 抽屉），界面不出现代码名；写 vitest 用例。
- [ ] 按 §4.6 写「成本」页（四种分组、时间范围、缓存命中率、预算告警、上限 0 显示不限）；写 vitest 用例。
- [ ] 验证：前端五步 → 0；`scripts/frontend_smoke.py` → `SMOKE OK`。
- [ ] 提交：`feat(m68): 前端运行追踪与成本页`

### Task 8: 文档、台账、全量验收与合并（spec T14）

**Files:** `docs/v3/04-接口与事件协议.md`、`docs/v3/14-现行接口清单.md`、`docs/v3/16-部署指南.md`、`docs/v3/10`（如与实现不一致）、`docs/v3/13`（§8 补条）、新建 `docs/v3/ledgers/m68-trace-evals.md`、`docs/v3/README.md`

- [ ] 按 §9 同步文档；13 §8 补实施偏离；台账（每任务证据、Rulings、两个套件的 replay 基线、手动检查未验证项）；README 状态行。
- [ ] 验证：spec §7 全部（pytest 与基线比不新增失败、`dump_openapi --check` 0、前端五步 0、两个冒烟 `SMOKE OK`、`agent_eval` 末行 `EVAL OK`、断链 0、`git diff --check` 0）。
- [ ] 提交：`docs(m68): 契约同步、台账与状态行`；然后 `git merge --no-ff trace-evals -m "merge: Trace 与评测底座（M68，见 docs/v3/13 §8.x）"` → `git tag v3-m68` → `git branch -d trace-evals` → 合并后复验与汇报。
