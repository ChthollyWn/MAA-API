# M24 · 当期活动与关卡代号实施计划

规格见 [specs/2026-10-09-m24-activity-knowledge.md](../specs/2026-10-09-m24-activity-knowledge.md)。行为以规格为准；规格没覆盖到的实现细节由实现者决定并写进报告，主 agent 抄进台账。

## 全局约束

- 工作分支 `m24-activity-knowledge`（已从 `refactor/v2` 建立，含规格与本计划）。直接在当前检出上工作，不建 worktree，不 push，不合入 `dev`。
- 后端测试：`.venv/bin/python -m pytest -q`（`python` 不在 PATH 里）。
- 前端：`cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`。
- 改了后端工具目录后：先跑 `.venv/bin/python scripts/dump_openapi.py`，再 `cd web && pnpm gen:api`，然后 `.venv/bin/python scripts/generate_api_guide.py`（本里程碑不新增端点与错误码，快照应有变化但仅限工具描述）。
- **每项变更先写失败测试并确认失败原因（RED），再实现（GREEN）**；长输出重定向到文件，只读尾部和退出码。
- **实现者不要 `git commit` / `git add` / `git stash`**：并行任务会互相干扰，提交由主 agent 在审核通过、亲自复跑验证之后统一做。
- 不引入新依赖。
- **源码与内置知识里不得出现任何当期数据**（「矢量突破」「VEC-」「act3break」等）：测试兜底断言。
- 网络不可作为事实来源：本里程碑不联网。
- 依赖真机的检查（Fight 真的能跑通活动关）只作补充证据，不作为硬性验证。

任务依赖：Task 1 → {Task 2, Task 3, Task 4}；Task 5 与 Task 1–4 并行；Task 6 依赖全部；Task 7 依赖 1–6。

## Task 1 · 活动索引服务（规格 §4.1）

**产物**：`maa_api/services/activity_index.py`、`tests/services/test_activity_index.py`。

- 层序沿用 `iter_resource_roots()` 的**反向**遍历（后加载的层覆盖前面的），找第一个存在 `version.json` 的层。
- 读三处：`version.json` 的 `activity.name` / `activity.time`、`Arknights-Tile-Pos/overview.json`、`tasks/Stages/<前缀>.json` 的**文件名存在性**。
- 推断规则严格照规格 §4.1：期数提取 → 前缀收敛 → 信心等级 → `related`；提不到期数一律 `none`，**不猜**。
- 按文件签名（dev/ino/size/mtime_ns）缓存；签名变化才重读（`update_resource` 之后同一进程内也要立刻反映）。
- 资源缺失/损坏抛 `RESOURCE_LOAD_FAILED`；单关查不到是**正常返回空数组**。
- 完成判据：`.venv/bin/python -m pytest -q tests/services/test_activity_index.py` 退出码 0；另跑一个临时脚本用真资源包打印当期活动与关卡首尾，贴进报告。

## Task 2 · 工具 `get_activity_stages`（规格 §4.2）

**产物**：`maa_api/agent/tools/activity.py`、`maa_api/agent/tools/__init__.py`、`tests/agent/test_activity_tools.py`、`tests/agent/test_tool_catalog.py`。

- 组 `activity`、`SAFE`、`read_only=True`、`model_visible=True`；参数 `stage: str | None`（1–32 字，`extra="forbid"`）。
- 返回值字段照规格 §4.2，`stages` 按代号数字升序、上限 60；`matches` 全部候选带 `is_current`。
- 服务从 `app.state.activity_index` 取，缺省回落到模块级默认实现；未装配且默认实现也读不到资源时抛 `RESOURCE_LOAD_FAILED`（与其它资源类工具一致）。
- `tests/agent/test_tool_catalog.py`：`EXPECTED_TOOLS` 加一条、条数 48 → 49，新增一条断言（组名、risk、read_only、参数 schema）。

## Task 3 · 当期活动注入与内置条目（规格 §4.3、§4.5）

**产物**：`maa_api/agent/knowledge/__init__.py`、`maa_api/agent/knowledge/builtin/activity-stage-codes.md`、`tests/agent/test_knowledge_service.py`、`maa_api/agent/knowledge/builtin/event-and-rerun.md`（只加一行指向新工具）。

- `render_context()` 在索引之后、术语表之前插「当期活动」一段（常量 `ACTIVITY_HEADER`），只放一行、≤120 字；读不到活动数据时**整段不插**。
- 活动数据经构造参数注入（缺省用 `activity_index` 的默认实现），读取失败只降级不抛。
- 新条目 `activity-stage-codes`（`kind: rule`）：只讲代号体系、重名与消歧、查法、两条限制；不含任何当期数据。
- 测试：`BUILTIN_IDS` 与 `BUILTIN_LIST_ORDER` 加一条；`_spec_blocks()` 改成读 3 份规格（M18 + M19 + M24）；新增「注入段顺序与内容」与「无活动数据时不插段」两条用例；把「源码/知识里不得出现当期数据」做成断言。

## Task 4 · `resolve_stage` 重名修正（规格 §4.4）

**产物**：`maa_api/services/stage_resolver.py`、`tests/services/test_stage_resolver.py`。

- 本地回退索引从 `stages.json` 换成 `Arknights-Tile-Pos/overview.json`；匹配顺序 `stageId` → `levelId` → `code` → 中文名，全部大小写不敏感。
- 返回全部候选（`candidates`，按 `stageId` 字典序）与 `candidates_total`；`resolved` 仍是第一条（向后兼容）。
- 内核路径仍优先；内核返回时 `candidates` 是单条。
- `stages_path` 语义变为「overview.json 的路径」，保留给测试；不保留两套代码路径。

## Task 5 · 文档同步（规格 §6，可并行）

**产物**：`docs/11-Agent模块设计.md`、`docs/12-实施计划与里程碑.md`、`docs/13-决策记录.md`、`docs/README.md`。

- 按规格 §6 逐条落实；`docs/13 §8.38` 记用户裁决、事实证据表、推断规则与它的边界、`resolve_stage` 的对外变更、以及「不写死当期数据」的理由。
- 文体与术语跟齐现有文档（书面中文、不用「AI 腔」、工具名用反引号）。

## Task 6 · 前端工具中文名（规格 §6 的附带项）

**产物**：`web/src/features/agent/format.ts` 与它的测试。

- 工具表加 `get_activity_stages: '查当期活动'`（与既有命名风格一致：`resolve_stage: '解析关卡'`、`read_knowledge: '查阅知识库'`）。
- 只加一格，不动其它文案；跑 `pnpm typecheck && pnpm test --run`。

## Task 7 · 验收（主 agent 亲自跑）

- `.venv/bin/python -m pytest -q`；
- `.venv/bin/python scripts/dump_openapi.py --check`、`.venv/bin/python scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`；
- 运行时实测（真资源包）：活动名、关卡首尾、`VEC-08` 全部候选、`render_context()` 的活动行与字数；
- 反例：假 `version.json`（活动名与关卡表完全无关）→ `confidence="none"`、不给代号；
- 前端 `pnpm typecheck && pnpm test --run && pnpm build`；
- 完成后 `git merge --no-ff` 回 `refactor/v2`，打 tag `v2-m24`，台账归档到 `docs/superpowers/ledgers/2026-10-09-m24-activity-knowledge.md`。
