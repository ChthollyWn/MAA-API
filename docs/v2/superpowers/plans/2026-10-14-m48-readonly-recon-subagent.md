# M48 只读探索工具与侦察 subagent（计划）

- 规格：[2026-10-14-m48-readonly-recon-subagent.md](../specs/2026-10-14-m48-readonly-recon-subagent.md)
- 分支：`m48-subagent`（基点 `refactor/v2` = `f9d6d7a`）
- 完成后：`git merge --no-ff` 回 `refactor/v2`、打 tag `v2-m48`，台账复制到 `docs/superpowers/ledgers/2026-10-14-m48-readonly-recon-subagent.md`

每个波次都能独立跑门禁。W1（配置与断言）与 W2（实现）写范围不重叠，可并行；W3 依赖 W2 的实现面；W4 收文档与生成物；W5 全量验收与合并。

## W1 配置与工具目录断言

**写范围**：`maa_api/settings.py`、`config.template.yaml`、`tests/agent/test_tool_catalog.py`、`tests/test_settings.py`、`tests/test_vapid_settings.py`

- `WorkspaceSettings.root: str = ""`（空串＝关闭探索能力，**不是**取进程 CWD：launchd 拉起时 CWD 可能是 `/`）。
- `AgentSettings` 追加 `recon_max_steps=8(1–20)`、`recon_output_chars=20000(1000–200000)`、`recon_max_chars=200000(10000–2000000)`。
- `config.template.yaml` 加 `workspace:` 段与三个 `recon_*` 项。
- **四项都登记进 `SETTING_KEYS`**（否则 `config.yaml` 与环境变量两层被整个忽略——实测缺口），并进 `CONFIG_ONLY_SETTING_KEYS`（宿主机路径 + 运维阈值，不进设置 API）。
- 工具目录断言：`EXPECTED_TOOLS` 加四条，计数 76 → **80**、可见 75 → **79**（初稿写的 78/77 少算了两个，以实测登记表为准）。
- 同步 `tests/test_settings.py::test_env_mapping_names_are_pinned` 与 `tests/test_vapid_settings.py` 的精确集合。
- **验收**：`.venv/bin/python -m pytest -q tests/test_settings.py tests/test_vapid_settings.py tests/agent/test_tool_catalog.py`；`resolve_settings({'workspace': {'root': ...}})` 能真的读到值。

## W2 只读探索 + 侦察 subagent

**写范围**：`maa_api/agent/recon/**`（新建）、`maa_api/agent/tools/recon.py`（新建）、`maa_api/agent/tools/__init__.py`、`maa_api/agent/runtime.py`、`maa_api/domain/errors.py`、`docs/05-API规范与路由清单.md`、`tests/domain/test_errors.py`

- `explore.py`：`list_files` / `read_file` / `search_code` 与其边界（根目录、越界含软链接、密钥名单与正文探测、二进制、2 MiB、忽略目录用 `os.walk` 原地剪枝、截断续读、git 四动作白名单）。
- `runner.py`：`run_recon` / `ReconDeps` / `ReconBudget`；子循环只带三个只读工具；三层预算（步数、单次输出、累计字符）到顶即收尾而不是抛错；报告 JSON 非法时退化。
- `delegate.py`：`delegate_recon` 工具（`SAFE` / `read_only` / `group="recon"`），从 `app.state.agent_runtime` 与 `settings_provider` 取依赖。
- `runtime.py`：`AgentRuntime.recon(...)` 公开入口，复用 `_make_client`，不占会话锁、不写 `agent_message`。
- 错误码只加三个（`WORKSPACE_NOT_CONFIGURED` 503 / `WORKSPACE_PATH_DENIED` 403 / `WORKSPACE_FILE_TOO_LARGE` 400），其余复用既有码；同步 docs/05 §4.20、`tests/domain/test_errors.py` 的计数（121 → 124、120 → 123、19 → 20 张表）。
- **验收**：`.venv/bin/python -m pytest -q tests/domain/test_errors.py tests/agent/`；Lead 自测脚本（越界/密钥/续读/搜索/预算耗尽/父只收报告）。

## W3 验收用例

**写范围**：`tests/agent/test_recon_explore.py`、`tests/agent/test_recon_delegate.py`（新建）

- 探索侧：未配置、越界（`..`、绝对路径、越界软链接）、密钥名单 + 目录里 `blocked`、二进制与超大、忽略目录、分段续读拼接、正则非法、`max_results` 上限、git 白名单外动作被拒、非仓库报外部源不可用。
- 委派侧：假客户端两步出报告、步数用尽、字符预算耗尽、JSON 非法退化、**父上下文只拿到报告**（断言报告里没有被读文件正文）。
- **验收**：`.venv/bin/python -m pytest -q tests/agent/test_recon_explore.py tests/agent/test_recon_delegate.py`。

## W4 文档口径

**写范围**：`docs/11-Agent模块设计.md`、`docs/13-决策记录.md`、`config.template.yaml` 的注释已在 W1

- docs/11 新增「只读探索与侦察 subagent」小节：四个工具的输入输出、边界口径、预算三件套、为什么父会话只收报告、git 四动作。
- docs/13 新增 `### 8.71`：D1–D5 与两项实施裁决（默认空串、四项 config-only）、被否决的替代方案、已知偏离。
- docs/11 的旧计数口径改 80 / 79。
- **验收**：`grep -n "8.71" docs/13-决策记录.md`；docs/11 字段名与 `maa_api/settings.py` 逐字一致。

## W5 全量验收与合并（主 agent 亲自跑）

- `.venv/bin/python -m pytest -q`（既有两条真资源失败照旧，基线不退化）。
- `.venv/bin/python scripts/dump_openapi.py --check`（工具目录不落 OpenAPI，应不变）。
- `cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`（不改前端，作为回归证据）。
- `scripts/*_smoke.py` 相关项与 `git diff --check`。
- 负向对照：`workspace.root` 留空时四个工具都报 `WORKSPACE_NOT_CONFIGURED`；配好根目录后能真的读到文件、且读不到 `.env`。
- 台账 `docs/superpowers/ledgers/2026-10-14-m48-readonly-recon-subagent.md`（含 Rulings I made），`git merge --no-ff` 回 `refactor/v2`，tag `v2-m48`。
