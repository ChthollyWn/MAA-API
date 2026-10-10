# M34 · 账号列表契约与分类清单（账号页空列表修复）

- 计划：`docs/superpowers/plans/2026-10-10-m34-account-list-contract.md`
- 分支：`m34-account-list-contract`（自 `refactor/v2`，基点 `8362e2f` = tag `v2-m33`），tag `v2-m34`
- 来源：用户报告（2026-10-10 20:08 真机跑完 `干员识别`）——顶部「已同步 / 共 384 名（精二 103）」，列表区却是空状态「没有符合条件的干员」

## 任务状态

| 工作项 | 产物 | 状态 |
| --- | --- | --- |
| W1 前端读键 | `web/src/features/account/api.ts`（`items` + `normalizeOperatorRow` / `normalizeDepotItem`）、`OperatorsTab.tsx:112`、`test-utils.tsx:172`、`DepotTab.tsx:57` 注释 | 完成（含 `tsc` 负向对照） |
| W2 分类清单 | `maa_api/db/repositories/account.py`（`AccountDepotItemRepository.categories()`）、`maa_api/services/account/depot.py`（`list_items` 返回 `categories`）、`tests/services/test_account_depot_service.py`（+1）、`tests/services/test_account_repositories.py`（+1） | 完成 |
| W3 契约护栏 | `maa_api/api/routers/account.py`（四个内联响应模型 + 两条路由 `response_model`）、`web/openapi.json`、`web/src/types/api.d.ts`、`api.ts` 删两条手写覆盖、`tests/api/test_account_router_contract.py`（+3）、`web/src/features/account/api.test.ts`（新，3 条） | 完成（含 OpenAPI 负向对照） |
| W4 文档 | `docs/05` §6.20、`docs/13` §8.49、`docs/12` M34 小节、`docs/README.md:122`、SDD `progress.md` | 完成（含验收数字） |
| 验收 | 后端全量、四处生成物、文案基线、前端三件套、五个冒烟、两条负向对照 | 完成（真机复验待用户） |

## 实测结论

1. **写侧正常、读侧没拿到数据**：真机库 `resource/maa_api.db` 的 `account_operator` 463 行（`own=1` **384** 行）、`account_recognition` 1 行（`item_count=384`），`GET /api/account/operators?owned_only=true` 与 `/summary` 都是 200（`resource/log/2026-10-10.log:2109-2143`）。所以数据、接口、鉴权都对，错在容器键。
2. **根因是「同一份数据两套形状描述」**：服务层 `maa_api/services/account/box.py:153` 返回 `{items, total, limit, offset}`（`docs/05` §6.20 冻结的口径），前端 `web/src/features/account/api.ts:250` 读 `data.operators ?? []`——键不存在就被 `??` 兜成空列表。后端没有 `response_model`（两个只读端点只写 `-> dict[str, Any]`），OpenAPI 里响应是自由对象，前端只能手写 `AccountEndpoints` 覆盖层，读错键不进类型检查。
3. **两边测试各自自洽**：后端 `tests/api/test_account_router.py:222-238` 断言 `items`，前端假后端 `test-utils.tsx:172` 造 `operators`；跨边界那个键谁也没管。M33 的教训是「建好能力没接线」，M34 的教训是「契约没有单一来源」。
4. **同类隐患顺带修**：`DepotTab.tsx:67` 的分类下拉读 `pages.at(-1)?.categories`，后端从来没给过——仓库识别一跑只会剩「全部分类」。已由 `AccountDepotItemRepository.categories()`（distinct + 按名排序）与 `list_items` 补齐，语义是整份识别快照的分类、与 `category`/`query` 无关。
5. **护栏有两处来源**：响应形状由装饰器 `response_model=` 与处理器返回标注共同决定，只删一处时 OpenAPI 与护栏用例都不变（FastAPI 从标注推断）。负向对照必须同时去掉两处才见红——已写进 §8.49 七。
6. **重放用例的选取要按语义**：`tests/services/test_account_capture_replay.py` 原先按「条数从后往前」挑载荷，用户 20:08 又跑一次识别后日志里多出 20:08 的增量行（`done=false`），挑到的不是 16:33 的收尾行，旧假设「残缺载荷是收尾批子集」随之崩掉。改成 `_pick_final` 同时卡 `done=true` 与条数（真机共 8 条收尾行：4×0 名、385、13、383、384）。

## 验收结果

主 agent 在所有写者停止后独占复跑（2026-10-10）。

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `.venv/bin/python -m pytest -o addopts= -q` | **2 failed / 3591 passed / 5 skipped**（220.63s）；两条失败是 M24 起已知的真资源用例 `tests/services/test_activity_index.py`；M33 基线 3586 passed，**+5 = 本轮新增 5 个用例**（router 契约 3、depot 服务 1、仓储 1） |
| 重放用例 | `pytest -o addopts= -q tests/services/test_account_capture_replay.py` | 第一次在全量里 FAILED（挑到增量行）→ 改成按 `done=true` 挑 → **1 passed** |
| 前端负向对照 | 把 `data.items` 改回 `data.operators` 后 `npx tsc --noEmit` | `src/features/account/api.ts(283,19): error TS2339: Property 'operators' does not exist on type '{ items: … }'`；还原后 `cmp` 逐字节一致（RESTORED_OK） |
| 后端负向对照 | 同时去掉 `response_model=AccountOperatorList,` 与 `-> AccountOperatorList` | `scripts/dump_openapi.py --check` exit=1（stderr「OpenAPI snapshot is stale: web/openapi.json」）且 `tests/api/test_account_router_contract.py::test_list_endpoints_pin_their_response_shape[/api/account/operators-…]` FAILED；还原后 `cmp` 一致 |
| 生成物 | `dump_openapi.py --check`、`generate_api_guide.py --check`、`generate_task_types_fixture.py --check`、`cd web && pnpm gen:api:check` | 四处全 `exit=0` |
| 文案基线 | `copy_inventory.py --check` → `--write-baseline` → `--check` | 先报新增文案（`docs正文/bold` 2471 → 2493、`docs正文/em-dash` 501 → 516、`开发向/bold` 290 → 291、`注释/bold` 232 → 236、`注释/em-dash` 100 → 103），重设为 **45 项**后「通过：无回潮」 |
| 前端 | `cd web && pnpm test` / `typecheck` / `build` | 73 文件 **735 passed**（新增 `api.test.ts` 3 条）／退出码 0／退出码 0（`static/` 重建，20:24） |
| 冒烟 | `agent_smoke.py`、`frontend_smoke.py`、`account_smoke.py`、`copilot_research_smoke.py`、`prts_smoke.py --m28` | 五个末行均 `SMOKE OK` |

## 遗留

1. **真机复验待用户执行**：刷新账号数据页，干员页签应列出 384 名已拥有干员（本机不连设备，`static/` 已重建、服务重启后生效）。
2. **单条 `GET /api/account/operators/{char_id}` 仍是自由对象**，`/summary`、`/operator-diffs`、`/upgrade-plans` 也仍在 `api.ts` 的手写覆盖层里（已逐一核对一致），下一轮可一起收进响应模型。
3. `DepotTab.tsx:67` 仍取 `pages.at(-1)?.categories`：后端现在每页都给同一份 `categories`，等价（见 §8.49 八.4）。
