# M28 · 卡池、基建排班、集成战略与剧情 · 实施计划

规格见 [specs/2026-10-09-m25-prts-knowledge.md](../specs/2026-10-09-m25-prts-knowledge.md) §8（下文「§」都指该文件）。行为以规格为准；规格没覆盖到的实现细节由实现者决定并写进报告，主 agent 抄进台账。

## 全局约束

- 工作分支 `m28-gacha-infrast-roguelike`（已从 `refactor/v2` 建立）。直接在当前检出上工作，不建 worktree，不 push，不合入 `dev`。
- 后端测试：`.venv/bin/python -m pytest -q`；前端：`cd web && pnpm typecheck && pnpm test --run && pnpm build`。
- 改了后端接口后：`.venv/bin/python scripts/dump_openapi.py`、`cd web && pnpm gen:api`、`.venv/bin/python scripts/generate_api_guide.py`。
- 先写失败测试确认 RED，再实现 GREEN；长输出重定向到文件，只读尾部与退出码。
- **实现者不要 `git commit` / `git add` / `git stash`**；`maa_api/main.py`、`maa_api/agent/tools/__init__.py`、`maa_api/agent/knowledge/__init__.py`、`maa_api/agent/runtime.py` 由主 agent 统一改。
- 不引入新依赖。**单测不打网**（真网络只在 `scripts/prts_smoke.py --m28` 里用）。

## 分工与写范围

| 工作流 | 写范围 |
|---|---|
| A 卡池 + 剧情 | `services/game/gacha.py`、`services/game/story.py`、`services/prts_parse/story.py`、`agent/tools/game.py`（追加两个工具）、`tests/services/test_game_gacha.py`、`tests/services/test_game_story.py`、`tests/agent/test_game_tools.py`（追加用例）（子任务 A） |
| B 基建（含 D3/D4/Q44/Q45） | `services/game/building.py`、`services/prts_parse/building.py`、`services/game/operators.py`（加 `building` 段）、`services/resource_service.py`（D4 校验）、`services/pipeline_runner.py`（D3 路径 + Q45 period 选班）、`agent/tools/estate.py`（新文件，四条工具）、`tests/services/test_game_building.py`、`tests/services/test_infrast_*`（子任务 B） |
| C 集成战略 + D5 + 前端 | `services/game/roguelike.py`、`domain/task.py` 的 `RoguelikeInput`（D5）与 `InfrastInput.facility`（D4 的设施枚举）、`agent/tools/roguelike.py`（新文件）、`web/src/features/tasks/*`（任务编辑器同步）、相关测试（子任务 C） |
| 主 agent | 注入（当期卡池 3 行）、提示词追加、`tools/__init__.py` 注册、工具中文名两边、测试门禁（工具目录 / 任务类型）、docs/13 §8.43、docs/12 M28、冒烟 `scripts/prts_smoke.py --m28`、验收与合并 |

## 冻结的接口

- 卡池：`GachaService.pools(*, status="current"|"recent", limit=5) -> dict`；返回 `{"query", "pools": [{"name", "start_at", "end_at", "up_six": [...], "up_five": [...], "source", "page"}], "pools_total", 信封}`。SMW：`寻访开启时间cn::+`，printouts `寻访名cn`/`寻访开启时间cn`/`寻访关闭时间cn`/`出率提升干员`；UP 干员星级用本机 `battle_data.json` 区分并标 `source=resource`；仅国服注入。
- 剧情：`StoryService.search(query, limit) -> dict`、`StoryService.get(title, *, offset=0, max_chars=6000) -> dict`；正文来自 `{{剧情模拟器|文本数据=…}}`，`[name="杜宾"] 台词` → `杜宾：台词`，去演出指令，按 `max_chars` 分页（2000–8000）。
- 基建：`BuildingService.skills(*, operator=None, room=None, skill=None) -> dict`、`candidates(*, layout, shifts, rooms=None) -> dict`、`plans() -> dict`。
- 肉鸽：`RoguelikeService.info(*, theme=None, topic="overview", name=None) -> dict`。
- `update_user_profile(section, content, mode)` 写自定义条目 `agent-profile`（标题「Agent 记录的用户档案」），按 `## <section>` 分节，每次确认并展示差异（沿用 M27 的 `PolicyDecision.extra_payload` 机制）。

## D3 / D4 / D5 / Q44 / Q45 的落点

- **D3**：`mode=10000` 的 Infrast 任务，`filename` 由执行器解析成 custom 层下的绝对路径再交给内核（与 Copilot 快照同样做法）；补测试。
- **D4**：校验对齐上游——放行 `duration`、`groups`、`candidates`、`use_operator_groups`、`drones.rule`；补上「`rooms` 必填、`product` 只许 6 个值、`drones.index` 1–5、`period` 为 `HH:MM`」；`InfrastInput.facility` 补 Processing、Training。
- **D5**：`RoguelikeInput` 按上游 v6.18.0 补齐主题（JieGarden、BlackFlow）、模式与一批参数；校验矩阵对齐 `RoguelikeConfig.cpp`（`start_with_elite_two` 只许 `mode=4`；Phantom 允许 `difficulty`）；前端任务编辑器同步。
- **Q44**：首次由 Agent 用 `ask_user` 问基建布局与班次，写进用户档案条目 `agent-profile`，之后直接读。
- **Q45**：`mode=10000` 且未给 `plan_index` 时，执行器读方案文件、按本机时区当前时间选第一个 `period` 覆盖现在的班次；都不覆盖时报 `TASK_PARAM_INVALID`；给了 `plan_index` 保持原语义。

## 验收（规格 §8.6，主 agent 在最终提交上亲自跑）

1. `.venv/bin/python -m pytest -q`；
2. `scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`；
3. `cd web && pnpm typecheck && pnpm test --run && pnpm build`；`scripts/frontend_smoke.py`、`scripts/agent_smoke.py` 末行 `SMOKE OK`；`scripts/copy_inventory.py --check`；
4. 真网络 `scripts/prts_smoke.py --m28`：当期卡池与 PRTS 一致；`get_building_skills(operator="银灰")` 有描述；`get_roguelike_info(topic="squads")` 返回最新主题的分队；`get_story(title="1-1 孤岛/BEG")` 分页正常；
5. 基建自定义方案的真机执行作为补充证据；
6. `git merge --no-ff` 回 `refactor/v2`，打 tag `v2-m28`；台账抄到 `docs/superpowers/ledgers/`。

## 不做

自动替用户抽卡；基建排班的全局最优求解（由模型编排，工具只提供候选与校验）；剧情整本导出。
