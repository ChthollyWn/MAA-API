# AI 腔规则命中：源码注释（维护者可见）

总计 12348 条文本。命中按规则汇总，「用户可见」只统计界面/对外文案，
「开发向」是注释、docstring、内部常量（维护者看得到，终端用户看不到）。

| 规则 | 严重度 | 用户可见 | 开发向 | 合计 |
|---|---|---|---|---|
| 粗体强调 | medium | 0 | 277 | 277 |
| 破折号当冒号用 | high | 0 | 125 | 125 |
| 「A 而不是 B」对照腔 | high | 0 | 110 | 110 |
| 绝对化规定腔 | medium | 0 | 64 | 64 |
| 「…即可」机器腔 | medium | 0 | 19 | 19 |
| 自指式讲解腔 | medium | 0 | 18 | 18 |
| 省略号 | medium | 0 | 4 | 4 |
| 宣传词 | medium | 0 | 3 | 3 |
| 提示腔 | medium | 0 | 3 | 3 |
| emoji/符号装饰 | high | 0 | 2 | 2 |
| 「不是…而是…」对照句 | high | 0 | 2 | 2 |
| 总结腔 | high | 0 | 1 | 1 |

## 粗体强调（medium｜用户可见 0 / 开发向 277）

**开发向**

- `maa_api/agent/knowledge/__init__.py:1027` 读全表是为了返回**写入后的生效内容**（合并内置值），不再有上限判定：
- `maa_api/agent/knowledge/builtin/operator-classes.md:12` *先锋 —— 对部署有更佳效果的职业**。部署费用普遍较低或适中，能产出额外部署费用，多用于开局展开阵型和补给费用。
- `maa_api/agent/knowledge/builtin/operator-classes.md:20` *近卫 —— 近战攻击为主的输出职业**。人数最多的职业，地面输出的主要来源。
- `maa_api/agent/knowledge/builtin/operator-classes.md:36` *狙击 —— 擅长远程物理攻击的输出职业**。在高台造成物理伤害，主要职责之一是打空中单位。
- `maa_api/agent/knowledge/builtin/operator-classes.md:48` *术师 —— 擅长远程法术攻击的输出职业**。在高台（乃至全局）提供法术输出。
- `maa_api/agent/knowledge/builtin/operator-classes.md:59` *医疗 —— 提供治疗、回复生命的职业**。高台单位，俗称「奶妈」。
- …… 另有 271 条，见 inventory.json

## 破折号当冒号用（high｜用户可见 0 / 开发向 125）

**开发向**

- `maa_api/agent/knowledge/builtin/operator-classes.md:12` *先锋 —— 对部署有更佳效果的职业**。部署费用普遍较低或适中，能产出额外部署费用，多用于开局展开阵型和补给费用。
- `maa_api/agent/knowledge/builtin/operator-classes.md:20` *近卫 —— 近战攻击为主的输出职业**。人数最多的职业，地面输出的主要来源。
- `maa_api/agent/knowledge/builtin/operator-classes.md:36` *狙击 —— 擅长远程物理攻击的输出职业**。在高台造成物理伤害，主要职责之一是打空中单位。
- `maa_api/agent/knowledge/builtin/operator-classes.md:48` *术师 —— 擅长远程法术攻击的输出职业**。在高台（乃至全局）提供法术输出。
- `maa_api/agent/knowledge/builtin/operator-classes.md:59` *医疗 —— 提供治疗、回复生命的职业**。高台单位，俗称「奶妈」。
- `maa_api/agent/knowledge/builtin/operator-classes.md:68` *重装 —— 高生命值与防御力的职业**。在地面阻挡多个敌人，俗称「坦克」「T」。
- …… 另有 119 条，见 inventory.json

## 「A 而不是 B」对照腔（high｜用户可见 0 / 开发向 110）

**开发向**

- `maa_api/agent/knowledge/__init__.py:840` 构造即加载：内置文件坏了要在装配处（启动）就炸，而不是等第一次请求
- `maa_api/agent/llm.py:49` : 所以这条映射是用户唯一能看出「窗口装不下」的地方，而不是笼统的请求失败。
- `maa_api/agent/llm.py:327` 把上游的拒绝翻译成可读的 400，而不是笼统的「请求失败」。
- `maa_api/agent/runtime.py:1808` 标题跟着会话自己的模型走，而不是当前设置里的模型：会话的 model/base_url 是
- `maa_api/api/routers/account.py:50` : ``recognition``，见服务层），所以这里用 Literal 而不是自由字符串。
- `maa_api/core/client.py:621` shield：超时由这里显式 set_exception，而不是让 wait_for 取消 Future，
- …… 另有 104 条，见 inventory.json

## 绝对化规定腔（medium｜用户可见 0 / 开发向 64）

**开发向**

- `maa_api/agent/preconditions.py:51` : `StartUp` 的小写形式：判定一律大小写不敏感（规格 §4.1）。
- `maa_api/agent/tools/pipeline.py:216` 取不到 error 结构（如超时返回的运行中快照）一律按「不提示」处理。
- `maa_api/db/migrations/versions/0018_unknown_training.py:47` : 识别永远不写、只有人工/Agent 才能给出的三列。
- `maa_api/services/account/capture.py:19` *采集失败绝不影响流水线回调**：:meth:`AccountCaptureService.handle_callback` 吞掉
- `maa_api/services/copilot/rules.py:72` : ``Click`` / ``Swipe`` 等动作一律放行（Q38）。
- `maa_api/services/external/http.py:193` 主机白名单在发请求之前判：来源登记表里没有的主机一律拒绝。
- …… 另有 58 条，见 inventory.json

## 「…即可」机器腔（medium｜用户可见 0 / 开发向 19）

**开发向**

- `maa_api/agent/confirmation.py:364` 放在确认已提交之后：授权写失败时确认仍是 approved，用户重批一次即可补齐；
- `maa_api/agent/knowledge/__init__.py:845` : M27：账号数据读取器（有 `async summary()` 即可）。没装时整段不插。
- `maa_api/agent/knowledge/__init__.py:847` : M28：当期卡池读取器（有 `async pools()` 即可）。没装或外服时整段不插。
- `maa_api/agent/questions.py:198` 事件没等到、时间也用完了：置过期；CAS 失败说明别人抢先处理，读回真值即可。
- `maa_api/main.py:1263` 配成 ≥ 60 分钟才注册。每 tick 现读配置，所以改配置文件后重启一次即可生效；
- `maa_api/services/copilot/rules.py:330` : 实际比对的成员：``opers`` 是自己，分组是全部成员（任一满足即可）。
- …… 另有 13 条，见 inventory.json

## 自指式讲解腔（medium｜用户可见 0 / 开发向 18）

**开发向**

- `maa_api/agent/knowledge/__init__.py:136` : 以 `## <section>` 分节；工具 `update_user_profile` 与确认卡片的差异计算共用本模块的两个
- `maa_api/main.py:931` 账号服务在本函数后面才装配（通知绑定更早）；拿不到就由计划服务按会话工厂
- `maa_api/main.py:1304` 养成计划的装配依赖（M31）：计划服务在本函数前段就建好了（那时定时任务
- `maa_api/model/util/updater.py:129` 请求的是https://ota.maa.plus/MaaAssistantArknights/api/version/stable.json，或其他版本类型对应的url
- `maa_api/services/account/planner.py:23` # 实现裁决（规格没写死、由本模块定，M27 T8）
- `maa_api/services/copilot/grant.py:4` *INTERNAL 调用方**生效（策略分支在 ``agent/policy.py``，本模块不判断调用方）：
- …… 另有 12 条，见 inventory.json

## 省略号（medium｜用户可见 0 / 开发向 4）

**开发向**

- `maa_api/agent/knowledge/convergence.py:86` : "body": "……最后一版正文全文……",
- `tests/domain/test_errors.py:43` : 表格数据行形如 ``| `CODE` | 404 | 含义…… |``；表头与文字段落都不匹配。
- `tests/domain/test_route_inventory.py:80` : §6 开头那句：``共 101 条 HTTP 端点……另有 1 条 WebSocket（§6.2）与 4 条静态与文档入口（§6.17）。``
- `tests/domain/test_route_inventory.py:88` : 表格数据行：``| GET | `/api/system/health` | …… |``。

## 宣传词（medium｜用户可见 0 / 开发向 3）

**开发向**

- `maa_api/services/external/prts.py:554` : 供领域层构造条件用的便捷函数：`id_condition("关卡id", "act3break_01")`。
- `maa_api/services/external/prts.py:566` : 供领域层构造条件用的便捷函数：`page_condition("7-18 爱国者之死")`。
- `tests/services/test_game_drops.py:553` 供 get_item / get_stage 用的两个便捷方法

## 提示腔（medium｜用户可见 0 / 开发向 3）

**开发向**

- `maa_api/services/stage_resolver.py:111` 注意：overview.json 的值是驼峰（``stageId`` / ``levelId``），返回给上层才转蛇形。
- `scripts/spike_subprocess_isolation.py:181` 注意：user_dir 必须已存在，否则 AsstSetUserDir 返回 False。
- `tests/api/test_app_skeleton.py:304` 注意：iter_route_contexts 对内置 /docs、/openapi.json 等给的是 RouteContext

## emoji/符号装饰（high｜用户可见 0 / 开发向 2）

**开发向**

- `maa_api/db/migrations/env.py:85` ⚠️ 必须在建任何表之前、且写在 connection 上（M2-01 实测）。
- `maa_api/db/migrations/env.py:88` ⚠️ 紧跟一次 commit，不能省。exec_driver_sql 会 autobegin 一个

## 「不是…而是…」对照句（high｜用户可见 0 / 开发向 2）

**开发向**

- `tests/db/test_repositories_update_notify_resource.py:517` 这不是缺陷，是「校验按 type 分发的 Pydantic 模型在服务层」的边界。
- `tests/services/test_upgrade_planner.py:399` M29：升级经验 / 龙门币用内置的官方数值表。显式注入而不是让规划器惰性加载，是为了

## 总结腔（high｜用户可见 0 / 开发向 1）

**开发向**

- `maa_api/services/copilot/notify.py:148` 浏览器推送读 body：同一句话，避免渠道与推送两边文案漂移。

