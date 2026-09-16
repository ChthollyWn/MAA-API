# 环境事实（跨会话共享）

> 本文件是**已实测确认**的环境事实，供拆卡者与 worker 共用。
> 目的：不让每个全新上下文的 agent 重复发现同一件事。
> 每条都必须来自实测，推测请标注「未验证」。
>
> **worker 可以在本文件对应小节末尾追加新发现的事实**（只追加、不改写既有条目、
> 不 `git add`，编排器会随台账一起提交）。这是 worker 被允许触碰 `.refactor/` 的
> 唯一例外 —— 踩过的坑如果不写下来，下一个里程碑的 worker 会再踩一次。

## 基础工具链

- **`python` 不在 PATH，只有 `python3`（3.13.3）。** verify 命令里写 `python -c ...` 会永远失败。
- 仓库内 venv：`.venv/`（M0-01 建立），解释器 `.venv/bin/python`，已 `pip install -e .`。
- `poetry lock` 若在受限沙箱下需要 `POETRY_CACHE_DIR=$(mktemp -d)`（默认缓存目录在仓库外）。
- Node v23.11.0；pnpm 11.1.3。adb 36.0.0 于 `/opt/homebrew/bin/adb`。

## MaaCore 内核

- 本地库：`resource/lib/maa/Darwin/`（v6.17.5）与 `resource/lib/maa/Linux/`（v6.17.2）。
- macOS 加载需 `DYLD_LIBRARY_PATH=/Users/chtholly/Developer/WorkSpace/MAA-API/resource/lib/maa/Darwin`。
- 真机/模拟器已连接：`127.0.0.1:5555`（`adb devices` 可见）。
- **`AsstMsg` 取值不是 1..12 连号**（v6.17.5 实测）：`InternalError=0`、`InitFailed=1`、`ConnectionInfo=2`、`AllTasksCompleted=3`、`AsyncCallInfo=4`、`Destroyed=5`、`TaskChain*=10000..10004`、`SubTask*=20000..20004`。照连号硬编码会把 `ConnectionInfo`(2) 当成 `TaskChainError`。
- **`AsstAppendTask` 校验任务必填参数，缺参数时静默返回 0**（不抛异常、无回调）。实测：`("StartUp", {})`→0、`("StartUp", {"client_type":"Official"})`→1；`("Infrast", {})`→0、带 `{"facility":["Mfg"]}`→2；`("Recruit", {})`/`{"times":4}` 均→0；`Award`/`Fight`/`Mall`/`Roguelike`/`OperBox`/`Depot` 空参数即可。`task_id == 0` 必须当失败处理。
- **`AsstSetUserDir` 要求目录已存在**，否则返回 False；旧封装 `Asst.load()` 把它 `&=` 进返回值，表现为「内核加载失败」。
- **按绝对路径 `ctypes.CDLL(<dir>/libMaaCore.dylib)` 不依赖 `DYLD_LIBRARY_PATH`**：`env -u DYLD_LIBRARY_PATH` 实测也能 dlopen（依赖走 @loader_path/@rpath）。那条环境变量是冗余保险。
- **`AsstGetTasksList(handle)`（`restype=c_char_p`）在本内核上直接段错误**：`<user_dir>/crash.log` 记 `SIGSEGV`，进程退出码是 **1**（不是 139/-11）。即 MaaCore 把 native 崩溃转成 exit 1 + crash.log，supervisor 不能靠「exitcode 为负」判崩溃。
- **`import maa_api.model.core.asst` 有 import 期副作用**：连带 import `maa_api.config.config`，后者 mkdir `static/ resource/lib resource/log resource/temp` 并可能拷贝 `daily_task_template.json`（CWD 非仓库根时 RuntimeError）。子进程启动序列别走这条链。
- **spawn 子进程 + `ctx.Queue()` 已验证可用**：入口必须是模块级函数、回调的 `event_queue` 靠子进程模块级全局持有（`ctypes.c_void_p(id(arg))` 指针法跨进程无效）；子进程继承父进程环境变量。实测 spawn→内核 READY 约 0.16–0.29s，SIGKILL→exitcode -9 约 0.05s。

## 验证命令的陷阱

- **`import maa_api.main` 不能当门禁。** 它在 import 期就走 `Updater().update()` 与内核 `dlopen`，实测约 2.5 分钟且依赖网络与本地资源。verify 请用轻量 import（如 `import maa_api.domain.task`）。
- verify 必须能在「改动前失败、改动后通过」两个方向上真正区分，否则是无意义的门禁。
- **旧 `maa_api/model/util/utils.py` 的 `Message` 原来是普通 `Enum`，`int(成员)` 抛 `TypeError`**（非 `IntEnum` 没有 `__int__`/`__index__`）。M1-03 的 verify 第 2 条用 `int(Old.成员)` 做 ABI 逐项比对，实测因此**永远无法通过**（第 1 次尝试即卡死在这条）。处置：M1-03 把该枚举改为 `IntEnum`（取值一个未动；`Message(msg)`、成员间比较、全量 pytest 语义不变）并随卡提交，M3 删除该模块时一并消失。**以后写涉及旧枚举的 verify 请用 `m.value` 而不是 `int(m)`**，除非确认它是 `IntEnum`。

## 编排与执行环境

- 分支 `refactor/v2`；`dev` 停在 `139c4bc`，是回滚锚点。
- worker 由编排器以 `dsh --profile headless` 一次性进程执行，每个 worker 是**全新上下文**。
- Node 23 下官方 `dsh` bin 因 `import.meta.main` 为 undefined 而静默 no-op，必须走包装器 `~/.local/bin/dsh-start.mjs`。
- worker 以 `DSH_PERMISSION_MODE=danger-full-access` 运行（无人值守不能卡审批：headless 没有审批应答方，`approval: ask` 会 fail closed）。

## git 注意事项

- `config.yaml` 已从跟踪中移除（本地保留），模板为 `config.template.yaml`。
- `.refactor/logs/`、`.refactor/state.json`、`.refactor/PAUSE`、`.refactor/TRIGGER`、`.refactor/orchestrator.lock`、`.refactor/orchestrator-src/` 均已 gitignore。
- **worker 只 `git add` 自己的 deliverables**，绝不 `git add -A`：`.refactor/` 下的台账由编排器负责提交。
