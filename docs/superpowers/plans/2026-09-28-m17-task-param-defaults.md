# M17 · 任务参数默认值实施计划

规格见 [specs/2026-09-28-m17-task-param-defaults.md](../specs/2026-09-28-m17-task-param-defaults.md)。**逐字段默认值表在规格 §4，实现时逐行照做，不要自行发挥。**

## 全局约束

- 后端测试：`.venv/bin/python -m pytest -q`（`python` 不在 PATH）。
- 前端：`cd web`，pnpm 用 `/Users/user/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/pnpm/bin/pnpm.mjs`，node 用 `/opt/homebrew/bin/node`（PATH 里没有 node；捆绑的 node v24 缺 rolldown native binding，会启动失败）。
- 改了后端接口后：`.venv/bin/python scripts/dump_openapi.py` → `cd web && pnpm gen:api`；前端 fixture 用 `.venv/bin/python scripts/generate_task_types_fixture.py` 重新生成。
- **实现者不要 `git commit`**：并行任务的索引会打架，提交由主 agent 在验收后统一做（提交信息用 Conventional Commits）。
- 长输出重定向到文件，只读尾部和退出码。
- pydantic v2 的 `default_factory` **不会**在 JSON Schema 里输出 `default`（实测），可变默认值必须写成字面量 `default=[...]` / `default={...}`：pydantic 会按实例深拷贝，不存在共享可变对象问题。
- 不引入新依赖。
- 涉及行为的裁决按规格执行；规格没覆盖到的实现细节自行决定并写进最终报告（我会抄进台账）。

## Task 1 · 后端：任务参数默认值与文案修正（已完成）

**产物**：`maa_api/domain/task.py`、`tests/domain/test_task_defaults.py`（新增）

- 按规格 §4 给字段补 `default=`。`enable` 在 `TaskInputBase` 上改一次即覆盖 9 种类型。
- 规格 §3.3 的三个本服务约定值（`Recruit.select` / `confirm`、`Infrast.facility`）在 `description` 里写明「默认值由本服务约定，内核无默认值」。
- `Infrast.facility` 补 `x-enum-labels`：`Mfg`=制造站、`Trade`=贸易站、`Power`=发电站、`Control`=控制中枢、`Reception`=会客室、`Office`=办公室、`Dorm`=宿舍。
- 文案修正（规格 §5.2）：`start_game_enabled` 的「默认 True」改成内核默认 False（源码 `StartUpTask.cpp:94`）；`Recruit.skip_robot` 改成键缺省=不跳过（源码用 `find`）；`Recruit.set_time` 的生效条件；`Recruit.confirm` 的「仅公招计算」= `confirm` 含 `-1`；`Recruit.expedite_times` 标注 v6.17.5 解析后丢弃；`Fight.expiring_medicine` 标注已弃用（内核改用 `medicine_expire_days`）；`Roguelike.squad/roles/core_char` 改成「留空 = 内核自动选择」。已写 `default` 的字段，把「不传则由内核使用默认值（X）」统一改成「内核默认值 X」。
- 新增测试（先 RED 再 GREEN）：
  - 9 种任务类型「只给 `name`」构造 → `normalize(task, ChannelDefaults())` 不抛异常（挡住「默认值违反 `model_validator`」）；
  - 规格 §4 表逐字段断言 `model_fields[field].default`；skip 的字段断言 `default is None`；
  - `facility` 的中文 `x-enum-labels` 存在且七个设施齐全。
- 运行：`.venv/bin/python -m pytest -q tests/domain`；提交 `feat(tasks): declare kernel defaults on task params`。

## Task 2 · 后端：渠道默认值的 schema overlay（已完成）

**产物**：`maa_api/api/routers/tasks.py`、`tests/api/test_task_types_defaults.py`（新增）

- `GET /api/tasks/types` 与 `/api/tasks/types/{type_name}`：在返回的 schema 里给 `client_type` / `server` 注入当前生效的 `default`（`load_channel_defaults`，默认 `Bilibili` / `CN`）。只改这两个响应对象，**不动 pydantic 模型、不动 `POST /validate` 与提交路径的注入语义**（`model_fields_set` 三态保持原样）。
- 注入要写在导出的 schema 副本上，不要污染 `model_json_schema()` 的缓存结果（参考 `export_type_schema` 现有的 `deepcopy`/重建方式，按代码事实处理）。
- 测试：默认设置下 `client_type.default == "Bilibili"`、`server.default == "CN"`；把 `channel.client_type` 写成 `Official` 后重新请求 → `default == "Official"`（用现有的设置仓储/session fixture）。
- 运行：`.venv/bin/python -m pytest -q tests/api`；提交 `feat(api): expose effective channel defaults in task schemas`。

## Task 3 · 生成物同步（Task 1 + 2 完成后）（已完成）

**产物**：`web/openapi.json`、`web/src/types/api.d.ts`、`web/src/features/tasks/task-types.fixture.json`

- `.venv/bin/python scripts/dump_openapi.py`
- `cd web && pnpm gen:api`
- `.venv/bin/python scripts/generate_task_types_fixture.py`
- 检查 diff：OpenAPI 里应出现约 55 个字段的 `default`，`api.d.ts` 只应出现默认值的类型变化，fixture 里 `client_type`/`server` 带 overlay 的 `default`。
- 提交 `chore(api): regenerate schema snapshots and task fixture`。

## Task 4 · 前端：按默认值显示与预填（Task 3 完成后）

**产物**：`web/src/features/tasks/form/{values,schema,serialize,summary}.ts`、`web/src/features/tasks/editor/*`、`web/src/features/tasks/composer/drafts.ts`、`web/src/features/schedules/ScheduleEditor.tsx`（如需）、相关测试

- 规格 §6 的八条逐条实现：物化默认值、状态与标签（默认 / 未设置 / 已修改 / 已设为空 / 必填）、恢复默认写回默认值、控件预填、摘要只列改过的字段、提交前兜底物化、设置保存后失效 `taskTypesKey`、测试。
- 深比较要处理数组与对象（`expected_collapsal_paradigms` 这类），不要用 `Object.is` 一把梭。
- 保持既有可访问性契约（`aria-describedby` 里的状态 id、`aria-label`）不变；`ui.test.tsx` / `editor.test.tsx` 里已有的断言可以改，但不能删测试。
- 运行：`cd web && pnpm typecheck && pnpm test --run && pnpm build`（用上面的 node/pnpm 路径）。
- 提交 `feat(web): prefill task params from schema defaults`。

## Task 5 · 文档同步（已完成）

**产物**：`docs/05-API规范与路由清单.md`、`docs/04-数据模型与持久化.md`、`docs/09-前端重构方案.md`、`docs/13-决策记录.md`、`docs/ENVIRONMENT.md`

- docs/05 §7/§9：默认值现在写进 schema；`to_core_params` 的 `exclude_none=True` 意味着默认值会随下发快照显式下发；`raw_params` 仍是 `exclude_unset=True`；渠道默认值 overlay 的适用范围与「不再跟随」的代价。
- docs/09 §7.3/§603/§656：把「未改动的字段不进草稿……控件显示内核默认值并标「默认」」改写为新的物化 + 预填 + 显式下发模型，并保留「服务端不预设内核默认值」这条历史理由的说明（现在由 §8 的偏离条目承接）。
- docs/13 §8 追加偏离条目（规格 §8 的五条），并注明用户 2026-09-28 的裁决。
- docs/04 §5.2（`task` 表）：`params` 快照现在包含默认值。
- docs/ENVIRONMENT.md：追加「`AsstAppendTask` 必填参数」的 v6.17.5 复测结论（`Recruit` 需 `select`+`confirm`、`Infrast` 需 `facility`、append 不需要连设备），并指向 `scripts/probe_task_defaults.py`。
- 提交 `docs: record task param default semantics`。

## Task 6 · 验收（主 agent 亲自跑）

- `.venv/bin/python -m pytest -q`
- `.venv/bin/python scripts/dump_openapi.py --check`
- `cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build && cd ..`
- `.venv/bin/python scripts/frontend_smoke.py`
- `DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin .venv/bin/python scripts/probe_task_defaults.py --defaults <全默认 JSON>`：每个任务类型 `task_id != 0`
- 完成后 `git merge --no-ff` 回 `refactor/v2`，打 tag `v2-m17`，把本计划连同 progress 归档到 `docs/superpowers/ledgers/2026-09-28-m17-task-param-defaults.md`。
