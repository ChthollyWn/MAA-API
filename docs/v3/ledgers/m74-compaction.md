# M74 摘要压缩与工具结果清理（台账）

- 分支：`compaction`；基点：`dev` = `b591046`；合并：`dev` 上的 `merge: 摘要压缩与工具结果清理（M74，见 docs/v3/13 §8.58–8.64）`；tag：`v3-m74`
- spec：[m74-compaction](../specs/m74-compaction.md)（748 行）
- 计划：[m74-compaction](../plans/m74-compaction.md)
- 决策记录：[docs/v3/13 §8.58–8.64](../13-决策记录.md)
- 迁移：`0028_compaction`（可逆，不回填）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `3fdcd52` | 计划入库；§0 十六条检查（`<...>` 占位符落为实际符号）；基线 `3 failed / 4475 passed` |
| G1（T1+T2） | `4f5c0dc` | 三个配置键；`CompactionTrigger`/`CheckpointStatus`；`COMPACTION_FAILED`（502，错误码 141/140→142/141）；`CompactionCheckpoint` 表 + 迁移 `0028` + 仓储 `add/active_for_session/list_for_session/supersede_active` + `list_after_seq` |
| G2（T3） | `8d0f171` | 48 个工具 `refetchable=True` 与 `EXPECTED_REFETCHABLE` 钉死（工具 93/48） |
| G3（T4） | `881f85e` | `CompactionSummary` schema（七子模型）与 `summarizer.md`（background、max_steps 1、compaction_summary）；`common.md` 末尾【压缩摘要】段；定义 15→16 |
| G4（T5） | `20163fd` | `context/durable.py`（`DurableState`/收集/渲染/`DurableContextMiddleware` 总控尾部工单段）、`context/clearing.py`（6 往返成批、三种占位、溢出抛 `LLM_CONTEXT_OVERFLOW`）、`ContextBudget`/`budget_for`；链位 6/8；`context_overflow` 映射 |
| G5（T6） | `be299ad` | `context/compaction.py`（`select_upto_seq`、`render_transcript`、`mechanical_summary`、`complete_refs`、`Compactor` 分块摘要/降级/手动 502、`CompactionMiddleware` 自动压缩）；`load_history` + 非 legacy 切 token 预算；链位 7；`app.state.compactor` |
| G6（T7） | `0c2bdc9` | 两个端点（`POST .../compact`、`GET .../checkpoints`）与响应模型，operationId `agent_compact_session`/`agent_list_checkpoints`；快照/类型/清单（端点 144→146） |
| G7（T8） | `d5bf5f9` | 前端：`features/compaction/`（标记/摘要展开/degraded/`insertMarkers`/`formatTokens`/`useCompactSession`）、总控与工单流接入、SSE/WS 失效 checkpoints、`/compact` 三种 Toast（前端 881 用例） |
| T9a | `6c8a9a4` | `test_orchestrator_long_session_stays_within_budget`（窗口 16000、200 用户消息 + 20 汇报、每次请求低于溢出线、至少压缩一次、摘要含全部 20 个工单 ID、`get_ticket` 仍在工具声明） |
| T9b | 本台账所在提交 | 文档同步（02/03/04/05/15）、13 §8.58–8.64、README、本台账；全量验收与合并 |

## 核心实现

- **预算**：`ContextBudget.window` 来自模型档案；`trigger_tokens = min(ratio×window, window−output_reserve)`；`overflow_tokens = window − output_reserve`；`DEFAULT_MAX_CONTEXT_CHARS`/`trim_history_to_char_budget` 只剩 legacy 分支。
- **压缩**：`select_upto_seq` 保留最近 `keep_recent_turns` 轮与 ≥4 个工具往返（长单轮缩到 4 并带「简报」进 `user_requests`）；同一事务 `supersede_active`+`add`；累积（新摘要输入含旧摘要）；ID 由 `complete_refs` 确定性补全，durable 状态原样进检查点；自动失败两次退回机械摘要（`degraded=true`），手动失败 502 不写检查点，手动预算用完 429。
- **清理**：`B=((R−K)//6)×6`；只清 >200 字、可重取、非错误的旧结果；占位保留 `role`/`tool_call_id`/`name`；清理后仍超溢出线在该层抛 `LLM_CONTEXT_OVERFLOW` → `finish_reason=context_overflow`。
- **事件**：SSE `compaction`（触发运行的 run_id）；WS `agent_event.compaction_created`（摘要运行 run_id + 7 字段）。
- **前端**：标记插在 `upto_seq` 之后（按块渲染避免拆开工具配对）、`invalidated` 不显示、摘要可展开、degraded 徽章；`/compact` 成功/跳过/失败三种 Toast；`compaction_created` 只失效对应会话 checkpoints。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | settings/schema/domain/db `877 passed`；`create_app()` ok；schema 76 项含三键 |
| G2 | `test_tool_catalog` 18 passed；计数 `93 48`；48 个均 `read_only` |
| G3 | `tests/agent/llm tests/agent/specs` `78 passed`；注册表 `background compaction_summary True` |
| G4 | `tests/agent/context` 19 passed；harness 76；parity 17；`tests/agent` 1078 passed |
| G5 | `tests/agent/context` 29→30 passed；`tests/agent` 1088 passed；parity 17；trim 只剩 legacy |
| G6 | 路由 4 passed；`tests/api tests/domain` 995 passed；openapi/genapi 0 |
| G7 | gen/typecheck/vitest（97 文件 881 用例）/build/frontend_smoke 全 0 |
| T9a | compaction_flow 5 passed；context 30 passed；未发现生产缺陷 |

### 最终验收（T9b，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | 见「合并后复验」；与 dev 起点（3 failed / 4475 passed）相比不新增失败 |
| `scripts/dump_openapi.py --check` | 0（146 operations） |
| `scripts/generate_api_guide.py --check` | 0（新端点后已重新生成） |
| 前端五步 | 0；vitest 97 文件 **881** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke` | 均 `SMOKE OK` |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 手动验收（真实 LLM 下连派工单看标记、按 ID 追问） | **未验证**（本会话无常驻服务与真实 LLM）；长会话用例与压缩链路测试覆盖等价断言 |

### 计数

- 业务表 38→**39**（`compaction_checkpoint`）；错误码 141/140→**142/141**（+1）；HTTP 端点 144→**146**（+2）；配置键 +3；内置定义 15→**16**（`summarizer`）；工具 93 不变（48 可重取）；迁移 head **`0028_compaction`**；OpenAPI tag 19 不变。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | legacy 保留字符预算，非 legacy 切 token 预算 | spec 非目标；parity 逐字节（13 §8.58） |
| R2 | `refetchable` 赋值时改 `test_extension_fields_keep_v2_defaults` 的过时断言 | 契约变化必然联动（13 §8.59） |
| R3 | 定义清单 15→16 同步 specs/API 测试 | 事实变化（13 §8.60） |
| R4 | 中间件链位 6/7/8 的既有断言同步 | 05 §5.2 序号是契约（13 §8.61） |
| R5 | 前端 compaction 单独归一 + 标记按块渲染 | 不改既有 SSE 语义、不拆工具配对（13 §8.62） |
| R6 | 一个会话一个 active 由事务保证，不加唯一索引 | 03 无该索引（13 §8.63） |
| R7 | 冒烟定义清单与 API 指南联动更新 | 事实变化（13 §8.64） |
| R8 | 真实 LLM 手动验收记「未验证」 | 无服务/真实 key；等价断言覆盖 |

## 偏离（已记 13 §8.58–8.64）

- §8.58 legacy 预算保留；§8.59 `refetchable` 断言联动；§8.60 定义清单联动；§8.61 链位同步；§8.62 前端归一与渲染；§8.63 唯一 active 实现；§8.64 冒烟清单与 API 指南。

## 遗留

- 真实 LLM 手动验收未做（连派工单看标记、展开摘要、按 ID 追问）；需在配好真实模型的服务上补，并记入下一里程碑台账。
- 摘要质量依赖 `summarizer` 提示词与模型；capability 评测已覆盖工具/行为，摘要语义质量尚无自动评测（M87 验证闸门/后续评测扩展）。
- 工具结果清理按 6 往返成批推进的「重复重取」风险需在真实使用中观察；已按 06 §5.2 设计并记台账。
