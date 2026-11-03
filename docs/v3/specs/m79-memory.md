# M79 长期记忆（规格）

> 阶段：P2　依赖：M76　分支：`memory`　迁移：`0032_memory`
> 本文是 M79 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 分支干净 | `git status -sb` | `## memory` |
| 2 | M76 已合并 | `git tag -l 'v3-m76'` | `v3-m76` |
| 3 | 下一个迁移号 | `ls maa_api/db/migrations/versions/ \| tail -n 3` | 最大号小于 0032；不是就用下一个空号并记 13 §8 |
| 4 | 检索服务可写入 | `rg -n "class KnowledgeIndex" maa_api/agent/knowledge/` | 1 行。没有这个类名就记下实际类名，调用它的 upsert |
| 5 | 档案工具仍在 | `rg -n "update_user_profile" maa_api/agent/tools/` | 至少 1 行。记忆不取代档案 |
| 6 | 待你处理的数据源 | `rg -n "AttentionSource\|attention" web/src/features/console -g '*.ts*' \| head` | 记下现有来源列表，本里程碑追加一项 |
| 7 | 基线 | `.venv/bin/python -m pytest -q tests/agent/test_knowledge_service.py tests/agent/test_orchestrator_routing.py > /tmp/m79-base.log 2>&1; echo $?; tail -n 3 /tmp/m79-base.log` | 记下退出码 |

## 1. 背景与目标

M76 让资料可检索。用户偏好、设备环境、做过的决定仍然只活在某一段对话里，压缩或换会话后会丢。本里程碑按 [07 §5、§6](../07-知识RAG与记忆.md) 做长期记忆：

1. 工单结束与压缩之后，抽取候选记忆，用户确认后才入库。
2. 确认后的记忆按会话稳定地注入 system 第 4 段，并进入检索。
3. 历史流水线、确认、汇报以 `history` 来源进同一套索引。
4. `remember` 只提交候选，不直接生效。

## 2. 非目标

- 不建知识图谱、不做父子块与交叉编码（M94）。
- 不改 `update_user_profile` 的字段与确认卡。档案仍是偏好的结构化入口；记忆是散文。
- 不把候选推送到 Web Push 或 IM。只出现在待你处理。
- 不自动合并两条记忆。冲突时旧条保持 `active`，新条停在 `proposed`，由用户选「取代」。

## 3. 相关文档

- 表：[03 §10.2](../03-数据模型.md)。来源 `history`、`memory` 已在 [03 §9.1](../03-数据模型.md)。
- 端点与错误码：[04 §2.3](../04-接口与事件协议.md) 的 M79 行；`MEMORY_NOT_FOUND`、`MEMORY_NOT_PROPOSED`。
- 配置：[04 §6](../04-接口与事件协议.md) 的 `memory.*`。
- 抽取、注入、历史索引的规则全文：[07 §5、§6](../07-知识RAG与记忆.md)。
- 中间件与工具：[05 §2.2](../05-运行时与代理定义.md) 第 4 项 `MemoryInjectionMiddleware`；[05 §3.3](../05-运行时与代理定义.md) 的 `remember`；[05 §3.4](../05-运行时与代理定义.md) 的 `memory_curator`。
- WebSocket：`agent_event` 的 `memory_proposed`。

## 4. 设计

### 4.1 数据模型

`memory_item` 与 03 §10.2 逐列一致。枚举：

```python
class MemoryKind(StrEnum):
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"

class MemoryStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
```

`kind` 的含义沿用 07 §5：`semantic` 是稳定事实与偏好，`episodic` 是某次发生过的事，`procedural` 是用户确认过的做法。

迁移 `0032_memory` 建 `memory_item`。索引 `ix_memory_item_status_kind`、`ix_memory_item_updated_at`。若 03 §10.2 还列了别的索引，以 03 为准补上。

### 4.2 模块与文件

新建：

- `maa_api/agent/memory/service.py`：`MemoryService`
- `maa_api/agent/memory/extract.py`：`extract_candidates`
- `maa_api/agent/memory/history_index.py`：`HistoryIndexer`
- `maa_api/agent/loop/memory_inject.py`：`MemoryInjectionMiddleware`
- `maa_api/agent/specs/builtin/memory_curator.md`
- `maa_api/api/agent/memories.py`
- `web/src/features/more/MemoryPage.tsx`

修改：

- `maa_api/agent/loop/` 的中间件链：第 4 位插入注入，位于档案注入之后、技能索引之前。读现有顺序再插，不重排其他项。
- 工单进入终态（`done`、`failed`、`cancelled`，07 §5.2）时调用 `extract_candidates`。三种终态都抽。
- 总控生成压缩检查点后，抽取被压缩掉的那一段：用户消息与总控的回答。
- `diagnose` 的工具名单追加 `search_knowledge`（07 §6）。定义 `version` 加 1。
- `remember` 只加给总控和 `general`（05 §3.2：总控点名；`general` 接收之后各里程碑的通用工具）。六个专职 Worker 与子代理不加。

### 4.3 接口

只实现 04 §2.3 的 M79 四行，不另加 POST 创建或 confirm/reject 路径。

| 方法与路径 | 函数名 | 行为 |
|---|---|---|
| GET `/api/agent/memories` | `list_memories` | query：`status`、`kind`、`q`、`page`、`size`。不传 `status` 时返回全部状态 |
| POST `/api/agent/memories/{memory_id}/review` | `review_memory` | `{approved: bool, content?}`。仅 `proposed`。`approved=true` 改为 `active`，可改写 `content`；`approved=false` 改为 `rejected`。已不是 proposed 则 409 `MEMORY_NOT_PROPOSED` |
| PUT `/api/agent/memories/{memory_id}` | `update_memory` | 改已生效记忆的 `content`、`subject`、`expires_at`。不存在则 404 `MEMORY_NOT_FOUND` |
| DELETE `/api/agent/memories/{memory_id}` | `delete_memory` | 204，物理删除。03 §14 不自动删，用户在这里删 |

取代关系不由请求体传入。抽取结果里的 `supersedes` 落在行的 `supersedes_id` 上；`review` 批准时若 `supersedes_id` 指向一条 `active` 记忆，把那条改为 `superseded`。指向的记忆不是 `active` 就丢掉这个字段再批准，不新造错误码。

`MemoryOut` 的字段与 03 §10.2 的列一致：`id`、`kind`、`subject`、`content`、`status`、`confidence`、`source_ticket_id`、`source_session_id`、`supersedes_id`、`expires_at`、`last_used_at`、`use_count`、`reviewed_by`、`reviewed_at`、`created_at`、`updated_at`。没有 `pinned` 列，不要加。

### 4.4 配置项

只注册 04 §6 已列出的键，分组以 04 为准：

| 键 | 默认 | 含义 |
|---|---|---|
| `memory.enabled` | true | false 时不抽取、不注入，`remember` 返回工具错误 `memory_disabled` |
| `memory.auto_extract` | true | false 时只保留 `remember` 入口（07 §5.2） |
| `memory.require_review` | true | false 时提议直接 `active`，`reviewed_by=auto`，仍发 `memory_proposed` |
| `memory.max_injection_tokens` | 1500 | 注入预算，范围 0–8000，用 M67 的 token 估算 |

`episodic` 默认 90 天、提议 30 天未审改为 `expired`，写在行为里，不另造配置键。

### 4.5 行为与算法

规则以 07 §5、§6 为准。下面只写落地时容易看错的点。

**抽取**

`extract_candidates` 起一轮 `agent_run`，`kind=memory_extract`，代理 `memory_curator`，`model_role=memory`。没有工单会话时 `session_id` 用总控会话。输入包含：工单标题、委托、最终汇报、用户原话、Worker 结论、关键工具结果；同时给出可能相关的已生效记忆（带 id）和用户档案全文。输出 schema 与 05 §8 一致：

```json
{"items": [{"kind": "semantic", "subject": "preference", "content": "……", "confidence": 0.8, "supersedes": null}]}
```

`content` 超过 500 字的丢弃。`subject` 用 07 §5.1 的分类键。同种类、同 `subject`、规范化后内容相同的提议丢弃。`supersedes` 只能指向本次输入里给出的已生效记忆 id，否则丢掉该字段。`require_review=true` 时 `status=proposed`；false 时直接 `active` 并写入 `memory` 来源索引。

`cancelled` 也抽取。抽取失败只记日志，不改变工单终态与压缩结果。调用放在终态提交之后的后台任务里。

**`remember`**

参数 `kind`、`subject`、`content`。`confidence` 记 1。写入 `status=proposed`（`require_review=false` 时直接 active）。返回 `{id, status}`。不写档案。

**注入**

只作用于总控与 Worker，顺序见 05 中间件第 4 位。

1. 候选：`status=active` 且未过期。选择前把到期的 `episodic` 改为 `expired`。
2. 必选组：`semantic` 且 `subject` 为 `preference` 或 `account`，按 `confidence`、`updated_at` 排序，最多占预算的 40%。
3. 相关组：检索 `memory` 来源。Worker 的查询词是工单标题、委托与任务说明；总控的查询词是最新检查点摘要加最近三条用户消息。
4. 先必选再相关，token 累加超过 `memory.max_injection_tokens` 就停。
5. 第 4 段开头固定一句：「长期记忆（用户审核过；与工具现查结果或用户档案冲突时以它们为准）」。每条写种类、日期与内容。
6. 被注入的行刷新 `last_used_at`，`use_count` 加一。

选择结果按会话缓存：同一工单的 Worker 沿用第一轮的选择；总控在新会话、每次压缩后、或生效记忆有增删改时重选。缓存命中时第 4 段字节级不变。

**审阅**

待你处理增加来源 `memory_proposed`，文案「N 条记忆待审核」。卡片沿用档案确认卡的差异展示：新内容原样；有 `supersedes_id` 时旧内容加删除线。按钮：批准、改后批准（把改过的 `content` 传给 `review`）、拒绝。不推送。

**历史索引**

`HistoryIndexer` 写入来源 `history`，`trusted=false`，写入前过 `redact_remote_data`。文档与 07 §6 的表一致，`external_id` 用那一列：

| external_id | 何时写 | 正文 |
|---|---|---|
| `ticket:<id>` | 工单进入终态 | 标题、种类、终态、委托、全部汇报摘要、结果里的关键字段 |
| `checkpoint:<id>` | 检查点生成 | 摘要正文 |
| `incident:<session_id>` | 启动时扫一遍已有 v2 诊断会话，之后不再新增 | 标题与最后一条助手回答，不超过 4000 字 |

`history` 与 `memory` 默认不参与检索。索引失败重试三次，仍失败记日志。

**过期**

不另建保留任务。注入选择前把 `expires_at` 已到的记忆改为 `expired`。新建 `episodic` 时 `expires_at = now + 90 天`。`proposed` 且 `created_at` 早于 30 天的，在同一次选择扫描里改为 `expired`。`semantic` 与 `procedural` 不设 `expires_at`。

**WebSocket**

写入 `proposed` 或 `require_review=false` 直接生效时，广播 `agent_event`，`event=memory_proposed`，`data` 含 `id`、`kind`、`preview`（正文前 80 字）。不调用 `WebPushService`。

### 4.6 前端

`/more/memories` 渲染 `MemoryPage`：待审核、生效中、已归档（`rejected`、`superseded`、`expired`）。待审核的操作与待你处理相同，都调用 `review_memory`。生效中的行可以改内容、改过期时间、删除。

导航放在「更多」里，标签「记忆」。`memory_proposed` 使待你处理与本页的查询失效。

### 4.7 提示词与代理定义

`memory_curator.md`：

```yaml
name: memory_curator
title: 记忆整理
kind: background
description: 从一段对话里抽出值得长期记住的候选。不调用工具。
model_role: memory
tools: []
delegates: []
device: none
risk_ceiling: none
output_schema: memory_proposals
max_steps: 1
version: 1
```

正文：

```
只抽出用户明确说过、并且下次还会用到的事实、偏好或做法。
不抽游戏数值、不抽一次性的流水线结果、不抽档案里已经有的内容、不抽你自己的推测。
拿不准就不要写。一条记忆说一件事，用陈述句，不超过 500 字。
kind 只能是 semantic、episodic、procedural。subject 用输入里说明的分类键。
supersedes 只能填输入里给出的已生效记忆 id，没有就省略。
输出只有 schema 规定的 JSON。
```

`remember` 的工具说明写进工具注册的 description：

```
把一条你认为该长期记住的事实提交给用户确认。确认前不会生效。
只提交用户明确表达过的偏好或环境事实。不要用它记流水线结果。
```

`common.md` 加一句：「用户的稳定偏好若已在记忆段里，不要再问一次。记忆段没有的，不要当成用户说过。」

## 5. 实施任务

### T1 迁移与服务

- 写范围：模型、枚举、迁移、`MemoryService`、`tests/db/test_models.py`、`tests/agent/test_memory_service.py`。
- 步骤：按 §4.1、§4.3。`review_memory` 的状态机在服务里做完，路由只做校验与调用。
- 验证：`.venv/bin/python -m pytest -q tests/db/test_models.py tests/agent/test_memory_service.py`，退出码 0。

### T2 抽取与工具

- 写范围：`extract.py`、`memory_curator.md`、`remember` 工具、工单终态与压缩后的调用点、测试。
- 步骤：FakeLLM 返回两条候选与一次非法 schema。非法时重试一次，仍非法则零条写入且不抛到调用方。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_memory_extract.py`，退出码 0。

### T3 注入、历史索引、保留

- 写范围：`memory_inject.py`、`history_index.py`、中间件链、保留任务、测试。
- 步骤：注入预算按 §4.5。历史索引用假的终态事件驱动，断言 FTS 能按关卡代号查到流水线文档。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_memory_inject.py tests/agent/test_history_index.py`，退出码 0。

### T4 端点与前端

- 写范围：`api/agent/memories.py`、`MemoryPage.tsx`、待你处理来源、`events.ts`、`query-bridge.ts`、路由。
- 步骤：按 §4.3、§4.6。组件测试覆盖确认、拒绝、空状态。
- 验证：`.venv/bin/python -m pytest -q tests/api/test_memories.py`；`cd web && pnpm typecheck && pnpm test --run src/features/more`。

### T5 定义与计数

- 写范围：总控与 Worker 的工具行、`diagnose` 追加 `search_knowledge`、`common.md`、目录测试、错误码与路由计数。
- 步骤：模型可见工具数加 1（`remember`）。只改总控、`general`、`diagnose` 三份定义。`search_knowledge` 已在 M76 计入，不改总数。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_tool_catalog.py tests/agent/test_builtin_workers.py tests/domain/test_errors.py tests/domain/test_route_inventory.py`，退出码 0。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/test_memory_service.py` | `test_review_activates_and_indexes` | status、`memory` 来源能查到 |
| 同上 | `test_review_reject` | rejected，索引没有 |
| 同上 | `test_review_twice_conflicts` | 第二次 `MEMORY_NOT_PROPOSED` |
| 同上 | `test_remember_stays_proposed` | 不进索引，confidence 为 1 |
| `tests/agent/test_memory_extract.py` | `test_ticket_done_proposes` | 终态触发 |
| 同上 | `test_cancelled_ticket_also_extracts` | cancelled 也会抽取 |
| 同上 | `test_duplicate_subject_skipped` | 同 subject 同内容只一条 |
| 同上 | `test_bad_schema_does_not_fail_ticket` | 工单仍是 done |
| `tests/agent/test_memory_inject.py` | `test_preference_first_within_token_budget` | token ≤ 1500，preference 在 |
| 同上 | `test_same_ticket_reuses_selection` | 同一工单第二次 wrap 字节相同 |
| 同上 | `test_proposed_not_injected` | 正文不含候选 |
| `tests/agent/test_history_index.py` | `test_finished_ticket_is_searchable` | `external_id` 为 `ticket:<id>` |
| `tests/api/test_memories.py` | `test_review_missing_is_404` | `MEMORY_NOT_FOUND` |

## 7. 验收

照 12 §4。另：

1. 脚本化用例：工单结束 → 候选出现在 `GET /api/agent/memories?status=proposed` → `review` 批准 → 下一次总控运行的 system 第 4 段含该句，并以「长期记忆（用户审核过」开头。
2. 全程没有 Web Push 调用。测试里对 `notify_confirmation` 打桩，调用次数为 0。
3. `diagnose` 的定义含 `search_knowledge`，总控的定义不含。

## 8. 风险与回滚

- 抽取挂在工单终态上，模型慢会拖长终态请求。调用放在终态提交之后的后台任务里，接口先返回。任务失败只记日志。
- 回滚：revert，`alembic downgrade -1`。已写入的 `history` 块可留在索引里，来源过滤后不可见。

## 9. 文档同步

- 05 §3.2：总控行已有 `remember`；`general` 的「通用工具」说明覆盖它；diagnose 行追加 `search_knowledge`。不要给六个专职 Worker 加 `remember`。
- 05 §3.4 若还没有 `memory_curator` 的成段说明，按 §4.7 补一段。
- 错误码数、路由数、表数（加 1）、模型可见工具数（加 1）同步到对应测试与现行接口清单。
- 07 §5 的描述与实现不一致时，改实现回到 07，不改 07 的规则。差异记 13 §8。

## 10. 停下来问

- 03 §10.2 的列与本 spec §4.3 对不上，且无法用「以 03 为准」消化。
- 中间件链第 4 位已经被别的里程碑占用，插入会改变已规定的相对顺序。
- 想让 `require_review=false` 成为默认。
