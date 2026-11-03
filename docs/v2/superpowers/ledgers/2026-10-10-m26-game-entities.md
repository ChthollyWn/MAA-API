# M26 进度台账

计划：`docs/superpowers/plans/2026-10-10-m26-game-entities.md`
规格：`docs/superpowers/specs/2026-10-09-m25-prts-knowledge.md` §6
分支：`m26-game-entities`（自 `refactor/v2`），完成后 tag `v2-m26`

## 任务状态

| 任务 | 状态 | 产物 |
|---|---|---|
| T1 夹具 | 完成 | `tests/fixtures/penguin/*`（含 README）、`tests/fixtures/prts/page_{silverash,surtr,kroos,originium_slug,patriot,item_30013,item_30033}.json`、`scripts/fetch_m26_fixtures.py` |
| T2 干员解析 | 完成 | `prts_parse/{costs,operator}.py`，56 例 |
| T3 敌人解析 | 完成 | `prts_parse/enemy.py`（`merge_levels` 链式补齐），30 例 |
| T4 材料解析 | 完成 | `prts_parse/item.py`，19 例 |
| T5 企鹅物流客户端与配置 | 完成 | `external/penguin.py`、`SOURCES["penguin"]`、`ExternalClient.get_json(host=)`、三个配置键，26 例 |
| T6 掉率领域层 | 完成 | `services/game/drops.py`（`DropRow`/`DropService`），34 例 |
| T7 四个工具与 get_stage 掉率 | 完成 | `tools/game.py` 四个新工具（工具总数 52 → 56）、`stages.py` 的 drops 组 |
| T8 前端与文档 | 完成 | `format.ts` 四个中文名；docs/13 §8.41、docs/12 M26、docs/11、docs/15、docs/README |
| T9 冒烟与验收 | 完成 | `scripts/prts_smoke.py --m26` 末行 `SMOKE OK` |

## 实测结论（T1，2026-10-10）

见计划「实测确认」表。要点：

- 干员页 `{{CharinfoV2}}`（`稀有度` 从 0 数）、`{{属性}}`、`{{天赋列表3}}`、`{{潜能提升}}`、`{{技能}}/{{技能2}}/{{技能3}}`、`{{精英化材料}}`、`{{技能升级材料}}`、`{{模组}}`；材料消耗 `{{材料消耗|龙门币|3w}}`，**模组材料实测写中文「8万」**。
- 敌人页 `{{敌人信息/common2}}` + 每级 `{{敌人信息/levelcontent}}`，级别 1 以上只写差异；实测只有 0/1 两级；级别级没有 `攻击范围`，只有 `攻击范围半径`。
- 材料页 `{{道具信息}}` + `{{道具配方/加工站}}`。
- 企鹅物流四个端点**都在 `/PenguinStats/api/v2` 前缀下**（裸路径返回 200 的 HTML）；默认 matrix 只含当前开放关卡（8,087 行），`show_closed_zones=true` 含已关闭（12,522 行）；`_perm`/`_rep` 与基础 id 同时存在且 `code` 相同；当前数据里没有 `times=0` 的行。

## Rulings I made

1. 掉率的「关卡是否开放」用企鹅物流自己的两档 matrix 实现（默认 vs `show_closed_zones=true`），不引入未在白名单里的 `/zones`。
2. 攻击范围用本机 `battle_data.json` 的 `ranges` 渲染成文字网格（`#`/`@`/`.`），标 `source=resource`。
3. `EXTERNAL_RESPONSE_INVALID` 与断网、限流、熔断、来源关闭并列，都算「外部来源不可用」→ 走降级（规格 §3.5 把「解析失败」列在同一栏）。落在 `operators.py`、`items.py`、`stages.py::_drops_group`。
4. 干员名字解析命中非干员页时**继续走搜索**，不能拿敌人页当干员页解析（否则返回字段全空的假记录）；搜索也没命中就报 `GAME_DATA_NOT_FOUND`。
5. PRTS 不可用时，`stats` 的精英阶段面板留空但**攻击范围仍从本机给出来**（规格 §6.3：「本机能给 basic 的一部分与攻击范围」）。
6. 材料本机兜底（规格 §3.5）用 `item_index.json` 的 `formula` + 本机 `stages.json` 的 `dropInfos`：给名字、itemId、稀有度、描述、用途、配方原料、哪些常驻关掉；龙门币与副产物如实留空进 `unavailable`。
7. `get_stage` 的 `drops` 组在 M26 变成「PRTS 定性描述 + 企鹅物流掉率」，掉率取不到只降级这一段（`rates_unavailable`），不影响其它字段。
8. 掉率行的样本阈值：`times < 100` 标 `small_sample` 并排在样本足够的行之后；`times = 0` 剔除；`sanity_per_item = apCost ÷ rate`，`rate = 0` 时给 `None`（除零保护）。
9. `sources.penguin_host` 失败一次后改试另一个主机；回退只对站点级故障（不可用 / 结构不符），限流与来源关闭不回退；缓存键不含主机（两个主机内容一致）。

## 主 agent 亲自复跑的验收

- 后端 `.venv/bin/python -m pytest -o addopts= -q` → **2 failed, 2645 passed, 5 skipped**；两条失败是 M24 起就存在的真资源用例（`tests/services/test_activity_index.py`，资源包当期活动「昨日海」名字无期数 → M24 推断给 `none`），证据见 M25 台账与 docs/13 §8.39 五、④。
- `scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check` 退出码 0（先重新生成；本里程碑不动 REST，快照只多四条工具）。
- 前端 `pnpm typecheck && pnpm test --run && pnpm build` 退出码 0（61 文件 / 638 项）；`scripts/agent_smoke.py`、`scripts/frontend_smoke.py` 末行 `SMOKE OK`；`scripts/copy_inventory.py --write-baseline` 后 `--check` 通过。
- 真网络 `scripts/prts_smoke.py --m26` 末行 `SMOKE OK`：银灰 6★/三技能带专精/两个模组、源石虫两级 11 项抗性、固源岩组配方与掉率前 5、掉率表 158 行首位样本 335,979。

## 实施中修掉的缺陷（记录，供后续里程碑参考）

1. 我自己的补丁一度把 `maa_api/agent/tools/game.py` 的 `get_prts_page` 函数头吃掉，导致整模块 `SyntaxError`（并行子任务发现并报告，已当场修好）。教训：批量改写工具文件后要跑 `build_registry()` 而不只是 `ast.parse`。
2. 企鹅物流的 `stages`/`items`/`period` 三条路径最初只写了裸路径，真网络会拿到 200 的 HTML；已改为带 `/PenguinStats/api/v2` 前缀，并把这条实测写进 docs/13 §8.41（附录 D 的表格是旧的）。
3. M25 的 `test_get_stage_...` 用例在 M26 后回落到真网络的 `default_drop_service()`；已改成注入掉率替身，单测不再打网。
4. 材料本机兜底（规格 §3.5 明写、初版漏做）已补实现，新增 `tests/services/test_item_local_fallback.py`。
