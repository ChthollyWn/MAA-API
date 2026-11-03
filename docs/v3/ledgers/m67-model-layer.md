# M67 模型层：角色、档案、结构化输出、成本、前缀缓存布局（台账）

- 分支：`model-layer`；基点：`dev` = `7b271f6`；合并：`dev` 上的 `merge: 模型层（M67，见 docs/v3/13 §8.20–8.25）`；tag：`v3-m67`
- spec：[m67-model-layer](../specs/m67-model-layer.md)
- 计划：[m67-model-layer](../plans/m67-model-layer.md)
- 决策记录：[docs/v3/13 §8.20–8.25](../13-决策记录.md)
- 来源：继续按 goal 推进 v3；执行方式 subagent-driven（每任务一个全新子代理实现、主 agent 逐任务复跑验证）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `e61eae2` | 计划入库；§0 的 17 条检查；三处 M65 结构适配按实现细节裁决（13 §8.20） |
| T1 | `631b410` | 4 个枚举、6 个错误码（133/132）、9 个 `llm.role_*` + `BudgetSettings` + `ContextSettings` 共 13 个配置键与 `_FIELDS`/模板/测试；`docs/v3/14` §4 加 6 行并同步总数 |
| T2 | `ad68a65` | `ModelProfile`/`CostLedgerEntry` 两表与迁移 `0024_model_profiles`（可逆）；两个仓储；`tests/db` 常量与 `COLUMN_WIDTH_OVERRIDES`；迁移用例 |
| T3 | `13b679e` | `git mv llm.py llm/client.py`；`llm/__init__.py` 重导出 7 个旧公共名；客户端加 `response_format`/`max_tokens`（None 时请求体逐字节不变）；`FakeLLM` 记 `request_options`；`llm/usage.py` 的 `Usage`/`normalize_usage` 并接进 chunks |
| T4 | `9d35917` | `llm/profiles.py`（`ResolvedProfile`/`ProbeResult`/视图模型/4 个探查/`ModelProfileService`）、`llm/roles.py`（`ModelRouter`）、`LEGACY_SPEC_NAMES`；v2 探查行为搬出但不变 |
| T5 | `a316b50` | `llm/cost.py`（`compute_cost_micros`/`local_day_start`/`CostLedger`）、`llm/budget.py`（`BudgetMiddleware`/`check_budget`）、AgentLoop 记账与 `BUDGET_EXCEEDED` 映射、保留策略第 6 步（365 天、`cost_ledger_deleted`） |
| T6 | `ad844a0` | `llm/structured.py`（注册表、两种模式、重试一次、`STRUCTURED_OUTPUT_INVALID`）与空 `schemas/` 包 |
| T7 | `78232a2` | `context/tokens.py`、`context/layout.py`；拆 `prepare_history_messages`/`trim_history_to_char_budget`（`build_model_context` 逐字节不变）；知识库 stable/dynamic 两方法；`ContextLayoutMiddleware` 的 legacy/非 legacy 分支与前缀断点 |
| T8 | `e210f53` | `api/routers/model_profiles.py` 8 个端点与响应模型；lifespan 装配 `model_profile_service`/`model_router`/`cost_ledger`/`context_layout`；`docs/v3/14` §6 加 8 行（133、30 条）；重生成 openapi 与 `api.d.ts` |
| T9 | `f81cc0f` | 设置页「模型档案」分区（角色卡 + 档案列表 + 编辑 Sheet + 检测/删除）、`sections.ts`/`SettingsPage.tsx`/`keys.ts`；移除「LLM 与 Agent」里的 9 个角色键 |
| T10 | 本台账所在提交 | 02/03/04/05/16 同步；13 §8.20–8.25；`docs/v3/15` 按新错误码表重生；v3 README 状态行；全量验收与合并 |

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| T1 | `pytest tests/test_settings.py tests/test_vapid_settings.py tests/api/test_settings_router.py tests/domain` = `479 passed`；`_FIELDS` 55→68、schema items 53→66 |
| T2 | `pytest tests/db` = `312 passed`；迁移升级/降级、索引/外键/唯一约束、AUTOINCREMENT 序列全部覆盖 |
| T3 | `pytest tests/agent/test_llm.py tests/agent/llm tests/agent/harness` = `105 passed`；parity 17；`tests/agent tests/api` 全量 = `1330 passed`；旧 `maa_api/agent/llm.py` 引用清零；`test_llm.py` 零改动 |
| T4 | probe/capability/profile/role 过滤集 = `24 passed`；`tests/agent/llm` = `31 passed`（含 T5/T6 后）；parity 17；v2 `test_runtime.py` 零改动 |
| T5 | `tests/agent/llm + retention` = `42 passed`；parity 17；`tests/agent/runs + test_runtime` = `119 passed`；`tests/agent` 全量 861 passed |
| T6 | `tests/agent/llm` = `31 passed`；parity 17 |
| T7 | `tests/agent` = `875 passed, 1 skipped, 2 xfailed`；parity 17；`test_knowledge_service.py` = `59 passed` 且既有行零删除；全量 pytest `4108 passed, 3 failed`（3 条真资源既有失败） |
| T8 | `tests/api tests/domain` = `932 passed`；`dump_openapi --check` 0；`pnpm gen:api:check` 0；8 个 operationId；tag 19 |
| T9 | 前端五步 0（gen:api/typecheck/vitest 75 文件 778 用例/build 全过）；`frontend_smoke.py` = `SMOKE OK` |

### 最终验收（T10）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | 见「合并后复验」；与本里程碑起点比不新增失败 |
| `scripts/dump_openapi.py --check` | 退出码 0 |
| `scripts/generate_api_guide.py --check` | 退出码 0（T10 发现 T1 加错误码后未重生，已补生：只新增 6 行错误码表） |
| 前端五步 | 退出码 0；vitest 75 文件 778 用例 |
| `scripts/frontend_smoke.py` / `scripts/agent_smoke.py` | 均 `SMOKE OK` |
| `scripts/check_doc_links.py` | `BROKEN 0` |
| `git diff --check` | 退出码 0 |
| `scripts/agent_eval.py` | 尚不存在（M68 提供），按 spec §7 记跳过 |
| 手动验收（真实 LLM：default 只读、角色切换、成本查询） | **未验证**（本机无真实 LLM 凭据） |

### 计数

- 错误码 127/126 → **133/132**；HTTP 端点 125 → **133**（`### 6.15 agent` 22 → **30 条**）；业务表 29 → **31**；`EXPECTED_INDEXES` 36 → **38**；`UNIQUE_CONSTRAINTS` +1；FK SET NULL 27 → 29；TEXT 18 → 19；配置键 +13（`SETTING_KEYS` 与设置 schema 各 +13，config-only 集合不变）；迁移 head `0023_agent_runs` → **`0024_model_profiles`**；OpenAPI tag 19 不变；工具 84 / 模型可见 83 不变。
- 新增测试文件 12 个：`tests/db/test_model_profiles_migration.py`、`tests/agent/llm/{test_client_options,test_usage,test_profiles,test_roles,test_cost,test_budget,test_structured}.py`、`tests/agent/context/{test_tokens,test_layout,test_layout_prefix}.py`、`tests/api/test_model_profiles_router.py`；前端新增 `web/src/features/models/`。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 三处 M65 结构适配（13 §8.20） | `client_factory` 在门面、无 harness conftest、`Usage` 已在 harness；均为实现细节，按 M65 实际接线 |
| R2 | `llm.py` 删除、由 `llm/__init__.py` 重导出（13 §8.21） | 同名包不能与模块并存；旧 import 与 `test_llm.py` 零改动 |
| R3 | 结构化输出用 pydantic 本地校验（13 §8.22） | 不新增依赖；V3-D35 的落地；错误带字段路径 |
| R4 | `Usage` frozen 后 loop 改 `+`、token 0→NULL、chunks 按字段合并（13 §8.23） | 同时满足 M67 语义与 M65 golden；parity 17 passed |
| R5 | `BudgetMiddleware` 有 `cost_ledger` 才进链（13 §8.24） | 既有五元链序断言不改；默认 0 上限时与 v2 完全一致 |
| R6 | 档案注册表放 `structured.py`、`schemas/` 只做业务 schema（13 §8.25） | 避免循环导入，对调用方无差异 |
| R7 | T2 把列宽例外从局部变量提升为模块级 `COLUMN_WIDTH_OVERRIDES`（保留 `agent_run.finish_reason`） | spec 要求；既有断言语义不变 |
| R8 | T4 的 probe 延迟导入 `TokenEstimator`（T7 才建），T7 落地后自动生效 | 任务顺序使然；测试 monkeypatch 该函数 |
| R9 | T8 的 `ModelRoleItem`/`ModelRolesView` 定义在路由内 | `roles.py` 没有这两个响应模型；不越界改 T4 文件 |
| R10 | T9 未做成本查询 UI | spec §4.6 没有成本 UI；成本页归 M68 |
| R11 | T10 补跑 `generate_api_guide.py` 重生 `docs/v3/15` | T1 加了 6 个错误码但没重生指南；主 agent 验收发现并修复（只新增 6 行） |
| R12 | `agent_eval.py` 不存在，评测门禁记跳过 | spec §7 自己说明由 M68 提供 |

## Q3 / Q5 建议值（只给建议，默认值未改；等用户确认）

- **Q3 成本预算默认值**：建议 `budget.per_run_cost_limit = 1.0` 元、`budget.daily_cost_limit = 10.0` 元、`budget.warn_ratio = 0.8`。按 `deepseek-flash` 的公开价位，单次运行成本通常远低于 1 元；每日 10 元足够日常使用且有明显异常拦截力。当前默认仍为 0（不限），确认后再改模板与 `BudgetSettings` 默认值。
- **Q5 各角色默认模型**：建议全部角色先留空（用 `default` 档案），即现状；随后建一个低成本 chat 档案（与 `default` 同模型即可）配给 `title`、`summarizer`、`trigger_eval`，等 M70 起总控上线再接 `orchestrator` 的轻模型。不把任何角色默认值写进模板，由用户在设置页按需指定。

## 偏离（已记 13 §8.20–8.25）

- §8.20 三处 M65 结构适配。
- §8.21 `llm.py` 与 `llm/` 不能并存。
- §8.22 结构化输出用 pydantic。
- §8.23 `Usage` frozen 后的累加与落库口径。
- §8.24 预算中间件「有账本才进链」。
- §8.25 模型档案注册表位置。

## 遗留

- Q3 / Q5 待用户确认（建议值见上）；确认前默认行为不变。
- spec §7 的手动验收（真实 LLM 下 default 只读、角色切换、成本查询）**未验证**：本机没有真实 LLM 凭据，单测与冒烟覆盖了同逻辑。
- `scripts/agent_eval.py` 由 M68 提供，本里程碑评测门禁跳过。
- 非 legacy 代理（M70 起）才会真正用上前缀缓存布局；本里程碑的首字节一致由单测钉住。
- 观察到的既有 flake：整包跑 `tests/agent` 时 `test_incidents.py::test_incident_allowed_action_still_uses_confirmation_service` 偶发 0.25 秒轮询超时（单跑与整文件重跑均通过，与 M67 改动无关）；M68 若频繁复现再处理。
- 前端成本页与预算展示归 M68；本里程碑只广播 `budget_warning`。
