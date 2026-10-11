# M40 规格 · 全量扫描的身份核对（详情页拉丁名）

- 日期：2026-10-11
- 里程碑：M40（接 M39 的两类练度扫描）
- 分支：`m40-identity-check`（自 `refactor/v2`，基点 `6abd5cc`）
- 规格：本文件；计划见 `docs/superpowers/plans/2026-10-11-m40-identity-check.md`；台账见 `docs/superpowers/ledgers/2026-10-11-m40-identity-check.md`
- 状态：实现、单测、真机抽取复核完成

## 1. 来源与目标

M39 按用户原话实现了「全量扫描」：进干员列表 → 点开第一格 → **不回列表**一路向左滑动，把列表里每个人的详情页读一遍。M39 的身份来源是**每屏开头的 16 格卡名**——屏内滑动的第 i 张被认为就是「车厢顺序第 i 格」那个人（`cell_of(index)`，列优先）。

2026-10-11 晚的整轮全量扫描（20:35 起、`screens=26`、`scanned=400`、`updated=230`，21:27 取消）暴露出这个身份假设会破：部分干员的练度是按**邻座**的读数写进去的。

- **决定性证据**：`char_502_nblade`（夜刀）是 2★ 干员，2★ 只有一个技能、没有模组，但库里存着 `skill_level 7`、`masteries {"1":0,"2":3,"3":3}`、`modules {"X":3}`；而 20:44:01 那次写入（三个字段同时变化）取自「读第 7 张详情页」那一帧，帧上的画面是**新约能天使**（6★ LV90、CHG-X 模组），两边的专精读数逐位相同。
- **机制**：详情页左滑是**甩动**，一次可能前进 ≥2 张，而 `frame_changed` 只能判「画面变没变」、判不出「变了几张」；再叠加「判成没动 → 重试一次」就可能多滑一张。屏内自某张起整体错位 1–2 张，直到下一屏重新点第一格才复位——这也解释了那 8 个「被写两次」的干员（错位时写了邻居读数、下一屏重叠重扫时又写回自己：`char_293_thorns`、`char_4042_lumen`、`char_401_elysm`、`char_243_waaifu`、`char_4046_ebnhlz`、`char_4072_ironmn`、`char_322_lmlee`、`char_4151_tinman`）。

**目标**：让全量扫描在**不回列表**（保持用户描述的滑动机制）的前提下，每一张都能自证「这一页是谁」；对不上就回列表重锚，重锚仍对不上就不写库。身份依据选**详情页左下角那行拉丁名**（`Nearl the Radiant Knight`），因为它是不随立绘变化、且能被内核 ASCII OCR 读出的纯文本。

**用户裁决**：用户 2026-10-11 晚对那轮正在跑的扫描说「让它跑完」（不取消、不动服务与设备）；本轮先把核对做成代码与单测，重跑一轮把错值刷回正确值的时间点另行征询。

## 2. 事实基础（真机取证）

### 2.1 拉丁名读得出来

探针 `scripts/probe_full_scan.py --step name`（内核节点 `OcrDetect` + `isAscii`，`DoNothing`，落在详情页左下角的拉丁名带上）在真机上逐格点开、只读这一行：

| 格 | 卡名 | 期望（`battle_data.name_en`） | 读到 | score | 生产口径 |
| --- | --- | --- | --- | --- | --- |
| top0 | 风笛 | `Bagpipe` | «Bagipipe» | 0.934 | 对得上 |
| top1 | 凛御银灰 | `SilverAsh the Reignfrost` | «SilverAsh tbo P» | 0.759 | 认不出 |
| top2 | 伊内丝 | `Ines` | «0» | 0.088 | 认不出 |
| top3 | 重岳 | `Chongyue` | «onvue» | 0.916 | 认不出 |
| top5 | 史尔特尔 | `Surtr` | «Surt» | 0.923 | 对得上 |
| top6 | 年 | `Nian` | «Niann» | 0.667 | 对得上 |
| top7 | 黍 | `Shu` | «Shu?» | 0.814 | 对得上 |
| bottom1 | 琴柳 | `Saileach` | «Saileach» | 0.986 | 对得上 |
| bottom2 | 赤刃明霄陈 | `Ch'en the Dawnstreak` | «Ch'enthe Dawnstreak» | 0.986 | 对得上 |

⇒ 生产口径 6/9 对得上，认不出的三格退回「按卡名写库」，**没有一次「命中别人」的误报**（这是它安全可用的前提）。

### 2.2 ROI 标定

内核 1280×720 基准，设备 2560×1440：

- 初版 `(8, 456, 510, 28)`：12 格对账只中 5 格，典型残读 `Ines`→`nes`、`SilverAsh`→`ilverAsh`（掉首字母）、`Nian`→`PTF`(0.26)。
- 定稿 **`(0, 452, 524, 32)`**：左边界放到 0、上下各留 4 px（白字带描边，横在中文美术字上方，不与 ★ 行重叠）；同一批格子干净了，代价是个别格子多一个字母（`Bagpipe`→`Bagipipe`）。残读交给模糊比对吸收。

### 2.3 读数形态

白字带描边的美术字，OCR 的失手方式是**掉字符**（首字母最常见）、**粘杂字**（`Shu` → `Shu?`）、**把 n 读成 nn**、偶尔整行读废（`0`）。score 与正确性只是弱相关（0.667 的年是对的、0.916 的重岳是错的），所以判定不能只卡 score。

### 2.4 期望值来源

`resource/maa-layers/repo/resource/battle_data.json` 的 `chars[*].name_en` 与干员目录同源。`own=1` 的 **384** 名干员**全部**有 `name_en`；最长的 `Skadi the Corrupting Heart`(26)、`Terra Research Commission`(25)、`Greyy the Lightningbearer`(25)、`SilverAsh the Reignfrost`(24)、`Eyjafjalla the Hvít Aska`(24)、`Exusiai the New Covenant`(24) 都落在 ROI 内。含非 ASCII 的有 `Eyjafjalla the Hvít Aska`、`Wiš'adel`、`Pozëmka`、`Varkáris`、`Nyamu Yūtenji`、`GALLUS²` ⇒ 比对前必须归一化（NFKD 去变音符 + 只留字母数字）。**个别干员的 `name_en` 直接是中文**（如「克莱门莎」）⇒ 这类没有拉丁名可用，退回卡名。

### 2.5 内核链尾节点的语义

`scripts/probe_training_scan.py:184-186` 的既有结论：**链上某个节点识别不出东西时整条链就结束**，后面的节点不会被尝试（`exceededNext` 也接不上）。所以拉丁名节点必须放在详情链**最末**，并且 `ELITE_SHORTCUTS`（精二走模组两节点、精一/精零直接跳尾）也要把它带上，否则精一/精零的人根本读不到这一行。

### 2.6 匹配阈值的离线实测

对 1048 条拉丁名做变形实验（去首字母、粘尾巴、换一个字母），用 `difflib.SequenceMatcher.ratio()` 归一化后比对：

- **99.7%** 的变形仍能**唯一**命中正确的名字。
- 失败样例集中在「本体与异格同名」：`rene` 会命中 `irene` 而不是 `arene`、`ubble` 会命中 `rubble` 而不是 `bubble` ⇒ 所以只跟**同一屏 16 格的期望名**比（候选集天然排除了大部分同族干扰），并要求**领先第二名足够多**、读数至少 3 个字符。

## 3. 范围

### 3.1 本里程碑

1. 内核节点 `MaaApi@OperatorScan@OperNameEn`（详情链末端、短路表带上）。
2. 干员目录 `OperatorMeta.name_en`（`battle_data` → 归一化后只留可用拉丁名）。
3. 比对：`_name_en_key` / `_name_en_hit`（同屏候选 + 相似度 + 领先余量）。
4. 编排接入：本屏第一格点开后的核对、屏内第 i>0 张的核对与「回列表重锚一次」、重锚后把新帧当作「滑动前」帧。
5. 对外契约：`GET /api/account/training-scan` 快照多 `realigned`（重锚过的张数）与 `unverified`（拉丁名读不出结论、按卡名写库的张数）。
6. 前端：两个计数进结果摘要（为 0 时不提）。
7. 文档：本规格、计划、台账、`docs/13` §8.56、`docs/12` M40、`docs/05` 端点表。

### 3.2 不做

1. **不改滑动机制**：仍然是「每屏读 16 格卡名 → 点开第一格 → 屏内连续左滑 15 次」，与用户 m00039 原话一致；本里程碑只给这条链加一次身份核对。
2. **不读中文美术字**：M36 实测 `OperBoxNameOCR` 读详情页大名字回「画」score 0.166，不做。
3. **不做整表先扫名**（M39 的 Design B），也不做逐格点开（真机更慢，约 6.3 s/张 vs 4.9 s/张）。
4. **不引入新依赖**：`difflib` / `unicodedata` 都是标准库。
5. **不改 `single` 模式**：名单扫描不变，两个新计数在单人模式下恒为 0。
6. **不在本轮清库或回滚**：那轮写错的练度值靠**再跑一轮全量**刷新（重扫到同一人时会用新读数覆盖），不写数据迁移脚本。

## 4. 设计

### 4.1 对外契约

`TrainingScanService.snapshot()` 多两个计数（与 `skipped` / `unresolved` / `mismatched` 一样是 **int**，不是列表）：

- `realigned`：屏内核对发现「这一页不是这一格那个人」，回列表重锚过的张数。
- `unverified`：这一页的拉丁名读不出结论（或这一格的卡名没读出来、目录里没有拉丁名），只能按卡名写库的张数。

### 4.2 内核节点

```
MaaApi@OperatorScan@OperNameEn = { algorithm: OcrDetect, isAscii: true, text: [],
                                   roi: [0, 452, 524, 32], action: DoNothing }
```

放在 `DETAIL_NODE_KEYS` 末尾；`Elite2 → (ModuleCode, ModuleStage, OperNameEn)`、`Elite1/Elite0 → (OperNameEn,)`；`operator_scan_tasks()` 的节点数从 43 变 44。**只读不算写**：`DoNothing` 不进任何写库路径。

### 4.3 目录侧

`OperatorMeta` 加 `name_en: str | None = None`；`load_operator_catalog()` 用 `_latin_name(entry.get("name_en"))` 取值——非 ASCII（`battle_data` 里那些中文 `name_en`）一律当**没有**拉丁名，不留半截可用性。

### 4.4 比对

```
_name_en_key(text)          # NFKD + 只留字母数字 + 小写（Wiš'adel → wisadel）
_name_en_hit(reported, expected: Mapping[int, str]) -> int | None
```

判定顺序：归一化后相等直接命中；否则对每个候选算 `SequenceMatcher.ratio()`，要求

- 最高分 ≥ `NAME_EN_MIN_SCORE = 0.7`；
- 最高分比第二名高 ≥ `NAME_EN_MIN_MARGIN = 0.15`（只有一个候选时跳过这条）；
- 读数长度 ≥ `NAME_EN_MIN_FUZZY_LENGTH = 3`（2 个字符的 `ne` 不猜；`Ines`→`nes` 这种掉首字母的要能认）。

### 4.5 编排接入

`_scan_screen(names, lookup, ambiguous, catalog)`：

1. `_expected_name_en(names, lookup, catalog)` 给「卡名读出来了、且目录里有拉丁名」的格子建期望表（下标＝车厢顺序，只覆盖这一屏）。
2. 第一格：原来的重试循环里加一条判据——读到的拉丁名如果命中**别人**（`_name_en_hit(...) not in (None, index)`），也算「点开的不是这一格那位」，重试。
3. 第 i>0 张：滑动 → 读详情 → `_confirm_card(index, row, column, detail, expected_en)`：
   - 期望为空、或读数认不出 ⇒ **原样返回**并 `unverified += 1`（按卡名写库，与 M39 行为一致）；
   - 命中别人 ⇒ `realigned += 1`、`_best_effort_back()` 回列表、`click_cell_node(row, column)` 重新点开这一格；重锚帧若命中自己就返回**新读数**，否则返回 `None`；
   - 返回 `None` ⇒ 这一张不写库：`scanned += 1` 并进 `skipped`（原因「详情页里的拉丁名跟这一格的卡名对不上，回列表重锚后仍对不上，读数不写库」）。
4. 重锚成功时把 `image` 换成**重锚那一帧**——它才是下一张的「滑动前」画面。

### 4.6 日志

- `全量扫描：第 %d 张画面里是「%s」（不是这一格卡名说的「%s」），回列表重新对齐`
- `全量扫描：第 %d 张重新点开还是对不上，这一张的读数不写库`
- `全量扫描：第 %d 张的拉丁名核对不出结论（读到 %r），按卡名写库`

### 4.7 成本

每张多一个 `DoNothing` 的 OCR 节点（同一次详情页识别里），实测不改变每张的步数结构；按 OCR 单次 0.1–0.2 s 估，整轮 384 张多 40–80 s（相对 45–60 min 可忽略）。重锚是异常路径：一次重锚 ≈ 回列表 + 点开 + 再读 ≈ 6–8 s。

### 4.8 前端

`TrainingScanState` 加 `realigned` / `unverified`（归一化兜 0）；结果摘要里为 0 不提，非 0 时追加 `重锚 N 张` / `身份未核 N 张`（单位是**张**，与 `scanned` 一致；`skipped`/`unresolved`/`mismatched` 沿用 M36 的「名」）。

### 4.9 降级与失败

- 拉丁名读不出 ⇒ **不拦**，按卡名写库并计数（保守：不因为一次 OCR 失手丢读数）。
- 拉丁名命中别人 ⇒ 重锚一次；重锚仍不对 ⇒ 丢这一张（写错比漏写坏）。
- 整屏卡名都读不出 ⇒ 期望表是空的，全屏按原口径走（等价于 M39 行为）。

## 5. 兼容与风险

- **契约只加不改**：两个新键是增量，老前端读不到也不炸（前端归一化对缺失键兜 0）。
- **误判风险**：判定要求领先第二名 0.15 且只在同屏候选内比较，宁可「认不出」（不拦）也不「认错」（拦）；真机 9 格里零误报。
- **修不到的面**：读不出拉丁名的那部分张数仍按卡名写库，漂移在这部分上观察不到（真机 3/9）。这是本轮的已知边界，靠 `unverified` 计数可观测；后续可以考虑「详情页职业/稀有度图标」做第二路信号（本轮不做）。
- **数据修复**：那轮 `updated=230` 里被写错的人，需要在修正版上**再跑一轮全量**刷新（用户常驻服务 8002 空闲时才跑；耗时 45–60 min）。是否现在跑、由谁发起（GUI 还是 Agent 工具）另行征询用户。
- **两台设备/两个实例**：本轮不改启动方式；`scripts/start-maa-api.sh` 已有端口护栏（`docs/13` §8.55），本轮的真机复核直接用用户常驻服务所在的那台设备。

## 6. 文档同步

| 文件 | 改什么 |
| --- | --- |
| `docs/superpowers/specs/2026-10-11-m40-identity-check.md` | 本文件 |
| `docs/superpowers/plans/2026-10-11-m40-identity-check.md` | W1–W5 分波与写范围 |
| `docs/superpowers/ledgers/2026-10-11-m40-identity-check.md` | 台账：裁决、实施中修掉的问题、真机数字、门禁数 |
| `docs/13-决策记录.md` | 新增 §8.56（来源与目标／事实／实现／护栏与负向对照／偏离与不做／验收） |
| `docs/12-实施计划与里程碑.md` | 新增 M40 小节（依赖、可交付状态、验证方式、验收结论） |
| `docs/05-API规范与路由清单.md` | `GET /api/account/training-scan` 行补两个计数 |

## 7. 验收

1. **单测**：`tests/services/test_account_training_scan.py` 新增 6 例（模糊匹配能认残读但不认同族、期望表只覆盖卡名可读的格子、核对一致照写、漂移时重锚并写重锚帧的读数、重锚仍不对就不写库、认不出时按卡名写库），并同步 `test_the_elite_shortcut_skips_the_other_badges` 与 `test_snapshot_shape_matches_the_spec`；前端 `trainingScanSummary` 的摘要用例加两个计数。
2. **真机抽取复核**：`scripts/probe_full_scan.py --step name --cells ...` 一屏 9 格，生产口径 ≥ 6/9 对得上、**零误报**（不得出现「命中别人」）。
3. **门禁**：后端全量 `pytest`、四处生成物 `--check`、文案基线 `--check`、前端 `typecheck`/`vitest`/`build`。
4. **合并**：`git merge --no-ff` 回 `refactor/v2` 并打 tag `v2-m40`。
