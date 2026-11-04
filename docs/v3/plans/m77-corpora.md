# M77 语料扩充 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 MAA 文档、PRTS、拆包数据、作业站四个外部语料接进 M76 检索层：`maa-docs`/`prts`/`gamedata`/`copilot-site` 四来源带许可与开关；唯一调度器上 `maa-rag-refresh`（`rag.refresh_cron`，默认 `0 4 * * *`）+ 未建索引来源启动 120 秒后首同步；增量（版本/`content_hash`/上游删除）；`get_prts_page` 页面进 `prts`（≤500 页）；四语料开关；PRTS 许可核对记台账（13 Q9）。

**Architecture:** G1 外部访问层（T2）+ G2 页面/干员小改（T3）并行 → G3 来源协议扩展与 refresh（T4）→ G4 四个语料来源与夹具（T5）→ G5 设置/装配/工具描述（T6）→ G6 前端（T7）→ G7（我：文档、全量验收、合并 tag）。

**Spec:** [m77-corpora](../specs/m77-corpora.md)（567 行）

## 开工基线（2026-11-04）

- 分支 `corpora`，基点 `dev` = `972fe7c`（M76 合并后测试加固）；tag `v3-m76`；**无迁移**（head 仍 `0030_knowledge_index`）。
- §0：1–12 符合（M76 indexer 已有 `CorpusSource`/`KnowledgeIndexService.register`/`refresh_all` 缺；同步按 20 个一批；`setting_key` 已参与检索过滤；四个外部客户端/唯一调度器/pages 函数/operators 表/activities/工具描述均在）；基线 `3 failed / 4638 passed`（同一组真资源失败）。
- **T1 联网核对（已跑，全部符合）**：PRTS `rightsinfo` = CC BY-NC-SA 4.0（creativecommons.org/licenses/by-nc-sa/4.0/）；`robots.txt` 的 `User-agent: *` 段只禁 `/index.php`、`/images/` 与特殊页面，**没有** `Disallow: /api.php`；GitHub 仓库 `default_branch=dev-v2`、`license.spdx_id=AGPL-3.0`。→ 按 spec 继续，证据入台账。
- **接缝裁决（记 13 §8.88–8.92）**：
  1. 来源行只在 `KnowledgeIndexService.start()` 的 `ensure_source` 里创建，而 spec §4.5 在 `start()` 之前调 `never_indexed()`——全新库会读到空。实际：**在 `await knowledge_index_service.start()` 之后**再快照 `never_indexed()` 并注册 `maa-rag-first-sync-*` 一次性任务（APScheduler 运行中可 `add_job`），定时任务注册顺序不变。
  2. 仓库**没有** `app.state.activity_service`（只有 `activity_index`）：`CopilotSiteCorpusSource` 在未注入时内部回落 `default_activity_service()`；装配处仍传 `getattr(app.state, "activity_service", None)`。
  3. `tests/services/test_external_client.py` 的位置快照钉死 `SOURCES` 5 项；新增 `maa_docs`/`gamedata_corpus` 后必须同步为 7 项（只改期望/名单）。
  4. `ENV_OVERRIDES` 由 `SETTING_KEYS` 自动派生；新增 5 键后要同步 `tests/test_settings.py` 的**钉死表**与断言，不改派生逻辑。
  5. 文档：16 已列 `fastly.jsdelivr.net`/`api.github.com`（M29 名下），M77 只需补用途说明与刷新/开关位置；04 已有 M77 三行（规格说两行，实际行数取文档现状，不改）。

## Global Constraints

- **不加表/端点/错误码/迁移**；配置键 +5；工具、端点、错误码、表数不变。
- 四来源文档只存本机库（V3-D43）；日志不得记录外部正文；结果带许可与出处。
- 不取 `stage_table.json`/`skill_table.json`，卡片不写属性数值；作业站不索引 `actions`、不按浏览量/好评率排序；不支持 GitHub token。
- 四来源均 `trusted=False`、`searchable_by_default=True`、`scheduled_refresh=True`；关闭开关后不抓取、不参与检索。
- 增量语义：来源版本未变不下载、`content_hash` 未变不重建、上游删除随同步删、失败保留旧文档。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m77/`。
- 不 push；提交 `<类型>(m77): <中文摘要>`；最后合并回 `dev` 打 `v3-m77`。

## Review Focus

1. **外部访问层**：两个 `SourceSpec`（容量/超时/限流/缓存级别/许可）、`resolve_source` 的 gamedata 两处分支；`get_json(cache=False)` 不读不写不回退；`GamedataClient.corpus_table` 只走镜像、无缓存、无 GitHub 兜底；`MaaDocsClient` 三端点 URL/base64 解码/truncated/403 改写；既有 excel/level 行为不变。
2. **页面/干员小改**：`clean_page_text` 与私有清洗一致；`add_listener` 在 `_load` 成功后拿到 `(WikiPage, fetched_at)`，监听器异常不影响返回；两个公开别名。
3. **索引扩展**：`push_documents` 等锁、不删、写完补向量；`refresh_all` disabled→skipped、单错不影响下一个、single-flight、最后补向量；`never_indexed` 只含开关为真且未建索引；`prune` 只在成功后调用并计入 `deleted`。
4. **四来源**：MAA 文档只 `.md`、<200B 跳过、版本=blob sha、二次同步不下载；PRTS 20/批、missing 跳过、随读监听与开关、90 天/max_pages prune；拆包模板与筛选（不可获得/NONE/隐藏敌人）、富文本清理、无属性字段、版本未变不重建、单表失败不删；作业站关卡合并去重、卡片无 actions/计数、活动失败不删。
5. **定时与装配**：`maa-rag-refresh` 存在且触发器来自 `rag.refresh_cron`（解析失败回落默认并 warning）；首同步任务在 `start()` 之后注册且仅从未建索引来源、120s 后；六来源列表 `setting_enabled` 正确。
6. **前端**：来源行「许可：」小字（空不显示），`setting_enabled=false` 时开关禁用与提示；测试覆盖。

---

### Task 0: 分支、§0、T1 核对、计划入库（已完成）

- [x] 建 `corpora`；§0 十三条；T1 三条联网核对（符合）；裁决写入本计划；提交计划。

### Task 1（G1）: 外部访问层（spec T2）

**Files:** `maa_api/services/external/sources.py`、`http.py`、`gamedata.py`、新建 `maa_docs.py`、新建 `tests/services/{test_maa_docs_client,test_gamedata_corpus,test_external_client_cache_flag}.py`、`tests/services/test_external_client.py`（位置快照 5→7）

- [ ] 两个 `SourceSpec`（逐字：hosts/paths/params/max_bytes/超时/限流/并发/缓存级别/许可）+ `GAMEDATA_CORPUS_FILES` + `resolve_source` 的 gamedata 两处分支与 `__all__`。
- [ ] `get_json(..., cache=True)`；`GamedataClient._fetch(kind/cache/allow_github)` 与 `corpus_table`/`validate_corpus_table`；`MaaDocsClient`（docs_dir/tree/blob、403 改写）；既有 excel/level 不变。
- [ ] 三个新测试文件（§6 全部用例，`httpx.MockTransport`）与 SOURCES 快照更新。
- [ ] 验证：`pytest tests/services > /tmp/m77-t2.log` → 0。
- [ ] 提交：`feat(m77): 外部访问层与 MAA 文档客户端`

### Task 2（G2）: 页面与干员服务小改（spec T3）

**Files:** `maa_api/services/game/pages.py`、`operators.py`、新建 `tests/services/test_pages_listener.py`

- [ ] `clean_page_text`、`PageService.add_listener`（`_load` 成功后逐个 await、异常只 debug）、`PROFESSION_CN`/`POSITION_CN` 别名。
- [ ] 验证：`pytest tests/services/test_pages_listener.py tests/services/test_game_pages.py tests/agent/test_game_tools.py` → 0。
- [ ] 提交：`feat(m77): 页面监听与干员中文名别名`

### Task 3（G3）: 来源协议扩展与刷新（spec T4）

**Files:** `maa_api/agent/knowledge/index/indexer.py`、新建 `tests/agent/knowledge_index/test_rag_refresh.py`

- [ ] `push_documents`（等来源锁、同规则写、不删、排 `ensure_embeddings`）、`refresh_all`（rag disabled→空；scheduled_refresh/已启用/开关真；单错继续；single-flight；最后补向量）、`never_indexed`；可选成员 `scheduled_refresh`/`start(index)`/`close()`/`prune(config)` 的调用点（`start/close/sync 成功后`）。
- [ ] 验证：`pytest tests/agent/knowledge_index` → 0（M76 用例不变）。
- [ ] 提交：`feat(m77): 来源推送与定时刷新`

### Task 4（G4）: 四个语料来源（spec T5）

**Files:** 新建 `maa_api/agent/knowledge/sources/{__init__,maa_docs,prts,gamedata,copilot_site}.py`、新建 `tests/agent/knowledge_sources/`（4 个测试文件）、新建 `tests/fixtures/corpora/`（手写小夹具，不放真实大表）

- [ ] 四个来源类（id/title/license/setting_key/full_listing/trusted/searchable_by_default/scheduled_refresh、`validate_config` 规则、`list_refs`/`fetch`/`prune`/`start`/`close`）+ `build_external_sources`；MAA 文档 43 篇口径/PRTS 20 批与随读/拆包模板与筛选/作业站关卡与卡片。
- [ ] 验证：`pytest tests/agent/knowledge_sources` → 0；无 skip/importorskip。
- [ ] 提交：`feat(m77): 四个外部语料来源`

### Task 5（G5）: 设置、装配与工具描述（spec T6）

**Files:** `maa_api/settings.py`、`services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`maa_api/main.py`、`maa_api/agent/tools/knowledge.py`（只改描述）、新建 `tests/api/test_rag_refresh_wiring.py`

- [ ] `RagSettings` 5 键 + cron 校验器；`_FIELDS` 五项（含 restart_required）；模板注释；测试（钉死表 + cron 拒绝）；`search_knowledge` 描述逐字。
- [ ] `main.py`：装配 `build_external_sources`（activity 回落到 `default_activity_service`）；`start()` 之后注册 `never_indexed` 首同步（120s）；`maa-rag-refresh` cron（解析失败回落+warning，`replace_existing`/`max_instances=1`/`coalesce`）。
- [ ] 验证：`pytest tests/test_settings.py tests/test_vapid_settings.py tests/api/test_rag_refresh_wiring.py tests/agent/test_search_knowledge_tool.py` → 0；`agent_smoke.py` → `SMOKE OK`。
- [ ] 提交：`feat(m77): 语料开关、定时刷新与装配`

### Task 6（G6）: 前端（spec T7）

**Files:** `web/src/features/settings/knowledge/RetrievalSources.tsx`、`retrieval.test.tsx`

- [ ] 来源行「许可：{license}」（空不显示）；补 `setting_enabled=false` 禁用与提示的用例。
- [ ] 验证：`pnpm typecheck && pnpm test --run` → 0。
- [ ] 提交：`feat(m77): 检索来源显示许可`

### Task 7（G7，我）: 文档、全量验收与合并（spec T8/§9）

**Files:** `docs/v3/{07,16,02,13,README}`、新建 `docs/v3/ledgers/m77-corpora.md`

- [ ] 07 §3 与实现不符处；16 出站域名与刷新/开关说明；02 §4.1 `knowledge/sources/` 文件清单；13 §8.88–8.92；README；台账（T1 证据、接线裁决、手动联网验收未做说明）。
- [ ] 跑 §7：全量 pytest 不新增失败、openapi 不变、前端五步、三冒烟、regression/capability、断链、diff；联网补充验收记「未做/不可自动」。
- [ ] 提交 `docs(m77): 台账、契约同步与状态行`；`git merge --no-ff corpora` → tag `v3-m77` → 删分支 → 合并后复验 → 汇报。
