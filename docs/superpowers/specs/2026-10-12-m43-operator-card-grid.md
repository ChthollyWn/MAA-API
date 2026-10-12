# M43：干员页换成卡片网格（一图流风格）+ 显示模组类型

- 里程碑：M43
- 分支：`m43-operator-card-grid`（从 `refactor/v2` 新建）
- 状态：待实现
- 上游：M27 账号数据（干员表与编辑抽屉）、M42 森空岛同步（练度与模组数据的来源）

## 1. 来源与目标

用户 2026-10-12 发来一并流（yituliu）干员 box 的截图，原话：

> 我希望 maaapi 的干员展示页也这么好看且明了，同时他是能够显示模组类型的，参考他的代码

并给出两个参考仓库：`Arknights-yituliu/frontend-v2-plus`（Vue，128★）与 `Arknights-yituliu/BackEndV3`（**Java 17 / Spring Boot 3**，71★，无 LICENSE）。

目标口径：账号页「干员」从**表格行**换成**卡片网格**——每张卡一眼看出头像、中文名、精英化/等级/潜能、三个技能的专精、以及**模组的类型与等级**（如 `CCR-X`、`Δ`）；未拥有的干员整体灰显。同时把「模组类型」这条数据链补齐（M42 收口时它还是 `NULL`）。

## 2. 事实基础（本机实测 + 两个参考仓库核对）

### 2.1 现状（MAA-API）

- `web/src/features/account/OperatorsTab.tsx` 用 `Section` + 行列表（`FieldCell` 纯文本），没有图片；分页 `useInfiniteQuery`（每页 `OPERATOR_PAGE_SIZE`），筛选条、识别状态条、森空岛绑定卡、差异条与编辑抽屉都在同一页。
- 契约 `account_operator`（`docs/04` §5.18）有 `modules: {"X": 2}`，但**没有模组 id / 分支码 / 显示名**，也**没有技能 id** ⇒ 现在这两样都画不出图标与 `CCR-X` 标签。

### 2.2 资源 URL（本机实测 HTTP 200）

| 资源 | URL 模板 | 实测 |
| --- | --- | --- |
| 干员头像 | `https://web.hycdn.cn/arknights/game/assets/char/avatar/{charId}.png` | 200，65 KB PNG |
| 技能图标 | `https://web.hycdn.cn/arknights/game/assets/char_skill/{skillId}.png` | 200，17 KB PNG（`skchr_amiya_3`） |
| 立绘 | `https://web.hycdn.cn/arknights/game/assets/char/portrait/{urlencode(skinId)}.png` | 本轮不做（只需小头像） |
| **模组图标** | 官方 CDN **没有**这类资源：试过 `assets/equip/icon/…`、`assets/equip_icon/…`、`assets/uniequip/icon/…`、`assets/equip-icon/…`、`assets/char_equip/…`、`assets/skill_icon/…` 六个路径，`{typeIcon}.png` 全部 404 | yituliu 用自有 CDN `https://cos.yituliu.cn/arknights/equip-icon/{typeIcon}.png`（694 B，实测 200） |

### 2.3 模组类型与分支码：**森空岛响应里就有，不需要新数据源**

- 拆包 `uniequip_table.json`（`torappu.prts.wiki/gamedata/latest/excel/`，与仓库既有镜像同源）的字段：`equipDict.<id>.typeName2` = `X/Y/D/A/B`（权威类型字母）、`.typeName1` = 分支码（`SOL` / `EXE` / `ISW` / `RA` / `CCR` …）、`.uniEquipName` = 显示名、`.uniEquipIcon` / `.typeIcon` = 图标名、`type = INITIAL|ADVANCED`（`INITIAL` 即 `uniequip_001_*` 证章）、`equipTypeInfos` 给枚举与 `sortId`（`B` 为特限）。
- **森空岛 `player/info` 的 `equipmentInfoMap` 每条就是这份表的子集**：`{id, name, shiningColor, typeIcon}`，其中 `typeIcon` 形如 `ccr-y`（分支码小写 + `-` + 类型字母小写），证章为 `original`。
- **交叉验证**：本轮 W0 真机取样里 `uniequip_001_absin` 的 `typeIcon` 是 `original`，与拆包表里同一 id 的值**逐字相同**；拆包表全表 911 条的 `typeIcon` 分布是 `original 398 / x 301 / y 183 / a 14 / d 6`，另有 9 条异常写法（`ra-a`、`so-a`、`so-b`、`dec-X`、`WAH-Y`，含大小写与两位分支码）。
- 结论：`module_type_of()` 改成从 `typeIcon` 解析（大小写不敏感、分支码 1–4 位），`original` = 基础模组（不进 `modules`），解析不出才计 `dropped`；拆包表**本轮不引入**（没有它也能得到类型与标签；只在需要显示名/图标顺序时才需要，见 §3.2）。

### 2.4 参考仓库的展示口径（只取事实，不复制代码）

- 模组类型显示映射：`X→X`、`Y→Y`、`D→Δ`、`A→α`、`B→β`；**只显示 level > 0 的模组**（`OperatorCard.vue` 的 `moduleDisplay`）。
- 卡片配色按稀有度：6★ `#FF6B35/#FF9800`、5★ `#E5C100/#FFD700`、4★ `#A855F7/#C084FC`、3★ `#3B82F6/#60A5FA`、2★ `#6EE7B7/#A7F3D0`、1★ `#9CA3AF/#D1D5DB`、未拥有 `#4B5563/#6B7280`；卡片左侧一条稀有度色带、右上角星级徽章、顶部职业图标、左下潜能/精英化/等级、右下技能（图标 + 专精数字）与模组（图标 + `类型-等级`）。
- 卡片尺寸 90px（移动端更小），网格间距 12px，`max-width: 600px` 断点。
- 头像在他们那里是 CSS 精灵图（`sprite-avatar bg-<charId>`，180px 基准缩放）；**MAA-API 不引入精灵图管线**，直接用官方 CDN 的单张头像。
- **许可**：`BackEndV3` 全仓无 LICENSE，`frontend-v2-plus` 是 NOASSERTION ⇒ 只参考「字段路径 / 响应形状 / 视觉口径」这类事实，不复制代码与数据产物；文档里注明参考来源。

## 3. 范围

### 3.1 本里程碑做

0. **W0 事实取证**（§2，已完成并写进本文件）。
1. **数据层**：迁移 `0020_operator_card_fields` 给 `account_operator` 加两列可空 JSON：`skill_ids`（按槽位的技能 id 列表）与 `module_details`（`[{type, level, equip_id, branch, label, type_icon}]`）。
2. **映射**：`services/skland/mapping.py` 从 `typeIcon` 解析类型字母与分支码，产出 `module_details`；`skills[].id` 按槽位记进 `skill_ids`；`modules`（`{"X": 2}`）语义不变（计划层与手动编辑继续用它）。
3. **契约**：`AccountOperatorRow` 增加 `skill_ids` / `module_details`（可空），重新生成 `web/openapi.json` 与 `web/src/types/api.d.ts`；森空岛同步写入这两列。
4. **前端**：新增 `OperatorCard`（头像、中文名、稀有度色带、星级、职业、潜能/精英化/等级、技能图标 + 专精、模组标签 + 等级）与 `OperatorGrid`（响应式列数）；`OperatorsTab` 的列表段换成网格，**保留**识别状态条、森空岛绑定卡、差异条、筛选条、分页（「加载更多」）与点卡开编辑抽屉。
5. **未拥有**：`own=false` 的行整体灰显（去色 + 降低不透明度），不显示技能/模组。
6. **加载与失败兜底**：图片 `loading="lazy"` `decoding="async"`；图片加载失败只留占位，不影响布局与信息。
7. **测试**：映射单测（typeIcon 各种写法/证章/异常）、同步写入、契约形状、前端卡片与网格用例（含未拥有灰显、专精与模组标签、图片 URL 拼接）。
8. **文档**：docs/04（两列）、docs/05（行字段）、docs/09（账号页卡片段）、docs/11（`get_operator_box` 返回值描述）、docs/13 §8.63、docs/12 M43 小节、台账与文案基线。

### 3.2 本里程碑不做

- **不做模组图标**：官方 CDN 没有该资源（§2.2 六条路径全 404），yituliu 的自有 CDN 无可再分发许可、热链又引入第三方与离线双重问题。模组用**文字标签**（`CCR-X`、`Δ`）+ 类型字母配色表达。
- **不做精灵图 / 自建图片管线**，不下载或缓存干员美术资源到仓库。
- **不引入 `uniequip_table.json`**（§2.3 已证明不需要）：只有当以后要显示模组**全名**、排序或图标时才考虑，届时按 M42 的老规矩先问用户。
- **不改** `modules` 的语义与 `PATCH` 校验（模组仍按 `X/Y/D/A` 手动填），不做模组「装了哪个」的切换写操作（M42 §3.2 的老口径）。
- **不做立绘大图/皮肤**、不做卡面拖拽排序、不做导出。

## 4. 设计

### 4.1 数据层（迁移 `0020_operator_card_fields`）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `skill_ids` | JSON、可空 | `["skchr_amiya_2", "skchr_amiya_3"]`，**按槽位顺序**；只有森空岛同步写得出来（`OperBox` 与手动编辑都没有 id），没有时 `NULL`＝未知 |
| `module_details` | JSON、可空 | `[{"type":"Y","level":3,"equip_id":"uniequip_002_amiya","branch":"ccr","label":"CCR-Y","type_icon":"ccr-y"}]`；`type` 为空表示基础模组（`original`）时不入库；没有同步过为 `NULL` |

两列都不回填（老行保持 `NULL`），downgrade 直接删列。`modules` 仍是 `{"Y": 3}`（计划层与手动编辑的契约），同步时两处一起写。

### 4.2 映射规则（`services/skland/mapping.py`）

1. `module_type_of(equip_id, equipment_info_map)` 改为：取该 id 的 `typeIcon`，用 `^([a-z0-9]{1,4})-([a-z])$`（大小写不敏感）解析出 `branch` 与字母；字母须落在 `MODULE_TYPES`（`X/Y/D/A`）里，`B` 或未知名（如 `so-b`）**不写入该键**并计一次 `dropped`（`MODULE_TYPES` 不动，避免牵动 PATCH 校验与前端约束）。
2. `typeIcon == "original"` ⇒ 基础模组：不进 `modules`、不进 `module_details`，也**不计** `dropped`（那是正常状态）。
3. `locked=true` 或等级越界：不进 `modules`；`module_details` 里也跳过（与「确认没装」一致）。
4. 非空 `equip` 一个可解析项都没有 ⇒ `modules` 仍返回 `None`（不知道），这是 M42 的既有护栏；`module_details` 同理返回 `None`。
5. `skill_ids`：按 `skills[]` 顺序取 `id`（上限 3，超出计数丢弃）；`skills` 不是列表 ⇒ `None`。
6. 解析不出来时 `label` 也按 `type_icon.upper()` 原样给出（例如 `dec-X` → `DEC-X`），只有连 `typeIcon` 都没有才不给 `label`。

### 4.3 契约（wire）

`AccountOperatorRow` 增两个字段（都可空）：

```json
{
  "modules": {"Y": 3},
  "module_details": [
    {"type": "Y", "level": 3, "equip_id": "uniequip_002_amiya", "branch": "ccr", "label": "CCR-Y", "type_icon": "ccr-y"}
  ],
  "skill_ids": ["skchr_amiya_2", "skchr_amiya_3"]
}
```

`get_operator_box`（Agent 工具）与前端类型都跟着生成；文档同步更新字段清单（docs/05 §6.20）。

### 4.4 前端

- `web/src/features/account/OperatorCard.tsx`（新）：
  - 顶部：稀有度色带 + 星级徽章 + 职业图标（用本地已有的「中文职业」文案，不引职业图标资源）；
  - 中部：头像（官方 CDN，`charId` 拼接；`own=false` 时灰显且不加载）；
  - 底部：名字、（精二/精一/精零徽章）等级、潜能；右侧两行——技能（每个槽位一个图标 + 专精数字，专精 0 用弱化样式）与模组（`label` 文本 + 等级，类型字母做小徽章色）。
  - 无 `skill_ids` / `module_details`（老数据或 OperBox-only）时降级：技能只显示槽位与专精数字（不显图标）、模组只显示 `X 3` 这种字母 + 等级。
- `web/src/features/account/OperatorGrid.tsx`（新）：`grid` 布局（移动 2 列 → 平板 3–4 列 → 桌面 6 列），点击卡片回调 `onSelect(row)`（打开既有编辑抽屉）。
- `OperatorsTab.tsx`：把 `ul` 行列表换成 `OperatorGrid`，条目数、注意保持既有 `data-testid` 或补充新测试；分页按钮文案不变。
- 新增 `formatModuleLabel(detail)` / `moduleTypeGlyph(type)`（`X→X / Y→Y / D→Δ / A→α`）等纯函数，便于单测。
- `skillIconUrl(skillId)` / `avatarUrl(charId)` 放在 `features/account/assets.ts`，集中一处便于以后换源。

### 4.5 资源与离线的取舍

图片来自**官方 CDN**（`web.hycdn.cn`，鹰角自己的域名），浏览器直接 `<img>` 加载：无需后端代理、无新增密钥；代价是 PWA 离线或该域名不可达时只是没图（信息与布局不受影响）。这一点写进 §5 风险与 docs/09。

## 5. 兼容与风险

| 风险 | 处理 |
| --- | --- |
| `typeIcon` 形状变化（上游换写法） | 解析失败只影响该模组：`modules` 保持 `NULL`（不知道）并计 `dropped`，绝不写错类型；异常写法已有用例表 |
| `B` / 未知字母模组被丢掉 | 记 `dropped_invalid` 与日志；`MODULE_TYPES` 不扩（会牵动 PATCH 校验与前端约束），文档记明 |
| 图片 CDN 不可达 / 离线 | 只缺图，不阻塞渲染；卡片布局用固定尺寸占位 |
| 技能 id 只在森空岛同步后才有 | 老数据/纯 OperBox 行降级成「无图标、只有槽位与专精数字」，不伪造图标 |
| 385 名干员 + 每人 3–5 张图 | 每页 50 张卡、`loading="lazy"`、`decoding="async"`；分页沿用「加载更多」 |
| 参考仓库无许可 | 只复用事实（字段路径、颜色、断点），不复制代码/数据产物；docs/13 记明出处 |

## 6. 文档同步

| 文档 | 改动 |
| --- | --- |
| `docs/superpowers/specs/2026-10-12-m43-operator-card-grid.md` | 本文件 |
| `docs/superpowers/plans/2026-10-12-m43-operator-card-grid.md` | 新增计划 |
| `docs/superpowers/ledgers/2026-10-12-m43-operator-card-grid.md` | 实施台账 |
| `docs/13-决策记录.md` | 新增 §8.63（卡片网格与模组类型的口径、参考来源与许可说明、`B` 型不扩枚举） |
| `docs/04-数据模型与持久化.md` | `account_operator` 加 `skill_ids` / `module_details` 两列 + 迁移 `0020` |
| `docs/05-API规范与路由清单.md` | §6.20 干员行字段清单补两项 |
| `docs/09-前端重构方案.md` | 账号页干员段：表格 → 卡片网格、资源来源与离线降级 |
| `docs/11-Agent模块设计.md` | `get_operator_box` 返回字段说明补两项 |
| `docs/12-实施计划与里程碑.md` | M43 小节与验收结论 |
| 生成物 | `web/openapi.json`、`web/src/types/api.d.ts`（先 `dump_openapi.py` 再 `pnpm gen:api`） |
| `docs/copy-audit/*` | 文案基线重设 |

## 7. 验收

1. 映射：`typeIcon` 的六类写法（正常 `ccr-y`、大写 `dec-X`、两位分支 `ra-a`、未知字母 `so-b`、证章 `original`、缺失）各有用例；`skill_ids` 按槽位且超 3 截断。
2. 同步：一轮森空岛同步后，库里出现 `skill_ids` 与 `module_details`；`modules` 与 `module_details` 的类型集合一致。
3. 契约：`GET /api/account/operators/{char_id}` 返回新字段，`dump_openapi.py --check` 与 `pnpm gen:api:check` 当前。
4. 前端：卡片渲染头像/名字/精英化/等级/潜能/技能/模组；未拥有灰显；无 `skill_ids` 时降级；点卡开编辑抽屉；`pnpm typecheck` / `test --run` / `build` 全绿。
5. 门禁：`.venv/bin/python -m pytest -q`（只有既有真资源与抖动失败）、`scripts/frontend_smoke.py`、`scripts/account_smoke.py`、`scripts/copy_inventory.py --check`。
6. 真机（用户）：账号页能一眼看出每名干员的练度与模组类型；`m43-operator-card-grid` `--no-ff` 合并回 `refactor/v2` 并打 `v2-m43`。
