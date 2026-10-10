# M34 · 账号页列表契约对齐（前端读错键 + 契约护栏）

- 分支：`m34-account-list-contract`（基点 `8362e2f` = tag `v2-m33`）
- 来源：用户 2026-10-10 20:10 报告——「干员识别已同步，但是没有显示任何干员」。截图里账号页
  干员页签显示「已同步、共 384 名（精二 103）、识别于 2026-10-10 20:08」，列表却是
  「没有符合条件的干员」。
- 关联：[docs/13 §8.49](../13-决策记录.md)、[docs/05 §6.20](../05-API规范与路由清单.md)。

## 1. 根因

写侧没问题，读侧读错了键。

- 真机库 `resource/maa_api.db`：`account_operator` 463 行（`own=1` 384 行、`own=0` 79 行），
  `account_recognition` 1 行（`kind=operbox`、`item_count=384`、`client_type=Bilibili`、
  带 `pipeline_id` / `task_id`）——M33 的接线与落库都对。
- 服务层 `maa_api/services/account/box.py:153` 返回 `{"items": [...], "total", "limit", "offset"}`，
  这正是 [docs/05 §6.20](../05-API规范与路由清单.md) 第 801 行冻结的契约（「列表用
  `{items, total, limit, offset}`」）。
- 前端 `web/src/features/account/api.ts:250` 读的是 `data.operators ?? []` —— 键不存在，
  兜底成空数组，于是页面走空状态。`20:10:00` 的访问日志里
  `GET /api/account/operators?limit=50&offset=0&owned_only=true` 是 200，后端其实返回了 384 条。
- 为什么两边测试都没拦住：后端 `tests/api/test_account_router.py:225` 断言 `items`，
  前端 `web/src/features/account/test-utils.tsx` 的 MSW 假数据造的是 `operators` ——
  两套测试各自自洽，真机契约没人比对。根子在 `web/openapi.json`：这两个只读端点没有
  `response_model`，生成的 OpenAPI 里响应是自由对象，前端只好在 `api.ts` 里手写
  `AccountEndpoints` 覆盖层，读错键也就没人能发现。

## 2. 同类隐患（一并修）

`web/src/features/account/DepotTab.tsx:67` 由 `data.categories` 生成分类下拉，
而 `GET /api/account/depot` 从不返回这个键（服务层 `list_items` 只给 `items/total/limit/offset`）。
仓库识别（`account_depot_item` 现在 0 行）一跑，用户就会看到分类下拉里只剩「全部分类」。

## 3. 决定

1. **前端对齐契约读 `items`。** 后端键名不动：docs/05 §6.20 已经冻结，且 `items` 是账号页
   其余列表（差异、仓库、养成计划）一致的写法。
2. **`categories` 由后端补**，语义为「当前识别快照里的全部分类，按名字排序，与 `category` /
   `query` 过滤无关」。理由：分页下前端只能看到已取回的那几页，派生出来的下拉会随筛选缩水；
   分类清单本来就是「按分类分组」这份契约的一部分。同步写进 docs/05 §6.20。
3. **护栏：给两个只读列表端点加 `response_model`**（`AccountOperatorList` /
   `AccountDepotList`，行模型 `AccountOperatorRow` / `AccountDepotRow`），把形状钉进
   `web/openapi.json` 与生成类型 `web/src/types/api.d.ts`，并删掉 `api.ts` 里这两条手写覆盖。
   之后「读错键」在 `pnpm typecheck` 就失败，不依赖谁去比对假数据。
4. **不做**：其余手写覆盖（`/summary`、`/operator-diffs`、`/upgrade-plans`）本轮不动——它们的
   键已与真机一致；要收口留到后续里程碑按同一手法处理。M33 已合并的接线、计数与护栏不动。

## 4. 任务

| # | 任务 | 落地物 |
|---|---|---|
| W1 | 前端读键对齐：`fetchOperators` 读 `items`，`OperatorListPayload.items`，`OperatorsTab` 消费点与 MSW 假数据同步 | `web/src/features/account/api.ts`、`OperatorsTab.tsx`、`test-utils.tsx` 及受影响的用例 |
| W2 | 后端补 `categories`：`DepotService.list_items` 返回快照内全部分类（排序、与筛选无关） | `maa_api/services/account/depot.py` + 用例 |
| W3 | 契约护栏：两个列表端点加 `response_model`；重建 `web/openapi.json` 与生成类型；删手写覆盖、wire→view 归一化 | `maa_api/api/routers/account.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`api.ts` |
| W4 | 文档与验收：docs/05 §6.20、docs/13 §8.49、docs/12 M34 段、本计划与台账 | 文档四处 + 门禁复跑 |

## 5. 验收

- 负向对照两条：把 `data.items` 改回 `data.operators` → `cd web && pnpm typecheck` 必须报错；
  去掉 `response_model` → `scripts/dump_openapi.py --check` 必须报差异。
- `.venv/bin/python -m pytest -o addopts= -q`：只允许两条已知真资源用例失败
  （`tests/services/test_activity_index.py`）。
- 生成物四处 `--check` 全 0：`scripts/dump_openapi.py`、`scripts/generate_api_guide.py`、
  `scripts/generate_task_types_fixture.py`、`cd web && pnpm gen:api:check`；
  `scripts/copy_inventory.py --check` 通过（新增文案先 `--write-baseline`）。
- `cd web && pnpm test`（vitest）、`pnpm typecheck`、`pnpm build` 全绿，产物落到 `static/`。
- 冒烟五个脚本末行 `SMOKE OK`。
- 真机复验：重建 `static/` 后页面刷新，干员页签列出 384 名已拥有干员（此前是空状态）。

## 6. 遗留

- `static/` 是本地构建产物、不入库（`git ls-files static` 为空），所以本轮不改部署形态，
  只重建一次供本机页面生效。
