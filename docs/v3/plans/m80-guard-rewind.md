# M80 提示注入防护与会话回溯 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提示注入防护（22 个工具打 `untrusted_output`、规则检测 + 包装 + 降权/强制确认/拦截、`rules_llm` 复核、span/meta/审计）与会话回溯（`rewind`/`fork` 端点、被取代消息不进上下文、检查点作废/重建、副作用告知、前端四动作与折叠）。

**Architecture:** G1 迁移/模型 + 错误码/设置（T1+T2）→ 并行 G2 规则/包装（T3）→ G3 工具打标 + 强制确认（T4+T5）→ G4 中间件与接线（T6）→ G5 回溯/分叉/端点/快照（T7）→ G6 前端（T8）→ G7（我：文档、验收、合并 tag）。

**Spec:** [m80-guard-rewind](../specs/m80-guard-rewind.md)（900 行）

## 开工基线（2026-11-05）

- 分支 `guard-rewind`，基点 `dev` = `1c18880`（M79 合并）；tag `v3-m79`；迁移 head `0032_memory`，下一号 `0033`。
- §0 1–13 符合：`untrusted_output` 字段已在 registry 但**无任何工具赋值**；无 `InjectionGuardMiddleware`/`guard/` 包；四个中间件在（`harness/spill.py`、`harness/builtin_middleware.py`、`workers/middleware.py`）；`ToolOutcome` 可加字段；七表在；无回溯列；结构化入口 `complete_structured`（`llm/structured.py:173`）、`client_for`、`CostLedger.record`、`SpanRecorder`；前端消息列表在 `console/conversation/` 与 `features/tickets/`。全量基线见台账（预期 3 条真资源失败）。
- **接缝裁决（记 13 §8.112–8.121）**：
  1. **docs/09 §7 与 M80 spec 冲突**：09 §7.2–§7.4 写的是另一套打标格式（`<<<UNTRUSTED>>>`）、加权评分规则（3/3/2/2/1、≥3 判可疑）与降权算法（有效上限降为 none/原子授权不复用），与 spec §4.5 不同。裁决：**以本里程碑 spec 为唯一规格实现**，同步把 09 §7 改写为 spec §4.5 口径（打标格式、`guard/rules.py` 的 low/medium/high、EXEMPT/DENY/ALWAYS_CONFIRM + 逐次强制确认），并记 13 §8；用户若之后要 09 旧口径，再回改实现。
  2. **结构化输出**：spec 的 `injection_verdict.json` 与仓库惯例不符（`llm/schemas/*.py` pydantic + `register_schema`）。实际落 `maa_api/agent/llm/schemas/injection_verdict.py`（`InjectionVerdict` 模型 + register），在 `schemas/__init__.py` 注册。
  3. **中间件挂载点**：链装配在 `maa_api/agent/runtime.py` 的 `build_agent_chain`（16 项：… Plan 9、Spill 10、LoopGuard 11 …）。`InjectionGuardMiddleware` 插在第 10 位之后、LoopGuard 之前（即 05 §5.2 第 11 位），改 `runtime.py`；`context/spill.py` 实际为 `harness/spill.py`。
  4. **强制确认**：`ConfirmationService.invoke/_invoke` 加可选 `require_confirmation`（None 时与改动前逐字节一致）；新增 `_force_confirmation` 与 `record_rejection`（审计终态 `rejected`，不改 `agent_audit` 表结构）。
  5. **建单入口**：`TicketService.create` 加 `write_brief: bool = True`（默认行为不变），fork 传 `False`。
  6. **前端落点**：总控消息列表是 `features/console/conversation/ConversationView.tsx` + `use-conversation.ts`（无 `list_orchestrator_messages` 函数名），工单页在 `features/tickets/`；`transcript.ts` 的 `toolBody` 需导出或经 `buildTranscript` 带上 `guard`。
  7. **测试命名**：确认测试为 `test_confirmation_service.py`/`test_confirmation_origin.py`/…（spec 的 `test_confirmation.py`/`test_policy.py` 不存在）；迁移测试命名对齐 `test_rewind_migration.py`。
  8. **计数**：错误码 155/154→**157/156**（`REWIND_TARGET_INVALID` 400、`PROMPT_INJECTION_BLOCKED` 403）；端点 164→**166**（agent 61→63）；业务表不变；配置键 +2（`guard.injection_detection`/`guard.block_on_detect`）；工具总数与可见数**不变**（100/99），附录 A 的 22 个工具加 `untrusted_output=True` 并由测试整集钉死。
  9. **回溯列**：`AgentSession.forked_from_session_id`（FK SET NULL）/`forked_from_seq`、`AgentMessage.superseded_at`；不加索引；迁移 0033 可逆。
  10. **冒烟**：`orchestrator_smoke.py` 新增「提示注入打标与降权」「会话回溯与工单分叉」两步，末行仍 `SMOKE OK`。

## Global Constraints

- 不扫描截图/图片；不识别编码指令；不回滚任何副作用；不支持 v2 会话/子代理会话/后台运行；MCP 工具留给 M91；不改 `agent_audit` 表结构与终态行。
- 落库 tool 消息必须保持合法 JSON（前端 `parseJson`）；包装只发生在「给模型看的上下文」，落库内容不换。
- `guard.injection_detection=off` 时仍包装（`marked`），不扫描；`block_on_detect=true` 时可疑结果整段拦截（`PROMPT_INJECTION_BLOCKED`），审计仍 `success`。
- `rules_llm`：每轮最多 20 次、输入 ≤6000 字、超时 20 秒、两次不合法回退规则；成本记 `role='trigger_eval'`；span `guard.scan`/`guard.classify`。
- 回溯写锁顺序：先 `UPDATE ... superseded_at`（事务第一条语句）再查活动运行；运行中/待处理确认提问 409；`dry_run` 不写库。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m80/`。
- 不 push；提交 `<类型>(m80): <中文摘要>`；最后合并回 `dev` 打 `v3-m80`。

## Review Focus

1. **规则**：附录 B 20 条规则与样例（20 个 rule id 全命中、17 条游戏文本零误报、零宽字符归一化、`tool_steer_pattern` 只匹配 write tools、severity 聚合）；`collect_text` 跳过 base64/image/nonce 键、≤200k 截断。
2. **包装**：编号用落库 nonce、内部前缀替换、flagged 加安全提示、8 类中文名、摘录首条命中；`blocked` 无原始结果；非 JSON/旧消息原样；重复渲染字节相同。
3. **打标**：`is_untrusted` 规则（definition 标记 OR 结果顶层 `untrusted` 非 false）；22 工具整集相等；`read_artifact`/`search_knowledge` 动态 `untrusted`；外置保留 guard 与 `meta.untrusted`。
4. **降权**：`since_step` 语义（下一步起、同批后续不受影响）、继承（父 meta/历史 flagged 直到人类消息）、EXEMPT/DENY/ALWAYS_CONFIRM/read_only/可确认判定、拒绝审计、强制确认 payload.guard、`off` 不扫描仍 marked。
5. **复核**：命中/他人内容才调、`benign≥0.7` 只能撤中低危、高危不可撤、失败回退、上限、span 属性。
6. **回溯**：目标校验 7 种 reason、`superseded` 计数/事件、检查点作废与重建、计划放弃、副作用清单与提示正文、`dry_run`/noop、活动运行与待处理 409、写锁竞争。
7. **分叉**：标题/委托字段照抄、复制 `seq≤at_seq` 且未取代消息（seq 保持、run_id 空）、最新有效检查点 active、notice、`write_brief=False`、不复制订阅/租约/计划/产物。
8. **接口/前端**：两错误码/两端点/新字段（`superseded_at`/`forked_from`/SSE guard/WS `session_rewound`/确认 payload.guard）；四动作、折叠、GuardBadge、确认卡、运行中禁用、事件失效。

---

### Task 0: 分支、§0、计划入库（已完成）

- [x] 建 `guard-rewind`；§0 1–13；10 项接缝裁决写入本计划；提交计划。

### Task 1（G1）: 迁移/模型 + 错误码/设置（spec T1+T2）

**Files:** `db/models.py`、新建 `migrations/versions/0033_rewind.py`、`db/repositories/agent.py`、`domain/errors.py`、`settings.py`、`services/settings_schema.py`、`config.template.yaml`、`tests/db/test_models.py`、新建 `tests/db/test_rewind_migration.py`、`tests/domain/test_errors.py`、`tests/test_settings.py`、`docs/v3/14-现行接口清单.md`

- [ ] 三列与迁移（可逆、空库升降级）；`list_by_session(include_superseded=False)`；两错误码（157/156）；`GuardSettings` 两键 + `_FIELDS`/模板/测试；14 号文档错误码两行与总数句（端点计数留给 G5）。
- [ ] 验证：`pytest tests/db tests/domain/test_errors.py tests/test_settings.py tests/test_vapid_settings.py` → 0。
- [ ] 提交：`feat(m80): 回溯列、错误码与防护设置`

### Task 2（G2）: 规则与包装（spec T3）

**Files:** 新建 `maa_api/agent/guard/{__init__,rules,render}.py`、新建 `tests/agent/guard/{test_rules,test_render}.py`

- [ ] 附录 B 全文逐字（20 规则、常量、`normalize`/`collect_text`/`scan_text`/`is_suspicious`）；`render_tool_content` 六条规则与 flagged 安全提示。
- [ ] 验证：`pytest tests/agent/guard` → 0；样例全过、重复渲染字节相同。
- [ ] 提交：`feat(m80): 注入规则检测与外部数据包装`

### Task 3（G3）: 工具打标与强制确认（spec T4+T5）

**Files:** 附录 A 注册处、`tools/workspace.py`（read_artifact）、`tools/knowledge.py`（search_knowledge）、`tests/agent/test_tool_catalog.py`、`agent/confirmation.py`、新建 `tests/agent/test_confirmation_guard.py`

- [ ] 22 个工具 `untrusted_output=True`；两个动态 `untrusted`；`test_tool_catalog` 的 `UNTRUSTED_OUTPUT_TOOLS` 整集钉死；`require_confirmation`/`_force_confirmation`/`record_rejection`（默认 None 行为不变）。
- [ ] 验证：`pytest tests/agent/test_tool_catalog.py tests/agent/test_confirmation_guard.py tests/agent/test_confirmation_*.py` → 0。
- [ ] 提交：`feat(m80): 不可信工具打标与强制确认`

### Task 4（G4）: 中间件与接线（spec T6）

**Files:** 新建 `agent/guard/injection.py`、新建 `llm/schemas/injection_verdict.py`、`harness/context.py`、`harness/loop.py`、`harness/spill.py`、`harness/builtin_middleware.py`（Policy 传 require_confirmation）、`runtime.py`（链位）、`context/layout.py`、`agent/specs/common.md`、新建 `tests/agent/guard/test_middleware.py`

- [ ] `InjectionGuardMiddleware`（扫描/包装/降权/拒绝/强制确认/复核/span/meta）；`ToolOutcome.guard` 与落库/SSE/WS 带 guard；layout 包装；spill 保留；`common.md` 两段逐字；Policy 接线。
- [ ] 验证：`pytest tests/agent` → 0（含既有）；`tests/agent/guard/test_middleware.py` 全过。
- [ ] 提交：`feat(m80): 注入防护中间件与包装接线`

### Task 5（G5）: 回溯、分叉与接口快照（spec T7）

**Files:** 新建 `context/rewind.py`、新建 `api/routers/rewind.py`、`api/schemas/agent_v3.py`、`tickets/service.py`（write_brief）、`main.py`、`scripts/orchestrator_smoke.py`、新建 `tests/agent/context/{test_rewind,test_fork}.py`、新建 `tests/api/test_rewind_api.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14`、`tests/domain/test_route_inventory.py`

- [ ] `RewindService`（12 步、目标校验、检查点/计划、副作用与提示、事件、dry_run/noop/竞争）；`fork_ticket`（复制委托/历史/检查点、forked_from、notice、write_brief=False）；两错误码已由 G1 加；两端点（400/404/409/201+Location/operationId `agent_rewind_session`/`agent_fork_ticket`）；14 号文档两路由与计数 166；冒烟两步。
- [ ] 验证：`pytest tests/agent/context tests/api/test_rewind_api.py tests/domain/test_route_inventory.py` → 0；`dump_openapi --check` 0；`orchestrator_smoke` `SMOKE OK`。
- [ ] 提交：`feat(m80): 会话回溯、工单分叉与接口`

### Task 6（G6）: 前端（spec T8）

**Files:** 新建 `web/src/features/rewind/{api,targets,MessageActions,RewindDialog,EditResend,ForkDialog,SupersededFold,GuardBadge}.*` 与测试、`features/console/conversation/*`、`features/tickets/*`、`features/agent/transcript.ts`、确认卡、`realtime/{events,query-bridge}.ts`

- [ ] 四动作（回到这里/重新生成/编辑后重发/分叉）、dry_run→正式、折叠、GuardBadge/InfoHint、确认卡 InlineAlert、运行中禁用、`session_rewound` 失效；前端用例。
- [ ] 验证：`pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` → 0；`frontend_smoke` `SMOKE OK`。
- [ ] 提交：`feat(m80): 回溯与分叉前端`

### Task 7（G7，我）: 文档、全量验收与合并（spec §9）

**Files:** `docs/v3/{02,04,05,06,07,09,13,14,16,README}`、新建 `docs/v3/ledgers/m80-guard-rewind.md`

- [ ] 09 §7 按 spec 口径改写；02/04/05/06/07/14/16 同步；13 §8.112–8.121；README；台账。
- [ ] 跑 §7：全量 pytest 不新增失败、openapi/指南、前端五步、三冒烟（含新增两步）、regression/capability、断链、diff；能力评测若因包装下降先调 §4.7 措辞重跑，仍降则记录并停下来问。
- [ ] 提交 `docs(m80): 台账、契约同步与状态行`；`git merge --no-ff guard-rewind` → tag `v3-m80` → 删分支 → 合并后复验 → 汇报。
