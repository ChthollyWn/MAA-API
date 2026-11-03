# SDD ledger — plan: docs/superpowers/plans/2026-10-09-m24-activity-knowledge.md

## Setup

- Base：`refactor/v2` 的 `10eff9a`（`chore(web): 首页去掉四处冗余副标题`），分支 `m24-activity-knowledge`（从 `refactor/v2` 新建，直接在当前检出上工作，不建 worktree、不 push、不合入 `dev`）。
- Spec：`docs/superpowers/specs/2026-10-09-m24-activity-knowledge.md`；计划：`docs/superpowers/plans/2026-10-09-m24-activity-knowledge.md`。
- 用户指令（2026-10-09）：「我希望继续扩充 agent 的知识库，现在的 agent 甚至不知道游戏的最新版本活动是什么，也不知道对应的关卡代号。」同轮三条裁决：① 动态工具 + 系统消息注入 + 知识库条目三者都做；② 按里程碑规范做（分支 + 规格/计划 + `--no-ff` 合并 + tag）；③ 顺带修 `resolve_stage` 的代号重名行为。
- 基线：M19 起内置知识 31 条、索引 + 术语表每轮约 4.3 千字；本节改动前的全量 pytest 记录见 §8.36 的 2070 passed / 4 skipped（其后若干提交又加了用例，本里程碑收尾时实测 2123 passed / 4 skipped）。

## Preflight plan scan

| 依赖 | 结论 |
|---|---|
| 活动索引服务 → 工具 | 串行：工具直接用服务的返回形状 |
| 活动索引服务 → 注入段 | 串行：`render_activity_context` 同源 |
| 活动索引服务 → `resolve_stage` 改造 | 并行（两者都读 `overview.json`，但各自独立解析，不共享代码；本期不做公共层抽取） |
| docs 同步（队友） | 与代码并行；规格先行，文档按规格写 |
| 前端工具中文名（队友） | 与后端并行，只加一格映射 |
| 收尾验收 | 依赖全部；由主 agent 亲自跑 |

## Rulings I made

- Ruling: **不把当期活动写进 markdown**，改为运行时现读资源包。理由：M19 §3.2/§4.3 明确把「会随版本失效的当期数据」排除在外，两周一换的清单写进文件后下一次活动就会骗人。代价：多一个服务模块与一条工具，且推断是**排序**不是映射（见下一条）。
- Ruling: **活动名 → 关卡代号用「收敛 + 排序」而不是映射**，并且把不确定性摊开。包内没有映射表（`version.json` 只有名字，`overview.json` 只有代号），上游带映射的 `StageActivityV2.json` 在 MAA API 上、联网取用违反 M19 的「运行时不联网」。做法：期数收敛（只留 `activities/` 命名空间 + 有字母模式后缀的前缀）+ 按「主关数 → 总关数 → 前缀」排序取第一，多个候选时 `confidence="low"` 并列出 `alternatives`。代价：活动名不带期数时给不出代号（`none`），这条如实回报而不是猜。
- Ruling: **`related` 与 `alternatives` 分开**。`related` 只列同一类玩法的其它期次（`act3break` 的 `act1break`/`act2break`），同期的其它玩法进 `alternatives`；混在一起会让模型误判「哪个是当期」。
- Ruling: **注入段读不到活动数据时整段不插**，而不是写「暂无」。与「知识库读失败降级为『知识库暂不可用』」不同：这里没有内容可展示，写占位只花 token 不提供信息。
- Ruling: **`resolve_stage` 的 `stageId` 查询精确返回**，不参与同图合并；其余各级（`levelId`/`code`/`name`）按 `levelId` 合并 `#f#` 变体。依据：真实数据里 `main_01-07` 与 `main_01-07#f#` 的 `levelId`、中文名、关卡图完全相同，不合并会让「候选数」虚高；但用户明确给了带 `#f#` 的 stageId 时，要原样返回他给的那条。
- Ruling: **`stages_path` 参数保留但语义换成 overview**，并加等价的 `overview_path`。旧名不删是为了不动装配与既有测试；两个都传时以 `overview_path` 为准。
- Ruling: **接口零新增**：不加端点、不加错误码。查不到关卡是 `matches: []` + `hint`（查数据不是错误），未知前缀与资源缺失分别复用 `INVALID_PARAMETER` 与 `RESOURCE_LOAD_FAILED`。
- Ruling: **内置条目用中性占位**（`XX-01`、`act<期数><模式>_08`），不出现任何真实活动名/期次/前缀。理由：条目是随代码发布的常驻知识，写当期数据等于把会过期的信息固化。
- Ruling: 文案审计产物（`docs/copy-audit/*`）按惯例重新生成并 `--write-baseline` 重设基线。依据：本期新增的是新模块的中文文档与 docs/13 的决策记录，`docs正文/bold` 等计数上升属新增正文而非回潮；`scripts/copy_inventory.py --check` 随后通过。

## Tasks

| Task | 产物 | 结果 |
|---|---|---|
| 1 · 活动索引服务 | `maa_api/services/activity_index.py` + `tests/services/test_activity_index.py` | 完成：20 个用例（夹具边界 + 真资源包） |
| 2 · 工具 `get_activity_stages` | `maa_api/agent/tools/activity.py`、`tools/__init__.py`、`tests/agent/test_activity_tools.py`、`tests/agent/test_tool_catalog.py` | 完成：9 个用例；工具总数 48 → 49 |
| 3 · 注入段 + 内置条目 | `maa_api/agent/knowledge/__init__.py`、`builtin/activity-stage-codes.md`、`builtin/event-and-rerun.md`、`tests/agent/test_knowledge_service.py`、`tests/api/test_agent_knowledge_router.py` | 完成：内置 31 → 32 条；`_spec_blocks()` 改读三份规格 |
| 4 · `resolve_stage` 重名修正 | `maa_api/services/stage_resolver.py` + `tests/services/test_stage_resolver.py` | 完成：回退索引换 `overview.json`，返回全部候选，去重同图变体 |
| 5 · 文档同步（队友 docs-writer） | `docs/11`、`docs/12`、`docs/13`、`docs/README` | 完成，主 agent 按最终实现校正了推断规则与验收段 |
| 6 · 前端工具中文名（队友 web-labels） | `web/src/features/agent/format.ts`、`format.test.ts` | 完成：`get_activity_stages: '查当期活动'` |
| 7 · 验收（主 agent） | 见下 | 完成 |
| 8 · 独立验证（队友 m24-verifier） | 只读对抗式评审 | 见「独立验证」 |

## Task progress

- **Task 1**：三处数据源（`version.json` / `overview.json` / `tasks/Stages/` 文件名）按 `iter_resource_roots()` 反向层序读取，按文件签名缓存。实测：当期 9 个候选收敛到 3 个完整活动（`act3break` 12 主关 / 32 关 > `act3bossrush` 8 / 8 > `act3enemyduel` 2 / 2），第一名与当期活动一致。中间发现并修掉两个真实缺陷：① 前缀里的期数**夹在词中间**（`act3break` 的 3 在 `act` 与 `break` 之间），最初按「尾部数字」拆会全部漏掉；② 代号排序不能取第一条数字，否则 `VEC-EX-1` 会排到 `VEC-02` 前面。
- **Task 2**：三种参数形态（不给 / `stage` / `prefix`）与四条失败路径写死；REST 目录与 invoke 实测通过。
- **Task 3**：注入段 101 字（≤120），位置在索引之后、术语表之前；读不到活动数据时整段不插。为不改既有 render 断言语义，测试夹具默认注入「读不到资源」的桩，另加固定快照桩测内容；新加一条「内置文件里不得出现当期数据」的断言（口径：活动全名与 `levelId` 齐全的前缀算当期数据；单独出现的模式名不算，M19 术语表里「矢量突破」是作为模式列举的）。
- **Task 4**：内核优先不变；本地回退换成 `overview.json`（4255 条）。实测 `1-7` 合并成 1 条候选、`VEC-01` 4 条（四期同名）、`act3break_08` 与中文名各精确命中 1 条。
- **Task 5**：队友写了 docs/11 §2.2/§3.2/§6、docs/12 的 M24 条目、docs/13 §8.38、docs/README 决策速查表；主 agent 在实现定稿后校正了「推断规则」段落（旧写法还是尾部数字那套）与验收段，并补了 `overview_path`/去重/参数名等实现细节。**过程中我把「2120 passed」误替换进 docs/12 的 M19/M21/M22/M23 四个条目的验收行**，已 `git checkout` 回滚该文件并只重贴 M24 段落（复核 `git diff` 为纯 18 行新增、无删改）。
- **Task 6**：`format.ts:118` 加一格，`format.test.ts` 补一条断言；`pnpm typecheck` 与 `pnpm test --run`（611 passed）退出码 0。
- **Task 7（主 agent 亲自跑）**：
  - 后端 `.venv/bin/python -m pytest -o addopts= -q` → **2123 passed, 4 skipped**，退出码 0；
  - `.venv/bin/python scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check` 三处退出码 0（先跑不带 `--check` 的重新生成：OpenAPI 快照只多一条工具，无新端点/错误码）；
  - 前端 `pnpm typecheck && pnpm test --run && pnpm build` 退出码 0（611 个用例、构建产物含 service worker 自检）；
  - `scripts/agent_smoke.py` 与 `scripts/frontend_smoke.py` 末行均为 `SMOKE OK`；
  - 运行时实测（真资源包）：`get_activity_stages` 不带参数 → 活动 `矢量突破#3 拟生态`、`started_at` `2026-09-29`、前缀 `act3break`、`confidence=low`、`related=[act1break, act2break]`、`alternatives=[act3bossrush(8), act3enemyduel(2)]`、代号区间 `VEC-01…VEC-12`、32 关、`navigation=missing`；`render_context()` 活动行 101 字、知识库段总长由此前的 4325 字变为 4513 字；REST `/api/agent/tools` 列出 49 条，`invoke` 两种参数形态都 200；
  - **反例**：假 `version.json`（活动名与关卡表毫无关系）→ `confidence="none"`、`stage_id_prefix=None`、不给任何代号，注入行改说「关卡代号未知，问到具体关卡时用 get_activity_stages 带代号查」；
  - 文案审计：`scripts/copy_inventory.py --write-baseline` 后 `--check` 通过。

## 独立验证

由**没有参与实现**的队友 `m24-verifier` 只读评审（task-3），报了 8 类问题。逐条处置：

| # | 问题 | 处置 |
|---|---|---|
| 1 | `tools/activity.py` 的 `Field(description=…)` 与运行时 hint 里写死 `VEC-08` / `act3break_08` / `act3break`（会随工具 schema 给模型） | 改为中性占位与「形如 `<前缀>_08`」，实测扫描不再命中 |
| 2 | `services/activity_index.py` 的 docstring/注释写死当期活动名、前缀与代号 | 全部换成占位（`act7demo` / `XX-01`）与「实测」描述；4/1 的数量笔误改成 8/2 |
| 3 | 泄漏测试取 `sorted(candidates)[0]`（真资源上是 `act11d0`）而非当期前缀，且只扫 `builtin/*.md` | 重写为 `test_agent_visible_sources_carry_no_version_sensitive_data`：前缀取 `snapshot().stage_id_prefix`，扫描扩到 `builtin/*.md` + `tools/activity.py` + `services/activity_index.py` + `knowledge/__init__.py`（这条测试在修 1、2 时确实抓到了残留） |
| 4 | `docs/11` 参数写成 1–32 字、漏 `prefix`；候选说成进 `related`；写了不支持的 `#三` | 全部按实现改正，并补 `code_range` 与分级匹配 |
| 5 | `docs/11`/`docs/12`/规格 §4.1 把 `repo/` 当运行时层 | 改为 `iter_resource_roots()` 的真实层序（基础层 → `cache` → `custom`；`repo/` 在资源更新时已合并进基础层） |
| 6 | `find_stage` 是四字段并集而非规格的级联顺序（`切尔诺伯格` 多返回一条） | 改为分级匹配并回报 `matched_field`；实测 `切尔诺伯格` 现在 1 条（code 级） |
| 7 | `alternatives` 截断 5 与 `known` 不一致；`prefix` 查询无 60 上限；注入行无长度保护；读失败每轮带栈 warning；`app.state.activity_index` 无生产装配 | 逐条修：`known` 从 `as_dict()["alternatives"]` 取；`prefix` 加 `MAX_STAGES` 截断；新增 `MAX_ACTIVITY_LINE_CHARS=140` 预算（超长名字先退代号区间再裁名，实测长名 140 字封顶）；warning 只打一次、其后 debug；`main.py` 装配 `ActivityIndex` 并注入 `KnowledgeService` |
| 8 | `tasks/Stages/` 不在缓存签名里（新增导航文件后仍报 missing）；`MAX_STAGES` 断言测的是不截断的内部表；排序措辞有歧义 | 签名加导航目录清单 + 回归用例 `test_adding_a_navigation_file_invalidates_the_cache`；断言改测 `as_dict()["stages"]` 与 `stages_total`；规格措辞改为「主关数降序 → 总关数降序 → 前缀升序」 |

验证者同时实测通过的部分：推理边界（无期数 → `none` 且不给代号；多候选 → 取第一 + 其余进 `alternatives`）、字段名与取值域逐条对齐、真资源全流程、`resolve_stage` 的向后兼容与重名候选、四条异常路径都抛 `RESOURCE_LOAD_FAILED`、缓存对 `version.json` 变更即时失效、REST 目录 49 条。

8 类问题全部处置完，`m24-verifier` 又做了一轮只读复验。第二轮结论：6 条完全到位、2 条有残留、1 个需要裁决的副作用。处置：

- **残留 1（层序）**：`docs/12` 与规格 §4.1 里还留着「资源层 = 基础层 + `repo` 增量层」和示例 `source: resource/maa-layers/repo/resource`（实现里不可能出现这个 source，`_current_layer()` 只遍历 base → cache → custom）。两处都改了。
- **残留 2（长度上限）——这条是我自己的流程错误**：我为了容纳长活动名把上限从 120 改成了 140，而 120 是**用户裁决过的每轮成本**、写在规格 §3.1 与 docs/11/README 里。按 AGENTS.md「改变对外行为或范围的裁决要停下来问」，我不该自行放宽。已改回 `MAX_ACTIVITY_LINE_CHARS = 120`，退代号 + 裁名的逻辑在 120 下同样自洽（实测长名正好 120 封顶），并把测试断言从「跟着常量走」改回字面 `<= 120 <= MAX_ACTIVITY_LINE_CHARS`——跟着常量的断言抓不到上限被悄悄放宽这种漂移。
- **副作用（分级匹配）**：`find_stage("切尔诺伯格")` 现在只返回代号级那一条，以它作中文名的另一关不再同时出现。这是分级顺序的必然结果，我选择保留并在规格 §4.2 与 docs/11 §3.2 明写这条边界（要查那一关得用它的代号或 `stageId`）；理由是本工具的主要问法是代号与 `stageId`，取并集会把两关混在一起返回。
- 另补：docs/11 列出 `matched_field`。

两次复验后，主 agent 又跑了全量后端（**2123 passed, 4 skipped**）、三处生成物 `--check`、前端 `typecheck` + `test --run`（611 passed）。文案审计基线最终重设一次（本期连续新增正文，`docs正文/bold` 等 +1 量级；`not-but`、代码注释里的 `——` 这类真回潮已逐条改写，重设前的口径与理由记录在此）。


## 最终交付

- 分支 `m24-activity-knowledge`，从 `refactor/v2`（`10eff9a`）新建。提交：规格与计划、后端实现与测试、文档与生成物、本台账。
- 交付内容：新增活动索引服务与只读工具 `get_activity_stages`；system 消息知识库段新增一行当期活动；内置条目 31 → 32 条（`activity-stage-codes`）；`resolve_stage` 回退索引换成含活动的 `overview.json` 并在重名时返回全部候选（`candidates`/`candidates_total`，`resolved` 语义不变）。
- 未做（有意）：联网查 `StageActivityV2.json`；活动分阶段开放的时间表判断；`stage_layout.py` 与 `activity_index.py` 的 `overview.json` 解析公共层抽取（属重构）；真机端到端（用 `Fight` 真的跑通当期活动关）。
- 遗留：`tasks/Stages/` 里没有当期活动的导航文件（`navigation="missing"`），能否直接提交 `Fight` 属未知，工具只如实回报、不打包票；活动名不带期数时推不出代号，属已知边界；若以后要更准，可考虑在资源更新链路上缓存 MAA 官方的 `StageActivityV2.json`，但那要先改「运行时不联网」这条决策。
