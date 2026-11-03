# M40 计划 · 全量扫描的身份核对（详情页拉丁名）

- 规格：`docs/superpowers/specs/2026-10-11-m40-identity-check.md`
- 台账：`docs/superpowers/ledgers/2026-10-11-m40-identity-check.md`
- 分支：`m40-identity-check`（自 `refactor/v2`，基点 `6abd5cc`）；完成后 `git merge --no-ff` 回 `refactor/v2` 并打 tag `v2-m40`
- 真机：BlueStacks / Android 2560×1440（`127.0.0.1:5555`），用户常驻服务 8002 空闲时才动设备

分波推进，每一波都有独立的写范围；跨波依赖写在下一条 `blocked_by` 里。真机检查只作补充，主判据是单测与门禁。

## W1 事实取证（探针与离线实测）

**写范围**：`scripts/probe_full_scan.py`（探针，不进生产路径）、规格 §2

- 详情页左下角拉丁名带的位置与可读性：ROI 两轮标定 → `(0, 452, 524, 32)`。
- 逐格点开只读拉丁名，与 `battle_data.json` 的 `name_en` 对账（前缀口径 + 生产口径两栏）。
- 匹配阈值离线实测：1048 条拉丁名的变形实验，定 `0.7` / `0.15` / 3 字符。

**验收**：`--step name` 真机 9 格：生产口径 6/9 对得上、零误报；报告落 `/tmp/m40_name4.json`。
**状态**：完成。

## W2 服务层（节点 + 目录 + 比对 + 编排接入）

**写范围**：`maa_api/services/account/scan_tasks.py`、`maa_api/services/account/capture.py`、`maa_api/services/account/scan.py`
**blocked_by**：W1

1. `scan_tasks.py`：`OPER_NAME_EN_ROI`、`OPER_NAME_EN` 节点、`DETAIL_NODE_KEYS` 末尾、`ELITE_SHORTCUTS` 三条分支。
2. `capture.py`：`OperatorMeta.name_en`、`_latin_name()`、`load_operator_catalog()`。
3. `scan.py`：`_name_en_key` / `_name_en_hit`、`NAME_EN_MIN_*`、状态里的 `realigned` / `unverified`、`_detail_scan` 带 `name_en`、`_expected_name_en` / `_confirm_card`、`_scan_screen` 两处接入、`_scan_full` 传 `catalog`。

**验收**：`python -m py_compile` 三个文件通过；节点自检 44 个、详情链末尾是 `OperNameEn`。
**状态**：完成。

## W3 契约与前端

**写范围**：`maa_api/services/account/scan.py`（`snapshot()` 两个键，与 W2 同文件但只动这一处）、`web/src/features/account/api.ts`、`web/src/features/account/TrainingScanCard.tsx`、`web/src/features/account/test-utils.tsx`
**blocked_by**：W2

- `snapshot()` 多 `realigned` / `unverified`（int）；前端类型、`IDLE_TRAINING_SCAN`、归一化、`AfterStart` 种子、三个测试 fixture 同步。
- 摘要追加 `重锚 N 张` / `身份未核 N 张`（为 0 不提）。

**验收**：`pnpm typecheck` 0 错（字段是必填，漏改的构造点会被 TS 抓出来）。
**状态**：完成。

## W4 单测

**写范围**：`tests/services/test_account_training_scan.py`、`web/src/features/account/TrainingScanCard.test.tsx`
**blocked_by**：W2、W3

- 后端 6 例：模糊匹配认残读不认同族、期望表只覆盖卡名可读的格子、一致照写、漂移重锚并写重锚帧的读数、重锚仍不对不写库、认不出按卡名写库；同步 `test_the_elite_shortcut_skips_the_other_badges`、`test_snapshot_shape_matches_the_spec`。
- 前端 1 处：摘要的两个计数（`realigned: 4, unverified: 5` → `重锚 4 张 · 身份未核 5 张`），请求层用例带两个字段。

**验收**：`pytest -q tests/services/test_account_training_scan.py tests/services/test_account_scan_image.py` 全过；`pnpm test --run` 全过。
**状态**：完成。

## W5 文档 + 台账 + 门禁 + 合并

**写范围**：`docs/superpowers/specs/2026-10-11-m40-identity-check.md`、`docs/superpowers/plans/2026-10-11-m40-identity-check.md`、`docs/superpowers/ledgers/2026-10-11-m40-identity-check.md`、`docs/13-决策记录.md`、`docs/12-实施计划与里程碑.md`、`docs/05-API规范与路由清单.md`、`docs/09-前端重构方案.md`（如涉及）
**blocked_by**：W4

1. 写 `docs/13` §8.56（六段式）与 `docs/12` M40 小节。
2. `docs/05`：`GET /api/account/training-scan` 行补两个计数；`docs/09` 若提到扫描卡片摘要则同步。
3. 生成物：`scripts/dump_openapi.py` + `cd web && pnpm gen:api`；四处 `--check` 与文案基线 `--check`（棘轮）。
4. 后端全量 `pytest`、前端 `typecheck` / `test --run` / `build`、两个冒烟脚本。
5. `git merge --no-ff` 回 `refactor/v2`、打 tag `v2-m40`。

**验收**：全部门禁绿；合并后 `git log --oneline -1` 是 merge 提交、`git tag --list v2-m40` 有值。
**状态**：进行中。

## 复跑一轮全量的口径（另开，需先问用户）

修正版上线后，那轮 `updated=230` 里被写错的人要靠**再跑一轮全量**刷新。跑法：确认 8002 上没有正在跑的流水线 → 由用户在账号页点「全量扫描」（或 Agent 工具 `scan_operator_training(mode="full")`）→ 45–60 min。本轮不自动发起。
