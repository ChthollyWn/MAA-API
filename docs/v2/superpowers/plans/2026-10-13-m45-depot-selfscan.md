# M45 计划：不再依赖内核白名单，自己识别仓库全部道具

- 规格：`docs/superpowers/specs/2026-10-13-m45-depot-selfscan.md`
- 台账：`docs/superpowers/ledgers/2026-10-13-m45-depot-selfscan.md`
- 分支：`m45-depot-selfscan`（从 `refactor/v2` @ `0670331` 新建）
- 前置：M44 仓库识别覆盖 + 道具图（`0670331`）、M27 仓库识别（`DepotInfo` 回调 + `DepotService`）、M43 资源代理
- 设备占用：W0 已用完（本轮）；W2 一轮；W4 一轮验收
- 收尾：W4 用 `git merge --no-ff` 合并回 `refactor/v2`，打 tag `v2-m45`

## 波次

### W0 实验 + 规格与计划（已完成，不占设备收尾）

- **为什么**：开工前必须先定两件事 —— ① M44 那层「同名覆盖」到底生效不生效（决定 M44 的 W1 是「已交付」还是「要重做」）；② Python 侧能不能真的把整页 24 格认出来（决定 M45 的技术路线成不成立）。
- **写范围**：`docs/superpowers/specs|plans|ledgers/2026-10-13-m45-depot-selfscan.md`（只落文档）；实验脚本在 `/tmp/m45/`（不进版本库）。
- **做法与结论**：
  - A（覆盖层）：读 MAA v6.18.0 的 `AsstCaller.cpp:55-68` / `ResourceLoader.cpp:167-177` / `TaskData.cpp:78-105,226-280,315-323` 定性「逐 key 合并、后来者胜、`m_json_all_tasks_info` 从不清空」，再用 `maa_api/core/worker.py:277-286` + `main.py:547-550` 确认 custom 最后加载；最后**真机实验**：临时覆盖内核 `DepotBegin`（只给 `next`）→ 日志 `asst.log:223858` 显示 `to_be_recognized` 变成我们的列表、`asst.log:223855` 显示 `algorithm: JustReturn` 被继承 ⇒ 同名覆盖生效 + 按 key 合并。**结论：能覆盖。**
  - A 的副产品（M44 两处修正）：`DepotAllTabClicked` 因模板 600×276 > roi 300×138 报 `templ size is too large`（`asst.log:221804`），全日志 0 命中，是死代码；`26 → 77` **不是**页签覆盖带来的 —— `asst.log:150254`（15:44，进程起于 15:40、M44 合并于 16:58）在**没有覆盖层**的情况下就拿到 77，同进程 15:52 只有 26（`asst.log:150950`），差别是内核 `swipe_and_analyze()` 提前 `break`。
  - B（Python 识别）：`/tmp/m45/ncc_v3.py` 在 1066×599 空间按内核几何（`129 / 158 / 122 / 162×132`）对 479 张模板做 masked NCC。**24 格：18 格接受且全对，4 格 `ap_supply` 家族按 margin 判歧义，2 格模板库没有。** `7003` 命中 `0.8978`、`3141` 命中 `0.8728`、`7002` 命中 `0.9159`；`7004/3401` 本页不在，注入召回 `0.9999` 命中。半格错位 24 个位置**误接受 0/24**。单页 **733 ms**（479 模板）。阈值建议 `top1 ≥ 0.75 且 margin ≥ 0.15`。
  - 顺带发现：内核自己的网格在本页 `x_first = 105` ⇒ 第 8 列 cell roi 右边界 `1069 > 1066` 被 `include()` 丢掉，**内核只扫 21 格**；日志里那一趟的 `roi.x` 只到第 7 列，是独立佐证。
- **验收**：规格 §2 的每条事实都能指到「文件:行号」或「日志行号」；`/tmp/m45/` 里的脚本可重跑复现数字。
- **blocked_by**：无

### W1 识别器实现 + 单测（不占设备）

- **为什么**：W0-b 证明了路线成立，但那是脚本；要变成有单测、有配置项、可复现的生产代码。
- **写范围**：
  - `maa_api/services/account/depot_scan.py`（新，识别器：几何读取 + 网格拟合 + masked NCC + 阈值判定）
  - `maa_api/services/account/depot_geometry.py`（新，或并入上者：从本机内核 `tasks.json` 读 `DepotMatchData` / `DepotBaseRect` / `DepotQuantity`，读不到就用实测默认值并记 warning）
  - `maa_api/settings.py` + `maa_api/services/settings_schema.py`（`account.depot_scan_*`：`top1_min` 默认 `0.75`、`margin_min` 默认 `0.15`、`min_accepted` 默认 `8`、`scales` 默认 `[0.95,1.0,1.05]`）
  - `tests/services/test_account_depot_scan.py`（新）
  - `tests/services/fixtures/depot/*.png`（W0 的两张截图转 fixture，或裁小后入库）
  - `docs/04`、`docs/13` §8（新条目）
- **做法**：
  1. 取帧 → 缩放到 1066×599（缩放比例从 `DepotMatchData.roi` 算，不写死）；
  2. **网格定位**：行/列能量谱 + 栅格搜索（`pitch ∈ [120,140] / [145,175]`，`first ∈ [0,pitch)` 步长 0.5），另用金色稀有度圆环连通域投影交叉验证，两者差 > 6 px 就整轮失败；再用 `DepotBaseRect` 当先验兜底；
  3. 每格 masked NCC 全库打分（mask = 模板灰度 `>= 1` + 抹右下 `80×50`；尺度取 `scales` 里最优）；
  4. 阈值判定 → `{item_id, score, margin, rect}` 或 `{unknown, top3, rect}`；
  5. 整页接受数 `< min_accepted` ⇒ 整轮 `ScanFailed`，不写库。
- **性能目标**：单页 ≤ 1.5 s（W0 实测 733 ms @479 模板；若超，先按 HSV 均值颜色预筛到 60 个候选 —— W0 实测 314 ms）。
- **验收**：
  - `pytest -q tests/services/test_account_depot_scan.py` 全绿，且断言：24 格都有结果；接受的 ≥ 18 格且 `4002/4003/4001/3003/4006/3141/4004/4005/7001/7002/7003` 全部命中；`ap_supply` 那 4 格是「歧义」不是某个 id；半格错位输入误接受 **0**；两张 fixture（d1/d2）的 top-1 差异 ≤ 2 格且差异只出现在 `ap_supply_lt_60/80`。
  - 一张 fixture 跑一遍 ≤ 1.5 s（用 `time.perf_counter` 断言上限放宽到 3 s，避免 CI 抖动）。
- **blocked_by**：W0

### W2 任务链 / 取帧接线 + 数量（占设备一轮）—— **已完成（数量除外）**

> **实施结果（2026-10-13 下午，详见台账 W2 节）**：
> - 已做：取帧接线：`maa_api/services/account/depot_scan_service.py` 的 `DepotScanService`
>   （默认取帧器走 `DeviceManager.screenshot("adb")`，与 `GET /api/device/screenshot` 同一条路；
>   错误一律给明确错误码，不返回空结果）；14 例单测。
> - 已做：任务链：`maa_api/core/depot_selfscan_overlay.py` 生成 `MaaApi@DepotSelfScan.json`
>   （`@Start` / `@AllTab` / `@EnterDepot` / `@SwipeNextPage` / `@SwipePrevPage` / `@Done`）。
>   **没有** `@AllTabSelected`：M44 那类选中态模板死在尺寸上，页面对不对由 `min_accepted` 回答。
> - 已做：设置项：`account.depot_scan_{enabled,top1_min,margin_min,min_accepted}`，默认
>   `false / 0.75 / 0.15 / 8`。
> - 已做：多屏取证：**竖向滑动无效、横向一划一页**（几何照内核 `DepotSlowlySwipeToTheRight`）；
>   格距可复用，**横向相位逐屏漂 ±30 px（实测 78–114 vs 先验 86.6），必须逐屏重拟合**，
>   且 `FIRST_SEARCH_MAX = 24` 兜不住（q02 那帧复用先验格线 0/24，重拟合后 11 接受）。
> - 未做：**数量整块挪到 W3**（不在 W2 半做：24 个 `NumberOcrReplace` 的 roi 要按当屏拟合出的
>   格线现算，翻页后必须动态换 roi；加上内核 OCR 前的 `subtract(item_templ * 0.41)` 任务 JSON
>   做不到，准确率必然低于内核。理由写进台账 W2 节 D。

- **为什么**：W1 的识别器只吃图；要有人把图送到它面前，还要把数量补上。
- **写范围**：
  - `resource/maa-layers/custom/resource/tasks/MaaApi@DepotSelfScan.json`（**新增**，全部用 `MaaApi@DepotSelfScan@…` 前缀，**不覆盖内核 `Depot*`**）：
    - `@GotoDepot`：进仓库页（复用内核 `DepotBegin` 的导航或 ClickRect 兜底）
    - `@ClickAllTab`：点「全部」页签（`roi [450,0,300,138]`，`postDelay` 足够）
    - `@AllTabSelected`：确认「全部」已选中（选中态模板，**尺寸必须 ≤ roi**，W0 的 600×276 教训）
    - `@Done`：链尾出口（`JustReturn` / `Stop`）
    - `@Qty<col><row>` × 24：`OcrDetect` + `baseTask: NumberOcrReplace`，每格一个 roi，`maxTimes: 1`
  - `maa_api/services/account/depot_scan_chain.py`（新，链的提交与取帧编排）或并入 `depot.py`
  - `maa_api/services/account/depot.py`（合并两路结果 + 数量分批策略）
  - `tests/services/test_account_depot_scan_chain.py`
- **做法**：
  1. 一条 `Custom` 链把设备带到「全部」页并停住（不点任何道具）；Python 侧用 `MaaApi` 现有的截图通道取帧；
  2. 多屏：`@SwipeUp` 滑动后重新取帧，**每次滑动后重新做网格定位**（不假设滑动量精确）；维护已见 id 集合，连续两屏新增为空就停；
  3. 数量分批：与内核 `DepotInfo` 重叠的 id 直接用内核数量；只有我们独有的 id 才用 24 个 `@Qty*` 节点 OCR；OCR 无结果就**不写数量**（不写 0）；
  4. 数量节点必须在取帧之后、页签未变的前提下跑（否则 roi 与内容对不上）。
- **验收**：真机跑完整链路一轮 → 内核日志里能看到 `MaaApi@DepotSelfScan@*` 的命中记录、`@Qty*` 节点的 OCR 文本；离线回放同一批帧得到同一结果。
- **blocked_by**：W1

### W3a 合并入库与页面展示（不占设备）—— **已完成（2026-10-13）**

> **W2 交过来的三件事**：前两件在 W3a 做完，第三件（数量）整块留 W3b。
> 1. **数量（整块从 W2 挪来）** ⇒ **W3b**：24 个 `NumberOcrReplace` 节点的 roi 要按**当屏
>    拟合出的格线**现算（横向相位逐屏漂 ±30 px，写死 roi 会整体错位），翻页后要能换一批
>    roi；与内核 `DepotInfo` 重叠的 id 用内核数量，只有我们独有的 id 走自己的 OCR，
>    OCR 读不到就**不写数量**。
> 2. **逐屏扫描的收口** ⇒ **W3a 已做**：一页扫完再翻页（`@SwipeNextPage`），**连续两屏
>    没有新增 id 就停**（横滑到底会进干员信物区，那里的格线不是 8 列 × 129.92，拟合会
>    `insufficient`）；另有页数上限 10 与「翻页失败只收手、不作废已扫到的屏」。
> 3. **`fit_lattice` 的搜索半径放宽** ⇒ **W3a 已做**：`FIRST_SEARCH_MAX` 24 → 40 px
>    （实测偏移 +27.7 px、逐屏极差 36 px）；同时发现「格距与相位一起搜」会把格距带偏，
>    改成**只量相位、格距锁先验**（理由与实测数字见台账 W3a 节）。整轮闸门按**聚合结果**
>    判，不按屏判（末页接受格数会低到 7）。

- **为什么**：识别出来要落库、要在页面上看得见，而且**不能因为新链路把 M44 的护栏绕过**。
- **写范围**：
  - `maa_api/services/account/depot.py`（按 id 并集合并：有数量的一方优先；都有取我们的；都没有则数量留空）
  - `maa_api/services/account/depot_scan.py`（未知道具的记录格式）
  - `maa_api/db/…`（若需要在 `account_depot_item` 记「未知道具数」；能不加列就不加）
  - `web/src/features/account/DepotTab.tsx`（新增分类的正常展示、缺失数量显示「—」、未知道具的如实呈现）、`.test.tsx`
  - `tests/services/test_account_depot*.py`、`tests/api/test_account_*`
  - 生成物：`scripts/dump_openapi.py` → `pnpm gen:api`
- **做法**：
  1. 合并规则见规格 §4.5；同一 id 出现的两路结果都记进识别记录（便于排查）；
  2. **M44 下限护栏复用**：合并后的条目数低于阈值仍然整轮不写；再加一层 W1 的「接受格数下限」，两层都拦住才写；
  3. 页面：新出现的分类（凭证/寻访/家具/货币/消耗品）正常显示；数量缺失显示「—」而不是「0」；未知道具显示「未知道具 ×N（最高相似度 0.xx）」，不猜 id。
- **验收（W3a）**：单测覆盖「两路并集」「同 id 数量取舍（取内核）」「护栏拦住」「数量缺失不写 0」「未启用时行为不变」「逐屏扫描的四种停止」；前端用例断言「—」「来源标签」「未识别清单」；`pnpm build` 后在页面上肉眼确认。
- **blocked_by**：W2

### W3b 数量读取与真机重验收（占设备一轮）—— **已完成（2026-10-13）**

> **实施结果**（详见台账 W3b 节与规格 §4.7）：
> - **数量 ⇒ 实测后裁决不做**：探针两轮（6 格 × 3 变体；生产形状 roi 整页 24 格）。
>   手工微调的 roi 上纯数字 15/15 对、万/亿缩写 3/3 错；**生产形状 roi 13 对 / 6 错 /
>   5 读不出**，含 `7002 加急许可 1302 → 302`（分数 0.9995）—— 门槛拦不住。自识别独有的
>   id 一律写 `NULL`（页面「—」），理由与数字见规格 §2.6、台账 W3b-A。
> - **`DepotChainPager` 完成判据** ⇒ 已接内核 `TaskChainCompleted`（超时退回等落定；
>   链报错抛 `DepotScanError`），单测 4 条。
> - **真机端到端** ⇒ 条目数 77 → **96**（内核 77 + 自识别 19）；`7003/4004/4005/3141/
>   7001/7002` 全部进库；`7004/3401` 本账号没有。途中修掉一个真机崩溃
>   （收尾读 `PIL.Image.shape`，8 屏结果全丢）。
> - **设备争抢 ⇒ 会抢**（同流水线下一个任务 1 s 后就占设备，翻页提交被拒 →
>   `page_turn_failed`，独有 id 19 → 11）⇒ `_submit()` 退避重试。
> - **M44 重验收** ⇒ `Depot.next` 的 A/B 证明覆盖**逐 key 生效**；但它在「从主界面进仓库」
>   这条正常路径上必然匹配失败（模板只在未选中态匹配）⇒ 空 `DepotInfo` + `TaskChainError`。
>   **删/改 M44 覆盖层的裁决留给 W4。**

- **为什么**：W3a 只把身份补齐了，`7003` 这类自识别独有的道具数量还是空的（页面显示「—」）；
  M44 留下的真机验收欠账也要在同一轮还掉。
- **写范围**：`maa_api/core/depot_selfscan_overlay.py`（24 个 `@Qty<col><row>` 的
  `OcrDetect` + `baseTask: NumberOcrReplace`，roi 按当屏格线现算或分批生成）、
  `maa_api/services/account/depot_scan_service.py`（翻页后换 roi、`DepotChainPager` 的
  完成判据接内核 `TaskChainCompleted`）、`tests/**`、account 真机验收记录。
- **做法**：数量分批照规格 §2.6——与内核 `DepotInfo` 重叠的 id 直接用内核数量；
  只有我们独有的 id 走自己的 OCR；OCR 无结果就**不写数量**（不写 0）。
- **验收（已完成）**：条目数 77 → 96（内核 77 + 自识别 19），`7003/4004/4005/3141/7001/7002`
  全部进库、5 个基础道具数量与内核 `DepotInfo` 一致；数量读不到/不做的仍显示「—」。
- **blocked_by**：W3a

### W4 文档、门禁、真机验收、合并打 tag（占设备一轮）—— **已完成（2026-10-13；合并与 tag 留给主 agent）**

> **实施结果**（详见台账 W4 节与 `docs/13` §8.67）：
> - **删掉 M44 的覆盖层**（用户批准）：`depot_overlay.py` 不再生成任何 `Depot` /
>   `DepotAllTab` 覆盖，内核恢复自带的 `Depot.next = ["DepotMaterialTab"]`；模块只剩
>   「每次启动删掉老检出上残留的 `MaaApi@DepotAllTabs.json`」这一步（文件是启动期生成的、
>   不进版本库，只删生成代码老检出上仍会被内核加载）。真机复验：`to_be_recognized`
>   回到内核值、整轮识别非空、custom 层里不再有那个文件。
> - **写库加「相对上一轮的下降护栏」**：`agent.depot_drop_min_prev`（默认 20）+
>   `agent.depot_drop_ratio`（**默认 0.75**，不是 0.7 —— 96 × 0.7 = 67.2 < 69，
>   0.7 拦不住实测的 96 → 69），命中时整表不动、返回 0、warning + 识别记录
>   `detail.skipped`；`ratio=0` / `min_prev=0` 关闭。单测 4 条。
> - 文档：`docs/04`（两道写库闸）、`docs/12`（M45 小节）、`docs/13`（§8.64 补 W4 裁决与
>   下降护栏、§8.66 五、4 收口、新增 §8.67）、`docs/README.md`（决策速查表）、本文件与规格。
> - 门禁：后端全量、`dump_openapi.py --check`、前端 `typecheck` / `test --run` / `build`、
>   `copy_inventory.py --write-baseline && --check`（尾行见台账 W4 节）。

- **为什么**：收口。
- **写范围**：`docs/04`、`docs/05`、`docs/09`、`docs/12`（M45 小节）、`docs/13`（§8 新条目 + **更正 §8.64 里 26→77 的说法**）、`docs/README.md`（决策速查表）、`docs/copy-audit/*`、`maa_api/core/depot_overlay.py`（修正 :47-48 的错误注释）、台账回填。
- **顺序**：文档 → `scripts/dump_openapi.py`（不带 `--check` 重生成）→ `cd web && pnpm gen:api` → 全门禁（后端 `pytest -q`、`dump_openapi.py --check`、`generate_api_guide.py --check`、`generate_task_types_fixture.py --check`、`pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`、`frontend_smoke.py`）→ `scripts/copy_inventory.py --write-baseline && --check` → 真机验收 → 台账回填 → `git merge --no-ff` 回 `refactor/v2` → tag `v2-m45`。
- **真机验收**：跑完整链路，`account_depot_item` 里出现 `7003/4004/4005/3141`（以及本页实际有的其余新 id），5 个基础道具数量与内核 `DepotInfo` 一致；再故意构造一个「接受格数不足」的结果确认护栏不写库。
- **两处必须在文档里写明**：① M44 的 `DepotAllTabClicked` 是死代码；② 26→77 是内核不稳定，不是页签覆盖的功劳（M44 W1 的验收结论要改）。
- **blocked_by**：W1、W2、W3

## 裁决记录（W0 期间自行决定、不影响对外行为）

| # | 裁决 | 理由 |
| --- | --- | --- |
| 1 | M45 用**自己的任务名 + `Custom`**，不覆盖内核 `Depot*` 节点 | 覆盖机制虽已证实可用，但 M45 要的是与内核那趟**并存**（内核仍是 95 个材料的稳定来源），覆盖会改掉内核链行为 |
| 2 | M45 **不删** M44 的覆盖层 | 不在本轮范围内；删不删留到 W4 一并裁决，且 M45 链路不依赖它。**W4 已裁决：删掉**（用户 2026-10-13 批准，W3b 真机 A/B 证明它在正常路径上把整轮识别打挂），理由见 `docs/13` §8.64 与台账 W3b-D |
| 3 | 数量先只做「分批补」，OCR 失败就不写 | 内核 OCR 前的模板相减（`*0.41`）在任务 JSON 里做不到，宁可缺数量也不写假数字 |
| 4 | 阈值默认 `top1 ≥ 0.75 且 margin ≥ 0.15` | W0 实测：单看分数分不开（真格最低 0.7876 < 半格伪峰 0.7907），加 margin 后真格 ≥0.1664 > 半格 ≤0.1540 |
| 5 | W1 的 fixture 用 W0 的两张真截图 | 有真值可对（肉眼 + M44 已有的 5 个基础道具数量），比合成图可信 |
