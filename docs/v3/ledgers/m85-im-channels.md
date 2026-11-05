# M85 IM 通道（台账）

- 分支：`im-channels`；基点：`dev` = `0200ae4`；合并：`dev` 上的 `merge: IM 通道（M85，见 docs/v3/13 §8.163–8.172）`；tag：`v3-m85`
- spec：[m85-im-channels](../specs/m85-im-channels.md)（320 行）
- 计划：[m85-im-channels](../plans/m85-im-channels.md)
- 决策记录：[docs/v3/13 §8.163–8.172](../13-决策记录.md)
- 迁移：`0038_channels`（可逆；反序删三表）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `74ba27b` | 计划入库；§0 1–8；Q6「只做四平台」/Q7「v2 通知不变」；12 项接缝裁决；基线 3 条真资源失败 |
| G1（T1） | `f16a18e` | 四枚举；`channel_binding`/`channel_bind_code`/`outbox` 三表 + 迁移 0038；`OutboxWorker`（去重/2 秒 20 条/退避/8 次 dead/fatal/启动重置） |
| G2（T3） | `0a7d753` | `ChannelAdapter` 协议 + `Inbound`/`FakeAdapter`；Telegram/飞书/钉钉/QQ 适配器（长连接/轮询接收、发送、签名校验、常量含文档链接与查阅日期）；extras 尝试后按离线环境回滚（见 §8.165） |
| G3（T2 前半） | `757b540` | `ChannelsSettings` 16 键 + 分组「IM 通道」；`SettingService` 加密扩到 5 个密钥（含 4 个通道密钥，明文迁移保留）；`KNOWN_KINDS` + `im.message`；三仓储；`ChannelService` 绑定码/`/bind`/入站 publish/回执 outbox/数据方法 |
| G4（T2 后半 + T4） | `6b8e39e` | 六端点（通道/绑定码/绑定/解除/测试/发件箱）；出站 enqueue（reply/report/confirmation/brief/alert）；v3 Web Push 改走 outbox；确认入站精确短语/破坏类拒绝；装配/关停；187 端点 |
| G5（T5） | `50f2b6b` | 设置页「IM 通道」（`ChannelSection`：四平台/绑定码/绑定列表/解除）；总控提示词 version 7；16 号部署节 + 三个 SDK 名；冒烟 `[ok] channels` |
| G5 修复 | `f0e46a0` | IM 入站改走 `OrchestratorService.post_user_message`，同时落 `agent_message(role=user)` 与收件箱；冒烟断言随语义更新 |
| G6 | 本台账所在提交 | 02/08/09/11/13/README 同步；13 §8.163–8.172；本台账；全量验收与合并 |

## 核心实现

- **三表**：`channel_binding`（active/revoked + 唯一索引）、`channel_bind_code`（8 位大写去 `0O1I`、10 分钟、用过即废、原子 CAS）、`outbox`（去重键唯一、状态机、引用运行/工单）。
- **入站**：四平台长连接/轮询 → `Inbound` → `handle_inbound`；`/bind CODE` 绑定（过期/已用/平台不符/重复绑定 revoke）、非 bind 斜杠命令拒绝、普通消息以 `im.message` 事件进总控（`agent_message` + 收件箱，`dedupe_key=im:<platform>:<message_id>` 去重），未绑定丢弃。
- **出站**：总控 IM 回复 `reply:<run_id>`；汇报/确认/简报/critical 告警在 active 绑定时各写一条，无绑定不写。发件箱每 2 秒取 pending/failed（≤20），成功/退避/fatal/8 次 dead；启动把遗留 `sending` 改回 `pending`。
- **确认**：IM 精确「同意/拒绝 <前 8 位>」才批准/拒绝；破坏类默认只给网页链接并保持 pending（`CHANNEL_APPROVAL_NOT_ALLOWED` 语义）。
- **密钥**：`setting_service` 对 `llm.api_key` + 四个通道密钥加密/解密/明文迁移（缺主密钥报 `LLM_ENCRYPTION_KEY_INVALID`）；接口统一 `***`。
- **Web Push**：M82/M83/M84 的 v3 推送改为写 `outbox(channel=webpush)`，由 worker 经 `WebPushService.broadcast` 发送；v2 三处（Q7=A）不动。
- **前端**：设置页新增「IM 通道」分区（`ChannelSection` + `SchemaSection` 16 键，密钥 `***`），不另做指挥台页面。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | outbox/db/枚举 619 passed；迁移可逆/离线；三表计数 56 |
| G2 | 适配器+发件箱 37 passed；四平台文档/版本记录；缺 extra 不抛 |
| G3 | 绑定/事件/设置/错误码 169 passed；16 键默认值；`KNOWN_KINDS=16` |
| G4 | 通道/确认/装配/推送 18+42 passed；openapi/gen 0；smoke OK |
| G5 | gen/typecheck/vitest（112 文件 970 用例）/build 全 0；`[ok] channels`；`SMOKE OK` |
| G5 修复 | 事件/绑定/确认 74 passed；`[ok] channels` 仍通过 |

### 最终验收（G6，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | **3 failed / 5292 passed**；与基线同一组真资源失败，无新增 |
| `scripts/dump_openapi.py --check` | 0（187 operations） |
| `scripts/generate_api_guide.py --check` | 0（新端点后重新生成） |
| 前端五步 | 0；vitest 112 文件 **970** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke`（含 `[ok] channels`） | 均 `SMOKE OK` |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 手动验收（真实四平台收发/推送） | **未验证**（无真实平台凭据与公网回调）；FakeAdapter 冒烟覆盖链路等价逻辑 |

### 计数

- 错误码 164/163→**169/168**；端点 181→**187**（agent 78→84）；业务表 53→**56**；配置键 +16；事件 kind 15→16；总控 `version` 6→7；工具/规则/定义数不变；迁移 head **`0038_channels`**。

## 四平台文档与签名结论（实施当天 2026-11-05 查阅）

| 平台 | SDK/版本 | 接收方式 | 发送 | 签名/校验 |
|---|---|---|---|---|
| Telegram | 无（`httpx`） | `getUpdates` 长轮询 | `sendMessage` | token 在 URL，无独立签名 |
| 飞书 | `lark-oapi` 1.7.5（[PyPI](https://pypi.org/project/lark-oapi/)、[SDK 文档](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk)） | `lark.ws.Client` 长连接 | `im.v1.message.create` | `sha256(timestamp+nonce+encrypt_key+body)` + AES-256-CBC 解密 + VerificationToken 比对 |
| 钉钉 | `dingtalk-stream` 0.24.3（[PyPI](https://pypi.org/project/dingtalk-stream/)、[Stream 文档](https://opensource.dingtalk.com/developerpedia/docs/learn/stream/overview)） | Stream WebSocket | access_token + `oToMessages/batchSend`/`groupMessages/send` | Base64(HMAC-SHA256(timestamp+"\\n"+secret))，握手用 client_id/secret |
| QQ | `qq-botpy` 1.2.1（[PyPI](https://pypi.org/project/qq-botpy/)、[机器人文档](https://bot.q.qq.com/wiki/develop/api/)） | botpy 网关 | `api.sgroup.qq.com`（sandbox 可选） | Ed25519(timestamp+body) |

未核对的平台保持 `channels.*_enabled=false`，文档不写已联通；测试全部用固定假密钥与 `httpx.MockTransport`，无外网访问。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 路由落 `api/routers/channels.py`；适配器按 spec 文件布局 | 仓库布局（13 §8.163） |
| R2 | 入站用长连接/轮询、不新增 HTTP 回调端点 | 避免范围扩张（§8.164） |
| R3 | extras 按离线环境回滚，适配器仍延迟导入 | 无法更新 lock（§8.165） |
| R4 | 加密集合扩到 5 个密钥，access_token/vapid 不纳入 | 兼容既有部署（§8.166） |
| R5 | `im.message` 加 kind；入站改走 `post_user_message` 落 `agent_message` | 总控需看到正文（§8.167） |
| R6 | 发件箱 2 秒/20 条/8 次 dead；v3 Web Push 改走 outbox | spec §4.5 + 08 §1.5（§8.168） |
| R7 | 确认精确短语/破坏类默认拒绝 | spec §4.5（§8.169） |
| R8 | 设置页分组、不做指挥台页面 | spec §4.6（§8.170） |
| R9 | 计数与文档同步 | §8.171 |
| R10 | 09 §5 细则与 NotifyService bug 只记录不实现 | 范围控制（§8.172） |

## 偏离（已记 13 §8.163–8.172）

- §8.163 文件布局；§8.164 长连接入站；§8.165 extras/离线；§8.166 加密范围；§8.167 im.message 与入站修复；§8.168 发件箱/Web Push；§8.169 确认边界；§8.170 前端入口；§8.171 计数文档；§8.172 09 §5 与既有 bug。

## 遗留

- 真实四平台收发与推送未验证（无真实凭据/公网）；适配器只经固定样例与 FakeAdapter 冒烟。
- 09 §5 的 IM 审批令牌、时钟偏差、`risk=none` 拒绝、`ask_user` 作答未实现（08 §8 已注明）。
- `NotifyService.notify()` 不存在导致养成计划完成通知从未发出（Q7=A 保持原样，仅记录）。
- 三个 IM SDK 的 poetry extras 未声明（离线环境无法更新 lock），需要时手动安装；平台协议日后变化需按常量旁的文档链接更新。
- `outbox` 的 30/90 天保留清理未实现（03 §14）。

## v3 路线完成（M63–M85）

- 标签 `v3-m63` … `v3-m85` 全部就位；`dev` 上累计 23 个里程碑的合并提交。
- 每个里程碑均由 spec → plan → 子代理 TDD → 主 agent 全量验收（pytest/OpenAPI/前端五步/四冒烟/评测/断链/diff）→ `merge --no-ff` → tag → 台账 + 13 §8 偏离。
- 最终全量测试：**3 failed / 5292 passed**（仅 3 条真资源数据漂移的既有失败）；前端 **970** 用例；端点 187、错误码 169/168、业务表 56、迁移 head 0038。
