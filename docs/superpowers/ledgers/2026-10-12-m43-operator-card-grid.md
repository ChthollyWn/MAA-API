# M43 台账：干员页卡片网格 + 模组类型（`m43-operator-card-grid`）

- 规格：`docs/superpowers/specs/2026-10-12-m43-operator-card-grid.md`
- 计划：`docs/superpowers/plans/2026-10-12-m43-operator-card-grid.md`
- 分支基点：`refactor/v2`（tag `v2-m42`）
- 状态：实施中

## 波次进度

| 波次 | 内容 | 状态 |
| --- | --- | --- |
| W0 | 事实取证：官方 CDN 资源、`typeIcon` 口径、参考仓库许可 | 完成（规格 §2） |
| W1 | 迁移 `0020_operator_card_fields` + `mapping` 的 `skill_ids` / `module_details` | 完成 |
| W2 | 同步写入 + 契约（`AccountOperatorRow` 三个字段）+ 生成物 | 完成 |
| W3 | 前端卡片与网格 | 完成（独立复核后补了「未拥有不画技能/模组」） |
| W4 | 文档、台账、门禁、合并打 tag | 完成 |

## 真机与实测事实（W0）

| 事项 | 实测结果 |
| --- | --- |
| 头像资源 | `https://web.hycdn.cn/arknights/game/assets/char/avatar/char_002_amiya.png` → 200，65 KB PNG |
| 技能图标资源 | `.../char_skill/skchr_amiya_3.png` → 200，17 KB PNG |
| 模组图标资源 | 官方 CDN 六条候选路径（`equip/icon`、`equip_icon`、`uniequip/icon`、`equip-icon`、`char_equip`、`skill_icon`）**全部 404**；yituliu 用自有 CDN `cos.yituliu.cn/arknights/equip-icon/{typeIcon}.png`（694 B，200） |
| `typeIcon` 口径 | 森空岛 `equipmentInfoMap[id].typeIcon` 形如 `ccr-y`（分支码 + `-` + 类型字母），证章 `original`；与拆包 `uniequip_table.json` 同一 id 值**逐字相同**（`uniequip_001_absin → original` 双向核对） |
| 拆包表分布 | `uniequip_table.json` 911 条，`typeIcon` 分布 `original 398 / x 301 / y 183 / a 14 / d 6`，另有 9 条异常写法（`ra-a`、`so-a`、`so-b`、`dec-X`、`WAH-Y`） |
| `equipTypeInfos` | `ORIGINAL(sort0) / X(1) / Y(2) / D(3) / A(4,特限) / B(5,特限)`；`B` 全表仅 1 条 |
| 参考仓库许可 | `frontend-v2-plus` LICENSE=NOASSERTION；`BackEndV3` **无 LICENSE 文件**（`license: null`）⇒ 只复用事实（字段路径、配色、断点），不复制代码与数据产物 |

## 实施中修掉的问题（按发现顺序）

1. **M42 的静默丢字段**：`GET /api/account/operators` 声明了 `response_model=AccountOperatorList`，而 `AccountOperatorRow` 里**漏声明 `favor_percent`** ⇒ 列表响应把信赖整列丢掉（单条读取返回裸 dict 才带，所以 M42 的用例没抓到）。M43 补模型 + 加「列表行必须含这些字段」的断言防复发。
2. **同步侧的静默不写**：`_skland_value_changed` 对未知字段走 `optional_int(before) != optional_int(value)`，两个新列表列会被判成「没有变化」⇒ **永远不写**。补 `skill_ids` / `module_details` 两个分支（列表归一后比较）。
3. **证章与「认不出」必须分开**：`original`（`uniequip_001_*`）没有类型字母，但它是**正常状态**，不能计 `dropped`（否则「上游变了没有」的信号被噪声淹没）。实现用 `{"type": None}` 的明细与返回 `None` 区分。
4. **推翻 M42 的一条结论**：M42 判定「森空岛给不出模组类型」，依据只是**键名**（没有 `type` / `moduleType`），没看 `typeIcon` 的取值。M43 取证后确认 `typeIcon` 就是类型 ⇒ 不需要引入拆包数据（docs/13 §8.61 二、3g 已改，§8.63 一记明）。

## 门禁（主 agent 亲自复跑，2026-10-12 14:4x）

| 门禁 | 结果 |
| --- | --- |
| 后端全量（分支上）`.venv/bin/python -m pytest -o addopts= -q --tb=no` | **2 failed / 3804 passed / 6 skipped**（265 s）：两条失败都是 `tests/services/test_activity_index.py` 的真资源用例（M24 起既有，docs/13 §8.40），与本轮无关 |
| 后端全量（合并后 `refactor/v2` 上复跑） | **3 failed / 3803 passed / 6 skipped**（284 s）：除上面两条既有真资源失败外，多一条 `tests/core/test_crash_recovery.py::test_backoff_sequence_grows_then_failed_after_budget`——隔离复跑 1 failed / 14 passed 之外单跑该类全绿，属既有的时序抖动族（与前几轮 `test_incidents.py` / `test_app_skeleton.py` 同类），与本轮改动无关 |
| 前端 `pnpm gen:api:check` | `OpenAPI types are up to date.` |
| 前端 `pnpm typecheck` | 0 错误 |
| 前端 `pnpm test --run` | **76 文件 / 792 例全过**（基线 778 + 新增 14：卡片 11、网格 3） |
| 前端 `pnpm build` | OK，末行 `[sw_harness] SERVICE WORKER OK` |
| `scripts/dump_openapi.py --check` | `OpenAPI snapshot is current` |
| `scripts/frontend_smoke.py` | `SMOKE OK` |
| `scripts/account_smoke.py` | `SMOKE OK` |
| `scripts/copy_inventory.py --check` | 通过（45 项基线，无回潮） |

**中途抓到的问题**（详见上文「实施中修掉的问题」）：四条被冻结契约挡住的门禁（路由
`OPERATOR_ROW_FIELDS`、`models.EXPECTED_JSON_COLUMNS`、两处行形状断言）在加上新字段后变红，
已同步更新；一次全量跑出现 6 条失败，其中 4 条是上面这四条真问题、另 2 条是真资源既有失败。

**独立复核**：并行的一个复核 agent（只读）在我停笔 180 s 后独立跑了 `typecheck` / `test --run` /
`build`，结论一致（790 例时点），并指出「未拥有仍画技能槽与模组 chip」这一条规格偏离——已修并补用例。

## 真机验收（用户）

- 前置：**重启服务**才会应用迁移 `0019` + `0020`（当前运行中的进程停在 `0018`，`skland_credential` 表与 `favor_percent` / `skill_ids` / `module_details` 三列都还不存在）。
- 步骤：`config.yaml` 配 `skland.credential_key` → 账号页扫码绑定 → 点「同步练度」→ 看卡片网格与模组标签。
