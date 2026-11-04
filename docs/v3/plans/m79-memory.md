# M79 长期记忆 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 长期记忆与历史检索：工单终态/压缩后抽取候选记忆（用户确认才生效）、按会话稳定注入 system 第 4 段并进入检索、`remember` 只提交候选、历史流水线/检查点/诊断会话以 `history` 来源进同一索引、记忆端点与 `/more/memories` 记忆页、待你处理 `memory_proposed`、`memory.*` 四个配置键。

**Architecture:** G1 表/迁移/MemoryService（T1）→ G2 抽取/curator 定义/remember/终态与压缩挂钩（T2）→ G3 注入中间件/history 索引/history+memory 检索来源/span 指标（T3）→ G4 端点/前端/待你处理/事件（T4）→ G5 定义与计数（T5）→ G6（我：文档、全量验收、合并 tag）。

**Spec:** [m79-memory](../specs/m79-memory.md)（291 行）

## 开工基线（2026-11-05）

- 分支 `memory`，基点 `dev` = `b9c286e`（M78 合并）；tag `v3-m78`；迁移 head `0031_skills`，下一号 `0032`；§0 七条全部符合（`KnowledgeIndexService` 在 `index/indexer.py:171`、`update_user_profile` 在 `tools/estate.py`、attention 现有三类 confirmation/question/ticket、基线定向 65 passed）。
- 全量基线：`3 failed / 4720 passed`（同一组真资源失败）。
- **接缝裁决（记 13 §8.102–8.111）**：
  1. **路径适配**：`agent/loop/memory_inject.py` → `maa_api/agent/memory/inject.py`（02 §4.1 已写）；`api/agent/memories.py` → `maa_api/api/routers/memories.py`；前端 `features/more/MemoryPage.tsx` → `web/src/features/memory/`（仓库现状）；路由仍 `/more/memories`。
  2. **中间件位置**：v3 没有独立的「档案注入中间件」（档案在 system 段文本里）；`MemoryInjectionMiddleware` 插在 `ContextLayoutMiddleware` 之后、`SkillsMiddleware` 之前（满足「技能索引之前」），写 system 第 4 段的 `memory` 槽（`SYSTEM_SEGMENT_ORDER` 已有），不改其他中间件相对顺序。
  3. **规格 §7 与现状冲突**：「总控定义不含 `search_knowledge`」不成立——总控自 M76 起就有；按现状（总控、general 已有，diagnose 本卡补），记录 13 §8。
  4. **以 03 §10.2 为准**：表列含 `source_run_id`；索引为 `ix_memory_item_status_kind` + `ix_memory_item_subject`（spec §4.1 写的 `updated_at` 索引不建）；`MemoryOut` 字段 = 03 §10.2 全部列（含 `source_run_id`）。
  5. **新增两个检索来源**：`history` 与 `memory` 的 `CorpusSource` 适配器（`searchable_by_default=false`、`trusted=false`、不参与定时刷新；写入走 `push_documents`），装配进 `main.py`；检索默认不查，显式 `sources=history`/`sources=memory` 才查。
  6. **事件载荷**：`agent_event` 用仓库 house style `{session_id, event, payload: {id, kind, preview}}`（04 的 `data` 即 payload）；前端 `memory_proposed` 使待你处理与记忆页查询失效。
  7. **计数**：工具 99→**100**（模型可见 98→**99**，`remember`；`v3_only` 加它）；错误码 153/152→**155/154**（`MEMORY_NOT_FOUND` 404、`MEMORY_NOT_PROPOSED` 409）；端点 160→**164**（agent 57→**61**）；业务表 45→**46**；配置键 +4；14 号文档总数句与错误码总数句同步（现文「共 150 条」陈旧，改为 155）。
  8. **可观测**：按 docs/10 实现 `memory.inject` span 与 `maa.memory.items`/`maa.memory.tokens` 指标（spec 未列，属既有可观测契约）。
  9. **docs 11 差异**：其 `/more/memory` 与「N 条记忆等你审核」以 spec 为准，文档同步为 `/more/memories`、「N 条记忆待审核」。
  10. **前端夹具**：`console/test-utils.tsx` 的 `ConsoleApiState` 增加 memories mock；`attention.ts`/`use-attention.ts` 增加 `memory_proposed` 来源。

## Global Constraints

- 迁移 `0032_memory` 可逆；`memory_item` 逐列照 03 §10.2（含三个 FK `SET NULL`、`supersedes_id` 自引用、`pinned` 不建）。
- 抽取失败只记日志、不改终态与压缩结果；放在终态提交之后的后台任务；`cancelled` 也抽；非法 schema 重试一次仍非法则零写入。
- 注入仅总控/Worker；`memory.enabled=false` 不抽不注；`remember` 在 disabled 时返回工具错误 `memory_disabled`；`require_review=false` 时直接 active、`reviewed_by=auto`、仍发事件。
- 历史/记忆来源 `trusted=false`，写入前过 `redact_remote_data`；`history`/`memory` 默认不参与检索；索引失败重试 3 次后记日志。
- 不推送 Web Push/IM；待你处理只多一个来源。`update_user_profile` 不变，记忆不写档案。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m79/`。
- 不 push；提交 `<类型>(m79): <中文摘要>`；最后合并回 `dev` 打 `v3-m79`。

## Review Focus

1. **表与服务**：列/FK/索引准确；`review` 状态机在服务（仅 proposed；true→active 可改 content；false→rejected；否则 409）；`supersedes` 只指向 active 旧条则改 superseded、否则丢字段；批准/`require_review=false` 写 `memory` 索引；`remember` confidence=1、只提议、不写档案。
2. **抽取**：一轮 `memory_extract`/`memory_curator`/`model_role=memory`；输入含工单信息+已生效记忆+档案；content>500 丢弃；同 kind+subject+规范化内容去重；supersedes 白名单；非法 schema 重试一次；后台任务、失败不扩散。
3. **注入**：第 4 段固定开头句逐字；必选组 ≤40% 预算、按 confidence/updated_at；相关组检索 memory 来源；token ≤ `max_injection_tokens`；同工单缓存字节稳定；总控新会话/压缩后/记忆变更重选；注入行刷 last_used_at/use_count；到期 episodic 与 30 天未审 proposed 在扫描中置 expired；`memory.inject` span 与两个指标。
4. **历史索引**：`ticket:`/`checkpoint:`/`incident:` 三种文档正文与触发时机；启动扫 v2 诊断会话、之后不新增；`<4000` 字；`ticket:` 可按关卡代号 FTS 命中。
5. **端点/前端**：4 端点与 2 错误码；列表不传 status 返回全部；记忆页三组（待审核/生效中/已归档）、确认/改后确认/拒绝、改内容/过期时间/删除；待你处理 `memory_proposed` 与文案；事件失效。
6. **定义与计数**：总控、general 加 `remember`；diagnose 加 `search_knowledge` 并 version+1；`common.md` 一句；工具/错误码/端点/表计数与文档同步。

---

### Task 0: 分支、§0、计划入库（已完成）

- [x] 建 `memory`；§0 七条；10 项接缝裁决写入本计划；提交计划。

### Task 1（G1）: 迁移与服务（spec T1）

**Files:** `maa_api/domain/enums.py`、`tests/domain/test_enums.py`、`maa_api/db/models.py`、新建 `maa_api/db/migrations/versions/0032_memory.py`、`tests/db/test_models.py`、新建 `maa_api/db/repositories/memory.py`、新建 `maa_api/agent/memory/{__init__,service}.py`、新建 `tests/agent/test_memory_service.py`

- [ ] `MemoryKind`/`MemoryStatus`；`memory_item` 表（03 §10.2 全列 + FK + 两索引）；迁移 0032（可逆、空库可升）；仓储；`MemoryService`（`list`/`get`/`create_candidate`/`review`/`update`/`delete`/`active_for_injection`/`mark_expired`/`record_use`/索引写入与事件注入点）。
- [ ] 验证：`pytest tests/db/test_models.py tests/agent/test_memory_service.py tests/domain/test_enums.py` → 0；`alembic upgrade head --sql | grep -c memory_item` ≥1。
- [ ] 提交：`feat(m79): 记忆枚举、表、0032 迁移与服务`

### Task 2（G2）: 抽取与 remember（spec T2）

**Files:** 新建 `maa_api/agent/memory/extract.py`、新建 `maa_api/agent/specs/builtin/memory_curator.md`、新建 `maa_api/agent/tools/memory.py`、`tools/__init__.py`、`maa_api/agent/tickets/service.py`（终态监听）、`maa_api/agent/context/compaction.py`（压缩后抽取）、新建 `tests/agent/test_memory_extract.py`

- [ ] `extract_candidates`（`memory_extract` 运行、schema、重试/丢弃规则、后台任务、失败只日志）；`memory_curator.md` 逐字；`remember` 工具（description 逐字、`memory_disabled`、只提议）；终态（done/failed/cancelled）与压缩后挂钩。
- [ ] 验证：`pytest tests/agent/test_memory_extract.py` → 0（FakeLLM 两条候选 + 一次非法 schema）。
- [ ] 提交：`feat(m79): 记忆抽取、curator 与 remember 工具`

### Task 3（G3）: 注入、历史索引与来源（spec T3）

**Files:** 新建 `maa_api/agent/memory/inject.py`、新建 `maa_api/agent/memory/history_index.py`、新建 `maa_api/agent/memory/sources.py`（history/memory 两个 `CorpusSource`）、`runtime.py`（链位）、`main.py`（来源注册、启动扫描）、新建 `tests/agent/test_memory_inject.py`、`tests/agent/test_history_index.py`

- [ ] `MemoryInjectionMiddleware`（第 4 段 memory 槽、必选/相关/预算/缓存/过期扫描/用量刷新/span+指标）；`HistoryIndexer`（ticket/checkpoint/incident、redact、重试 3 次）；两个来源适配器与装配。
- [ ] 验证：`pytest tests/agent/test_memory_inject.py tests/agent/test_history_index.py` → 0。
- [ ] 提交：`feat(m79): 记忆注入、历史索引与检索来源`

### Task 4（G4）: 端点与前端（spec T4）

**Files:** 新建 `maa_api/api/routers/memories.py`、`maa_api/domain/errors.py`、`maa_api/api/schemas/agent_v3.py`、`maa_api/main.py`（include_router）、`web/src/features/memory/{api,MemoryPage,memory.test}.*`、`web/src/routes/memories/index.tsx`、`router.tsx`、`nav.ts`、`events.ts`、`query-bridge.ts`、`features/console/{attention,use-attention}.ts`、`AttentionList.tsx`、`console/test-utils.tsx`、新建 `tests/api/test_memories.py`

- [ ] 4 端点（列表/审核/更新/删除）+ `MemoryOut`；2 错误码；`/more/memories` 记忆页（三组、批准/改后批准/拒绝、改内容/过期/删除）、导航「记忆」、`memory_proposed` 事件与失效、待你处理来源与文案。
- [ ] 验证：`pytest tests/api/test_memories.py` → 0；`cd web && pnpm typecheck && pnpm test --run src/features/memory` → 0。
- [ ] 提交：`feat(m79): 记忆端点、记忆页与待你处理`

### Task 5（G5）: 定义与计数（spec T5）

**Files:** `maa_api/agent/specs/builtin/{orchestrator,general,diagnose}.md`、`maa_api/agent/specs/common.md`、`tests/agent/test_tool_catalog.py`、`tests/agent/specs/*`、`tests/domain/test_errors.py`、`docs/v3/14-现行接口清单.md`、`web/openapi.json`、`web/src/types/api.d.ts`

- [ ] 总控/general 工具加 `remember`、diagnose 加 `search_knowledge` 且 version+1；`common.md` 一句；工具 100/99、错误码 155/154、端点 164（agent 61）、表 46 的测试与 14 号文档同步；OpenAPI/类型重生成。
- [ ] 验证：`pytest tests/agent/test_tool_catalog.py tests/agent/test_builtin_workers.py tests/domain/test_errors.py tests/domain/test_route_inventory.py` → 0；`dump_openapi --check` 0。
- [ ] 提交：`feat(m79): 记忆相关定义与计数`

### Task 6（G6，我）: 文档、全量验收与合并（spec §9）

**Files:** `docs/v3/{02,03,04,05,07,10,11,13,14,README}`、新建 `docs/v3/ledgers/m79-memory.md`

- [ ] 07 §5/§6 实现同步；05 §3.2/§3.4；02 §2.2/§4.1/§5；10 span/指标；11 记忆页；13 §8.102–8.111；README；台账（基线、裁决、手动项未验证）。
- [ ] 跑 §7：全量 pytest 不新增失败、openapi/指南、前端五步、三冒烟、regression/capability、断链、diff；脚本化验收（终态→proposed→批准→下一次总控第 4 段）尽量用自动用例覆盖，不调 WebPush 用打桩断言。
- [ ] 提交 `docs(m79): 台账、契约同步与状态行`；`git merge --no-ff memory` → tag `v3-m79` → 删分支 → 合并后复验 → 汇报。
