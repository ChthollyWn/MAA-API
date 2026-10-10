# AI 腔规则命中：测试内的中文断言（改文案的连带影响面）

总计 2445 条文本。命中按规则汇总，「用户可见」只统计界面/对外文案，
「开发向」是注释、docstring、内部常量（维护者看得到，终端用户看不到）。

| 规则 | 严重度 | 用户可见 | 开发向 | 合计 |
|---|---|---|---|---|
| 绝对化规定腔 | medium | 0 | 59 | 59 |
| 粗体强调 | medium | 0 | 41 | 41 |
| 「A 而不是 B」对照腔 | high | 0 | 35 | 35 |
| 破折号当冒号用 | high | 0 | 34 | 34 |
| 自指式讲解腔 | medium | 0 | 9 | 9 |
| 省略号 | medium | 0 | 5 | 5 |
| 「…即可」机器腔 | medium | 0 | 3 | 3 |
| 「不是…而是…」对照句 | high | 0 | 2 | 2 |
| 「首先…其次…」罗列腔 | high | 0 | 1 | 1 |
| 提示腔 | medium | 0 | 1 | 1 |
| emoji/符号装饰 | high | 0 | 1 | 1 |

## 绝对化规定腔（medium｜用户可见 0 / 开发向 59）

**开发向**

- `tests/agent/test_knowledge_service.py:1279` Agent 看得到的东西里不得写死**这一期**的数据：当期活动名与它的 stageId 前缀。 口径与 M19 §4.3「不写会随版本失效的内容」一致。扫描三处 Agent 可见面：内置知识 条目（每轮注入索引、`read_knowled…（共 416 字）
- `tests/agent/test_llm.py:341` 模型名写错时要能在 details 里看到上游原话，且凭据不得回显。
- `tests/agent/test_runtime.py:2831` 一律忽略
- `tests/api/__init__.py:1` ``tests/api``：接入层用例（M3-04 起）。 本目录的用例**不得 import ``maa_api.main``**（M3-09 之前它还是旧装配）， 被测 app 一律在用例里用 ``FastAPI()`` 现搭；公共夹具…（共 148 字）
- `tests/api/conftest.py:1` ``tests/api`` 共享夹具（M3-04 建，M3-05~M3-09 直接用）。 本目录的用例**不得 import ``maa_api.main``**（M3-09 之前它还是旧装配：import 期 读真实配置、建目录，甚至可能…（共 1523 字）
- `tests/api/test_app_skeleton.py:244` 进入 TestClient 即执行 lifespan 第 1–2 步：配置加载 + 迁移到 head。 断言版本行落在**临时库**里（``isolated_db`` 换掉了模块级 engine/session_factory 与三条 UR…（共 164 字）
- …… 另有 53 条，见 inventory.json

## 粗体强调（medium｜用户可见 0 / 开发向 41）

**开发向**

- `tests/agent/test_knowledge_service.py:1` M18 Task 4: the knowledge store merges built-in Markdown with user overrides. 规格 §4.1。三条纪律： - 内置条目随代码发布，``agent_knowledg…（共 369 字）
- `tests/agent/test_knowledge_service.py:207` 三份规格 §4.6 的 markdown 代码块**并集**，按 frontmatter 里的 id 索引。 M18 的 5 个块、M19 的 26 个块与 M24 的 1 个块合起来恰好是全部内置条目。
- `tests/agent/test_knowledge_service.py:260` §4.6 是内置条目的登记表：条目集合与 frontmatter 必须逐字一致。 三份规格的块并集必须**恰好**覆盖 ``builtin/`` 目录：多一条（规格没登记） 或少一条（文件漏了）都要失败。B3 文案审计按规范改写了部分正文（…（共 204 字）
- `tests/agent/test_knowledge_service.py:1279` Agent 看得到的东西里不得写死**这一期**的数据：当期活动名与它的 stageId 前缀。 口径与 M19 §4.3「不写会随版本失效的内容」一致。扫描三处 Agent 可见面：内置知识 条目（每轮注入索引、`read_knowled…（共 416 字）
- `tests/agent/test_runtime.py:56` 只保留对话链路上的请求。 能力探测与标题生成都是运行时自己发的一次性调用，不是用户的一轮对话，两者的 共同特征是**没有系统提示词**（`build_model_context` 的第一条永远是 system）。 不能按「有没有工具」区分：…（共 160 字）
- `tests/agent/test_runtime.py:2220` OpenAI 协议要求 assistant 的 tool_calls 后面**紧跟**同样数量的 tool 消息。 这里曾经插入一条带截图的 `user` 消息（把画面作为图片输入），于是 「assistant(tool_calls) → …（共 269 字）
- …… 另有 35 条，见 inventory.json

## 「A 而不是 B」对照腔（high｜用户可见 0 / 开发向 35）

**开发向**

- `tests/agent/test_activity_tools.py:180` 没有装配索引、默认门面也读不到资源时，报的是资源不可用而不是空结果。
- `tests/agent/test_knowledge_service.py:1279` Agent 看得到的东西里不得写死**这一期**的数据：当期活动名与它的 stageId 前缀。 口径与 M19 §4.3「不写会随版本失效的内容」一致。扫描三处 Agent 可见面：内置知识 条目（每轮注入索引、`read_knowled…（共 416 字）
- `tests/agent/test_media_tools.py:1259` 字段级超限走既有校验链路（``TOOL_ARGS_INVALID``）；语义级越界才是 ``INVALID_PARAMETER``。
- `tests/agent/test_runtime.py:1797` 截图必须作为 tool 消息的图片部分，而不是另起一条 user 消息
- `tests/agent/test_runtime.py:2267` 截图应当作为 tool 消息的多模态 content，而不是另起一条 user 消息
- `tests/api/conftest.py:1` ``tests/api`` 共享夹具（M3-04 建，M3-05~M3-09 直接用）。 本目录的用例**不得 import ``maa_api.main``**（M3-09 之前它还是旧装配：import 期 读真实配置、建目录，甚至可能…（共 1523 字）
- …… 另有 29 条，见 inventory.json

## 破折号当冒号用（high｜用户可见 0 / 开发向 34）

**开发向**

- `tests/agent/test_runtime.py:3461` 规格 §5：批次中途中断后每个 ``tool_calls`` 都有对应 tool 消息。 中断点由测试控制：第一条 tool 消息落库之后、第二条之前挂住这一轮，正好落在 「补历史」的中间窗口上——这是最容易补重复或漏补的一条路径。
- `tests/agent/test_runtime.py:3953` 轮次失败要留下两样东西：日志里的一行，和会话里的一条说明。 2026-10-09 的实况是两样都没有：用户消息已经落库，回复与错误原因都没留下，用户 第二天只能问「Agent 怎么又不好使了」，而服务端一个字都查不到——`AppError`…（共 147 字）
- `tests/agent/test_runtime.py:4054` 一次瞬时抖动不能让整个进程永久「能力不足」。 旧实现无条件缓存探查结果：探测失败一次，之后每一轮都在几秒内报「当前模型未通过 function calling 能力检查」，只有重启服务才恢复——用户看到的就是「Agent 又用不了 了，重启…（共 172 字）
- `tests/api/conftest.py:1` ``tests/api`` 共享夹具（M3-04 建，M3-05~M3-09 直接用）。 本目录的用例**不得 import ``maa_api.main``**（M3-09 之前它还是旧装配：import 期 读真实配置、建目录，甚至可能…（共 1523 字）
- `tests/api/test_app_skeleton.py:1` M3-09 应用装配验收：lifespan、CORS、OpenAPI 元数据与尾斜杠（docs/02 §7、docs/05 §11/§5.1/§2.5）。 被测对象是模块级的 :data:`maa_api.main.app`（本卡起 ``t…（共 1215 字）
- `tests/api/test_auth.py:1` 四渠道 token 鉴权与会话依赖（M3-06；docs/05 §5、§4.1、§5.2、§5.3）。 被测 app 全部在本文件里现搭（``FastAPI(...)`` + M3-04 的 :func:`~maa_api.api.erro…（共 937 字）
- …… 另有 28 条，见 inventory.json

## 自指式讲解腔（medium｜用户可见 0 / 开发向 9）

**开发向**

- `tests/agent/test_activity_tools.py:1` ``get_activity_stages`` 工具（M24 规格 §4.2）。 用假索引把三种参数形态与失败路径写死；真资源的形状由 ``tests/services/test_activity_index.py`` 覆盖，这里不重复。
- `tests/agent/test_llm.py:71` 只断言错误映射的用例用它：重试有自己的用例覆盖，这里不该被退避拖慢。
- `tests/api/test_system_router.py:76` 无前缀 include 本模块也要得到完整路径。 不按 ``{r.path for r in app.routes}`` 断言：fastapi 0.141 的 ``include_router`` 是惰性的，``app.routes`` 里…（共 215 字）
- `tests/core/test_hardware_smoke.py:1` M1-14 真机冒烟测试：真实 MaaCore + 真实设备的加载 / 连接 / 截图 / 原子操作 / 最短任务。 本文件把 M1-13 的命令行脚本（``scripts/core_smoke.py``）固化成可重复执行的 pytest …（共 2531 字）
- `tests/core/test_worker.py:1` M1-07 ``core/worker.py`` 的端到端测试：真实 spawn 子进程 + 真实 Queue。 这里不加载 MaaCore、不连设备：``boot_config["asst_factory"]`` 指向 ``tests.f…（共 1086 字）
- `tests/db/test_repositories_log.py:282` 每批最多 ``PURGE_BATCH_SIZE`` 行：两维都分批，单次调用内部循环删完。 monkeypatch 把批上限缩到 2，用 6 行数据逼出多批路径（真按 5000 行造数据 太慢，且批量边界才是这里要钉住的行为）。
- …… 另有 3 条，见 inventory.json

## 省略号（medium｜用户可见 0 / 开发向 5）

**开发向**

- `tests/agent/test_runtime.py:3159` 正在读取日志……
- `tests/agent/test_runtime.py:3195` 正在读取日志…… {INTERRUPTED_NOTICE}
- `tests/agent/test_runtime.py:3576` 正在排查……
- …… 另有 2 条，见 inventory.json

## 「…即可」机器腔（medium｜用户可见 0 / 开发向 3）

**开发向**

- `tests/db/test_repositories_pipeline.py:56` 写入即可读；``get_by_idempotency_key`` 只按真实 key 命中，空 key 返回 None。
- `tests/db/test_repositories_schedule_setting.py:225` 写入即可读；默认值、``template`` 原样存取、入参提交后仍可读。
- `tests/domain/test_route_inventory.py:1` 全量路由清单契约（docs/05 §6 与 web/openapi.json 的一致性门禁）。 docs/05 §6 是路由清单，`web/openapi.json` 是同一批路由的机器可读投影（由 ``scripts/dump_opena…（共 902 字）

## 「不是…而是…」对照句（high｜用户可见 0 / 开发向 2）

**开发向**

- `tests/db/test_repositories_update_notify_resource.py:319` ``update_progress`` 只写给到的列；``None`` 不是「置 NULL」而是「不改」。
- `tests/domain/test_errors.py:1` 全量错误码契约（M3-02，docs/05 §4.1–§4.15 十五张表）。 三组断言： 1. **形状**：``ErrorCode`` 是 StrEnum 且成员名 == 取值；全量 105 条；``ERROR_HTTP_STATUS`…（共 715 字）

## 「首先…其次…」罗列腔（high｜用户可见 0 / 开发向 1）

**开发向**

- `tests/agent/test_runtime.py:2843` 不用「首先/其次/最后/另外」

## 提示腔（medium｜用户可见 0 / 开发向 1）

**开发向**

- `tests/db/conftest.py:1` ``tests/db`` 共享 fixture：临时 SQLite 库 + 异步会话（M2-07 建，M2-08~M2-12 直接用）。 本仓**没有 pytest-asyncio**（不要为异步用例新增插件依赖），所以这里的 fixtur…（共 1325 字）

## emoji/符号装饰（high｜用户可见 0 / 开发向 1）

**开发向**

- `tests/services/test_callback_translator.py:321` 3⭐通关1-7 掉落统计: 龙门币: 10(+2) 作战记录: 4(+0)

