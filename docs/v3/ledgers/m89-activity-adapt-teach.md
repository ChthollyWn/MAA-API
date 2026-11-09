# M89 新活动自适应与示教录制（台账）

- 分支：`activity-adapt-teach`；基点：`dev` = `e0ce1c6`（M88 合并 + 台账提交）；合并：待主 agent 复核后执行（本波不合并）；tag：`v3-m89`（待主 agent）
- spec：[m89-activity-adapt-teach](../specs/m89-activity-adapt-teach.md)；计划：[m89-activity-adapt-teach](../plans/m89-activity-adapt-teach.md)

## §0 结果

| # | 实测 |
|---|---|
| 1 | `## activity-adapt-teach` |
| 2 | `v3-m88` |
| 3 | 1 行（archive_screenshot） |
| 4 | `0041_nav_graph.py` |
| 5 | `108 107` |
| 6 | 端点 186；错误码 `171 170 22` |
| 7 | 业务表 57；配置 `143 132` |
| 8 | 3 行（register_custom_task/_validate_custom_task/_write_enabled_custom_tasks） |
| 9 | atomic.py 1 / device.py 2 |
| 10 | lease.py 5 / device_lease.py 1 |
| 11 | demonstration 代码 0、前端路由 0 |
| 12 | 360 passed（enums/models/migrate） |
| 13 | 场景 21 |

- 基线（M88 合并后 dev、M88 台账）：全量 pytest **3 failed / 5487 passed / 5 skipped / 2 xfailed**（3 条既有真资源失败）；`tests/evals` **72 passed**；capability **16/16**、`EVAL OK`；工具 108/107；端点 186；错误码 171/170/22；表 57；配置 143/132；场景 21；head 0041。

## 任务与提交

| 任务 | 提交 | 结果 |
|---|---|---|
| T0 前置检查与接缝裁决 | 无（只核对） | §0 十三条全通过；键位、函数、计数与 spec 一致 |
| T1 枚举、错误码、表与迁移 | `88a5b5b` | `DemonstrationStatus`、两个错误码、`demonstration` 七列表、迁移 `0042_demonstration`；错误码 173/172/23、表 58 |
| T2 服务状态机与录制闸门 | `47d4cc2` | `DemonstrationService` 八个方法、`_recording` 闸门、`ClaimGate.allow_manual`、仓储与 runner 透传 |
| T3 采集接线 | `464638e` | 三个设备端点接 `begin`/`finish`/`cancel`；未录制时行为逐字不变；截图失败 id 记 `null` |
| T4 编译器、校验器与批量注册 | `4e880db` | 节点模板与 720 换算、`check_bundle`、`register_custom_task(tasks=...)` 单事务/单重载/失败全回滚 |
| T5 工具、代理定义与计数 | `77628cf` | 两个工具、`general` v8、工具 110/109、refetchable 54 |
| T6 REST、装配、OpenAPI 与清单 | `f42602b` | 四端点、`convert`、`recover()` 装配、`demonstration.enabled`；端点 190（agent 87）、配置 144/133 |
| T7 前端示教页、画布与转换预览 | `296b28f` | `/more/demonstrations`、实时画布、点/滑/长按、步骤列表、转换预览与注册确认；7 个新前端用例 |
| T8 冒烟、文档、计数、评测与台账 | 本提交 | 示教冒烟、两个 capability 场景、02/03/04/05/11/13/15/16 与配置同步、台账 |

## Rulings I made

1. **错误码文档落点**：`DEMONSTRATION_NOT_FOUND`、`DEMONSTRATION_STATE_INVALID` 只进 14 §4.23 与 04 §5；不新增 error code 之外的枚举或 HTTP 绑定。15 的错误码表是生成物，T1/T6 漏跑后在 T8 补 `scripts/generate_api_guide.py`，`--check` 归零。
2. **配置键同步落点**：`demonstration.enabled` 同时改 `settings.py`（`SETTING_KEYS`/`ENV_OVERRIDES`/`DemonstrationSettings`）、`settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`；04 §6 已有行，只核对。前端设置分区不需要新条目。
3. **冒烟注入点**：`SmokeDemoDevice` 继承 `tests/fakes/lifespan.py` 的 `SmokeDeviceManager`，只覆写 `snapshot()` 并新增 `screenshot`/`swipe`/`long_press`；在 `install_fake_lifespan(app)` 之后替换 `app.state.device_manager_factory`，复用 FakeAsst 的连接回调与 `close()`，不新写平级假设备。
4. **路由锚点**：brief 要求把 `more/demonstrations` 放在 `more/watchdog` 之后，spec §4.6 写「放在 `more/nav` 之前」；两处只影响 `router.tsx` 文件顺序，路由匹配无差异，按 brief 落地并记 13 §8.183。
5. **冲突预检**：`convert` 在预览阶段也检查同名 `custom_task` 资产，冲突按 422 `CUSTOM_TASK_INVALID` 返回，因为注册一定失败；写入竞态仍由服务层抛 409 `RESOURCE_ASSET_CONFLICT`。
6. **04 §5 已是 422**：`CUSTOM_TASK_INVALID` 在 v2 文档里本来就是 422，spec §9 的「400 改 422」只核对不执行，也不新增第三个码。
7. **02/03/04/05/11 的预置条目只核对**：P4 升格提交已写入 `DemonstrationService`、模块树、四端点、错误码、设置项、导航图行与装配表第 12 行；T8 逐条对 spec §4.1/§4.3/§4.4 检查，未重复添加。05 §6.1 的「当前」计数从 M88 的 108/107 改成 110/109（M89 收口，M90 后 111/110），与 §8.182 偏离 6 的单点口径一致。
8. **13 §8.183**：记录 convert 的写资源例外、`register_custom_task` 的 `tasks`、`CUSTOM_TASK_INVALID` 保持 422、编译器缺省时长/空 steps/slug、采集顺序与 `device_lost`、`note_step` 无 `screen`、`register` 字段告警、未验证项。
9. **15/16 补充**：15 只重生成、不手改；16 新增 §24「示教录制（M89）」，是 spec §9 清单之外的部署说明，沿用 M88 §23 的先例。
10. **`test_m70_specs.py` 只改一处**：brief 说 `general` 期望集合有两处，实测只有一处，已改该处。
11. **既存测试替身跟随私有签名**：T2 把 `claim_next` 改成始终把 `allowed_session_ids`/`allow_manual` 传给 `_next_pending_id`，`tests/db/test_repositories_pipeline.py` 里既有的 `_StaleCandidateRepository` 只接受 `core_id`；T8 全量 pytest 抓出后补上同签名参数，断言不变（`17 passed`）。

## 验证证据

| 命令 | 结果 |
|---|---|
| T1 指定四文件 pytest（w1 报告） | `410 passed`，退出码 0；加 `tests/db/test_migrate.py` 后 `420 passed` |
| T2 指定 pytest（w1 报告） | `72 passed`，退出码 0；`tests/agent/` 全量 `1724 passed, 1 skipped, 2 xfailed` |
| T3 指定 pytest（w2 报告） | `31 passed`；capture 单文件 `7 passed` |
| T4 指定 pytest（w2 报告） | `45 passed`；`tests/api` 全量 `626 passed` |
| T5 指定 pytest（w3 报告） | `87 passed`；工具计数 `110 109` |
| T6 指定 pytest（w3 报告） | `138 passed`；补充回归 `550 passed`；`dump_openapi.py --check` 退出码 0 |
| T7 前端（`/tmp/m89-t7.log`、`/tmp/m89-t7-type.log`、`/tmp/m89-t7-build.log`） | `pnpm gen:api:check` 0；`pnpm typecheck` 0；`pnpm test --run src/features/demonstrations` **7 passed**；全量 `pnpm test --run` **1001 passed（115 文件）**；`pnpm build` 0 |
| T8 示教冒烟（`/tmp/m89-smoke.log`） | 退出码 0，末行 `SMOKE OK`；开始/5 步/10 截图/停止/预览 chain 6/注册/`Custom` 流水线全跑通 |
| T8 capability（`/tmp/m89-cap.log`） | 退出码 0，**18/18 通过**，`EVAL OK`；两个新场景分别 4 与 8 次模型调用 |
| T8 regression（`/tmp/m89-reg.log`） | 退出码 0，**5/5 通过**，`EVAL OK` |
| T8 `tests/evals/test_regression_replay.py`（`/tmp/m89-eval-pytest.log`） | `7 passed`，场景数两处断言为 23 |
| 目标测试（`/tmp/m89-target.log`） | **449 passed**，退出码 0 |
| 全量后端 pytest（`/tmp/m89-pytest.log`） | **3 failed / 5550 passed / 5 skipped / 2 xfailed**，与基线同一组真资源失败，无新增（详见「最终全量」） |
| `dump_openapi.py --check` | 退出码 0 |
| 前端五步（`/tmp/m89-web.log`） | 退出码 0；`gen:api:check` 0、`typecheck` 0、`pnpm test --run` **1001 passed（115 文件）**、`pnpm build` 0 |
| `agent_smoke.py` / `orchestrator_smoke.py` / `frontend_smoke.py` | 末行均 `SMOKE OK` |
| `generate_api_guide.py --check` | 退出码 0 |
| `check_doc_links.py` | 末行 `BROKEN 0` |
| `git diff --check` | 无输出 |
| 计数复核 | 工具 `110 109`；配置 `144/133`；测试 `test_errors.py` + `test_route_inventory.py` + `test_models.py` 共 283 passed（173/172/23、190/87、58） |
| 既存测试替身（`/tmp/m89-repo-test.log`） | 补 `_next_pending_id` 参数后 `17 passed` |

### 最终全量

- 后端全量（`/tmp/m89-pytest.log`）：**3 failed / 5550 passed / 5 skipped / 2 xfailed**，耗时 502.84 秒。3 条失败与基线同一组真资源用例：`test_activity_index.py::test_real_resource_pack_reports_the_current_activity_and_its_stages`、`test_activity_index.py::test_real_resource_pack_disambiguates_a_repeated_code`、`test_stage_layout.py::test_ambiguous_code_picks_the_first_stage_id_and_counts_candidates`；**没有新增失败**。M89 前基线 3 failed / 5487 passed / 5 skipped / 2 xfailed，通过数增加 63。
- 第一次全量（修测试替身前）另外失败的两条：`test_incidents.py::test_incident_allowed_action_still_uses_confirmation_service`（代码注释里记过的满载 flake，单独回放通过）与 `test_repositories_pipeline.py::test_claim_next_returns_none_when_candidate_was_taken`（测试替身签名，已修）；第二次全量两条都通过。

## 偏离（已记 13 §8.183）

- convert 是 v2「REST 不写资源」的唯一例外；`register_custom_task` 加可选 `tasks`（工具层 32、服务层 128、additive）。
- `CUSTOM_TASK_INVALID` 保持 422（04 已是 422，M89 只核对）。
- 编译器缺省 swipe 300 ms、long_press 600 ms；空 steps 抛 `CompileError`；slug 首字符非字母数字补 `Demo_`。
- 采集顺序：`begin` 在 `preempt_device` 之前；设备未连接 `abort("device_lost")`；分辨率未知跳过该步；动作成功期间 stop 的 409 吞掉；`note_step` 无 `screen` 参数（用当时快照）。
- 启动 `recover()` 把 `recording` 置 `discarded`；`register` 与 Pydantic `BaseModel.register` 同名产生良性 `UserWarning`，`CompiledTaskOut.register` 用显式 `Field`。
- 15 生成块在 T1/T6 后未更新，T8 补跑；16 补 §24 部署说明；`test_m70_specs.py` 实测只有一处 `general` 集合。
- 真机 6 项、滑动/长按可靠性、物理输入 `getevent`、画布只录页面内操作、截图 7 天过期后的真实时钟行为均**未验证**。

## 整支审查修复

- 整支审查 REQUEST CHANGES（队列唤醒缺失、设备断开不自动丢弃、stop(A)/start(B) 竞态、前端长按定时器与 convert 错误分支、批量注册冲突回滚用例）→ 修复 `508d28d`；scoped re-review 又复现「`_release_gate` 与 `start` 的在途交错」→ 修复 `_release_gate` 持 `_start_lock` 并补确定性用例（红证据：无锁时该用例 `stop(A)` 提前完成、C 的闸门被关）。
- `abort(demonstration_id=…)` 的 id 不匹配选择返回 `False`（内部方法、无 REST 面），不再抛 409；`stop` 不匹配仍抛 `DEMONSTRATION_STATE_INVALID`。

## 遗留

1. spec §7 的 6 项真机验收**未执行**：真机点击/滑动/长按与截图对齐、预览 chain 顺序、注册后 `Custom` 试跑、录制中刷新与重开页面、拔设备与 `discarded`、连续 20 次点击的截图时差。本任务只用假设备、回放与临时库验证，如实记「未验证」。
2. 滑动与长按的真机可靠性未验证：v2 真机记录里 `adb shell input swipe` 对游戏画面可能完全无效；长按编译成起终点相同的 `Swipe`，需要真机确认这段时长被当作长按。页面文案已写明「滑动是否生效取决于设备与界面，点击最可靠」。
3. 画布只录页面内发生的操作；用户在手机上直接操作不会被采集（D1 边界），物理输入 `getevent` 不在 M89 范围。
4. 步骤截图只保留 7 天，过期后页面显示「截图已过期」占位；已注册任务不依赖截图。该过期行为没有用真实时钟跨 7 天验证。
5. capability 首次整包回放时遇到一次 SQLite `database is locked`（知识文档种子写入）导致新场景超时；单独回放与重跑整包均 18/18 通过，未改评测 harness。若后续再出现，按环境抖动另查。
6. 合并、tag、删除分支按用户要求留给主 agent；本台账的「合并」「tag」两行在合并后补。
7. `test_incident_allowed_action_still_uses_confirmation_service` 是代码注释里记过的满载 flake（M68 §8.26、M71 §8.44）：第一次全量 pytest 在 60 秒上限处超时，单跑通过；修完测试替身后第二次全量结果见「最终全量」。这是既存 flake，不是 M89 新失败。
