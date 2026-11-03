# M37 · 练度扫描可用性修复（名单来源 / 名字参数 / 起手拉起游戏 / 空屏护栏） · 进度

- 分支：`m37-scan-usability`（自 `refactor/v2`，基点 `d286190` = tag `v2-m36`），完成后 tag `v2-m37`
- 规格：`docs/superpowers/specs/2026-10-11-m37-scan-usability.md`
- 计划：`docs/superpowers/plans/2026-10-11-m37-scan-usability.md`
- 来源：用户 2026-10-11 报障两条（原文：「仓库内有干员数据，但是扫描服务报错」「agent 扫描我的克莱门莎练度时，卡住了，界面是游戏中的干员列表，但是无操作，然后突然回到了模拟器主页，导致死循环，滑动列表等操作可以参考 maa 原生的干员扫描任务」），附账号页截图（卡片红框「开始扫描失败 · 这一轮没有可扫的干员：精二名单是空的」）与 Agent 会话截图（旁白重复「画面还停在设备桌面，游戏没在跑，这就是上次失败的原因。先把游戏拉起来再扫。」）
- 真机：BlueStacks / Android 2560×1440（`127.0.0.1:5555`），B 服包名 `com.hypergryph.arknights.bilibili`；验收服务 `127.0.0.1:8011`（仓库拷贝 `/tmp/m36_accept`，DB 为副本，`maa_api` 与 `resource/*` 都是仓库 symlink）

## 任务状态

| 工作项 | 写范围 | 状态 |
| --- | --- | --- |
| W1 名单来源 await 修复 | `maa_api/services/account/scan.py`（`_operators_from_box`、`resolve_targets`） | 完成（主 agent） |
| W2 `char_ids` 收名字 | `maa_api/services/account/scan.py`（`resolve_targets` 的 `char_ids` 循环） | 完成（主 agent） |
| W3 起手 `StartUp` + 独立超时 + 装配注入 | `maa_api/services/account/scan.py`（`_start_game`／`_run_tasks`）、`maa_api/main.py:1003-1014` | 完成（主 agent；真机冷启动实测） |
| W4 空屏护栏 | `maa_api/services/account/scan.py`（`_scan` 第一屏） | 完成（主 agent） |
| W5 单测回归 | `tests/services/test_account_training_scan.py`（新增 5 例 + 同步既有断言） | 完成（该文件 42 例、相关六套件 99 例全绿） |
| W6 前端文案 | `web/src/features/account/TrainingScanCard.tsx`（`BUSY_HINT`） | 完成（`pnpm vitest run src/features/account` 9 文件 93 例全绿） |
| W7 真机验收 + 文档 + 台账 + 基线 + 合并 | `/tmp/m37_scan.py`、`docs/13` §8.52、`docs/12` M37、`docs/README.md`、本文件、`docs/superpowers/ledgers/2026-10-11-m37-scan-usability.md`、`docs/copy-audit/*`（棘轮重设） | 完成（见下） |
| 验收 | 单测 / 真机三轮 / 后端全量 / 四处生成物 / 文案基线 / 前端三件套 / 两个冒烟 | 完成（见下） |

## Rulings I made（实施中定的口径）

1. **`StartUpInput` 的字段是平铺的，没有 `params` 口子**（`maa_api/domain/task.py:207-246`，`extra="forbid"`）：起手任务下发成 `{"name": "StartUp", "start_game_enabled": true, "client_type": <设置里的渠道>}`，与 `maa_api/services/account/plans.py:131` 同款。取不到默认渠道时不带 `client_type`，让内核自己决定。
2. **`StartUp` 不替代「进干员列表」这一步**：它的职责只是把客户端拉起来（内核里 `StartUp@ReturnButton` 是 `exec_times:0,max_times:0` 禁用，真机实测 7 秒就 `TaskChainCompleted`）。游戏停在别的游戏内页面时，页面对齐仍靠内核 `OperBoxBegin` 链——真机实测停在干员列表页也能一路跑通（`OperBoxRoleTab` 命中即停）。
3. **起手单独一条超时**：`DEFAULT_STARTUP_TIMEOUT_SECONDS = 180`，失败文案点到「游戏没起来或设备掉了」；步超时仍是 90 秒。真机冷启动那一轮全长 79 秒（12:52:00 → 12:53:19）。
4. **名单来源同步、异步都要能用**：`resolve_targets` 与 `_operators_from_box` 都做 `inspect.isawaitable` 判定再 `await`（与同文件 `_plan_operator_names`／`_grab_image`／`_callbacks` 同一写法），不在协议层强制 async——注入同步替身的既有单测继续可用。
5. **`char_ids` 收名字，但同名多形态仍然 `unresolved`**：先按 `char_id` 查目录，查不到再用名字索引；名字对不上人时不猜，如实进 `unresolved`（这是缺陷 1b，工具描述里本来就写着「char_id 或名字」，实现向描述对齐）。
6. **空屏护栏只在第一屏触发**：第一屏一个卡名都没读出来 → 立刻失败（`CORE_COMMAND_FAILED`，「画面可能不是干员列表页」）；第一屏之后的空屏是「翻屏过头」的正常现象，仍按 M36 口径把没找到的人记进 `skipped`。
7. **起手这一步也记进这一轮的流水线链**：`self._started.set()`、`first_pipeline_id` 都在 `_run_tasks` 里，取消与「第一步进队列」的受理语义不变。

## 实施中修掉的问题（按发现顺序）

1. **缺陷一离线复现**：`_operators_from_box` 漏了 `await`（返回协程对象），`resolve_targets` 拿到非 Mapping 就当空名单 → `total 0` → `submit` 抛「这一轮没有可扫的干员：精二名单是空的」。复现脚本用生产 `session_factory` + 真 `OperatorBoxService` 打出 `RuntimeWarning: coroutine 'OperatorBoxService.list_operators' was never awaited`；同一进程里直接调 `list_operators(owned_only=True, elite_min=2, limit=200)` 是 `total = 103`（真库 `own=1&elite=2` 就是 103 人）。
2. **为什么单测与五轮真机验收都没抓到**：单测一律注入**同步**的 `list_operators` 替身；M36 的真机轮全都显式传了 `char_ids`（默认名单分支从未被执行）。修法：替身加 `async_list` 开关复现漏 await，另外真机补跑一次空请求体（等价于点账号页按钮）。
3. **缺陷二的真机证据**：Agent 那次扫描的第一步（`MaaApi@OperatorScan@EnterBox`）从 12:35:05 起一直重试内核 `OperBoxBegin` 的九个候选（`OperBox` / `OperBoxRoleTabSelect` / `OperBoxSortTabSelect` / `OperBoxRoleTab` / `OperBoxBegin@QuickSwitch@ToOperBox@Entry` / `…@Open` / `OperBox@ReturnButton` / `OperBox@FromStageSN` / `OperBox@FromAnnihilation`）一个都没命中，55 秒后被 Agent 自己 `DELETE`；随后 Agent 补了两次 `StartUp`（12:36:13、12:36:35）并陷入重复旁白。修法：扫描起手先跑 `StartUp`。
4. **起手第一版写成 `{"name": "StartUp", "params": {...}}` 会被 422 挡下**（`StartUpInput` 是 `extra="forbid"` 的平铺模型）→ 改平铺，并在 `test_the_round_starts_the_client_first` 里断言下发的 payload。

## 真机验收（三轮，2026-10-11）

| 轮 | 起跑状态 | 请求 | 结果 |
| --- | --- | --- | --- |
| A | 游戏停在**干员列表页**（用户报障状态） | `{"limit": 2}`（不传 `char_ids`） | 受理 200、`total=2`（凯尔希 / 陈，`deferred` 101 人）——修复前这里就是那句报错；终态 `succeeded`，`scanned=0 updated=0 skipped=2`，6 条流水线全 completed，内核 `StartUp → EnterBox → 16 格卡名 → SwipeNextPage`，无 `TaskChainError`、无超时 |
| B | 先 `adb shell am force-stop com.hypergryph.arknights.bilibili`（焦点回到 BlueStacks 桌面） | `{"limit": 1, "char_ids": ["风笛"], "include_plan_targets": false}` | 受理 200、`total=1`、`char_222_bpipe 风笛`（**按中文名受理成功**）；内核 `12:52:00.796 StartGameTaskPlugin` → `12:52:02.467` 完成、客户端窗口被拉起；终态 `succeeded`，12:52:00 → 12:53:19（79 秒，含冷启动），`scanned=1 updated=0 skipped=0`；6 条流水线：启动游戏 → 进干员列表 → 读卡名 → 点开 风笛 → 读详情页 → 回到列表 |
| C | 离线（生产 DB 只读） | 默认 `limit=40` / `char_ids=["克莱门莎"]` | `total 40, deferred 63, unresolved []`（前 3 名 凯尔希 / 陈 / 煌）；「克莱门莎」→ `char_4231_clemnt` |

- 验收副本 DB：`char_222_bpipe` 仍是 `elite 2 / level 90 / skill_level 7 / masteries {"1":0,"2":3,"3":3} / modules {"X":3}`（与 M36 已知真值一致，`updated=0` 是因为值没变），`account_recognition` 新增 `id=11 kind=training item_count=0`。
- 驱动脚本 `/tmp/m37_scan.py`（argv[1] 给 JSON body，`POST` 后每 5 秒轮询到终态并打印快照 + 最近流水线）。

## 验收

1. 单测：`tests/services/test_account_training_scan.py` 42 例、相关六套件 99 例、前端账号特性 93 例全绿（新增 5 例点名四条缺陷：`test_an_async_operator_source_is_awaited`、`test_char_ids_accept_a_name`、`test_the_round_starts_the_client_first`、`test_the_round_without_a_default_channel_lets_the_core_decide`、`test_the_first_screen_must_show_the_operator_list`）。
2. 真机：见上表三轮。
3. 全量门禁（主 agent 在冻结树上独占复跑）：`.venv/bin/python -m pytest -o addopts= -q` → `2 failed, 3692 passed, 5 skipped, 23 warnings in 239.23s`（两条红是既有 `tests/services/test_activity_index.py` 真资源用例）；`dump_openapi.py --check` / `generate_api_guide.py --check` / `pnpm gen:api:check` 三处原文通过；`copy_inventory.py --write-baseline`（45 项）后 `--check` → `通过：无回潮，0 项较基线下降`；`pnpm build` 末行 `[sw_harness] SERVICE WORKER OK`；`pnpm vitest run` → 74 文件 769 例；`scripts/agent_smoke.py` 与 `scripts/frontend_smoke.py` 末行 `SMOKE OK`。
4. 落点：分支 `m37-scan-usability` → `git merge --no-ff` 回 `refactor/v2` → tag `v2-m37`；不 push、不碰 dev。
