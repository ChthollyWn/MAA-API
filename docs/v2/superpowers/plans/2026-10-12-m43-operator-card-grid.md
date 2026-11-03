# M43 计划：干员页换成卡片网格 + 显示模组类型

- 规格：`docs/superpowers/specs/2026-10-12-m43-operator-card-grid.md`
- 分支：`m43-operator-card-grid`（从 `refactor/v2` 新建）
- 前置：M42 已合并（merge `ecce1a2`，tag `v2-m42`）；W0 事实取证已完成并写进规格 §2
- 设备占用：不占设备、不启动内核（纯数据层 + 前端）

## 波次

### W0 事实取证（已完成，不占设备）

- 为什么：卡片要画头像、技能图标与模组类型，先得确认这三样各自从哪来、有没有许可问题。
- 写范围：规格 §2（本波只落文档，不改代码）。
- 做法与结论：
  1. 官方 CDN 实测：头像 `web.hycdn.cn/arknights/game/assets/char/avatar/{charId}.png`（200/65 KB）、技能图标 `.../char_skill/{skillId}.png`（200/17 KB）；模组图标试了六个路径全 404。
  2. 模组类型：森空岛 `equipmentInfoMap[id].typeIcon`（`ccr-y` / `original`）与拆包 `uniequip_table.json` 的同一 id 值逐字相同 ⇒ **不需要新数据源**。
  3. 参考仓库：`BackEndV3` 无 LICENSE、`frontend-v2-plus` NOASSERTION ⇒ 只取事实（字段路径、配色、断点），不复制代码或数据产物。
- 验收：§2 的表格与结论可复现（URL 实测、`typeIcon` 分布、交叉验证）。
- blocked_by：无

### W1 数据层与映射（不占设备）

- 为什么：没有 `skill_ids` 与 `module_details` 就没有图标与 `CCR-X` 标签；这两列只能在同步时写入。
- 写范围：`maa_api/db/migrations/versions/0020_operator_card_fields.py`（新增）、`maa_api/db/models.py`、`maa_api/services/skland/mapping.py`、`tests/db/test_operator_card_fields_migration.py`（新增）、`tests/services/test_skland_mapping.py`。
- 做法：
  1. 迁移建两列（可空 JSON，不回填，downgrade 删列）。
  2. `module_type_of()` 改为解析 `typeIcon`：`^([a-z0-9]{1,4})-([a-z])$`（忽略大小写），字母须在 `MODULE_TYPES` 内；`original` 是基础模组（不进 `modules`、不计 `dropped`）；`B` 与未知名计一次 `dropped` 且不写该键。
  3. 新增 `module_details` 产出（`type` / `level` / `equip_id` / `branch` / `label` / `type_icon`，`label` 默认 `type_icon.upper()`）。
  4. `skill_ids` 按 `skills[]` 顺序（上限 3，超出计数）。
  5. 三处护栏保持：非空 `equip` 全解析不出 ⇒ `modules=None`；`skills` 非列表 ⇒ `masteries=None` 且 `skill_ids=None`。
- 验收：`tests/services/test_skland_mapping.py` 覆盖六类 `typeIcon` 写法与槽位截断；`tests/db/test_operator_card_fields_migration.py` 三条（升级建列、不回填、降级删列）。
- blocked_by：W0

### W2 同步与契约（不占设备）

- 为什么：新列要有人写、要出现在接口与生成类型里，前端才能用。
- 写范围：`maa_api/services/account/box.py`（`apply_skland_sync` 写两列）、`maa_api/services/account/rules.py`（`BOX_FIELDS` / `box_row_wire`）、`maa_api/api/routers/account.py`（`AccountOperatorRow` 字段与 docstring）、`web/openapi.json`、`web/src/types/api.d.ts`、`tests/services/test_account_box_skland.py`、`tests/api/test_account_router.py`、`tests/api/test_skland_e2e.py`。
- 做法：
  1. `apply_skland_sync` 写 `skill_ids` / `module_details`（`None` 不动列，空列表是正面读数）。
  2. `box_row_wire` 透传两列；`BOX_FIELDS` 加入它们（字段来源白名单随之生效）。
  3. 路由模型补两个字段 + 说明；`.venv/bin/python scripts/dump_openapi.py` → `cd web && pnpm gen:api`。
- 验收：`tests/services/test_account_box_skland.py` 断言两列写入与 `None` 不动；`tests/api/test_account_router.py` 的字段集合断言更新；`dump_openapi.py --check` 与 `pnpm gen:api:check` 当前。
- blocked_by：W1

### W3 前端卡片与网格（不占设备）

- 为什么：这是用户要的「好看且明了」本体。
- 写范围：`web/src/features/account/OperatorCard.tsx`、`OperatorGrid.tsx`、`assets.ts`（新增）、`OperatorsTab.tsx`、`format.ts`、`api.ts`（类型）、`OperatorCard.test.tsx`、`OperatorGrid.test.tsx`、`OperatorsTab.test.tsx`。
- 做法：
  1. `assets.ts`：`avatarUrl(charId)`、`skillIconUrl(skillId)`、`moduleTypeGlyph(type)`（`X/Y/D/A → X/Y/Δ/α`），集中一处便于换源。
  2. `OperatorCard`：稀有度色带与描边（6★ 橙、5★ 金、4★ 紫、3★ 蓝、2★ 绿、1★ 灰）、星级、名字、职业、精英化/等级/潜能、技能（图标 + 专精数字，专精 0 弱化）、模组（`label` + 等级；无 `module_details` 时退化成字母 + 等级）。未拥有：整体灰显、不加载图片、不给技能与模组。
  3. `OperatorGrid`：`grid`（移动 2 列 → 平板 3–4 → 桌面 6），点击回调 `onSelect`。
  4. `OperatorsTab`：列表段换成网格，其余段落（状态条、绑定卡、差异条、筛选、加载更多）不动。
- 验收：`pnpm typecheck`、`pnpm test --run`（新增卡片/网格用例：带头像与技能图标 src、无 `skill_ids` 的降级、未拥有灰显与不加载图片、模组标签与 glyph、点击回调、网格列类名）、`pnpm build`。
- blocked_by：W2

### W4 文档、台账、门禁与合并（不占设备）

- 写范围：`docs/04`、`docs/05`、`docs/09`、`docs/11`、`docs/12`、`docs/13`（§8.63）、`docs/README`（决策速查表）、`docs/copy-audit/*`、`docs/superpowers/ledgers/2026-10-12-m43-operator-card-grid.md`、生成物。
- 顺序：改文档 → `dump_openapi.py` → `pnpm gen:api` → 全门禁（`pytest -q`、`gen:api:check`、`typecheck`、`test --run`、`build`、`frontend_smoke.py`、`account_smoke.py`、`copy_inventory.py --check`）→ 台账补实测数字 → 提交 → `git checkout refactor/v2 && git merge --no-ff m43-operator-card-grid` → tag `v2-m43`。
- blocked_by：W3

## 验收

见规格 §7：映射与迁移用例、同步写库、契约与生成物、前端卡片与网格用例、全门禁、真机（用户看账号页）、`--no-ff` 合并打 `v2-m43`。
