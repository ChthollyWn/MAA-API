# M67 模型层 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `llm.py` 拆成 `llm/` 包；模型档案存表（`default` 由 `llm.*` 合成、只读），九个角色选档案；逐次成本入账（`cost_ledger`）与三级预算；结构化输出（pydantic 校验 + 重试一次）；token 估算与前缀缓存布局（legacy 定义沿用 v2 布局）；新增 8 个模型档案/角色/成本端点与设置页「模型档案」分区。

**Architecture:** T1 配置/枚举/错误码 → T2 两表与迁移 0024 + 仓储 → T3 `llm/` 包拆分与用量归一 → T4 档案服务与角色路由 → T5 成本账本与预算 → T6 结构化输出 → T7 token 估算与前缀布局 → T8 REST 端点与装配 → T9 前端 → T10 文档、台账、全量验收与合并。

**Tech Stack:** SQLModel + Alembic（0024）、pydantic v2（`model_json_schema`/`model_validate`）、httpx 客户端、pytest、React 19 / TanStack Query / vitest。

**Spec:** [m67-model-layer](../specs/m67-model-layer.md)（900 行；每个任务照 spec 对应小节做）

## 开工基线（2026-11-03，§0 已跑）

- 分支 `model-layer`，基点 `dev` = `7b271f6`（M66 合并提交），工作区干净；tag `v3-m63`…`v3-m66`。
- `RunService` 在位；`AgentRun` 已有 `cost_micros`/`cached_tokens`/`reasoning_tokens`/`model_profile` 四列；`llm.py` 仍是单文件；迁移 head `0023_agent_runs`。
- 中间件：`ContextLayoutMiddleware`、`LoopGuardMiddleware` 在 `harness/builtin_middleware.py`；`BudgetMiddleware` 尚不存在。
- `build_model_context` 在 `harness/builtin_middleware.py`（下文 `<ctx_module>`）；`Usage` 类已在 `harness/context.py:52`。
- 两个 legacy 定义的 `model_role` 都是 `worker`；`docs/v3/14-现行接口清单.md` 在位。
- 后端基线：`3 failed, 4043 passed, 5 skipped, 2 xfailed`（3 条真资源用例既有失败；本里程碑不新增失败）。
- **三处 spec 与 M65 实现不一致**（实现细节裁决，记 13 §8.20）：
  1. §0 #9 期望 `client_factory` 出现在 `maa_api/agent/harness`，实际只在门面 `maa_api/agent/runtime.py`（M65 的 `AgentLoop` 收 `call_model` 闭包）。T4/T8 的角色路由从 `app.state.model_router` 取客户端，门面与测试替身仍走 `client_factory`；不把 `client_factory` 搬进 harness。
  2. §0 #16 期望有 `tests/agent/harness/conftest.py` 的 loop 夹具，实际没有；T7 的前缀用例照 `tests/agent/harness/test_harness_loop.py` 的装配方式自建 `MemoryTranscript` + 假模型，不新建共享 conftest。
  3. §0 #11 找到 `Usage` 在 `harness/context.py`：T3 的 `normalize_usage` 产出既有 `Usage`（复用，不新建同名类）。
- Q3（成本预算默认值）与 Q5（各角色默认模型）本里程碑**不定**（spec §2 非目标），只在台账给建议值。

## Global Constraints

- **v2 行为不变**：`legacy_chat`/`legacy_incident` 的布局、能力探查与选模型方式不改；M65 行为对齐 golden 必须继续 `17 passed`；v2 的 `tests/agent/test_*.py`、`tests/api/test_agent_router.py` 断言不改。
- **llm 导入兼容**：`from maa_api.agent.llm import OpenAICompatibleClient`、`TIMEOUT_MAX_ATTEMPTS` 等模块常量与 `tests/agent/test_llm.py` 照常可用；`rg -n "maa_api/agent/llm.py" maa_api tests scripts` 必须无输出。
- **不新增依赖**：结构化输出用 pydantic，不引入 `jsonschema`；不引 tiktoken。
- **预算默认 0（不限）**：不配置就不改变 v2 行为。
- **配置键 13 个、错误码 6 个、端点 8 个、表 2 张**（spec §9 计数目标：错误码 127/126 → 133/132；端点 125 → 133；表 29 → 31；tag 仍 19；工具 84/83 不变）。
- 不 push；提交信息 `<类型>(m67): <中文摘要>`；每任务一个提交，最后合并与打 tag `v3-m67`。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，命令行不加 `-q`；长输出重定向 `/tmp/m67/`。

## Review Focus

1. **llm 拆分后的导入兼容**：所有旧 import 路径与模块级常量；`test_llm.py` 不改即通过；`__pycache__` 里旧 `llm.pyc` 不影响（用 `grep -rn "agent\.llm"` 复核）。
2. **legacy 布局不变**：前缀缓存布局只作用于非 legacy 定义；`ContextLayoutMiddleware` 对 legacy 走原 `build_model_context` 路径；golden parity + v2 `test_runtime.py` 必须保持。
3. **预算不误杀**：两个上限默认 0；`BudgetMiddleware` 只在新链第 2 位；广播 `budget_warning` 每运行一次。
4. **档案密钥与只读约定**：`default` 只读、`api_key="***"`；加密复用 `MAA_LLM_ENCRYPTION_KEY`；改 `base_url` 清探查结果；被角色引用的档案不能删/禁用。
5. **计数与快照**：错误码 133/132、端点 133、表 31、设置 schema 多 13 项、OpenAPI tag 19；`dump_openapi`/`gen:api` 快照一致。

---

### Task 0: 分支、§0 前置检查与计划入库（已完成）

- [x] 建分支 `model-layer`，跑 §0 的 17 条检查；三处不一致按上面裁决记 13 §8.20。
- [x] 提交本计划：`docs(m67): M67 模型层实施计划`。

### Task 1: 配置项、枚举与错误码（spec T1）

**Files:** `maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`maa_api/domain/enums.py`、`maa_api/domain/errors.py`、`tests/domain/test_enums.py`、`tests/domain/test_errors.py`、`docs/v3/14-现行接口清单.md`

- [ ] 按 §4.4 加三个设置类、13 个键、`_FIELDS` 与模板/`ENV_OVERRIDES` 同步；按 §4.1 加四个枚举（已有跳过）并同步 `EXPECTED_VALUES`；按 §4.3.2 加 6 个错误码与 HTTP 绑定；`docs/v3/14` §4 的 v3 Agent 小节加 6 行与总数；`test_errors.py` 计数各 +6。
- [ ] 验证：`pytest tests/test_settings.py tests/test_vapid_settings.py tests/api/test_settings_router.py tests/domain` → 0；`GET /api/settings/schema` 多 13 项。
- [ ] 提交：`feat(m67): 模型层配置、枚举与错误码`

### Task 2: 数据模型与迁移 0024（spec T2）

**Files:** `maa_api/db/models.py`、新建 `maa_api/db/migrations/versions/0024_model_profiles.py`、新建 `maa_api/db/repositories/model_profile.py`、`cost.py`、`tests/db/test_models.py`、新建 `tests/db/test_model_profiles_migration.py`

- [ ] 按 §4.1 加 `ModelProfile`（UUID PK）与 `CostLedgerEntry`（AUTOINCREMENT）两表与迁移（可逆降级只删两表，`agent_run` 数据不变）；写两个仓储；同步 `tests/db/test_models.py` 的常量与 `COLUMN_WIDTH_OVERRIDES`（`("cost_ledger","role"): 24`）并写迁移用例。
- [ ] 验证：`pytest tests/db` → 0（`test_initial_migration.py` 的元数据一致、索引、降级到底再升级全过）。
- [ ] 提交：`feat(m67): 模型档案与成本账本两表及 0024 迁移`

### Task 3: llm 包拆分与用量归一（spec T3）

**Files:** `maa_api/agent/llm.py`（`git mv` 为 `llm/client.py`）、`llm/__init__.py`、`llm/usage.py`、`harness/chunks.py`、`tests/fakes/fake_llm.py`、新建 `tests/agent/llm/test_client_options.py`、`test_usage.py`

- [ ] `git mv`；写 `__init__.py` 重新导出（所有旧模块级常量）；加 §4.2.1 两个参数（不传时请求体与 v2 相同）；`FakeLLM` 适配；`usage.py` 的 `normalize_usage`（DeepSeek 缓存字段、OpenAI details、非法值归 0、cached ≤ prompt）并接进 chunk 解析（复用 `harness/context.py` 的 `Usage`）。
- [ ] 验证：`pytest tests/agent/test_llm.py tests/agent/llm tests/agent/harness` → 0；`python -c "from maa_api.agent.llm import OpenAICompatibleClient, TIMEOUT_MAX_ATTEMPTS; print('ok')"`；`grep -rn "maa_api/agent/llm.py" maa_api tests scripts` 无输出。
- [ ] 提交：`refactor(m67): llm 拆包与用量归一`

### Task 4: 档案服务与角色路由（spec T4）

**Files:** `maa_api/agent/llm/profiles.py`、`roles.py`、`maa_api/agent/specs/schema.py`（只加 `LEGACY_SPEC_NAMES`）、`<ctx_module>`（只改探查部分）、新建 `tests/agent/llm/test_profiles.py`、`test_roles.py`

- [ ] 写 `ResolvedProfile`/`ProbeResult`/请求与视图模型；搬出 `probe_tool_calling`/`probe_vision` 与常量（`_probe_capabilities` 改调它们）；写尾部位置探查、`ModelProfileService`（CRUD、加解密、只读 default、in-use 保护、探查 TTL/去重）、`ModelRouter`（角色→档案，legacy 定义用会话快照）。
- [ ] 验证：`pytest tests/agent/llm tests/agent/test_runtime.py -k "probe or capabilit or profile or role"` → 0；既有 probe 用例不改即通过。
- [ ] 提交：`feat(m67): 模型档案服务与角色路由`

### Task 5: 成本账本与预算（spec T5）

**Files:** `maa_api/agent/llm/cost.py`、`budget.py`、AgentLoop 所在文件（成本记录 + `BUDGET_EXCEEDED` 终止映射）、中间件链装配处（`BudgetMiddleware` 放第 2 位）、`maa_api/services/retention_service.py`、新建 `tests/agent/llm/test_cost.py`、`test_budget.py`、`tests/services/test_retention_service.py`

- [ ] 写 `compute_cost_micros`/`local_day_start`/`CostLedger`（记账、累计进 `agent_run`、按日/代理/模型/角色汇总）；`BudgetMiddleware` + `check_budget`（单次与每日、USD 不计、warn_ratio 每次运行只告警一次）；保留策略加第 6 步与 `cost_ledger_deleted`。
- [ ] 验证：`pytest tests/agent/llm tests/services/test_retention_service.py` → 0。
- [ ] 提交：`feat(m67): 成本账本与预算`

### Task 6: 结构化输出（spec T6）

**Files:** `maa_api/agent/llm/structured.py`、`llm/schemas/__init__.py`、新建 `tests/agent/llm/test_structured.py`

- [ ] 按 §4.2.7/§4.5.3 实现两种模式（`response_format` 与提示词约束）、本地 pydantic 校验、失败重试一次、最终 `STRUCTURED_OUTPUT_INVALID`；schema 注册表。
- [ ] 验证：`pytest tests/agent/llm/test_structured.py` → 0。
- [ ] 提交：`feat(m67): 结构化输出`

### Task 7: token 估算与前缀缓存布局（spec T7）

**Files:** `maa_api/agent/context/__init__.py`、`tokens.py`、`layout.py`、`harness/builtin_middleware.py`（只改 `ContextLayoutMiddleware`）、`<ctx_module>`（只拆函数）、`maa_api/agent/knowledge/__init__.py`、新建 `tests/agent/context/test_tokens.py`、`test_layout.py`、`test_layout_prefix.py`、`tests/agent/test_knowledge_service.py`（只加新方法用例）

- [ ] 写 `tokens.py`（系数/校准）、`layout.py`（段序/尾部策略 `tail_system`/`tail_user`）、拆 `prepare_history_messages`/`trim_history_to_char_budget`、知识库 `render_stable_context`/`render_dynamic_context`、改 `ContextLayoutMiddleware`（legacy 走原路径）。
- [ ] 验证：`pytest tests/agent` → 0；v2 的 `test_runtime.py`/`test_incidents.py`/`test_knowledge_service.py` 不改即通过；parity `17 passed`；前缀用例通过。
- [ ] 提交：`feat(m67): token 估算与前缀缓存布局`

### Task 8: REST 端点与装配（spec T8）

**Files:** 新建 `maa_api/api/routers/model_profiles.py`、`maa_api/main.py`、新建 `tests/api/test_model_profiles_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14-现行接口清单.md`

- [ ] 按 §4.3.1 写 8 个端点（档案 CRUD、`/{name}/probe`、`/roles`、`/costs`）与响应模型；`main.py` lifespan 装配 `model_profile_service`/`model_router`/`cost_ledger`/`context_layout` 并 include；重生成 openapi/adt；`docs/v3/14` §6.15 加 8 行并同步总数（125 → 133）与「（22 条）」→「（30 条）」。
- [ ] 验证：`pytest tests/api tests/domain` → 0；`dump_openapi --check` 0；8 个 operationId 都以 `agent_` 开头且唯一；tag 仍 19。
- [ ] 提交：`feat(m67): 模型档案、角色与成本端点`

### Task 9: 前端模型档案分区（spec T9）

**Files:** `web/src/features/models/**`、`web/src/features/settings/sections.ts`、`SettingsPage.tsx`、相关测试、`web/src/api/keys.ts`

- [ ] 按 §4.6 实现设置页「模型档案」分区（列表、新建/编辑、检测能力、角色选择、成本查询入口），移除「LLM 与 Agent」里的 9 个角色键；写 vitest 用例。
- [ ] 验证：前端五步 → 0；`scripts/frontend_smoke.py` → `SMOKE OK`；分区清单测试通过。
- [ ] 提交：`feat(m67): 设置页模型档案分区`

### Task 10: 文档、台账、全量验收与合并（spec T10）

**Files:** 02/03/04/05/16、新建 `docs/v3/ledgers/m67-model-layer.md`

- [ ] 按 spec §9 同步文档；13 §8 追加（`llm.py` 与 `llm/` 不能并存、结构化输出用 pydantic、§0 三处 M65 适配）；台账（含每任务证据、Rulings、Q3/Q5 建议值）；v3 README 状态行。
- [ ] 验证：spec §7 全部（pytest 不新增失败、`dump_openapi`/`generate_api_guide`/前端五步 0、两个冒烟 `SMOKE OK`、断链 0、`git diff --check` 0；`scripts/agent_eval.py` 尚不存在 → 台账记跳过）。
- [ ] 提交：`docs(m67): 契约同步、台账与状态行`；然后 `git merge --no-ff model-layer -m "merge: 模型层（M67，见 docs/v3/13 §8.20–8.22）"` → `git tag v3-m67` → `git branch -d model-layer` → 合并后复验与汇报。
