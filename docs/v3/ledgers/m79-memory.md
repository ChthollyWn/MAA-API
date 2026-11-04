# M79 长期记忆（台账）

- 分支：`memory`；基点：`dev` = `b9c286e`；合并：`dev` 上的 `merge: 长期记忆（M79，见 docs/v3/13 §8.102–8.111）`；tag：`v3-m79`
- spec：[m79-memory](../specs/m79-memory.md)（291 行）
- 计划：[m79-memory](../plans/m79-memory.md)
- 决策记录：[docs/v3/13 §8.102–8.111](../13-决策记录.md)
- 迁移：`0032_memory`（可逆）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `207f095` | 计划入库；§0 七条；10 项接缝裁决；基线 `3 failed / 4720 passed` |
| G1（T1） | `68e0d2a` | `MemoryKind`/`MemoryStatus`；`memory_item`（03 §10.2 全列、4 个 SET NULL FK、status_kind+subject 索引）；迁移 0032；`MemoryService`（候选/审核/更新/删除/注入查询/过期/用量/事件/索引回调）；14 用例 |
| G2（T2） | `bc5d6a5` | `extract_candidates`（`memory_extract` 运行、schema、重试/丢弃/去重/supersedes 白名单、后台任务）；`memory_curator.md` 逐字；`remember` 工具（只提议/`memory_disabled`）；工单三终态与压缩后挂钩 |
| G3（T3） | `a8ab342` | `MemoryInjectionMiddleware`（第 4 段 memory 槽、固定开头、必选/相关/预算/缓存/过期/用量、span）；`HistoryIndexer`（ticket/checkpoint/incident、redact、重试）；history/memory 两个来源 + 装配；`remove_documents` 索引清理 |
| G4（T4） | `81085df` | 4 个记忆端点 + `MemoryOut`；`MemorySettings` 四键；记忆页（待审核/生效中/已归档）；导航「记忆」；`memory_proposed` 事件与失效；待你处理第四类来源「N 条记忆待审核」 |
| G5（T5） | `a92d4d4` | 总控/general 加 `remember`、diagnose 加 `search_knowledge`（version+1）；`common.md` 一句；计数（100/99、155/154、164、17 定义）；14 号文档同步 |
| G6 | 本台账所在提交 | 修检索来源计数 6→8；02/05/10/11/13/README 同步；13 §8.102–8.111；本台账；全量验收与合并 |

## 核心实现

- **数据**：`memory_item` 17 列（`source_ticket_id`/`source_session_id`/`source_run_id`/`supersedes_id` 四个 `SET NULL` FK，`kind`/`status`/`confidence`/`expires_at`/`last_used_at`/`use_count`/`reviewed_by`/`reviewed_at`），索引 `status_kind` + `subject`。
- **抽取**：工单 `done`/`failed`/`cancelled` 提交后后台起一轮 `memory_extract`（`memory_curator`、`model_role=memory`），输入含工单信息、已生效记忆（带 id）、用户档案；非法 schema 重试一次仍非法则零写入；失败只日志；总控压缩后抽被压缩段。
- **审核**：`proposed` → `review`（批准/改后批准/拒绝）；`supersedes` 只指向输入内 active 记忆，批准时旧条改 `superseded`、否则丢字段；`require_review=false` 时直接 active（`reviewed_by=auto`）并仍发事件；不推送 Web Push/IM。
- **注入**：仅总控/Worker；第 4 段 memory 槽以「长期记忆（用户审核过；与工具现查结果或用户档案冲突时以它们为准）」开头；必选组（preference/account）≤40% 预算、相关组检索 `memory` 来源；1500 token 默认（0–8000）；同一工单 Worker 缓存字节稳定，总控新会话/压缩/记忆变更重选；注入行刷 `last_used_at`/`use_count`；选择前把到期 episodic 与 30 天未审 proposed 置 expired。
- **历史**：`history` 来源写 `ticket:<id>`（终态）、`checkpoint:<id>`（压缩）、`incident:<session_id>`（启动扫 v2 诊断会话一次，≤4000 字）；`redact_remote_data`；失败重试 3 次；`trusted=false`。
- **检索来源**：`memory`（trusted=true、默认不参与检索）与 `history`（trusted=false、默认不参与检索）两个推送式 `CorpusSource`；删除/被取代时清索引。
- **前端**：`/more/memories` 记忆页（三组、批准/改后批准/拒绝、改内容/过期时间/删除）；待你处理第四类来源与文案；事件失效。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | models/memory_service/enums 296 passed；`tests/db` 410 passed；迁移空库升降级通过 |
| G2 | extract 8 passed；service+skill 25 passed |
| G3 | inject/history 16 passed；knowledge_index+skills 47 passed；相关 196 passed |
| G4 | 端点+注入+设置 64 passed；前端 memory+console 91 passed；`frontend_smoke` `SMOKE OK` |
| G5 | 目录/定义/错误码/路由 132 passed；openapi 0；`agent_smoke` `SMOKE OK` |

### 最终验收（G6，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | **3 failed / 4775 passed**；与基线同一组真资源失败，无新增 |
| `scripts/dump_openapi.py --check` | 0（164 operations） |
| `scripts/generate_api_guide.py --check` | 0（新端点后重新生成） |
| 前端五步 | 0；vitest 102 文件 **913** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke` | 均 `SMOKE OK` |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 脚本化验收 | 审核→下一次总控第 4 段以「长期记忆（用户审核过」开头且含该句（`test_approved_memory_enters_system_segment_4`）；工单终态→proposed（`test_ticket_done_proposes`）；`notify_confirmation` 打桩 0 次 |
| 手动验收（真实模型抽取/记忆页交互） | **未验证**（无常驻服务/真实 key）；自动用例覆盖等价逻辑 |

### 计数

- 工具 99→**100**（模型可见 98→**99**）；定义 16→**17**（`memory_curator`）；错误码 153/152→**155/154**；端点 160→**164**（agent 57→61）；业务表 45→**46**；配置键 +4；迁移 head **`0032_memory`**。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 路径按仓库实际布局 | spec 写的是规划目录（13 §8.102） |
| R2 | 注入中间件在 ContextLayout 后、Skills 前 | 无独立档案中间件（13 §8.103） |
| R3 | 保留总控的 `search_knowledge`，只给 diagnose 加 | spec §7 与现状冲突，按现状（13 §8.104） |
| R4 | 表/DTO 以 03 §10.2 为准 | 含 `source_run_id`、`subject` 索引（13 §8.105） |
| R5 | 新增 history/memory 两个来源；memory trusted=true | 检索器要求注册；07 §2.5（13 §8.106） |
| R6 | 事件 payload house style；文档 11 同步 `/more/memories` | 一致性（13 §8.107） |
| R7 | 抽取运行 session_id=None + meta 目标会话 | 避免活动运行唯一索引冲突（13 §8.108） |
| R8 | `memory_disabled` 作工具结果字符串 | 同 M75 口径，不改错误码（13 §8.109） |
| R9 | 指标以结构化日志代替导出 | 无指标设施（13 §8.110） |
| R10 | 检索来源测试 6→8 | G3 新增两个来源（13 §8.111） |
| R11 | 真实模型手动验收记「未验证」 | 无常驻服务；自动用例覆盖 |

## 偏离（已记 13 §8.102–8.111）

- §8.102 布局适配；§8.103 注入位置；§8.104 spec §7 冲突；§8.105 表/DTO 以 03 为准；§8.106 来源与 trusted/删除清理；§8.107 事件与文档 11；§8.108 抽取会话；§8.109 `memory_disabled`；§8.110 可观测；§8.111 计数。

## 遗留

- 真实模型抽取质量（是否只抽用户明确说过的事实）未用真实 LLM 验证；提示词按 spec 逐字，能力评测未覆盖记忆场景。
- `memory`/`history` 来源默认不参与检索，只有显式 `sources=` 才查；记忆注入的相关组依赖检索器。
- 指标以结构化日志代替（无指标导出设施）。
- 旧库升级后 `memory_item` 为空属预期；抽取从后续工单终态开始。
