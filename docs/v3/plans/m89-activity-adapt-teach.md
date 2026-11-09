# M89 新活动自适应与示教录制 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户能在 `/more/demonstrations` 用实时画面示教点击、滑动、长按，服务端在现有设备端点的成功路径采集步骤与前后截图并落 `demonstration` 表；`convert` 两阶段把步骤编译成 `Custom_*` 链并按需一次性注册；`general` Worker 用 `draft_custom_task`、`validate_custom_task` 与扩展后的 `register_custom_task(tasks=...)` 对新活动起草、校验、注册、试跑；录制期间由进程内闸门独占设备。

**Architecture:** T0 基线 → T1 枚举、错误码、表与迁移 → T2 服务状态机与录制闸门 → T3 设备端点采集接线 → T4 编译器、链级规则与批量注册 → T5 两个 SAFE 工具与 `general` 定义 → T6 四个 REST 端点、装配、配置键与清单 → T7 示教页、画布与转换预览 → T8 冒烟、评测、文档、计数、台账、验收与合并。每层只依赖前一层已钉死的接口，T1–T7 各自可独立验证。

**Tech Stack:** FastAPI + SQLModel + Alembic + pytest；Pydantic v2；`asyncio`；Pillow 截图；React 19 + TypeScript + TanStack Query + openapi-fetch + Vitest；MaaCore `tasks.json`（`JustReturn` / `ClickRect` / `Swipe` / `Stop`）。

**Spec:** [m89-activity-adapt-teach](../specs/m89-activity-adapt-teach.md)（655 行；本计划实现 §0、§4 的 D1–D24、§5 的 T1–T8、§6 测试、§7 验收、§9 文档、§11 契约）

## Global Constraints

- 分支 `activity-adapt-teach`，从 `dev`（M88 合并后的提交）新建；全程不 push；完成后 `git merge --no-ff` 回 `dev`，打 tag `v3-m89`，删除临时分支。
- 迁移只加 `0042_demonstration`，`down_revision="0041_nav_graph"`；`upgrade()` 建 `demonstration` 七列表，`downgrade()` 删表；不建索引、不做数据迁移。
- 工具总数/模型可见：`108/107` → `110/109`；端点总数/§6.15 agent：`186/83` → `190/87`；错误码/HTTP 绑定/节数：`171/170/22` → `173/172/23`；业务表 `57` → `58`；配置键 `SETTING_KEYS`/`_FIELDS`：`143/132` → `144/133`；评测场景 `21`→`23`（`tests/evals/test_regression_replay.py` 的两处断言与场景文件数一起改，见 Task 8）；refetchable `52` → `54`。
- `CUSTOM_TASK_INVALID` 保持 v2 的 422：04 §5 的前置提交已把它写成 422，M89 只核对不改（spec §9 的「400 改 422」无需执行，记台账）；M89 只新增 `DEMONSTRATION_NOT_FOUND`（404，`details={"id"}`）与 `DEMONSTRATION_STATE_INVALID`（409，`details={"id","status","action"}`）两个码。
- `convert` 是两阶段：默认只编译并返回，不改状态、不写资源；`register=true` 才一次性注册整条链。这是 v2「资源写入侧没有 HTTP 端点」的唯一例外，记 13 §8。
- 批量注册只扩展 v2 `ResourceService.register_custom_task` 的可选 `tasks` 映射；旧 `content` 调用签名与返回形状不变；工具层 `tasks` 上限 32 个节点，REST `convert` 不受该上限约束。
- 录制占用设备只用 `DeviceLeaseService` 的进程内闸门（`set_recording` / `recording` / `ClaimGate.allow_manual`），不写 `device_lease` 行、不改表结构、不造合成 `run_id`。
- 只新增一个配置键 `demonstration.enabled`（bool，默认 `true`，分组「Agent」，`hot_action="none"`）；步数上限 100、`CLICK_SETTLE_SECONDS`、列表每页 50 都写成常量。
- 只录 `click` / `swipe` / `long_press`；不录 `input_text` / `key_event` / `back_to_home`；截图 trigger 复用 `ScreenshotTrigger.MANUAL`，不新增枚举取值。
- `general.md` 只加两个工具与一段正文，`version` 由 7 改 8；不改 `copilot.md`、不新增 Worker、不改 M87 copilot 循环。
- 一律 `.venv/bin/python`；本机没有 `rg`，搜索用 `grep -rn`；`pytest.ini` 已有 `-q`，命令行用 `-o addopts= -q`；长输出重定向 `/tmp/m89-*.log`，只看退出码与尾部。
- 提交信息 `<类型>(m89): <中文摘要>`；不提交 `config.yaml`、`resource/`、`.venv/`、`web/node_modules/`；不写真实密钥与个人信息。

## 开工基线

- `dev` = M88 合并后的提交，Task 0 用 `git rev-parse --short dev HEAD` 实测并记台账；tag `v3-m88` 已存在。
- 迁移 head `0041_nav_graph.py`，新迁移 `0042_demonstration`。
- spec §0 的预期计数：工具 `108 107`、端点 `186`、错误码 `171 170 22`、业务表 `57`、配置 `143 132`、迁移 head `0041_nav_graph`、评测场景 `21`。这些是 M88 后的基线，Task 0 逐项实测。
- `tests/evals` 的用例数、`tests/evals/test_regression_replay.py` 的场景数断言（M88 后应为 `21`，两处）与 `agent_eval` 各 suite 通过数在 Task 0 实测并记台账；后续只按「不低于基线」比较，场景数按基线 + 2 改。
- 全量 pytest 的既有失败在 Task 0 如实记录（起点已有的失败逐条记进台账）；M89 不新增失败。
- 工作区必须干净；`docs/v3/05`、`docs/v3/12` 的 P4 预置修改已在 M88 合并提交里。

## §0 前置检查与接缝裁决

| # | 检查 | 命令 | 期望 |
|---|---|---|---|
| 1 | 分支与工作区 | `git status -sb` | 第一行 `## activity-adapt-teach`，无其它行 |
| 2 | M88 已合并 | `git tag -l 'v3-m88'` | `v3-m88` |
| 3 | 截图归档函数 | `grep -n "async def archive_screenshot" maa_api/services/screenshot_archive.py` | 1 行，签名含 `trigger`、`backend` |
| 4 | 迁移 head | `ls maa_api/db/migrations/versions/ \| grep -E '^[0-9]{4}_' \| sort \| tail -n 1` | `0041_nav_graph.py` |
| 5 | 工具计数 | `.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"` | `108 107` |
| 6 | 端点、错误码、节数 | `.venv/bin/python -c "from maa_api.main import create_app; a=create_app(); print(sum(len([m for m in v if m in ('get','post','put','delete','patch')]) for v in a.openapi()['paths'].values()))"`；`grep -n "ALL_CODE_COUNT\|HTTP_BOUND_CODE_COUNT\|SECTION_COUNT" tests/domain/test_errors.py` | 端点 `186`；`171 170 22` |
| 7 | 业务表与配置键 | `.venv/bin/python -c "from tests.db.test_models import BUSINESS_TABLES; print(len(BUSINESS_TABLES))"`；`.venv/bin/python -c "from maa_api.settings import SETTING_KEYS; from maa_api.services.settings_schema import _FIELDS; print(len(SETTING_KEYS), len(_FIELDS))"` | `57`；`143 132` |
| 8 | v2 自定义任务链路 | `grep -n "async def register_custom_task\|def _validate_custom_task\|def _write_enabled_custom_tasks" maa_api/services/resource_service.py` | 3 行，约 502、794、648 |
| 9 | 设备原子端点 | `grep -n "async def click_device\|async def swipe_device\|async def long_press_device" maa_api/api/routers/atomic.py maa_api/api/routers/device.py` | 3 行，约 89、371、406 |
| 10 | 租约、闸门与抢占 | `grep -n "class ClaimGate\|async def acquire\|async def preempt\|async def claim_gate" maa_api/agent/lease.py`；`grep -n "async def preempt_device" maa_api/api/routers/device_lease.py` | 5 行；`ClaimGate` 只有 `lease_id`、`allowed_session_ids` |
| 11 | 示教代码与前端路由尚不存在 | `grep -rn "demonstration" maa_api web/src --include=*.py --include=*.ts --include=*.tsx`；`grep -n "more/demonstrations" web/src/routes/router.tsx web/src/components/layout/nav.ts` | 都无输出 |
| 12 | 迁移与枚举测试基线 | `.venv/bin/python -m pytest -o addopts= -q tests/domain/test_enums.py tests/db/test_models.py tests/db/test_migrate.py > /tmp/m89-base.log 2>&1; echo $?; tail -n 3 /tmp/m89-base.log` | 退出码 `0` |

第 3 条不成立时按 spec §10 停下来问，不自己造归档函数。第 5–7 条与实测不一致时先核对 M86–M88 的 spec 登记，把差值记进台账，后续断言一律用「实测基线 + 本条目标的增量」；本计划正文写的是 spec 的预期值。

接缝裁决（实现时按这张表，不再另找入口）：

| 接缝 | 做法 | 不做什么 |
|---|---|---|
| 错误码文档落点 | `DEMONSTRATION_*` 两条在 T1 同时写进 `docs/v3/14` §4.23，否则 `test_errors.py` 的 `DOC_ROWS` 门禁在 T1 就红；§6.15 的四行与总数句在 T6 | 不留到 T6 才写 §4.23 |
| 配置键五处同步 | 全部落在 T6（`settings.py`、`settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、影响四端点 503），T8 只校验文档 | 不在 T8 才补 `settings.py` |
| 采集落点 | 三个设备端点成功路径挂 `capture.begin/finish`；点击的 `begin` 在 `preempt_device` 之前，`finish` 在 `operation_lock` 块之后 | 不改 `_run_atomic` 实现、不改端点返回形状 |
| 闸门复用 | `DeviceLeaseService.set_recording/recording`；`claim_gate()` 录制期间返回 `allow_manual=False` 的空集；`start` 先开闸门再 `preempt("rest_atomic")` | 不写 `device_lease` 行、不扩列、不新增抢占 reason |
| 步骤 JSON | `steps` 是扁平对象数组，`screen` 存 `[width, height]` 设备像素；`delay_ms` 夹 0–10000；每步必带 `at`（`steps.at`，结尾 `Z` 的 UTC ISO 时间：naive UTC 补 `UTC` 后 `isoformat().replace("+00:00", "Z")`，测试只钉「结尾 Z 且可解析」） | 不拆 action 联合类型、不加版本字段 |
| 编译节点 | `JustReturn` + `ClickRect`/`Swipe`/`Stop`；`ClickRect` 10×10，`Swipe` 100×100，`specialParams` 四元组 | 不引用模板图、不生成 `ClickSelf` |
| 唯一新配置键 | `demonstration.enabled`；页面与四端点同一开关 | 不加 `demonstration.max_steps` 等键 |
| 冒烟假设备 | `install_fake_lifespan(app)` 之后、`TestClient.__enter__()` 之前把 `app.state.device_manager_factory` 换成 `SmokeDemoDevice`；它继承 `tests/fakes/lifespan.py` 的 `SmokeDeviceManager`，只覆写 `snapshot()` 并新增 `screenshot`/`swipe`/`long_press` | 不新写平级假设备，不丢 `connect_with_retry`/`on_core_connection_event`/`close` 这些 lifespan 要调的事件接口 |
| 前端路由锚点 | `router.tsx` 与 `nav.ts` 都紧跟 `more/watchdog` 插入；导航组 `agent` | 不照 m86 片段漏 `group`、不改技能/记忆分组 |
| 注册冲突预检 | 用 `ResourceAssetRepository.get_by_kind_name(CUSTOM_TASK, name)` 逐个比对；写入竞态由 UNIQUE 约束转 409 | 不新增批量端点、不改旧调用 |

## Review Focus

1. **录制闸门与手动提交互斥**：录制中 `acquire` 必须 409 `DEVICE_LEASE_BUSY`，`claim_gate()` 必须 `allow_manual=False` 且允许集为空，手动提交流水线留在 `pending`；停止后手动流水线按队列顺序恢复。Task 2 的 `test_claim_gate_blocks_manual_while_recording`、`test_recording_gate_does_not_claim_manual`、`test_recording_gate_releases_manual_after_stop` 钉住；Task 3 的 `test_device_lost_discards_recording` 钉住断开释放。
2. **convert 两阶段与资源写入回滚**：`register=false` 不改状态、不写资产；`register=true` 只在单事务写全部节点、只重载一次；重载失败必须删行、删文件、恢复 `tasks.json` 快照并回到 `recorded`。Task 4 的 `test_register_bundle_rolls_back_on_reload_failure`、Task 6 的 `test_convert_preview_does_not_write` 与 `test_convert_failure_paths` 钉住。
3. **编译坐标 720 基准**：设备像素按 `round(px * 720 / height)` 换算并夹到 1280×720；`ClickRect` 的 10×10 落点夹 0–1270/0–710，`Swipe` 起终点夹 0–1180/0–620；1080 高与 720 高都不越界。Task 4 的 `test_scale_and_chain_tail`、`test_compile_node_shapes` 与 Task 7 的 `maps_tap_to_device_pixels` 钉住。
4. **截图归档失败容忍**：单张截图失败或抛错时对应 id 写 `null`，步骤仍追加、设备动作仍按原结果返回；设备断开才丢弃录制。Task 3 的 `test_swipe_long_press_and_null_screenshots` 与 `test_failed_action_appends_no_step` 钉住。
5. **前端画布坐标映射**：用 `GET /api/device/status` 的 `resolution` 与画布 `rect` 映射到设备像素并夹到 `[0, width-1]`/`[0, height-1]`，不按 `naturalWidth`；滑动与长按不重复发点击。Task 7 的 `maps_tap_to_device_pixels`、`classifies_swipe_and_long_press`、`starts_and_stops_recording` 钉住。

---

### Task 0: 分支、§0、基线与计划入库

**Files:**
- Create: `docs/v3/plans/m89-activity-adapt-teach.md`（开工时从 `debug/plans/` 移入）
- 不改代码

**Interfaces:**
- Produces: 开工基点、§0 实测结果、全量与评测基线、本计划的提交。
- Consumes: `dev` 的 M88 合并提交、tag `v3-m88`。

- [ ] **Step 1: 从 dev 新建分支并确认干净**

```bash
git checkout dev
git checkout -b activity-adapt-teach
git status -sb
git rev-parse --short dev HEAD
git tag -l 'v3-m88'
```

Expected：第一行 `## activity-adapt-teach`，没有其他行；`dev` 与 `HEAD` 同一个短哈希；`v3-m88` 存在。不建 worktree。

- [ ] **Step 2: 跑 §0 的 12 条检查**

按上面的表逐条执行，把每条的实际输出记进台账草稿。任一条与期望不符：第 3 条直接停下报告；第 5–7 条记录差值后继续；其它条停下报告。

- [ ] **Step 3: 记录基线与既有失败**

```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/m89-base-full.log 2>&1; echo $?; tail -n 30 /tmp/m89-base-full.log
.venv/bin/python -m pytest -o addopts= -q tests/evals > /tmp/m89-base-evals.log 2>&1; echo $?; tail -n 3 /tmp/m89-base-evals.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m89-base-reg.log 2>&1; echo $?; tail -n 5 /tmp/m89-base-reg.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m89-base-cap.log 2>&1; echo $?; tail -n 5 /tmp/m89-base-cap.log
```

Expected：记录退出码、passed/failed 计数、既有失败清单、两个 suite 的通过数；capability 的通过率就是 T8 的下限。基线失败若多于 spec §0 记载，停下来报告。

- [ ] **Step 4: 提交计划**

```bash
git add docs/v3/plans/m89-activity-adapt-teach.md
git commit -m "docs(m89): 示教录制与新活动自适应的实施计划"
```

---

### Task 1: 枚举、错误码、表与迁移（spec T1）

**Files:**
- Modify: `maa_api/domain/enums.py`、`maa_api/domain/errors.py`、`maa_api/db/models.py`
- Create: `maa_api/db/migrations/versions/0042_demonstration.py`
- Modify: `tests/domain/test_enums.py`、`tests/domain/test_errors.py`、`tests/db/test_models.py`、`docs/v3/14-现行接口清单.md`（只加 §4.23 两行）
- Test: `tests/db/test_demonstration_migration.py`（新建）

**Interfaces:**
- Produces: `DemonstrationStatus`（`RECORDING="recording"`、`RECORDED="recorded"`、`CONVERTED="converted"`、`DISCARDED="discarded"`）；`ErrorCode.DEMONSTRATION_NOT_FOUND`（404）、`ErrorCode.DEMONSTRATION_STATE_INVALID`（409）；`Demonstration` 模型与表 `demonstration`；迁移 revision `0042_demonstration`。
- Consumes: 既有 `new_uuid`、`utcnow`、`json_column`、`datetime_column`；`ERROR_HTTP_STATUS` 的追加方式。

- [ ] **Step 1: 写失败的计数与迁移测试**

```python
# tests/domain/test_enums.py：EXPECTED_VALUES 增加
"DemonstrationStatus": {
    "RECORDING": "recording", "RECORDED": "recorded",
    "CONVERTED": "converted", "DISCARDED": "discarded",
}
# tests/domain/test_errors.py
ALL_CODE_COUNT = 173
HTTP_BOUND_CODE_COUNT = 172
SECTION_COUNT = 23
# tests/db/test_models.py
BUSINESS_TABLES 增加 "demonstration"（测试名与 docstring 的 57 改 58）
UUID_PK_TABLES 增加 "demonstration"
EXPECTED_JSON_COLUMNS 增加 ("demonstration", "steps")、("demonstration", "converted_to")
```

`tests/db/test_demonstration_migration.py` 照 `tests/db/test_channels_migration.py` 写三个用例：

- `test_upgrade_creates_demonstration_table`：`EXPECTED_COLUMNS` 为 `id VARCHAR(36) NOT NULL`、`title VARCHAR(64) NOT NULL`、`status VARCHAR(16) NOT NULL`、`steps JSON NOT NULL`、`converted_to JSON NULL`、`created_at DATETIME NOT NULL`、`updated_at DATETIME NOT NULL`；主键名 `pk_demonstration`；`PRAGMA index_list` 无业务索引。
- `test_defaults_and_downgrade`：插入一行后 `steps == []`、`converted_to is None`；`downgrade(0041_nav_graph)` 后表不存在，再 `upgrade()` 是空表。
- `test_offline_sql_renders_both_directions`：离线 `--sql` 输出含 `CREATE TABLE demonstration` 与 `DROP TABLE demonstration`。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/domain/test_enums.py tests/domain/test_errors.py tests/db/test_models.py tests/db/test_demonstration_migration.py > /tmp/m89-t1-red.log 2>&1; echo $?; tail -n 8 /tmp/m89-t1-red.log
```

Expected：非 0；`AttributeError: DemonstrationStatus`、计数不等于 173/172/23、表集合少 `demonstration`、迁移文件不存在。

- [ ] **Step 3: 实现枚举、错误码、模型与迁移**

`Demonstration` 照 spec §4.1 逐列写，`__tablename__="demonstration"`，`steps` 用 `json_column("steps", nullable=False)`，`converted_to` 用 `json_column("converted_to")`，两个时间列用 `datetime_column`；`steps` 的元素形状照 spec §4.1，必须含 `at`（结尾 `Z` 的 UTC 时间，本任务只建列不校验元素）。错误码各带一行中文注释，`ERROR_HTTP_STATUS` 绑 404 与 409。迁移文件照 `0038_channels.py` 的结构，`revision="0042_demonstration"`、`down_revision="0041_nav_graph"`；`upgrade()` 建表并显式命名 `pk_demonstration`，`downgrade()` 删表。`docs/v3/14` 在 §4 末尾新建 `### 4.23 示教与自定义任务`，只写两行错误码表。

- [ ] **Step 4: 跑测试与离线 SQL**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/domain/test_enums.py tests/domain/test_errors.py tests/db/test_models.py tests/db/test_demonstration_migration.py > /tmp/m89-t1.log 2>&1; echo $?; tail -n 5 /tmp/m89-t1.log
.venv/bin/python -m alembic upgrade head --sql 2>/dev/null | grep -c "demonstration"
```

Expected：退出码 `0`；`grep -c` 输出 ≥ 2（建表与主键）。

- [ ] **Step 5: 提交**

```bash
git add maa_api/domain/enums.py maa_api/domain/errors.py maa_api/db/models.py maa_api/db/migrations/versions/0042_demonstration.py tests/domain/test_enums.py tests/domain/test_errors.py tests/db/test_models.py tests/db/test_demonstration_migration.py docs/v3/14-现行接口清单.md
git commit -m "feat(m89): 示教表、枚举、错误码与迁移"
```

---

### Task 2: 服务状态机与录制闸门（spec T2）

**Files:**
- Create: `maa_api/agent/demonstrations/__init__.py`、`maa_api/agent/demonstrations/service.py`
- Modify: `maa_api/agent/lease.py`、`maa_api/db/repositories/pipeline.py`、`maa_api/services/pipeline_runner.py`
- Test: `tests/agent/demonstrations/__init__.py`、`tests/agent/demonstrations/test_service.py`（新建）；`tests/agent/lease/test_device_lease_service.py`、`tests/services/test_pipeline_runner.py`

**Interfaces:**
- Produces: `DemonstrationService(session_factory, *, device_manager, lease, resource_service)`；`recover() -> int`、`is_recording() -> bool`、`active_id`（只读属性，当前 `recording` 的 id 或 `None`）、`start(*, title) -> dict`、`stop(id, *, discard=False) -> dict`、`list(*, status, page, size) -> dict`、`note_step(...) -> dict | None`（追加的步骤含 `at`，见 Step 3）、`abort(reason) -> None`；常量 `MAX_DEMONSTRATION_STEPS = 100`；`DeviceLeaseService.set_recording(active)` / `recording()`；`ClaimGate.allow_manual: bool = True`；`claim_gate()` 录制期间返回 `ClaimGate(lease_id="demonstration", allowed_session_ids=frozenset(), allow_manual=False)`；`PipelineRepository._next_pending_id/claim_next(..., allow_manual=True)`；`PipelineRunner._claim_next` 透传 `gate.allow_manual`。`convert(id, *, register)` 不属 T2：它依赖 T4 的编译器与批量注册，由 Task 6 在 `service.py` 实现并补服务层用例。
- Consumes: `Demonstration`、`DemonstrationStatus`、`PipelineRepository.current()`、`DeviceLeaseService.preempt("rest_atomic", detail=...)`、`AppError`。

- [ ] **Step 1: 写状态机、闸门与领取测试**

```python
# tests/agent/demonstrations/test_service.py
async def test_start_conflicts_with_existing_recording(): ...   # 409 DEMONSTRATION_STATE_INVALID，details={'id','status','action'}，原行不变
async def test_start_rejects_running_pipeline(): ...            # PIPELINE_ALREADY_RUNNING，lease.recording() is False
async def test_concurrent_start_only_one_row(): ...             # asyncio.gather 两个 start，恰好一个成功
async def test_stop_transitions(): ...                          # 有步骤 recorded；零步与 discard=True discarded
async def test_stop_on_recorded_is_state_invalid(): ...         # 409，details 含 id/status/action
async def test_stop_unknown_id_is_not_found(): ...              # 404 DEMONSTRATION_NOT_FOUND，details={'id': id}
async def test_recover_and_abort_discard(): ...                 # recover 只改 recording；abort 置 discarded 且释放闸门
async def test_note_step_sets_at_utc_z(): ...                   # steps[-1]["at"] 结尾 Z、可被 fromisoformat 解析；delay_ms 仍按 started_at 差
async def test_note_step_stops_at_100(): ...                    # 第 100 步后 recorded、闸门 False
```

`tests/agent/lease/test_device_lease_service.py` 增加 `test_acquire_busy_while_recording`（409 `DEVICE_LEASE_BUSY`）、`test_claim_gate_blocks_manual_while_recording`（`allow_manual is False`、`allowed_session_ids == frozenset()`）、`test_recording_gate_releases_manual_after_stop`（`set_recording(False)` 后 `claim_gate()` 回到租约闸门）。`tests/services/test_pipeline_runner.py` 增加 `test_recording_gate_does_not_claim_manual`（手动行保持 `pending`）与 `test_manual_claim_resumes_after_recording_stops`（解除后同一行变 `running`）。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/demonstrations tests/agent/lease/test_device_lease_service.py tests/services/test_pipeline_runner.py > /tmp/m89-t2-red.log 2>&1; echo $?; tail -n 10 /tmp/m89-t2-red.log
```

Expected：非 0；`ModuleNotFoundError: maa_api.agent.demonstrations`、`TypeError: ClaimGate 没有 allow_manual`、`claim_gate` 未按录制返回。

- [ ] **Step 3: 实现服务与闸门**

`DemonstrationService` 用两个锁：`_start_lock` 包住开始流程，`_append_lock` 串行 `note_step`；内存活动标记 `_active_id`。`start` 顺序固定为：锁内查无其它 `recording` 行 → `device_manager.snapshot()` 的 `state == "connected"` 且 `resolution` 两个分量 > 0，否则 503 `DEVICE_NOT_CONNECTED` → `lease.set_recording(True)` → `PipelineRepository.current()` 非空则回滚闸门并抛 409 `PIPELINE_ALREADY_RUNNING` → 插入 `recording` 行（失败回滚闸门）→ `await lease.preempt("rest_atomic", detail="用户开始示教录制")`（异常只记日志）→ 返回 wire。`recover()` 把所有 `recording` 行改 `discarded`（同步 `updated_at`）、清 `_active_id`、`lease.set_recording(False)`。`abort(reason)` 同样丢弃活动行并清闸门。`note_step` 在锁内重读行、校验 id 与状态、`seq=len(steps)+1`、`delay_ms` 取与上一步 `started_at` 的毫秒差（夹 0–10000，第一步 0），`at` 取本步 `started_at`（naive UTC）按 UTC 补时区后 `isoformat().replace("+00:00", "Z")`，追加后提交；第 100 步提交后置 `recorded`、清 `_active_id` 并清闸门。找不到 id 抛 404 `DEMONSTRATION_NOT_FOUND`（details `{"id": id}`）；状态不允许时抛 409 `DEMONSTRATION_STATE_INVALID`（details `{"id", "status", "action"}`）。

`DeviceLeaseService` 加 `_recording=False`、`set_recording`（同时 `notify_all` 唤醒排队者）、`recording`；`acquire` 在抢占记号检查之后、入队之前检查 `_recording` 并抛 409 `DEVICE_LEASE_BUSY`（消息「用户正在网页上示教，设备暂不可用」），`_try_insert` 成功后再检查一次，已开录则删刚插入的行并抛同一码。`ClaimGate` 增加末位字段 `allow_manual: bool = True`；`claim_gate()` 先判 `_recording`。仓储与 runner 按 Interfaces 加参数；`allow_manual=False` 时候选条件只有 `agent_session_id IN (...)`。`set_recording(False)` 由 `stop`、`abort`、`recover` 调用。

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/demonstrations tests/agent/lease/test_device_lease_service.py tests/services/test_pipeline_runner.py > /tmp/m89-t2.log 2>&1; echo $?; tail -n 6 /tmp/m89-t2.log
```

Expected：`0`；状态机表每一行都有对应用例；闸门解除后手动领取恢复。

- [ ] **Step 5: 提交**

```bash
git add maa_api/agent/demonstrations maa_api/agent/lease.py maa_api/db/repositories/pipeline.py maa_api/services/pipeline_runner.py tests/agent/demonstrations tests/agent/lease/test_device_lease_service.py tests/services/test_pipeline_runner.py
git commit -m "feat(m89): 示教状态机与录制闸门"
```

---

### Task 3: 采集接线（spec T3）

**Files:**
- Create: `maa_api/agent/demonstrations/capture.py`
- Modify: `maa_api/api/routers/atomic.py`（`click_device`）、`maa_api/api/routers/device.py`（`swipe_device`、`long_press_device`）
- Test: `tests/api/test_demonstration_capture.py`（新建）

**Interfaces:**
- Produces: `PendingStep(demonstration_id, screen, backend, started_at, before)`；`begin(request, manager) -> PendingStep | None`；`finish(request, pending, manager, *, action, points, duration_ms) -> None`；`cancel(pending) -> None`。
- Consumes: `app.state.demonstration_service`、`DemonstrationService.is_recording/active_id/abort/note_step`、`DeviceManager.snapshot/screenshot`、`CLICK_SETTLE_SECONDS`（`maa_api/agent/tools/raw.py`）、M86 `archive_screenshot`。

- [ ] **Step 1: 写采集测试**

```python
# tests/api/test_demonstration_capture.py
async def test_click_appends_step_with_two_screenshots(): ...  # 一条步骤、两个非空 screenshot_id、动作坐标不变
async def test_no_capture_without_recording(): ...             # manager.screenshot 未调用，响应与现状一致
async def test_failed_action_appends_no_step(): ...            # manager.click 抛 DEVICE_NOT_CONNECTED，note_step 未调用
async def test_swipe_long_press_and_null_screenshots(): ...    # 坐标/时长正确；archive_screenshot 抛错时 id 为 None，步骤仍在
async def test_unknown_resolution_skips_step(): ...            # snapshot 无 resolution，note_step 未调用，录制仍 recording
async def test_device_lost_discards_recording(): ...           # state != connected，abort("device_lost")，行变 discarded
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/api/test_demonstration_capture.py > /tmp/m89-t3-red.log 2>&1; echo $?; tail -n 8 /tmp/m89-t3-red.log
```

Expected：非 0；`ModuleNotFoundError: maa_api.agent.demonstrations.capture`，或端点响应里没有新增步骤。

- [ ] **Step 3: 实现 `capture.py` 并接线**

`begin` 取 `request.app.state` 上的服务；服务缺失、`settings.demonstration.enabled` 为假或 `is_recording()` 为假时返回 `None`，不截图、不加锁。设备状态不是 `connected` 时调 `await service.abort("device_lost")` 并返回 `None`。分辨率从 `manager.snapshot()["resolution"]` 取，缺失时记 warning、返回 `None`（丢弃该步但录制继续）。操作前截图在内存（`manager.screenshot()`），异常时 `before=None`；`PendingStep.backend` 取本次截图结果的 `backend`，截图失败时用 `"adb"`；`PendingStep.demonstration_id` 取 `service.active_id`，为空时返回 `None`。`finish` 以 `pending.backend` 作为 `note_step` 的 `backend`，两张截图共用同一个值。

`finish` 在 `pending is None` 时直接返回；先 `await asyncio.sleep(CLICK_SETTLE_SECONDS)`，再截操作后的图（异常时 `after=None`），最后 `await service.note_step(..., started_at=pending.started_at)`（步骤的 `at` 由这个时间生成）。`note_step` 内部逐张调 `archive_screenshot(session, image, trigger=ScreenshotTrigger.MANUAL, backend=ScreenshotBackend(backend))`，按返回的 `(row, meta)` 取 `meta["screenshot_id"]`；单张失败该 id 写 `None`。`cancel` 只记 debug 日志。

三个端点的接线固定为：

```python
pending = await begin(request, manager)
try:
    result = await _run_atomic(...)   # click 用原有的 preempt + operation_lock 块
except BaseException:
    await cancel(pending)
    raise
await finish(request, pending, manager, action=..., points=..., duration_ms=...)
return result
```

`click_device` 的 `begin` 放在 `preempt_device` 之前，`finish` 放在 `operation_lock` 块之后；`swipe_device`、`long_press_device` 包住现有 `_run_atomic` 调用。`_run_atomic` 与既有错误返回逐字不动。

- [ ] **Step 4: 跑测试与设备端点回归**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/api/test_demonstration_capture.py tests/api/test_device_router.py > /tmp/m89-t3.log 2>&1; echo $?; tail -n 6 /tmp/m89-t3.log
```

Expected：`0`；未录制时三个端点与现状逐字一致；假设备点三次得到三条步骤与六条截图记录。

- [ ] **Step 5: 提交**

```bash
git add maa_api/agent/demonstrations/capture.py maa_api/api/routers/atomic.py maa_api/api/routers/device.py tests/api/test_demonstration_capture.py
git commit -m "feat(m89): 设备端点采集示教步骤"
```

---

### Task 4: 编译器、校验器与批量注册（spec T4）

**Files:**
- Create: `maa_api/agent/demonstrations/compiler.py`、`maa_api/agent/custom_task/__init__.py`、`maa_api/agent/custom_task/rules.py`
- Modify: `maa_api/services/resource_service.py`
- Test: `tests/agent/demonstrations/test_compiler.py`、`tests/services/test_custom_task_rules.py`（新建）；`tests/services/test_agent_resource_service.py`

**Interfaces:**
- Produces: `Step`、`Compiled(entry, tasks, chain)`、`CompileError(errors)`、`parse_step`、`compile_steps(*, prefix, steps)`、`scale_point(value, height, maximum)`；`TaskIssue(task, field, reason)`、`NAME_PATTERN`、`ALLOWED_ACTIONS=frozenset({"ClickSelf","ClickRect","DoNothing","Swipe","Stop"})`、`MAX_BUNDLE_TASKS=128`、`check_bundle(tasks, entry) -> (errors, chain_errors, chain)`、`check_entry_name`；`ResourceService.validate_custom_task_content(content) -> list[dict]`；`ResourceService.register_custom_task(*, name, description, content=None, tasks=None)`。
- Consumes: `Demonstration`、`parse_step` 的 steps、`ResourceAssetKind.CUSTOM_TASK`、`ResourceAssetRepository.get_by_kind_name`、`_snapshot_custom_file` / `_write_enabled_custom_tasks` / `_reload`。

- [ ] **Step 1: 写编译、规则与批量注册测试**

```python
# tests/agent/demonstrations/test_compiler.py
def test_compile_node_shapes(): ...      # ClickRect [x,y,10,10]；Swipe 有 rectMove 与 specialParams；长按起终点相同
def test_scale_and_chain_tail(): ...     # 1080 高：y=540 → 360；x=1920 → 夹 1279；最后动作指向 _Done；_Done 是 Stop
def test_compile_rejects_zero_height(): ...  # CompileError，errors[0]["field"] 标出 height/screen
# tests/services/test_custom_task_rules.py
def test_chain_errors(): ...             # 未知 next、环、缺 Stop 都进 chain_errors
def test_action_and_bounds(): ...        # InputText 被拒；1281 与 721 被拒；validate_custom_task_content 返回 field=content 不抛
# tests/services/test_agent_resource_service.py
async def test_register_bundle_writes_all_nodes_once(): ...   # tasks.json 有全部节点，_reload 一次
async def test_register_bundle_rolls_back_on_reload_failure(): ...  # 行、文件、tasks.json 全回到调用前
async def test_register_bundle_rejects_conflict_before_write(): ...  # 409 RESOURCE_ASSET_CONFLICT，无新行
async def test_register_single_call_shape_unchanged(): ...    # 只给 content 时返回单个资产
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/demonstrations/test_compiler.py tests/services/test_custom_task_rules.py tests/services/test_agent_resource_service.py > /tmp/m89-t4-red.log 2>&1; echo $?; tail -n 8 /tmp/m89-t4-red.log
```

Expected：非 0；编译器与 rules 模块不存在，`register_custom_task` 不接受 `tasks`。

- [ ] **Step 3: 实现编译器与节点模板**

节点形状照 spec §4.5 写死：

```python
{"algorithm": "JustReturn", "action": "ClickRect", "specificRect": [x, y, 10, 10], "postDelay": 800, "next": ["<next>"]}
{"algorithm": "JustReturn", "action": "Swipe", "specificRect": [x1, y1, 100, 100], "rectMove": [x2, y2, 100, 100], "specialParams": [duration, 1, 37, 1], "postDelay": 800, "next": ["<next>"]}
{"algorithm": "JustReturn", "action": "Stop"}   # 名字 <prefix>_Done
```

`compile_steps` 先校验 `screen` 两分量 > 0、各点在 `[0,width)`/`[0,height)`，违规抛 `CompileError`；`parse_step` 把扁平 dict 转 `Step`（缺省整数字段取 `None`、`screen` 转 `tuple[int,int]`）。`scale_point` 用 `round(value * 720 / height)` 再夹 `0..maximum`，x 传 1279、y 传 719；`ClickRect` 再把 x 夹 0–1270、y 夹 0–710，`Swipe` 起终点夹 0–1180/0–620；`duration_ms` 滑动夹 100–5000、长按夹 600–5000；`postDelay` 取下一步 `delay_ms`，最后一步 800；每个动作节点 `next` 指向下一个，最后指向 `_Done`。名字生成按 spec 第 8 条：`prefix` 去掉 `Custom_` 后把非 `[A-Za-z0-9_.-]` 换成 `_`、截 40、空则 `Demo`，节点名 `Custom_<slug>_<序号>`，总长 ≤ 62。`Compiled` 只有 `entry`/`tasks`/`chain` 三个字段；「长按编译成起终点相同的滑动」这条 `warnings` 由 T5 的 `draft_custom_task` 生成。

- [ ] **Step 4: 实现 `rules.py`、公开校验与批量注册**

`check_bundle` 按 spec §4.5 的 1–5 条实现：节点数 1–128、`entry` 存在、每个键过 `NAME_PATTERN`；`action` 属于 `ALLOWED_ACTIONS`；`next`/`onErrorNext`/`exceededNext` 的值都在 `tasks` 里，`#self` 视为环；从 `entry` DFS，同路径重复报环，遍历不到 `Stop` 报缺出口，`chain` 记第一次到 `Stop` 的路径；`ClickRect` 缺 `specificRect`、`Swipe` 缺 `specificRect`/`rectMove`、矩形越出 1280×720 或宽高 ≤ 0 都报错；结构错误进 `errors`，链级错误进 `chain_errors`。`validate_custom_task_content` 是 `_validate_custom_task` 的公开包装，捕获 `AppError` 返回 `[{"field":"content","reason":exc.message}]`。

`register_custom_task` 判 `content`/`tasks` 恰好给一个，否则 422；`tasks` 路径在锁内：`name in tasks`、节点数 1–128、每个名字过 `_custom_task_name`、每个节点过 `_validate_custom_task`、`check_bundle` 无错；逐个 `get_by_kind_name` 预检，冲突抛 409 且不写任何行；然后在一个会话里创建全部 `resource_asset` 行并提交，按需写每个节点的大文件，调用一次 `_write_enabled_custom_tasks()` 与一次 `_reload()`；任一步失败则删全部新行、删新文件、恢复 `tasks.json` 快照、再重载一次，抛 `RESOURCE_LOAD_FAILED`。返回 `{"entry": name, "task_names": [...], "items": [...]}`；`content` 路径原样走 `_register_custom_task`。

- [ ] **Step 5: 跑测试**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/demonstrations/test_compiler.py tests/services/test_custom_task_rules.py tests/services/test_agent_resource_service.py > /tmp/m89-t4.log 2>&1; echo $?; tail -n 6 /tmp/m89-t4.log
```

Expected：`0`；编译结果能被 v2 的 `_validate_custom_task` 逐节点接受；批量注册只重载一次；失败后无部分资产。

- [ ] **Step 6: 提交**

```bash
git add maa_api/agent/demonstrations/compiler.py maa_api/agent/custom_task maa_api/services/resource_service.py tests/agent/demonstrations/test_compiler.py tests/services/test_custom_task_rules.py tests/services/test_agent_resource_service.py
git commit -m "feat(m89): 示教编译、链级校验与批量注册"
```

---

### Task 5: 工具、代理定义与目录计数（spec T5）

**Files:**
- Create: `maa_api/agent/tools/custom_task.py`
- Modify: `maa_api/agent/tools/__init__.py`、`maa_api/agent/tools/resource.py`、`maa_api/agent/specs/builtin/general.md`
- Test: `tests/agent/test_custom_task_tools.py`（新建）；`tests/agent/test_tool_catalog.py`、`tests/agent/test_resource_ops_schedule_tools.py`、`tests/api/test_agent_specs_router.py`、`tests/agent/specs/test_agent_specs.py`、`tests/agent/specs/test_m70_specs.py`

**Interfaces:**
- Produces: `DraftStep`、`DraftCustomTaskParams(stage_code, steps, name)`、`ValidateCustomTaskParams(entry, tasks)`、`draft_custom_task`、`validate_custom_task`、`register_tools(registry)`；两个工具组 `custom_task`、风险 `SAFE`、`read_only=True`、`refetchable=True`、`rest_visible=True`；`RegisterCustomTaskParams` 改为 `content`/`tasks` 二选一。
- Consumes: `compile_steps`、`TaskIssue`/`check_bundle`、`ResourceService.validate_custom_task_content`、`ResourceService.register_custom_task(tasks=...)`、`_service(context)`。

- [ ] **Step 1: 写工具与目录测试**

```python
# tests/agent/test_custom_task_tools.py
async def test_draft_returns_bundle_without_writing(): ...  # entry/tasks/chain/warnings（长按步骤的 warnings 含「长按」）；无资源资产
async def test_validate_and_register(): ...                 # valid=False 不抛且 errors/chain_errors 完整；注册一次 name=entry；content 与 tasks 同给时参数校验失败
# tests/agent/test_tool_catalog.py
def test_custom_task_tools_are_safe_and_rest_visible(): ...  # group/risk/rest_visible/read_only/refetchable
# tests/agent/specs/test_agent_specs.py
def test_legacy_chat_lists_every_v2_registered_tool_in_registration_order(): ...  # len(definitions)==110；M89_TOOLS 被排除出 legacy_chat
# tests/agent/specs/test_m70_specs.py
def test_general_tools_equal_legacy_chat_minus_delegate_recon_plus_worker_tools(): ...  # general 集合含两个新工具；本文件两处 general 期望集合都要改
```

`tests/agent/test_tool_catalog.py` 的 `EXPECTED_TOOLS` 加两个名字、总数断言的四处数字改 `110/109`，`EXPECTED_REFETCHABLE` 加两个名字并把 `len(...)` 从 52 改 54；`UNTRUSTED_OUTPUT_TOOLS` 保持 23。`tests/agent/test_resource_ops_schedule_tools.py` 增加 `content`/`tasks` 二选一的参数用例。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_custom_task_tools.py tests/agent/test_tool_catalog.py tests/agent/test_resource_ops_schedule_tools.py > /tmp/m89-t5-red.log 2>&1; echo $?; tail -n 8 /tmp/m89-t5-red.log
```

Expected：非 0；`custom_task.py` 不存在，目录总数仍是 108/107。

- [ ] **Step 3: 实现两个工具**

`DraftCustomTaskParams.steps` 为 1–31 步（加 `Done` 不超过 32 个节点），`DraftStep` 字段为 `action`（`click`/`swipe`/`long_press`）、`x`/`y`、`x1`/`y1`/`x2`/`y2`、`duration_ms`、`delay_ms`、`screen`（长度 2 的整数列表）；`stage_code` 必填，`name` 未给时用 `Custom_<清理后的 stage_code>`。`draft_custom_task` 只调 `compile_steps` 并返回 `{"entry","tasks","chain","warnings"}`，不写库、不写文件；长按步骤在 `warnings` 里写「长按编译成起终点相同的滑动」。`validate_custom_task` 先逐节点调 `validate_custom_task_content`，再调 `check_bundle`，返回 `{"valid","errors","chain_errors","chain"}`，校验失败不抛 `AppError`。`RegisterCustomTaskParams` 用 `model_validator` 保证 `content` 与 `tasks` 恰好一个，`tasks` 1–32 个且 `name` 是入口；handler 把 `tasks` 原样传给服务。两个工具在 `register_tools` 里注册，`maa_api/agent/tools/__init__.py` 的注册顺序放在 resource 工具之后。参数与返回字段以 `docs/v3/05` §6.1 的 M89 段为契约原文（`draft_custom_task` 的 `stage_code`/`steps`/`name` 与四个返回键、`validate_custom_task` 的 `entry`/`tasks` 与四个返回键、`register_custom_task` 的 `tasks` 1–32 且与 `content` 二选一），实现与文档逐字一致。

- [ ] **Step 4: 改 `general.md` 与目录计数**

`tools` 列表末尾（`remember` 之后）加 `draft_custom_task`、`validate_custom_task`；`version: 7` 改 `version: 8`；正文在「外部资料」段之后加 spec §4.7 的整段（逐字）。`tests/api/test_agent_specs_router.py` 的 `EXPECTED_VERSIONS["general"]` 改 8（版本锚点只在这个文件，`tests/agent/specs/test_agent_specs.py` 与 `test_m70_specs.py` 都没有版本常量）。`tests/agent/specs/test_agent_specs.py` 的 `len(definitions)` 改 110，新增 `M89_TOOLS = {"draft_custom_task", "validate_custom_task"}` 并并入「legacy_chat 不加载」排除集。`tests/agent/specs/test_m70_specs.py` 的 `general` 期望集合有两处（M88 后约 :106 与 :431，行号以开工实测为准），两处都要并入两个工具名，不能只改第一处。

- [ ] **Step 5: 跑测试与计数**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_tool_catalog.py tests/agent/test_custom_task_tools.py tests/agent/test_resource_ops_schedule_tools.py tests/api/test_agent_specs_router.py tests/agent/specs/test_agent_specs.py tests/agent/specs/test_m70_specs.py > /tmp/m89-t5.log 2>&1; echo $?; tail -n 6 /tmp/m89-t5.log
.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"
```

Expected：`0`；最后一行 `110 109`。

- [ ] **Step 6: 提交**

```bash
git add maa_api/agent/tools/custom_task.py maa_api/agent/tools/__init__.py maa_api/agent/tools/resource.py maa_api/agent/specs/builtin/general.md tests/agent/test_custom_task_tools.py tests/agent/test_tool_catalog.py tests/agent/test_resource_ops_schedule_tools.py tests/api/test_agent_specs_router.py tests/agent/specs/test_agent_specs.py tests/agent/specs/test_m70_specs.py
git commit -m "feat(m89): 自定义任务起草与校验工具"
```

---

### Task 6: REST、装配、配置键、OpenAPI 与清单（spec T6）

**Files:**
- Create: `maa_api/api/routers/demonstrations.py`
- Modify: `maa_api/main.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`docs/v3/14-现行接口清单.md`（§6.15）、`maa_api/agent/demonstrations/service.py`（`convert` 与 `_convert_lock`）
- Test: `tests/api/test_demonstrations.py`（新建）、`tests/agent/demonstrations/test_service.py`（追加 convert 服务层用例）；`tests/domain/test_route_inventory.py` 只跑门禁，不改代码
- 生成物：`web/openapi.json`、`web/src/types/api.d.ts`（`scripts/dump_openapi.py` 与 `pnpm gen:api`）

**Interfaces:**
- Produces: 四端点 `start_demonstration`（201）、`list_demonstrations`（200）、`stop_demonstration`（200）、`convert_demonstration`（200）；DTO `DemonstrationCreate/Stop/Convert`、`DemonstrationStepOut`（含 `seq`、`action`、坐标、`duration_ms`、`delay_ms`、`screen`、两个截图 id 与 `at: str`，ISO 8601 UTC 带 `Z`）、`DemonstrationOut`、`DemonstrationStartOut`、`DemonstrationPage`、`CompiledTaskOut`、`DemonstrationConvertOut`；`DemonstrationService.convert(id, *, register) -> dict` 与 `_convert_lock`；`app.state.demonstration_service`；`DemonstrationSettings(enabled=True)`。
- Consumes: `DemonstrationService`、`require_auth`、`error_responses`、`get_session`、`DemonstrationStatus`、`device_manager` 的 `resolution`。

- [ ] **Step 1: 写四端点测试**

```python
# tests/api/test_demonstrations.py
async def test_start_returns_recording_and_screen(): ...     # 201、status=recording、screen 两个分量
async def test_start_error_paths(): ...                       # 第二个 start 409 DEMONSTRATION_STATE_INVALID（details 含 id/status/action）；设备未连接 503 DEVICE_NOT_CONNECTED；开关关闭 503 SERVICE_UNAVAILABLE（error 无 details）
async def test_disabled_switch_returns_503_on_all_endpoints(): ...  # 四个端点都 503 SERVICE_UNAVAILABLE，error.details 键不存在
async def test_list_steps_and_pagination(): ...               # steps 完整且每步 at 以 Z 结尾、可解析；status 过滤；size=51 → 422 VALIDATION_ERROR
async def test_stop_error_paths(): ...                        # 404 details={"id": ...}；状态错误 409 details 含 id/status/action
async def test_convert_preview_does_not_write(): ...          # 状态仍 recorded，资产数不变，registered 为空，converted_to 为 None
async def test_convert_register_marks_converted(): ...        # converted、converted_to.entry 正确
async def test_convert_failure_paths(): ...                   # 校验 422 且 details 含 errors/chain_errors；名字预检 422；写入竞态 409；状态仍 recorded、converted_to 为 None、无部分资产
async def test_openapi_has_four_demonstration_operations(): ...  # paths 与 operationId 齐全
# tests/agent/demonstrations/test_service.py（服务层，追加到 T2 的文件）
async def test_service_convert_preview_keeps_recorded(): ...      # register=False：recorded、converted_to None、无资产
async def test_service_convert_register_marks_converted(): ...    # register=True：converted、converted_to 四键、asset_ids 与 task_names 等长
async def test_service_convert_register_failure_keeps_recorded(): ...  # 注册抛 RESOURCE_LOAD_FAILED：仍 recorded、converted_to None、无部分资产
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/api/test_demonstrations.py tests/agent/demonstrations/test_service.py > /tmp/m89-t6-red.log 2>&1; echo $?; tail -n 8 /tmp/m89-t6-red.log
```

Expected：非 0；`ModuleNotFoundError: maa_api.api.routers.demonstrations`；服务层 `convert` 尚不存在。

- [ ] **Step 3: 实现四端点、DTO 与服务的 `convert`**

`router = APIRouter(prefix="/api/agent", tags=["agent"])`；每个路由 `dependencies=[Depends(require_auth)]` 与 `responses=error_responses(...)` 列全可能的码。`start` 的 body 可省略（`None` 按 `{}`），`title` 为空时用 `示教 MM-DD HH:MM`（服务端本地时间）；201 返回 `DemonstrationStartOut`，`screen` 从 `device_manager.snapshot()["resolution"]` 取 `[width, height]`。`list` 的 `status` 非法值 400 `INVALID_PARAMETER`，`page` 默认 1、`size` 默认 20 且 1–50（Pydantic query 越界 422），按 `created_at DESC, id DESC` 排序并返回完整 `steps`；`DemonstrationStepOut` 的 `at` 原样透传服务写入的 `Z` 结尾字符串（字段类型 `str`，不让 Pydantic 重新格式化）。`stop` 与 `convert` 的 body 可省略。服务缺失或 `settings.demonstration.enabled` 为假时四端点都 503 `SERVICE_UNAVAILABLE`（不带 `details`），四端点共用同一个 `_require_service()`。`convert` 方法写在 `maa_api/agent/demonstrations/service.py`（本任务修改 `service.py`），路由只解析 body、调用与映射错误；服务在 `_convert_lock` 中重读行，按 §4.5「转换」的 6 步实现：状态必须 `recorded`；编译与两层校验；逐个比对现有 `custom_task` 名字；`register=false` 直接返回；`register=true` 调 `register_custom_task(name=entry, description=f"示教 {title[:48]}", tasks=compiled.tasks)`，成功后把 `status` 改 `converted`、写 `converted_to` 再提交。404 `DEMONSTRATION_NOT_FOUND` 的 `details` 是 `{"id": id}`；409 `DEMONSTRATION_STATE_INVALID` 的 `details` 是 `{"id","status","action"}`；422 的 `details` 为 `{"errors": [...], "chain_errors": [...]}`；名字预检冲突 422，写入竞态 409 `RESOURCE_ASSET_CONFLICT`，重载失败 500 `RESOURCE_LOAD_FAILED`；`converted_to` 写 `{"entry","task_names","asset_ids","registered_at"}`。

- [ ] **Step 4: 装配服务与启动清理**

`main.py` 在 `device_lease_service` 装配（约 1563–1577 行）之后构造 `DemonstrationService(db_session.session_factory, device_manager=app.state.device_manager, lease=device_lease_service, resource_service=resource_service)`，`await demonstration_service.recover()`，`app.state.demonstration_service = demonstration_service`；`create_app()` 的 router 区块加 `app.include_router(demonstrations.router)`；lifespan 的 teardown 属性元组加 `"demonstration_service"`。不改既有装配顺序，`nav_service` 仍是第 11 行；02 §5 的第 12 行 `demonstration_service` 已由 P4 升格提交就位，T8 只核对。

- [ ] **Step 5: 加配置键的五处同步**

1. `settings.py`：`SETTING_KEYS` 加 `"demonstration.enabled": ("demonstration", "enabled")`；新增 `class DemonstrationSettings(BaseModel)`（`extra="ignore"`，`enabled: bool = True`）；`Settings` 加字段；`ENV_OVERRIDES` 加 `"demonstration.enabled": "MAA_DEMONSTRATION_ENABLED"`。
2. `services/settings_schema.py`：`_FIELDS` 加 `"demonstration.enabled": dict(label="示教录制", group="Agent", type="boolean", description="关闭后示教页与四个端点都不可用", hot_action="none")`。
3. `config.template.yaml`：在 `nav:` 段之后加 `demonstration:` 段与注释（spec §4.4 第 3 条逐字）。
4. `tests/test_settings.py`：`ENV_OVERRIDES` 断言集合加 `MAA_DEMONSTRATION_ENABLED`。
5. 计数断言：`SETTING_KEYS` 144、`_FIELDS` 133。

- [ ] **Step 6: 更新 14 §6.15、生成 OpenAPI 与前端类型**

`docs/v3/14` 的 §6.15 追加四行端点，小节计数 `（83 条）` 改 `（87 条）`，§6 开头总数 `186` 改 `190`；§4 开头的「共 `171` 条」改「共 `173` 条」，若同一句还写了 HTTP 绑定数与节数就一并改成 `172` 与 `23`（当前只写了总数，没有这两个数字就只改总数；以 `grep -n "共 .* 条" docs/v3/14-现行接口清单.md` 实测为准）。§4.23 的两行表格已在 T1 就位。跑：

```bash
.venv/bin/python scripts/dump_openapi.py
cd web && pnpm gen:api
cd ..
.venv/bin/python scripts/dump_openapi.py --check > /tmp/m89-openapi.log 2>&1; echo $?; tail -n 3 /tmp/m89-openapi.log
```

Expected：`0`；`web/openapi.json` 与 `web/src/types/api.d.ts` 出现四个新 operation。

- [ ] **Step 7: 跑端点、路由清单与设置测试**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/api/test_demonstrations.py tests/agent/demonstrations/test_service.py tests/domain/test_route_inventory.py tests/domain/test_errors.py tests/test_settings.py > /tmp/m89-t6.log 2>&1; echo $?; tail -n 8 /tmp/m89-t6.log
.venv/bin/python -c "from maa_api.settings import SETTING_KEYS; from maa_api.services.settings_schema import _FIELDS; print(len(SETTING_KEYS), len(_FIELDS))"
```

Expected：`0`；端点 190/87、错误码 173/172/23、配置 `144 133` 全部对上。

- [ ] **Step 8: 提交**

```bash
git add maa_api/api/routers/demonstrations.py maa_api/agent/demonstrations/service.py maa_api/main.py maa_api/settings.py maa_api/services/settings_schema.py config.template.yaml tests/test_settings.py tests/api/test_demonstrations.py tests/agent/demonstrations/test_service.py tests/domain/test_route_inventory.py docs/v3/14-现行接口清单.md web/openapi.json web/src/types/api.d.ts
git commit -m "feat(m89): 示教四端点、装配与配置开关"
```

---

### Task 7: 前端示教页、画布与转换预览（spec T7）

**Files:**
- Modify: `web/src/routes/router.tsx`、`web/src/components/layout/nav.ts`、`web/src/api/keys.ts`
- Create: `web/src/features/demonstrations/`：`DemonstrationsPage.tsx`、`useDemonstrations.ts`、`LiveCanvas.tsx`、`canvas.ts`、`StepList.tsx`、`ConvertPanel.tsx`、`DemonstrationsPage.test.tsx`、`canvas.test.ts`

**Interfaces:**
- Produces: `keys.agent.demonstrations(filters)`、`keys.agent.demonstrationsAll()`；`useDemonstrations(filters)`、`useStartDemonstration`、`useStopDemonstration`、`useConvertDemonstration`；`mapToDevice(clientX, clientY, rect, screen) -> {x, y}`、`classifyPointer(dx, dy, heldMs)`、`swipeDuration(heldMs)`、`longPressDuration(heldMs)`。
- Consumes: 生成的 `DemonstrationOut`/`DemonstrationPage`/`DemonstrationConvertOut` 类型、`api`（openapi-fetch）、`api.GET('/api/device/status')` 的 `resolution`、截图 URL `/api/device/screenshot?backend=adb&format=jpeg&size=mobile&quality=70`。

- [ ] **Step 1: 写画布与页面测试**

```ts
// canvas.test.ts
it('maps_tap_to_device_pixels', () => {
  expect(mapToDevice(640, 360, { left: 0, top: 0, width: 1280, height: 720 }, { width: 1280, height: 720 }))
    .toEqual({ x: 640, y: 360 })
  expect(mapToDevice(999, 999, { left: 0, top: 0, width: 640, height: 360 }, { width: 1280, height: 720 }))
    .toEqual({ x: 1279, y: 719 })
})
it('classifies_swipe_and_long_press', () => {
  expect(classifyPointer(2, 2, 200)).toBe('tap')
  expect(classifyPointer(100, 0, 300)).toBe('swipe')
  expect(classifyPointer(3, 1, 700)).toBe('long_press')
  expect(swipeDuration(50)).toBe(100)
  expect(swipeDuration(9000)).toBe(2000)
  expect(longPressDuration(600)).toBe(600)
  expect(longPressDuration(9000)).toBe(3000)
})
// DemonstrationsPage.test.tsx
it('starts_and_stops_recording', ...)                 // 按钮、状态文案、查询失效
it('preview_then_register_requires_confirmation', ...) // 预览不出确认，注册出确认
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd web && pnpm test --run src/features/demonstrations > /tmp/m89-t7-red.log 2>&1; echo $?; tail -n 8 /tmp/m89-t7-red.log
```

Expected：非 0；模块不存在。

- [ ] **Step 3: 实现查询、页面与画布**

`useDemonstrations.ts`：`useQuery` 调 `GET /api/agent/demonstrations`，key 为 `keys.agent.demonstrations(filters)`；列表有 `recording` 时 `refetchInterval: 2000`，否则 `false`；三个 mutation 成功后失效 `keys.agent.demonstrationsAll()`。`DemonstrationsPage.tsx` 左右两栏（窄屏单列），左栏「示教」卡片、右栏「示教列表」；进入页面不自动开始，设备未连接或分辨率未知时按钮禁用并显示「设备未连接」。`LiveCanvas.tsx`：录制中每 1000ms 用 `fetch` 取 JPEG，转 object URL 铺在画布上，替换时 `URL.revokeObjectURL` 旧地址；`<img>` 用 `w-full h-auto select-none`、`draggable={false}`，外层 `touch-action: none`；未录制时只显示静态画面与「开始示教」。

`canvas.ts` 的 `mapToDevice` 用 `rect` 的 `width`/`height` 映射到 `screen`，最后夹到 `[0, width-1]`/`[0, height-1]`；不读 `naturalWidth`。手势：pointerdown 记录起点与时间；位移 > 8px 取消长按定时器，抬起按 `swipe` 发 `POST /api/device/swipe`（`duration_ms` 夹 100–2000）；按住 600ms 未移动发 `POST /api/device/long_press`（`duration_ms` 夹 600–3000），抬起不再发点击；其余按下到抬起 < 600ms 且位移 ≤ 8px 发 `POST /api/device/click`。请求进行中忽略新的 pointerdown；失败 Toast「示教失败：{原因}」，录制继续。

- [ ] **Step 4: 实现步骤列表与转换面板**

`StepList.tsx`：每行显示序号、动作、坐标、延迟、状态与两张缩略图（`GET /api/screenshots/{id}`；404/410 显示「截图已过期」占位）。`ConvertPanel.tsx`：预览调 `convert(register=false)`，按节点展开 JSON，显示 `chain` 与 `errors/chain_errors`，有错误禁用「注册任务」；注册先出确认「注册为自定义任务？；会写入 N 个任务节点并重载资源」，确认后调 `convert(register=true)`，成功 Toast「已注册 N 个任务」。页面文案、空态、状态标签逐字照 spec §4.6 的表；`router.tsx` 在 `more/watchdog` 之后加 `more/demonstrations`，`nav.ts` 的 `secondaryNav` 在 `/more/watchdog` 之后加 `{ to: '/more/demonstrations', label: '示教录制', icon: MousePointerClick, description: '在画面里点几下，转成自定义任务', group: 'agent' }`。

- [ ] **Step 5: 跑前端验证**

```bash
cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run src/features/demonstrations > /tmp/m89-t7.log 2>&1; echo $?; tail -n 8 /tmp/m89-t7.log
cd web && pnpm build > /tmp/m89-t7-build.log 2>&1; echo $?; tail -n 5 /tmp/m89-t7-build.log
```

Expected：两个 `0`；1280×720 画布点 (640,360) 请求坐标是 (640,360)；滑动与长按不重复发点击。

- [ ] **Step 6: 提交**

```bash
git add web/src/routes/router.tsx web/src/components/layout/nav.ts web/src/api/keys.ts web/src/features/demonstrations
git commit -m "feat(m89): 示教页面、实时画布与转换预览"
```

---

### Task 8: 冒烟、文档、计数、评测、台账与合并（spec T8）

**Files:**
- Create: `scripts/demonstration_smoke.py`、`tests/evals/scenarios/m89-new-activity-custom-task.yaml`、`tests/evals/scenarios/m89-demonstration-is-user-operation.yaml`、`tests/evals/cassettes/m89-new-activity-custom-task.jsonl`、`tests/evals/cassettes/m89-demonstration-is-user-operation.jsonl`、`docs/v3/ledgers/m89-activity-adapt-teach.md`
- Modify: `docs/v3/02-系统架构.md`、`docs/v3/03-数据模型.md`、`docs/v3/04-接口与事件协议.md`、`docs/v3/05-运行时与代理定义.md`、`docs/v3/11-前端指挥台.md`、`docs/v3/13-决策记录.md`、`tests/evals/test_regression_replay.py`

**Interfaces:**
- Produces: `SMOKE OK` 的示教端到端冒烟；两个 capability 场景；`tests/evals/test_regression_replay.py` 的场景数断言 `21`→`23`（两处：`len(scenarios)` 与 `len(ids)`）；02–05、11、13 的同步段落；台账。
- Consumes: T1–T7 的全部实现、`tests/fakes/lifespan.py` 的 `install_fake_lifespan` 与 `SmokeDeviceManager`、`tests/fakes/v3_db.py` 的临时库写法、`scripts/agent_smoke.py` 的隔离写法。

- [ ] **Step 1: 写示教冒烟脚本**

照 `scripts/agent_smoke.py` 的隔离写法：临时目录、重设 `db_session` 的 `DB_PATH/ASYNC_URL/SYNC_URL/engine/session_factory`、清空 `ENV_OVERRIDES` 环境变量、改 `settings_module.DEFAULT_CONFIG_PATH` 与 `main_module.REPO_ROOT`、`install_fake_lifespan(main_module.app)` 之后把 `app.state.device_manager_factory` 换成 `SmokeDemoDevice`，再 `TestClient.__enter__()`。`SmokeDemoDevice` **继承 `tests/fakes/lifespan.py` 的 `SmokeDeviceManager`**（构造签名 `settings, client, *, broadcast, core_id` 和 `connect_with_retry`、`on_core_connection_event`、`close`、`reconnect_retry_attempts` 全部复用），只覆写 `snapshot()` 为 `{**super().snapshot(), "state": "connected", "resolution": {"width":1280,"height":720}}`，新增 `screenshot(backend="adb")` 返回 `ScreenshotResult(Image.new("RGB", (1280, 720), "white"), "adb")`，并新增 `swipe(x1, y1, x2, y2, duration_ms)`、`long_press(x, y, duration_ms)` 的记录列表（签名同 `DeviceManager`）；`click` 沿用基类的 `clicks` 记录。不新写平级假设备，否则 lifespan 的 `on_core_connection_event`（FakeAsst 的 `ConnectionInfo:Connected` 回调）与 `close()` 会缺接口。

脚本依次断言并打印 `[ok] ...`：

1. `POST /api/agent/demonstrations` → 201、`status=recording`、`screen=[1280,720]`；`app.state.device_lease_service.recording()` 为真。
2. 三次 `POST /api/device/click`、一次 `swipe`、一次 `long_press` → `GET /api/agent/demonstrations` 的该行有 5 步；每步两个截图 id 非空、`at` 以 `Z` 结尾且可被 `datetime.fromisoformat` 解析（共 10 行 `screenshot`）。
3. `POST /{id}/stop` → `recorded`，闸门释放。
4. `POST /{id}/convert {"register": false}` → `compile.valid=true`、`chain` 长度 6、状态仍 `recorded`。
5. `POST /{id}/convert {"register": true}` → `converted`、`converted_to.entry` 以 `Custom_Demo_` 开头；`resource/maa-layers/custom/resource/tasks.json` 含入口与 `_Done`。
6. 用 `submit_pipeline` 的 REST 端点提交 `[{"name":"Custom","task_names":[entry]}]`，假内核收到一次 `Custom` 任务。
7. 失败打印 `[FAIL] 原因` 并返回 1；成功末行严格 `SMOKE OK`。

- [ ] **Step 2: 写两个评测场景与 cassette**

`m89-new-activity-custom-task.yaml`：`suite=capability`、`agent=general`、`entry.kind=ticket`（`worker=general`，`title`/`brief`/`scope` 写「新活动没有作业，走一遍并注册自定义任务」）、`human.confirmations=approve`；`tool_sequence` 的 `steps` 依次 `draft_custom_task`、`validate_custom_task`、`register_custom_task`（`args` 含 `{"name": {"re": "^Custom_"}, "tasks": {"len": {"gte": 3}}}`）、`submit_pipeline`，`max_calls={"register_custom_task": 1}`；`outcome_state` 要求流水线 `completed`；`text_contains` 含 `Custom_` 与「开始行动」。cassette 的 header `"agent":"general"`、`"source":"handwritten"`；前两条动态上下文探查回 `{"text":"OK"}`，之后 `draft_custom_task`（3 个点击步骤、`screen=[1280,720]`）、`validate_custom_task`（`entry`/`tasks` 用 `{{result:call-draft:entry}}`/`{{result:call-draft:tasks}}`）、`register_custom_task`（`name`/`tasks` 用 `{{result:call-draft:entry}}`/`{{result:call-draft:tasks}}`）、`submit_pipeline`（`task_names` 用 `{{result:call-register:entry}}`）、`report`（该条应答同时带 `text` 摘要与 `tool_calls`，摘要含 `Custom_` 与「开始行动」），末尾一条记忆抽取回 `{"text":"{\"items\": []}"}`。

`m89-demonstration-is-user-operation.yaml`：同样 `suite=capability`、`agent=general`、ticket 入口（`title`/`brief`/`scope` 写「用户在示教页录了一段，问接下来怎么办」）；`tool_sequence` 的 `forbid` 为 `["draft_custom_task","validate_custom_task","register_custom_task"]`，`min_calls={"report":1}`；`text_contains` 含 `/more/demonstrations` 与「示教」。cassette 为两条探查 + `report`（应答带 `text` 摘要，摘要含 `/more/demonstrations` 与「示教是用户自己在页面里完成的操作」）+ 一条记忆抽取。

写完后改 `tests/evals/test_regression_replay.py` 的 `test_every_scenario_loads_and_has_a_cassette`：`assert len(scenarios) == 21` 与 `assert len(ids) == 21` 两处都改成 `== 23`（M88 后 21，M89 加 2 个场景，21→23）。若 Task 0 实测不是 21，按「实测 + 2」改并把差值记台账，不改别的断言。

- [ ] **Step 3: 同步文档与计数**

按 spec §9 逐项核对；P4 升格提交已就位的只核对、缺的才补，不重复添加：

- 03 §12 的 `demonstration` 行已含 `steps` 元素（含 `at`）与 `converted_to` 形状、§13 的 `DemonstrationStatus`、§14 的「不自动删除」列表含 `demonstration`：核对措辞与 spec §4.1 一致，缺则补。
- 04 §2.5 的四行端点、§5 的两个新码、§6 的 `demonstration.enabled` 已就位；`CUSTOM_TASK_INVALID` 在 04 §5 已是 422（M89 复用，spec §9 的「400 改 422」无需执行），只核对不改。
- 05 §3.2 的 `general` 工具列已含 `draft_custom_task`、`validate_custom_task`(M89)；§6.1 的计数句已含 `110/109`、目录表有 M89 行、契约段已写明 `draft_custom_task`/`validate_custom_task` 的参数与返回以及 `register_custom_task` 的可选 `tasks`（工具层 1–32、与 `content` 二选一、additive）：逐条核对 T5 实现与文档一致，缺则补。
- 02 §2.2 的 `DemonstrationService`、§4.1 的 `agent/demonstrations/` 与 `agent/custom_task/`、`api/routers/demonstrations.py`、`archive_screenshot` 行的「M89 复用」说明、§4.3 的 `demonstration_smoke.py`、§5 装配表第 12 行 `demonstration_service`（`nav_service` 保持第 11 行）已就位：核对归档复用说明写明「M86 的 `archive_screenshot`，M89 采集复用」；缺则补。
- 11 §3 的 `/more/demonstrations` 行已含实时画布、点/滑/长按、预览与注册确认：核对。
- 14 的 §4.23 两行在 T1 已加、§6.15 四行与总数/节数在 T6 已改：T8 只跑 `test_errors.py` 与 `test_route_inventory.py` 复核 `173/172/23` 与 `190/87`，不重复改文档。
- 13 §8 末尾按当时最后编号 +1 加一条，记 D1–D24 的裁决、REST 写资源例外、长按编译方式、04 §5 已是 422 无需改、遗留真机项。

- [ ] **Step 4: 写台账**

`docs/v3/ledgers/m89-activity-adapt-teach.md`：分支、基点、合并提交、tag、spec/plan 链接、T0–T8 表格、§0 实测值、Rulings（错误码文档落点、配置键同步落点、冒烟注入点、路由锚点、冲突预检、04 §5 已是 422 不执行「400 改 422」、02/03/04/05/11 的预置条目只核对）、验证证据（命令与结果）、遗留（真机验收 6 项、滑动可靠性）。

- [ ] **Step 5: 跑示教冒烟、评测与文档检查**

```bash
.venv/bin/python scripts/demonstration_smoke.py > /tmp/m89-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m89-smoke.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m89-cap.log 2>&1; echo $?; tail -n 8 /tmp/m89-cap.log
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_regression_replay.py > /tmp/m89-eval-pytest.log 2>&1; echo $?; tail -n 5 /tmp/m89-eval-pytest.log
.venv/bin/python scripts/check_doc_links.py | tail -n 1
```

Expected：`0`、`SMOKE OK`；capability `0` 且通过数不低于 M88 台账的 capability 基线（M88 后 16 个场景，M89 加 2 个后 18 个）；场景文件总数 23；`test_regression_replay.py` 通过且两处断言是 23；`BROKEN 0`。

- [ ] **Step 6: 跑 spec §7 的自动验收**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/domain/test_enums.py tests/domain/test_errors.py tests/db/test_demonstration_migration.py tests/db/test_models.py tests/agent/demonstrations tests/api/test_demonstration_capture.py tests/api/test_demonstrations.py tests/services/test_custom_task_rules.py tests/agent/test_custom_task_tools.py > /tmp/m89-target.log 2>&1; echo $?; tail -n 8 /tmp/m89-target.log
.venv/bin/python -m pytest -o addopts= -q > /tmp/m89-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m89-pytest.log
.venv/bin/python scripts/dump_openapi.py --check > /tmp/m89-openapi.log 2>&1; echo $?; tail -n 2 /tmp/m89-openapi.log
(cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build) > /tmp/m89-web.log 2>&1; echo $?; tail -n 5 /tmp/m89-web.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m89-agent-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m89-agent-smoke.log
.venv/bin/python scripts/orchestrator_smoke.py > /tmp/m89-orch-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m89-orch-smoke.log
.venv/bin/python scripts/frontend_smoke.py > /tmp/m89-front-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m89-front-smoke.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m89-reg.log 2>&1; echo $?; tail -n 8 /tmp/m89-reg.log
.venv/bin/python scripts/check_doc_links.py | tail -n 1
git diff --check
```

Expected：所有退出码 `0`；全量 pytest 与 T0 基线相比不新增失败；四个冒烟（示教、agent、orchestrator、frontend）末行都是 `SMOKE OK`；regression 全通过；`BROKEN 0`；`git diff --check` 无输出。真机 6 项按 spec §7 手动执行，未执行就记「未验证」。

- [ ] **Step 7: 提交、合并与 tag**

```bash
git add scripts/demonstration_smoke.py tests/evals/scenarios/m89-new-activity-custom-task.yaml tests/evals/scenarios/m89-demonstration-is-user-operation.yaml tests/evals/cassettes/m89-new-activity-custom-task.jsonl tests/evals/cassettes/m89-demonstration-is-user-operation.jsonl tests/evals/test_regression_replay.py docs/v3/02-系统架构.md docs/v3/03-数据模型.md docs/v3/04-接口与事件协议.md docs/v3/05-运行时与代理定义.md docs/v3/11-前端指挥台.md docs/v3/13-决策记录.md docs/v3/ledgers/m89-activity-adapt-teach.md
git commit -m "docs(m89): 示教冒烟、评测场景与契约同步"
git checkout dev
git merge --no-ff activity-adapt-teach
git tag v3-m89
git branch -d activity-adapt-teach
```

合并后在 `dev` 上重跑 Step 6 的 1、2、7 条与四个冒烟；把 merge commit、tag 与复验结果补进台账并提交。

---

## Self-Review

- **规格覆盖**：§0（Task 0 + 各任务验证）、§4.1（T1 的列与 `at`）、§4.2 服务/编译器/rules/capture（T2–T4、T6 的 convert）、§4.3 四端点与 DTO（T6）、§4.4 配置键（T6）、§4.5 状态机/闸门/采集/编译/校验/批量注册/转换/启动清理（T2–T4、T6）、§4.6 前端（T7）、§4.7 提示词与版本（T5）、§5 T1–T8（一一对应）、§6 每个用例名都在所属任务的测试步骤里、§7 验收（T8）、§9 文档与计数（T1、T6、T8）、§11 契约名字（T1、T4、T5、T6、T8）。
- **Review Focus 落点**：5 条分别由 T2（互斥与恢复）、T4+T6（两阶段与回滚）、T4+T7（720 换算）、T3（截图失败）、T7（画布映射）的具名用例钉住，没有只写在文档里的条目。
- **类型与命名一致**：`DemonstrationService` 的八个方法（含 `convert`）与 T6 端点、T3 `capture` 调用一致；`Step/Compiled` 与 T5 的 `DraftStep`、T6 的 `CompiledTaskOut` 对齐；`ClaimGate.allow_manual` 在 T2 定义、T2 的仓储/runner 与 Review Focus 引用同名；`register_custom_task(tasks=...)` 在 T4 定义、T5 与 T6 调用一致；`converted_to` 的四个键在 T4、T6、T8 冒烟里同名；`steps.at` 在 T1 的元素形状、T2 的 `note_step`、T3 的 `started_at` 透传、T6 的 `DemonstrationStepOut.at: str`、T8 冒烟断言五处同名。
- **评审回补**：场景数断言 `21→23`（Task 8）、`steps` 的 `at`（T1/T2/T3/T6/T8）、冒烟假设备继承 `SmokeDeviceManager`（T8）、05 §6.1 三条工具契约与 02 §4.1 的 `archive_screenshot` 复用核对（T8）、04 §5 已是 422 不改（T8）、convert 的服务层两阶段与注册失败用例（T6）都已落到具体步骤。
- **比例**：计划正文只写签名、常量、节点模板与测试断言，不抄服务函数体；编译器算法只写规则，不写实现逐行。
- **未决项**：无。真机 6 项是 spec §7 明确的可选补充，不是阻塞项；计数用 Task 0 实测校正的规则已写进开工基线。
