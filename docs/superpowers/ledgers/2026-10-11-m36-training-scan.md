# M36 · 干员详情练度扫描（技能等级、专精、模组从详情页读回） · 进度

- 分支：`m36-training-scan`（自 `refactor/v2`，基点 `f786193` = tag `v2-m35`），完成后 tag `v2-m36`
- 规格：`docs/superpowers/specs/2026-10-11-m36-training-scan.md`
- 计划：`docs/superpowers/plans/2026-10-11-m36-training-scan.md`
- 来源：用户 2026-10-11「可以开始开发m36了」（M35 收尾时定下的下一步：内核只读列表页，技能等级 / 专精 / 模组要自己开详情页读；名单限「精二以上 + 养成计划目标」）
- 真机：Redmi / Android 2560×1440，游戏停在「干员」列表页；验收服务 `127.0.0.1:8011`（仓库拷贝 `/tmp/m36_accept`，DB 为副本，`maa_api` 与 `resource/*` 都是仓库 symlink）

## 任务状态

| 工作项 | 写范围 | 状态 |
| --- | --- | --- |
| W1 节点定义与真机标定 | `maa_api/services/account/scan_tasks.py`（41 节点 + `Done`）、`scripts/probe_training_scan.py`、`tests/services/test_account_scan_image.py` | 完成（主 agent；真机标定见「实施中修掉的问题」1、4） |
| W2 写回口径 | `maa_api/services/account/box.py`（`apply_detail_scan`、`record_training_scan`）、`maa_api/services/account/rules.py`（`DETAIL_SCAN_FIELDS`）、`maa_api/db/repositories/account.py`（`RECOGNITION_KIND_TRAINING`）、`tests/services/test_account_detail_scan.py` | 完成（主 agent；10 例） |
| W3 扫描服务 | `maa_api/services/account/scan.py`（`TrainingScanService`）、`tests/services/test_account_training_scan.py` | 完成（主 agent；35 例） |
| W4 HTTP 与装配 | `maa_api/api/routers/account.py`（`POST`/`GET`/`DELETE /api/account/training-scan`）、`maa_api/main.py`、`tests/api/test_account_scan_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/04`、`docs/05` | 完成（12 例 + 四处生成物） |
| W5 Agent | `maa_api/agent/tools/account.py`（`scan_operator_training` / `cancel_training_scan`）、`maa_api/agent/policy.py`、`maa_api/agent/knowledge/builtin/playbook-promotion-planning.md`、`tests/agent/test_training_scan_tools.py` | 完成（12 例） |
| W6 前端 | `web/src/features/account/TrainingScanCard.tsx` + `.test.tsx`、`api.ts`、`OperatorsTab.tsx`、`test-utils.tsx`、`web/src/features/agent/format.ts`（+ 1 行 kind 显示） | 完成（主 agent 复跑 typecheck / vitest / build） |
| W7 真机验收 + 文档 + 台账 + 合并 | `/tmp/m36_accept/run_scan.py`、`docs/13` §8.51、`docs/12` M36、`docs/superpowers/specs/2026-10-11-m36-training-scan.md`（§2.3／§2.4／§4.1／§4.2／§5／§6／§7 按真机就地订正）、本文件、`docs/superpowers/ledgers/2026-10-11-m36-training-scan.md`、`docs/copy-audit/*`（棘轮重设） | 完成（见下） |
| 验收 | 后端全量 / 四处生成物 / 文案基线 / 前端三件套 / 两个冒烟 / 真机五轮 | 完成（见下） |

## Rulings I made（实施中定的口径）

1. **一个 `Custom` 任务会沿节点 `next` 走完整条链**（真机实测：只提交 `CardNameTopCol0`，`asst.log` 里 `first: [CardNameTopCol0]` 一路走到 `CardNameBottomCol5`）。所以规格 §4.1 的「一步一条流水线」改成「**一条链一次流水线**」：读屏一次提交链首，一次收完 16 格读数；每个干员的详情链同理。
2. **链尾必须有 `JustReturn` 的 `Done` 兜底**：节点读不出来时内核会重试到 `retry_times: 20`（≈30 s）再报 `TaskChainError`，而 app 的流水线不把 `TaskChainError` 当终态 → 只能耗到 90 s 步超时。每个节点的 `next` / `exceededNext` 都带 `Done`，服务层提交时再追加一次。
3. **读数只能走内核实时回调**：识别结果是 `SubTaskStart` / `SubTaskCompleted`（20001/20002），内核只把「带文案」的 `SubTaskExtraInfo`（20003）写 `log_entry` → `callbacks_for()` 拿不到。改成 `with self.tap() as tap:`（`client.on("CALLBACK", tap.handle)`，与 M29 `CopilotResultService.tap()` 同一条路），`callbacks_for` 只作兜底合并。
4. **详情链的节点顺序按「读不出来会截断链」排**：`DETAIL_NODE_KEYS = (SkillLevel, Elite2, Elite1, Elite0, ModuleCode, ModuleStage)`——只有解锁模组才有的 `ModuleStage` 排最后。
5. **模组编号内联 `OcrDetect + isAscii`**：内核 `NumberOcrReplace` 的替换表会把 `X` 变成 `0`、字母改坏；`LETTERS` 这个名字内核里不存在，引用它整条链提交失败（`append_task` 返回 0）。
6. **干员列表是横向翻页**：竖划一格都不动（`adb` 三组竖划与内核 `SwipeDown` 节点前后卡面区均值差 0.00–0.55），横向从右往左划才换屏（`adb` 62.64、内核节点 96.66）→ 节点改名 `SwipeNextPage`、照抄内核 `SlowlySwipeToTheRight` 的横向形状。
7. **同一个人一轮只扫一次**：翻屏一次约 5 列、一屏 8 列，必然会重叠；`_scan` 里按 `char_id` 去重（`done` 集合），否则同一批人被反复点开（真机实测 `scanned=11` 而名单只有 7 人）。
8. **详情页干员名不识别**：艺术字，裸 OCR 只回一个「画」（score 0.17）；身份一律以列表页卡名为准。
9. **卡名 OCR 的固定漏检如实汇报**：读不出来的格子跳过并计入 `skipped`，不按位置猜（猜错会写错人）。
10. **文案基线按棘轮重设**：本轮新增文案让 `copy_inventory --check` 报 7 类增量，按仓库既有做法 `--write-baseline` 后随本轮提交。

## 实施中修掉的问题（全部由真机暴露，按发现顺序）

1. **首跑「练度扫描：读卡名超过 90 秒还没结束（设备可能掉线或正忙）」**：内核 `asst.log` 显示 `cur_task: CardNameBottomCol5`、`cur_retry: 12, retry_times: 20`、`first: [CardNameTopCol0]`，20 次重试后 `TaskChainError`；库里那条 `task` 到手动 `DELETE` 才变 `cancelled`。修法见 Rulings 1、2。
2. **改完仍然「一屏 0 格读数」**：内核日志里卡名 OCR 明明读到 0.9988/0.9999（`text: "乌尔比安"` / `"泥岩"`），而 `log_entry` 每步只有 3 行（开始 / 完成 / 全部完成，`source='maa_task'`），按 `meta like '%乌尔比安%'` 查为 0 → 读数必须走实时回调（Rulings 3）。
3. **模组读数空缺**：详情链只把「紧邻的下一个」当候选（`following = [index+1:index+2]`），精二干员命中 `Elite2` 后直接跳到 `Done`，`ModuleCode` / `ModuleStage` 从没跑过 → 改成「当前节点之后的所有节点 + `Done`」都进 `next` / `exceededNext`。
4. **翻屏没翻动（竖划无效）**：见 Rulings 6。`adb shell input swipe` 竖划三组、慢速 2000 ms、`input motionevent` 序列、内核 `SwipeDown` 节点全部 0.00–0.55；横向一次 62.64。
5. **同一批人被读两遍、没找到的人被静默丢掉**：`position += matched` 把重复计数当进度，7 人名单 `scanned=11`、`skipped=0`、麦哲伦直接消失 → 按 `char_id` 去重（Rulings 7），并把「没找到的人」如实记进 `skipped`。

## 真机验收（2026-10-11，五轮递增）

| 轮次 | 名单 | 结果 | 说明 |
| --- | --- | --- | --- |
| run2 | 风笛 | `succeeded`，21 s，`scanned=1` | `Done` 兜底生效（不再 90 s），但读数全空 → 暴露问题 2 |
| run3 | 风笛 | `succeeded`，`scanned=1 updated=1` | 风笛 03:58:37 写入 `skill 7 / mast {"1":0,"2":3,"3":3} / mod {"X":3}` ＝ W1 手工核对真值 |
| run4 | 7 人 | `succeeded`，2 分 49 秒，`scanned=11 updated=5 skipped=0` | 5 行写入 03:59:34–04:00:22；暴露问题 4、5（麦哲伦被静默丢掉） |
| run5 | 7 人（故意从第二屏起跑） | `succeeded`，`scanned=0 skipped=7` | 7 人全部如实进 `skipped`；12 条流水线（1 进列表 + 6 读卡名 + 5 翻屏）全 completed |
| run6 | 7 人（列表回到第一屏） | `succeeded`，04:14:23→04:17:14，`scanned=5 updated=0 skipped=2` | 5 人各只点开一次；跳过 ＝ 重岳（该格卡名没读出来）+ 麦哲伦（不在可达屏内） |

- 内核日志 `resource/maa-api/debug/asst.log`：2026-10-11 12:14 之后 `TaskChainError` **0 次**（之前累计 26 次），横向翻屏与 `Done` 兜底都没留下链错。
- 库副本（`/tmp/m36_accept/resource/maa_api.db`）终值：风笛 `7 / 0-3-3 / {"X":3}`、凛御银灰 `7 / 3-3-3 / 无模组`（两名与 W1 手工核对一致）、伊内丝 `{"1":0,"2":3,"3":3}`、重岳 `{"1":0,"2":0,"3":3}`、史尔特尔与玛恩纳 `{"1":0,"2":0,"3":3} + {"X":3}`、麦哲伦保持 `精二 1 级 / 三列 NULL / updated_at 2026-10-10`（没被碰过）。
- 节点文件 `resource/maa-layers/custom/resource/tasks/MaaApi@OperatorScan.json`：42 个节点、含 `SwipeNextPage`、不含 `SwipeDown`。

## 验收（主 agent 在所有写者停止后独占复跑，工作树即最终提交前状态）

1. **后端全量** `.venv/bin/python -m pytest -o addopts= -q` → `2 failed, 3687 passed, 5 skipped, 23 warnings in 216.74s`。两条红是既有长期已知红 `tests/services/test_activity_index.py::test_real_resource_pack_reports_the_current_activity_and_its_stages` 与 `::test_real_resource_pack_disambiguates_a_repeated_code`（真资源包「昨日海」没有期数 → `confidence='none'`，§8.39 五、④），本轮**无新增红**（M35 是 3599 passed，本轮 +88 例）。
2. **生成物四处**：`scripts/dump_openapi.py --check` → `OpenAPI snapshot is current: web/openapi.json`；`scripts/generate_api_guide.py --check` → `API guide generated sections are current: docs/14-开放API接入指南.md`；`web` 内 `pnpm gen:api:check` → 见下（前端三件套同一批跑）；`scripts/copy_inventory.py --write-baseline` 后 `--check` → `通过：无回潮，0 项较基线下降`（基线 45 项，本轮新增文案按棘轮重设）。
3. **前端**：`pnpm typecheck` 退出码 0；`pnpm vitest run` → `Test Files 74 passed (74)` / `Tests 769 passed (769)`（M35 是 73 / 755）；`pnpm build` → 末行 `[sw_harness] SERVICE WORKER OK`。
4. **冒烟**：`scripts/agent_smoke.py` → 末行 `SMOKE OK`（前一句 `[ok] 临时 SQLite 隔离；仓库 DB/config 指纹未改变（409600 bytes）`）；`scripts/frontend_smoke.py` → 末行 `SMOKE OK`。
5. **真机五轮**：见上表；内核日志 `TaskChainError` 0 次；库副本终值与游戏界面逐人对齐（风笛、凛御银灰两名与 W1 手工核对一致）。
