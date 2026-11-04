# M76 RAG 检索层（台账）

- 分支：`rag-index`；基点：`dev` = `3b23bcd`；合并：`dev` 上的 `merge: RAG 检索层（M76，见 docs/v3/13 §8.74–8.84）`；tag：`v3-m76`
- spec：[m76-rag-index](../specs/m76-rag-index.md)（892 行）
- 计划：[m76-rag-index](../plans/m76-rag-index.md)
- 决策记录：[docs/v3/13 §8.74–8.84](../13-决策记录.md)
- 迁移：`0030_knowledge_index`（可逆）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `6ab0ef6` | 计划入库；§0 十六条；22 处接缝核对与 5 项裁决；基线 `3 failed / 4572 passed` |
| G1（T1+T2） | `3470dfb` | `numpy>=2.0` 主依赖 + `embed-local` extra（锁文件已更新）；`RagSettings` 五项 + SETTING_KEYS/_FIELDS/模板/ENV；`KnowledgeSourceKind`；三表 + `KNOWLEDGE_FTS_DDL`/event.listen；迁移 `0030`（可逆、离线可渲染）；业务表 44 张 |
| G2（T3+T4） | `f60862a` | `chunker`（normalize/标题分节/重叠/签名）、`fts`（trigram/整词/LIKE 转义/bm25/snippet/来源过滤）、`vectors`（小端/缓存/top_k）、`embeddings`（Null/Remote/Local/Resolver，含成本 `on_usage` seam）；21 用例 |
| 加固 | `3676ab7` | 索引监听器用例 `_wait_until` 5s→20s |
| G3（T5） | `2956ef2` | `KnowledgeIndexRepository` 15 方法；`KnowledgeIndexService`（start/同步增量/忙脏/补向量冷却/监听）；`KnowledgeEntrySource`；`KnowledgeService` 加 listener/`list_effective` |
| G4（T6） | `7357c83` | `Retriever`（RRF k=60、同文档 ≤3、来源解析、向量四态、`took_ms`）；5 用例 |
| G5（T7） | `7931697` | 四个检索端点 + 两错误码（共 4，150/149）+ 14 清单（156 端点）+ 快照/类型；6 用例 |
| G6（T8+T9） | `0cc9166` | `search_knowledge` 工具（不可信信封）；三个定义 tools/version/提示词；lifespan 装配索引服务与检索器、监听器、两处关闭清理；工具 97/96；wiring 用例 |
| G7（T10） | `6c5257c` | 设置页「知识库」分区（URL tab）、来源列表（四态/计数/开关/重建轮询/Toast）、测试检索（结果/空态/失败）；前端 100 文件 **898** 用例 |
| G8 | 本台账所在提交 | 02/16/13/README 同步；13 §8.74–8.84；本台账；全量验收与合并 |

## 核心实现

- **FTS**：trigram 中文三字组 OR、ASCII 整词、`1-7` 连字符保留、≤2 字走 LIKE（`%_\` 转义 + ESCAPE）；bm25(text 1.0, heading 2.0)、snippet、标题命中优先。
- **索引**：启动 ensure 来源行 + reset_indexing + 后台同步；版本文档 hash + `chunker` 签名增量；`full_listing` 删过期；变更监听在 commit 后触发；忙时脏标记重排；失败置 error 保留旧文档；补向量分批 64、`unavailable` 冷却 600s。
- **向量**：小端 float32 BLOB、按 `index_version` 缓存矩阵、来源掩码 + 分数降序/id 升序；远端兼容 OpenAI `/embeddings`（批次 16、index 排序、重试一次、错误脱敏、429 retry_after）；本地 ONNX 懒加载（缺依赖/缺文件/池化/query_prefix/线程）；维度校验；Resolver 对 `AppError` 按缺失处理。
- **检索**：RRF 融合、同文档 ≤3、默认来源（启用+可检索+设置开关）、点名停用进 `skipped_sources`、`rag.enabled=false` 空结果。
- **工具**：`search_knowledge`（query/sources/top_k、note 逐字、不可信来源合并 `build_envelope`），总控/general/researcher 可用（version+1）。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | settings/db/enums 539 passed；`get_settings().rag` 五项默认；迁移 SQL 4 处 FTS；`app ok` |
| G2 | knowledge_index 21 passed；无 skip/importorskip |
| G3 | knowledge_index + knowledge_service 89 passed（连跑 3 次）；`tests/db/enums` 496 passed |
| G4 | retriever 5 passed；knowledge_index 33 passed |
| G5 | router+domain 473 passed；openapi/genapi 0；四路径齐 |
| G6 | 工具/目录/wiring 22 passed；`tests/agent` 1155 passed；`agent_smoke` `SMOKE OK`；97/96 |
| G7 | gen/typecheck/vitest（100 文件 898 用例）/build/frontend_smoke 全 0 |

### 最终验收（G8，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | 见「合并后复验」；与基线（3 failed / 4572 passed）相比不新增失败 |
| `scripts/dump_openapi.py --check` | 0（156 operations） |
| `scripts/generate_api_guide.py --check` | 0（新端点后重新生成） |
| 前端五步 | 0；vitest 100 文件 **898** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke` | 均 `SMOKE OK` |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 手动验收（真实索引/向量/本地 ONNX） | **未验证**（无常驻服务/真实模型）；自动用例覆盖等价逻辑，部署步骤写入 16 |

### 计数

- 业务表 41→**44**（+3，FTS 虚拟表/触发器不计）；错误码 146/145→**150/149**（+4）；HTTP 端点 152→**156**（+4）；工具 96→**97**（模型可见 95→**96**，可重取 48→49）；配置键 +5；迁移 head **`0030_knowledge_index`**；依赖新增 `numpy>=2.0` 与 `embed-local` extra。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | Resolver 捕获 `AppError` 当「档案缺失」→ Null/unavailable | spec 要求不报错（13 §8.74） |
| R2 | `ENV_OVERRIDES`/`SETTING_KEYS` 在 settings.py | 实际位置（13 §8.75） |
| R3 | 两处关闭流程与属性清理都加 | 避免只覆盖一半路径（13 §8.76） |
| R4 | 业务表测试名实修正为 44 | 实际表数（13 §8.77） |
| R5 | 前端保留 `Page` 外壳 | 页面框架不变（13 §8.78） |
| R6 | 先预置两错误码、G5 收敛到 150/149；Retriever 用 `index._embeddings` | 任务依赖顺序（13 §8.79） |
| R7 | 索引 `start()` 挪到启动 try 末尾 | 避开 schedule 装载的 SQLite 写锁；设计本就是后台索引（13 §8.80） |
| R8 | embedding 成本统一 `agent_name=knowledge_index` | provider 回调无调用方上下文；不引入 contextvar（13 §8.81） |
| R9 | 索引监听器等待与 wiring 轮询加固 | 测试时序（13 §8.82） |
| R10 | 工具/定义计数联动同步 specs 测试 | 事实变化（13 §8.83） |
| R11 | 真实环境手动验收记「未验证」 | 无服务/真实模型；部署步骤写入 16 |

## 偏离（已记 13 §8.74–8.84）

- §8.74 Resolver 缺失处理；§8.75 配置表位置；§8.76 两处关闭；§8.77 表计数名实；§8.78 前端 Page 外壳；§8.79 错误码预置与私有访问；§8.80 索引 start 时机；§8.81 embedding 记账调用方；§8.82 测试加固；§8.83 计数联动；§8.84 版本映射与 crash 退避再次加固。

## 遗留

- 真实环境手动验收未做（FTS 两字词、向量补全进度、本地 ONNX/远端 embedding 切换）；步骤已写入 [16 §15](../16-部署指南.md)，自动用例覆盖等价逻辑。
- embedding 成本记账的 `agent_name` 统一为 `knowledge_index`，未按调用方区分（M87/M94 可细化）。
- capability 评测只覆盖既有场景；RAG 检索质量（trigram 长句召回）未做量化评测，M94 计划再做查询改写/重排。
- `python -m pytest` 全量约 9 分钟；本机 3 条真资源失败与 M74 基线一致。
