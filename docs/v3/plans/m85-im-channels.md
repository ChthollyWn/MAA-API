# M85 IM 通道 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 四个 IM 平台（Telegram/飞书/钉钉/QQ）接入总控：绑定码、`im.message` 进同一个总控会话、发件箱（去重/退避/死信/重启续传）、破坏类确认不能在 IM 批准、密钥加密存储；设置页「IM 通道」+ outbox 列表端点 + 部署段落。

**Architecture:** G1 三表/枚举/迁移/发件箱 worker（T1）→ G2 适配器协议 + Fake + 四平台适配器 + extras（T3）→ G3 设置密钥加密 + ChannelService 绑定/入站 + `im.message` 事件（T2 前半）→ G4 六端点 + 出站 enqueue（汇报/确认/简报/告警）+ 确认入站分支（T2 后半 + T4）→ G5 前端/总控提示词/部署段落/冒烟段落（T5）→ G6（我：文档、验收、合并 tag）。

**Spec:** [m85-im-channels](../specs/m85-im-channels.md)（320 行）

## 开工基线（2026-11-05）

- 分支 `im-channels`，基点 `dev` = `0200ae4`（M84 合并）；tag `v3-m84`；迁移 head `0037_spend_ledger`，下一号 `0038`。
- **Q6/Q7 已裁定且与 spec 默认一致**：Q6「只做四个平台」、Q7「维持 v2 通知不变（选项 A）」→ 三处 v2 出口（`ConfirmationService._push_confirmation`、养成计划完成、作业授权终态）**不改**；发件箱只承接 IM 回复、汇报、确认、简报、Web Push（M85 新增部分）。
- §0 逐项核对：`_push_confirmation` 在 `agent/confirmation.py:1000`；`copilot_research_finished` 在 `services/copilot/notify.py`；`sections.ts` 结构已知（需加 `channels` 分区 + 分组映射「IM 通道」）；Fernet 路径为 `maa_api/agent/secrets.py` 的 `encrypt_api_key/decrypt_api_key`，由 `SettingService` 对 `llm.api_key` 特判；16 号部署文档存在。
- 全量基线：3 条真资源失败（expected）。
- **接缝裁决（记 13 §8.163–8.172）**：
  1. **路由落点**：`api/agent/channels.py` 不存在此目录，实际落 `maa_api/api/routers/channels.py`（仓库存量做法，同 M81–M84）。
  2. **设置密钥加密**：`SettingService` 目前只对 `llm.api_key` 加密/迁移；实际把四个平台 secret 一起纳入同一个密钥集合（`_SECRET_SETTING_KEYS`），refresh 时解密与一次性迁移、update 时加密，缺密钥报 `LLM_ENCRYPTION_KEY_INVALID`；接口回显仍由 `sensitive=True` 统一打 `***`。
  3. **`im.message` 事件**：`EventSource.IM_*` 与 thread 前缀已存在，`KNOWN_KINDS` 无该 kind → 新增 1 种（15→16），不新增内置规则；入站默认进总控（`kind=user`），回复某工单消息时进该工单并给总控摘要（按发件箱回执 `ref_ticket_id` 找）；payload 取 08 §2.2（`binding_id`/`message_id`/`text`/`reply_to` + `platform`/`external_chat_id`）。
  3b. **入站回调不新增 HTTP 端点**：spec §4.3 只列 04 的 6 行，且 §4.5 的「回调」可用各平台长连接实现——Telegram `getUpdates` 轮询（httpx）、飞书 `lark-oapi` WebSocket 长连接、钉钉 Stream（WebSocket）、QQ `qq-botpy` WebSocket 网关；签名/解密校验作为适配器内函数实现并被测试覆盖（飞书 EncryptKey/VerificationToken、QQ Ed25519 签名等），不新增对外路由。这样避免范围扩张；若未来平台要求 HTTP webhook，另立里程碑。
  3c. **前端入口**：spec §4.6「设置页新分组、不在指挥台另做入口」为准，11 §路由表的 `/more/channels` 页在 G6 改文档（settings 分组 `/more/settings/channels`），记 13。
  3d. **Web Push 从 M85 起走发件箱**：按 08 §1.5，把 M82 工单推送、M83 critical 告警推送、M84 简报推送的「实际发送」改为写 `outbox`（channel=`webpush`，发送仍由 `WebPushService` 完成）；Q7=A 的三处 v2 出口（`_push_confirmation`/养成计划完成/作业授权终态）不动。
  3e. **09 §5 的部分规则不在本里程碑**：`ask_user` 的 IM 作答、risk=none 拒绝、审批令牌/时钟偏差属 M85 之外的既有设计细则，spec §3 未列；本里程碑只实现消耗类与原子授权的精确短语批准 + 破坏类拒绝，其余记 13 遗留。
  4. **发件箱**：`OutboxWorker` 每 2 秒取 pending/failed、最多 20 条；成功 `sent`、`OutboxRetry` 退避 `min(3600, 2**attempts)`、8 次 `dead`、`OutboxFatal` 直接 `dead`；启动把 `sending` 改回 `pending`；`dedupe_key` UNIQUE 冲突即返回旧行（不重复入队）。
  5. **入站去重**：`im.message` 的 `dedupe_key=f"im:{platform}:{message_id}"`；重复消息不写第二次收件箱。
  6. **绑定码**：8 位大写字母数字去 `0O1I`，10 分钟有效、用过即废；HTTP `BIND_CODE_INVALID` 用于绑定码接口/入站回复；入站无效只回「绑定码无效或已过期」，不写收件箱。
  7. **确认**：IM 文本必须精确「同意 <id 前 8 位>」/「拒绝 <id 前 8 位>」；破坏类且 `allow_destructive_approval=false` → `CHANNEL_APPROVAL_NOT_ALLOWED`，不调批准；v2 三处推送不动（Q7=A）。
  8. **平台 SDK**：三个 extra `im-feishu`/`im-dingtalk`/`im-qq`（16 号文档现有单个 `im` 预留名在 G5 改为三个）、可选延迟导入；锁定当天 PyPI 版本：`lark-oapi==1.7.5`（[PyPI](https://pypi.org/project/lark-oapi/)、[文档](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk)）、`dingtalk-stream==0.24.3`（[PyPI](https://pypi.org/project/dingtalk-stream/)、[Stream 文档](https://opensource.dingtalk.com/developerpedia/docs/learn/stream/overview)）、`qq-botpy==1.2.1`（[PyPI](https://pypi.org/project/qq-botpy/)、[QQ 机器人文档](https://bot.q.qq.com/wiki/develop/api/)）；Telegram 用已有 `httpx`。未装 extra 的适配器 `start` 记「未安装」不启动、不抛；签名/发送 URL 常量集中写在文件顶部并注文档链接与查阅日期；测试用 `httpx.MockTransport`/假传输、不访问外网。
  9. **计数**：错误码 164/163→**169/168**（5 个 `CHANNEL_*`/`BIND_CODE_INVALID`）；端点 181→**187**（agent 78→84，只加 04 的六行、无回调端点）；业务表 53→**56**；配置键 +16；事件 kind 15→16；工具/规则/定义数不变；总控 `version` 6→7；迁移 head **0038**。
  9b. **加密推广**：按 09 §8.2 把 `SettingService` 的 `llm.api_key` 特判推广为按 `_FIELDS` 的 `sensitive` 元数据统一加密/解密/明文迁移（`secrets.py` 加语义别名，保留 `encrypt_api_key` 兼容）；四个通道 secret 自动纳入。
  9c. **既有 bug 不动**：`NotifyService` 没有 `notify()`，养成计划完成通知实际从未发出（被 `except` 吞掉）；Q7=A 下「保持原样」，只在 13 §8 记录，不在本里程碑修复。
  10. **前端分组**：`sections.ts` 新 id `channels`、标题「IM 通道」、`GROUP_SECTIONS['IM 通道']='channels'`；`ChannelSection` 在设置页分组渲染（非密钥字段 + 密钥 `***` 回显 + 生成绑定码 + 绑定列表/解除）。

## Global Constraints

- 只做四个平台；不加第五个平台、不新增工具/代理定义；不改 `analyst.md`；不改 v2 三处通知出口（Q7=A）。
- 密钥只加密存放（与 `llm.api_key` 同一条 Fernet），接口/日志/文档不留明文；`send` 失败分类：网络/429/5xx 可重试，其余 4xx 致命。
- 发件箱 enqueue 与业务状态同一事务；真正 HTTP 发送在提交后；重启从待发与发送中继续，已发送不重发。
- 绑定只认 `(platform, external_user_id, external_chat_id)` 的 active 行；新绑定把旧行 `revoked`；群聊其他成员的消息丢弃。
- 输出截断按 `channels.max_reply_chars`（默认 300），超长附 Web 链接。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m85/`。
- 不 push；提交 `<类型>(m85): <中文摘要>`；最后合并回 `dev` 打 `v3-m85`。

## Review Focus

1. **表/迁移**：三表列/索引/唯一约束/自增；`outbox.dedupe_key` UNIQUE；`ref_*` SET NULL；迁移可逆。
2. **OutboxWorker**：状态机/退避公式/8 次 dead/fatal/启动重置/dedupe 冲突/2 秒批 20 条/并发只取一次。
3. **适配器**：协议三方法；四平台 URL/签名/消息字段常量集中；缺 extra 不崩；`send` 回执；Inbound 形状；Fake 记录。
4. **ChannelService**：绑定码生成/过期/已用/平台不符/重复绑定 revoke；入站去重与收件箱（总控/工单+摘要）；`/` 非 bind 拒绝；出站 reply/report/confirmation/brief/alert 的 dedupe 键与 target；无绑定时不写。
5. **确认**：精确短语、破坏类默认拒绝 `CHANNEL_APPROVAL_NOT_ALLOWED`、非破坏类批准；v2 三处测试保持原断言。
6. **密钥/设置**：16 个键默认/范围/分组/掩码/加密迁移；缺密钥错误。
7. **端点/前端**：六端点形状/错误码/分页；设置页分组、绑定码按钮、绑定列表；总控提示词版本；部署段落。

---

### Task 0: 分支、§0、Q6/Q7、计划入库（已完成）

- [x] 建 `im-channels`；§0 1–8；Q6/Q7 为 spec 默认；10 项接缝裁决；提交计划。

### Task 1（G1）: 三表、枚举、迁移、发件箱（spec T1）

**Files:** `domain/enums.py`、`tests/domain/test_enums.py`、`db/models.py`、新建 `migrations/versions/0038_channels.py`、`tests/db/test_models.py`、新建 `tests/db/test_channels_migration.py`、新建 `agent/channels/{__init__,outbox}.py`、新建 `tests/agent/test_outbox.py`

- [ ] 四个枚举（`ChannelPlatform`/`OutboxChannel`/`OutboxKind`/`OutboxStatus`）；`channel_binding`/`channel_bind_code`/`outbox` 三表 + 索引/唯一；迁移 0038 可逆；`OutboxWorker`（状态机/退避/dead/启动重置/dedupe）；`FakeAdapter` 协议占位（与 G2 对齐）。
- [ ] 验证：`pytest tests/agent/test_outbox.py tests/db tests/domain/test_enums.py` → 0；`alembic upgrade head --sql | grep -c outbox` ≥1。
- [ ] 提交：`feat(m85): 通道三表、迁移与发件箱`

### Task 2（G2）: 适配器协议、Fake 与四平台（spec T3）

**Files:** 新建 `agent/channels/{adapter,fake,telegram,feishu,dingtalk,qq}.py`、`pyproject.toml`（extras）、新建 `tests/agent/test_channel_adapters.py`

- [ ] `ChannelAdapter` 协议 + `Inbound`/`OutboxRetry`/`OutboxFatal`；FakeAdapter；四平台 `start/stop/send` 与签名校验/回调解析（常量集中 + 文档链接/日期）；extras 可选延迟导入；缺 extra `start` 只记「未安装」。
- [ ] 验证：`pytest tests/agent/test_channel_adapters.py` → 0；四平台缺 extra 时不抛。
- [ ] 提交：`feat(m85): 四平台通道适配器`

### Task 3（G3）: 设置密钥 + 绑定与入站（spec T2 前半）

**Files:** `services/setting_service.py`（secret 集合加密/迁移）、`settings.py`（`ChannelSettings` 16 键）、`services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、新建 `agent/channels/service.py`、`agent/events/kinds.py`（`im.message`）、`db/repositories/`（通道仓储）、新建 `tests/agent/test_channel_bind.py`

- [ ] 16 配置键 + `_FIELDS`（分组「IM 通道」/sensitive）+ 模板；SettingService 把四个 secret 纳入加密/迁移；`ChannelSettings`。
- [ ] 仓储 + `ChannelService`：绑定码生成（8 位/10 分钟）、`/bind` 处理（过期/已用/平台不符/重复 revoke）、入站去重/收件箱（总控/工单+摘要）、`/` 非 bind 回复、`send` 封装。
- [ ] `KNOWN_KINDS` 加 `im.message`（16）；同步 kinds 测试与数量。
- [ ] 验证：`pytest tests/agent/test_channel_bind.py tests/test_settings.py tests/agent/events` → 0。
- [ ] 提交：`feat(m85): 通道绑定、入站与密钥加密`

### Task 4（G4）: 六端点、出站 enqueue 与确认（spec T2 后半 + T4）

**Files:** 新建 `api/routers/channels.py`、`main.py`、新建 `tests/api/test_channels.py`、`tests/agent/test_channel_confirm.py`、`agent/confirmation.py`（入站答复分支）、`agent/channels/service.py`（出站 enqueue 各来源）、`agent/tickets/service.py`（汇报监听接入 outbox？按 spec §4.5 出站）、`agent/brief/service.py`（简报推送改写 outbox webpush）、`agent/watchdog/service.py`（critical 告警）、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/v3/14`

- [ ] 六端点（列表/绑定码/绑定列表/解除/测试/outbox 分页）；错误码 5 个（169/168）；14 号文档 187/agent 84；OpenAPI/类型。
- [ ] 出站：总控 IM 来源回复、汇报、确认、简报、告警在已绑定时各写一条 outbox（dedupe 键照 spec）；Web Push 从 M85 起走发件箱（`webpush` channel，按 08 §1.5）；无绑定不写。
- [ ] 确认入站：精确短语批准/拒绝；破坏类默认 `CHANNEL_APPROVAL_NOT_ALLOWED`；v2 三处出口保持 A。
- [ ] 验证：`pytest tests/api/test_channels.py tests/agent/test_channel_confirm.py tests/agent/test_confirmation.py tests/services` → 0；`dump_openapi --check` 0；`gen:api:check` 0。
- [ ] 提交：`feat(m85): 通道端点、出站与确认边界`

### Task 5（G5）: 前端、总控提示词、部署与冒烟段落（spec T5 + §7.1）

**Files:** 新建 `web/src/features/settings/ChannelSection.tsx`、`sections.ts`、`SettingsPage.tsx`、测试；`agent/specs/builtin/orchestrator.md`（version 7）；`docs/v3/16` 部署段落；`scripts/orchestrator_smoke.py` 可选段落

- [ ] 设置页「IM 通道」（四平台开关/字段/密钥 `***`/生成绑定码/绑定列表解除）；`sections.ts` 快照测试；总控提示词段逐字 + version 7；部署段落逐字；冒烟 `[ok] channels`（FakeAdapter 绑定→入站→发件箱 sent/总控有消息）。
- [ ] 验证：`pnpm gen:api:check && pnpm typecheck && pnpm test --run src/features/settings && pnpm build` → 0；`frontend_smoke` OK；`orchestrator_smoke` SMOKE OK（含新段落）。
- [ ] 提交：`feat(m85): 通道设置页、总控提示与部署说明`

### Task 6（G6，我）: 文档、全量验收与合并（spec §9）

**Files:** `docs/v3/{02,03,04,05,09,11,13,14,16,README}`、新建 `docs/v3/ledgers/m85-im-channels.md`

- [ ] 02 服务表 `ChannelService`/`OutboxWorker`；03 §11.7 校验；04 六端点/5 错误码/16 键（预写则校验）；05 总控 tools 不改；09 §8 密钥路径；11 设置分组；13 §8.163–8.172；16 部署段落；README 状态行（v3 路线完成）；台账（四平台文档链接/签名结论/版本）。
- [ ] 跑 §7：全量 pytest 不新增失败、openapi/指南、前端五步、四冒烟、regression/capability、断链、diff；手动项记「未验证」。
- [ ] 提交 `docs(m85): 台账、契约同步与状态行`；`git merge --no-ff im-channels` → tag `v3-m85` → 删分支 → 合并后复验 → 汇报；最后把 v3 路线（M63–M85）完成证据汇总到台账。
