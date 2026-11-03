# M85 IM 通道（规格）

> 阶段：P3　依赖：M84　分支：`im-channels`　迁移：`0038_channels`
> 本文是 M85 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 分支干净 | `git status -sb` | `## im-channels` |
| 2 | M84 已合并 | `git tag -l 'v3-m84'` | `v3-m84` |
| 3 | 下一个迁移号 | `ls maa_api/db/migrations/versions/ \| tail -n 3` | 最大号小于 0038；不是就用下一个空号并记 13 §8 |
| 4 | 确认推送的现有出口 | `rg -n "async def _push_confirmation" maa_api/agent/confirmation.py` | 1 行，约 955 行 |
| 5 | 另两处对外通知 | `rg -n "upgrade_plan_finished\|copilot_research_finished" maa_api/services maa_api/main.py` | 能定位养成计划完成与作业授权终态 |
| 6 | 设置分组常量 | `rg -n "分组\|sections" web/src/features/settings/sections.ts \| head` | 记下文件结构，本里程碑追加「IM 通道」 |
| 7 | 密钥加密入口 | `rg -n "Fernet\|encrypt" maa_api/agent/settings maa_api/services -g '*.py' \| head` | 找到 llm.api_key 的加密函数，通道密钥走同一条 |
| 8 | 部署文档是否已出现 | `test -f docs/v3/16-部署指南.md && echo yes \|\| echo no` | `yes` 则把 §9 的段落写进该文件；`no` 则只把段落留在台账，等 M64 的文档落地后再贴 |

第 8 条两种结果都合法。其余不符合就停下来。

开工前先读 [13](../13-决策记录.md) 的 Q6、Q7。Q6 未裁定时只做 Telegram、飞书、钉钉、QQ 官方机器人，不要加第五个平台。Q7 未裁定时按 08 §9 的选项 A：v2 的 `NotifyService`、`_push_confirmation`、养成计划完成、作业授权终态保持原样，不改写发件箱。发件箱只承接本里程碑新增的 IM 回复、汇报、确认、简报、告警，以及 08 §1.5 里写明从 M85 起走发件箱的 Web Push。13 若已把 Q7 裁定为 C，再按 §4.5 末尾的三处调用点改出口，并在台账引用条目编号。裁定含糊就停下来问。

## 1. 背景与目标

总控目前只在网页里收消息。本里程碑按 [08 §8](../08-主动性与通道.md) 把四个 IM 平台接成总控的另一个入口，并用一张发件箱保证重启后通知仍会发出。

做完后：

1. 用户在网页上生成绑定码，在 IM 里发送 `/bind <code>` 完成绑定。绑定码 10 分钟有效，用过即废。
2. 已绑定会话的文本消息进入总控收件箱，来源标记为对应平台。总控的回复写入发件箱，由平台适配器发送。
3. 发件箱去重、退避、死信，重启后从 `pending` 与 `sending` 继续。
4. 破坏类确认默认只在网页上批准。`channels.allow_destructive_approval=false`。
5. 平台密钥加密存储，与 `llm.api_key` 同一条 Fernet 路径（[09 §8](../09-安全与治理.md)）。

## 2. 非目标

- 不在 IM 里渲染工单页、计划批准卡、产物抽屉。IM 只收发明文与一行深链。
- 不实现第五个平台，即使 Q6 的备注里出现了名字。等 13 有裁定再开里程碑。
- 不改变 v2 通知的触发条件。改的是出口：从直接推送改为写发件箱。
- 不把 IM 消息当成已确认。确认仍走确认卡。
- 不开放群聊里的任意成员。只有持有绑定码的那个外部用户 id 与 chat id 对可以发消息。

## 3. 相关文档

- 表：[03 §11.7](../03-数据模型.md)。
- 端点与错误码：[04 §2.4](../04-接口与事件协议.md) 的 M85 行；`CHANNEL_*`、`BIND_CODE_INVALID`。
- 协议与绑定流程：[08 §8](../08-主动性与通道.md)。
- 配置分组：[04 §6](../04-接口与事件协议.md)。前端分组列表：`web/src/features/settings/sections.ts`。
- 密钥：[09 §8](../09-安全与治理.md)。
- 三处现有推送：`ConfirmationService._push_confirmation`；养成计划完成通知（`maa_api/services/account/plans.py` 约 606 行）；作业授权终态（`maa_api/services/copilot/grant.py` 约 551 行，构造在 `maa_api/services/copilot/notify.py`）。

## 4. 设计

### 4.1 数据模型

三张表与 03 §11.7 逐列一致。枚举：

```python
class ChannelPlatform(StrEnum):
    TELEGRAM = "telegram"
    FEISHU = "feishu"
    DINGTALK = "dingtalk"
    QQ = "qq"

class OutboxChannel(StrEnum):
    IM_TELEGRAM = "im_telegram"
    IM_FEISHU = "im_feishu"
    IM_DINGTALK = "im_dingtalk"
    IM_QQ = "im_qq"
    WEBPUSH = "webpush"

class OutboxKind(StrEnum):
    REPLY = "reply"
    REPORT = "report"
    CONFIRMATION = "confirmation"
    BRIEF = "brief"
    ALERT = "alert"

class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    DEAD = "dead"
```

`channel_binding.status` 用 `active`、`revoked` 两个字符串，不必再造枚举，除非 03 已有。

迁移 `0038_channels`。`outbox.dedupe_key` 唯一。`downgrade` 反序删表。

### 4.2 模块与文件

新建：

- `maa_api/agent/channels/adapter.py`：`ChannelAdapter` 协议，三个方法 `start`、`stop`、`send`。
- `maa_api/agent/channels/telegram.py`、`feishu.py`、`dingtalk.py`、`qq.py`。
- `maa_api/agent/channels/fake.py`：`FakeAdapter`，测试与冒烟用。
- `maa_api/agent/channels/service.py`：`ChannelService`，负责绑定、入站、出站。
- `maa_api/agent/channels/outbox.py`：`OutboxWorker`。
- `maa_api/api/agent/channels.py`。
- `web/src/features/settings/ChannelSection.tsx`。

依赖放可选 extra，不放进默认安装集：

- `im-feishu`、`im-dingtalk`、`im-qq`：各平台官方 Python SDK，版本在实施当天按上游当前次版本锁定，并在台账写明版本号与文档链接。
- Telegram 用已经在依赖里的 `httpx`，不新增 extra。

某个 extra 没安装时，该平台适配器的 `start` 记录「未安装」并不启动，其他平台不受影响。不要在导入期因缺少 extra 而让整个应用起不来。

### 4.3 接口

只实现 04 §2.4 的 M85 行。函数名即该表第三列。

| 方法与路径 | 函数名 | 行为 |
|---|---|---|
| GET `/api/agent/channels` | `list_channels` | 四个平台的启用、连接状态、最近错误。不含密钥，不含绑定列表 |
| POST `/api/agent/channels/{platform}/bind-codes` | `create_bind_code` | 201 `{code, expires_at}`。`platform` 取 `telegram`、`feishu`、`dingtalk`、`qq` |
| GET `/api/agent/channels/bindings` | `list_channel_bindings` | 绑定列表，不含密钥 |
| DELETE `/api/agent/channels/bindings/{binding_id}` | `revoke_channel_binding` | 204。不存在则 404 `CHANNEL_BINDING_NOT_FOUND` |
| POST `/api/agent/channels/{platform}/test` | `test_channel` | 给该平台全部已绑定身份发测试消息。未配置 503 `CHANNEL_NOT_CONFIGURED`；发送失败 502 `CHANNEL_SEND_FAILED` |
| GET `/api/agent/outbox` | `list_outbox` | query：`status`、`channel`、`page`、`size` |

没有重试端点。`dead` 行留在列表里，不提供 HTTP 重入。绑定码 8 位，大写字母与数字，去掉 `0O1I`，10 分钟有效。

错误码只用 04 §5 的 M85 五行：`CHANNEL_NOT_CONFIGURED`、`CHANNEL_SEND_FAILED`、`CHANNEL_BINDING_NOT_FOUND`、`BIND_CODE_INVALID`、`CHANNEL_APPROVAL_NOT_ALLOWED`。入站绑定码无效时向该 IM 回「绑定码无效或已过期」，不写收件箱，也不为这一句新造错误码。

### 4.4 配置项

分组「IM 通道」，同步 `web/src/features/settings/sections.ts`。密钥走与 `llm.api_key` 相同的 Fernet 路径，接口回显 `***`（04 §1 对模型档案的掩码；不要用别的点号样式）。键名与默认值以 04 §6 为准：

| 键 | 默认 |
|---|---|
| `channels.max_reply_chars` | 300，范围 80–4000 |
| `channels.allow_destructive_approval` | false |
| `channels.telegram_enabled`、`channels.feishu_enabled`、`channels.dingtalk_enabled`、`channels.qq_enabled` | false |
| `channels.telegram_bot_token`、`channels.feishu_app_secret`、`channels.dingtalk_client_secret`、`channels.qq_app_secret` | 空，敏感 |
| `channels.feishu_app_id`、`channels.dingtalk_client_id`、`channels.qq_app_id` | 空 |
| `channels.feishu_domain` | `https://open.feishu.cn` |
| `channels.telegram_proxy` | 空，表示用 `app.proxy` |
| `channels.qq_sandbox` | false |

发件箱轮询间隔与最大尝试次数写死在 `OutboxWorker`：每 2 秒一批，最多 20 条；`attempts` 达到 8 次标 `dead`。这两项不进设置。

键名用下划线，例如 `channels.telegram_enabled`、`channels.telegram_bot_token`。

### 4.5 行为与算法

**适配器**

```python
class ChannelAdapter(Protocol):
    platform: ChannelPlatform
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send(self, chat_id: str, text: str) -> dict: ...
```

`send` 成功返回平台回执，存进 `outbox.receipt`。失败抛 `OutboxRetry` 或 `OutboxFatal`。网络错误、429、5xx 为可重试；4xx（除 429）为致命，直接 `dead`。

入站统一成：

```python
@dataclass
class Inbound:
    platform: ChannelPlatform
    external_user_id: str
    external_chat_id: str
    text: str
    message_id: str
```

适配器把平台回调转成 `Inbound`，调用 `ChannelService.handle_inbound`。签名校验失败的回调直接 401，不进入服务。各平台签名算法以实施当天的官方文档为准，台账记录文档版本与校验通过的一条样例（密钥打码）。

**绑定**

1. 网页 `POST /api/agent/channels/{platform}/bind-codes` 写入 `channel_bind_code`，`platform` 记在行上。
2. 入站文本去掉首尾空白后匹配 `^/bind\s+([A-Z0-9]{8})$`。
3. 码存在、未使用、未过期：写 `channel_binding`，`used_at=now`，向该 chat 回复「已绑定」。平台与码上的 `platform` 都非空且不一致时拒绝。
4. 同一 `(platform, external_user_id, external_chat_id)` 已有 `active` 绑定时，新的绑定先把旧行 `revoked`。

**入站消息**

已绑定且 `active`：发布为 `im.message` 事件（08 §2），默认写入总控收件箱，`kind=user`，`payload` 含 `platform`、`external_chat_id`、`message_id`。用户回复的是一条属于工单的机器人消息时，按发件箱回执找到工单，写入该工单收件箱，并给总控一条摘要。`message_id` 做去重键。然后唤醒对应会话。

文本以 `/` 开头但不是 `/bind` 的，回复「这个通道只接收普通文本」，不写入收件箱。

**出站**

总控运行产生给用户的正文，且本轮收件箱来源是 IM：写一条 `outbox`，`kind=reply`，`channel` 对应该平台，`target` 为 `external_chat_id`，`dedupe_key=f"reply:{run_id}"`。

汇报、确认、简报、值守 critical 在用户已有 `active` 绑定时各写一条，`dedupe_key` 为 `f"{kind}:{source_id}:{binding_id}"`。没有绑定时不写。

**发件箱**

`OutboxWorker` 每 2 秒取 `status in (pending, failed)` 且 `next_attempt_at <= now` 的行，最多 20 条。领取时把状态改为 `sending`。

| 结果 | 写入 |
|---|---|
| 成功 | `sent`，`sent_at`，`receipt` |
| `OutboxRetry` | `attempts+1`，`failed`，`next_attempt_at = now + min(3600, 2**attempts)` 秒 |
| `attempts` 达到 8 | `dead` |
| `OutboxFatal` | `dead`，`last_error` |

进程启动时把遗留的 `sending` 改回 `pending`。这样崩溃后续上。

**确认**

IM 里可以批准消耗类确认与原子操作授权（08 §8）。破坏类默认只回网页链接；`channels.allow_destructive_approval=false` 时返回 `CHANNEL_APPROVAL_NOT_ALLOWED`，不调用批准。文本必须精确等于「同意 <confirmation_id 前 8 位>」或「拒绝 <前 8 位>」。不要把「可以」「同意」单独当成批准。

**v2 三处出口**

Q7 仍是选项 A 时，这三处不改：`ConfirmationService._push_confirmation`、养成计划完成通知、`main.py` 的 `_copilot_notify`（作业授权终态的构造在 `maa_api/services/copilot/notify.py`）。

Q7 裁定为 C 时才改：保持原来的触发条件，把 Web Push 与 IM 的实际发送改成 `OutboxService.enqueue`。`NotifyService` 的邮件、Webhook、Bark 仍直接发送。去重键与现有推送 tag 对齐，同一事件不发两次。测试对比改动前后的触发次数。

### 4.6 前端

设置页新分组「IM 通道」渲染 `ChannelSection`：

- 四个平台各一块：启用开关、非密钥字段、密钥字段（回显点，留空表示不修改）。
- 「生成绑定码」按钮。成功后显示码与过期时间，按钮在请求中禁用。
- 已绑定列表：平台、显示名、绑定时间、解除按钮。

不在指挥台另做入口。

### 4.7 提示词与代理定义

总控 `orchestrator.md` 增加一段，`version` 加 1：

```
消息来源可能是网页，也可能是已绑定的 IM。两种来源的话都是用户说的。
IM 上看不到计划卡和确认卡。需要用户批准破坏类操作时，告诉他去网页处理，不要在 IM 里收集「可以」「同意」。
回复保持短。单条不超过 `channels.max_reply_chars`（默认 300 字），超出就先给结论，并写上网页路径。
```

不新增代理定义。

## 5. 实施任务

### T1 迁移与发件箱

- 写范围：三张表、枚举、`outbox.py`、`tests/agent/test_outbox.py`、`tests/db/test_models.py`。
- 步骤：按 §4.5 的状态表。用 `FakeAdapter`，先失败两次再成功，断言 `attempts` 与最终 `sent`。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_outbox.py tests/db/test_models.py`，退出码 0。

### T2 绑定与入站

- 写范围：`service.py`、`fake.py`、`api/agent/channels.py`、`tests/agent/test_channel_bind.py`。
- 步骤：绑定码过期、用过、平台不符、重复消息四条路径。入站写入总控收件箱用现有 `InboxService`。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_channel_bind.py tests/api/test_channels.py`，退出码 0。

### T3 四个适配器

- 写范围：四个平台文件、`pyproject.toml` 的 extras、`tests/agent/test_channel_adapters.py`。
- 步骤：每个平台用记录下来的官方文档做签名校验与发送 URL。测试不访问外网：用 `httpx.MockTransport` 或 SDK 的假传输。断言未安装 extra 时 `start` 不抛。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_channel_adapters.py`，退出码 0。

### T4 确认与 v2 出口

- 写范围：`confirmation.py` 的入站答复分支。Q7 仍是 A 时不要改三处 v2 推送。Q7 已裁定为 C 时才改那三处的发送出口。
- 步骤：破坏类在默认配置下不因 IM 文本而通过，错误码 `CHANNEL_APPROVAL_NOT_ALLOWED`。选项 A 下，三处旧推送测试保持原断言，发件箱不新增它们的行。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_confirmation.py tests/agent/test_channel_confirm.py`，退出码 0。Q7 为 C 时再跑 `rg -n "upgrade_plan_finished|copilot_research_finished|_push_confirmation" tests` 列出的推送测试。

### T5 前端、提示词、部署段落

- 写范围：`ChannelSection.tsx`、`sections.ts`、`orchestrator.md`、部署段落。
- 步骤：分组可见；密钥回显为 `***`；生成绑定码的按钮会禁用。部署段落按 §9。
- 验证：`cd web && pnpm typecheck && pnpm test --run src/features/settings`。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/test_outbox.py` | `test_retry_then_sent` | 退避后成功 |
| 同上 | `test_fatal_is_dead` | 4xx 不重试 |
| 同上 | `test_dedupe_key_unique` | 第二次 enqueue 不插行 |
| 同上 | `test_sending_reset_on_start` | 遗留 sending 回到 pending |
| `tests/agent/test_channel_bind.py` | `test_bind_code_expires` | `BIND_CODE_INVALID`，无收件箱 |
| 同上 | `test_bind_then_message_reaches_orchestrator` | 收件箱含 platform |
| 同上 | `test_duplicate_platform_message_ignored` | 只一条 |
| `tests/agent/test_channel_confirm.py` | `test_destructive_not_approved_from_im` | 确认仍 pending |
| 同上 | `test_exact_phrase_approves_non_destructive` | 仅精确格式 |
| `tests/agent/test_channel_adapters.py` | `test_missing_extra_does_not_break_start` | 其他平台仍 start |
| `tests/api/test_channels.py` | `test_secret_roundtrip_masked` | GET 设置回显 `***`，不见明文 |

## 7. 验收

照 12 §4。另：

1. `FakeAdapter` 冒烟：生成绑定码、入站 `/bind`、入站一句用户话、发件箱变为 `sent`、总控会话有这条用户消息。可以加进 `scripts/orchestrator_smoke.py` 的可选段落，失败时返回 1。
2. 四个平台的官方文档链接与签名结论写在台账。未核对的平台保持对应的 `channels.*_enabled=false`，不要在文档里写成已联通。
3. `rg -n "bot_token|app_secret|client_secret" docs` 无真实密钥。

## 8. 风险与回滚

- 平台协议会变。适配器把 URL、签名头、消息字段集中在文件顶部的常量里，并在常量旁注释文档链接与查阅日期。
- 发件箱若在业务提交之前写入，业务回滚会留下孤儿通知。enqueue 与业务状态放在同一个数据库事务里；真正的 HTTP 发送在事务提交之后。
- 回滚：revert，`alembic downgrade -1`。解除各平台后台的回调 URL，避免旧地址继续打到已回滚的服务。

## 9. 文档同步

部署段落（`docs/v3/16-部署指南.md` 已存在时写入「IM 通道」一节，不存在时整段抄进台账）：

```
IM 通道默认关闭。启用某个平台前安装对应 extra，在设置页填写该平台的应用凭证，
在平台后台把事件回调指到本服务的 HTTPS 地址。绑定码只在网页上生成。
破坏类确认默认不能在 IM 里批准。凭证加密后存放，日志与文档里不要留下明文。
```

同步计数：表加 3；路由按 04 的 M85 行；错误码只使用 04 已列的码，总数若 04 已含则不加。`sections.ts` 的分组若有快照测试，把「IM 通道」加进去。

05 的总控工具名单不改。

## 10. 停下来问

- 13 的 Q6 或 Q7 在开工时已有与本 spec 默认不同的裁定，而裁定含糊到无法直接改出口。
- 某个平台的现行文档要求的签名或回调路径与 08 §8 冲突。
- Fernet 路径找不到，或 `llm.api_key` 并没有加密。
- 想把「可以」「同意」当作确认。
- 想新增 04 没有的错误码或第五个平台。
