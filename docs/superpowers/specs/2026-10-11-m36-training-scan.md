# M36 · 干员详情练度扫描（技能等级 / 专精 / 模组）

## 1. 目标与用户决策

M35 把账目的 `skill_level`、`masteries`、`modules` 三列定成「未识别就是 `NULL`，不冒充 0 值」，并明确内核的 `OperBox` 识别**读不到**这三个字段（`maa_api/domain/task.py:1378` 的 `OperBoxInput` docstring 写明）。于是这三列在真机库里 463 行全 `NULL`，养成计划对技能的增量只能按「未知」处理。

M36 给这三列补一条**真正的识别通道**：让内核去开干员详情页，把技能等级、三个技能的专精等级、模组等级读出来，按识别来源写回账号表。未读到的字段仍然保持 `NULL`。

用户决策（2026-10-11）：

1. **写回口径**：识别到的值直接按识别来源写库；没读到的字段保持 `NULL`，不做兜底、不猜。
2. **入口范围**：HTTP API + Agent 工具 + 前端账号页入口，三处齐做。
3. **首版名单**：已拥有且精英化二以上（`elite >= 2`）∪ 进行中养成计划的目标干员；单次扫描有上限，可配。
4. **设备配合**：为了标定详情页 ROI 与模板，用户配合在模拟器里登录游戏并停在干员列表页。

## 2. 事实基础（2026-10-11 真机实测）

### 2.1 设备与页面

- 模拟器：`adb -s 127.0.0.1:5555`，分辨率 2560x1440；内核基准坐标系是 1280x720，缩放系数 2（`client.click` 的坐标是内核基准，`maa_api/core/client.py:986`）。
- 前台包：`com.hypergryph.arknights.bilibili`（B 服）；内核 v6.18.0。
- **干员列表页**：8 列 × 2 行卡面可见，卡片名条在内核 y 350-370（上排）与 645-680（下排），列中心间距约 108（内核），首列中心 x 约 82。
- **干员详情页**（内核资源里叫 `OperFiles`）：从列表点卡面任意位置进入；左上返回箭头在内核 (40,40)，**返回后列表滚动位置保持**（实测：风笛详情页返回后仍在第一屏原位）。

### 2.2 详情页可读字段（风笛 6★ 精二 90 实测）

| 字段 | 内核依据 | 实测值 |
| --- | --- | --- |
| 技能等级 | `OperProgress@CurrentSkillLevel`（roi `[1215,359,14,22]`，baseTask `NumberOcrReplace`，读「RANK n」的数字） | 7 |
| 专精 1/2/3 | **内核模板路不可用**（W1 实测否掉了初版设想），改由 Python 侧看像素：截图（`Asst.get_image()`，与同批识别同一帧）上每个圆点取 7x7 紧框，框内最亮 25% 像素均值 ≥ 160 判「亮」，亮几个就是几级 | 0 / 3 / 3（另测无专精样本：槽一 0、槽二三 3） |
| 精英化 | 三个单模板 `OperFilesElite0/1/2.png` + `templThreshold: 0.9`（取分数最高者），roi `[866,235,119,88]` | 2（交叉校验用） |
| 满级标志 | `OperFilesLevelMax.png`（「EXP -/-」），roi `[974,111,76,64]` | 命中 |
| 模组 | 详情页右下模组面板：编号「CHG X」用裸 `OcrDetect`（roi `[1085,576,60,24]`，正则 `^\s*([A-Z]{2,4})\s*([A-Z]{1,4})\s*$`）；等级「STAGE n」用 `NumberOcrReplace`（roi `[1222,542,32,26]`） | `CHG-X` / 3；无模组的干员两个节点都读不到 ⇒ 保持 `NULL` |
| 卡名（列表页，身份来源） | 16 个逐列 OCR 节点（8 列 × 2 行），`baseTask: OperBoxNameOCR`，名字条带 y = 344（上排）/ 666（下排）、带高 30、列距 160 | 每屏约 3/16 格读不出名字（本次：下排 col0/col3/col6）⇒ 那些格跳过并记 `unresolved` |

专精徽章的语义已人工放大核对：三个圆点全暗＝0，全亮＝3，逐点亮＝1/2。**初版「模板匹配必须由内核做」的结论被 W1 推翻**：内核的多模板数组只认第一个元素（满亮徽章也回 `OperFilesSkillMaster0.png`），而单亮点模板对全暗的圆点照样给 0.83–0.99 的高分，没有判别力；Python 侧像素读数在真机两例上都分得干净（亮 255 / 暗 71，阈值 160）。

详情页大名字改用 `OperBoxNameOCR` 读不出来（`specialParams` 是按卡名小字条调的，实测回「画」score 0.166）⇒ **身份复核改成点开前用列表页卡名定身份**，详情页不再读名字。

### 2.3 读数通道（本次发现的关键事实）

`SubTaskStart`(20001) / `SubTaskCompleted`(20002) 的 `details.result` 会把**每个**识别节点的结果交给回调，形如 `{"rect":[x,y,w,h],"score":0.846,"text":"可收获"}`（OCR）或 `{"template":"OperBoxFlagRole4.png",…}`（模板），证据在 `resource/maa-api/debug/asst.log`。契约见 `maa_api/core/asst_protocol.py`。

仓库目前按 `_STEP_MARKER = "action"` 把这些事件整类丢掉（`maa_api/services/callback_translator.py:110,154-157`），所以现在**没有任何 Python 侧代码能看到识别结果**。M36 新增第二个订阅点，不动现有的丢弃口径。

W7 真机又坐实一条：这些读数**也不进 `log_entry`**——内核只把「带文案」的 `SubTaskExtraInfo`（20003）写日志，`SubTaskStart` / `SubTaskCompleted`（20001/20002）不写，所以 `callbacks_for()` 读 `log_entry.raw` 天然拿不到（首跑 6 屏读数全空、`scanned=0` 的真因）。读数必须走内核**实时回调**（`client.on("CALLBACK", tap.handle)` 的 `CallbackTap`，与 M29 `CopilotResultService.tap()` 同一条路），`log_entry` 只作兜底合并。

### 2.4 内核侧能力边界

- 内核只导出 26 个 `Asst*` 符号，**没有** `AsstGetRecognitionDetail`、没有 OCR 查询接口；`libfastdeploy_ppocr.dylib` 的导出全是 C++ mangled 名。⇒ 读数字只能走「内核 pipeline 节点 + 回调」这一条路。
- `AsstAsyncClick` 已绑定并封装（`maa_api/core/asst.py:246,429`、`client.py:986`），是 Python 侧**唯一**的点击手段。
- 自定义任务：`Custom` + `task_names`（`maa_api/domain/task.py:1411`），节点定义允许的键见 `maa_api/services/resource_service.py:52-93`；`action: "DoNothing"` 是只读节点的关键（不点、不滑、只识别）。
- **自定义任务不是「只跑名单里那一个节点」**（W7 真机实测）：内核会沿提交节点的 `next` 一路走到底。只提交 `…@CardNameTopCol0`，`asst.log` 里 `first: [CardNameTopCol0]` 会一路跑到 `CardNameBottomCol5`。⇒ 一条链只提交链首，服务层不按段续跑（§4.2）。
- **识别不出来的节点会在一轮内重试到 `retry_times: 20`**（≈30 s）才报 `TaskChainError`，而 app 侧流水线**不把它当终态** → 这一条流水线只能耗到 90 s 步超时。首跑「练度扫描：读卡名超过 90 秒还没结束」的真因就是这个。⇒ 链尾必须有 `JustReturn` 的 `Done` 兜底节点，提交时把 `Done` 追加在 `task_names` 末尾。
- **干员列表是横向翻页的**：竖划一格都不动（`adb shell input swipe` 竖划三组、内核 `SwipeDown` 节点前后卡面区均值差 0.00–0.55），横向从右往左才换屏（`adb` 62.64、内核节点 96.66）；内核一次约翻 5 列，不满一屏（§4.1 `SwipeNextPage`、§4.2 第 6 步）。
- 自定义节点可用 `baseTask` 继承内核节点（内核资源里 1090 个任务），本里程碑大量复用 `OperProgress@*` 与 `OperBox*` 的 ROI/模板，避免自己标定。

### 2.5 写回侧门槛

- `maa_api/services/account/rules.py:70` 的 `RECOGNIZED_FIELDS = ("own","elite","level","potential")` **不含**三列；`box.apply_recognition` 只遍历这个元组（`box.py:447`），来源护栏 `WRITE_SOURCES = ("manual","agent")`（`rules.py:97`）——`RECOGNITION_SOURCE = "recognition"` 本身是合法来源（`rules.py:96`，前端 `FieldSource` 已含 `'recognition'`）。
- 迁移 `0018_unknown_training.py:48` 把三列写成 manual/agent only 的检查约束口径；M36 要扩这条口径。

## 3. 范围

### 3.1 本里程碑

1. 只读自定义任务 `MaaApi@OperatorScan@*` + 真机标定（卡名、技能等级、专精、精英化、模组编号与等级）。
2. 扫描服务：名单组装、逐屏读卡名、点开目标卡、读详情、写回、进度、取消、超时、跳过的记录。
3. 写回口径扩展：`rules.py` 新增「详情扫描字段集」+ `box.py` 新写入入口；识别记录新 kind。
4. `POST/GET /api/account/training-scan`（提交与状态），前端账号页干员标签页的扫描卡片（按钮 + 进度 + 取消）。
5. Agent 工具 `scan_operator_training`（条件确认），知识库 playbook 增补。
6. 单测、真机验收、文档与台账。

### 3.2 不做

- 不点任何**会改变游戏状态**的按钮（升级、精英化、专精、模组解锁/升级），扫描全程只读。
- 不做潜能、等级、是否拥有（M33 的 `OperBox` 已有，且这三列已有数据）。
- 不做模组解锁条件、模组分支详情、皮肤/语音/档案页。
- 不重写 `OperBox` 识别链路，不改 `callback_translator` 现有丢弃口径。
- 不做「全 box 无上限遍历」：首版按名单 + 上限收敛。

## 4. 设计

### 4.1 内核侧节点（`maa-layers/custom/resource/tasks/MaaApi@OperatorScan.json`）

全部只读（`action: "DoNothing"` / `JustReturn`），文件由服务 `ensure_tasks()` 写入并触发资源重载，机制照 `maa_api/services/copilot/result.py:505`。节点名一律带 `MaaApi@OperatorScan@` 前缀，定义在 `maa_api/services/account/scan_tasks.py`，共 42 个（16 卡名 + 16 点击 + 6 详情 + 3 导航 + 1 兜底 `Done`）：

| 节点 | 定义要点 | 产出 |
| --- | --- | --- |
| `…@CardNameTopCol0..7` / `…@CardNameBottomCol0..7`（16 个） | `baseTask: OperBoxNameOCR`，roi 一列一格：`(160*col, 344 或 666, 160, 30)` | 每张卡的名字 + rect（读不出的格子跳过） |
| `…@ClickTopCol0..7` / `…@ClickBottomCol0..7`（16 个） | `algorithm: JustReturn` + `action: ClickRect` + `specificRect: [卡面中心, 10, 10]`，`postDelay: 800` | 点开一格的详情页（必命中，不靠识别） |
| `…@SkillLevel` | `baseTask: NumberOcrReplace`，roi `[1215,359,14,22]` | 技能等级数字（只收 1–7） |
| `…@Elite2` / `…@Elite1` / `…@Elite0` | 单模板 `OperFilesElite2/1/0.png` + `templThreshold: 0.9`，roi `[866,235,119,88]` | 取分数最高者 → 2/1/0 |
| `…@ModuleCode` | 自己声明的 ASCII `OcrDetect`（**不走** `NumberOcrReplace`／也不引用不存在的 `LETTERS`），roi `[1085,576,60,24]` | 形如 `CHG X` → 类型字母 X/Y/D/A |
| `…@ModuleStage` | `baseTask: NumberOcrReplace`，roi `[1222,542,32,26]` | 「STAGE n」的数字（1–3；要 `ModuleCode` 先有效） |
| `…@EnterBox` | `algorithm: JustReturn`，`next: ["OperBoxBegin"]` | 进干员列表页（复用内核链，顺带按等级排序） |
| `…@Back` | `baseTask: Return`，模板 `["Return.png","Return-White.png"]`，roi `[0,0,180,80]` | 回列表（深浅主题各一张模板） |
| `…@SwipeNextPage` | `algorithm: JustReturn` + `action: Swipe`，`specificRect [1080,350,100,100]` → `rectMove [100,350,100,100]`（内核基准坐标，横向从右往左划），`specialParams [400,1,37,1]`，`postDelay 1200` | 列表翻到下一屏（导航；只翻页不点干员）。**初版 `…@SwipeDown`（照内核 `SwipeToTheUp` 的竖划形状）在真机上无效**，W7 改成横向 |
| `…@Done` | `algorithm: JustReturn`（`task_names` 末尾也会追加一次） | 链尾兜底：任何节点识别不出来时也干净收尾，不再重试到 `TaskChainError`（§2.4） |

三条实现口径（都是真机踩出来的）：`maxTimes: 1`（不重试）；每个节点的 `next` 与 `exceededNext` 都指向**该节点之后链上的所有剩余节点**再加 `Done`——不是只指向紧邻的下一个（详情链上精二的干员匹配 `Elite2` 后 `Elite1` 必然读不到，只给紧邻候选会一路掉到 `Done`，模组两个节点永远不跑，真机实测 `modules` 全 `NULL` 就是这个原因）；**不用**跨节点 `cache`（缓存会让后续节点读到旧帧）。链由内核自己走完（§2.4），所以服务层只提交链首节点加 `Done`，见 §4.2。节点名与读数解析由 `scan_tasks.py` 的 `card_name_node` / `parse_card_name_node` / `click_cell_node` / `node_key` / `elite_level_of` / `module_type_of` / `module_stage_of` 负责；专精不在这里（见 §2.2，Python 侧像素读数在 `scan_image.py`）。

### 4.2 扫描流程（服务侧编排）

**一条链一次流水线**（W7 真机订正）：内核会沿 `next` 把整条链走完（§2.4），所以服务层只提交**链首节点 + `Done`**，一次收整条链的识别回调；每个干员 5 条流水线——`…@EnterBox` → 读屏（一条链读完 16 格卡名）→ 点开 → 详情链 → `…@Back`。每步仍能设超时、取消与重试（`DEFAULT_STEP_TIMEOUT_SECONDS = 90`），超时/取消/失败照旧抛。初版「一步一条独立流水线、服务层按段续跑」的写法在真机上跑不通：链会在读不出的节点上重试到 `TaskChainError`（§2.4），而 app 侧不把它当终态，只能耗到 90 s 步超时。

1. **准备**：`ensure_tasks()` 写资源文件并触发内核资源重载（同 M29，进程内只做一次），然后跑 `…@EnterBox`——它 `next` 到内核 `OperBoxBegin`，由内核自己把干员列表页打开并点「等级」页签排序。
2. **读屏**：提交链首 `…@CardNameTopCol0`（`task_names` 末尾追加 `Done`），内核沿 `next` 走完 16 格；回调里的 `text` 按节点名定位到 `(行, 列)`，得到本屏每格的名字。读不出名字的格子没有回调，不会截断后面格子的读数。
3. **匹配**：名字按客户端的名字替换表归一后（`_name_token`）查干员目录（`capture.load_operator_catalog`）得 `char_id`，与名单求交；读不出名字、或目录里没有的格子跳过并计入 `unresolved`（同一名字有多个形态时按目录歧义丢弃，不猜）。
4. **逐格读详情**：`…@ClickTopColN`/`…@ClickBottomColN`（`JustReturn` + `ClickRect` 到卡面中心，必命中）→ 提交详情链首 `…@SkillLevel`（同一条流水线跑完 6 个只读节点 + `Done`）→ `Asst.get_image()` 取**同一帧**截图 → `scan_image.mastery_levels_from_image` 数专精亮点 → `box.apply_detail_scan(char_id, …)` 只写这次真读到的字段。**精英化 < 2 时不提交专精与模组**（`MIN_MASTERY_ELITE = 2`；否则规则的 `skipped` 会把「规则拒绝」误报成「读花了」）。读失败/名字不符记 `skipped`/`mismatched`，继续下一格。
5. **返回**：跑 `…@Back`（模板 `Return.png`/`Return-White.png`，深浅主题各一张），失败只记日志不打断整轮（`_best_effort_back`）。
6. **换屏**：本屏目标处理完后跑 `…@SwipeNextPage` **横向翻一屏**（内核一次约翻 5 列，一屏 8 列 ⇒ 屏间重叠），重复 2-5；服务层按 `char_id` 去重（`_scan` 的 `done` 集合），同一个人一轮只点开一次。翻不到的人**如实进 `skipped`**（用 `matched` 推进名单会把没找到的人静默挤出去，run4 实测就是这样丢掉了一个目标）。屏数上限 `_screen_cap(total)`（`名单/每屏格数` 的三倍再加三屏余量），覆盖完名单或到上限就停。
7. **收尾**：写识别记录（`RECOGNITION_KIND_TRAINING = "training"`，`item_count` = 本轮改动的干员数）、把 `{scanned, updated, skipped, unresolved, mismatched}` 写进扫描状态；取消时对当前这条流水线调 `request_cancel`，已完成的部分保留。**没有**「滚回列表顶部」这一步（首版不做，回到列表由 `…@EnterBox` 重开时的位置决定）。

### 4.3 名单与上限

- 来源一：`list_operators(owned_only=True, elite_min=2)`（干员表里已有的精英化数据）。
- 来源二：进行中养成计划的 `targets[i]["operator"]`（**存的是干员名**，需一次名字 → `char_id` 解析）。
- 合并去重后按 `char_id` 排序，取前 `limit`（默认 40，请求可配 1-200）。
- 上限之外的干员在返回体里记 `deferred`（不是失败）。

### 4.4 写回

- `rules.py` 新增 `DETAIL_SCAN_FIELDS = ("skill_level","masteries","modules")`，并把 `field_sources_wire` 的口径扩到这三列；`RECOGNIZED_FIELDS` 保持不变（那是 `OperBox` 的口径，混在一起会让 M33 的回调路径也去写三列）。
- `box.py` 新增 `apply_detail_scan(char_id, *, skill_level=None, masteries=None, modules=None, recognized_at, source=RECOGNITION_SOURCE)`：只写本次**真的读到**的字段；`None` 一律不动（保持 `NULL` 或既有值）；`masteries`/`modules` 逐键合并，不整体覆盖；`field_sources[field] = "recognition"`、`recognized_values` 同步。
- 迁移不变（三列在 `0018` 已是可空）；只改代码侧的口径注释与 `docs/04` 的字段说明。
- 养成计划侧：M35 已经把「未知不算增量」写进规划层，M36 之后同一份计划会因为已知字段变多而自动给出更准的增量，**不需要改规划层**（回归验证即可）。

### 4.5 识别记录

- `RECOGNITION_KIND_TRAINING = "training"`（`maa_api/db/repositories/account.py:49-50` 旁），`item_count` = 本次成功写入的干员数。
- `GET /api/account/summary` 的 `box` 段沿用现有结构；扫描状态单独由新端点给，不复用 `box.recognized_at`（避免把「干员识别」与「练度扫描」两个时间混成一个）。

### 4.6 API

- `POST /api/account/training-scan`：body `{limit?: int = 40, char_ids?: list[str] | None, include_plan_targets?: bool = true}`；非阻塞，返回 `{pipeline_id, total, targets: [{char_id, name}], deferred: [...], unresolved: [...]}`；服务未起 → `AppError(SERVICE_UNAVAILABLE, "账号数据功能还在启动中，请稍后重试")`；已有扫描在跑 → `AppError(PIPELINE_ALREADY_RUNNING, "已有练度扫描在跑，先等它结束或取消")`（该错误码映射 409；初版设想的 `CONFLICT` 在 `ErrorCode` 里不存在）。
- `GET /api/account/training-scan`：`{status: idle|running|succeeded|failed|cancelled, pipeline_id, started_at, finished_at, scanned, updated, skipped, unresolved, mismatched, message}`。
- `DELETE /api/account/training-scan`：转 `PipelineRunner.request_cancel`，返回最新状态。
- 端点风格照 `GET /summary`（无 `response_model`、`dependencies=[Depends(require_auth)]`、`responses=error_responses(...)`），但要重跑 `scripts/dump_openapi.py` 与 `cd web && pnpm gen:api`。

### 4.7 Agent 工具

- `scan_operator_training`：`ToolRisk.CONDITIONAL`、`read_only=False`（它写账号表且要占设备若干分钟）→ 走人工确认；参数 `{limit?: int, include_plan_targets?: bool, char_ids?: list[str]}`；返回提交结果与当前状态；`status` 用只读工具 `get_training_scan_status`？——**不做**，状态由 `account_status` 一类既有只读工具或 HTTP 给，首版只加提交与取消两个动作工具。
- 知识库：`playbook-promotion-planning` 增补「练度未知可以先扫一遍」，措辞不得含「数字 + 游戏单位」（`tests/agent/test_knowledge_no_numbers.py` 会红）。

### 4.8 前端

- `web/src/features/account/OperatorsTab.tsx`：`<RecognitionStatus>`（:128）下方新增「练度扫描」卡片：目标人数、按钮（开始扫描）、活跃时 `<Progress>` 与「取消」、结束后显示 `{updated}/{scanned}` 与跳过原因摘要。会正面推翻 `RecognitionStatus.tsx` 头注释里「不提供刷新识别按钮」的旧裁决——那条裁决只约束**干员识别**，要在这里写明界限（`docs/13` 补偏离条）。
- `web/src/features/account/api.ts`：`accountKeys.trainingScan()`、`fetchTrainingScan()`、`startTrainingScan()`、`cancelTrainingScan()`；活跃时 `refetchInterval` 5s（照 `PipelineDetail.tsx:95`）。
- 未识别仍然显示「未识别」（M35 口径），扫描后字段来源显示「识别」。

### 4.9 失败、取消与并发

- 单个干员读失败（模板/OCR 没命中、名字不符）：记 `skipped`/`mismatched`，继续下一个，不中断整轮。
- 整体失败（内核断开、任务失败、超时）：状态置 `failed` 带 `message`，已写入的部分保留（不事务回滚整轮）。
- 取消：`request_cancel(pipeline_id)`；取消后已完成部分保留；状态 `cancelled`。
- 并发：同一时刻只允许一个扫描（服务内锁 + 409）；扫描本身是一条普通流水线，会与用户任务排队，不抢内核。

## 5. 兼容与风险

- **模板/ROI 随游戏版本漂移**：模板来自内核资源（`OperFilesSkillMaster*`、`OperFilesElite*`），内核资源升级时会一起更新；本服务不复制模板，只 `baseTask` 继承。ROI 若失效，表现为「读不到」→ 该字段保持 `NULL`，不会写错值。
- **模组面板缺失**：干员没有模组（未开放/未解锁）时右下角面板内容不同，`ModuleStage` 读不到 → `modules` 保持 `NULL`。W1 要用一个未解锁模组的干员真机验证这一路。
- **卡名 OCR 误读**：靠客户端的名字替换表（内核 `CharsNameOcrReplace`）压误读，仍解析不到就跳过并汇报。
- **卡名 OCR 有固定漏检**（W7 实测）：同一屏固定有一两格的卡名读不出来（run6 里重岳就是这一格），表现为该格进 `skipped` 而不是被猜一个 `char_id`。要覆盖某个具体的人，先把列表翻到他在的那一屏，或用请求里的 `char_ids` 点名。
- **点击误触**：只点卡面中心与返回箭头；不点详情页右侧的升级/专精入口区域（ROI 里全是只读节点，`action` 一律 `DoNothing`）。
- **设备占用**：扫描期间用户手动操作会打断识别；文档与前端提示里写明。

## 6. 文档同步

`docs/04-数据模型与持久化.md`（§5.19 三列来源口径新增「详情扫描」、`account_recognition.kind` 多一个 `training`）、`docs/05-API规范与路由清单.md`（三个新端点）、`docs/09-前端重构方案.md`（账号页新卡片）、`docs/11-Agent模块设计.md`（`scan_operator_training` / `cancel_training_scan`）、`docs/13-决策记录.md`（新 §8.51：节点复用内核 `OperProgress`、识别通道用 `details.result`、名单与上限、前端界限）、`docs/12-实施计划与里程碑.md`（M36 正文与验收结论）、`docs/README.md`（账号行补一句）、`docs/14` 若 OpenAPI 段受影响由脚本重跑、`docs/superpowers/ledgers/2026-10-11-m36-training-scan.md`。本节（规格）在 W7 按真机订正 §2.3／§2.4／§4.1／§4.2／§5。

## 7. 验收

**结论（2026-10-11，主 agent 在冻结树上独占复跑，全部通过）**：

1. `scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check` 全 current；`scripts/copy_inventory.py --write-baseline` 后 `--check` 通过（新增文案让基线按棘轮重设，45 项）。
2. `.venv/bin/python -m pytest -o addopts= -q` → `2 failed, 3687 passed, 5 skipped`（M35 是 3599 passed，本轮 +88 例；两条红仍是既有的 `tests/services/test_activity_index.py` 真资源用例，docs/13 §8.39）。
3. `cd web && pnpm typecheck && pnpm vitest run && pnpm build` → 退出码 0 / `74 passed (74)`、`769 passed (769)` / 末行 `[sw_harness] SERVICE WORKER OK`。
4. `scripts/agent_smoke.py`、`scripts/frontend_smoke.py` 末行均 `SMOKE OK`。
5. **真机扫描验收（五轮，游戏停在干员列表）**：run2 单目标 21 秒跑通（实时回调通道成立）；run3/run4 风笛写回 `skill 7 / 专精 {1:0,2:3,3:3} / 模组 {X:3}`，与界面手工核对一致；run4 暴露「翻屏不动导致重复扫描 + 没找到的人被静默吞掉」；run5（故意从第二屏起跑）`scanned=0 skipped=7`，无人被丢；run6 七人名单 2 分 51 秒 `scanned=5 skipped=2`（重岳＝卡名漏检、麦哲伦＝不在可达屏内），每人只点开一次，`updated=0`（值早已写入且一致）。全程内核日志 `TaskChainError` 0 次（此前累计 26 次）。
6. 负向对照：内核不可用/取消/超时三种情况下服务不写脏数据、状态与跳过原因正确（单测 `test_cancel_stops_the_round_and_keeps_what_was_read`、`test_missing_screenshot_leaves_masteries_unknown`、`test_a_mismatched_operator_is_not_written` 等；真机 run5 的 `skipped=7`）。
