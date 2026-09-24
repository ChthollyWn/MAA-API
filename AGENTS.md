# MAA-API

以 MaaAssistantArknights 为内核的 FastAPI 服务，正在进行 v2 重构。M0–M9 已完成，后续里程碑见 docs/12。

## 规格与决策

- docs/README.md 的决策速查表是单一事实来源，docs/01–13 是重构方案，docs/13 是决策记录。
- docs/13 §8 记录实施后的偏离与待决事项，与其他文档冲突时以它为准。
- 实现偏离文档时，在同一任务内更新对应文档，并在 docs/13 §8 补一条。
- superpowers 的 spec 放 docs/superpowers/specs/，计划放 docs/superpowers/plans/，台账归档到 docs/superpowers/ledgers/。

## 分支与发布

- 集成分支是 refactor/v2。每个里程碑从它新建 `m<N>-<简短英文名>` 分支，完成后用 `git merge --no-ff` 合并回 refactor/v2，并打 `v2-m<N>` tag。
- 不要 push，不要合并到 dev。
- 不使用 worktree：.venv、config.yaml、resource/、web/node_modules 都不进版本库，新 worktree 里没有。直接在当前检出上切分支工作。

## 命令

- 后端测试：`.venv/bin/python -m pytest -q`
- 前端：`cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`
- OpenAPI 快照检查：`.venv/bin/python scripts/dump_openapi.py --check`。改了后端接口，先运行不带 `--check` 的同一命令重新生成，再 `cd web && pnpm gen:api`。
- 前端集成冒烟（需先 `pnpm build`）：`.venv/bin/python scripts/frontend_smoke.py`
- `scripts/*_smoke.py` 是各子系统的端到端冒烟，成功时末行是 `SMOKE OK`。

## 环境陷阱（完整记录见 docs/ENVIRONMENT.md）

- `python` 不在 PATH，一律用 `.venv/bin/python`。
- macOS 没有 `timeout` 命令，需要限时就用工具自身的超时参数。
- FastAPI 0.141 下 include 进来的路由对象没有 `.path`，取路由路径用 `set(app.openapi()['paths'])`。
- 依赖真机、模拟器或真实内核的检查（如 `DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin .venv/bin/python scripts/core_smoke.py`）不能作为硬性验证，只作补充检查并记录证据。

## 对 superpowers 流程的项目约定

- 任务审核通过后、标记完成前，主 agent 亲自重跑该任务的验证命令和后端测试，不以实现者的报告为准。
- 只影响实现细节的裁决可以自行决定并记入台账；改变对外行为、范围或与 docs/13 冲突的裁决，停下来问我。
- 计划完成、删除 SDD 工作区之前，把 progress.md 复制到 docs/superpowers/ledgers/<计划文件名>.md 并提交，"Rulings I made" 一并写入。
- 测试、构建、diff 这类长输出重定向到文件，只读尾部和退出码；主 agent 不通读大文档，交给子 agent。
- 上下文压缩或换新会话后，先读当前计划的台账和 `git log`，再继续。
- spec、计划和汇报使用简体中文。
