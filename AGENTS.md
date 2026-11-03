# MAA-API

以 MaaAssistantArknights 为内核的 FastAPI 服务。v2 重构 M0–M62 已完成并合入 master；v3（SuperAgent 改造）方案见 docs/v3/README.md，实施路线见 docs/v3/12，决策与偏离见 docs/v3/13。

## 规格与决策

- docs/v3/README.md 的决策速查表是 v3 的单一事实来源，docs/v3/01–13 是 v3 方案，docs/v3/13 是 v3 决策记录。
- docs/v3/13 §8 记录实施后的偏离与待决事项。
- 实现偏离 v3 文档时，在同一任务内更新对应文档，并在 docs/v3/13 §8 补一条。
- docs/v2/ 是 v2（M0–M62）的冻结文档，只读；代码注释与测试里形如 docs/13 §8.x、docs/05 §6 的文字引用指 docs/v2/ 下同名文件。
- v3 的 spec 放 docs/v3/specs/，计划放 docs/v3/plans/，台账放 docs/v3/ledgers/。

## 分支与发布

- 公开仓库 `github.com/ChthollyWn/MAA-API`：**master** 是对外稳定分支（仓库默认分支），**dev** 是开发集成分支（当前仅本地，未推送；需要公开时再推同名分支）。
- 日常工作从 dev 新建 `<简短英文名>` 分支；完成后用 `git merge --no-ff` 合并回 dev。v2 里程碑完成打 `v2-m<N>` tag，v3 里程碑完成打 `v3-m<N>` tag。
- 发布时把 dev 合入 master 并推送 master；里程碑分支与临时分支不推。
- 本地分支只保留 master 与 dev 两个；临时分支用完即删。历史 tag（`v2-m*`）已公开，不要改写已发布历史。
- 默认不 push：提交到本地即可。用户明确要求推送时，push 当前分支到 origin 的同名分支，不必再确认一次；没有明确要求就不要推。
- 仓库已公开：config.yaml、resource/、.venv/、web/node_modules/ 都不进版本库；提交前检查敏感信息，绝不把真实密钥、凭据、个人身份信息写进代码或文档。
- 不使用 worktree：.venv、config.yaml、resource/、web/node_modules 都不进版本库，新 worktree 里没有。直接在当前检出上切分支工作。

## 命令

- 后端测试：`.venv/bin/python -m pytest -q`
- 前端：`cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`
- OpenAPI 快照检查：`.venv/bin/python scripts/dump_openapi.py --check`。改了后端接口，先运行不带 `--check` 的同一命令重新生成，再 `cd web && pnpm gen:api`。
- 前端集成冒烟（需先 `pnpm build`）：`.venv/bin/python scripts/frontend_smoke.py`
- `scripts/*_smoke.py` 是各子系统的端到端冒烟，成功时末行是 `SMOKE OK`。
- 文档断链检查：`.venv/bin/python scripts/check_doc_links.py`，末行 `BROKEN 0` 表示没有基线之外的断链；`--all` 列出全部断链（含 `scripts/doc_links_baseline.txt` 里的已知项）。

## 环境陷阱（完整记录见 docs/ENVIRONMENT.md）

- `python` 不在 PATH，一律用 `.venv/bin/python`。
- macOS 没有 `timeout` 命令，需要限时就用工具自身的超时参数。
- FastAPI 0.141 下 include 进来的路由对象没有 `.path`，取路由路径用 `set(app.openapi()['paths'])`。
- 依赖真机、模拟器或真实内核的检查（如 `DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin .venv/bin/python scripts/core_smoke.py`）不能作为硬性验证，只作补充检查并记录证据。
- 本机没有 ripgrep（`rg` 不在 PATH）；v3 spec 里的 `rg` 命令改用 `grep -rn`。
- `pytest.ini` 的 `addopts` 已经是 `-q`；命令行再带 `-q` 会变成 `-qq`，日志里没有 passed/failed 汇总行。要看计数时用 `.venv/bin/python -m pytest -o addopts= -q`。

## 对 superpowers 流程的项目约定

- 任务审核通过后、标记完成前，主 agent 亲自重跑该任务的验证命令和后端测试，不以实现者的报告为准。
- 只影响实现细节的裁决可以自行决定并记入台账；改变对外行为、范围或与 docs/v3/13 冲突的裁决，停下来问我。
- 计划完成、删除 SDD 工作区之前，把 progress.md 复制到 docs/v3/ledgers/<计划文件名>.md 并提交，"Rulings I made" 一并写入。
- 测试、构建、diff 这类长输出重定向到文件，只读尾部和退出码；主 agent 不通读大文档，交给子 agent。
- 上下文压缩或换新会话后，先读当前计划的台账、docs/v3/13 §8 和 `git log`，再继续。
- spec、计划和汇报使用简体中文。
