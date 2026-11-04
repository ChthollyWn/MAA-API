# M77 语料扩充（台账）

- 分支：`corpora`；基点：`dev` = `972fe7c`；合并：`dev` 上的 `merge: 语料扩充（M77，见 docs/v3/13 §8.88–8.93）`；tag：`v3-m77`
- spec：[m77-corpora](../specs/m77-corpora.md)（567 行）
- 计划：[m77-corpora](../plans/m77-corpora.md)
- 决策记录：[docs/v3/13 §8.88–8.93](../13-决策记录.md)
- 迁移：无（head 仍 `0030_knowledge_index`）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `706690b` | 计划入库；§0 十三条；T1 三条联网核对（全部符合）；5 项接缝裁决 |
| G1（T2） | `7e8cdc9` | `SOURCES` 加 `maa_docs`/`gamedata_corpus` 两登记与 `GAMEDATA_CORPUS_FILES`；`get_json(cache=False)`；`GamedataClient.corpus_table`（只镜像、无缓存、无 GitHub 兜底）；新 `MaaDocsClient`（目录/树/blob、base64、truncated、403 额度提示）；12 新用例 + SOURCES 快照 5→7 |
| G2（T3） | `70ba964` | `clean_page_text`、`PageService.add_listener`（`_load` 成功后 `(page, fetched_at)`，异常只 debug）、`PROFESSION_CN`/`POSITION_CN` 别名；3 用例 |
| G3（T4） | `e04d94b` | `push_documents`（等来源锁、不删、补向量）、`refresh_all`（single-flight、skipped/error、末位补向量）、`never_indexed`；来源可选 `start/close/prune` 调用点；4 用例 |
| G4（T5） | `7f87f5f` | 四来源 `MaaDocsSource`/`PrtsCorpusSource`/`GamedataCorpusSource`/`CopilotSiteCorpusSource` + `build_external_sources`；`tests/fixtures/corpora/` 手写夹具；21 用例 |
| G5（T6） | `5d75b8d` | `RagSettings` +5（四开关 + `refresh_cron` 校验）；`_FIELDS`/模板/测试；装配四来源；`maa-rag-refresh` cron + `start()` 后 120s 首同步 date job；`search_knowledge` 描述更新；3 wiring 用例 |
| G6（T7） | `ce44cb7` | 来源行「许可：{license}」小字；前端 100 文件 **899** 用例 |
| G7 | 本台账所在提交 | 02/07/16/13/README 同步；13 §8.88–8.93；本台账；全量验收与合并 |

## 核心实现

- **四来源**（均 `trusted=false`、可检索、参与定时刷新）：
  - `maa-docs`：GitHub API 目录+递归树+blob（base64），只 `.md` 且 ≥200 B（6 个目录页跳过，实际 43 篇），版本=blob sha，标题 frontmatter→`# `→文件名，许可 AGPL-3.0。
  - `prts`：10 个默认种子 + 库内最旧随读页，一次 20 标题，`missing` 跳过；`get_prts_page` 监听把整页按 `origin=read` 推入（≤500 页，90 天过期，移出种子清理），许可 CC BY-NC-SA 4.0。
  - `gamedata`：`character_table`/`item_table`/`enemy_handbook_table` 三表→实体卡（可获得干员、MATERIAL/CONSUME 道具、图鉴可见敌人），卡片只写描述性字段、不写属性数值，版本=卡片 sha16。
  - `copilot-site`：当期活动关卡 + 配置额外关卡（去重保序），每关 hot 前 10；卡片只用元数据，不写 actions/浏览/好评率（后两者只进 meta）。
- **刷新**：唯一 `AsyncIOScheduler` 上 `maa-rag-refresh`（默认 `0 4 * * *`，解析失败 warning 回落）；`start()` 后 120 秒给从未建索引的来源各排一次首同步；单来源失败不影响下一个，最后补向量。
- **开关**：四个 `rag.*_enabled`（copilot 默认关）+ 来源 `enabled`；关闭后 `refresh_all` 记 skipped、检索不查、不抓取。
- **页面/干员小改**：`clean_page_text`、`PageService.add_listener`、`PROFESSION_CN`/`POSITION_CN`。

## 验证证据

### T1 联网核对（2026-11-04，curl 实测）

| 项 | 结果 |
|---|---|
| PRTS `rightsinfo` | `https://creativecommons.org/licenses/by-nc-sa/4.0/`（CC BY-NC-SA 4.0） |
| PRTS `robots.txt` | `User-agent: *` 段允许 `/api.php?action=mobileview&`；只禁 `/index.php`、`/images/` 与特殊页面，**没有** `Disallow: /api.php` |
| MAA 仓库 | `default_branch=dev-v2`、`license.spdx_id=AGPL-3.0` |

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | `tests/services` 外部相关 143 passed；SOURCES 快照更新 |
| G2 | pages/game 74 passed |
| G3 | knowledge_index 37 passed；`tests/agent` 1159 passed |
| G4 | knowledge_sources 21 passed（无 skip）；knowledge_index 37 passed |
| G5 | 设置/wiring/工具 50 passed；`agent_smoke` `SMOKE OK`；六来源与 job 断言过 |
| G6 | typecheck/vitest（100 文件 899 用例）/build/frontend_smoke 全 0 |

### 最终验收（G7，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | **3 failed / 4682 passed**；与基线同一组真资源失败，无新增 |
| `scripts/dump_openapi.py --check` | 0（快照未变，156 operations） |
| `scripts/generate_api_guide.py --check` | 0（无端点变化） |
| 前端五步 | 0；vitest 100 文件 **899** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke` | 均 `SMOKE OK` |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 联网补充验收（起服务重建索引/检索命中/随读 +1/无重复 blob） | **未做**（无常驻服务与真实网络抓取环境）；自动用例覆盖等价逻辑 |

### 计数

- 配置键 +5；工具 97/96、端点 156、错误码 150/149、业务表 44、迁移 head 0030 **均不变**。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 首同步任务在 `start()` 之后注册 | 来源行在 `start()` 内创建（13 §8.88） |
| R2 | 作业站来源回落 `default_activity_service()` | 无 `app.state.activity_service`（13 §8.89） |
| R3 | SOURCES 快照 5→7 | 新登记项（13 §8.90） |
| R4 | 设置只改钉死测试表 | ENV 自动派生（13 §8.91） |
| R5 | T1 三条核对按实测结果继续 | 与 spec 期望一致（13 §8.92） |
| R6 | 文档按实际实现口径同步（43 篇等） | §9 要求（13 §8.93） |
| R7 | 联网补充验收记「未做」 | 无常驻服务/真实抓取环境 |

## 偏离（已记 13 §8.88–8.93）

- §8.88 首同步注册时机；§8.89 活动服务回落；§8.90 来源登记快照；§8.91 设置派生与钉死表；§8.92 T1 核对证据；§8.93 文档与计数。

## 遗留

- 联网补充验收未做（真实抓取四类语料、检索命中、随读 `prts` +1、MAA 文档无重复 blob 下载）；自动用例用夹具覆盖等价逻辑，真实抓取的限额/跳转/大表解析留待部署后人工确认（16 §7 已写出站域名与开关说明）。
- GitHub 匿名额度 60/h 与 v2 拆包兜底共用；首全量约 51 次，可能挤掉当小时兜底（spec §8 已记，未额外缓解）。
- 拆包大表解析内存峰值约百兆（只在定时/手动重建时）。
