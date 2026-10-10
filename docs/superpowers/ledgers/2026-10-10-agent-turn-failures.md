# 2026-10-10 Agent 轮次失败处理（日志 / 能力缓存 / 重试）

本次不是里程碑，是用户报「为什么 agent 又无法使用了」之后的一次只读排查加修复。排查过程与裁决
记录在 [docs/13 §8.39](../../13-决策记录.md)，规格改动落在 [docs/11](../../11-Agent模块设计.md)
§8.1 与 §8.2，这里只记执行与验证。

## 起点

用户现象：Agent 页里 2026-10-09 21:47 与 21:50 的两条消息没有回复，第二天打开页面才发现。

只读排查（先不动代码）：

- 会话 `7cde979c`：两条 user 消息都落库了，其后没有 assistant 消息、没有任何 tool 消息，
  `agent_audit` 最后一条停在当天 19:43；
- `resource/log/uvicorn.err` 与 `2026-10-09.error.log`：那个时段只有 HTTP 访问日志，
  没有任何应用级日志（全量日志里连一行 `Agent turn failed` 都没有）；
- 时间基准：日志文件写本地时间、DB 的 `created_at` 落 UTC，同一事件相差 8 小时。用一次线上
  `POST /api/agent/capabilities/check` 比对两处才确认，否则「POST 与用户消息落库是同一秒」这件事
  会被误读成差 8 小时；
- 克隆会话重放（复制 `agent_session` + `agent_message` 到临时会话再发消息，原会话未动）：
  同一段历史加一条新消息可以正常跑到 `list_schedules` 之后 `ask_user` 提问；
- 额度与配置：`/user/balance` 560.94 CNY、`capabilities/check` 返回 `tool_calling`/`vision` 全 true。

结论：那一轮死在第一次上游请求上；三个缺陷（失败不留痕、能力探查的失败态被永久缓存、不重试且
失败后会话留半截）见 [docs/13 §8.39](../../13-决策记录.md) 第二节。

排查期间创建的临时会话与两条 `agent_audit` 记录已删除，原会话 24 条消息未改动。

## 改动

| 文件 | 改动 |
|---|---|
| `maa_api/agent/llm.py` | 拆出 `_stream_chat_completion_once()`，`stream_chat_completion()` 变成「这一次尝试还没吐过 chunk 才重试」的包装；新增 `_is_retryable()` / `_attempts_for()` / `_retry_delay()` / `_parse_retry_after()` 与 5 个模块常量 |
| `maa_api/agent/runtime.py` | 新增 `CAPABILITY_FAILURE_TTL_SECONDS` / `TURN_FAILED_FINISH_REASON` / `TURN_FAILURE_NOTICE_CODES` / 两条结尾文案 / `TURN_FAILURE_GENERIC_MESSAGE`；新增 `_cached_capabilities()` 与 `_remember_capabilities()`；`_probe_capabilities()` 记探查失败日志、按失败态设置过期；`_produce_turn()` 记轮次失败日志并补写说明；新增 `_record_turn_failure()` 与 `_error_brief()`；`_make_client()` 支持 `max_attempts`，标题调用显式传 1 |
| `tests/agent/test_llm.py` | 新增 8 条重试用例（含「已吐内容不重放」「错误映射不重试」「重试次数上限」「超时只多一次」「429 退避策略」）；新增 `_single_attempt_client()`，7 处「只断言错误映射」的用例改用它，测试文件从 30 秒回到 0.4 秒 |
| `tests/agent/test_runtime.py` | 新增 6 条：失败留痕与说明消息、上下文超长的结尾文案、说明的补写边界、瞬时探查失败可自愈、探查失败日志、成功态不过期 |
| `docs/11-Agent模块设计.md` | §8.1 补能力缓存规则，§8.2 补「上游抖动的重试与失败留痕」 |
| `docs/05-API规范与路由清单.md` | `capabilities/check` 那条补一句缓存语义 |
| `docs/13-决策记录.md` | 新增 §8.39 |

## Rulings I made

**1. 能力探查的失败态加 60 秒 TTL，而不是「失败就不缓存」。** 完全不缓存的话，上游持续不可用时
每一次重发都要先烧两次探查请求（各自最长 120 秒超时），用户重发三次就是三次白等。60 秒是
「抖动能自愈」与「别拿探查去打已经不稳的上游」之间的折中：同一分钟内的连续重发吃缓存，
隔一分钟以上的重发重新探。探查**成功**的结果照旧不过期，[docs/13 §8.13](../../13-决策记录.md)
那条「改完 `config.yaml` 要重启」的遗留结论不变。

**2. 重试放在 LLM 客户端里，不放轮次层。** 备选是在 `_complete()` 里重试，那样能力探查、标题生成、
正常轮次三处各要写一遍；而且只有客户端知道「这一次尝试有没有吐过 chunk」——重放的边界正好在那里。

**3. 读超时只多给一次机会（`TIMEOUT_MAX_ATTEMPTS = 2`）。** 每次读超时都要烧满
`timeout_seconds`（默认 120 秒），按 3 次尝试算就是六分钟的等待，用户分不清「还在跑」和「已经死了」。
连接层失败、5xx、429 都是一两秒内就有结论的失败，仍按 3 次尝试。

**4. 次数、退避与 TTL 都做成模块常量，不进 `SETTING_KEYS`。** 没有运维需要按部署调这三个数字的
证据，而每一个新键都要同时落到 `settings.py` 的 Pydantic 模型、`config.template.yaml`、设置页与
docs/05；等真有需求再加，比现在先加一个没人调的旋钮便宜。

**5. 失败说明落成助手消息（`finish_reason="turn_error"`），不新开表、不写系统消息。** 备选是维持
「普通回合出错不写成消息」的现状（前端注释里明确写过这是现状），但那个现状正是用户看到
「agent 不理我」的原因；自动诊断链路早就有 `_save_incident_failure()` 这个先例
（`finish_reason="incident_error"`），普通链路的 `turn_error` 只是与它对口径。

**6. 只在「用户消息确实是最后一条」时补说明。** 备选是无条件补。落库之前失败的错误
（`LLM_NOT_CONFIGURED`、会话不存在、会话忙）本来就没有半截对话，补一条反而让人以为消息发出去了。

**7. 不重放已经吐出去的内容。** 中途断流时宁可失败也不重试：重放会让用户看到重复文字，
而这一轮的部分内容已经落库（中断收尾那条路径依赖它）。

## 验证

- 后端：`.venv/bin/python -m pytest -o addopts= -q` → **2134 passed, 3 failed, 4 skipped**，退出码 1。
  3 条失败全是真资源用例（见「未完成 / 遗留」第 4 条），改动前后一致。
  计数自洽：归档基线 2123 passed + 4 skipped，加本次新增 14 条、减 3 条因资源包变化而失败的真资源用例 = 2134。
- 生成物：`scripts/dump_openapi.py --check` → `OpenAPI snapshot is current: web/openapi.json`；
  `scripts/generate_api_guide.py --check` → `API guide generated sections are current`，两者退出码 0
  （本次不动接口、错误码与工具清单）。
- 前端：`pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` 全部退出码 0，
  vitest **60 文件 / 611 项**全过，build 末行 `[sw_harness] SERVICE WORKER OK`。
- 文案审计：`scripts/copy_inventory.py --check` 因新增正文报计数上升（`docs正文/bold` 1776 → 1867、
  `docs正文/absolutes` 144 → 157 等），按 M24 的惯例 `--write-baseline` 重设基线（42 项）后
  `--check` 通过（「无回潮，0 项较基线下降」）。
- 行为侧只做了单元与用例级验证：没有对真实 DeepSeek 端点跑重试（只在 `httpx.MockTransport` 上验证
  重试与退避策略），也没有把运行中的服务切到本分支（用户的服务仍在跑旧代码，需要重启才会生效）。

## 未完成 / 遗留

**1. 能力探查本身仍可能烧满 120 秒 × 2 次。** 工具探针与视觉探针各用默认超时，上游挂死时用户要等
四分钟才拿到错误。给探查单独设更短的超时是下一个候选改法，本次没做（它会改变
`capabilities/check` 的既有行为，值得单独一轮）。

**2. 重试只覆盖「还没有吐出任何 chunk」的失败。** 中途断流不重放是有意取舍，见 Ruling 7。

**3. 2026-10-09 那两轮的确切上游错误无法回溯。** 当时没有任何日志，本次补的日志只能保证下一次
查得到；不能声称已经定位到那一次的具体成因（能排除的是额度、配置、上下文长度与会话锁）。

**4. 3 个真资源用例在本次改动前后都失败，与本次无关。** `tests/services/test_activity_index.py`
两条、`tests/agent/test_activity_tools.py` 一条：资源包 2026-10-09 更新后当期活动是「昨日海」，
名字里没有期数，`infer_activity` 按 [docs/13 §8.38](../../13-决策记录.md) 的既定边界给出
`confidence="none"`。已用 `git stash` 在同一检出上复跑确认：改动前后失败的是同样 3 条。
