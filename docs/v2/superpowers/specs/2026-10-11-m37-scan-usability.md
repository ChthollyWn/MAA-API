# M37 · 练度扫描可用性修复（空名单 422、名字参数、起手把游戏带起来）

M36 交付当天用户真机使用报了两个缺陷，M37 只修这两个缺陷暴露出来的口径问题，不改 M36 的识别与写回设计。

## 1. 目标与用户报障

用户原话（2026-10-11）：

> 1.仓库内有干员数据，但是扫描服务报错，2.agent扫描我的克莱门莎练度时，卡住了，界面是游戏中的干员列表，但是无操作，然后突然回到了模拟器主页，导致死循环，滑动列表等操作可以参考maa原生的干员扫描任务

拆成四条要修的口径：

1. **点名册来源**：账号页点「开始扫描」（请求体是空对象）必须能拿到名单；库里 `own=1 and elite>=2` 有 103 人，不能报「精二名单是空的」。
2. **`char_ids` 允许名字**：Agent 工具的参数描述写的是「`char_id` 或名字」，实现必须收名字，不能让「扫克莱门莎练度」掉进同一条报错。
3. **起手把游戏带起来**：MaaCore 的任务都从主界面起手，扫描第一步之前必须先把客户端拉起来（与全仓既有口径一致），不能再出现「停在干员列表/设备桌面被内核重试到超时」。
4. **画面不对要快点说**：一屏卡名一个都读不出来时立刻失败并给可操作提示，不能安静地跑完整轮再报「全跳过」。

## 2. 事实基础（2026-10-11 真机与日志实测）

### 2.1 缺陷一：空名单 422

- 现象：账号页「干员识别」卡片显示「共 384 名（精二 103）」，点「开始扫描」出红框「开始扫描失败 / 这一轮没有可扫的干员：精二名单是空的，先跑一次干员识别，或检查养成计划」；服务端 `resource/log/2026-10-11.log` 记 `POST /api/account/training-scan HTTP/1.1" 422`（12:33:26）。
- 前端确实发空体：`web/src/features/account/TrainingScanCard.tsx:100` 的 `mutationFn: () => startTrainingScan({})`，`web/src/features/account/api.ts:540` 的 `startTrainingScan(body = {})`。
- 离线复现：真库副本上直接构造服务复现，`RuntimeWarning: coroutine 'OperatorBoxService.list_operators' was never awaited`，`resolve_targets(limit=40, include_plan_targets=True)` 回 `total 0`；同一进程里 `box.list_operators(owned_only=True, elite_min=2, limit=200, offset=0)` 回 `total 103`。

### 2.2 缺陷二：名字参数

- `maa_api/agent/tools/account.py:148-152` 把 `char_ids` 描述成「只扫这些干员（`char_id` 或名字）」，但 `resolve_targets` 对 `char_ids` 只查干员目录的 `char_id` 键（名字解析只存在于养成计划分支），实测 `resolve_targets(char_ids=["克莱门莎"])` 回 `unresolved=['克莱门莎']`、`total 0`，撞的是同一句报错。

### 2.3 缺陷三：起手没有把游戏带起来

- 全仓既有口径：`maa_api/agent/preconditions.py:29-51` 与 `maa_api/agent/runtime.py:200` 写着「MaaCore 的每个任务都从主界面起手，只有 `StartUp` 会拉起客户端；游戏没启动就提交任务只会重试到失败」。
- 真机证据：Agent 会话让它扫克莱门莎时，扫描第一步（`MaaApi@OperatorScan@EnterBox`）从 12:35:05 一直重试内核 `OperBoxBegin` 的候选表（`to_be_recognized` 九项一个都没命中），12:36:00 被 Agent 自己 `DELETE` 掉，服务端记「练度扫描被取消：已扫 0 名」；随后 Agent 补了两次 `StartUp`（12:36:13、12:36:35）并陷入重复旁白。
- 内核 `StartUp` 的边界（`resource/maa-api/debug/asst.log:273122-273176`）：链首 `first: ["StartAtHome","StartWithSanity","SwitchTheme@ToggleSettingsMenu","StartUpBegin"]`，`StartGameTaskPlugin` 起过客户端，`StartUp@ReturnButton` 是 `exec_times:0,max_times:0`（禁用），7.1–7.2 秒就 `TaskChainCompleted`。⇒ `StartUp` 只负责把客户端拉起来；把游戏内页面带回主界面要靠内核自己的进列表链（`OperBoxBegin`）。

### 2.4 缺陷四：画面不对没有护栏

- M36 收尾口径是「找不到的人如实进 `skipped`」，但一屏卡名**一个都读不出来**时同样只是全进 `skipped`，用户看到的是「无操作 + 什么都没扫到」。这种画面多半不是干员列表页，应该快速失败。

## 3. 根因

| # | 根因 | 位置 |
| --- | --- | --- |
| 1 | 默认名单来源 `_operators_from_box` 直接把 `list_operators` 的**协程**当结果返回（漏 await），`resolve_targets` 拿到非 Mapping 就当空列表 | `maa_api/services/account/scan.py:937-939`、`:418-421` |
| 2 | `char_ids` 只按 id 查目录，名字进 `unresolved` | `maa_api/services/account/scan.py:394-403` |
| 3 | 扫描起手直接进 `OperBoxBegin` 链，没有 `StartUp` 这一步 | `maa_api/services/account/scan.py:564-569`（`_drive` → `_enter_box`） |
| 4 | 第一屏没有读数时不区分「卡名没读出来」与「本轮确实没有目标」 | `maa_api/services/account/scan.py:628-658`（`_scan`） |

## 4. 修复口径

### 4.1 名单来源（W1）

`resolve_targets` 取名单时对返回值做 `inspect.isawaitable` 判定再 `await`（与同文件 `_plan_operator_names`、`_grab_image`、`_callbacks` 同一写法），并把 `_operators_from_box` 改成 `async def`、内部同样做判定。注入同步替身的单测继续可用。

### 4.2 名字参数（W2）

`char_ids` 里的每一项先按 `char_id` 查目录，查不到再用干员目录的名字索引解析；同名多形态的名字仍然进 `unresolved`，不猜。工具描述保持不变（实现向描述对齐）。

### 4.3 起手启动游戏（W3）

`_drive` 在进列表之前先跑一条 `StartUp` 流水线：

- 任务参数 `{"start_game_enabled": true}`，`client_type` 取设置里的默认渠道（`get_settings().channel.client_type`，与 M33 回调接线同一个来源），取不到就不带这个字段；
- 独立超时 `DEFAULT_STARTUP_TIMEOUT_SECONDS = 180`，失败文案要说清是起手这一步：「练度扫描：启动游戏超过 180 秒还没结束（游戏没起来或设备掉了）」；
- 起手这一步也参与「第一步进队列」的受理语义与识别记录的 `pipeline_id`。

### 4.4 画面护栏（W4）

一屏卡名一个都没读出来时立刻失败，文案指向画面：「练度扫描：读卡名一个卡名都没读出来（画面可能不是干员列表页，先让游戏进到干员列表）」。第一屏之后的空屏不额外报错（翻屏过头会出现空屏，仍按 M36 口径收尾）。

### 4.5 文案与文档（W5、W6）

- 前端卡片说明与忙碌提示补一句「起手会把游戏拉起来」，口径与 `docs/13` §8.52 一致。
- `docs/12` M37 小节、`docs/13` §8.52、`docs/README.md` 账号行按本轮实测结论更新；本轮不动数据模型与接口，`docs/04` / `docs/05` 无需变更。

## 5. 风险与不做

- **不做**：不改识别与写回口径（M36 已验收的部分一行不动）；不给 `StartUp` 加「先关掉游戏」的行为；不在扫描里做「重试整轮」；不动内核资源文件。
- **风险**：`StartUp` 在游戏已经在主界面时多花约 7 秒（真机实测 7.1–7.2 秒），可接受；游戏停在干员列表时 `StartUp` 不会把页面带回主界面，进列表仍靠 `OperBoxBegin` 链（真机实测能识别已展开的职业栏与排序页签）。
- **回退**：起手这一步若在真机上产生新问题（例如拉起客户端失败），退化成「起手失败即明确报错，不自动拉起」，其余修复保留。

## 6. 验收

1. 单测：默认名单来源为 async 时能取到名单（回归缺陷一）；`char_ids` 收名字（回归缺陷二）；起手 `StartUp` 流水线排在进列表之前（回归缺陷三）；第一屏无读数立刻失败（回归缺陷四）；既有 M36 扫描用例全绿。
2. 真机：空请求体（等价于点账号页按钮）能拿到名单并跑完一轮；游戏关掉时扫描能自己把客户端拉起来；Agent 用名字（克莱门莎）发起扫描能成功受理。
3. 全量门禁：`pytest`、四处生成物 `--check`、文案基线 `--check`、前端 typecheck / vitest / build、`scripts/agent_smoke.py` 与 `scripts/frontend_smoke.py` 末行 `SMOKE OK`。
4. 落点：分支 `m37-scan-usability` → `git merge --no-ff` 回 `refactor/v2` → tag `v2-m37`；不 push、不碰 dev。
