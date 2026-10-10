# M35 · 练度未知不冒充事实（`NULL` ＝ 未识别） · 进度

- 分支：`m35-unknown-training`（自 `refactor/v2`，基点 `41a5732` = tag `v2-m34`），完成后 tag `v2-m35`
- 规格：`docs/superpowers/specs/2026-10-11-m35-unknown-training.md`
- 计划：`docs/superpowers/plans/2026-10-11-m35-unknown-training.md`
- 来源：用户报告（2026-10-11）「处理当前干员识别任务，无法识别到干员具体练度的问题」；用户裁决（ask_user_question）：**标为前置未知，不虚报增量**，顺序「先收尾 M35，再做 M36 扫描器（名单限精二以上 + 养成计划目标）」

## 任务状态

| 工作项 | 写范围 | 状态 |
| --- | --- | --- |
| W1 模型与迁移 | `maa_api/db/models.py`（六列可空、`empty_masteries()` 不再是列默认值）、`maa_api/db/migrations/versions/0018_unknown_training.py`、`tests/db/test_unknown_training_migration.py`（新，3 条）、`tests/db/test_initial_migration.py` | 完成（主 agent；含离线 `--sql` 路径） |
| W2 wire 与读写 | `maa_api/services/account/rules.py`（`UNKNOWN_TRAINING_FIELDS`、`mastery_wire`/`module_wire`/`box_row_wire` 传 `None`）、`maa_api/services/account/box.py`（`patch_operator` 已知才判约束）、`tests/services/test_account_box_rules.py` | 完成（主 agent） |
| W3 识别路径 | `maa_api/services/account/capture.py`（own-only 行留 `NULL`）、`tests/services/test_account_capture.py`、`tests/services/test_account_capture_replay.py` | 完成（主 agent） |
| W4 规划层 | `maa_api/services/account/planner.py`（`UpgradePlan.unknowns`、`_unknown_entry`/`_order_unknowns`、`_unknown_current`、`_levelup_increment` 未知即跳过、无记录不再当「刚获得」）、`tests/services/test_upgrade_planner.py` | 完成（主 agent；82 passed） |
| W5 API 契约 | `maa_api/api/routers/account.py`（`AccountOperatorRow` 六字段可空、PATCH 写明 `null` ＝ 不改）、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/05` | 完成 |
| W6 前端 | `web/src/features/account/`（`format.ts`、`OperatorsTab.tsx`、`OperatorEditorSheet.tsx`、`constraints.ts`、`api.ts`、`OperatorFilterBar.tsx`、`UpgradePlansSection.tsx` + 四个测试文件） | 完成（teammate `web-unknown-training`；主 agent 逐文件复核并复跑门禁） |
| W7 Agent 口径 | `maa_api/agent/tools/account.py` 三个工具描述、`maa_api/agent/knowledge/builtin/playbook-promotion-planning.md`、`task-preconditions.md` | 完成（主 agent；149 passed） |
| W8 文档与台账 | `docs/04` §5.18/§5.22、`docs/05`、`docs/13` §8.50、`docs/12` M35、`docs/README.md:122`、本文件、`docs/superpowers/ledgers/2026-10-11-m35-unknown-training.md` | 完成 |
| 验收 | 后端全量 / 四处生成物 / 文案基线 / 前端三件套 / 五个冒烟 / 负向对照 | 完成（见下） |

## Rulings I made

1. **`NULL` ＝ 未识别，而不是新增一列「识别过哪些字段」**：六列可空 + `field_sources` 作唯一台账，一个字段要么有值、要么 `NULL`；两处真相必然漂移，且每个读侧都要判两处，漏一处就退回虚报。
2. **不用哨兵值**（`level=-1`、`skill_level=0`）：类型与 `ge/le` 校验、前端渲染、Agent 提示词都要认识哨兵，JSON 列也没有自然的哨兵。
3. **不删技能三列**：删了会连带丢掉 M27 起就有的手动编辑与 Agent 补录——用户补录过的事实比识别能力更值钱。
4. **`PATCH` 的 `null` 与键缺失同义（不改）**：本轮不做「写 `null` 清空/退回未识别」，那是独立能力，需要自己的确认与差异语义，混进补录载荷会让「补录」与「抹掉」共用一条路。
5. **跨字段约束只在已知时判**：`elite` / `skill_level` 为 `NULL` 时不拦「专精要精二、技能 7 级」，否则用户根本没法补录；已知值照旧拦（精一填专精 422、技能未满填专精 422、精一填模组 422）。显式降精英化仍清空专精与模组。
6. **星级缺失仍按 1★ 兜底、但不再暗示「刚获得」**：那是材料表本身的保守取值（宁少不多），与「练度未知」是两回事；文案改口径，`assumed_current` 只在确实按默认阶段计算时才为真。
7. **迁移回填改成纯 SQL**：`--sql` 离线渲染拿不到查询结果，逐行 Python `fetchall()` 直接炸；六条 `UPDATE` + `json_extract(CASE WHEN json_valid(col) THEN col ELSE '{}' END, …)` 在线与离线一致，坏 JSON 行不报错。
8. **未知维度不计增量、不按默认值兜底**（用户裁决原话「标为前置未知，不虚报增量」）：规划结果里给 `unknowns: [{operator, field, reason}]` 与白话 notes，让 Agent/页面把缺口讲清楚，而不是静默按精零 1 级算材料。
9. **计划里的「待确认」项落进 `progress` JSON，不加列**（规格 §4.7 末句）：`UpgradePlanService.start()` 把启动那一次算出的 `unknowns` 与 `stages` / `total_sanity` 一起写进 `progress`，`preview()` 顶层返回同名键。不新开 `0019` 迁移、也不在详情页现算——现算会随仓库与识别状态漂移，和用户当初确认的那份不一致。旧计划行没有这个键，前端按空数组处理、那一块不渲染。
10. **Agent 确认卡片不做 `unknowns` 的结构化渲染**（teammate 建议过，本轮拒）：规格只要求「计划详情里能看到」，而卡片本来就渲染 `preview.notes`（`ConfirmationCard.tsx:333`），那句「…未识别，未计入材料增量…」不会丢；改成「干员 · 字段 · 理由」三列要动 M31 的卡片与其用例，本轮不扩（记在 §8.50 五、8）。

## 实施中修掉的问题

1. **`0018` 迁移的离线渲染**：初版逐行 SELECT + `json.loads`，`alembic upgrade head --sql` 时 `fetchall()` 无结果直接炸；改成纯 SQL UPDATE，并用 `json_valid` 兜坏 JSON——SQLite 的 `OR` **不短路**（实测 3.49.1），写成 `col IS NULL OR json_extract(...)` 仍会 `malformed JSON`。
2. **M33 的 own-only 行**：真机有 3 名已拥有但没有练度详情的干员，旧代码给它们落 0/0/0；现在三列保持 `NULL`，识别也永不写技能三列。
3. **旧测试按新语义改写**（6 条：`test_account_box.py` 1、`test_account_capture.py` 1、`test_account_repositories.py` 2、`test_account_router.py` 2）：建行默认值、识别结果、wire 形状的断言都要从 `0`/`1`/`{}` 改成 `None`；`{"masteries": {"1": 3}}` 从「拒」改成「未知时放行」，另立 `test_patch_masteries_only_judges_prerequisites_when_known` 覆盖「未知放行 / 已知违法仍拒 / 清空后已知即拒 / 满技能放行」四步。
4. **规划层旧文案与 fixture**：`test_upgrade_planner.py` 的默认 box 从空改成「已识别的甲、乙」两行（1★，不掺升级经验），「没有记录」的用例自己传空 box；三条 depot 用例补显式已识别行，否则需求为空、断言 KeyError。
5. **并发跑测试的抖动**：`tests/agent/test_incidents.py::test_incident_allowed_action_still_uses_confirmation_service` 在全量并发跑时红、单跑 15 passed；最终全量复跑要单独复核，不当产品缺陷。
6. **teammate 静默**：`legacy-test-adapt` 改了 6 行后 inactive 且无报告，6 条红仍在；主 agent 接手改完（66 passed）。
7. **W6 首轮交付留下三处红与两个缺口**：`pnpm typecheck` 报 `OperatorEditorSheet.tsx` 三处 `TS2304: Cannot find name 'UNKNOWN_TEXT'`（用了没 import）、`test-utils.tsx(167,58): TS18047: 'row.elite' is possibly 'null'`，`pnpm vitest run` 7 条红全在 `OperatorEditorSheet.test.tsx`；规格 §4.9 末句的筛选说明文案与组件层用例都没做。派回 teammate 后补齐（筛选说明落在 `OperatorFilterBar.tsx:108`，补 2 + 4 条用例）。
8. **规格 §4.7 末句一开始没人认领**：`UpgradePlansSection.tsx` 既不渲染 `progress.unknowns` 也不渲染 `notes`。后端由主 agent 补（`plans.py` 的 `start()` 落 `progress.unknowns`、`preview()` 顶层同名键，不新增列、不重算），前端由 teammate 补「待确认」块（`InlineAlert tone="warning"`，`planUnknowns()` / `planUnknownNotes()` 两个纯函数，旧行按空数组不渲染）。两者都补了用例（后端 1 条服务层用例 + 前端 5 条）。

## 验收（主 agent 在所有写者停止后独占复跑，工作树即最终提交前状态）

- **后端全量**：`.venv/bin/python -m pytest -o addopts= -q` → `2 failed, 3599 passed, 5 skipped, 23 warnings in 219.00s`。两条红是既有长期已知红 `tests/services/test_activity_index.py::test_real_resource_pack_reports_the_current_activity_and_its_stages` 与 `::test_real_resource_pack_disambiguates_a_repeated_code`（真资源包「昨日海」没有期数 → `confidence='none'`，见 docs/13 §8.39 五、④），本轮无新增红。
- **四处生成物**：`scripts/dump_openapi.py --check` → `OpenAPI snapshot is current: web/openapi.json`；`scripts/generate_api_guide.py --check` → `API guide generated sections are current: docs/14-开放API接入指南.md`；`web` 内 `pnpm gen:api:check` → `OpenAPI types are up to date.`；`scripts/copy_inventory.py --write-baseline` 后 `--check` 通过。
- **前端**：`pnpm typecheck` 退出码 0；`pnpm vitest run` → `Test Files 73 passed (73)` / `Tests 755 passed (755)`（M35 前 744）；`pnpm build` → 末行 `[sw_harness] SERVICE WORKER OK`。
- **冒烟**：`scripts/agent_smoke.py`（`[ok] 临时 SQLite 隔离；仓库 DB/config 指纹未改变（409600 bytes）`）、`scripts/account_smoke.py`、`scripts/copilot_research_smoke.py`、`scripts/prts_smoke.py --m28`、`scripts/frontend_smoke.py` 全部末行 `SMOKE OK`。
- **负向对照**：把 `box_row_wire` 的六列兜底改回 M35 之前 → `tests/services/test_account_box.py:170` 与 `tests/api/test_account_router.py:283` 必红；复原后（`rules.py` 的 `md5` 与实验前一致）同两条通过。
- **真机库副本**（不改 `resource/maa_api.db`）：`cp resource/maa_api.db /tmp/m35_realdb.db` 后 `upgrade` → `alembic_version = 0018_unknown_training`、技能三列非空 0 行、`elite/level/potential` 非空 463 行；`downgrade 0017_copilot_grant` → 六列恢复 `NOT NULL` 并填旧默认值（`pragma table_info` 三列 `notnull=1`）；再 `upgrade` 回 `0018` 得同样结果。服务重启后的实际迁移结果等用户核对。
