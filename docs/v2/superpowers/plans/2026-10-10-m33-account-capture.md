# M33 · 账号识别接线与可信度

- 分支：`m33-account-capture`（自 `refactor/v2`），完成后 tag `v2-m33`
- 依据：用户报告（规格 §1）；规格 `docs/superpowers/specs/2026-10-10-m33-account-capture.md`
- 口径：不是新功能。① 把 M27 建好却从没被调用的账号采集接到内核回调上；
  ② 修正「识别到 N 名干员」的计数口径；③ 给落库加护栏，别让失败/残缺识别清空账号数据

## 交付项

### W1 接线：`CALLBACK` → `AccountCaptureService`（规格 §4.1）

1. `maa_api/main.py`：`install_core_logging(...)` 之后注册 `core_client.on("CALLBACK", on_account_callback)`；
   回调里 `asyncio.create_task(capture.handle_callback(details, message=msg, pipeline_id=..., task_id=...,
   client_type=...))`，task 存 `set` 并在 `add_done_callback` 里丢弃。
2. 上下文：`pipeline_runner.log_context(msg, details)` 取 `(pipeline_id, task_id)`；
   `client_type` 取设置 `channel.client_type`（取值路径按 main.py 里既有的设置读取方式）。
3. 新建 `tests/api/test_account_capture_wiring.py`（或并入既有账户测试文件）：用假 core client 触发
   `CALLBACK`，断言落库、归属与「别的 what 不落库」。

### W2 计数：`all_opers` 的 `own=true`（规格 §4.2）

`maa_api/services/callback_translator.py` 的 `_operbox_count`：优先数 `all_opers` 里 `own=true` 的条数；
缺 `all_opers` 时退回 `own_opers` 条数再退回 `all_opers` 条数；同步 docstring 与
`tests/services/test_callback_translator.py` 的期望（含 383/380 同现的用例）。

### W3 护栏：空识别与收缩保护（规格 §4.3，强度按用户裁决）

1. `_capture_operbox`：`own=true` 为 0 → `skipped="no_owned_operators"`，不落库、不写识别记录、记 warning。
2. 收缩保护：`OperatorBoxService.apply_recognition` 加 `keep_owned` 开关（识别默认开、手动写入关），
   保留上次已拥有的行不被识别改成未拥有，返回 `kept_owned` 名单；capture 记 warning。
3. 测试：`tests/services/test_account_capture.py` 补三种载荷（0 / 13 / 383）与「手动取消拥有仍可生效」。

### W4 测试与冒烟

1. 真机载荷重放用例：从 `resource/maa-api/debug/asst.log` 抽最后一次 `OperBoxInfo`（有日志才跑，
   没有则 `skip`），断言「已拥有 383、3 名无详情但已拥有、护栏不误伤」。
2. `scripts/account_smoke.py` 增一步：真实 CALLBACK 事件（不是直接调 `handle_callback`）走通落库，
   保证「接线」这条被冒烟盯住（M27 的教训）。

### W5 文档

`docs/13-决策记录.md`（§8.48）、`docs/12-实施计划与里程碑.md`（M33 小节）、
`docs/README.md`（里程碑表）、`docs/11-Agent模块设计.md`（账号识别触发路径一句话）、
台账 `docs/superpowers/ledgers/2026-10-10-m33-account-capture.md`。

## 验收

1. 后端全量 `pytest -o addopts= -q`：只允许既有的两条真资源用例失败。
2. 四处生成物 `--check` 全 0；`scripts/copy_inventory.py --write-baseline` 后 `--check` 通过。
3. 五个冒烟脚本末行 `SMOKE OK`（含扩过的 `account_smoke.py`）；`pnpm typecheck`（本轮不动前端）。
4. 真机复跑一条 `干员识别`：账号页出现 383 名已拥有、日志那句按 383 计数。
