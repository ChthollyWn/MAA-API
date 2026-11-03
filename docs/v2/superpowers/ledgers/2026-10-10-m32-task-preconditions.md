# M32 · 任务执行前提（游戏状态）与流水线护栏

- 计划：`docs/superpowers/plans/2026-10-10-m32-task-preconditions.md`
- 规格：`docs/superpowers/specs/2026-10-10-m32-task-preconditions.md`
- 分支：`m32-task-preconditions`（自 `refactor/v2`），tag `v2-m32`

## 任务状态

| 工作项 | 产物 | 状态 |
| --- | --- | --- |
| W1 知识库条目 | `maa_api/agent/knowledge/builtin/task-preconditions.md`、`tests/agent/test_knowledge_service.py`（四份规格登记表）、`tests/agent/test_knowledge_no_numbers.py`（33 条）、`tests/services/test_knowledge_page_pointers.py`（33 条） | 完成 |
| W2 既有条目消歧 | `playbook-recruit.md`、`playbook-infrast-shift.md`、`playbook-sanity-planning.md`、`account-resources.md` | 完成 |
| W3 工具与提示词 | `maa_api/agent/tools/pipeline.py`、`maa_api/agent/tools/account.py`、`maa_api/agent/runtime.py` | 完成 |
| W4 代码护栏与用例 | `maa_api/agent/preconditions.py`、`maa_api/agent/policy.py`、`tests/agent/test_preconditions.py`（21 个测试函数 / 31 个用例） | 完成（已复核，含负向对照） |
| W5 文档 | `docs/11`（§6/§3.2/§4.1/§8.4）、`docs/12` M32 小节、`docs/13` §8.47、`docs/README.md`（M32 行与知识库行） | 完成（含验收数字） |
| 验收 | 后端全量、四处生成物、文案基线、前端三门禁、五个冒烟 | 完成 |

## 实测结论

1. **现场证据**（用户会话 `cbbc4bd1-88ff-4aa4-9d44-7628547a823c`）：`submit_pipeline [{"name":"OperBox"}]` 在游戏未启动时提交，内核重试 3 次（`max_retries: 3` / `retry_delay: 30`），201.27 秒后 `CORE_COMMAND_FAILED`，现场截图是模拟器桌面；模型看了截图才补 `StartUp` 重跑。失败链路本身没有错，缺的是**提交前的知识**。
2. **内核依据**（MaaCore v6.18.0）：`StartUp`/`Fight`/`Recruit`/`Infrast`/`Mall`/`Award`/`Depot`/`OperBox` 的首步都是主界面锚点 `SwitchTheme@ToggleSettingsMenu.png`（`roi=[227,327,159,152]`），`Roguelike`（`tasks/Roguelike/base.json`）同样；**只有 `StartUp` 会拉起客户端**（`StartUpBegin` → `StartUpThemes` → `GameStart`）。`CloseDown` 不在 `tasks.json`（接口级任务，直接关进程）；`Reclamation` 入口在 `tasks/RA/*.json`，从主界面导航进入。
3. **判定集合现算**：`GAME_RUNNING_TASK_TYPES = frozenset(TASK_MODELS) - {"StartUp", "CloseDown"}`，实测 11 个（`Award`、`Copilot`、`Custom`、`Depot`、`Fight`、`Infrast`、`Mall`、`OperBox`、`Reclamation`、`Recruit`、`Roguelike`）。
4. **提醒不拦**：`submit_pipeline` 的 `warnings` 只在「含游戏内任务且无 `StartUp`」时出现，`wait_pipeline` 的 `hint` 只在 `CORE_COMMAND_FAILED` 时出现，策略层只在**确实要确认**时把同一条警告追加到 `reasons`——是否需要确认的判定与用户确认过的内容都不变。
5. **知识库契约同步**：条目 ID 与 front matter 登记在 M32 规格 §4.6，`_spec_blocks()` 从三份规格扩到四份，条目数 32 → 33；知识四文件 81 passed。

## 验收结果

主 agent 在所有写者停止后独占复跑（2026-10-10）。

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `.venv/bin/python -m pytest -o addopts= -q` | **2 failed / 3568 passed / 5 skipped**（200.13s）；两条失败是 M24 起已知的真资源用例 `tests/services/test_activity_index.py`；本轮新增 31 个用例（`tests/agent/test_preconditions.py`） |
| 护栏负向对照 | 临时把 `missing_start_up_warning` 改成恒返回 `None` | `tests/agent/test_preconditions.py` **18 failed / 13 passed**；改回后 **31 passed** |
| 生成物 | `dump_openapi.py --check`、`generate_api_guide.py --check`、`generate_task_types_fixture.py --check`、`cd web && pnpm gen:api:check` | 四处全 `exit=0`（无生成物变化：工具与提示词不进 OpenAPI） |
| 文案基线 | `copy_inventory.py --check` → `--write-baseline` → `--check` | 先报 14 项上升（工具描述、系统提示词、知识条目与 docs 新增文案），重设为 **45 项** 后「通过：无回潮，0 项较基线下降」 |
| 前端 | `pnpm typecheck` / `pnpm test --run` / `pnpm build` | 退出码 0 / **72 文件 732 项全绿** / 退出码 0（`[sw_harness] SERVICE WORKER OK`）；本轮前端零改动 |
| 冒烟 | `agent_smoke.py`、`frontend_smoke.py`、`account_smoke.py`、`copilot_research_smoke.py`、`prts_smoke.py --m28` | 五个末行均 `SMOKE OK`（`exit=0`） |

同步修掉的既有断言（契约变化的必然结果）：`tests/agent/test_pipeline_tools.py` 的 `wait_pipeline` 描述、`tests/agent/test_registry_policy.py` 两处消耗理由 fixture（各补一条 `StartUp`）、`tests/api/test_agent_knowledge_router.py` 的登记表副本（32 → 33 条）。
