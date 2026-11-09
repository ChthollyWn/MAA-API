# M86 界面导航图 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给服务端加一张可修正的界面导航图：15 个种子界面 + 36 条跳转落库；`where_am_i` 先用感知哈希在 200 ms 内识别、拿不准再让视觉模型在候选里选；`navigate_to` 一次调用完成最短路径、逐步执行、截图校验、走错重规划、迷路回主界面；`remember_screen` 把导航员手动找到的新界面记进图；导航图页能看、改、启停与删除跳转；导航场景用 `ticket` 驱动回放。

**Architecture:** T1 两表/两枚举/迁移 → T2 截图归档、dHash、视觉客户端与识别器 → T3 图服务、种子与 Dijkstra → T4 导航执行器与设备适配（`_run_atomic` 改名 `run_atomic`）→ T7 配置键 → T5 三个工具与策略 → T6 四个 REST 端点、三个错误码与装配 → T8 导航员定义 → T9 前端导航图页 → T10 冒烟 → T11 评测假导航世界与两个场景 → T12 文档、全量验收、合并与 tag。T7 因 T5/T6 都读 `get_settings().nav`，在本计划中提前到 T5 之前执行；任务编号仍对应 spec 的 T 编号。

**Tech Stack:** FastAPI + SQLModel/Alembic + Pillow（不新增依赖）+ pytest（同步测试）；React 19 + TypeScript + TanStack Query + Vitest。

**Spec:** [m86-nav-graph](../specs/m86-nav-graph.md)（1245 行，2026-11-09 升格版）；评测驱动依赖 [evals-ticket-driver](../specs/evals-ticket-driver.md) 的 `drive_ticket`、`Scenario.human`、`HumanPolicy`（维护性补丁合并后才有）。

## 开工基线

- 分支：从 `dev` 新建 `nav-graph`；**不 push、不用 worktree**。`dev` 是维护性补丁 `evals-ticket-driver` 合并后的提交，Task 0 用 `git rev-parse --short dev HEAD` 实测并记进台账；写作时 `dev` 是 `e41ad72`，补丁尚未合并，不能据此开工。
- tag：`v3-m85` 已存在；迁移 head 是 `0040_question_expiry_historical`，本里程碑迁移固定 `0041_nav_graph`（0041 已在 [03 §2](../03-数据模型.md) 登记，不取下一个空号）。
- 全量 pytest 基线（2026-11-09，`evals-ticket-driver` 合并后实测）：**3 failed / 5261 passed / 5 skipped / 2 xfailed**（3 条均为真资源数据漂移：`test_activity_index` ×2、`test_stage_layout` ×1，见 [M85 台账](../ledgers/m85-im-channels.md)；起点已有的失败记进台账，不新增即通过）。`evals-ticket-driver` 合并前的基线是 4 failed / 5242 passed（多一条 `test_incidents` 时间敏感 flake）。
- `tests/evals` 基线（补丁合并后实测）：**69 passed**（`evals-ticket-driver` 合并前 51 passed；补丁新增 T1–T4 用例 18 条）。
- 计数基线：工具 104/103；`refetchable` 50；`exclusive` 8；`read` 4；端点 182/79；错误码 168/167/21；业务表 55（`tests/db/test_models.py` 的函数名仍写 56，实测 55）；配置键 139/128/13；事件 kind 16；评测场景 11；迁移 head 0040。
- 任务顺序裁决：spec §5 的 T5/T6 都读 `nav.enabled`/`nav.learn`/`nav.max_steps`，而这三个键属于 T7。本计划把 **T7 排在 T5 之前**，其余任务按 spec 的 T1→T4、T5→T6、T8→T12 顺序执行；原因与结论记进 13 §8 与台账。

### Task 0: 分支、§0 前置检查、接缝裁决、计划入库

**Files:**
- Create: `docs/v3/plans/m86-nav-graph.md`（本文件）；Create（运行时）: `.superpowers/sdd/2026-11-09-m86-nav-graph/progress.md`

**Interfaces:**
- Consumes: `dev` 上的 `evals-ticket-driver` 补丁。
- Produces: 开工基线记录、16 条前置检查结果、接缝裁决、后续任务的执行顺序。

- [ ] **Step 1: 从 dev 新建分支、把计划文件入库并确认工作区**

```bash
git checkout dev
git checkout -b nav-graph
git add docs/v3/plans/m86-nav-graph.md
git commit -m "docs(m86): 执行计划"
git status -sb
git rev-parse --short dev HEAD
```
Expected：`git status -sb` 只有 `## nav-graph` 一行（计划文件已在本次提交入库，不再显示 `??`）；`git rev-parse --short dev HEAD` 两行相同。工作区不干净时停下来，不要 stash 别人的改动。

- [ ] **Step 2: 逐条跑 §0 的 16 个检查**

| # | 命令 | 期望 |
|---|---|---|
| 1 | `git status -sb` | 第一行 `## nav-graph`，没有其他行（计划文件已在 Step 1 提交） |
| 2 | `git tag -l 'v3-m85'` | `v3-m85` |
| 3 | `ls maa_api/db/migrations/versions/ \| grep -E '^[0-9]{4}_' \| sort \| tail -n 1` | `0040_question_expiry_historical.py` |
| 4 | `grep -n "^name: navigator$" maa_api/agent/specs/builtin/navigator.md` | 1 行 |
| 5 | `grep -nE "^\s+(device\|refetchable\|untrusted_output\|rest_visible):" maa_api/agent/registry.py` | 4 行 |
| 6 | `grep -n "async def resolve" maa_api/agent/llm/roles.py` | 2 行（`resolve`、`resolve_for_spec`） |
| 7 | `grep -n "^def register_schema\|^def get_schema\|^async def complete_structured" maa_api/agent/llm/structured.py` | 3 行 |
| 8 | `grep -n "class LeaseMiddleware" maa_api/agent/lease.py` | 1 行，约 644 行 |
| 9 | `grep -n "_policy_session_id\|POLICY_SESSION_KEY" maa_api/agent/harness/builtin_middleware.py` | 7 行 |
| 10 | `grep -n "preempt" maa_api/api/routers/agent.py` | 6 行 |
| 11 | `.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"` | `104 103` |
| 12 | `.venv/bin/python -c "import PIL, numpy; print('ok')"` | `ok` |
| 13 | `test -f docs/v3/14-现行接口清单.md && echo ok` | `ok` |
| 14 | `grep -n "def archive_screenshot_result" maa_api/agent/harness/builtin_middleware.py` | 1 行，约 1408 行 |
| 15 | `grep -n "app.state.model_router = ModelRouter\|app.state.cost_ledger = CostLedger" maa_api/main.py` | 2 行（1333、1337） |
| 16 | `grep -n "def drive_ticket" maa_api/agent/evals/runner.py`；`grep -n "class HumanPolicy" maa_api/agent/evals/scenario.py` | 各 1 行 |

第 3、6、7、8、9、10、15、16 条不符合时**停工并报告**，不要自己补号、补符号或改评测驱动。第 16 条不符时说明维护性补丁尚未合并，M86 不能开工。

- [ ] **Step 3: 记录基线（写到 progress.md）**

```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/m86-base.log 2>&1; echo $?; tail -n 3 /tmp/m86-base.log
.venv/bin/python -m pytest -o addopts= -q tests/evals > /tmp/m86-base-eval.log 2>&1; echo $?; tail -n 2 /tmp/m86-base-eval.log
.venv/bin/alembic heads
ls tests/evals/scenarios/*.yaml | wc -l
```
Expected：全量退出码 `1`，尾行是既有失败（不是新增错误）；`tests/evals` 退出码 `0`；`alembic heads` 只有 `0040_question_expiry_historical (head)`；场景 `11`。把实际数字写进 progress.md，后续验收与它比较。

- [ ] **Step 4: 落接缝裁决**
把下面 12 条写进 progress.md 的「Rulings I made」，实现时照做：

1. **REST 只抢占、不持有租约**：`navigate_to` 的 `device='exclusive'` 只让 `_rest_preempt_hook` 在 REST 执行前调 `preempt_device`；`LeaseMiddleware.applies_to={'worker','subagent'}` 不动，REST 导航期间另一个 Worker 可以取租约，这是 M71 口径，不新增互斥。
2. **`device_factory` 注入**：`NavGraphService.__init__(..., device_factory=None)` 把工厂存成公开属性 `self.device_factory`；`make_device(context)` 在工厂为空时懒导入 `AppNavDevice`。T10/T11 直接替换 `app.state.nav_service.device_factory`。
3. **`remember_screen` 认出另一个节点时整体跳过**：spec §4.3.1 有一句「样本照加，只有学边被跳过」，与 §4.5.5 第 2 步「若结果是另一个节点 Y → 不写」冲突；按后者与测试名 `test_remember_skips_when_screen_is_another_node` 执行：不建节点、不加样本，返回 `skipped_reason="screen_is_<Y>"`、`exemplar_added=false`、`screenshot_id=null`、`edge=null`。记 13 §8。
4. **trail 固定七个键**：spec §4.5.4 文字写「六个键」但列了 `edge_id/action/from/expected/actual/ok/ms` 七个；按七个与 §6 的 `test_trail_has_fixed_keys_and_types` 执行。
5. **`where_am_i` 候选**：候选只含有样本且平均距离有限的节点，按平均距离升序取前 3；视觉候选按同样排序取前 8，再保证含 `home`；不把 `inf` 写进 JSON。
6. **首达信任只在五个条件同时成立时生效**：`nav.learn=true`、`step.origin=="seed"` 且 `edge_id` 非空、目标节点样本数为 0、`rec.node_id is None`、动作类型不是 `home`；`home` 的首达走 anchor，不适用本条。
7. **`archive_screenshot` 的提交归调用方**：`Navigator` 归档后立即 `await db_session.commit()` 再 `add_exemplar`；归档失败只记 warning，`screenshot_id=None`，不阻断导航。
8. **`remember_screen` 的审计查询**：`session_id` 取工具上下文的策略会话；从 `LastSeenStore` 取 `(P, cursor)`；只收 `id > cursor AND tool_name IN ('click','swipe','key_event') AND status='success'`，按 id 升序；0 条 `no_audit`、超过 8 条 `too_many_steps`、含 `long_press`/`input_text` `unsupported_action`、`P == node_id` `same_node`；`key_event` 只收 `BACK`。
9. **`max_steps` 只能调小**：`min(params.max_steps or nav.max_steps, nav.max_steps)`；`recover_home` 自己 `steps += 1`，达到上限抛 `NAV_STEP_FAILED(reason='max_steps')`。
10. **`update_nav_edge` 只改三个字段**：`enabled`、`action`、`cost_ms`；请求里的端点与现值不同 → 400（`details.field` 为 `from_node`/`to_node`），相同则忽略；`enabled` 从 false 改 true 时清零 `fail_count`；改 `action` 重算 `action_hash`，与同一对界面的另一条撞了 → 400（`details.field="action"`）。
11. **T10 直接走工具注册表**：`remember_screen` 的 `rest_visible=false`，冒烟不通过 `/api/agent/tools/remember_screen/invoke`，而是用 `registry.execute(name, args, ToolContext(...))` 在 `client.portal` 里调三个工具；REST 可见性由 T5 的目录测试单独钉。
12. **页面落点**：仓库没有 `web/src/features/more/`，「更多」页是 `web/src/routes/more/index.tsx`；导航图页按 spec 放 `web/src/features/nav/`，入口 `/more/nav`。

- [ ] **Step 5: 确认计划入库**

```bash
git log --oneline -1
git status -sb
```
Expected：首行是 `docs(m86): 执行计划`，`git status -sb` 只有 `## nav-graph`。

## Global Constraints

- 分支 `nav-graph` 从 `dev` 新建；完成后 `git merge --no-ff nav-graph` 回 `dev`，打 tag `v3-m86`，删临时分支；**不 push**；不用 worktree。
- 迁移固定 `maa_api/db/migrations/versions/0041_nav_graph.py`，`down_revision = "0040_question_expiry_historical"`；迁移只建表与索引，不写种子；种子由启动时 `ensure_seed()` 在 `nav_node` 为空时写入。
- 计数目标（全部写死）：工具 **107/106**（`refetchable` 51、`exclusive` 9、`read` 5、`none = len(registry.list()) - 14`）；HTTP 端点 **186/83**；错误码 **171/170/22**；业务表 **57**；配置键 **142/131/13**；迁移 head **0041**；事件 kind **16 不变**；评测场景 **13**；`navigator` 定义版本 **2**。
- `maa_api/agent/tools/raw.py` 的 `_run_atomic` 改名 `run_atomic`，同文件调用一并改；`maa_api/api/routers/device.py` 的同名 `_run_atomic` **不动**。
- 不新增事件 kind、SSE/WS 事件、端点、错误码、配置键或工具；只加 spec §4.3 的三个工具与四个端点、三个 `NAV_*` 码。
- 种子固定 15 个节点、36 条边；坐标是归一化坐标（0–1），出处逐条对 MAA 资源；不允许 `stage:` 种子节点；动作只允许 `BACK` 作为按键。
- 不用 MaaCore 模板匹配做逐步识别；不做模板裁剪、不写 custom 层图片、不做多分辨率适配（只支持 16:9）。
- 不改 M71 租约口径；不改 09「人工操作不占租约」；内置调用由 `LeaseMiddleware` 持租约，REST 只抢占。
- 后端命令一律 `.venv/bin/python`；`pytest.ini` 已带 `-q`，看计数用 `-o addopts= -q`；没有 `rg`，用 `grep -rn`；测试/构建/diff 长输出重定向到 `/tmp/m86-*.log`，只读尾部和退出码。
- 前端在 `web/` 下运行：`pnpm gen:api:check`、`pnpm typecheck`、`pnpm test --run`、`pnpm build`。
- 文档与提交信息用简体中文；提交格式 `feat(m86): <中文摘要>`、`docs(m86): ...`、`test(m86): ...`；进度写 `.superpowers/sdd/2026-11-09-m86-nav-graph/progress.md`，完成后复制为 `docs/v3/ledgers/m86-nav-graph.md`。
- 不把真实密钥、凭据、个人身份信息写进代码、测试、cassette 或文档。

## Review Focus

1. **视觉不可用/结构化失败必须走 unknown，不能抛**：没有 `model_router`/`cost_ledger`、档案 `supports_vision is not True`、模型请求失败、`STRUCTURED_OUTPUT_INVALID` 时，`where_am_i` 返回 `node=null`、`method="none"`、`confidence=0`，导航继续走回主界面重试。T2 的 `test_vision_unavailable_returns_unknown`、`test_structured_failure_returns_none` 与 T5 的 `test_where_am_i_is_pure_read` 钉住。
2. **首达信任不能把误点收成样本**：只有种子边、目标零样本、实际画面未匹配、`learn=true`、非 `home` 五条同时成立才收；`nav.learn=false` 时不收。T4 的 `test_first_arrival_bootstraps_seed_exemplar`、`test_bootstrap_skipped_when_learning_off` 钉住。
3. **REST 长导航期间租约空是契约，不是 bug**：`navigate_to` 执行前只 `preempt_device`，不 `acquire`；另一个 Worker 可以取走租约，下一次写设备动作按当时持有者执行或报租约错误。T6 的 `test_rest_navigate_to_preempts_without_holding_lease` 钉住「只抢占」。
4. **手写 cassette 的探查应答、tool id 顺序与记忆抽取**：ticket 场景前两条是档案首次使用的动态上下文探查（system 开头、无 tools，归 agent 通道，答 `{"text":"OK"}`）；之后才是 `delegate`、导航员工具、`report`；`ready` 结束的场景末尾还要一条记忆抽取 `{"text":"{\"items\": []}"}`。漏一条会 `exhausted`，多一条会 `unused`；子代理与 Worker 交替请求时 `_check_tool_ids` 按来源校验。T11 的 `--strict` 回放钉住。
5. **前端 `nav.ts` 的 group 与路由**：`secondaryNav` 项必须 `group: 'agent'`、`to: '/more/nav'`、图标 `Route`；`router.tsx` 的 `more/nav` 必须指向默认导出的 `NavGraphPage`，三个写操作成功后只失效 `keys.agent.navGraph()`。T9 的 `more.test.tsx` 与 `NavGraphPage.test.tsx` 钉住。

---

### Task 1（spec T1，依赖：无）: 数据模型与迁移

**Files:**
- Modify: `maa_api/domain/enums.py`（末尾加两个枚举）；`maa_api/db/models.py`（末尾加两表；模块 docstring「56 张业务表」改 57）；`tests/db/test_models.py`（`BUSINESS_TABLES`、`UUID_PK_TABLES`、`EXPECTED_INDEXES`、`EXPECTED_FK_CASCADE`、`EXPECTED_JSON_COLUMNS`、表数测试改名、索引 docstring 63→64）；`tests/domain/test_enums.py`（`EXPECTED_VALUES` 加两项）
- Create: `maa_api/db/migrations/versions/0041_nav_graph.py`；`tests/db/test_nav_graph_migration.py`

**Interfaces:**
- Consumes: `maa_api.db.models` 的 `json_column`、`datetime_column`、`new_uuid`、`utcnow`。
- Produces: `NavNodeKind`（`screen/stage/menu/dialog`）、`NavOrigin`（`seed/learned/user`）；`NavNode`、`NavEdge` 两表，列与 spec §4.1 逐列一致；迁移 revision `0041_nav_graph`。

- [ ] **Step 1: 先写失败测试**
`tests/db/test_models.py`：
- `BUSINESS_TABLES` 加 `"nav_node"`、`"nav_edge"`；`UUID_PK_TABLES` 只加 `"nav_edge"`（`nav_node.id` 是短 slug）。
- `EXPECTED_INDEXES` 加 `("uq_nav_edge_from_node_to_node_action_hash", "nav_edge", ("from_node","to_node","action_hash"), True)` 的同形条目（按该文件现有元组形状写）。
- `EXPECTED_FK_CASCADE` 加 `("nav_edge","from_node")`、`("nav_edge","to_node")`。
- `EXPECTED_JSON_COLUMNS` 加 `nav_node.exemplars`、`nav_node.recognizer`、`nav_edge.action`。
- 把 `test_metadata_has_exactly_56_business_tables` 改名为 `test_metadata_has_exactly_57_business_tables`，docstring 改「57 张业务表」；`test_index_inventory_matches_spec_exactly` 的 docstring「63 条」改「64 条」。

`tests/domain/test_enums.py` 的 `EXPECTED_VALUES` 加：

```python
"NavNodeKind": {"SCREEN": "screen", "STAGE": "stage", "MENU": "menu", "DIALOG": "dialog"},
"NavOrigin": {"SEED": "seed", "LEARNED": "learned", "USER": "user"},
```

`tests/db/test_nav_graph_migration.py` 照 `tests/db/test_channels_migration.py` 的写法建临时库，固定 `PREVIOUS_REVISION="0040_question_expiry_historical"`、`HEAD_REVISION="0041_nav_graph"`，写四个用例（名字照 spec §6）：
- `test_upgrade_creates_nav_tables_and_index`：`_columns()` 逐列比对 `VARCHAR(48)/VARCHAR(64)/VARCHAR(24)/JSON/VARCHAR(16)/DATETIME` 与可空性；唯一索引列序 `from_node,to_node,action_hash` 且 unique；两条外键 `ondelete='CASCADE'`。
- `test_nav_edge_unique_conflict`：插两个节点与一条边后，再插同 `(from_node,to_node,action_hash)` 的第二条，`pytest.raises(sqlite3.IntegrityError)`。
- `test_downgrade_drops_tables_and_reupgrades_empty`：先升级再降级，`alembic_version` 回 0040，两表与唯一索引消失；再升级两表存在且 `select count(*)` 都是 0。
- `test_offline_sql_renders_both_directions`：`command.upgrade(cfg, HEAD_REVISION, sql=True)` 与 `command.downgrade(cfg, PREVIOUS_REVISION, sql=True)` 捕获输出，含 `create_table('nav_node'`/`drop_table('nav_edge'`，且不建库文件。

- [ ] **Step 2: 跑红**
Run: `.venv/bin/python -m pytest -o addopts= -q tests/db/test_nav_graph_migration.py tests/db/test_models.py tests/domain/test_enums.py > /tmp/m86-t1-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t1-red.log`

Expected：非 0，报 `ImportError: cannot import name 'NavNode'` 或枚举/索引缺失。

- [ ] **Step 3: 实现枚举与模型**
在 `maa_api/domain/enums.py` 末尾加 `NavNodeKind`、`NavOrigin`（`StrEnum`，取值照 spec §4.1）。在 `maa_api/db/models.py` 末尾按 spec §4.1 逐列加 `NavNode`、`NavEdge`：`NavNode.id` 用 `Field(primary_key=True, max_length=48)`，`NavEdge.id` 用 `Field(default_factory=new_uuid, ...)`，JSON 列用 `json_column(..., server_default="'[]'"/"'{}'")`，`updated_at` 用 `datetime_column`，唯一索引放 `__table_args__`；模块 docstring 与两表 docstring 照 spec 抄。不要加 spec 之外的列或索引。

- [ ] **Step 4: 写迁移**
`0041_nav_graph.py` 照 spec §4.1 的 `upgrade()`/`downgrade()` 逐字实现：两张 `op.create_table`、两条具名 `ForeignKeyConstraint(..., ondelete="CASCADE")`、`op.create_index(..., unique=True)`；`downgrade()` 反序 `drop_index`、`drop_table("nav_edge")`、`drop_table("nav_node")`。`revision` 与 `down_revision` 写死。不写 `op.execute` 种子。

- [ ] **Step 5: 跑绿并核对迁移链**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/db tests/domain/test_enums.py > /tmp/m86-t1.log 2>&1; echo $?; tail -n 5 /tmp/m86-t1.log
.venv/bin/alembic heads
```
Expected：`0`；`0041_nav_graph (head)` 且只有一行。`tests/db/test_initial_migration.py` 的元数据比对、降级到底再升级全部通过。

- [ ] **Step 6: 提交**

```bash
git add maa_api/domain/enums.py maa_api/db/models.py maa_api/db/migrations/versions/0041_nav_graph.py tests/db/test_models.py tests/db/test_nav_graph_migration.py tests/domain/test_enums.py
git commit -m "feat(m86): 导航图两表、枚举与迁移 0041"
```

### Task 2（spec T2，依赖：T1）: 截图归档、感知哈希、视觉客户端与识别器

**Files:**
- Create: `maa_api/services/screenshot_archive.py`；`maa_api/agent/nav/__init__.py`、`maa_api/agent/nav/recognizer.py`；`maa_api/agent/llm/schemas/nav_classification.py`、`maa_api/agent/llm/schemas/nav_locate.py`；`tests/fakes/nav.py`（`make_screen`、`FakeVision`、`FakeNavDevice` 占位）；`tests/agent/nav/__init__.py`、`tests/agent/nav/test_phash.py`、`tests/agent/nav/test_recognizer.py`、`tests/agent/nav/test_vision_client.py`、`tests/agent/nav/test_screenshot_archive.py`
- Modify: `maa_api/agent/llm/schemas/__init__.py`（导入两个新 schema）

**Interfaces:**
- Consumes: `maa_api.util.image.store_screenshot`、`ScreenshotRepository.create`、`Screenshot`/`ScreenshotTrigger`/`ScreenshotBackend`、`complete_structured`、`ModelRole.VISION`、`ResolvedProfile.supports_vision`。
- Produces:
  - `archive_screenshot(session: AsyncSession, image: Image.Image, *, trigger: ScreenshotTrigger, backend: ScreenshotBackend) -> tuple[Screenshot, dict[str, Any]]`，返回 `{"screenshot_id","sha256","full_url","thumb_url"}`，不 commit。
  - `dhash(image, roi) -> int`、`signature(image, rois) -> str`、`distances(sig_a, sig_b) -> list[int] | None`、`Recognition`、`VisionClient`、`ModelVisionClient`、`Recognizer(graph, vision)`、常量照 spec §4.2。
  - `NavClassification`、`NavLocate` 两个 schema，模块末尾 `register_schema("nav_classification"/"nav_locate", ...)`。
  - `make_screen(name: str) -> Image.Image`、`FakeVision`、`FakeNavDevice`（T4 扩展）。

- [ ] **Step 1: 写 `tests/fakes/nav.py` 的图与假视觉**
`make_screen(name)`：1280×720 RGB 灰底（128,128,128）；`sha256(name).digest()` 同时决定 6 个 120×120 色块的位置与颜色——第 i 块从 4×3 候选网格按哈希选一个互不相同的格子、用后续字节在格内偏移，R/G/B 各取一个字节——让不同名字不仅颜色不同，dHash 的梯度位置也不同；`make_screen("a")` 与 `("b")` 的 `signature` 距离 > 20。若实现后这对名字仍达不到阈值，距离用例改用 `tests/fakes/nav.py` 里的固定 fixture 图（写死色块坐标与颜色），不放宽阈值。`FakeVision` 保存 `classify_result`、`locate_result`、`available_result=True` 与 `classify_calls`、`locate_calls`；`available()` 返回 `available_result`，`classify`/`locate` 记录调用并返回预设值。

- [ ] **Step 2: 写失败测试**
`test_phash.py`（名字逐字照 spec §6）：
- 同图 `distances(sig, sig) == [0] * len(rois)`；两个 ROI 的 `signature` 是两段 16 位十六进制、`:` 分隔。
- 对 `make_screen("a")` 用逐像素 ±3 的确定性噪声后，每段距离 ≤ 10；`make_screen("a")` 与 `("b")` 距离 > 20。
- ROI 数不同 `distances` 返回 `None`。
- 1280×720 图重复 20 次取中位数 ≤ 200 ms。

`test_screenshot_archive.py`：用 `engine, factory = asyncio.run(make_session_factory(tmp_path / "nav.db"))` 建库（`tests/fakes/v3_db.make_session_factory` 是 async，返回 `(engine, factory)`，照 `tests/agent/tickets/conftest.py:32`）；`async with factory() as session` 归档 `make_screen("home")`，断言文件在 `image/screenshot/<sha 前两位>/<sha>.jpg`、行里有 `trigger/backend/width/height/size_bytes`、返回 `screenshot_id/sha256/thumb_url`；用例结束 `engine.sync_engine.dispose()`。

`test_vision_client.py`：
- `test_available_requires_supports_vision`：`SimpleNamespace(model_router=router, cost_ledger=object())`，`router.resolve` 返回 `supports_vision=True/False/None`，前者 True、后两者 False。
- `test_available_is_false_without_router_or_ledger`：缺一个依赖时不抛、返回 False。
- `test_classify_and_locate_pass_agent_name_and_ids`：monkeypatch `maa_api.agent.nav.recognizer.complete_structured`，返回 `SimpleNamespace(value=NavClassification(...))`/`NavLocate(...)`；断言 `agent_name="navigator"`、`role="vision"`、`schema_name`、`run_id`/`session_id` 与构造参数一致。
- `test_structured_failure_returns_none`：monkeypatch 抛 `AppError(ErrorCode.STRUCTURED_OUTPUT_INVALID)`，`classify`/`locate` 都返回 None。

`test_recognizer.py`：用只实现 `graph()` 与 `get_node()` 的 `SimpleNamespace` 假图（`graph` 是 async 函数）与 `FakeVision`；识别器只读这两处，T3 落地后由 T4/T5 注入真实 `NavGraphService`，T2 的测试不依赖 T3。
- `test_nearest_exemplar_wins`：两个节点各有样本，近的胜出、`method="hash"`。
- `test_ambiguous_match_falls_back_to_vision`：两节点得分差 < 2，`classify_calls == 1`、`method="vision"`。
- `test_generic_node_yields_to_specific_stage`：`stage_start`（`generic=True`）与 `stage:1-7` 都匹配时取后者、`classify_calls == 0`。
- `test_vision_below_confidence_is_unknown`、`test_vision_unavailable_returns_unknown`、`test_vision_answer_outside_candidates_is_rejected`：三者都 `node_id is None`、`method="none"`；不可用时不调 `classify`。
- `test_recognize_does_not_write_graph`：识别前后 `graph()` 的节点 id 与每条样本逐一相等。

- [ ] **Step 3: 跑红**
Run: `.venv/bin/python -m pytest -o addopts= -q tests/agent/nav > /tmp/m86-t2-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t2-red.log`

Expected：非 0，报 `ModuleNotFoundError: maa_api.agent.nav` 或 `archive_screenshot` 不存在。

- [ ] **Step 4: 实现归档、schema、哈希与识别器**
`archive_screenshot`：`await asyncio.to_thread(store_screenshot, image)`；相对路径 `image/screenshot/<sha[:2]>/<sha>.jpg`；`ScreenshotRepository(session).create(Screenshot(trigger=..., backend=..., path=..., format="jpg", width=..., height=..., size_bytes=...))`；返回 `screenshot.id` 与 stored 的 `sha256/full_url/thumb_url`。不 commit。

`nav_classification.py`、`nav_locate.py` 照 spec §4.5.2 的模型逐字段写，`extra="forbid"`，模块末尾注册；在 `schemas/__init__.py` 加 import 与 `__all__`。

`recognizer.py`：
- `dhash`：归一化 roi 裁 `(round(x·W), round(y·H), round((x+w)·W), round((y+h)·H))`，左闭右开、越界裁剪、退化至少 1px，`convert("L").resize((9,8), BILINEAR)`，逐行比较相邻列，左 > 右记 1，得 64 位整数。
- `signature` 每段 `f"{bits:016x}"` 用 `:` 连接；`distances` 逐段异或 `bit_count()`，ROI 数不同返回 None。
- 节点匹配照 §4.5.2：每个样本每个 ROI 都 ≤ 节点的 `max_distance` 才算匹配，节点得分取匹配样本的最小平均距离；歧义判定与 generic 例外照 §4.5.2 第 2 步；视觉步骤照第 4 步（候选前 8 + `home`，置信度 ≥ 0.7，答案必须在候选里）。
- `ModelVisionClient`：`available()` 每次 `router.resolve(ModelRole.VISION.value)`，`supports_vision is not True` 或抛 `AppError` 返回 False；`classify`/`locate` 把图缩到 1280×720、JPEG 质量 80 的 data URL 放进 `messages=[{"role":"user","content":[{"type":"text","text":...},{"type":"image_url","image_url":{"url":...}}]}]`，再调 `complete_structured(...)`；任何 `AppError`/`Exception` 记 warning 返回 None。
- `CLASSIFY_PROMPT`/`LOCATE_PROMPT` 照 spec §4.5.2 逐字；候选行格式 `id｜中文名｜特征描述`。

- [ ] **Step 5: 跑绿并确认结构化 schema 不冲突**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/nav > /tmp/m86-t2.log 2>&1; echo $?; tail -n 5 /tmp/m86-t2.log
.venv/bin/python -m pytest -o addopts= -q tests/agent/llm/test_structured.py > /tmp/m86-t2-structured.log 2>&1; echo $?; tail -n 3 /tmp/m86-t2-structured.log
```
Expected：两个 `0`；`nav_classification`/`nav_locate` 注册不改变既有 schema 行为。

- [ ] **Step 6: 提交**

```bash
git add maa_api/services/screenshot_archive.py maa_api/agent/nav/__init__.py maa_api/agent/nav/recognizer.py maa_api/agent/llm/schemas/nav_classification.py maa_api/agent/llm/schemas/nav_locate.py maa_api/agent/llm/schemas/__init__.py tests/fakes/nav.py tests/agent/nav
git commit -m "feat(m86): 截图归档、感知哈希与视觉识别"
```

### Task 3（spec T3，依赖：T1、T2）: 图服务与种子

**Files:**
- Create: `maa_api/agent/nav/graph.py`；`maa_api/agent/nav/seed.py`；`tests/agent/nav/test_graph_service.py`；`tests/agent/nav/test_seed.py`
- Modify: `maa_api/agent/nav/__init__.py`（导出 `NavGraphService`）

**Interfaces:**
- Consumes: `NavNode`/`NavEdge`、`AgentAudit`、`session_factory`、`NavDevice`（仅类型，不 import navigator）。
- Produces: `NODE_ID_PATTERN`、`HOME_NODE_ID`、`ALLOWED_KEYS`、`MAX_SEQUENCE_STEPS`、`DISABLE_AFTER_FAILS`、`FAIL_PENALTY_MS`、`DEFAULT_COST_MS`、`HOME_EDGE_COST_MS`、`EXEMPLAR_SOURCES`、`MAX_EXEMPLARS`、`DUPLICATE_DISTANCE`；五个动作模型与 `NavAction`/`action_hash`/`validate_action`；`PathStep`/`PathPlan`；`DeviceFactory`；`LastSeenStore`；`NavGraphService` 全部方法（签名照 spec §4.2）；`SEED_NODES`/`SEED_EDGES`；`maa_api.agent.nav` 包级导出 `NavGraphService`。

- [ ] **Step 1: 写图服务失败测试**
`test_graph_service.py`：用 `engine, factory = asyncio.run(make_session_factory(tmp_path / "graph.db"))` 建库（`make_session_factory` 是 async，返回 `(engine, factory)`，照 `tests/agent/tickets/conftest.py:32`），`service = NavGraphService(factory)`；用例结束 `engine.sync_engine.dispose()`。
- `test_action_hash_ignores_wait_and_rounds_coordinates`：两个 `tap` 只有 `wait_ms` 不同、坐标差 0.0001 → 同哈希；`x` 差 0.002 → 不同哈希。
- `test_shortest_path_prefers_cheaper_edges`、`test_disabled_and_excluded_edges_are_skipped`：构造两条路径，代价小的胜出；`enabled=False` 与 `exclude` 的边不出现。
- `test_implicit_home_edge_connects_everything`、`test_locate_edges_need_vision`：没有显式边时经隐式边到 home；`vision=False` 绕开 locate 边，只剩它时返回 `PathPlan((), True)`。
- `test_shortest_path_returns_none_without_any_path`、`test_same_node_returns_empty_plan`：孤立节点 None；源等于目标空 plan。
- `test_three_failures_disable_edge`、`test_failures_do_not_disable_a_proven_edge`：0 成功 3 失败后 `enabled=False` 且 `record_failure` 返回 True；5 成功 3 失败仍启用。
- `test_success_updates_cost_moving_average`、`test_reenable_resets_fail_count`：1000 → 2000 后 `cost_ms == 1300`；重新启用后 `fail_count == 0`。
- `test_exemplars_dedupe_and_cap`：近重复不加；第 9 个挤掉最早的非 `user` 样本；`source` 与 ISO `added_at` 落进 JSON。
- `test_last_seen_cursor_advances`：写一条 `agent_audit` 后 `note_seen`，`last_seen.get(session)` 返回节点与最大 id。

- [ ] **Step 2: 写种子失败测试**
`test_seed.py`：
- `test_seed_counts_and_idempotence`：`ensure_seed()` 返回 15；节点 15、边 36；第二次返回 0。
- `test_seed_coordinates_are_normalized`：所有动作坐标在 0–1；`ReturnButton` 那几条是 `0.0703, 0.0556`；`store` 节点 ROI 的 x 是 `0.7812`。
- `test_seed_has_no_stage_specific_nodes`：没有 `stage:` 开头的 id。

- [ ] **Step 3: 跑红**
Run: `.venv/bin/python -m pytest -o addopts= -q tests/agent/nav/test_graph_service.py tests/agent/nav/test_seed.py > /tmp/m86-t3-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t3-red.log`

Expected：非 0，`ModuleNotFoundError: maa_api.agent.nav.graph`。

- [ ] **Step 4: 实现 `graph.py`**
按 spec §4.2 写常量与五个动作模型（全部 `extra="forbid"`）；`action_hash` 用规范 JSON（键排序、坐标保留 3 位、递归删 `wait_ms`）后 sha256；`validate_action` 用 `TypeAdapter(NavAction)`，`ValidationError` 转 `AppError(INVALID_PARAMETER)`。`NavGraphService` 所有写操作 `async with session_factory() as session` + 显式 `commit()`，JSON 列整列赋值；`ensure_seed()` 先查 `nav_node` 是否为空，空时一个事务写入，返回节点数，非空返回 0；`graph()` 返回节点与边；`shortest_path` 用 Dijkstra（代价 = `cost_ms` 或默认值 + `FAIL_PENALTY_MS×fail_count`，隐式 home 边、禁用/exclude、vision 规则照 §4.5.3）；`note_seen` 查 `max(AgentAudit.id) WHERE session_id=...`。`NavGraphService` 不 import `navigator`；`make_device` 在 `device_factory` 为空时懒导入 `AppNavDevice`。

- [ ] **Step 5: 写种子常量**
`seed.py` 照 spec §4.5.6 的两张表写 15 个节点、36 条边：`origin="seed"`，`recognizer={"rois": [...], "max_distance": 10, "description": "...", "generic": bool}`；节点 `title/kind` 与表一致；`stage_start` 只有 `[0.7891,0.8681,0.2031,0.0847]` 一个 ROI 且 `generic=True`；其余节点除整屏外按表加 ROI；动作 `tap` 不带 `wait_ms`（默认 1200），三条 `locate` 的 `query` 照表；不要写 `stage:` 节点。

- [ ] **Step 6: 更新 `nav/__init__.py` 导出 `NavGraphService`**
`maa_api/agent/nav/__init__.py` 加 `from maa_api.agent.nav.graph import NavGraphService`，并在 `__all__` 加 `"NavGraphService"`；不要在这个文件 import `navigator`（T4 再加 `Recognizer`/`Navigator`，避免 T3 时 Navigator 还不存在）。`graph.py` 若在模块顶层 import `seed`，先完成 Step 5 再导入，避免 `seed.py` 尚不存在时 `nav` 包导入失败。

- [ ] **Step 7: 跑绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/nav/test_graph_service.py tests/agent/nav/test_seed.py > /tmp/m86-t3.log 2>&1; echo $?; tail -n 5 /tmp/m86-t3.log
```
Expected：`0`。

- [ ] **Step 8: 核对种子坐标出处（本机有 MAA 资源）**
运行 spec §7.2 的 python 脚本（`docs/v3/specs/m86-nav-graph.md` 第 1131–1161 行）原样：它从 `resource/lib/maa/Darwin/resource/tasks/**/*.json` 读 21 个 `roi`，把中心坐标与 expected 表逐项比对。脚本不在计划里重抄，避免两份坐标表漂移。

Run: `.venv/bin/python - <<'EOF' ... EOF`（heredoc 内容照 spec §7.2 逐字复制）

Expected：打印 `BAD []`、退出码 0。出现差值 > 0.01 的条目时停工报告；≤ 0.01 的差异记台账并按资源值改 `seed.py`。

- [ ] **Step 9: 提交**

```bash
git add maa_api/agent/nav/graph.py maa_api/agent/nav/seed.py maa_api/agent/nav/__init__.py tests/agent/nav/test_graph_service.py tests/agent/nav/test_seed.py
git commit -m "feat(m86): 导航图服务、最短路径与种子"
```

### Task 4（spec T4，依赖：T2、T3）: 导航执行器与设备适配

**Files:**
- Create: `maa_api/agent/nav/navigator.py`；`tests/agent/nav/test_navigator.py`、`tests/agent/nav/test_nav_device.py`
- Modify: `maa_api/agent/tools/raw.py`（`_run_atomic` → `run_atomic`，同文件 6 处调用）；`maa_api/agent/nav/__init__.py`（导出 `Recognizer`/`Navigator`）；`tests/fakes/nav.py`（把 `FakeNavDevice` 补完整）；`tests/agent/test_device_raw_tools.py`（加 `run_atomic` 回归断言）

**Interfaces:**
- Consumes: `NavGraphService`、`Recognizer`、`raw.run_atomic`、`DeviceManager`、`AppError`。
- Produces:
  - `MAX_LEARNED_SEQUENCE = 8`、`LEARNABLE_TOOLS = frozenset({"click","swipe","key_event"})`。
  - `NavDevice` 协议（`screenshot/resolution/tap/swipe/key/home`，像素坐标）。
  - `AppNavDevice(context: ToolContext)`：每个写设备动作在 `raw.run_atomic(context, "navigate_to", ...)` 内；`screenshot` 走 `device_manager.screenshot(backend="adb")`；`resolution` 读 `snapshot()["resolution"]`（是 `{"width","height"}` dict，见 `device_service.py:432-439`），显式转成 `(int(width), int(height))` 元组后缓存；值为空时用截图尺寸并缓存。
  - `Performed`、`NavResult`、`Navigator(graph, recognizer, device, *, learn, max_steps, db_session=None, session_id=None)` 与 `where_am_i/navigate/perform/remember`。
  - `maa_api.agent.nav` 包级导出 `Recognizer`、`Navigator`（加上 T3 的 `NavGraphService`，T5 直接 `from maa_api.agent.nav import ...`）。
  - `FakeNavDevice(transitions: dict[str,str], start="home", home_result=True)`，动作键 `tap:x,y`/`swipe:x1,y1,x2,y2,duration`/`key:BACK`/`home`；未登记动作原地；`unknown_once` 让下一次截图返回认不出的图。

- [ ] **Step 1: 写 `run_atomic` 回归测试并跑红**
在 `tests/agent/test_device_raw_tools.py` 加：

```python
from maa_api.agent.tools import raw

def test_device_tools_use_run_atomic(monkeypatch):
    calls = []
    async def fake(context, operation, call):
        calls.append(operation)
        return await call(None)
    monkeypatch.setattr(raw, "run_atomic", fake)
    # 调 click / swipe / key_event / back_to_home
    assert calls == ["click", "swipe", "key_event", "back_to_home"]
```

Run: `.venv/bin/python -m pytest -o addopts= -q tests/agent/test_device_raw_tools.py::test_device_tools_use_run_atomic > /tmp/m86-t4-raw-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t4-raw-red.log`

Expected：非 0，`AttributeError: module 'maa_api.agent.tools.raw' has no attribute 'run_atomic'`。

- [ ] **Step 2: 写导航器与设备失败测试**
`test_navigator.py`（用例名照 spec §6）：
- `test_navigate_one_hop_records_success`：home→infrast 一步、成功计数 +1；`test_already_there_does_nothing`：目标是当前界面时设备没收到动作。
- `test_first_arrival_bootstraps_seed_exemplar`：首次到 infrast 目标无样本也收下，`learned_exemplars` 加 1，trail 末项 `ok=true`。
- `test_bootstrap_skipped_when_learning_off`：`learn=False` 没有新样本，最终 `NAV_STEP_FAILED`。
- `test_wrong_landing_learns_edge_and_replans`：给 depot 预置样本后，home→infrast 的 tap 落到 depot：原边失败 +1、新增 learned 边 home→depot、从 depot 继续。
- `test_unknown_screen_recovers_via_home`、`test_home_failure_raises_step_failed`：认不出 → 等待再认 → 回主界面后到达；`home()` 为 False → `reason="home_failed"`。
- `test_max_steps_raises_step_failed`、`test_missing_target_suggests_container`、`test_no_path_raises_path_not_found`、`test_locate_only_path_raises_needs_vision`：`max_steps=1` 需两步 → `max_steps` 且带 trail；`stage:1-7` → `NAV_NODE_NOT_FOUND`、`nearest=main_episode`；孤立节点 → `no_edges`；只靠 locate 边 → `needs_vision`。
- `test_locate_edge_learns_tap_edge`、`test_home_anchor_adds_home_exemplar`：视觉给 `(0.5,0.5)` 后到达并多一条 learned tap 边；`home()` 后 home 多一个 `anchor` 样本。
- `test_trail_has_fixed_keys_and_types`：每项恰好 `edge_id/action/from/expected/actual/ok/ms` 七键，`ms` 是非负整数。
- `test_remember_creates_node_and_sequence_edge`、`test_remember_skips_when_screen_is_another_node`：审计两次 click → 新节点 + `sequence` 边，坐标按分辨率归一化；识别为 home 时 `skipped_reason="screen_is_home"` 且不建节点。
- `test_learning_off_changes_nothing`：没有新边与新样本，失败计数照记。

`test_nav_device.py`：
- `test_each_write_action_runs_inside_run_atomic`：`click`/`swipe`/`key_event`/`back_to_home` 都经过 `run_atomic`，`screenshot` 不走。
- `test_lease_preempted_propagates`：设备抛 `DEVICE_LEASE_PREEMPTED` 时原样向上，不被吞。

- [ ] **Step 3: 跑红**
Run: `.venv/bin/python -m pytest -o addopts= -q tests/agent/nav/test_navigator.py tests/agent/nav/test_nav_device.py > /tmp/m86-t4-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t4-red.log`

Expected：非 0，`navigator` 模块缺失。

- [ ] **Step 4: 改名 `raw._run_atomic`**
把 `maa_api/agent/tools/raw.py` 的 `_run_atomic` 改名为 `run_atomic`，同文件 6 处调用全部改；`maa_api/api/routers/device.py` 里的 `_run_atomic` 不动。跑 `grep -rn "_run_atomic" maa_api/agent/tools/raw.py tests/agent`，Expected：没有输出。

- [ ] **Step 5: 实现 `AppNavDevice` 与 `Navigator`**
`AppNavDevice` 的 `tap/swipe/key/home` 各自 `await raw.run_atomic(self._context, "navigate_to", lambda manager: ...)`；`screenshot` 调 `manager.screenshot(backend="adb")` 取 `.image`；`resolution` 把 `snapshot()["resolution"]` 的 `{"width","height"}` 转成 `(int(width), int(height))` 元组后缓存，空时用 `screenshot()` 尺寸缓存（直接返回 dict 会违反 `NavDevice.resolution -> tuple[int,int]` 契约）。

`Navigator` 照 spec §4.5.4 的伪代码实现：`where_am_i` 识别并 `note_seen`；`navigate` 的初始识别、回主界面、Dijkstra、逐步执行、校验、首达信任、走错学边、迷路重试、`max_steps`、trail 七键、`NavResult` 计数；`perform` 五种动作（tap/swipe/key/sequence/locate，`home`）；`remember` 照 §4.5.5 五步（含审计查询与跳过原因）。所有归档用 `archive_screenshot`；成功加样本前 `await db_session.commit()`，失败只 warning。

- [ ] **Step 6: 更新 `nav/__init__.py` 导出 `Recognizer`、`Navigator`**
在 `maa_api/agent/nav/__init__.py` 加 `from maa_api.agent.nav.recognizer import Recognizer` 与 `from maa_api.agent.nav.navigator import Navigator`，`__all__` 补齐三个名字；用 `.venv/bin/python -c "from maa_api.agent.nav import NavGraphService, Recognizer, Navigator"` 验证（T5 依赖这个包级导入）。

- [ ] **Step 7: 补 `FakeNavDevice`**
按 Interfaces 的动作键实现：`screenshot()` 返回 `make_screen(self.screen)`；`resolution()` 返回 `(1280,720)`；`tap/swipe/key/home` 计算动作键，命中 `transitions` 时改 `screen`，未命中原地；`home()` 把 screen 设为 `"home"` 并返回 `home_result`；`unknown_once` 只影响下一次 `screenshot()`。

- [ ] **Step 8: 跑绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/nav tests/agent/test_device_raw_tools.py > /tmp/m86-t4.log 2>&1; echo $?; tail -n 5 /tmp/m86-t4.log
grep -rn "run_atomic" maa_api/agent/tools/raw.py | wc -l
```
Expected：`0`；`run_atomic` 的 grep 行数 7（1 个定义 + 6 个调用点），与改名前的 `_run_atomic` 计数相同。

- [ ] **Step 9: 提交**

```bash
git add maa_api/agent/nav/navigator.py maa_api/agent/nav/__init__.py maa_api/agent/tools/raw.py tests/fakes/nav.py tests/agent/nav tests/agent/test_device_raw_tools.py
git commit -m "feat(m86): 导航执行器与设备适配（raw.run_atomic 改名）"
```

### Task 7（spec T7，依赖：无，执行顺序在 T5 之前）: 配置项

**Files:**
- Modify: `maa_api/settings.py`（`NavSettings`、`Settings.nav`、`SETTING_KEYS` 三行）；`maa_api/services/settings_schema.py`（`_FIELDS` 三行）；`config.template.yaml`（`nav:` 段）；`tests/test_settings.py`

**Interfaces:**
- Consumes: 现有 `Settings`/`SETTING_KEYS`/`_FIELDS`/`ENV_OVERRIDES`。
- Produces: `NavSettings(enabled=True, learn=True, max_steps=12)`；`Settings.nav`；`SETTING_KEYS` 139→142；`_FIELDS` 128→131；环境变量 `MAA_NAV_ENABLED`/`MAA_NAV_LEARN`/`MAA_NAV_MAX_STEPS` 自动派生。

- [ ] **Step 1: 写失败测试**
`tests/test_settings.py` 加三条：
- `test_nav_settings_defaults`：`Settings().nav.enabled is True`、`learn is True`、`max_steps == 12`。
- `test_nav_env_overrides`：`resolve_settings(env={"MAA_NAV_ENABLED":"false","MAA_NAV_LEARN":"false","MAA_NAV_MAX_STEPS":"30"})` 读到 false/false/30。
- `test_nav_fields_schema`：`schema_for("nav.enabled")` 的 `label=="界面导航图"`、`group=="Agent"`、`type=="boolean"`；`nav.max_steps` 的 `minimum==1`、`maximum==50`。

Run: `.venv/bin/python -m pytest -o addopts= -q tests/test_settings.py -k nav > /tmp/m86-t7-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t7-red.log`

Expected：非 0，`AttributeError: 'Settings' object has no attribute 'nav'`。

- [ ] **Step 2: 实现配置**
`settings.py`：`class NavSettings(BaseModel)`（`extra="ignore"`；`enabled: bool = True`、`learn: bool = True`、`max_steps: int = Field(default=12, ge=1, le=50)`）；`Settings` 加 `nav: NavSettings = Field(default_factory=NavSettings)`；`SETTING_KEYS` 加三行 `("nav","enabled"/"learn"/"max_steps")`，第二项写全元组。

`settings_schema.py`：`_FIELDS` 加三条，`label/group/type/hot_action` 与 spec §4.4 逐字一致（`nav.max_steps` 带 `minimum=1, maximum=50`）。

`config.template.yaml` 追加 spec §4.4 的 `nav:` 段逐字（含中文注释与默认值）。

- [ ] **Step 3: 跑绿与计数**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/test_settings.py tests/test_vapid_settings.py > /tmp/m86-t7.log 2>&1; echo $?; tail -n 3 /tmp/m86-t7.log
.venv/bin/python -c "from maa_api.settings import SETTING_KEYS, CONFIG_ONLY_SETTING_KEYS; from maa_api.services.settings_schema import _FIELDS; print(len(SETTING_KEYS), len(_FIELDS), len(CONFIG_ONLY_SETTING_KEYS))"
```
Expected：`0`；`142 131 13`。

- [ ] **Step 4: 提交**

```bash
git add maa_api/settings.py maa_api/services/settings_schema.py config.template.yaml tests/test_settings.py
git commit -m "feat(m86): 导航图三个配置键"
```

### Task 5（spec T5，依赖：T3、T4、T7）: 工具、策略与目录

**Files:**
- Create: `maa_api/agent/tools/nav.py`；`tests/agent/test_nav_tools.py`
- Modify: `maa_api/agent/tools/__init__.py`（在 `register_raw_tools` 之后注册 nav 工具）；`maa_api/agent/policy.py`（`ATOMIC_TOOL_NAMES` 加 `navigate_to`）；`tests/agent/test_tool_catalog.py`（`EXPECTED_TOOLS`、104/103→107/106、device 集合与计数、refetchable、v3_only）；`tests/agent/specs/test_agent_specs.py`（`len(definitions)==107`、`M86_TOOLS` 并入 legacy 排除集）

**Interfaces:**
- Consumes: `maa_api.agent.nav` 包级导入的 `NavGraphService`、`Recognizer`、`Navigator`；`maa_api.agent.nav.recognizer.ModelVisionClient`、`AppNavDevice`、`get_settings().nav`、`PolicyEngine`。
- Produces:
  - `WhereAmIParams`/`RememberScreenParams`/`NavigateToParams`（`extra="forbid"`，字段照 spec §4.3.1）。
  - `where_am_i(params, context) -> dict`、`remember_screen(...)`、`navigate_to(...)`、`register_tools(registry)`。
  - 三条 `ToolDefinition`：`where_am_i` group `nav`、`SAFE`、`device="read"`、`read_only=True`、`refetchable=True`、`rest_visible=True`；`remember_screen` `SAFE`、`device="none"`、`read_only=False`、`refetchable=False`、`rest_visible=False`；`navigate_to` `DANGEROUS`、`device="exclusive"`、`read_only=False`、`refetchable=False`、`rest_visible=True`。
  - `ATOMIC_TOOL_NAMES` 含 `navigate_to`。

- [ ] **Step 1: 写目录失败测试**
改 `tests/agent/test_tool_catalog.py`：
- `EXPECTED_TOOLS` 加三个名字。
- `test_untrusted_output_tools_are_pinned`（`len(definitions)` 与模型可见数在 :135-136）与 `test_registry_exposes_all_delivered_m11_tools_and_no_deferred_copilot_runner`（`len(actual)` 与模型可见数在 :155-156）的 104→`107`、模型可见 103→`106`。
- `test_device_levels_follow_the_contract` 的 exclusive 集合加 `navigate_to`、计数 8→9；read 集合加 `where_am_i`、计数 4→5；`len(by_device["none"]) == len(registry.list()) - 14`。
- `test_extension_fields_keep_v2_defaults` 的 `v3_only` 加 `remember_screen`。
- `test_refetchable_tools_are_pinned` 的 `EXPECTED_REFETCHABLE` 加 `where_am_i`，`len(...) == 51`。
- `UNTRUSTED_OUTPUT_TOOLS` 保持 22 不动。

改 `tests/agent/specs/test_agent_specs.py`：`len(definitions) == 104`→`107`；定义 `M86_TOOLS = frozenset({"where_am_i","remember_screen","navigate_to"})` 并并入 `test_legacy_chat_lists_every_v2_registered_tool_in_registration_order` 的排除集。

Run: `.venv/bin/python -m pytest -o addopts= -q tests/agent/test_tool_catalog.py tests/agent/specs/test_agent_specs.py > /tmp/m86-t5-catalog-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t5-catalog-red.log`

Expected：非 0，工具集合与计数不符。

- [ ] **Step 2: 写工具行为失败测试**
`tests/agent/test_nav_tools.py`（用 `engine, factory = asyncio.run(make_session_factory(tmp_path / "nav-tools.db"))` 建库，`make_session_factory` 是 async、返回 `(engine, factory)`（照 `tests/agent/tickets/conftest.py:32`），`NavGraphService(factory)` 种子、`FakeNavDevice`、`tmp_settings`；用例结束 `engine.sync_engine.dispose()`）：
- `test_nav_tools_registered_with_extension_fields`：group `nav`；三个 `device` 分别为 `read/none/exclusive`；`read_only/refetchable/rest_visible` 与 §4.3.1 一致。
- `test_where_am_i_is_pure_read`：调用前后 `graph()` 的节点/样本与 `Screenshot` 行数完全一致；`note_seen` 不落库。
- `test_navigate_to_uses_atomic_grant`、`test_rest_navigate_to_needs_confirmation_each_time`、`test_where_am_i_is_safe`：用 `PolicyEngine.evaluate` 断言内置无授权 → `grant_atomic_ops`、有授权 → 放行；REST 每次确认；`where_am_i` 不确认。
- `test_remember_screen_creates_node_and_exemplar`：节点 `origin=learned`、样本 `source=agent`、返回 `created=true`。
- `test_nav_disabled_raises_service_unavailable`：`monkeypatch.setattr(nav_tools, "get_settings", lambda: SimpleNamespace(nav=SimpleNamespace(enabled=False, learn=True, max_steps=12)))`，三个工具都抛 `SERVICE_UNAVAILABLE`、`details["setting"]=="nav.enabled"`。

Run: `.venv/bin/python -m pytest -o addopts= -q tests/agent/test_nav_tools.py > /tmp/m86-t5-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t5-red.log`

Expected：非 0，`maa_api.agent.tools.nav` 不存在。

- [ ] **Step 3: 实现三个工具**
`tools/nav.py`：
- `_service(context)` 从 `context.request.app.state.nav_service` 取服务，缺失或 `not get_settings().nav.enabled` 抛 `SERVICE_UNAVAILABLE(details={"setting":"nav.enabled"})`。
- `_recognizer(app_state, context)` 现建 `ModelVisionClient(app_state, run_id=origin.run_id if origin else None, session_id=context.session_id)` 与 `Recognizer(service, vision)`。
- `where_am_i`：`Navigator(service, recognizer, service.make_device(context), learn=False, max_steps=1, session_id=context.session_id)`，取 `(rec, _image)`，`service.note_seen(context.session_id, rec.node_id)`，按 spec §4.3.1 的 JSON 形状返回；`confidence` 用 `1 - distance/(2*max_distance)` 夹到 0–1，视觉取模型值，none 为 0；候选最多 3。
- `remember_screen`：先校验 `NODE_ID_PATTERN`、title 非空、`kind` 与 `stage:` 前缀矛盾时抛 `INVALID_PARAMETER(details={"field": ...})`；再调 `Navigator(service, recognizer, service.make_device(context), learn=settings.nav.learn, max_steps=settings.nav.max_steps, db_session=context.db_session, session_id=context.session_id).remember(node_id=..., title=..., kind=...)`。`remember` 是显式工具，不因 `nav.learn=false` 跳过；内部要归档截图（`archive_screenshot`）、在加样本/学边前 `await db_session.commit()`，并按 `session_id` 与 `LastSeenStore` 游标查 `agent_audit` 的 `click/swipe/key_event` 成功记录；`db_session` 与 `session_id` 缺一不可。
- `navigate_to`：`max_steps = min(params.max_steps or settings.nav.max_steps, settings.nav.max_steps)`；调 `Navigator(..., learn=settings.nav.learn, max_steps=max_steps, db_session=context.db_session, session_id=context.session_id).navigate(target)`；把 `NavResult` 转成 §4.3.1 的 JSON（`learned={"edges","exemplars"}`）。

在 `tools/__init__.py` 的 `register_raw_tools` 之后加 `register_nav_tools(registry)`；`policy.py` 的 `ATOMIC_TOOL_NAMES` 加 `navigate_to`。

- [ ] **Step 4: 跑绿与工具计数**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_tool_catalog.py tests/agent/test_nav_tools.py tests/agent/test_registry_policy.py tests/agent/specs/test_agent_specs.py > /tmp/m86-t5.log 2>&1; echo $?; tail -n 5 /tmp/m86-t5.log
.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"
```
Expected：`0`；`107 106`。

- [ ] **Step 5: 提交**

```bash
git add maa_api/agent/tools/nav.py maa_api/agent/tools/__init__.py maa_api/agent/policy.py tests/agent/test_nav_tools.py tests/agent/test_tool_catalog.py tests/agent/specs/test_agent_specs.py
git commit -m "feat(m86): 导航三工具、原子授权与工具目录"
```

### Task 6（spec T6，依赖：T1、T3、T5、T7）: REST 端点、错误码与装配

**Files:**
- Create: `maa_api/api/routers/nav.py`；`tests/api/test_nav_router.py`
- Modify: `maa_api/api/schemas/agent_v3.py`（`NavExemplarOut`、`NavRecognizerModel`、`NavNodeOut`、`NavEdgeOut`、`NavGraphOut`、`NavNodePut`、`NavEdgePut`）；`maa_api/domain/errors.py`（三个 `NAV_*` 码与 `ERROR_HTTP_STATUS`）；`maa_api/main.py`（导入/挂路由、`app.state.nav_service`、`ensure_seed()`）；`tests/domain/test_errors.py`（`ALL_CODE_COUNT` 168→171、`HTTP_BOUND_CODE_COUNT` 167→170、`SECTION_COUNT` 21→22）；`docs/v3/14-现行接口清单.md`（§4.22 三行、§4 总数 171、§6 总数 186、§6.15 83 与四行）；`docs/v3/15-开放API接入指南.md`（重新生成）；`web/openapi.json`、`web/src/types/api.d.ts`（生成）

**Interfaces:**
- Consumes: `NavGraphService`、`NavAction`/`validate_action`、`ScreenshotRepository`、`get_settings`、`require_auth`、`error_responses`。
- Produces:
  - `NAV_NODE_NOT_FOUND` 404、`NAV_PATH_NOT_FOUND` 409、`NAV_STEP_FAILED` 409；`ErrorCode` 171/170/22。
  - `GET /api/agent/nav/graph`（`get_nav_graph`）→ 200 `NavGraphOut`；`PUT /api/agent/nav/nodes/{node_id}`（`put_nav_node`）→ 200 `NavNodeOut`；`PUT /api/agent/nav/edges/{edge_id}`（`update_nav_edge`）→ 200 `NavEdgeOut`；`DELETE /api/agent/nav/edges/{edge_id}`（`delete_nav_edge`）→ 204。
  - `app.state.nav_service = NavGraphService(db_session.session_factory)`，启动 `await ensure_seed()`，失败只 `logger.exception`。

- [ ] **Step 1: 写错误码失败测试**
`tests/domain/test_errors.py` 改三个计数常量，并让 `DOC_ROWS` 解析到 14 §4.22 的三行（同批改 14：先加 §4.22 三行与总数，再跑测试）。Run: `.venv/bin/python -m pytest -o addopts= -q tests/domain/test_errors.py > /tmp/m86-t6-errors-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t6-errors-red.log` → 非 0。

- [ ] **Step 2: 写路由失败测试**
`tests/api/test_nav_router.py` 用 `tests/api/conftest.py` 的 `tmp_settings`/`isolated_db`/`make_client`。注意 `isolated_db` 只隔离不建表（`tests/api/conftest.py:32-33`）：先 `from sqlmodel import SQLModel`、`import maa_api.db.models`，按 `tests/api/test_triggers_router.py:46-48` 写 `_create_schema(engine)`（`async with engine.begin() as connection: await connection.run_sync(SQLModel.metadata.create_all)`），在 `client = make_client(app)` 后 `client.portal.call(_create_schema, isolated_db)`，再 `client.portal.call(app.state.nav_service.ensure_seed)`；否则 `ensure_seed()` 报 `no such table: nav_node`。被测 app 现搭：`FastAPI()` + `register_exception_handlers` + `include_router(nav.router)` + `app.state.nav_service = NavGraphService(db_session.session_factory)`（`from maa_api.db import session as db_session`）+ `app.state.device_manager` 假设备。按 spec §6 写：
- `test_get_graph_returns_seed`、`test_put_node_creates_user_node`、`test_put_node_rejects_bad_id`。
- `test_put_node_add_current_exemplar`、`test_put_node_recomputes_exemplars_when_rois_change`、`test_put_node_removes_exemplar_by_phash`。
- `test_put_edge_creates_and_updates`、`test_put_edge_rejects_endpoint_change`、`test_put_edge_unknown_without_fields_is_404`、`test_put_edge_unknown_node_is_404`、`test_delete_edge`。
- `test_put_node_add_exemplar_rejects_expired_screenshot`（410）、`test_put_node_add_exemplar_missing_screenshot_is_404`。
- `test_put_node_add_current_exemplar_device_not_connected`：`app.state.device_manager.screenshot` 抛 `AppError(ErrorCode.DEVICE_NOT_CONNECTED)`，PUT 节点 body 含 `add_exemplar="current"` → 503 `DEVICE_NOT_CONNECTED`（spec §4.3.2）。
- `test_rest_navigate_to_preempts_without_holding_lease`：从 `maa_api.api.routers.agent` 导入 `_rest_preempt_hook`，用 `SimpleNamespace(registry=...)` 与 `Request` 替身，断言 `navigate_to` 的钩子被返回、`where_am_i`/`remember_screen` 返回 None；monkeypatch `preempt_device` 记录调用，调用钩子一次断言 `preempt_device` 被调、没有租约服务参与。

Run: `.venv/bin/python -m pytest -o addopts= -q tests/api/test_nav_router.py > /tmp/m86-t6-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t6-red.log` → 非 0。

- [ ] **Step 3: 加三个错误码**
`errors.py` 的 `ErrorCode` 三个成员各上一行写中文注释；`ERROR_HTTP_STATUS` 三条；模块注释里的计数改 171/170/22。不要复用 v2 的 `NOT_FOUND` 给导航目标。

- [ ] **Step 4: 加请求/响应模型**
`api/schemas/agent_v3.py` 按 spec §4.3.2 逐字段加七个模型；`NavRecognizerModel` 的 `rois` 默认 `[[0,0,1,1]]`、`min_length=1, max_length=4`、`max_distance` 1–32、`description` ≤200、`generic`；`NavNodePut`/`NavEdgePut` `extra="forbid"`；`NavExemplarOut.thumb_url` 允许 None；`NavGraphOut` 含 `enabled/learn/vision_available`。时间字段输出 ISO 8601 `Z`（用现有 `_iso_z` 风格）。

- [ ] **Step 5: 实现路由**
`api/routers/nav.py`，tag `agent`，`dependencies=[Depends(require_auth)]`，每个端点 `responses=error_responses(...)` 列出 spec §4.3.2 的码：
- `get_nav_graph`：`await service.graph()`；`enabled/learn` 取 `get_settings().nav`；`vision_available = await ModelVisionClient(request.app.state).available()`；exemplar 的 `thumb_url` 从 `ScreenshotRepository.get(screenshot_id)` 的 `path` 末段 sha 生成 `/api/images/{sha}/thumb`，查不到/`deleted_at` 非空为 None。
- `put_nav_node`：校验 `NODE_ID_PATTERN`（400 `field=node_id`）；新建 `origin="user"`、改已有不改 `origin`；`recognizer.rois` 变化时按每个样本 `screenshot_id` 读归档文件重算 `phash`，文件丢失/`screenshot_id` 为空的样本删掉；`add_exemplar='current'` 现截一张图（只读设备，不取租约），`archive_screenshot` 后立即 `await session.commit()` 再 `add_exemplar(source="user")`；截图抛 `DEVICE_NOT_CONNECTED` 时不吞，按原 code 返回 503；`add_exemplar_screenshot_id` 用已有截图（404/410，不归档）；两者都给 400；`remove_exemplars` 按 `phash` 删；样本上限 8 的淘汰规则照 §4.3.2。
- `update_nav_edge`：路径 id 必须是 UUID 形状（否则 400）；不存在时要求 `from_node/to_node/action` 齐全，缺任一 404 `NOT_FOUND`，端点不存在 404 `NAV_NODE_NOT_FOUND`，`origin="user"`，`upsert_edge(edge_id=path_id)`；存在时只允许改 `enabled/action/cost_ms`，端点不同 400、相同忽略，`action` 重算 hash 冲突 400 `field=action`，`enabled` false→true 清 `fail_count`。
- `delete_nav_edge`：不存在 404 `NOT_FOUND`，存在物理删除，204。

- [ ] **Step 6: 装配与文档清单**
`main.py`：在 `app.state.channel_service = channel_service` 之后、`update_scheduler = AsyncIOScheduler()` 之前加 `app.state.nav_service = NavGraphService(db_session.session_factory)` 和 `try: await ...ensure_seed() except Exception: logger.exception(...)`；`create_app()` 的 `include_router` 列表加 `nav.router`；顶部 import 列表加 `nav`。不新增 tag。

`docs/v3/14-现行接口清单.md`：§4 末尾加 `### 4.22 界面导航图` 三行（码/HTTP/含义），§4 开头的「共 168 条」改 171；§6 开头的「共 182 条」改 186，§6.15 的「（79 条）」改「（83 条）」并在表末追加四行（GET/PUT/PUT/DELETE，请求要点与主要错误码照 spec §4.3.2）。

- [ ] **Step 7: 生成 OpenAPI 与开放 API 指南**

```bash
.venv/bin/python scripts/dump_openapi.py
.venv/bin/python scripts/generate_api_guide.py
(cd web && pnpm gen:api)
.venv/bin/python scripts/dump_openapi.py --check; echo $?
```
Expected：`0`。确认 `web/openapi.json` 的 operationId 是 `agent_get_nav_graph`、`agent_put_nav_node`、`agent_update_nav_edge`、`agent_delete_nav_edge`。

- [ ] **Step 8: 跑绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/api/test_nav_router.py tests/domain/test_errors.py tests/domain/test_route_inventory.py tests/api/test_app_skeleton.py > /tmp/m86-t6.log 2>&1; echo $?; tail -n 5 /tmp/m86-t6.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
```
Expected：两个 `0`；19 个 tag 断言不变；端点 186/agent 83。

- [ ] **Step 9: 提交**

```bash
git add maa_api/api/routers/nav.py maa_api/api/schemas/agent_v3.py maa_api/domain/errors.py maa_api/main.py tests/domain/test_errors.py tests/api/test_nav_router.py docs/v3/14-现行接口清单.md docs/v3/15-开放API接入指南.md web/openapi.json web/src/types/api.d.ts
git commit -m "feat(m86): 导航图 REST 端点、错误码与装配"
```

### Task 8（spec T8，依赖：T5）: 代理定义

**Files:**
- Modify: `maa_api/agent/specs/builtin/navigator.md`；`tests/api/test_agent_specs_router.py`（`EXPECTED_VERSIONS["navigator"]` 1→2）；`tests/agent/specs/test_agent_specs.py`（加 `test_navigator_exposes_nav_tools`）

**Interfaces:**
- Consumes: 工具目录中的三个 nav 工具。
- Produces: `navigator` 定义 `version: 2`、`tools` 末尾三个工具、正文追加「## 界面导航图」六条；`general` 保持 `version: 6`。

- [ ] **Step 1: 写失败测试**
`tests/api/test_agent_specs_router.py` 的 `EXPECTED_VERSIONS["navigator"]` 改 2；`tests/agent/specs/test_agent_specs.py` 加：

```python
def test_navigator_exposes_nav_tools():
    navigator = builtin_spec("navigator")
    assert {"where_am_i", "remember_screen", "navigate_to"} <= set(navigator.tools)
    assert set(navigator.tools) >= {"get_screenshot", "click", "swipe", "back_to_home"}
    general = builtin_spec("general")
    assert not {"where_am_i", "remember_screen", "navigate_to"} & set(general.tools)
```

Run: `.venv/bin/python -m pytest -o addopts= -q tests/agent/specs/test_agent_specs.py tests/api/test_agent_specs_router.py > /tmp/m86-t8-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t8-red.log` → 非 0。

- [ ] **Step 2: 改定义**
`navigator.md` 的 frontmatter `tools` 末尾加 `where_am_i, remember_screen, navigate_to`，`version: 1`→`2`；正文末尾追加 spec §4.7 的「## 界面导航图」与六条，逐字复制（不增删标点）。`general.md` 不动。

- [ ] **Step 3: 跑绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/specs tests/api/test_agent_specs_router.py > /tmp/m86-t8.log 2>&1; echo $?; tail -n 3 /tmp/m86-t8.log
```
Expected：`0`；`navigator` 版本 2、`general` 版本 6。

- [ ] **Step 4: 提交**

```bash
git add maa_api/agent/specs/builtin/navigator.md tests/api/test_agent_specs_router.py tests/agent/specs/test_agent_specs.py
git commit -m "feat(m86): 导航员定义加三个导航工具"
```

### Task 9（spec T9，依赖：T6、T7，类型已生成）: 前端导航图页

**Files:**
- Create: `web/src/features/nav/NavGraphPage.tsx`、`NodeList.tsx`、`NodeEditor.tsx`、`EdgeTable.tsx`、`EdgeEditor.tsx`、`api.ts`、`NavGraphPage.test.tsx`
- Modify: `web/src/routes/router.tsx`、`web/src/components/layout/nav.ts`、`web/src/api/keys.ts`；`web/src/routes/more/more.test.tsx`

**Interfaces:**
- Consumes: 生成的 `components['schemas']['NavGraphOut']`、`NavNodeOut`、`NavNodePut`、`NavEdgeOut`、`NavEdgePut`；`api`/`keys`/`toApiError`；`Section`、`ListGroup`、`useConfirm`。
- Produces:
  - `api.ts`：`fetchNavGraph()`、`putNavNode(nodeId, body)`、`putNavEdge(edgeId, body)`、`deleteNavEdge(edgeId)`、`invokeWhereAmI()`（`POST /api/agent/tools/where_am_i/invoke`，`{arguments:{}}`）。
  - `keys.agent.navGraph()`；`NavGraphPage` 默认导出。
  - 组件 props：`NodeList({nodes, onSelect, onCreate})`、`NodeEditor({node, open, onClose, onSaved})`、`EdgeTable({edges, nodes, onToggle, onDelete, onCreate})`、`EdgeEditor({open, nodes, onClose, onSaved})`。

- [ ] **Step 1: 写前端失败测试**
`NavGraphPage.test.tsx` 用 `vi.stubGlobal('fetch')` 假后端，按 `web/src/routes/more/more.test.tsx:47-50` 用 `QueryClient` + `QueryClientProvider` 包住 `RouterProvider`，`createMemoryRouter(routeConfig, { initialEntries: ['/more/nav'] })` 在 `/more/nav` 渲染，写五个用例（名字照 spec §6）：`renders nodes and edges`、`toggles an edge`、`deletes an edge after confirm`、`creates a tap edge`、`shows where_am_i result`。断言：
- 首次 GET `/api/agent/nav/graph`，页面出现节点标题与「{n} 个界面 · {m} 条跳转」。
- 切换开关 PUT `/api/agent/nav/edges/{id}` body `{enabled:false}`，显示「已停用跳转」。
- 删除先出现确认弹窗标题「删除这条跳转？」，确认后 DELETE，显示「已删除跳转」。
- 新建跳转 PUT 路径为客户端生成的 UUID，body 含 `from_node`/`to_node`/`action: {type:'tap', x, y}`。
- 点「识别当前画面」POST `/api/agent/tools/where_am_i/invoke`，body `{arguments:{}}`，页面显示返回的 `node.title` 或「没有认出当前画面」。

`more.test.tsx` 在 `groups the secondary destinations...` 加两条断言：`screen.getByRole('link', { name: /界面导航图/ })` 指向 `/more/nav`；`routeConfig` 的 `/` children 含 `more/nav`。

Run: `(cd web && pnpm test --run src/features/nav src/routes/more) > /tmp/m86-t9-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t9-red.log` → 非 0。

- [ ] **Step 2: 接入路由与入口**
`keys.ts` 的 `agent` 下加 `navGraph: () => [...root,'agent','nav','graph'] as const`；`nav.ts` 从 `lucide-react` 导入 `Route` 并在 `secondaryNav` 的 `agent` 组（`/more/agents` 后）加 `{ to:'/more/nav', label:'界面导航图', icon:Route, description:'常用界面与跳转，导航员按它一步走到', group:'agent' }`；`router.tsx` 加 `const NavGraphPage = lazy(() => import('@/features/nav/NavGraphPage'))` 与 `{ path:'more/nav', element: page(<NavGraphPage />) }`。

- [ ] **Step 3: 实现页面**
按 spec §4.6 实现：顶部 `Section` 标题「界面导航图」、描述计数、hint（视觉不可用时补一句）、按钮「识别当前画面」；「界面」卡片列表（标题/id/类型/来源/缩略图）+ `NodeEditor`（标题与类型、特征描述、标记当前画面、样本移除、折叠高级区 JSON 编辑 `rois`/`max_distance`、新建界面 id 校验 `NODE_ID_PATTERN`）；「跳转」表格（从/到/动作/成功失败/耗时/状态）+ 开关 + 删除确认 + `EdgeEditor`（从/到/点击 x,y）；空状态「还没有界面节点」/「重启服务后会写入种子界面」；Toast 与错误文案照 spec §4.6 逐字。三个写操作成功后 `queryClient.invalidateQueries({queryKey: keys.agent.navGraph()})`；识别结果只放本地 state，不写缓存。

- [ ] **Step 4: 跑绿与构建**

```bash
(cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run src/features/nav src/routes/more) > /tmp/m86-t9.log 2>&1; echo $?; tail -n 5 /tmp/m86-t9.log
(cd web && pnpm build) > /tmp/m86-t9-build.log 2>&1; echo $?; tail -n 3 /tmp/m86-t9-build.log
```
Expected：两个 `0`；五个导航图用例与「更多」页两条断言通过。

- [ ] **Step 5: 提交**

```bash
git add web/src/features/nav web/src/routes/router.tsx web/src/components/layout/nav.ts web/src/api/keys.ts web/src/routes/more/more.test.tsx
git commit -m "feat(m86): 前端导航图页与入口"
```

### Task 10（spec T10，依赖：T4、T5、T6、T7）: 冒烟脚本

**Files:**
- Create: `scripts/nav_smoke.py`

**Interfaces:**
- Consumes: `tests.fakes.lifespan.install_fake_lifespan`、`tests.fakes.nav.FakeNavDevice`、`NavGraphService`、`build_registry`、`ToolContext`、`CallerType`、`db_session`。
- Produces: 临时目录内端到端冒烟，成功末行严格 `SMOKE OK`，失败打印 `[FAIL] 原因` 并返回 1。

- [ ] **Step 1: 写脚本骨架与隔离**
照 `scripts/agent_smoke.py`：`TEMP_PREFIX`、`tempfile.mkdtemp`、把 `db_session.DB_PATH/ASYNC_URL/SYNC_URL/engine/session_factory`、`settings_module.DEFAULT_CONFIG_PATH`、`main_module.REPO_ROOT` 指到临时目录；清 `ENV_OVERRIDES`；`install_fake_lifespan(main_module.app)`；`TestClient(...).__enter__()`。脚本结束恢复全部单例并 `dispose` 引擎。

- [ ] **Step 2: 写断言**
在 `client.portal` 里：
1. `await app.state.nav_service.ensure_seed()` 后 `graph()` 节点 15、边 36。
2. 把 `app.state.nav_service.device_factory` 设为 `lambda _ctx: FakeNavDevice(transitions={"tap:1045,630":"infrast","tap:90,40":"home"}, start="home")`。
3. `registry = build_registry()`；在 `client.portal.call` 的 async 函数里 `async with db_session.session_factory() as session:` 构造 `context = ToolContext(caller=CallerType.INTERNAL, session_id="nav-smoke-session", request_id="nav-smoke", request=SimpleNamespace(app=app), db_session=session)`，调 `await registry.execute("where_am_i", {}, context)`，断言 `node is None`、`method=="none"`。
4. 同一 `context` 调 `await registry.execute("navigate_to", {"target":"infrast"}, context)`，断言 `arrived=true`、`steps==2`、`trail` 首项 `action=="home"`、home 与 infrast 各有样本；从库读 home→infrast 边 `success_count==1`。
5. 同一 `context` 调 `await registry.execute("where_am_i", {}, context)`，断言 `node.id=="infrast"`、`method=="hash"`。
6. 把设备换到同一 `FakeNavDevice`（或改其 `transitions` 去掉 `tap:1025,500`），连续三次 `await registry.execute("navigate_to", {"target":"recruit"}, context)`：每次都在 `try/except AppError` 里捕获 `NAV_PATH_NOT_FOUND`；第三次后从库读该边 `enabled=false`、`fail_count>=3`。
7. 把 `device.screen = "stage:1-7"`，调 `await registry.execute("remember_screen", {"node_id":"stage:1-7","title":"1-7"}, context)`，断言返回 `node.created==true`、`exemplar_added==true`、新节点 `origin=="learned"`、样本 `source=="agent"`。

每个断言失败抛 `SmokeFailure`，`main()` 捕获后打印 `[FAIL] ...` 返回 1。

- [ ] **Step 3: 跑冒烟**

```bash
.venv/bin/python scripts/nav_smoke.py > /tmp/m86-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m86-smoke.log
```
Expected：`0`、`SMOKE OK`。确认脚本没有读写仓库的 `resource/maa_api.db` 与 `config.yaml`（临时目录之外无新文件）。

- [ ] **Step 4: 提交**

```bash
git add scripts/nav_smoke.py
git commit -m "feat(m86): 导航图端到端冒烟"
```

### Task 11（spec T11，依赖：T10 与 evals-ticket-driver 补丁）: 评测场景与假导航世界

**Files:**
- Modify: `maa_api/agent/evals/scenario.py`（只加 `NavWorld` 与 `World.nav`；`HumanPolicy` 由补丁提供，不改）；`tests/evals/environment.py`（`world.nav` 绑定假设备）；`tests/evals/test_regression_replay.py`（11→13）；`tests/evals/test_eval_scenario.py`（加 `test_nav_world_parses`）
- Create: `tests/evals/scenarios/nav-infrast-navigate-to.yaml`；`tests/evals/scenarios/nav-unknown-stage-remember.yaml`；`tests/evals/cassettes/nav-infrast-navigate-to.jsonl`；`tests/evals/cassettes/nav-unknown-stage-remember.jsonl`

**Interfaces:**
- Consumes: `drive_ticket`、`Scenario.human`、`HumanPolicy`、`tests.fakes.nav.FakeNavDevice`、`_open_world`。
- Produces: `World.nav: NavWorld | None = None`；`NavWorld(start="home", transitions={})`（`extra="forbid"`）；两个 capability 场景与 cassette。

- [ ] **Step 1: 写解析失败测试**
`tests/evals/test_eval_scenario.py` 加 `test_nav_world_parses`：写一个最小场景 YAML（含 `entry.kind: ticket` 与 `world.nav.start/transitions`），`load_scenario` 后断言 `scenario.world.nav.start == "home"`、`transitions["tap:1045,630"] == "infrast"`；另写未知字段 `world.nav.unknown` 时 `pytest.raises(ScenarioError)`。

Run: `.venv/bin/python -m pytest -o addopts= -q tests/evals/test_eval_scenario.py::test_nav_world_parses > /tmp/m86-t11-red.log 2>&1; echo $?; tail -n 5 /tmp/m86-t11-red.log` → 非 0。

- [ ] **Step 2: 加 `NavWorld`**
在 `scenario.py` 的 `World` 前加：

```python
class NavWorld(BaseModel):
    model_config = _MODEL_CONFIG
    start: str = "home"
    transitions: dict[str, str] = Field(default_factory=dict)
```

`World` 加 `nav: NavWorld | None = None`；`__all__` 加 `"NavWorld"`。不改 `HumanPolicy`/`Entry`/`WorldSeed`/`Scenario`。

- [ ] **Step 3: 绑定假设备**
`tests/evals/environment.py` 的 `_open_world` 在 `client.__enter__()` 之后、`yield` 之前：

```python
nav = scenario.world.nav
if nav is not None:
    from tests.fakes.nav import FakeNavDevice
    device = FakeNavDevice(transitions=dict(nav.transitions), start=nav.start)
    app.state.nav_service.device_factory = lambda _context: device
```

`app.state.nav_service` 由 T6 的启动装配提供；为空时断言失败并打印。

- [ ] **Step 4: 写两个场景 YAML**
`nav-infrast-navigate-to.yaml` 与 `nav-unknown-stage-remember.yaml` 逐字照 spec §5 T11 的 JSON 内容（`id/title/suite/agent/source/tags/entry/human/settings/world/expect/limits` 全字段）；场景一 `world.nav.start="home"`、`transitions={"tap:1045,630":"infrast","tap:90,40":"home"}`；场景二 `world.screen="stage"`、`world.nav.start="stage:1-7"`、`transitions={}`。

- [ ] **Step 5: 写两条 cassette**
注意 spec §5 T11 的「7/9 条」是补丁前口径；`evals-ticket-driver` 的 R7 要求以 `ready` 结束的 ticket 场景末尾再有一条记忆抽取应答，所以本计划按 8/10 条写，回放时以 cassette 实际消费顺序为准。第一行 header：

```json
{"type":"header","format":1,"scenario":"nav-infrast-navigate-to","source":"handwritten","recorded_at":"2026-11-09T00:00:00Z","model":"eval-model","agent":"general","spec_version":null,"notes":"M86：ticket 驱动导航到基建"}
```

之后按顺序写 `{"type":"call","channel":"agent","reply":...}`。场景一第一条 `delegate` 的完整行如下（其余行只换 `reply`）：

```json
{"type":"call","channel":"agent","reply":{"tool_calls":[{"id":"call-delegate","name":"delegate","arguments":{"agent":"navigator","task":"导航到基建","context":"目标界面：infrast（基建）。先 where_am_i，再 navigate_to。"}}],"finish_reason":"tool_calls"}}
```

探查两条固定写成 `{"type":"call","channel":"agent","reply":{"text":"OK"}}`（档案首次使用的动态上下文探查：system 开头、无 tools，`classify_request` 归 agent 通道，归 probe 会分错通道）；场景一的 tool id 顺序：

| 序号 | 发出方 | 工具/内容 | tool call id |
|---|---|---|---|
| 1 | 探查 | 文本 `{"text":"OK"}` | — |
| 2 | 探查 | 文本 `{"text":"OK"}` | — |
| 3 | Worker | `delegate` | `call-delegate` |
| 4 | 导航员 | `where_am_i` | `call-where-1` |
| 5 | 导航员 | `navigate_to` | `call-navigate` |
| 6 | 导航员 | 最终正文 JSON（无 tool_calls） | — |
| 7 | Worker | `report` | `call-report` |
| 8 | memory_curator | 记忆抽取文本 `{"text":"{\"items\": []}"}` | — |

场景一其余应答的 arguments：4 `{}`；5 `{"target":"infrast"}`；6 的 `text` 是 JSON 字符串 `{"summary":"已到基建","findings":[{"title":"导航完成","detail":"navigate_to 两步到达基建","evidence":["navigate_to 结果"]}],"uncertainties":[],"artifacts":[]}`；7 `{"status":"ready","summary":"已到基建，导航员完成。"}`。场景以 `ready` 结束，末尾必须有第 8 条记忆抽取应答 `{"type":"call","channel":"agent","reply":{"text":"{\"items\": []}"}}`，漏写会 `exhausted`、多写会 `unused`。

场景二的 header `scenario`/`notes` 换成 `nav-unknown-stage-remember`；10 条应答依次为：探查 OK ×2、`delegate`（`call-delegate`，task「记下 1-7 界面」）、`where_am_i`（`call-where-1`）、`navigate_to`（`call-navigate`，`{"target":"stage:1-7"}`，返回错误 `NAV_NODE_NOT_FOUND` 由工具真实执行，不在 cassette 里写错误）、`remember_screen`（`call-remember`，`{"node_id":"stage:1-7","title":"1-7"}`）、第二次 `where_am_i`（`call-where-2`）、导航员最终正文 JSON（`summary` 写「已记下 1-7」）、Worker `report`（`{"status":"ready","summary":"已记下 1-7 界面。"}`）、记忆抽取 `{"text":"{\"items\": []}"}`。tool id 顺序为 `call-delegate` → `call-where-1` → `call-navigate` → `call-remember` → `call-where-2` → `call-report`，同样按请求到达顺序写，不重排。

- [ ] **Step 6: 改场景计数与回放**
`test_regression_replay.py` 的 `len(scenarios) == 11` 与 `len(ids) == 11` 改 13。Run：

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals > /tmp/m86-eval-unit.log 2>&1; echo $?; tail -n 5 /tmp/m86-eval-unit.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay --scenario nav-infrast-navigate-to --scenario nav-unknown-stage-remember > /tmp/m86-eval.log 2>&1; echo $?; tail -n 8 /tmp/m86-eval.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay --strict --scenario nav-infrast-navigate-to --scenario nav-unknown-stage-remember > /tmp/m86-eval-strict.log 2>&1; echo $?; tail -n 8 /tmp/m86-eval-strict.log
```
Expected：三个 `0`；两个场景 `通过`、`模型调用` 分别 ≤ 12 与 ≤ 14；`--strict` 不报 `mismatch`/`exhausted`。

- [ ] **Step 7: 提交**

```bash
git add maa_api/agent/evals/scenario.py tests/evals/environment.py tests/evals/scenarios/nav-infrast-navigate-to.yaml tests/evals/scenarios/nav-unknown-stage-remember.yaml tests/evals/cassettes/nav-infrast-navigate-to.jsonl tests/evals/cassettes/nav-unknown-stage-remember.jsonl tests/evals/test_regression_replay.py tests/evals/test_eval_scenario.py
git commit -m "feat(m86): 导航评测场景与假导航世界"
```

### Task 12（spec T12，依赖：T1–T11）: 文档同步、全量验收、合并与 tag

**Files:**
- Modify: `docs/v3/02-系统架构.md`、`docs/v3/03-数据模型.md`、`docs/v3/04-接口与事件协议.md`、`docs/v3/05-运行时与代理定义.md`、`docs/v3/16-部署指南.md`、`docs/v3/13-决策记录.md`、`docs/v3/README.md`
- Modify（已由 T6 改过则只核对）: `docs/v3/14-现行接口清单.md`、`docs/v3/15-开放API接入指南.md`
- Create: `docs/v3/ledgers/m86-nav-graph.md`

**Interfaces:**
- Consumes: 前面全部任务的产出与验证日志。
- Produces: 文档与实现一致、台账、`git merge --no-ff nav-graph`、tag `v3-m86`。

- [ ] **Step 1: 逐项核对 §9**
- 02：核对 `NavGraphService` 服务表行、`nav/` 四文件、`screenshot_archive.py`、`scripts/nav_smoke.py`、§5 第 11 行 `nav_service`（升格时已预写；有出入则改成实现事实）。
- 03：核对 `nav_node.exemplars` 的 `source/added_at` 说明与 §13 两个枚举（已预写；不改 spec 之外的列）。
- 04：核对四个端点行、`NAV_*` 三行、`nav.*` 三行（已预写）。
- 05：把 §6.1 的「当前 104 个、模型可见 103 个（M85 收口基线）」改成「当前 107 个、模型可见 106 个（M86 收口基线）」，其余 M87–M90 预写值保留；核对 `navigator` 工具行与 §8 两个 schema 行。
- 16：加「界面导航图」一节，说明 `nav.enabled`、`nav.learn`、`nav.max_steps` 与视觉模型档案的关系；关闭 `nav.enabled` 后三个工具不可用、导航员退回逐次点击。
- 14/15：核对 T6 的 171/186/83 与生成结果；不一致时以生成命令重跑。
- README：状态行改为 **v3 路线（M63–M86）已全部完成（2026-11-09）**，tag 列表补 `v3-m86`。

- [ ] **Step 2: 写 13 §8**
在 §8 末尾（补丁已写 §8.179 时为 `### 8.180`）加一条 `### 8.180 界面导航图（M86，2026-11-09）`，写：用户口径、T7 提前到 T5 的顺序裁决、`remember_screen` 认出他节点时整体跳过、trail 七键、`where_am_i` 候选口径、REST 只抢占不持租约、种子坐标核对结果（`BAD []` 或差异逐条）、M89 复用 `screenshot_archive`。编号以 13 §8 尾部实际下一空号为准；被占时按当时空号并在台账注明。

- [ ] **Step 3: 写台账**
`docs/v3/ledgers/m86-nav-graph.md` 照 M85 台账结构写：分支/基点/合并/tag、spec/plan/13 链接、迁移、做了什么（T1–T12 与提交）、核心实现、验证证据（逐任务与最终验收表）、计数、Rulings I made（Task 0 的 12 条 + 实施中新增）、偏离与遗留。

- [ ] **Step 4: 全量验收（§7.1）**

```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/m86-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m86-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
(cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build) > /tmp/m86-web.log 2>&1; echo $?
.venv/bin/python scripts/frontend_smoke.py | tail -n 1
.venv/bin/python scripts/agent_smoke.py | tail -n 1
.venv/bin/python scripts/orchestrator_smoke.py | tail -n 1
.venv/bin/python scripts/nav_smoke.py | tail -n 1
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m86-reg.log 2>&1; echo $?
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m86-cap.log 2>&1; echo $?; tail -n 5 /tmp/m86-cap.log
git diff --check; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
```
Expected：pytest 不新增失败（起点 4 failed 仍在则记录，新增 0）；各退出码 `0`；四个冒烟末行 `SMOKE OK`；regression 全通过；capability 8/8、通过率不低于 M85 台账基线（6/6、100%）；`BROKEN 0`；`git diff --check` 0。真机补充验证（§7.3）做不到时在台账写「未验证」，不阻塞合并。

- [ ] **Step 5: 合并、tag、复验**

```bash
git status -sb
git checkout dev
git merge --no-ff nav-graph
git tag v3-m86
git branch -d nav-graph
git log --oneline -3
```

合并提交信息：`merge: 界面导航图（M86，见 docs/v3/13 §8.180）`。合并后在 `dev` 上重跑 Step 4 的 pytest、openapi check、nav_smoke 与两个评测套件，把结果补进台账；把 progress.md 复制为台账并提交 `docs(m86): 台账与里程碑收口`。**不 push**。

回滚口径（spec §8，不主动执行）：在 `dev` 上用合并提交的 hash 执行 `git revert -m 1 <merge>`，再 `.venv/bin/alembic downgrade 0040_question_expiry_historical`；降级会丢掉学到的导航图，执行前把 merge hash、原因与数据影响记进台账。

## Self-Review

1. **Spec coverage：** §0 → Task 0；§4.1 → Task 1；§4.2/§4.5.1–§4.5.2 → Task 2；§4.2 图服务/§4.5.3/§4.5.6 → Task 3；§4.2 导航器/§4.5.4 → Task 4；§4.3.1/§4.5.5 工具部分 → Task 5；§4.3.2/§4.5.5 REST → Task 6；§4.4 → Task 7；§4.7 → Task 8；§4.6 → Task 9；§5 T10 → Task 10；§5 T11 → Task 11；§5 T12/§7/§9 → Task 12；§6 的每个用例名都落在对应任务的 Step 里；§7.2 坐标核对在 Task 3 Step 8；§7.3 真机在 Task 12 Step 4 记为补充验证。
2. **Step scan：** 每个 Step 都是「写测试/跑红/实现/跑绿/核对命令/提交」之一，带命令与期望输出；没有「TODO」「待定」「视情况」「适当」；每个实现 Step 只给签名、常量与 spec 已钉死的值，没有把函数体写成代码。
3. **Type consistency：** 全计划统一使用 `NavGraphService`、`NavNode`、`NavEdge`、`PathStep`、`PathPlan`、`LastSeenStore`、`NavDevice`、`AppNavDevice`、`Performed`、`NavResult`、`Navigator`、`Recognition`、`VisionClient`、`ModelVisionClient`、`Recognizer`、`archive_screenshot`；T5/T6/T10/T11 调用的方法名与 T3/T4 的 Interfaces 完全一致；参数 `target/max_steps/node_id/title/kind/session_id` 在各任务间不换名。
4. **Review Focus：** 五条分别由 T2（视觉 unknown、structured failure）、T4（首达信任）、T6（REST 只抢占）、T11（cassette 探查/严格回放）、T9（nav.ts/路由/key）的用例覆盖；没有只写在 Review Focus、没有测试的条目。
5. **Proportion：** 本计划 857 行，spec 1245 行；代码块只保留签名、常量、测试断言与命令，不写实现正文；每个任务的验证命令都能在没看过仓库的情况下照跑，期望输出写的是退出码、计数或末行。
