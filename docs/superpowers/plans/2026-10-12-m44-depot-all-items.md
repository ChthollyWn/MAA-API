# M44 计划：仓库识别覆盖全部页签 + 道具图

- 规格：`docs/superpowers/specs/2026-10-12-m44-depot-all-items.md`
- 分支：`m44-depot-all-items`（从 `refactor/v2` 新建）
- 前置：M27 仓库识别（`DepotInfo` + 整表替换）、M43 资源代理（`GameAssetService`）
- 设备占用：**需要真机两轮**（W1 页签链实验、W4 端到端验收），其余波次不占设备

## 波次

### W0 事实取证（已完成，不占设备）

- 为什么：先搞清楚 MAA 到底扫了哪些页签、能拿到什么、缺什么。
- 结论（规格 §2）：内置链只点「养成材料」页签（`Depot → DepotMaterialTab`）；一次真机识别 26 件（21 材料 + 龙门币/至纯源石/合成玉/赤金/采购凭证）；顶部数值后三位被截断；**整表替换把 77 行冲成 26 行**；道具图 `item/{icon}.png` 实测 200。
- 写范围：规格 §2（只落文档）。

### W1 页签链真机实验 + 实现（占设备）

- 为什么：规格给了 A/B 两个候选，哪条成立只有真机说了算。
- 写范围：`resource/maa-layers/`（新增/修改覆盖层 JSON）、`maa_api/services/account/depot.py`、`maa_api/services/pipeline_runner.py`（若需要按页签提交多次识别）、`tests/services/test_account_depot*.py`、`docs/13`。
- 步骤：
  1. 真机：进仓库截图 → 记录页签栏的**页签名字与 roi** → 分别点每个页签再截图；
  2. 跑一次内置 `Depot`，记录 `DepotInfo` 的 id 集合与页面所见是否一致；
  3. **候选 A**：在层里覆盖 `DepotMaterialTab` 为「点『全部』页签」，再跑一次 `Depot`，看 id 集合是否覆盖全部页签；成立就采用 A（改动最小、无需改合并语义）；
  4. **候选 B**（A 不成立时）：自定义链依次点页签 + 每个页签一次识别，`DepotService` 改成按页签合并；
  5. 把实验证据（截图路径、id 集合对照）写进台账。
- 验收：一轮识别覆盖全部页签（含 `7003/7004`），且 `DepotInfo` 的 id 集合与页面所见一致。
- blocked_by：W0

### W2 下限护栏（不占设备）

- 为什么：77 → 26 的覆盖是实测事故，M44 必须堵住。
- 写范围：`maa_api/services/account/depot.py`、`maa_api/settings.py`（`account.depot_min_items`）、`tests/services/test_account_depot.py`、`docs/04`。
- 做法：`replace_all` 增加下限判断，低于阈值**整轮不写**并记 warning；阈值可配（默认 20，`0` ＝关闭），写进 `settings_schema` 让页面可改。
- 验收：单测构造 3 件识别 → 表不变 + 记录有跳过原因；构造 30 件 → 正常写入。
- blocked_by：无（可与 W1 并行，但改同一个文件要串行）

### W3 道具图代理 + 仓库页展示（不占设备）

- 为什么：用户明确要求「展示带道具图片」。
- 写范围：`maa_api/services/account/assets.py`（`ASSET_KINDS` 加 `item`）、`maa_api/api/routers/account.py`（`kind` 文档）、`web/src/features/account/DepotTab.tsx`、`web/src/features/account/assets.ts`、`web/src/features/account/DepotTab.test.tsx`、`tests/services/test_account_assets.py`、`tests/api/test_account_assets_router.py`、生成物。
- 做法：
  1. 后端 `kind=item`（上游 `item/{icon}`），`icon` 由服务端从本机 `item_index.json` 的 `icon` 字段解析——接口只收 `item_id`，不让前端拼文件名（对不了图标名）；
  2. 前端仓库页：图标 + 名字 + `× 数量` + 分类；龙门币/合成玉/至纯源石标「约」；
  3. 图标缺失 → 占位，不留破图。
- 验收：`tests/services/test_account_assets.py` 增加 `item` 分支；前端用例断言图标 src 与「约」；`pnpm build` 后在真机页面上肉眼确认一轮。
- blocked_by：无

### W4 文档、门禁、真机验收与合并（占设备一轮）

- 写范围：`docs/04`、`docs/05`、`docs/09`、`docs/12`、`docs/13`（§8.64）、`docs/README`、`docs/copy-audit/*`、台账。
- 顺序：文档 → `dump_openapi.py` → `pnpm gen:api` → 全门禁 → 台账回填 → `--no-ff` 合并回 `refactor/v2` → tag `v2-m44`。
- 真机验收：跑一轮仓库识别 → 仓库页看到全部页签的道具与图标；再故意构造一次小结果确认护栏不覆盖。
- blocked_by：W1、W2、W3
