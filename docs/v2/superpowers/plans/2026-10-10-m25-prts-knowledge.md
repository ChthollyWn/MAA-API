# M25 · PRTS 基础层、当期活动、关卡与通用检索 · 实施计划

规格见 [specs/2026-10-09-m25-prts-knowledge.md](../specs/2026-10-09-m25-prts-knowledge.md)（下文「§」都指该文件）。行为以规格为准；规格没覆盖到的实现细节由实现者决定并写进报告，主 agent 抄进台账。

## 全局约束

- 工作分支 `m25-prts-knowledge`（已从 `refactor/v2` 建立）。直接在当前检出上工作，不建 worktree，不 push，不合入 `dev`。
- 后端测试：`.venv/bin/python -m pytest -q`（`python` 不在 PATH 里）。
- 前端：`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`。
- 改了后端接口或工具目录后：`.venv/bin/python scripts/dump_openapi.py`、`cd web && pnpm gen:api`、`.venv/bin/python scripts/generate_api_guide.py`（顺序见 AGENTS.md）。
- **每项变更先写失败测试并确认失败原因（RED），再实现（GREEN）**；长输出重定向到文件，只读尾部和退出码。
- **实现者不要 `git commit` / `git add` / `git stash`**：并行任务会互相干扰，提交由主 agent 在审核通过、亲自复跑验证之后统一做。
- 不引入新依赖（httpx、pydantic、FastAPI 都已有）。
- **单测不打网**：全部用 `tests/fixtures/` 夹具与 `httpx.MockTransport`；真网络只在 `scripts/prts_smoke.py` 与手工验收里用。
- 依赖真机、模拟器或真实内核的检查只作补充证据。

任务依赖：T1 ∥ T2 ∥ T3 ∥ T4；T5 依赖 T3+T4；T6 依赖 T5；T7 依赖 T5+T6；T8 依赖 T6；T9 依赖 T3；T10 依赖 T7+T9；T11 依赖全部。

## 实测确认（T2，2026-10-10 主 agent 抓取）

| 结论 | 证据 |
|---|---|
| 活动 SMW 查询可用，返回 `timestamp`（Unix 秒）与 `raw`（UTC）；本次在开活动为「昨日海」（10-09 12:00 → 10-30 03:59）与「矢量突破 #3」（09-29 16:00 → 10-20 03:59） | `action=ask&query=[[分类:有活动信息的页面]][[!分类:集成战略]]|?名称缩短|?活动开始时间|?活动结束时间|sort=活动开始时间|order=desc` |
| 活动页关卡用 `{{关卡链接|代号|stageId?}}` 模板；`矢量突破/03` 写成 `{{关卡链接|VEC-01|act3break 01}}`（第二参数是 stageId，空格代替下划线），`昨日海` 只写代号 `{{关卡链接|YW-1}}` | 两份活动页 wikitext |
| 第二参数按「全小写字母/数字/下划线/空格」判定为 stageId，空格换下划线；含大写或中文时是真页面名 | `Template:关卡链接` 文档：第二参数为关卡 ID，也可用页面名称 |
| 只写代号时，代号是**重定向**：`YW-1` → `YW-1 互助会`；`action=query&titles=…&redirects=1` 一次解析 20 个 | 实测 `titles=YW-1|YW-2|YW-ST-1|VEC-01` |
| 关卡页的 SMW 属性 `关卡id` 就是 stageId：`[[关卡id::act3break_01]]` → `VEC-01 寂静螺旋`，带 `关卡名称`、`敌人数量` | 实测 |
| **SMW 的 OR 条件数量有上限**：8 个条件可用，12 个报「由于此wiki对查询大小或深度的限制」；`[[关卡id::act3break_*]]` 通配符返回 0 条（不支持） | 实测 2/4/6/8 与 12、通配符 |
| `list=search` 返回 `<span class="searchmatch">` 高亮标签，`爱国者` 的首位命中是 `7-18 爱国者之死` | 实测 |

分块上限按 **6 个条件**取（8 可用，留余量）。

## Task 1 · 修策略漏洞 D1（规格 §5.3）

**产物**：`maa_api/agent/policy.py`、`tests/agent/test_registry_policy.py`。

- `_consumption_reasons` 改按「内核实际会收到的参数」判断：`submit_pipeline` / `create_schedule` / `update_schedule` 用 `model_dump(exclude_unset=False)`；`set_task_params` 用「现有参数 + 本次修改」合并后的结果（取不到现有参数时按完整默认参数判断，fail closed）。
- 现有 `_as_json_value` 的 `exclude_unset=True` 是漏洞根源；改成带 `exclude_unset` 参数的内部函数。
- 正反例：省略 `shopping` 的 Mall 要确认、省略 `investment_enabled` 的 Roguelike 要确认、显式 `false` 不确认、Fight 计数默认 0 不确认。
- `set_task_params` 的合并：`PipelineRunner` 的现有参数从哪来——实现者读 `maa_api/services/pipeline_runner.py` 与 `tools/pipeline.py`，按实际可得的数据合并；拿不到就 fail closed。

## Task 2 · 夹具（规格 §5.2 T2、§4.9）

**产物**：`tests/fixtures/prts/`（`README.md` 记来源与抓取日期）。

- 活动 SMW 查询、`矢量突破/03`、`昨日海`、`7-18`（重定向请求）与 `7-18 爱国者之死`、一个活动关卡页（`VEC-08 磷脂层破坏`）、`爱国者` 搜索、分类查询、`YW-1` 重定向解析、关卡 id 的 SMW 反查。
- 裁剪成解析器真正读到的结构，但**保留原始 JSON 外壳**（`query.results` / `query.pages` / `query.search`），让访问层的响应校验也被夹具覆盖。
- 抓取脚本放 `scripts/fetch_prts_fixtures.py`（不打进测试路径，只作可复现的抓取入口）。

## Task 3 · 访问层与配置（规格 §3.2–§3.3、§4.6、附录 B）

**产物**：`maa_api/services/external/{__init__,sources,http,cache,status,envelope}.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`maa_api/domain/errors.py`、`tests/services/test_external_*.py`。

- `sources.py`：`SourceSpec`（kind、中文标签、hosts、paths、max_bytes、超时、限流、并发、缓存档）与登记表；M25 只登记 `prts`。`resolve_spec()` 每次现读设置（来源开关、每分钟上限）。
- `http.py`：`ExternalClient` 独占 `httpx.AsyncClient`；只发 GET；URL 由 spec + 参数字典拼装后用白名单复核；UA；代理按 `sources.use_proxy` + `app.proxy`；流式读响应，**解压后**计数超限中止；连接 5 s / 读取按来源 / 总时限 = 读取 + 15 s；连接错误与 502/503/504 重试一次（1 s + 抖动）；429 读 `Retry-After` 进冷却；连续 3 次失败熔断 60 s；令牌桶每分钟上限、取不到最多等 5 s；同键并发合并（single-flight）；过期值 24 h 内可回退并标 `stale`。
- `cache.py`：进程内 TTL + LRU（按字节估算，默认 64 MB），键 =（来源，规范化参数）；三档 TTL（`live`/`static`/`level`，M25 只用前两档 + `level` 默认 24 h）；统计条数、字节、命中率。
- `status.py`：每个来源的运行状态与 `snapshot()`（供 REST 与设置页）。
- `envelope.py`：来源信封（§3.4）的构造与合并。
- 错误码：`EXTERNAL_SOURCE_DISABLED`、`EXTERNAL_SOURCE_UNAVAILABLE`、`EXTERNAL_SOURCE_RATE_LIMITED`、`EXTERNAL_RESPONSE_INVALID`、`GAME_DATA_NOT_FOUND` 加进 `ErrorCode` 与 `ERROR_HTTP_STATUS`。
- 配置：`sources.enabled`、`sources.use_proxy`、`sources.cache_live_seconds`、`sources.cache_static_seconds`、`sources.cache_max_mb`、`sources.prts_enabled`、`sources.prts_rate_per_minute`（附录 B，M25 的键），同时登记 `SETTING_KEYS`、`Settings` 子模型与 `settings_schema`（分组「外部数据源」，全部 `hot_action="none"`）。
- 日志只记来源、端点键、状态码、耗时、字节数、是否命中缓存，不记正文。

## Task 4 · wikitext 解析（规格 §5.5）

**产物**：`maa_api/services/prts_parse/{__init__,wikitext}.py`、`tests/services/test_prts_wikitext.py`。

- `Template(name, args)`（命名 + 位置参数，保持顺序）、`parse_templates`、`find_templates`、`template_param`。
- 文本清洗：链接取显示名、展示类模板取文字、`<br>` 归一为换行、去掉 `<ref>` / HTML 注释 / 其余标签。
- 章节切分：`split_sections` 按 `==标题==` 层级。
- 纯函数，不联网、不读盘；边界穷举（嵌套模板、缺参、未知展示模板保守取最后一个位置参数）。

## Task 5 · PRTS 客户端与解析（规格 §5.4、§5.6）

**产物**：`maa_api/services/external/prts.py`、`maa_api/services/prts_parse/{activity,stage}.py`、`tests/services/test_prts_client.py`、`tests/services/test_prts_activity_parse.py`、`tests/services/test_prts_stage_parse.py`。

- `PrtsClient`：`ask`、`cargo`、`wikitext`（一次最多 20 标题）、`categories`、`search`、`sections`；固定 `format=json&formatversion=2`；标题与查询词按白名单字符校验；SMW/Cargo 查询只由固定模板拼装。
- 活动页解析：`{{活动信息}}` 字段（名称缩短、起止时间）+ `{{关卡链接}}` 的代号与 stageId 提示。
- 关卡页解析：`{{普通关卡信息}}` / `{{突袭关卡信息}}` / `{{敌方情报}}` / `{{关卡装置}}` 的字段白名单。

## Task 6 · 当期活动领域层（规格 §5.6）

**产物**：`maa_api/services/game/{__init__,activities}.py`、`tests/services/test_game_activities.py`、改造 `maa_api/agent/tools/activity.py`。

- 流程照 §5.6 五步；SMW OR 条件按 6 个一块分块（实测上限）。
- 关卡页标题解析：代号是重定向时先 `action=query&titles=…&redirects=1`；有 stageId 提示时用 `[[关卡id::<sid>]]` 反查页面标题。
- 与本机 `overview.json` 关联出 `levelId` 与 `navigation`（沿用 M24 规则）。
- 取不到就退回「代号 + 中文名」本地精确匹配，标 `matched_by`。
- 外服或 PRTS 不可用：返回 M24 本地快照，`source=resource`，加 `region_note` / `fallback_reason`。
- `get_activity_stages` 返回形状照 §5.6；`stage` / `prefix` 参数保留原语义。

## Task 7 · 三个新工具（规格 §5.7、§5.8）

**产物**：`maa_api/services/game/{stages,pages}.py`、`maa_api/agent/tools/game.py`、`maa_api/agent/tools/__init__.py`、`tests/agent/test_game_tools.py`、`tests/agent/test_tool_catalog.py`。

- `get_stage`：参数 `stage`、`include`；字段白名单 `basic`/`enemies`/`devices`/`drops`/`map`；PRTS 页面定位（SMW 反查 → 标题 → 搜索）；降级只给本机字段。
- `search_prts`：`query`、`limit`；结果带 `kind`（一次批量分类查询推断）、去高亮 `snippet`。
- `get_prts_page`：`title`、`section`、`offset`、`max_chars`；只允许主命名空间；返回重定向后标题、修订号、分类、`kind`、`fields`、`sections`、`text`（带截断信息）。
- 单个工具结果 ≤ 24 KB；列表有上限与 `total`。

## Task 8 · 注入与提示词（规格 §5.9–§5.10）

**产物**：`maa_api/agent/knowledge/__init__.py`、`maa_api/agent/runtime.py`、`maa_api/agent/knowledge/builtin/activity-stage-codes.md`、`tests/agent/test_knowledge_service.py`、`tests/agent/test_runtime.py`。

- 当期活动注入改多行（每个在开活动一行，最多 5 行，每行 ≤ 120 字）；标题按 PRTS / 降级 / 外服三种口径；3 s 预算，超时用旧值或本地兜底；读不到整段不插。
- 系统提示词新增「外部资料」段，插在「发东西给用户」与「数据边界」之间，正文与 §5.10 逐字一致。
- `activity-stage-codes` 补「以 PRTS 为准」的口径与新工具名。

## Task 9 · REST 与生成物（规格 §5.11、§4.7、附录 C）

**产物**：`maa_api/api/routers/sources.py`、`maa_api/main.py`、`docs/05`、`docs/14`、`scripts/dump_openapi.py` 快照、`scripts/generate_api_guide.py` 产物、`web/src/api` 生成类型。

- `GET /api/sources/status`、`DELETE /api/sources/cache?source=`。
- 端点不开 PRTS 代理；工具目录照旧出现在 `GET /api/agent/tools`。

## Task 10 · 前端（规格 §5.12）

**产物**：`web/src/features/agent/format.ts` 与测试、`web/src/features/agent/ToolCallBar.tsx` 与测试、`web/src/features/settings/SourcesStatus.tsx` 与测试、`sections.ts`、`web/src/features/settings/schema.test.ts`。

- `TOOL_LABELS` 补三个新工具；后端 `webpush_service._TOOL_LABELS` 同步（若无对应项则不加）。
- `ToolCallBar` 解析 `result.sources`，在折叠区之外渲染来源标签：来源名、页面标题（只为白名单主机生成外链，`rel="noreferrer noopener"`）、抓取时间、「缓存」/「旧数据」、外服提示。
- 设置页「外部数据源」分区与状态面板（每 30 s 刷新，含「清空缓存」）。

## Task 11 · 文档、冒烟与验收（规格 §4.10、§5.13、§5.15）

**产物**：`docs/13` §8.39、`docs/12` M25–M30 六节、`docs/11`、`docs/README`、`docs/15`、`docs/ENVIRONMENT.md`、`scripts/prts_smoke.py`。

- 验收命令照 §5.15 逐条跑；真网络冒烟作补充证据。
- 计划完成前把 `progress.md` 抄到 `docs/superpowers/ledgers/2026-10-10-m25-prts-knowledge.md`，含「Rulings I made」。

## 主 agent 亲自跑的验收

1. `.venv/bin/python -m pytest -q` 退出码 0。
2. `.venv/bin/python scripts/dump_openapi.py --check`、`.venv/bin/python scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`。
3. `cd web && pnpm typecheck && pnpm test --run && pnpm build`；`scripts/frontend_smoke.py`、`scripts/agent_smoke.py` 末行 `SMOKE OK`；`scripts/copy_inventory.py --check`。
4. `scripts/prts_smoke.py`（真网络，补充证据）。
5. `git merge --no-ff` 回 `refactor/v2`，打 tag `v2-m25`。

## 不做

干员、敌人、材料、卡池等专用工具（M26/M28）；其余 31 条知识条目（M30）；PRTS 的 REST 代理；`torappu.prts.wiki` 地图预览图。
