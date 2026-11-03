# M26 · 干员、敌人、材料与掉落率 · 实施计划

规格见 [specs/2026-10-09-m25-prts-knowledge.md](../specs/2026-10-09-m25-prts-knowledge.md) §6（下文「§」都指该文件）。行为以规格为准；规格没覆盖到的实现细节由实现者决定并写进报告，主 agent 抄进台账。

## 全局约束

- 工作分支 `m26-game-entities`（已从 `refactor/v2` 建立）。直接在当前检出上工作，不建 worktree，不 push，不合入 `dev`。
- 后端测试：`.venv/bin/python -m pytest -q`；前端：`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`。
- 改了后端接口或工具目录后：`.venv/bin/python scripts/dump_openapi.py`、`cd web && pnpm gen:api`、`.venv/bin/python scripts/generate_api_guide.py`。
- 先写失败测试确认 RED，再实现 GREEN；长输出重定向到文件，只读尾部和退出码。
- **实现者不要 `git commit` / `git add` / `git stash`**：并行任务会互相干扰，提交由主 agent 统一做。
- 不引入新依赖。**单测不打网**：全部用 `tests/fixtures/{prts,penguin}/` 夹具与 `httpx.MockTransport`。

任务依赖：T1 ∥ T5；T2/T3/T4 依赖 T1；T6 依赖 T2–T5；T7 依赖 T5；T8 依赖 T6；T9 依赖全部。

## 实测确认（T1，2026-10-10 主 agent 抓取）

| 结论 | 证据 |
|---|---|
| 干员页：`{{CharinfoV2}}`（`干员id`=`char_172_svrash`、`稀有度=5` 从 0 数、`职业`、`分支`、`特性`、`位置`、`标签`、`情报编号`）、`{{干员获得方式}}`（`获得方式`、`上线时间`）、`{{属性}}`（`再部署`、`部署费用=18→20`、`阻挡数`、`攻击速度`、`精英N_满级[_生命上限/攻击/防御/法术抗性]`、`精英0_1级_*`、`信赖加成_*`、`潜能`、`潜能类型`）、`{{干员攻击范围}}`（`精英0范围=2-3` 这种编号）、`{{天赋列表3}}`（`天赋N/条件/效果` + `潜能增强_*`）、`{{潜能提升}}`（`潜能2..6`）、`{{技能}}`/`{{技能2}}`/`{{技能3}}`（`技能名`、`技能类型1`、`技能类型2`、`技能{1..7}初始/消耗/持续/描述`、`技能专精{1..3}*`）、`{{精英化材料}}`（`精1`/`精2`）、`{{技能升级材料}}`（键 `2..7`、`一8..一10`、`二8..二10`、`三8..三10`）、`{{模组}}`（`名称`、`类型图标=lor-x`、`类型=LOR-X`、`生命/生命2/生命3`、`攻击/攻击2/攻击3`、`特性追加`、`特性`、`天赋2/3`、`潜能天赋2/3`、`材料消耗*`） | `银灰` 15,756 字 wikitext |
| 材料消耗写作 `{{材料消耗|龙门币|3w}}`：`w` 后缀是「万」（`3w`=30000） | 同上 |
| 敌人页：`{{敌人信息/common2}}`（`id`、`名称`、`index`、`地位级别`、`描述`、`伤害类型`、`攻击方式`、`行动方式`、`种类`、`能力`、`登场活动`）+ 每级一个 `{{敌人信息/levelcontent}}`（`index`、`最大生命值`、`攻击力`、`防御力`、`法术抗性`、`移动速度`、`攻击速度`、`攻击间隔`、`重量等级`、`损伤抵抗`、`元素抗性`、`基础嘲讽等级`、十二种 `XX抗性`、`技能N/技能N效果/技能N初始/技能N消耗`、`天赋`）；**级别 1 以上只写与级别 0 不同的字段** | `源石虫` 668 字、`爱国者` 1,807 字 |
| **实测：源石虫与爱国者都只有级别 0 与级别 1 两级**（规格 §6.8 的「三级数值」按「多级数值」理解，冒烟按实际级数断言） | 两份敌人夹具的 `==级别N==` 只有 0/1 |
| 材料页：`{{道具信息}}`（`名称`、`itemId=30013`、`iconId`、`描述`、`用途`、`获得方式`、`稀有度`、`分类`）+ `{{道具配方/加工站}}`（`产物`、`产物数量`、`龙门币消耗`、`心情消耗`、`加工站等级需求`、`副产物总概率`、`原料N`/`原料N数量`、`副产物N`/`副产物N掉率`） | `固源岩组`、`聚酸酯组` |
| 企鹅物流 `/stages`：1,620 条，`stageId`、`code`（显示代号，`main_08-13` 的 code 是 `R8-11`）、`apCost`、`zoneId`、`stageType`、`existence.<SERVER>.{exist,openTime,closeTime}` | 实测 |
| 企鹅物流 `/items`：155 条，`itemId`、`name`、`itemType`、`rarity` | 实测 |
| 企鹅物流 `/matrix`：`{stageId, itemId, times, quantity, stdDev, start, end}`；默认只返回**当前开放**的关卡（8,087 行），`show_closed_zones=true` 含已关闭（12,522 行）→ 正好是 `include_closed` 开关 | 两个变体实测对比 |
| `_perm`（常驻化）与 `_rep`（复刻）后缀的关卡在 `/stages` 里与基础 id **同时存在**且 `code` 相同（`a001_01` / `a001_01_perm` / `a001_01_rep` 都是 `GT-1`）；基础 id 的 `closeTime` 到期、`_perm` 仍开放 | 实测 |
| 当前 CN 的 1,620 条 `/stages` 里 `exist` 全为 true（`existence` 是「这个服有没有这关」，不是「现在开不开」） | 实测 |

## Task 1 · 夹具（规格 §6.2 T1、§4.9）

**产物**：`tests/fixtures/prts/{page_silverash,page_surtr,page_kroos,page_originium_slug,page_patriot,page_item_30013,page_item_30033}.json`、`tests/fixtures/penguin/{matrix_subset,matrix_closed_subset,stages_subset,items_subset,period_subset}.json`、`tests/fixtures/prts/README.md` 与 `tests/fixtures/penguin/README.md`（来源与抓取日期）、`scripts/fetch_m26_fixtures.py`。

- 干员：银灰（有模组、三个技能）、史尔特尔、一个 3★（克洛丝）。
- 敌人：源石虫（两级、字段少）、爱国者（两级、技能与天赋多）。
- 材料：固源岩组（`itemId=30013`）、聚酸酯组（`30033`）。
- 企鹅物流：matrix 裁成「固源岩组/聚酸酯组的全部行 + 少量 `_perm`/`_rep` 行 + 一条 `times<100` + 一条 `times=0`」，stages 裁成被用到的关卡，items 裁成相关条目，period 取前 3 条。

## Task 2 · 干员解析（规格 §6.3）

**产物**：`maa_api/services/prts_parse/operator.py`、`tests/services/test_prts_operator_parse.py`。

- 纯函数 `parse_operator_page(content, *, title) -> OperatorPage`；子结构：`OperatorSkill`（名称、触发/回复类型、各级初始/消耗/持续/描述、备注）、`OperatorTalent`（名称、条件、效果）、`OperatorModule`（名称、类型代号、各级属性、特性追加、天赋变化）、`OperatorCosts`（精英化、技能升级、专精、模组）。
- 材料消耗解析：`{{材料消耗|名称|数量}}`，数量带 `w` 后缀按万处理，`24w`、`3w`、纯数字都要对。
- 字段名与取值以 T1 实测为准；缺字段一律 `None`/空元组，不抛异常。

## Task 3 · 敌人解析（规格 §6.4）

**产物**：`maa_api/services/prts_parse/enemy.py`、`tests/services/test_prts_enemy_parse.py`。

- `parse_enemy_page(content, *, title) -> EnemyPage`（基础字段 + `levels`）。
- `merge_levels(page) -> tuple[EnemyLevel, ...]`：级别 1 以上用级别 0 补齐（PRTS 只写差异）。
- 异常抗性十二项与技能/天赋按实测键名解析。

## Task 4 · 材料解析（规格 §6.5）

**产物**：`maa_api/services/prts_parse/item.py`、`tests/services/test_prts_item_parse.py`。

- `parse_item_page(content, *, title) -> ItemPage`（基础 + `CraftRecipe`）。
- 配方：原料、数量、龙门币、心情、加工站等级、副产物与概率。

## Task 5 · 企鹅物流客户端与配置（规格 §6.6、附录 B）

**产物**：`maa_api/services/external/penguin.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/services/test_penguin_client.py`、`tests/test_settings.py`（env 名表）。

- `PenguinClient`：`matrix(server, include_closed, stage_filter, item_filter)`、`stages(server)`、`items()`、`period()`；整表缓存 `static`；按单个物品/关卡查询时从整表筛，不额外请求。
- 主机：`sources.penguin_host`（默认 `.io`）；请求失败一次后自动改试另一个（`ExternalClient.get_json` 需要支持显式选主机，`host` 必须在该来源的 `hosts` 里）。
- 配置：`sources.penguin_enabled`(true)、`sources.penguin_host`(.io/.cn)、`sources.penguin_rate_per_minute`(10, 1–60)，登记 `SETTING_KEYS`、`Settings` 子模型、`settings_schema`（分组「外部数据源」）与 `config.template.yaml`。
- `SourceSpec` 登记 `penguin`：主机两个、路径 `/PenguinStats/api/v2/result/matrix`、`/stages`、`/items`、`/period`，参数白名单 `server`、`stageFilter`、`itemFilter`、`show_closed_zones`，响应上限 4 MB、读取 20 s、每分钟 10、并发 2。

## Task 6 · 掉率领域层（规格 §6.5、§6.6）

**产物**：`maa_api/services/game/drops.py`、`tests/services/test_game_drops.py`。

- `DropService.item_rates(item, limit, include_closed, server)` 与 `stage_rates(stage, limit, include_closed, server)`；`top_rates(item_name, limit)` 供 `get_item` 用。
- 每行：关卡代号（`/stages` 的 `code`）与 stageId、物品、掉率（`quantity/times`）、样本数、理智消耗（`apCost`）、单件期望理智（`apCost ÷ 掉率`）、统计区间（`start`/`end` 的本地日期）、是否开放。
- 规则：`times = 0` 剔除；`times < 100` 标「样本少」且不参与排序推荐；`_perm`/`_rep` 后缀按实测规则对回基础 id（基础 id 存在时用它，code 相同）。
- 来源信封注明 CC BY-NC 4.0。

## Task 7 · 四个工具与 `get_stage` 掉率（规格 §6.1、§6.3–§6.5）

**产物**：`maa_api/agent/tools/game.py`、`maa_api/services/game/{operators,enemies,items,stages}.py`、`tests/agent/test_game_tools.py`。

- 新增 `get_operator`、`get_enemy`、`get_item`、`get_drop_rates`（组 `game`、SAFE、只读）；`get_stage` 的 `drops` 组补掉率。
- 名字解析：干员走「本机 `battle_data.json` 精确匹配 → PRTS 重定向且属于 `分类:干员` → 搜索并按分类过滤」，多候选返回候选列表；敌人与材料同理（敌人无本地兜底）。
- 单结果 ≤ 16 KB（干员）/ ≤ 24 KB（其它）；长描述截断并标 `truncated`。

## Task 8 · 前端与文档

**产物**：`web/src/features/agent/format.ts` 与测试、`docs/13` §8.41、`docs/12` M26、`docs/11` §3.2、`docs/15` 出站域名。

- 前端：`TOOL_LABELS` 补「查干员」「查敌人」「查材料」「查掉落率」；来源标签支持企鹅物流（`penguin-stats.io` / `.cn` 已在白名单）。
- 文档：docs/13 新增 §8.41（裁决、实测与偏离、D 项修复、许可）；docs/12 M26 写全；docs/11 §3.2 补四个工具；docs/15 出站域名加 `penguin-stats.io` / `penguin-stats.cn`。

## Task 9 · 冒烟与验收（规格 §6.8）

**产物**：`scripts/prts_smoke.py --m26`。

- `get_operator("银灰")` 稀有度 6★、三个技能都带专精描述、带模组；
- `get_enemy("源石虫")` 有各级数值（实测两级）；
- `get_item("固源岩组")` 带配方与掉率前 5；
- `get_drop_rates(item="固原岩组")` 首位关卡样本数 ≥ 100。

## 主 agent 亲自跑的验收

1. `.venv/bin/python -m pytest -q`；
2. `scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`；
3. `cd web && pnpm typecheck && pnpm test --run && pnpm build`；`scripts/frontend_smoke.py`、`scripts/agent_smoke.py` 末行 `SMOKE OK`；`scripts/copy_inventory.py --check`；
4. `scripts/prts_smoke.py --m26`（真网络，补充证据）；
5. `git merge --no-ff` 回 `refactor/v2`，打 tag `v2-m26`；台账抄到 `docs/superpowers/ledgers/`。

## 不做

基建技能描述（M28）；干员档案与语音；干员头像。
