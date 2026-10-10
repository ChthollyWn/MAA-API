# M33 · 账号识别接线与可信度

- 分支：`m33-account-capture`（自 `refactor/v2`），完成后 tag `v2-m33`
- 上游：M27（账号数据与养成计划，`docs/13` §8.42）、M32（任务执行前提，`docs/13` §8.47）
- 触发：用户真机跑完 `OperBox` 后报告两个问题（§1）

## 1. 目标与用户决策

用户原话（m02966）：

> 两个问题，一个是我实际有383名干员，但是任务只识别到380，另一个是识别任务完成后，没有更新到账号数据

截图（m02965）：流水线只跑一条「干员识别」，任务结果「1 分 31 秒 已完成」，日志末段
`17:20:00 识别到 380 名干员 / 子任务完成: OperBoxRecognitionTask / 完成任务 [OperBox] / 全部任务已完成`。

诊断结论（§2）：两句都是**我们自己这一侧**的问题——

1. 「380」是**日志计数口径错了**：`callback_translator` 按 `own_opers`（带练度详情的条目）计数，
   而 MaaCore 的 `all_opers` 里 `own=true` 是 383（与用户实际一致）。
2. 「没有更新到账号数据」是**接线缺失**：`AccountCaptureService` 在生产代码里没有任何调用方，
   识别结果从来没有落库（`account_operator` 等四张表 0 行）。

用户裁决（m0XXXX，待回填）：护栏强度见 §4.3。

## 2. 事实基础

### 2.1 真机载荷（MaaCore `resource/maa-api/debug/asst.log`，2026-10-10）

今日全部 `done=true` 的 `OperBoxInfo` 回调，`details` 恒有 `all_opers`（463 条）/ `own_opers` / `done`：

| 时刻 | `all_opers` 条数 | `own=true` 条数 | `own_opers` 条数 | 说明 |
| --- | --- | --- | --- | --- |
| 16:26:54 / 16:27:48 / 16:28:46 / 16:29:41 | 463 | **0** | 0 | M32 故事里游戏没启动、重试 4 次的失败尝试，内核照样发 `done=true` |
| 16:32:46 | 463 | 385 | 382 | 成功识别；比 17:20 多 `char_1052_kalts2`（凯尔希·思衡托）、`char_4023_rfalcn`（红隼） |
| 16:33:08 | 463 | **13** | 13 | 明显残缺的识别，内核仍发 `done=true` |
| 17:20:00 | 463 | **383** | 380 | 用户报的这次；383 = 用户实际拥有数 |

- `own_opers ⊆ all_opers(own=true)` 在全部 84 次回调里都成立（`own_opers` 是「有练度详情的那批」）。
- 固定只有 3 名 `own=true` 却从不进 `own_opers`：`char_002_amiya`、`char_1037_amiya3`（阿米娅）、
  `char_512_aprot`（暮落）——它们**是已拥有**，只是没有精英化 / 等级 / 潜能详情。
- 结论：**「已拥有多少名」只能看 `all_opers` 的 `own` 字段**；`own_opers` 只代表「有多少名带练度详情」。

### 2.2 接线缺口（问题②）

- `maa_api/services/account/capture.py:150` 的 `AccountCaptureService` 在 `maa_api/main.py:978` 构造、
  `:983` 挂到 `app.state.account_capture`，但**生产代码里没有任何地方调用 `handle_callback`**：
  `grep` 只命中 `tests/services/test_account_capture.py:95/506/525` 与
  `scripts/account_smoke.py:258/266/291/429/488`。
- 生产里的内核 `CALLBACK` 消费者只有四处：`maa_api/main.py:653`（连接事件）、
  `maa_api/services/copilot/result.py:597`（作业结果识别）、
  `maa_api/services/pipeline_runner.py:252`（流水线生命周期）、
  `maa_api/services/log_wiring.py:123`（日志翻译）——没有一处喂给账号采集。
- 这正是 M27 冒烟测不出这条缺陷的原因：冒烟脚本直接调 `handle_callback`，绕过了「谁调它」。
- 数据库实测：`resource/maa_api.db` 的 `account_recognition` / `account_operator` /
  `account_depot_item` / `account_operator_diff` **各 0 行**。

### 2.3 现有落库路径的语义（`OperatorBoxService.apply_recognition`，box.py:354）

- `operators`（`char_id → {own, elite, level, potential, name, rarity, profession}`）逐行 upsert；
  `all_operators` 是本次 `all_opers` 的全集，**在全集里、这次没识别到**的 id 会被置 `own=False`（box.py:421-422）。
- 因此**把 16:26–16:29（`own=true` 为 0）或 16:33（13）这样的载荷直接落库，会把 463 行整表标成未拥有**
  ——比「识别失败不动数据」危险得多。仓库侧已有先例：`_capture_depot`（capture.py:298-302）对空载荷
  明确「不整表替换」，注释写「识别失败时清空账号数据比留着旧数据危险得多」。

## 3. 范围

### 3.1 本里程碑

1. **接线**：把 `OperBoxInfo` / `DepotInfo` 回调真正送进 `AccountCaptureService`（§4.1）。
2. **计数**：日志「识别到 N 名干员」按 `all_opers` 的 `own=true` 计数（§4.2）。
3. **护栏**：空识别与残缺识别不清空账号数据（§4.3，强度见用户裁决）。
4. 测试（含真机载荷重放）与文档、台账同步。

### 3.2 不做

- 不动前端（账号页的渲染不变；新数据天然满足它的既有契约）。
- 不调 MaaCore 的识别 roi / 不碰 `resource/`。
- 不接线 `sanity_observation` / `stage_drop`（迁移 0007 建了表、目前没有任何写入方）——
  与本轮用户报告无关，登记为遗留（§5）。
- 不新增工具、不新增 HTTP 端点、不新增设置键。

## 4. 设计

### 4.1 接线（唯一入口）

新增 `maa_api/services/account/callback_wiring.py`：

```
install_account_capture(client, capture, *, context_provider=None, client_type_provider=None)
```

在 `maa_api/main.py` 里，`app.state.account_capture = account_capture` 之后立刻调用它
（位置在 `install_core_logging(...)` 之后，同款先例）：

```
install_account_capture(
    core_client,
    account_capture,
    context_provider=pipeline_runner.log_context,
    client_type_provider=lambda: get_settings().channel.client_type,
)
```

- **载荷形状**：内核回调 payload 是 `{"msg": <int>, "details": <dict>}`；`details` 就是
  `CallbackTranslator` 认识的信封（`{"what": "OperBoxInfo"|"DepotInfo", "details": {...}}`），
  也正是 `AccountCaptureService.handle_callback(callback=...)` 要的东西——同一份对象，不复制、不解析。
- **异步**：`handle_callback` 是协程，回调是同步执行的内核线程上下文——用
  `asyncio.get_running_loop().create_task(...)` 调度，并把 task 存进 `set` +
  `add_done_callback` 丢弃（避免被 GC）；task 的异常在 `_finish` 里记一条 warning，
  绝不影响流水线。没有可用事件循环（同步上下文，例如单测直接调）时只记 warning、不落库。
- **上下文**：`pipeline_id` / `task_id` 复用 `pipeline_runner.log_context(msg, details)`
  （与日志同一套归属，日志页与账号页对得上）；`client_type` 取设置的 `channel.client_type`
  （`maa_api/settings.py:170`，默认 `Bilibili`）；`created_at` 不传，由服务自己取 `utcnow()`。
- **过滤**：只按消息类型粗筛 `Message.SubTaskExtraInfo`（正是采集服务自己的第一道判断），
  `what` 是否是 `OperBoxInfo` / `DepotInfo`、`done` 是否收尾，全部交给 `handle_callback`
  的既有分发（`not_subtask_extra_info` / `unsupported` / `incomplete` / `bad_payload`），
  保证「谁负责什么」只有一处。两个 provider 抛异常时记日志并降级（上下文为 `None`），不跳过采集。
- **关闭**：随进程退出即可（与既有 CALLBACK 消费者同款，不新增生命周期管理）。

### 4.2 计数口径（问题①）

`callback_translator._operbox_count`（callback_translator.py:386）：

- `all_opers` 里**至少有一条带 `own` 字段**时 → 数 `own=true` 的条数（`own: null` 算「带字段、
  未拥有」；缺 `own` 字段的条目不计）。
- 没有 `all_opers`、或其中没有任何条目带 `own` 字段（旧版内核形状）→ 退回 `own_opers` 条数，
  再退回 `all_opers` 条数。
- `own_opers` 的条数**不再**作为首选：它只代表「带练度详情的已拥有数」，不是拥有总数。
  两个边角行为按实现固定下来：① `own: null` 视同带字段（计为未拥有）；② `all_opers` 是空 list
  而 `own_opers` 非空时会退回 `own_opers` 条数（真机数据不可能出现，因为 `own_opers ⊆ all_opers`
  的 own=true 子集）。
- 日志文案仍是「识别到 {count} 名干员」，`count` 的含义变成「已拥有总数」。

### 4.3 护栏（用户裁决：A）

**用户裁决（2026-10-10）：「A（推荐）识别只加不减：本次没识别到的、库里已有的已拥有干员保留为
已拥有，并在日志提示；真要取消拥有走 Agent/手动编辑」。** 下面按 A 实现。

`AccountCaptureService._capture_operbox` 落库前的两道闸：

- **G1 · 空识别跳过**：`all_opers` 非空、但 `own=true` 的条数为 0 → 不落库、不写识别记录、
  `skipped="no_owned_operators"`，记一条 warning（提示「可能是游戏没启动或不在干员列表，
  账号数据保持原样」）。对应 §2.1 里 16:26–16:29 那四次。
- **G2 · 收缩保护**：`OperatorBoxService.apply_recognition(..., keep_owned=True)`——识别说
  「未拥有」而行里已经是已拥有时保留 `True`（**新建行不算**：新建行的 `own=True` 只是占位），
  记一条 warning（人数 + 名字，最多 5 个），并把被保留的 id 通过返回值的 `kept_owned`
  透出成 `AccountCaptureResult.kept_count`。手动 / Agent 的 `update_operator_box` 不受此限
  （那才是「真的要取消拥有」的通道）；保留值照实记进 `recognized_values` 与
  `field_sources = recognition`。

### 4.4 日志与记录

- 护栏触发时 `logger.warning`（会进 `log_entry`，日志页可见），文案含「本次识别到 N 名、现有 M 名、
  保留了 K 名」与名字样例。
- 识别记录（`account_recognition`）只在真的落库时写，`item_count` = 本次拿到的已拥有数。

## 5. 错误与兼容

- 接线失败（回调里抛异常）绝不能影响流水线：`handle_callback` 已保证吞异常并返回 `skipped="error"`；
  调度处的 `create_task` 之外不写任何可能抛的代码。
- 旧版内核没有 `all_opers` 时，计数退回、护栏 G1/G2 都不触发（没有 `all_opers` 就没有「整表未拥有」的风险，
  `apply_recognition` 也不会把集合外的人置为未拥有）。
- 幂等：同一条回调重复送达时 upsert 幂等；差异（`account_operator_diff`）的四个条件（box.py:429-435）
  保证重复识别不会刷差异。
- 遗留（不在本轮）：`sanity_observation` / `stage_drop` 两张表没有任何写入方。

## 6. 文档同步

`docs/13-决策记录.md`（新增 §8.48）、`docs/12-实施计划与里程碑.md`（M33 小节）、
`docs/README.md`（里程碑表加 M33）、`docs/11-Agent模块设计.md`（账号识别的触发路径）、
台账 `docs/superpowers/ledgers/2026-10-10-m33-account-capture.md`。

## 7. 验收

1. 后端全量 `pytest -o addopts= -q`：只允许既有的两条真资源用例失败。
2. 新用例：接线（CALLBACK → 落库，含 pipeline/task 归属）、计数（383/0/13 三种载荷）、
   护栏（空识别不落库、收缩保护或按裁决的等价行为）、真机载荷重放（有 `asst.log` 时跳过式）。
3. 四处生成物 `--check` 全 0；`scripts/copy_inventory.py --write-baseline` 后 `--check` 通过。
4. 五个冒烟脚本末行 `SMOKE OK`；前端 `pnpm typecheck`（不改前端）。
5. 真机复跑一条 `干员识别`：账号页出现 383 名已拥有（用户执行，或现场有设备时由主 agent 执行）。
