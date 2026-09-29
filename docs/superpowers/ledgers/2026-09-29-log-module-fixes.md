# 2026-09-29 日志模块实测修正（路径统一 / 键位对齐 / 降噪）

本次不是里程碑，是用户报告两个现象后的一次只读排查加修复：任务日志里大量"未映射回调"、
核心日志一直为空。排查过程与裁决记录在 [docs/13 §8.21](../../13-决策记录.md)，
这里只记执行与验证。

## 起点

用户要求"只读排查"，先定位根因、不动代码。排查手段是回放真实日志 + 看运行中进程持有的句柄：

- `resource/maa-api/debug/asst.log` 样本：8221 条 `append_callback` 载荷；
- `lsof -c Python`：API 进程（92576）持 `resource/lib/maa/Darwin/debug/asst.log` 的**读**句柄
  （mtime 停在 9-25），内核子进程（92601）写 `resource/maa-api/debug/asst.log`（当时 40 MB 且在增长）。

两个根因（详见 docs/13 §8.21）：内核日志路径两侧各自推导、结论不同；翻译器从
`details.details.task` 取任务名，而实测该位置在 `SubTask*` 上恒为空。

## 改动

| 文件 | 改动 |
|---|---|
| `maa_api/settings.py` | 新增 `core_user_dir()`：user_dir 的单一推导来源 |
| `maa_api/main.py` | `boot_config["user_dir"]` 改用它（一行） |
| `maa_api/services/log_wiring.py` | `resolve_core_log_path()` 改为 `<user_dir>/debug/asst.log`，不再读 `maa_core_path` |
| `maa_api/services/callback_translator.py` | 新增 `_flatten()` 逐层取值；`_subtask_start` / `_subtask_error` / completed 分支改用它；动作级步骤静音；补 4 类映射 |
| `tests/services/test_callback_translator.py` | 重写为真实载荷形状驱动 |
| `tests/services/test_log_paths.py` | 新增：路径解析 + tailer 端到端 |
| `tests/api/test_app_log_wiring.py` | 更新那条会误导人的注释（`maa_core_path` 与日志路径无关） |
| `scripts/log_ws_smoke.py` | tail 的 asst.log 改到 user_dir 下；不再依赖 `maa_core_path` 定位日志 |
| `docs/06` §3.2/§3.3/§3.5/§5.1、`docs/13` §8.21 | 记录实测修正与降噪约定 |

## Rulings I made

**1. 不加 `user_dir` 配置项。** 只提成 `settings.core_user_dir()` 一个函数。理由：它是装配内部约定
（`AsstSetUserDir` 的入参），不是运维要调的东西；加键会与 docs/03「内核目录留空自动探测」重复表达。
如果将来要支持"把日志写到别处"，那应该是 `user_dir` 整体可配，而不是给日志单开一个开关。

**2. 动作级步骤直接不产记录，而不是降级或加配置阈值。** 备选是加一个 `log.task_step_level`
之类的开关（像 `core_min_level` 那样），但那样默认值仍要有人来定，且把"内核内部每一次点击"
当成一种可配置的日志级别去表达，语义是错的。判定放在白名单命中之后，
`StartButton2` 这类同时带 `action` 的确认动作照常产出 `INFO`。

**3. 不给具名子任务加中文标签表。** 试过一版 19 条的 `_SUBTASK_LABELS`，随后删掉：
内容是新的展示契约但无规格依据，且会把内核原名挡在搜索之外。现在直接用原名
（`子任务开始：StartGameTask`），可检索、无新约定。

**4. "未映射"口径收紧。** 未见过的具名子任务不再算未映射（它有自己的名字）。这样
"未映射回调"重新变回可信的告警信号。`SubTaskStart` 因此不再产出任何"未映射回调"。

**5. `InfrastDormConfirmButton` 单独补一条，不复用 `InfrastDormDoubleConfirmButton` 的文案。**
两者是内核 `tasks.json` 里两条不同的任务定义（7538 次 vs 764 次引用），文案分别是"基建宿舍确认"
与"请进行基建宿舍的二次确认"。

## 验证

- 后端：`.venv/bin/python -m pytest` → **1747 passed, 4 skipped**（改动前 1743 passed，新增 4 项）。
- 真实日志回放：8221 条回调 →
  - 旧实现：7923 条记录，其中 **4426 条**"未映射回调"；
  - 新实现：**1880 条**（`INFO` 1362 / `DEBUG` 384 / `WARNING` 124 / `ERROR` 10），**未映射 0 条**。
  1880 条落在 docs/04 §10.1 给一条完整日常流水线的 1000–3000 条预算内。
- `scripts/api_smoke.py` → `SMOKE OK`（52 项全过，含"仓库零残留"指纹校验）。
- `scripts/frontend_smoke.py` → `SMOKE OK`（先 `pnpm install --frozen-lockfile` + `pnpm build`，均 exit 0）。

## 未完成 / 遗留

**1. `scripts/log_ws_smoke.py` 仍然跑不到底，卡在第二个内核子进程 spawn。**
这是**既有的、与本次改动无关**的问题：从 HEAD 原样取一份脚本到 `/tmp` 跑，同样卡住。
两版的差别恰好证明了本次修复有效 —— HEAD 版卡在**核心日志那一步之前**（`等待 LogHub 观察源记录`
拿不到 `source="core"`），修好的版本**通过了核心日志 tail → live WS 那一步**，卡在更后面的
FakeAsst 收集阶段。

证据与初判：卡点处**第二个内核子进程根本没被创建**（`pgrep` 无 spawn 子进程，supervisor 也没打
"内核子进程已启动"），说明卡在 `multiprocessing.Process.start()` 的 fork 之前的信号量分配上。
同一个进程此前已 spawn 过一次内核，退出时 `resource_tracker` 报了
`There appear to be 6 leaked semaphore objects to clean up at shutdown`。即第一个
`CoreSupervisor` 的进程/队列信号量没被释放，第二次 spawn 卡住。

未修，因为：① 与本次改动无关，`git log` 显示该冒烟自 `5011cd9`（M4-11）之后再没跑过 ——
在核心日志为空的前提下它**必然卡在核心日志那一步**，所以第二个 spawn 这条路径长期没被走到过；
② 修它要动 FakeAsst 的 supervisor 生命周期，属于另一个问题。需要时单独开一轮。

**2. `user_dir` 在仓库内是硬约定。** `core_user_dir()` 返回 `REPO_ROOT/resource/maa-api`，
部署时 `REPO_ROOT` 由容器里的代码位置决定（见 docs/13 §8.10 的部署配套），本次未动部署侧。

**3. 日志页历史查询的来源/级别下推没做**（用户明确留待后续）。前端仍只发
`order`/`size`/`q`/`since`/`until`，"核心"标签页从**不分来源的最新 100 条**里本地筛。
core 记录现在开始有了，所以这个副作用会变得可见 —— 单独一轮处理，要连 docs/09 §6.3 一起改。

**4. 未做真机复核。** 修复后的核心日志 tail 只在单元测试与冒烟里验证过；运行中的服务
（pid 92576，8002 端口）仍持有旧路径的句柄，**需要重启服务**才会切到新路径。
