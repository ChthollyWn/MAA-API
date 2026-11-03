# M37 · 练度扫描可用性修复（实施计划）

规格：`docs/superpowers/specs/2026-10-11-m37-scan-usability.md`
分支：`m37-scan-usability`（从 `refactor/v2` 起，基点 `d286190` = tag `v2-m36`）
完成 tag：`v2-m37`（完成后 `git merge --no-ff` 回 `refactor/v2`；不 push、不碰 dev）

## 交付项

| 波次 | 内容 | 写范围 | 依赖 |
| --- | --- | --- | --- |
| W1 | 名单来源 await 修复（`_operators_from_box` 改 async + `resolve_targets` 的 `isawaitable` 兜底） | `maa_api/services/account/scan.py` | 无 |
| W2 | `char_ids` 收名字（目录 id → 名字索引，同名多形态仍 `unresolved`） | `maa_api/services/account/scan.py` | 无 |
| W3 | 起手 `StartUp` 流水线 + 独立超时 + `main.py` 注入 `client_type` 提供方 | `maa_api/services/account/scan.py`、`maa_api/main.py` | W1 |
| W4 | 空屏护栏（第一屏一个卡名都没读出来立刻失败） | `maa_api/services/account/scan.py` | W1 |
| W5 | 单测回归四条缺陷 | `tests/services/test_account_training_scan.py`、`tests/api/test_account_scan_router.py`（如需） | W1–W4 |
| W6 | 前端文案（起手会把游戏拉起来） | `web/src/features/account/TrainingScanCard.tsx` | 无 |
| W7 | 真机验收 + 文档 + 台账 + 基线 + 合并 | `docs/*`、`.superpowers/sdd/2026-10-11-m37-scan-usability/progress.md` | 全部 |

## 顺序与依赖

1. W1 → W2 → W3/W4 依次改同一个文件（`scan.py`），单波一次提交前一起跑测试。
2. W5 与 W1–W4 同文件不同段，改完统一跑 `tests/services/test_account_training_scan.py`。
3. W6 独立于后端（文案）。
4. W7 收口：全部门禁 + 真机三条 + 文档 + 台账 + 合并 tag。

## 验收

见规格 §6。要点：四条缺陷各有回归用例；真机空请求体扫描能拿到名单、游戏关掉能自拉起、Agent 用名字受理成功；四处生成物 current、全量 pytest 除既有两条真资源红外无新增失败、前端 typecheck/vitest/build 绿、两条 smoke 末行 OK、文案基线无回潮。

## 风险与回退

- 起手 `StartUp` 让每轮多约 7 秒（真机实测），不抢内核、不消耗理智。
- 若起手在真机上失败（客户端拉不起来），退化成「明确报错、不自动拉起」，其余修复保留。
- 名字解析遇到同名多形态：进 `unresolved`，不猜（与养成计划分支同一口径）。
