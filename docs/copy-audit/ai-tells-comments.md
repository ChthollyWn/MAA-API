# AI 腔规则命中：源码注释（维护者可见）

总计 7927 条文本。命中按规则汇总，「用户可见」只统计界面/对外文案，
「开发向」是注释、docstring、内部常量（维护者看得到，终端用户看不到）。

| 规则 | 严重度 | 用户可见 | 开发向 | 合计 |
|---|---|---|---|---|
| 粗体强调 | medium | 0 | 192 | 192 |
| 「A 而不是 B」对照腔 | high | 0 | 79 | 79 |
| 破折号当冒号用 | high | 0 | 63 | 63 |
| 绝对化规定腔 | medium | 0 | 46 | 46 |
| 自指式讲解腔 | medium | 0 | 8 | 8 |
| 「…即可」机器腔 | medium | 0 | 7 | 7 |
| 提示腔 | medium | 0 | 3 | 3 |
| 省略号 | medium | 0 | 3 | 3 |
| emoji/符号装饰 | high | 0 | 2 | 2 |
| 「不是…而是…」对照句 | high | 0 | 1 | 1 |

## 粗体强调（medium｜用户可见 0 / 开发向 192）

**开发向**

- `maa_api/agent/knowledge/__init__.py:654` 读全表是为了返回**写入后的生效内容**（合并内置值），不再有上限判定：
- `maa_api/agent/knowledge/builtin/operator-classes.md:12` *先锋 —— 对部署有更佳效果的职业**。部署费用普遍较低或适中，能产出额外部署费用，多用于开局展开阵型和补给费用。
- `maa_api/agent/knowledge/builtin/operator-classes.md:20` *近卫 —— 近战攻击为主的输出职业**。人数最多的职业，地面输出的主要来源。
- `maa_api/agent/knowledge/builtin/operator-classes.md:36` *狙击 —— 擅长远程物理攻击的输出职业**。在高台造成物理伤害，主要职责之一是打空中单位。
- `maa_api/agent/knowledge/builtin/operator-classes.md:48` *术师 —— 擅长远程法术攻击的输出职业**。在高台（乃至全局）提供法术输出。
- `maa_api/agent/knowledge/builtin/operator-classes.md:59` *医疗 —— 提供治疗、回复生命的职业**。高台单位，俗称「奶妈」。
- …… 另有 186 条，见 inventory.json

## 「A 而不是 B」对照腔（high｜用户可见 0 / 开发向 79）

**开发向**

- `maa_api/agent/knowledge/__init__.py:522` 构造即加载：内置文件坏了要在装配处（启动）就炸，而不是等第一次请求
- `maa_api/agent/llm.py:49` : 所以这条映射是用户唯一能看出「窗口装不下」的地方，而不是笼统的请求失败。
- `maa_api/agent/llm.py:327` 把上游的拒绝翻译成可读的 400，而不是笼统的「请求失败」。
- `maa_api/agent/runtime.py:1806` 标题跟着会话自己的模型走，而不是当前设置里的模型：会话的 model/base_url 是
- `maa_api/core/client.py:621` shield：超时由这里显式 set_exception，而不是让 wait_for 取消 Future，
- `maa_api/core/worker.py:283` 跳过而非报错（docs/03 §7）。
- …… 另有 73 条，见 inventory.json

## 破折号当冒号用（high｜用户可见 0 / 开发向 63）

**开发向**

- `maa_api/agent/knowledge/builtin/operator-classes.md:12` *先锋 —— 对部署有更佳效果的职业**。部署费用普遍较低或适中，能产出额外部署费用，多用于开局展开阵型和补给费用。
- `maa_api/agent/knowledge/builtin/operator-classes.md:20` *近卫 —— 近战攻击为主的输出职业**。人数最多的职业，地面输出的主要来源。
- `maa_api/agent/knowledge/builtin/operator-classes.md:36` *狙击 —— 擅长远程物理攻击的输出职业**。在高台造成物理伤害，主要职责之一是打空中单位。
- `maa_api/agent/knowledge/builtin/operator-classes.md:48` *术师 —— 擅长远程法术攻击的输出职业**。在高台（乃至全局）提供法术输出。
- `maa_api/agent/knowledge/builtin/operator-classes.md:59` *医疗 —— 提供治疗、回复生命的职业**。高台单位，俗称「奶妈」。
- `maa_api/agent/knowledge/builtin/operator-classes.md:68` *重装 —— 高生命值与防御力的职业**。在地面阻挡多个敌人，俗称「坦克」「T」。
- …… 另有 57 条，见 inventory.json

## 绝对化规定腔（medium｜用户可见 0 / 开发向 46）

**开发向**

- `scripts/api_smoke.py:363` 环境变量层优先级高于 yaml：开发机上的 MAA_* 会让结果随环境漂移，一律清掉。
- `scripts/maa-api-service.sh:130` 只看「是不是 python」确实不够精确，但比「监听中的端口一律算别人的进程」好：
- `scripts/maa-api-service.sh:271` 直接死掉，而服务其实已经起来了——最糟的失败方式。变量一律 init 后再读。
- `web/src/features/agent/QuestionCard.tsx:110` 必须选中才能提交；跳过不需要选。整卡在终态一律禁用。
- `web/src/features/agent/chart/EChartSurface.tsx:47` `onError`，由父组件打开数据表兜底——图表永远不是唯一的信息来源。
- `web/src/features/agent/format.ts:30` 会话累计 token 的展示：**大于等于 0.1M 一律按百万读**（用户要求的统一单位），
- …… 另有 40 条，见 inventory.json

## 自指式讲解腔（medium｜用户可见 0 / 开发向 8）

**开发向**

- `maa_api/model/util/updater.py:129` 请求的是https://ota.maa.plus/MaaAssistantArknights/api/version/stable.json，或其他版本类型对应的url
- `web/src/components/layout/BrandMark.tsx:9` 插画自带画框与透明留白，所以这里不加圆角容器与内边距，只保留尺寸类；调用方
- `web/src/components/ui/use-authed-image.ts:102` 依赖收敛成基本类型：调用方每次渲染新建 source 对象也不会重复取图。
- `web/src/features/agent/AgentPage.tsx:433` 普通回合出错时服务端不会把错误写成消息，这里不单独存下来，实时副本一清空就看不到了。
- `web/src/features/agent/AgentPage.tsx:491` tool_result 分支摘掉，所以这里不会把已经结束的一轮永久留下）。
- `web/src/features/agent/markdown.robustness.test.ts:49` 只有下面这些**结构性标记**允许消失（它们变成了元素属性或列表序号）。
- …… 另有 2 条，见 inventory.json

## 「…即可」机器腔（medium｜用户可见 0 / 开发向 7）

**开发向**

- `maa_api/agent/questions.py:198` 事件没等到、时间也用完了：置过期；CAS 失败说明别人抢先处理，读回真值即可。
- `maa_api/services/retention_service.py:296` 库里的相对路径越界属脏数据：跳过并留一条 debug 即可，不影响这轮清理。
- `scripts/probe_core_api.py:109` 依赖库以 @rpath/@loader_path 记录，按绝对路径 dlopen 即可解析；
- `web/src/features/agent/AgentPage.test.tsx:1093` 错误横幅与落库的 assistant 消息都会出现这句话，断言存在即可。
- `web/src/features/agent/markdown.tsx:235` - 括号还没闭合（`[文档](https://exa`）：把到文本末尾的内容当目标，链接立即可见；
- `web/src/types/api.d.ts:2987` @description 切换到已登录的账号，输入登录名的一部分即可；不填则不切换
- …… 另有 1 条，见 inventory.json

## 提示腔（medium｜用户可见 0 / 开发向 3）

**开发向**

- `maa_api/services/stage_resolver.py:111` 注意：overview.json 的值是驼峰（``stageId`` / ``levelId``），返回给上层才转蛇形。
- `scripts/spike_subprocess_isolation.py:181` 注意：user_dir 必须已存在，否则 AsstSetUserDir 返回 False。
- `tests/api/test_app_skeleton.py:304` 注意：iter_route_contexts 对内置 /docs、/openapi.json 等给的是 RouteContext

## 省略号（medium｜用户可见 0 / 开发向 3）

**开发向**

- `tests/domain/test_errors.py:43` : 表格数据行形如 ``| `CODE` | 404 | 含义…… |``；表头与文字段落都不匹配。
- `tests/domain/test_route_inventory.py:75` : §6 开头那句：``共 101 条 HTTP 端点……另有 1 条 WebSocket（§6.2）与 4 条静态与文档入口（§6.17）。``
- `tests/domain/test_route_inventory.py:83` : 表格数据行：``| GET | `/api/system/health` | …… |``。

## emoji/符号装饰（high｜用户可见 0 / 开发向 2）

**开发向**

- `maa_api/db/migrations/env.py:85` ⚠️ 必须在建任何表之前、且写在 connection 上（M2-01 实测）。
- `maa_api/db/migrations/env.py:88` ⚠️ 紧跟一次 commit，不能省。exec_driver_sql 会 autobegin 一个

## 「不是…而是…」对照句（high｜用户可见 0 / 开发向 1）

**开发向**

- `tests/db/test_repositories_update_notify_resource.py:517` 这不是缺陷，是「校验按 type 分发的 Pydantic 模型在服务层」的边界。

