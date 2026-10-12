# AI 腔规则命中：测试内的中文断言（改文案的连带影响面）

总计 6304 条文本。命中按规则汇总，「用户可见」只统计界面/对外文案，
「开发向」是注释、docstring、内部常量（维护者看得到，终端用户看不到）。

| 规则 | 严重度 | 用户可见 | 开发向 | 合计 |
|---|---|---|---|---|
| 粗体强调 | medium | 0 | 87 | 87 |
| 绝对化规定腔 | medium | 0 | 70 | 70 |
| 破折号当冒号用 | high | 0 | 56 | 56 |
| 「A 而不是 B」对照腔 | high | 0 | 50 | 50 |
| 自指式讲解腔 | medium | 0 | 10 | 10 |
| 省略号 | medium | 0 | 7 | 7 |
| 「…即可」机器腔 | medium | 0 | 4 | 4 |
| 「不是…而是…」对照句 | high | 0 | 2 | 2 |
| 「首先…其次…」罗列腔 | high | 0 | 1 | 1 |
| 提示腔 | medium | 0 | 1 | 1 |
| emoji/符号装饰 | high | 0 | 1 | 1 |

## 粗体强调（medium｜用户可见 0 / 开发向 87）

**开发向**

- `tests/agent/test_activity_tools.py:1` ``get_activity_stages`` 工具（M25 规格 §5.6）。 用假活动服务（返回现造的 :class:`ActivityReport`）与假客户端把参数形态、返回信封与降级路径 写死；本机资源的推断规则由 ``tests…（共 274 字）
- `tests/agent/test_copilot_site_tools.py:1` 作业站工具（规格 §9.4、§9.5）：注册元信息、参数边界与派发。 `agent/tools/__init__.py` 的接入由 M29 的装配任务做（本任务的写范围不含它），所以这里 自己建一个空注册表调 `register_tools…（共 188 字）
- `tests/agent/test_game_entity_tools.py:1` M26 的四个游戏数据工具（规格 §6.1、§6.3–§6.5）。 `get_operator`、`get_enemy`、`get_item`、`get_drop_rates` 的注册与参数校验走真注册表； 工具与领域层之间用**假的服务对…（共 403 字）
- `tests/agent/test_knowledge_convergence.py:1` M30 T3：删除内置条目时的覆盖行迁移（规格 §10.1 最后一条、§10.3 的 T3）。 背景：``agent_knowledge`` 的覆盖行只存与内置**不同**的列，其余为 NULL。内置条目 一旦从 ``builtin/*.m…（共 719 字）
- `tests/agent/test_knowledge_no_numbers.py:1` M30 T4：「无数值」检查测试（规格 §10.3、§10.1）。 规格 ``docs/superpowers/specs/2026-10-09-m25-prts-knowledge.md`` §10.1 把内置知识条目的 数值口径定成「数…（共 807 字）
- `tests/agent/test_knowledge_no_numbers.py:205` MAA 操作参数里的固定招募时长（Recruit 固定填 `9:00:00`，T1-a §5 第 6 条豁免它）。注意「时长档位 → 稀有度下限」那张表 T1-a §5 明确**不豁免**：`1:00:00` 也不在白名单里（除非它出现在 …（共 134 字）
- …… 另有 81 条，见 inventory.json

## 绝对化规定腔（medium｜用户可见 0 / 开发向 70）

**开发向**

- `tests/agent/test_knowledge_convergence.py:1` M30 T3：删除内置条目时的覆盖行迁移（规格 §10.1 最后一条、§10.3 的 T3）。 背景：``agent_knowledge`` 的覆盖行只存与内置**不同**的列，其余为 NULL。内置条目 一旦从 ``builtin/*.m…（共 719 字）
- `tests/agent/test_knowledge_no_numbers.py:166` 界面比例与坐标：screen-* 用「约占横向 50%–93%、纵向 4%–14%」「横向 93%、纵向 89%」描述可点区域，是操作坐标而非游戏数值（T1-b §5 风险 1 明确要求豁免）。判据就是任务书写的「界面描述语境」：必须带 `…（共 179 字）
- `tests/agent/test_knowledge_no_numbers.py:185` 行内代码：backtick 包起来的一律是工具名、字段名、条目 id 或参数值（`get_prts_page`、`threshold`、`series`、`mode`、`medicine`、`plan_index`、`screen-home…（共 209 字）
- `tests/agent/test_knowledge_no_numbers.py:574` 规格 §10.3 T4：正文不得出现「数字 + 游戏单位」。
- `tests/agent/test_knowledge_service.py:1287` Agent 看得到的东西里不得写死**这一期**的数据：当期活动名与它的 stageId 前缀。 口径与 M19 §4.3「不写会随版本失效的内容」一致。扫描三处 Agent 可见面：内置知识 条目（每轮注入索引、`read_knowled…（共 416 字）
- `tests/agent/test_llm.py:341` 模型名写错时要能在 details 里看到上游原话，且凭据不得回显。
- …… 另有 64 条，见 inventory.json

## 破折号当冒号用（high｜用户可见 0 / 开发向 56）

**开发向**

- `tests/agent/test_knowledge_convergence.py:1` M30 T3：删除内置条目时的覆盖行迁移（规格 §10.1 最后一条、§10.3 的 T3）。 背景：``agent_knowledge`` 的覆盖行只存与内置**不同**的列，其余为 NULL。内置条目 一旦从 ``builtin/*.m…（共 719 字）
- `tests/agent/test_knowledge_no_numbers.py:479` ``numbers_to_remove`` 里挑出来的真实数值片段 ``(来源, 片段)``。 复核报告这些字段是描述性短语，必须挑掉带注释（→、括号、分号）与不以数值为主体的 条目，只留下「数字 + 单位」或裸百分比形态的片段——它们必须…（共 142 字）
- `tests/agent/test_policy_account_write.py:1` 养成计划确认卡片（M31 补口，规格 §7.7）：预演进 payload、冲突进 reasons。 M27 的确认卡片只有 ``summary`` / ``targets`` / ``run_times``，用户按下确认之前看不到 「会和现…（共 374 字）
- `tests/agent/test_policy_account_write.py:148` 条目的字段缺一个就用另一边，全缺就跳过——不能让确认卡片多一行空话。
- `tests/agent/test_runtime.py:3461` 规格 §5：批次中途中断后每个 ``tool_calls`` 都有对应 tool 消息。 中断点由测试控制：第一条 tool 消息落库之后、第二条之前挂住这一轮，正好落在 「补历史」的中间窗口上——这是最容易补重复或漏补的一条路径。
- `tests/agent/test_runtime.py:3953` 轮次失败要留下两样东西：日志里的一行，和会话里的一条说明。 2026-10-09 的实况是两样都没有：用户消息已经落库，回复与错误原因都没留下，用户 第二天只能问「Agent 怎么又不好使了」，而服务端一个字都查不到——`AppError`…（共 147 字）
- …… 另有 50 条，见 inventory.json

## 「A 而不是 B」对照腔（high｜用户可见 0 / 开发向 50）

**开发向**

- `tests/agent/test_activity_tools.py:377` 没有装配索引、默认门面也读不到资源时，报的是资源不可用而不是空结果。
- `tests/agent/test_knowledge_no_numbers.py:166` 界面比例与坐标：screen-* 用「约占横向 50%–93%、纵向 4%–14%」「横向 93%、纵向 89%」描述可点区域，是操作坐标而非游戏数值（T1-b §5 风险 1 明确要求豁免）。判据就是任务书写的「界面描述语境」：必须带 `…（共 179 字）
- `tests/agent/test_knowledge_no_numbers.py:222` 星级/稀有度/精英阶段/技能等级序号：`1★–6★`、`6★`、`3 星通关`、`精英阶段 0 / 1 / 2`、`精一 / 精二`、`专一~专三`、`技能 1/2/3`、`Lv.` 后跟符号占位。它们是分类标签而非数值；T1-a §5 把…（共 227 字）
- `tests/agent/test_knowledge_no_numbers.py:262` Markdown 列表项与标题里的序号（`1.`、`-`、`## 第 1 步`）：属于排版而不是数值。只豁免行首那截标记，`- 135 理智` 这种行里的数值仍会命中。
- `tests/agent/test_knowledge_no_numbers.py:346` 扫一行，返回**全部**候选；被豁免的候选 ``rule_id`` 非 None，其余是命中。 保留两类「整行豁免」：围栏代码块与跨行代码（奇数个反引号）的行，内容是工具调用 示例而不是知识正文；这类行的候选统一记到对应规则名下，便于白名单…（共 171 字）
- `tests/agent/test_knowledge_service.py:1287` Agent 看得到的东西里不得写死**这一期**的数据：当期活动名与它的 stageId 前缀。 口径与 M19 §4.3「不写会随版本失效的内容」一致。扫描三处 Agent 可见面：内置知识 条目（每轮注入索引、`read_knowled…（共 416 字）
- …… 另有 44 条，见 inventory.json

## 自指式讲解腔（medium｜用户可见 0 / 开发向 10）

**开发向**

- `tests/agent/test_activity_tools.py:1` ``get_activity_stages`` 工具（M25 规格 §5.6）。 用假活动服务（返回现造的 :class:`ActivityReport`）与假客户端把参数形态、返回信封与降级路径 写死；本机资源的推断规则由 ``tests…（共 274 字）
- `tests/agent/test_llm.py:71` 只断言错误映射的用例用它：重试有自己的用例覆盖，这里不该被退避拖慢。
- `tests/api/test_system_router.py:76` 无前缀 include 本模块也要得到完整路径。 不按 ``{r.path for r in app.routes}`` 断言：fastapi 0.141 的 ``include_router`` 是惰性的，``app.routes`` 里…（共 215 字）
- `tests/core/test_hardware_smoke.py:1` M1-14 真机冒烟测试：真实 MaaCore + 真实设备的加载 / 连接 / 截图 / 原子操作 / 最短任务。 本文件把 M1-13 的命令行脚本（``scripts/core_smoke.py``）固化成可重复执行的 pytest …（共 2531 字）
- `tests/core/test_worker.py:1` M1-07 ``core/worker.py`` 的端到端测试：真实 spawn 子进程 + 真实 Queue。 这里不加载 MaaCore、不连设备：``boot_config["asst_factory"]`` 指向 ``tests.f…（共 1086 字）
- `tests/db/test_repositories_log.py:282` 每批最多 ``PURGE_BATCH_SIZE`` 行：两维都分批，单次调用内部循环删完。 monkeypatch 把批上限缩到 2，用 6 行数据逼出多批路径（真按 5000 行造数据 太慢，且批量边界才是这里要钉住的行为）。
- …… 另有 4 条，见 inventory.json

## 省略号（medium｜用户可见 0 / 开发向 7）

**开发向**

- `tests/agent/test_runtime.py:3159` 正在读取日志……
- `tests/agent/test_runtime.py:3195` 正在读取日志…… {INTERRUPTED_NOTICE}
- `tests/agent/test_runtime.py:3576` 正在排查……
- `tests/services/test_game_roguelike.py:157` {{Cbox2|mdi=true|icon=information-variant-circle|text='''在非标准模式下……'''}} {{收藏品/common |名称=热水壶 |iconId=rogue_6_relic_legac…（共 401 字）
- `tests/services/test_game_story.py:235` 杜宾：……我也希望是你的错觉。
- …… 另有 2 条，见 inventory.json

## 「…即可」机器腔（medium｜用户可见 0 / 开发向 4）

**开发向**

- `tests/db/test_repositories_pipeline.py:56` 写入即可读；``get_by_idempotency_key`` 只按真实 key 命中，空 key 返回 None。
- `tests/db/test_repositories_schedule_setting.py:225` 写入即可读；默认值、``template`` 原样存取、入参提交后仍可读。
- `tests/domain/test_route_inventory.py:1` 全量路由清单契约（docs/05 §6 与 web/openapi.json 的一致性门禁）。 docs/05 §6 是路由清单，`web/openapi.json` 是同一批路由的机器可读投影（由 ``scripts/dump_opena…（共 902 字）
- `tests/services/test_copilot_notify.py:136` 通知不能因为一个认不出的结果值就发不出去；记一条 warning 即可。

## 「不是…而是…」对照句（high｜用户可见 0 / 开发向 2）

**开发向**

- `tests/db/test_repositories_update_notify_resource.py:319` ``update_progress`` 只写给到的列；``None`` 不是「置 NULL」而是「不改」。
- `tests/domain/test_errors.py:1` 全量错误码契约（M3-02，docs/05 §4.1–§4.16 十六张表）。 三组断言： 1. **形状**：``ErrorCode`` 是 StrEnum 且成员名 == 取值；全量 105 条；``ERROR_HTTP_STATUS`…（共 715 字）

## 「首先…其次…」罗列腔（high｜用户可见 0 / 开发向 1）

**开发向**

- `tests/agent/test_runtime.py:2843` 不用「首先/其次/最后/另外」

## 提示腔（medium｜用户可见 0 / 开发向 1）

**开发向**

- `tests/db/conftest.py:1` ``tests/db`` 共享 fixture：临时 SQLite 库 + 异步会话（M2-07 建，M2-08~M2-12 直接用）。 本仓**没有 pytest-asyncio**（不要为异步用例新增插件依赖），所以这里的 fixtur…（共 1325 字）

## emoji/符号装饰（high｜用户可见 0 / 开发向 1）

**开发向**

- `tests/services/test_callback_translator.py:337` 3⭐通关1-7 掉落统计: 龙门币: 10(+2) 作战记录: 4(+0)

