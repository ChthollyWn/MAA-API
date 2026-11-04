# M76 RAG 检索层 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SQLite FTS5（trigram）+ BLOB 向量 + RRF 的混合检索：迁移 `0030` 建 `knowledge_source`/`knowledge_document`/`knowledge_chunk`/`knowledge_chunk_fts` 与三个触发器；内置与用户条目自动索引（增量、正文未变不重建）；`GET /api/agent/retrieval/search` 带出处/可信/融合分数（二字词走 LIKE）；`rag.embedding_provider` 在 none/remote/local 间切换且后台补向量、不可用时只走全文；`search_knowledge` 工具给总控/general/researcher；设置页「检索来源」视图（状态/计数/开关/重建/测试检索）；为 M77/M79 的 `CorpusSource` 铺路。

**Architecture:** G1 依赖/配置/枚举/表/迁移 → G2 分块+全文、向量+EmbeddingProvider → G3 索引服务+内置来源+listener → G4 Retriever → G5 端点/错误码/快照 → G6 search_knowledge 工具+定义+装配 → G7 前端 → G8（我：文档、全量验收、合并 tag）。

**Spec:** [m76-rag-index](../specs/m76-rag-index.md)（892 行）

## 开工基线（2026-11-04，§0 已跑）

- 分支 `rag-index`，基点 `dev` = `3b23bcd`（M75 合并）；tag `v3-m75`；迁移 head `0029_artifacts_plans`，下一个 `0030`。
- §0 全部满足：SQLite 3.49.1 支持 FTS5 trigram；numpy 2.4.6（经 scipy 传递，未显式声明）；poetry（`[tool.poetry.extras]`）；`ModelRouter.resolve(role)`/`client_for`；`CostLedger.record(run_id=None 可记)`；工具计数 **96/95**；三个代理定义在（orchestrator/general/researcher）；错误码 **146/145/20**；`KnowledgeService`（`INDEX_KINDS`/`FULLTEXT_KINDS`/`put_entry`/`delete_entry`，**无** `list_effective`/`add_change_listener`）；前端 `case 'knowledge'` 在（有 `Page` 外壳）。
- 基线：全量 pytest 与 `tests/agent` 记录在台账（预期 3 条既有真资源失败）。
- **接缝裁决（记 13 §8.74–8.79）**：
  a. `ModelRouter.resolve("embedding")` 在角色未设置/kind 不符时**抛** `MODEL_PROFILE_NOT_FOUND`；`EmbeddingResolver.current()` 必须捕获 `AppError` 并按「档案缺失 → Null + `unavailable`」处理，不能只判空。
  b. `ENV_OVERRIDES`/`SETTING_KEYS` 在 `maa_api/settings.py`（不在 `tests/test_settings.py`）；T1 只改 settings 与测试断言。
  c. 关闭流程有两处（启动失败路径与正常路径），`knowledge_index_service`/`retriever` 的 `close()` 与属性清理元组**两处都要加**。
  d. `tests/db/test_models.py` 的业务表断言名实不符（`..._26_business_tables` 而实际 41）；加 3 张后改为 **44** 并同步函数名/docstring。
  e. 前端 `SettingsPage` 的 `case 'knowledge'` 保留 `Page` 外壳，只把内容换成 `<KnowledgePartition />`；`sections.ts` 的 summary 改「知识条目、检索来源与用户档案」。
- 计数目标：工具 96→**97**（模型可见 95→**96**）；错误码 146/145→**150/149**；端点 152→**156**；业务表 41→**44**；配置键 +5；迁移 head **0030**；FTS 虚拟表与触发器不进 `BUSINESS_TABLES`。

## Global Constraints

- FTS 不进 SQLModel 元数据与 `BUSINESS_TABLES`；`create_all` 靠 `event.listen(..., after_create, DDL)` 建虚拟表/触发器。
- 迁移 `0030` 只建三表+FTS+触发器，`downgrade` 先触发器→虚拟表→索引→表；离线 `upgrade --sql` 可渲染；不在迁移里做查询。
- 不改 `KnowledgeService.render_context()` 与 `read_knowledge` 行为；不索引 `playbook`/`glossary`/`profile`（`FULLTEXT_KINDS` 排除）；不新增外部来源。
- 向量失败不影响全文（`EMBEDDING_UNAVAILABLE` 捕获为 `vector="unavailable"`）。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m76/`。
- 不 push；提交 `<类型>(m76): <中文摘要>`；最后合并回 `dev` 打 `v3-m76`。

## Review Focus

1. **FTS**：trigram 三字组 OR、ASCII 整词、`1-7` 连字符保留、短词 LIKE（≤2 字）、`%_\` 转义、标题命中优先、`snippet`/bm25 排序；`"`/`AND`/`(` 当普通字符；纯标点 400。
2. **索引**：启动 ensure+reset_indexing+后台同步；版本/正文 hash/分块签名增量；`full_listing` 删除过期文档；监听器在 commit 后触发；busy 记脏重排；失败置 error 保留旧文档；`ensure_embeddings` 分批、`unavailable` 10 分钟不重试。
3. **向量**：打包小端 float32、矩阵缓存按 index_version、top_k 过滤来源与 id 平局；远端 URL/批次/排序/重试/错误映射（不泄漏 key）；本地懒加载依赖/模型文件/池化/prefix；维度校验。
4. **Retriever**：RRF(k=60)、同文档 ≤3 块、来源解析（默认启用的可检索来源、点名停用进 skipped）、`vector` 四态、`rag.enabled=false` 空结果、二字词 LIKE。
5. **接口**：四端点校验在函数内（q 1–500、top_k 1–30、sources 已注册）+ 错误码/状态码/`Z` 时间；`RetrievalResult` 形状逐字。
6. **工具与定义**：`search_knowledge` 参数/描述/返回/`note`/不可信信封；三个定义 tools+version；工具计数 97/96。
7. **装配**：lifespan 第 9 步、监听器、两处关闭清理；真实 lifespan 后 sources 列出 builtin/user 且 builtin.doc_count>0。
8. **前端**：`?tab=sources` 分区、来源状态/计数/开关/重建（202+轮询+Toast）、测试检索（结果/空态/失败）、纯文案逐字。

---

### Task 0: 分支、§0、计划入库（已完成）

- [x] 建 `rag-index`，跑 §0 十六条；5 处接缝裁决写入本计划；提交计划。

### Task 1（G1）: 依赖、配置、枚举、表与迁移（spec T1+T2）

**Files:** `pyproject.toml`、`poetry.lock`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`maa_api/domain/enums.py`、`tests/domain/test_enums.py`、`maa_api/db/models.py`、新建 `maa_api/db/migrations/versions/0030_knowledge_index.py`、`tests/db/test_models.py`、`tests/db/test_initial_migration.py`、新建 `tests/db/test_knowledge_index_migration.py`

- [ ] `numpy>=2.0` 主依赖 + `embed-local` 可选组（poetry extras），按 M64 方式更新锁文件（离线失败则报告，不静默跳过）；`RagSettings` 五项 + `SETTING_KEYS` + `_FIELDS`（含 enum/范围/模板注释）+ 测试。
- [ ] `KnowledgeSourceKind`；三张表（列/索引/唯一/FK 照 §4.1）与 `KNOWLEDGE_FTS_DDL`/`event.listen`；迁移 `0030`（可逆、离线可渲染）；`test_models` 登记表 + FTS 断言 + 新迁移测试；`EXCEPTED_VALUES` 加枚举。
- [ ] 验证：`pytest tests/test_settings.py tests/test_vapid_settings.py tests/db tests/domain/test_enums.py` → 0；`alembic upgrade head --sql | grep -c knowledge_chunk_fts` > 0。
- [ ] 提交：`feat(m76): RAG 配置、枚举、三张表与 0030 迁移`

### Task 2（G2）: 分块与全文检索、向量与 EmbeddingProvider（spec T3+T4）

**Files:** 新建 `maa_api/agent/knowledge/index/{__init__,chunker,fts,vectors,embeddings}.py`、新建 `tests/agent/knowledge_index/{test_rag_chunker,test_rag_fts,test_rag_vectors,test_rag_embeddings}.py`

- [ ] chunker（normalize/chunk_markdown/签名/标题路径/代码块/超长段）、fts（`build_fts_plan` 四规则/`search_lexical` FTS+LIKE/转义/来源过滤/snippet）。
- [ ] vectors（pack/unpack/cache/top_k）、embeddings（Protocol/Null/Remote/Local/Resolver；远端 MockTransport、本地注入 loader、错误映射不泄漏 key、维度校验；`resolve("embedding")` 的 `AppError` 当缺失）。
- [ ] 验证：`pytest tests/agent/knowledge_index` → 0；无 skip/importorskip。
- [ ] 提交：`feat(m76): 分块、全文检索与向量 Provider`

### Task 3（G3）: 索引服务与内置来源（spec T5）

**Files:** 新建 `maa_api/agent/knowledge/index/{indexer,entries}.py`、新建 `maa_api/db/repositories/knowledge_index.py`、`maa_api/agent/knowledge/__init__.py`（只加 listener/`list_effective`）、新建 `tests/agent/knowledge_index/test_rag_indexer.py`、`tests/agent/test_knowledge_service.py`（追加用例）

- [ ] `KnowledgeIndexService`（start/close/register/source/request_sync/sync_source/ensure_embeddings/on_knowledge_changed/is_busy/index_version）、`KnowledgeEntrySource`、`KnowledgeIndexRepository` 全部方法、`KnowledgeService.add_change_listener/list_effective`。
- [ ] 测试 §6 的 7 条 indexer + 2 条 knowledge service；第二次同步 `rebuilt==0`。
- [ ] 提交：`feat(m76): 知识索引服务与内置来源`

### Task 4（G4）: Retriever（spec T6）

**Files:** 新建 `maa_api/agent/knowledge/index/retriever.py`、新建 `tests/agent/knowledge_index/test_rag_retriever.py`

- [ ] `RetrievalHit`/`RetrievalResult`/`rrf_merge`/`Retriever.search` 五步；§6 五条用例（RRF 平局、vector 四态、同文档 ≤3、停用来源 skipped、rag disabled 空）。
- [ ] 提交：`feat(m76): 混合检索器`

### Task 5（G5）: REST 端点与错误码（spec T7）

**Files:** `maa_api/domain/errors.py`、新建 `maa_api/api/routers/knowledge_index.py`、`maa_api/main.py`（include_router）、`docs/v3/14-现行接口清单.md`、`tests/domain/test_errors.py`、新建 `tests/api/test_knowledge_index_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`

- [ ] 4 错误码（404/409/503/400）+ 14 号文档 §4/§6 与计数；四端点（函数内校验、`RetrievalResult`/来源视图/更新/重建 202/409）；快照/类型。
- [ ] 验证：`pytest tests/api/test_knowledge_index_router.py tests/domain` → 0；`dump_openapi --check` 0；`pnpm gen:api:check` 0。
- [ ] 提交：`feat(m76): 检索端点与错误码`

### Task 6（G6）: `search_knowledge` 工具、三个定义与装配（spec T8+T9）

**Files:** `maa_api/agent/tools/knowledge.py`、`tests/agent/test_tool_catalog.py`、新建 `tests/agent/test_search_knowledge_tool.py`、三个代理定义、`maa_api/main.py`（装配）、新建 `tests/api/test_knowledge_index_wiring.py`

- [ ] 工具参数/描述/返回/note/不可信信封（`build_envelope`/`SourceRef`）；注册 `knowledge`/SAFE/read_only/refetchable/rest_visible；计数 97/96；三个定义 tools+version+提示词两句。
- [ ] 装配：`EmbeddingResolver`+`KnowledgeIndexService`+两来源+`start()`+监听器+`app.state.knowledge_index_service`/`retriever`；两处关闭清理。
- [ ] 验证：`pytest tests/agent tests/api/test_knowledge_index_wiring.py` → 0；`agent_smoke.py` `SMOKE OK`；真实 lifespan 后 sources 有 builtin/user 且 builtin.doc_count>0。
- [ ] 提交：`feat(m76): 检索工具、代理定义与索引装配`

### Task 7（G7）: 前端（spec T10）

**Files:** 新建 `web/src/features/settings/knowledge/{KnowledgePartition,RetrievalSources,RetrievalSearch}.tsx`、`retrieval-api.ts`、`retrieval.test.tsx`；改 `SettingsPage.tsx`、`sections.ts`

- [ ] 分区（URL `tab`）、来源列表（状态/计数/开关/重建轮询/Toast/Empty/Error）、测试检索（结果/分数/打开原文/空态/失败）；文案逐字；typecheck/vitest/build/frontend_smoke。
- [ ] 提交：`feat(m76): 检索来源设置页`

### Task 8（G8，我）: 文档、全量验收与合并（spec T11/§9）

**Files:** `docs/v3/{02,03,07,14,16,13,README}`、新建 `docs/v3/ledgers/m76-rag-index.md`、`scripts/agent_smoke.py`（若定义数/工具计数需同步）

- [ ] 07 与实现不一致处同步；16 部署指南加「知识检索与向量」；02 §2.2/§4.1/§5；13 §8.74–8.79；README；台账（基线、计数、手动验收未做说明）。
- [ ] 跑 §7：全量 pytest 不新增失败、openapi/genapi/指南、前端五步、三冒烟、regression/capability、断链、diff；手动项记「未验证」。
- [ ] 提交 `docs(m76): 台账、契约同步与状态行`；`git merge --no-ff rag-index` → tag `v3-m76` → 删分支 → 合并后复验 → 汇报。
