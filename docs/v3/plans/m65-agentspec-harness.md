# M65 代理定义与通用循环 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `maa_api/agent/runtime.py`（2986 行）里的「模型 → 工具 → 模型」循环拆进 `maa_api/agent/harness/`，引入 `maa_api/agent/specs/`（AgentSpec + 三份内置定义）与两个只读端点；对外行为一处不改（SSE 事件、落库消息、审计、确认、提问、中断、自动诊断、侦察报告逐字一致）。

**Architecture:** 先录 16 个场景的行为对齐 golden（T1，在任何 `maa_api/` 改动之前），再按依赖方向逐层搬家：registry 扩展字段（T2）→ specs 定义（T3）→ harness 类型/中间件/事件（T4）→ chunks（T5）→ 上下文组装（T6）→ 工具批执行与三个中间件（T7）→ AgentLoop 接管（T8）→ recon（T9）→ 端点与清单（T10）→ 冒烟与文档（T11）→ 合并（T12）。每个任务后跑 `tests/agent/` 全量与行为对齐测试，passed 数不得下降。

**Tech Stack:** Python 3.13（`.venv/bin/python`）、pytest 8、FakeLLM（`tests/fakes/fake_llm.py`）、OpenAPI 快照与前端类型生成。

**Spec:** [m65-agentspec-harness](../specs/m65-agentspec-harness.md)（1400 行，每个任务照它做；本计划只补执行顺序、门禁与本地适配）

## 开工基线（2026-11-03，写计划前实测，§0 全部通过）

- 分支 `agentspec-harness`，基点 `dev` = `47fc381`，工作区干净。
- `maa_api/agent/runtime.py` 2986 行；`maa_api/agent/harness`、`maa_api/agent/specs` 不存在。
- 系统提示词 sha256：`664c320349ef13cf3fdbc1f1d6969b85eebf7c7ffe980cd656c1b03381b72536`；侦察提示词 sha256：`c321d4483a9824cdd517d8844a37a558c4d3269a2f51151c45d960be214b9bda`。
- 要搬的 6 个方法、3 个模块函数都在；`registry.py` 还没有 `device`/`rest_visible`；`tests/agent/` 基线 `686 passed, 1 skipped, 2 xfailed`；`scripts/agent_smoke.py` 末行 `SMOKE OK`。
- 错误码 124 / HTTP 有状态 123；`docs/v3/14-现行接口清单.md` 记 `共 121 条 HTTP 端点`；`AGENT_SPEC_NOT_FOUND` 与 `list_specs`/`get_spec` 都不存在。
- `pytest.ini` 的 `addopts = -q`；本文与 spec 的 pytest 命令都不再带 `-q`（否则不打印汇总行）。
- 环境：没有 `rg`，spec 里的 `rg` 一律用 `grep -rn` / `grep -c`；没有 `timeout`。

## Global Constraints

- **行为一处不改**：拆分前后 SSE 帧、落库消息、审计、确认、提问、中断、诊断与侦察报告逐字一致；唯一对外新增是两个只读端点与 `AGENT_SPEC_NOT_FOUND`。
- **golden 纪律**：T1 提交 golden 之后，任何任务都不许设 `MAA_REGENERATE_PARITY`、不许手改 `tests/agent/harness/golden/legacy_parity.json`；比对失败只能改代码。`git log -- tests/agent/harness/golden/legacy_parity.json | wc -l` 最终必须是 1。
- **既有测试一行不删不改**：只允许 `tests/agent/test_tool_catalog.py`、`tests/api/test_agent_router.py` 末尾追加用例，以及 `tests/domain/test_errors.py` 的计数常量。
- 依赖方向照 spec §4.2.1：`harness/*` 不导入 `maa_api.agent.runtime`；`runtime.py` 只做门面与再导出。
- 不新增依赖、表、配置键、工具，或 `AGENT_SPEC_NOT_FOUND` 以外的错误码；不改 `AgentRuntime` 构造参数与公开方法签名，不改 `run_recon`、`dispatch_tool`、`ReconDeps`、`ReconStep` 签名。
- `docs/v2/` 只读；`docs/v3/` 的活文档按 spec §9 同步。
- 不 push；提交信息 `<类型>(m65): <中文摘要>`；每个任务一个提交（spec 各任务已给消息）。
- 长输出重定向到 `/tmp/m65-*.log`，只看尾部与退出码。

## Review Focus

1. **golden 之外的细微行为差异**：事件顺序、落库字段、收尾文案、审计 id、`tool_result` 形状。由 T1 的 16 场景比对在每个任务后钉住（`tests/agent/harness/test_harness_parity.py`）。
2. **门面内部依赖被拆断**：测试替换实例属性、monkeypatch 模块全局、按记录器名抓日志、`_persist_message` 替换、替身确认服务的 `invoke` 关键字集合。由 spec §4.2.3 表逐项对照 + T8 的 `test_harness_runtime_facade.py` 钉住。
3. **循环导入**：`harness` 反向导入 `runtime`。由 `test_harness_never_imports_the_runtime_facade`（文本扫描）钉住。
4. **提示词被无意改动**：三份内置定义必须与 v2 提示词逐字相同。由 T3 的 sha256 用例与 §0 第 4、5 条在 T3/T11 复跑钉住。
5. **v2 怪行为被顺手修掉**：spec §4.5.3 列明不修（如「未答成的提问不跳过后续调用」）。由 T7 的 `test_unanswered_question_does_not_skip_later_calls` 与整包测试钉住。
6. **计数漂移**：错误码 124→125（有状态 123→124）、HTTP 端点 121→123、（18 条）→（20 条）。由 T10 的 `tests/domain/test_errors.py`、`test_route_inventory.py` 与三个 `--check` 钉住。

---

### 执行方式

本项目 v2（M0–M62）的里程碑由子代理按 spec 逐任务实现、主 agent 逐任务复跑验证（AGENTS.md 的约定）。M65 有 12 个任务且 spec 写到「能力较弱的执行者照着做」的粒度，采用 **subagent-driven**：每个任务派一个全新子代理，只给它 spec 的对应小节、本计划的任务说明与写范围；完成后主 agent 亲自跑该任务的验证命令并检查提交，通过才进入下一个任务。T1 的 golden 必须先由子代理录制、主 agent 复核后提交，之后任何任务不得重录。

---

### Task 0: 分支与前置检查（已完成，记录证据）

- [x] 建分支 `agentspec-harness`（基点 `dev` = `47fc381`）。
- [x] 跑 spec §0 的 16 条检查（`rg` 换 `grep`）：提示词哈希、runtime 2986 行、6 方法 + 3 函数在位、`tests/agent/` = 686 passed / 1 skipped / 2 xfailed、`agent_smoke.py` = SMOKE OK、错误码 124/123、端点 121、`addopts = -q`。
- [x] 提交本计划：`docs(m65): M65 代理定义与通用循环实施计划`。

### Task 1: 行为对齐基线（spec T1，**改动任何 `maa_api/` 代码之前**）

**Files:**
- Create: `tests/agent/harness/test_harness_parity.py`（照 spec 附录 B）
- Create: `tests/agent/harness/golden/legacy_parity.json`（生成）

**Interfaces:**
- Produces: 16 场景的 golden；后续每个任务的回归门禁

- [ ] **Step 1: 照附录 B 写测试**（16 场景、`_Normalizer`、`_request`、`_Harness`、两个用例；`tests/agent/harness/` 不放 `__init__.py`）。
- [ ] **Step 2: 录 golden**

Run:
```bash
MAA_REGENERATE_PARITY=1 .venv/bin/python -m pytest tests/agent/harness/test_harness_parity.py > /tmp/m65-t1-regen.log 2>&1; echo $?; tail -n 2 /tmp/m65-t1-regen.log
```
Expected: 退出码 0。

- [ ] **Step 3: 连跑两次**

Run:
```bash
for i in 1 2; do .venv/bin/python -m pytest tests/agent/harness/test_harness_parity.py > /tmp/m65-t1-$i.log 2>&1; echo "run$i:$?"; tail -n 1 /tmp/m65-t1-$i.log; done
.venv/bin/python -c "import json; print(len(json.load(open('tests/agent/harness/golden/legacy_parity.json'))))"
git status --porcelain
```
Expected: 两次都 `0` 与 `17 passed`；`16`；工作区只有这两个新文件。

- [ ] **Step 4: 提交**

```bash
git add tests/agent/harness && git commit -m "test(m65): v2 会话、诊断与侦察的行为对齐基线"
```
提交后 golden 冻结：此后不许 `MAA_REGENERATE_PARITY=1`、不许手改。

### Task 2: 工具定义扩展字段与 REST 过滤（spec T2）

**Files:** `maa_api/agent/registry.py`、`tools/raw.py`、`status.py`、`media.py`、`copilot_attempt.py`、`ops.py`、`maa_api/api/routers/agent.py`、`tests/agent/test_tool_catalog.py`、`tests/api/test_agent_router.py`

- [ ] 按 spec §4.2.16 加 `device`、`rest_visible`、`refetchable`、`untrusted_output` 与校验；赋 `device`；REST 目录按 `rest_visible` 过滤；§6 的 T2 用例追加在文件末尾。
- [ ] 验证：`pytest tests/agent/ tests/api/test_agent_router.py`（日志 `/tmp/m65-t2.log`）→ 0；`scripts/dump_openapi.py --check` → 0；`tests/agent/harness/test_harness_parity.py` 仍全绿。
- [ ] 提交：`feat(m65): 工具定义增加 device 等扩展字段，REST 目录按 rest_visible 过滤`

### Task 3: 代理定义：schema、注册表与三份内置定义（spec T3）

**Files:** `maa_api/agent/specs/`（新）、`runtime.py`（`SYSTEM_PROMPT`、`INCIDENT_ALLOWED_TOOLS`）、`recon/runner.py`（`SYSTEM_PROMPT`）、`main.py`、`tests/agent/specs/test_agent_specs.py`（新）

- [ ] 建包；先跑 spec 附录 A 的 `/tmp/m65/write_builtin_specs.py` 生成三份 md；按 §4.2.14/§4.2.15 写 `schema.py`、`registry.py`；两个提示词常量改为从定义读；`main.py` 装配注册表；写 T3 用例。
- [ ] 验证：§0 第 4、5 条哈希不变；`pytest tests/agent/ tests/api/`（`/tmp/m65-t3.log`）→ 0；`scripts/agent_smoke.py` → SMOKE OK；parity 全绿。
- [ ] 提交：`feat(m65): 代理定义注册表与三份内置定义`

### Task 4: harness 核心类型、中间件接口与事件总线（spec T4）

**Files:** `maa_api/agent/harness/{__init__,context,middleware,events}.py`（新）、`runtime.py`（`sse_event` 只留再导出）、`tests/agent/harness/{test_harness_middleware,test_harness_events}.py`（新）

- [ ] 按 §4.2.4–§4.2.6 建三个模块（`context.py` 导入 T3 的 `AgentSpec`）；`sse_event` 原样搬 `events.py`；写 T4 用例。
- [ ] 验证：`pytest tests/agent/`（`/tmp/m65-t4.log`）→ 0。
- [ ] 提交：`refactor(m65): harness 核心类型、中间件接口与事件总线`

### Task 5: 流式解析搬到 chunks.py（spec T5）

**Files:** `harness/chunks.py`（新）、`runtime.py`、`tests/agent/harness/test_harness_chunks.py`（新）

- [ ] 搬 `merge_tool_call_deltas`、`_chunk_tool_calls`→`chunk_tool_calls`；`_complete`→`collect_completion`；删 `_TurnDraft` 改用 `TurnDraft`；旧循环改 `collect_completion` 返回对象；`_probe_capabilities` 用新名；runtime 再导出 `merge_tool_call_deltas`。
- [ ] 验证：`pytest tests/agent/`（`/tmp/m65-t5.log`）→ 0；parity 全绿。
- [ ] 提交：`refactor(m65): 流式 chunk 解析搬到 harness/chunks`

### Task 6: 上下文组装搬到 builtin_middleware.py（spec T6）

**Files:** `harness/builtin_middleware.py`（新）、`runtime.py`、`tests/agent/harness/test_harness_context_layout.py`（新）

- [ ] 搬 §4.2.2「T6」各行；`build_model_context` 默认参数改 `_DEFAULT_SYSTEM_PROMPT`；定义 `TraceMiddleware`、`ContextLayoutMiddleware`（暂不接线）；runtime 改为导入再导出；`_hydrate_latest_screenshot` 从新模块导入两个私有函数。
- [ ] 验证：`pytest tests/agent/`（`/tmp/m65-t6.log`）→ 0。
- [ ] 提交：`refactor(m65): 上下文组装搬到 harness/builtin_middleware`

### Task 7: 工具批执行与三个中间件（spec T7）

**Files:** `harness/toolexec.py`（新）、`builtin_middleware.py`、`chunks.py`、`runtime.py`、`tests/agent/harness/test_harness_toolexec.py`（新）、`test_harness_chunks.py`

- [ ] 按 spec 的 5 个步骤：`parse_tool_call`/`parse_arguments`、`toolexec.execute_batch` 与闸门、`ClarificationMiddleware`/`PolicyMiddleware`/`LoopGuardMiddleware`、旧循环过渡改造、删旧方法并再导出常量。
- [ ] 验证：`pytest tests/agent/`（`/tmp/m65-t7.log`）→ 0；`pytest tests/agent/test_runtime.py tests/agent/test_incidents.py tests/agent/test_account_resources_flow.py`（`/tmp/m65-t7b.log`）→ 0；parity 全绿。
- [ ] 提交：`refactor(m65): 工具批执行与提问、确认中间件`

### Task 8: AgentLoop 接管 v2 会话与自动诊断（spec T8）

**Files:** `harness/loop.py`（新）、`runtime.py`、`tests/agent/harness/test_harness_loop.py`、`test_harness_runtime_facade.py`（新）

- [ ] 写 `loop.py`；runtime 加 `_LegacyTranscript` 与 `_legacy_chain()`，`_run_turn_body` 改成「v2 前置步骤 + `AgentLoop.run`」；`_run_turn`/`run_incident` 传 `spec_name`；删旧循环内联逻辑；更新模块说明；写 T8 用例。
- [ ] 验证：`pytest tests/agent/`（`/tmp/m65-t8.log`）→ 0；`wc -l < maa_api/agent/runtime.py` < 1900；runtime 里不再有旧符号；parity 全绿；`test_harness_runtime_facade.py` 全过。
- [ ] 提交：`refactor(m65): AgentLoop 接管 v2 会话与自动诊断`

### Task 9: recon 跑在 AgentLoop 上（spec T9）

**Files:** `maa_api/agent/recon/runner.py`、`tests/agent/harness/test_harness_recon.py`（新）

- [ ] 按 §4.2.12 改写 `run_recon`，加 `ReconBudgetMiddleware`、`ReconDispatchMiddleware`；删私有函数前先 `grep` 确认没有测试导入。
- [ ] 验证：`pytest tests/agent/`（`/tmp/m65-t9.log`）→ 0；`pytest tests/agent/test_recon_delegate.py tests/agent/test_recon_explore.py`（`/tmp/m65-t9b.log`）→ 0；parity 全绿。
- [ ] 提交：`refactor(m65): 侦察子循环跑在 AgentLoop 上`

### Task 10: 代理定义端点、错误码与接口清单（spec T10）

**Files:** `maa_api/api/routers/agent_specs.py`（新）、`main.py`、`domain/errors.py`、`tests/domain/test_errors.py`、`tests/api/test_agent_specs_router.py`（新）、`docs/v3/14-现行接口清单.md`、`docs/v3/15-开放API接入指南.md`、`web/openapi.json`、`web/src/types/api.d.ts`

- [ ] 按 §4.3.1 写路由并 include；按 §4.3.2 加 `AGENT_SPEC_NOT_FOUND`；`tests/domain/test_errors.py` 计数 125/124；改 `docs/v3/14`（§4.11 一行、总数 125、§6 两行、121→123、（18）→（20）、`GET /api/agent/tools` 用途补一句）；重新生成 openapi/guide/api.d.ts；写 T10 用例。
- [ ] 验证：`pytest tests/domain/ tests/api/ tests/test_api_guide_generator.py`（`/tmp/m65-t10.log`）→ 0；`dump_openapi.py --check`、`generate_api_guide.py --check`、`pnpm gen:api:check` 全 0。
- [ ] 提交：`feat(m65): 代理定义列表与详情端点`

### Task 11: 冒烟、全量验证与文档（spec T11）

**Files:** `scripts/agent_smoke.py`、`docs/v3/02-系统架构.md`、`docs/v3/05-运行时与代理定义.md`、`docs/v3/13-决策记录.md`（§8）、`docs/v3/ledgers/m65-agentspec-harness.md`（新）

- [ ] `agent_smoke.py` 加代理定义三步检查；跑 spec §7 全部验收；按 §9 同步文档；写台账（每任务 passed 数、golden sha256、runtime 前后行数、Rulings）。
- [ ] 验证：spec §7 全部通过（pytest 全量 0 新增失败、三个 `--check` 0、前端四步 0、两个冒烟 SMOKE OK、断链 `BROKEN 0`、`git diff --check dev...HEAD` 0），以及 §7 特有的 4 条（golden 只提交过 1 次、runtime < 1900、提示词哈希不变、既有测试零删除）。
- [ ] 提交：`docs(m65): 冒烟、契约细化与台账`

### Task 12: 合并与 tag（spec T12）

- [ ] `git status --porcelain` 为空 → `git checkout dev && git merge --no-ff agentspec-harness -m "merge: 代理定义与通用循环（M65，见 docs/v3/13 §8.<N>）"` → `git tag v3-m65` → `git branch -d agentspec-harness`。
- [ ] 合并后复验（主 agent 亲自跑）：pytest 全量、三个 `--check`、断链、`git diff --check`、golden 提交数、runtime 行数。
- [ ] 汇报：tag、runtime 行数前后、golden 状态、端点/错误码计数、遗留。
