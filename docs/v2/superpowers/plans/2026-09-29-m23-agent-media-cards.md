# M23 · Agent 会话内的图片与图表实施计划

规格见 [specs/2026-09-29-m23-agent-media-cards.md](../specs/2026-09-29-m23-agent-media-cards.md)。**行为以规格为准**；规格没覆盖到的实现细节由实现者决定并写进报告，主 agent 抄进台账。裁决见 [13-决策记录](../../13-决策记录.md) §8.29。

## 全局约束

- 工作分支 `m23-agent-media-cards`（已从 `refactor/v2` 建立）。直接在当前检出上工作，不建 worktree，不 push，不合入 `dev`。
- `python` 不在 PATH，一律 `.venv/bin/python`。
- 前端 node/pnpm 不在默认 PATH：命令前加 `export PATH=/opt/homebrew/bin:$PATH`（node 23.11.0 / pnpm 11.1.3）。
- **不要 `git commit` / `git add` / `git stash`**；提交由主 agent 统一做。
- 长输出重定向到文件，只读尾部与退出码。
- **不新增数据表、不新增 REST 端点、不新增错误码、不引入后端新依赖**（前端只允许新增 `echarts`）。
- 后端既有断言会变红的只有 `tests/agent/test_tool_catalog.py`（工具清单 43 → 48，新增组 `media`）；其它既有测试**不许为了让新用例好写而放宽**。

任务依赖：Task 1 与 Task 2 都是后端且文件不相交（Task 1 只碰 `agent/**`、`services/stage_layout.py`、`util/image.py`、`tests/agent/**`、`tests/services/test_stage_layout.py`、`tests/util/**`；Task 2 只碰 `services/pipeline_runner.py`、日志写入侧与 `tests/services/**`），可并行；Task 3 前端独立（无端点变化，`gen:api` 产物不变）；Task 4 文档、Task 5 验收、Task 6 台账与合并由主 agent 收口。

## Task 1 · 后端：媒体工具链

**产物**：

- `maa_api/agent/tools/media.py`：五个工具（`send_screenshot` / `annotate_screenshot` / `send_item_icons` / `send_stage_map` / `send_chart`），组 `media`，参数与校验按规格 §3.2、§3.3、§3.5；
- `maa_api/services/stage_layout.py`：`Arknights-Tile-Pos` 索引、平局规则、四类资源根查找、单格 3 字符归一化（规格 §3.4）；
- `maa_api/util/image.py`：截图加工（裁剪 / 放大 / 画框 / CJK 字体探测，规格 §3.3）；
- `maa_api/agent/runtime.py`：`_archive_screenshot_result` 补 `media` 信封、`build_model_context` 的媒体占位、`SCREEN_TOOL_NAMES` 加 `annotate_screenshot` 且拦截文案按工具名生成、系统提示词新段落；
- 测试：`tests/agent/test_media_tools.py`、`tests/services/test_stage_layout.py`、`tests/util/test_image_annotation.py`、`tests/agent/test_runtime.py` 增补、`tests/agent/test_tool_catalog.py` 清单更新、`tests/fixtures/tile_keys.json` 夹具。

**完成判据**：`.venv/bin/python -m pytest -q tests/agent tests/services tests/util` 退出码 0；规格 §5 里属于 Task 1 的用例全部存在且真的断言到（尤其：107 个 tileKey 零遗漏、图表两级上限、视觉降级拦截文案、`get_screenshot` 结果新增 `media` 且既有断言不变）。

## Task 2 · 后端：任务留档截图与失败日志附件

**产物**：

- 流水线执行器：任务终态（`completed` / `failed` / `skipped`）留一张 `Screenshot(trigger=task_snapshot, pipeline_id, task_id)`，`cancelled` 不留；截图失败只记 WARNING、带超时、不影响任务终态（规格 §3.9）；
- 失败与跳过时把同一张图挂进日志 `meta.attachment`（规格 §3.10）；
- 测试：任务终态留档、取消不留、截图抛错时终态不变、失败日志附件字段与去重。

**完成判据**：`.venv/bin/python -m pytest -q tests/services tests/api` 退出码 0；规格 §5 里属于 Task 2 的用例全部存在。

## Task 3 · 前端：媒体卡与 ECharts

**产物**（`web/src/`）：

- `features/agent/media.ts`：`parseMediaCards`（白名单 / 上限 / 未知忽略 / `get_screenshot` 旧形态兼容）；
- `features/agent/MediaCards.tsx`：`ImageCard` / `ItemIconsCard` / `StageMapCard`（SVG）/ `ChartCard`；
- `features/agent/chart/`：`EChartSurface.tsx`（动态 import + 按需注册 + ResizeObserver + 主题跟随 + 失败回落）、`buildChartOption.ts`（纯函数）；
- `components/ui/use-authed-image.ts`：抽公共取图 hook，`PipelineScreenshots` 改用它；
- `ToolCallBar.tsx`：卡片渲染在 `<details>` 之外、上方；`format.ts` 的 `TOOL_LABELS` 补五个工具；
- `index.css`：`--chart-1…--chart-6` 两套；
- `package.json` / `pnpm-lock.yaml`：`echarts@^6.1`；
- 测试：`media.test.ts`、`ChartCard.test.tsx`、`StageMapCard.test.tsx`、`ImageCard.test.tsx`、`ToolCallBar`、`AgentPage.test.tsx` 增补。

**完成判据**：`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` 退出码 0（`test`/`build` 必须带 `export PATH=/opt/homebrew/bin:$PATH`）。

## Task 4 · 文档同步（子 agent + 主 agent）

- 子 agent：docs/04 §5.4、docs/06 §8、docs/09 §3.5 + §4/§9、docs/11 §3.2/§4.4/§5/§8、docs/12 M23、docs/README 决策速查表。
- 主 agent：docs/13 §8.29、SDD 台账。

## Task 5 · 验收（主 agent 亲自跑）

- `.venv/bin/python -m pytest -q`；
- `.venv/bin/python scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`；
- `cd web && pnpm typecheck && pnpm test --run && pnpm build`；
- `scripts/agent_smoke.py`、`scripts/frontend_smoke.py`；
- 行为实测：假 LLM 驱动一轮跑完五个媒体工具 → 断言 SSE `tool_result` 带 media 信封、落库、历史重建一致；流水线跑一次确认真实留档截图与失败日志附件；
- `git diff` 复核：除 Task 2 的流水线终态截图外，执行器语义未被改动。

## Task 6 · 台账与合并（主 agent）

- `.superpowers/sdd/2026-09-29-m23-agent-media-cards/progress.md` → `docs/superpowers/ledgers/2026-09-29-m23-agent-media-cards.md`；
- `git merge --no-ff` 回 `refactor/v2`，打 tag `v2-m23`。
