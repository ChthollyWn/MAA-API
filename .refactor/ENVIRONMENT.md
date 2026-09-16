# 环境事实（跨会话共享）

> 本文件是**已实测确认**的环境事实，供拆卡者与 worker 共用。
> 目的：不让每个全新上下文的 agent 重复发现同一件事。
> 每条都必须来自实测，推测请标注「未验证」。

## 基础工具链

- **`python` 不在 PATH，只有 `python3`（3.13.3）。** verify 命令里写 `python -c ...` 会永远失败。
- 仓库内 venv：`.venv/`（M0-01 建立），解释器 `.venv/bin/python`，已 `pip install -e .`。
- `poetry lock` 若在受限沙箱下需要 `POETRY_CACHE_DIR=$(mktemp -d)`（默认缓存目录在仓库外）。
- Node v23.11.0；pnpm 11.1.3。adb 36.0.0 于 `/opt/homebrew/bin/adb`。

## MaaCore 内核

- 本地库：`resource/lib/maa/Darwin/`（v6.17.5）与 `resource/lib/maa/Linux/`（v6.17.2）。
- macOS 加载需 `DYLD_LIBRARY_PATH=/Users/chtholly/Developer/WorkSpace/MAA-API/resource/lib/maa/Darwin`。
- 真机/模拟器已连接：`127.0.0.1:5555`（`adb devices` 可见）。

## 验证命令的陷阱

- **`import maa_api.main` 不能当门禁。** 它在 import 期就走 `Updater().update()` 与内核 `dlopen`，实测约 2.5 分钟且依赖网络与本地资源。verify 请用轻量 import（如 `import maa_api.domain.task`）。
- verify 必须能在「改动前失败、改动后通过」两个方向上真正区分，否则是无意义的门禁。

## 编排与执行环境

- 分支 `refactor/v2`；`dev` 停在 `139c4bc`，是回滚锚点。
- worker 由编排器以 `dsh --profile headless` 一次性进程执行，每个 worker 是**全新上下文**。
- Node 23 下官方 `dsh` bin 因 `import.meta.main` 为 undefined 而静默 no-op，必须走包装器 `~/.local/bin/dsh-start.mjs`。
- worker 以 `DSH_PERMISSION_MODE=danger-full-access` 运行（无人值守不能卡审批：headless 没有审批应答方，`approval: ask` 会 fail closed）。

## git 注意事项

- `config.yaml` 已从跟踪中移除（本地保留），模板为 `config.template.yaml`。
- `.refactor/logs/`、`.refactor/state.json`、`.refactor/PAUSE`、`.refactor/TRIGGER`、`.refactor/orchestrator.lock`、`.refactor/orchestrator-src/` 均已 gitignore。
- **worker 只 `git add` 自己的 deliverables**，绝不 `git add -A`：`.refactor/` 下的台账由编排器负责提交。
