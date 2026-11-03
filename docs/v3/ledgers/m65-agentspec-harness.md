# M65 代理定义与通用循环（台账）

- 分支：`agentspec-harness`；基点：`dev` = `47fc381`；合并：`dev` 上的 `merge: 代理定义与通用循环（M65，见 docs/v3/13 §8.10–8.11）`；tag：`v3-m65`
- spec：[m65-agentspec-harness](../specs/m65-agentspec-harness.md)
- 计划：[m65-agentspec-harness](../plans/m65-agentspec-harness.md)
- 决策记录：[docs/v3/13 §8.10–8.11](../13-决策记录.md)
- 来源：继续按 goal 推进 v3（用户已确认「原样开工」）；执行方式为 subagent-driven（每个任务一个全新子代理实现、主 agent 逐任务复跑验证）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `164c340` | 计划入库；§0 16 条前置检查全过（两段提示词哈希、runtime 2986 行、686/1/2 基线、`SMOKE OK`） |
| T1 | `84a651e` | 16 场景行为对齐 golden（`tests/agent/harness/golden/legacy_parity.json`，sha256 `e882382e0b63752d495cbccbe66f4e278a747500f7bcc10965fd5265f34ef593`）+ 测试骨架；golden 只在这一次提交 |
| T2 | `29cf956` | `ToolDefinition` 加 `device`/`refetchable`/`untrusted_output`/`rest_visible` 与校验；v2 12 个设备工具赋值；REST 目录与调用按 `rest_visible` 过滤 |
| T3 | `33cbac5` | `maa_api/agent/specs/`（`schema.py` 完整模型、`registry.py` 17 条校验、三份内置定义 md）；三个提示词常量改为读定义；`main.py` 装配注册表；**提前落 `AGENT_SPEC_NOT_FOUND`**（见偏离） |
| T4 | `2514540` | `harness/context.py`、`middleware.py`、`events.py`；`sse_event` 搬入 events 并再导出 |
| T5 | `973a01f` | `harness/chunks.py`（`merge_tool_call_deltas`、`chunk_tool_calls`、`collect_completion`）；删 `_TurnDraft`/`_complete` |
| T6 | `eb738eb` | `harness/builtin_middleware.py`（上下文组装全部搬入 + `TraceMiddleware`、`ContextLayoutMiddleware` 未接线）；runtime 再导出 |
| T7 | `ef5182d` | `harness/toolexec.py`（三闸门、并行批、确认终态补 skipped、`stop_run`）+ `ClarificationMiddleware`/`PolicyMiddleware`/`LoopGuardMiddleware`；旧循环改调 `execute_batch`；再导出五个常量 |
| T8 | `2b98a4e` | `harness/loop.py`（`AgentLoop`、`Transcript`、`MemoryTranscript`、`select_stop`）；runtime 变门面（`_LegacyTranscript`、`_legacy_chain()`、`spec_name`），删除旧循环；runtime 2986 → 1663 行 |
| T9 | `3c7665e` | `recon/runner.py` 改跑 `AgentLoop`；`ReconBudgetMiddleware`、`ReconDispatchMiddleware`；公开签名不变 |
| T10 | `76c6d2b` | `api/routers/agent_specs.py` 两个只读端点（Pydantic `SpecList`/`SpecDetail`）；`main.py` include；`docs/v3/14` §6 加两行、端点 121→123；重生成 openapi 与 `api.d.ts` |
| T11 | 本台账所在提交 | `scripts/agent_smoke.py` 加代理定义三步检查；02/04/05/12 契约细化写回；13 §8.10–8.11；本台账 |

## 验证证据

### 行为对齐（每个任务后都跑）

- `pytest tests/agent/harness/test_harness_parity.py` 每个任务后都是 `17 passed`（16 场景 + `test_golden_covers_exactly_the_scenarios`）
- golden 键数 16；`git log --format=%h -- tests/agent/harness/golden/legacy_parity.json | wc -l` = `1`
- 全旅程未再设 `MAA_REGENERATE_PARITY`、未手改 golden

### 逐任务 passed 数（`tests/agent/`，或注明的组合）

| 任务 | 结果 |
|---|---|
| T2 | `716 passed, 1 skipped, 2 xfailed`（`tests/agent/` + `tests/api/test_agent_router.py`） |
| T3 | `1219 passed, 1 skipped, 2 xfailed`（`tests/agent/` + `tests/api/`） |
| T4 | `756 passed, 1 skipped, 2 xfailed` |
| T5 | `759 passed, 1 skipped, 2 xfailed` |
| T6 | `761 passed, 1 skipped, 2 xfailed` |
| T7 | `771 passed, 1 skipped, 2 xfailed`；`test_runtime.py test_incidents.py test_account_resources_flow.py` = `99 passed` |
| T8 | `792 passed, 1 skipped, 2 xfailed`；`test_runtime.py test_incidents.py` = `98 passed` |
| T9 | `797 passed, 1 skipped, 2 xfailed`；`test_recon_delegate.py test_recon_explore.py` = `67 passed, 2 xfailed` |
| T10 | `tests/domain/ tests/api/ tests/test_api_guide_generator.py` = `904 passed` |

### 最终验收（T11，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -o addopts= -q` | `3 failed, 3973 passed, 5 skipped, 2 xfailed in 228.90s`；3 条失败与基点同一组（`test_activity_index.py` 2 + `test_stage_layout.py` 1，真资源用例） |
| `scripts/dump_openapi.py --check` | 退出码 0 |
| `scripts/generate_api_guide.py --check` | 退出码 0（`docs/v3/15` current） |
| `cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` | 退出码 0；vitest 74 文件 773 用例全过 |
| `scripts/frontend_smoke.py` | 退出码 0，`SMOKE OK` |
| `scripts/agent_smoke.py` | 退出码 0，`SMOKE OK`（含新增的代理定义列表 / legacy_chat 正文 / 未知名字 404 三步） |
| `scripts/check_doc_links.py` | `BROKEN 0` |
| `git diff --check dev...HEAD` | 退出码 0 |
| `wc -l < maa_api/agent/runtime.py` | `1663`（< 1900） |
| `grep -c '^SYSTEM_PROMPT = """' -r maa_api` | 0（两段提示词都在 `specs/builtin/`） |
| 两段提示词 sha256 | `664c320349ef13cf3fdbc1f1d6969b85eebf7c7ffe980cd656c1b03381b72536` / `c321d4483a9824cdd517d8844a37a558c4d3269a2f51151c45d960be214b9bda`（与基点一致） |
| 既有测试删除行 | `git diff dev...HEAD -- 'tests/agent/test_*.py' tests/api/test_agent_router.py` 的减行 = `0` |

### 计数

- 工具 84 个（模型可见 83）不变；设备级别：exclusive 8 个、read 4 个、其余 none。
- HTTP 端点 121 → 123（`web/openapi.json` operation 数实测 123；`docs/v3/14` §6 同步）。
- 错误码 124 → 125（有 HTTP 状态 123 → 124），新增 `AGENT_SPEC_NOT_FOUND`（404）。
- OpenAPI tag 19 个、业务表 28 张不变。
- 新增测试文件 11 个：`test_harness_{parity,middleware,events,chunks,context_layout,toolexec,loop,runtime_facade,recon}.py`、`tests/agent/specs/test_agent_specs.py`、`tests/api/test_agent_specs_router.py`。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 执行方式选 subagent-driven：每任务一个全新子代理实现，主 agent 逐任务复跑验证再派下一个 | 12 个任务、spec 已写到「照着做」的粒度；延续 v2 M0–M62 的 SDD 惯例；比单会话原生执行更能隔离上下文 |
| R2 | `AGENT_SPEC_NOT_FOUND` 与计数、`docs/v3/14` §4 行提前到 T3 | T3 的 `AgentSpecRegistry.get` 契约测试需要这个错误码（13 §8.11） |
| R3 | skipped 占位结果也带 `stop_run`/`stop_reason="confirmation_resolved"` | 按 §4.2.8「最后把终态码且未 stop 的结果置 stop_run」字面执行；T8 的 `select_stop` 仍取第一个实际终态，行为与 v2 一致 |
| R4 | T7 过渡期 `RunContext` 额外传 `draft` | T7 与中断收尾共用同一个草稿；§4.2.11 未列，属内部过渡细节，T8 后不再需要（保留无害） |
| R5 | recon 的 `new_uuid` 从 `maa_api.db.models` 导入 | 纯函数；recon 仍不碰会话库（§4.2.12） |
| R6 | T1 的附录 B 按骨架 + 16 场景表补齐完整测试 | spec 附录 B 只给骨架与场景表；首录时测试代码自身有 lambda 参数错误，修正测试代码后重录，golden 未手改 |
| R7 | 12 §6 的「legacy_chat 录制回放评测」由 golden 承担 | 写回 §4.8 第 9 条；M68 再迁成评测用例 |
| R8 | §7 的「pytest 退出码 0」按「与基点相比不新增失败」执行 | 基点即有 3 条真资源用例失败（环境相关），spec 上半句同时要求「不新增失败」 |

## 偏离（已记 13 §8.10–8.11）

- §8.10 M65 契约细化：按 spec §4.8 的九条把运行时契约写回 02/04/05/12。
- §8.11 `AGENT_SPEC_NOT_FOUND` 提前到 T3（原定 T10）。

## 遗留

- 3 条真资源用例的既有失败（`tests/services/test_activity_index.py` 2 条、`tests/services/test_stage_layout.py` 1 条），与 `resource/` 数据漂移有关，非本里程碑引入。
- `docs/v3/05` §2.4 收件箱转消息、§2.5 提及展开仍是 M66+ 的设计，M65 未实现（非目标）。
- `TraceMiddleware` 目前是空实现，M68 加 span。
- golden 是 v2 行为的唯一防线：后续里程碑若动 runtime/harness，必须继续跑 `test_harness_parity.py`；M68 迁成录制回放评测用例。
- 前端无界面改动，只重生了 `web/openapi.json` 与 `web/src/types/api.d.ts`。
