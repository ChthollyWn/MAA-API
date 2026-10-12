# M42：退役自定义练度扫描，改用森空岛（Skland）账号练度

- 里程碑：M42
- 分支：`m42-skland-training`（从 `refactor/v2` 新建；M41 合并打 `v2-m41` 之后）
- 状态：待实现（规格 2026-10-12）
- 上游：M36–M41 练度扫描线（读屏取练度，本里程碑退役）；M27/M33 账号数据线（`OperBox` / `DepotInfo` 回调，保留）

## 1. 来源与目标

用户原话（ref m00077）：

> 可以，我要求去除maaapi项目中的自定义练度扫描任务，全面接入森空岛扫码获取，同时不要外源项目依赖，只能参考其他的项目，先编写docs

同轮的四项裁决（ref m00145，用户逐项选定）：

| 问题 | 选定 |
| --- | --- |
| 删除范围 | **A**：只退役详情练度扫描（M36–M41）；`OperBox` / `DepotInfo` 保留 |
| 二维码 | **A**：仓库内自研零依赖 QR 编码器（后端出 SVG） |
| 绑定方式 | **B**：扫码为主 + 手动 `cred`/`token` 兜底 |
| 同步策略 | **A**：手动同步为主 + 可选定时（默认关，最短 1 小时） |

**目标口径**：干员练度（精英化、等级、潜能、技能等级、专精、模组、信赖）由森空岛官方账号数据提供；M36–M41 那条「内核读屏逐屏识别」的扫描线整体退役，不再有它自己的端点、Agent 工具与前端卡片。仓库数据仍走 `DepotInfo`（森空岛接口没有仓库）。`OperBox` 保留：它是离线兜底，也是本轮 W0 用来交叉验证字段语义的对照源。

## 2. 事实基础（本机实测 + 源码核对）

### 2.1 网络与接口实测（2026-10-12T02:38Z，`curl`，macOS，仓库根）

| 请求 | HTTP | 实测响应（原文，省略号处为截断） |
| --- | --- | --- |
| `POST https://as.hypergryph.com/general/v1/gen_scan/login`，body `{"appCode":"4ca99fa6b56cc2ba"}` | 200（DNS 21 ms，总 125 ms） | `{"data":{"scanId":"e2264fb214643b5df50c9a820c4c1b9f","scanUrl":"hypergryph://scan_login?scanId=e2264fb214643b5df50c9a820c4c1b9f","enableScanAppList":[{"appCode":"4ca99fa6b56cc2ba","name":"森空岛","iconUrl":"https://web.hycdn.cn/…"}]},"msg":"OK","status":0,"type":"A"}` |
| `GET https://as.hypergryph.com/general/v1/scan_status?scanId=<未扫码>` | 200 | `{"msg":"未扫码","status":100,"type":"A"}` |
| `GET …/scan_status?scanId=<已扫码未确认>`（**2026-10-12 11:56:30Z 真机捕获**） | 200 | `{"msg":"已扫码待确认","status":101,"type":"A"}` |
| `GET …/scan_status?scanId=<已失效>` | 200 | `{"msg":"已失效","status":102,"type":"A"}` |
| `GET …/scan_status?scanId=zzz` | 400 | `{"msg":"Key: 'ScanStatusReq.scanId' Error:Field validation for 'scanId' failed on the 'len' tag"}` |
| `POST https://as.hypergryph.com/user/auth/v1/token_by_scan_code`，body `{"scanCode":"deadbeef"}` | 200 | `{"msg":"登录失败","status":100,"type":"B"}` |
| `GET https://zonai.skland.com/api/v1/game/player/binding`（无 `cred`） | 401（141 ms） | `{"code":10002, "message":"用户未登录", "timestamp":"1791772721"}` |

从实测得到的四条口径：

1. **鹰角侧（`as.hypergryph.com`）HTTP 恒 200，成败在 body 的 `status`**：`0` 成功、`100` 未扫码/登录失败、**`101` 已扫码待确认**（真机捕获，见下表）、`102` 已失效；`type`（`A`/`B`）与业务无关。判定只能看 `status`，不能看 HTTP 码（只有参数长度校验会 400）。
2. **`gen_scan/login` 直接返回 `scanUrl`**（`hypergryph://scan_login?scanId=<32 hex>`），二维码内容取它，不自己拼串。
3. `scanId` 是 32 位小写 hex，长度校验在上游做。
4. **zonai 侧（`zonai.skland.com`）未登录是 HTTP 401 + `code:10002`**；即两侧的错误形状不同，客户端要分开归一。
5. 本机到两个域名的连通性正常（`as.hypergryph.com` 125 ms、`zonai.skland.com` 141 ms），不需要代理。

### 2.2 扫码登录与取数链路（从 nonebot-plugin-skland / skland-daily-attendance 现行实现逐条核对）

| 步 | 请求 | 关键字段 |
| --- | --- | --- |
| 1 | `POST as.hypergryph.com/general/v1/gen_scan/login` `{"appCode":"4ca99fa6b56cc2ba"}` | `data.scanId`、`data.scanUrl` |
| 2 | `GET as.hypergryph.com/general/v1/scan_status?scanId=`（轮询） | `status`：`100` 未扫码 → **`101` 已扫码待确认**（**不能当终态**，继续轮询同一张码）→ `0` 并给 `data.scanCode`；`102` 已失效 |
| 3 | `POST as.hypergryph.com/user/auth/v1/token_by_scan_code` `{"scanCode":…}` | `data.token`（鹰角通行证 token） |
| 4 | `POST as.hypergryph.com/user/oauth2/v2/grant` `{"appCode":"4ca99fa6b56cc2ba","token":…,"type":0}` | `data.code` |
| 5 | `POST zonai.skland.com/api/v1/user/auth/generate_cred_by_code` `{"code":…,"kind":1}` | `data.cred`（凭证）、`data.token`（**签名密钥**） |
| 5b | `GET zonai.skland.com/api/v1/auth/refresh`（头带 `cred`） | `data.token`（续期签名密钥） |
| 6 | `GET zonai.skland.com/api/v1/game/player/binding` | `bindingList[]` 里 `appCode == "arknights"` 的 `uid` |
| 7 | `GET zonai.skland.com/api/v1/game/player/info?uid=<uid>` | `data.chars[]`、`data.status`、`data.charInfoMap`、`data.equipmentInfoMap` 等 |

**签名算法**（nonebot-plugin-skland `api/request.py::get_sign_header`，逐字照抄）：

```
timestamp = int(datetime.now().timestamp()) - 1
secret    = path + query_params + str(timestamp) + json.dumps(header_for_sign, separators=(",", ":"))
sign      = md5(hmac_sha256(token, secret).hexdigest()).hexdigest()
```

GET 时 `query_params` 是 URL 查询串，POST 时是 body 的紧凑 JSON；`token` 是第 5 步入库的签名密钥。请求头 `cred` / `sign` / `timestamp` / `platform` / `vName`。

**dId（设备指纹，`fp-it.portal101.cn` 那套混淆参数）只被签到类接口需要**，本里程碑只读，不碰（§3.2）。

### 2.3 字段对位（`data.chars[]` → `account_operator`，`maa_api/db/models.py:994-1044`）

| Skland | 本地列 | 换算 |
| --- | --- | --- |
| `chars[].charId` | `char_id` | 直用 |
| `chars[].evolvePhase`（0–2） | `elite` | 直用 |
| `chars[].level`（0–90） | `level` | 直用（`MAX_LEVEL = 90`，`rules.py:118`） |
| `chars[].potentialRank`（0–5） | `potential` | **+1**：本项目 `MAX_POTENTIAL = 6`（`rules.py:120`）、wire 样例 `"potential": 6`（`api/routers/account.py:87`），是「潜能等级」而非「潜能数」。W0 用 OperBox 交叉验证（§2.5-1） |
| `chars[].mainSkillLvl`（1–7） | `skill_level` | 直用（`MAX_SKILL_LEVEL = 7`，`rules.py:115`） |
| `chars[].skills[].specializeLevel`（0–3） | `masteries` | 键是技能槽位 `"1"`–`"3"`（`MasteryKey`，`api/routers/account.py:63`）：**槽位数取 `len(skills)`**（响应里就是这名干员真有的技能，不查本机表——稀有度读不出来时按表截槽会把 6★ 的第 2、3 个技能专精写成 0）；`0` 是**确认没有专精**，不是未识别 |
| `chars[].equip[].level`（0–3） | `modules` | 键是模组类型 `X/Y/D/A`（`MODULE_TYPES`，`rules.py:109`）：类型取**同一次响应**里的 `equipmentInfoMap`（本机资源层没有 `uniequip_table.json`，`GAMEDATA_FILES` 也不含它）；`locked=true` 或等级越界 → 不写该键；**非空 `equip` 里一个类型都认不出来时整列留 `None`（不知道），不落 `{}`（确认没有）**；空 `equip` 列表才是正面读数 `{}` |
| `chars[].favorPercent`（0–200） | **新列 `favor_percent`** | 直用（本轮唯一新增的练度列） |
| `data.status.name` / `data.status.level` | `skland_role.nickname` / `level` | 直用 |
| 不在 `chars` 里的 `char_id` | `own = False` | 只在**整轮完整**时写（§4.5 护栏 ①③） |

现有六列的口径不变：`elite/level/potential/skill_level/masteries/modules` 都可为 `NULL` ＝ 未识别（M35，`rules.py:81-88` 的 `UNKNOWN_TRAINING_FIELDS`）。**森空岛给的是权威值**，写入后这些列不再是 `NULL`，规划层的 `unknowns` 随之清空。

### 2.4 现有实现位置（删除面与保留面）

| 对象 | 位置 |
| --- | --- |
| 扫描服务 | `maa_api/services/account/scan.py`（1642 行，`TrainingScanService:454`、`submit:593`、`_drive:741`、`_scan_full:877`、`_detail_scan:1337`、`_record:1580`）、`scan_image.py`（104 行）、`scan_tasks.py`（533 行，`operator_scan_tasks():415`） |
| 扫写入 | `services/account/box.py`：`apply_detail_scan:544-668`、`record_training_scan:670-699` |
| 字段口径 | `services/account/rules.py`：`DETAIL_SCAN_FIELDS:77`（`skill_level/masteries/modules`） |
| 路由 | `api/routers/account.py`：`_scan_service:495-507`、`TrainingScanRequest:510-544`、POST `:547-585`、GET `:588-611`、DELETE `:614-627` |
| 装配 | `main.py:1013-1024`（延迟 import + `app.state.training_scan_service`） |
| Agent | `agent/tools/account.py`：`scan_operator_training:276`、`cancel_training_scan:297`、`_scan_service:430-445`；`agent/policy.py:39-47`（`ACCOUNT_WRITE_TOOLS`）、`:55`（`SAFE_TOOL_NAMES`）、`:343`（确认卡特化） |
| 知识库 | `agent/knowledge/builtin/playbook-promotion-planning.md:16,21`、`task-preconditions.md:27`、`screen-operator.md:25,30`、`operator-progression.md:20-41` |
| 前端 | `web/src/features/account/TrainingScanCard.tsx`（291 行）、`TrainingScanCard.test.tsx`（378 行）、`api.ts` 扫描段（`:184-235`、`:263/:267/:278`、`:301`、`:484`、`:506-583`）、`test-utils.tsx` 扫描件（`:156/:174/:193/:211/:224`、`:266-273`、`:375-394`）、挂载点 `OperatorsTab.tsx:14,143` |
| 探针 | `scripts/probe_training_scan.py`（593 行）、`scripts/probe_full_scan.py`（606 行） |
| **保留** | `main.py:978-1008` 的 OperBox / DepotInfo 回调链路；迁移 `0018_unknown_training.py`（它是「练度六列可空」，**不是扫描专属**）；`AccountRecognition` 表与历史 `kind='training'` 行 |

### 2.5 W0 必须真机取证的十项

1. `potentialRank` 与 OperBox 的 `potential` 是否差 1（同账号交叉对照，写进台账）。
2. `data.charInfoMap` 到底有哪些字段（是否含 `name` / `rarity` / `profession`）。
3. ~~`skills[].id` 与本机 `character_table` 的 `skillId` 是否同一命名空间~~——**已由设计消解**：槽位数直接取 `len(skills)`，不再依赖本机技能表（`skills[].id` 只用于样例记录）。
4. `equip[].id`（形如 `uniequip_002_xxx`）到模组类型 `X/Y/D/A` 的映射表来源与覆盖率——**部分消解**：认不出就留 `None`（不知道），不会把「不知道」写成「没有」；覆盖率的实测仍然需要真机。
5. `bindingList[]` 的真实字段（`uid` / `channelMasterId` / `nickname` / `level` / `isDefault` / `isDeleted`）。
6. ~~`scan_status` 确认后的 `status` 值与 `data.scanCode` 形状~~——**已实测**：中间态 `101`（`msg=已扫码待确认`）已捕获并写进 §2.1/§4.2；确认成功后的 `0` + `data.scanCode` 仍待一次完整扫码。
7. `cred` 与签名密钥的有效期、`auth/refresh` 的返回形状与失败语义。
8. `favorPercent` 的取值上限与已拥有但未放进编队的干员是否也在 `chars` 里。
9. `chars[]` 是否包含未拥有干员（决定 `own=False` 推导是否成立）与常规数量级（决定 §4.5 的 `SKLAND_MIN_CHARS` 阈值）。
10. 接口头里 `platform` / `vName` 的实际取值（照抄社区实现后再真机验证一次）。

取证脚本：`scripts/probe_skland_login.py`（W0 新增，真机扫码一次，只打印形状与计数，不落库、不写仓库）。

## 3. 范围

### 3.1 本里程碑做

0. **W0 事实取证**（§2.5 十项）——`scripts/probe_skland_login.py` + 台账。
1. **零依赖二维码编码器**（§4.1）：`maa_api/util/qr.py` 出 SVG，不引入任何第三方库。
2. **森空岛客户端与签名**（§4.3）：`maa_api/services/skland/client.py`。
3. **凭据与角色落库**（§4.4/§4.5）：迁移 `0019_skland_account.py` + `db/models.py` 两张表 + `account_operator.favor_percent`。
4. **扫码登录会话与六个端点**（§4.2/§4.6）：`maa_api/api/routers/skland.py`。
5. **同步与写库**（§4.5）：`services/skland/{mapping,sync}.py` + `OperatorBoxService.apply_skland_sync`。
6. **退役 M36–M41 扫描面**（§4.9）：后端 3 个文件、3 个端点、装配、Agent 2 个工具、前端卡片、5 个测试文件、2 个探针。
7. **Agent 面换血**（§4.7）：删 2 个工具、加 2 个工具、改 4 篇知识库。
8. **前端扫码绑定卡片**（§4.8）：`SklandBindingCard.tsx` + `FieldSource='skland'` 徽章 + 文案基线重设。
9. **文档与生成物**（§6）。

### 3.2 本里程碑不做

- **不做签到、日常、基建换班、剿灭**：只读账号数据，因此**不碰 dId 设备指纹**（`fp-it.portal101.cn` 那套混淆参数是整条链路里最脆弱的一环）。
- **不做仓库**：`player/info` 没有仓库数据，仓库识别（`DepotInfo`）仍是唯一来源。
- **不删 OperBox 链路**（M33）：保留作离线兜底与 W0 的交叉校验对照。
- **不做多账号 / 多角色管理**：只绑一个森空岛账号；`bindingList` 多角色时取 `isDefault`，否则取第一个。
- **不做前端的定时同步开关**：定时走 `config.yaml` 的 `skland.auto_sync_interval_minutes`（与 `agent.box_stale_days` 等阈值同款，运行时可改设置的 UI 不新增）。
- **不落其余字段**：`gainTime`（获得时间）、皮肤、基建、剿灭、肉鸽、`defaultSkillId`/`defaultEquipId` 本轮都不入库。
- **不引入任何第三方依赖**（用户硬约束）：Python 侧与 npm 侧都不加包。
- **不改既有 wire 形状**：`account_operator` 六列的读写形状、`PATCH` 语义、`AccountOperatorDiff` 协议保持不变。

## 4. 设计

### 4.0 模块边界

新增子系统 `maa_api/services/skland/`（`client.py` / `mapping.py` / `sync.py` / `__init__.py`）与 `maa_api/api/routers/skland.py`；二维码编码器放 `maa_api/util/qr.py`（与 `util/image.py` 同层）。**不复用 `services/external/`**：那层是为「外部情报内容源」设计的分发元数据层（`services/external/sources.py` 的 `SourceSpec` 只登记 GET 路径与查询参数，响应进缓存并按档位限流），森空岛是要带签名头、带 POST body、带账号凭证的取数通道，塞进去会把「凭证签名」与「公共内容缓存」两种语义搅在一起。

### 4.1 零依赖二维码编码器

- 接口：`maa_api/util/qr.py::qr_svg(payload: str, *, ecc: str = "M", scale: int = 6, border: int = 4) -> str`，返回完整 `<svg>` 字符串（`viewBox` 按模块数，黑色模块合成一条 `<path>`）。
- 支持：byte 模式、版本 1–10 自适应、ECC `L/M/Q/H`（默认 `M`）、8 个掩码按 ISO/IEC 18004 §8.8.2 罚分选优、格式信息 BCH(15,5) 与版本信息 BCH(18,6)。
- 本项目的 payload 是 `hypergryph://scan_login?scanId=<32 hex>` 共 46 字节，版本 3-M 足够；上限留到版本 10 是为了以后放别的串，代码量几乎不涨。
- **改法**：纯标准库实现（GF(256) 的 RS 纠错、分块表、掩码罚分、SVG 拼装），约 300–400 行 + 单测。
- **验证**（三重）：① 金标单测——用**临时 venv 里离线安装一次** `segno` 生成三个向量的模块矩阵（含 §2.1 实测拿到的真 `scanUrl`），把矩阵硬编码进 `tests/util/test_qr.py` 逐模块比对，`segno` 不进 `pyproject.toml`；② RS 生成多项式与分块表对照 ISO 表；③ 真机——用户用森空岛 App 扫页面上的码，能跳到确认页即端到端通过。
- **被否方案**：加 `segno`/`qrcode`（违反 m00077 的零依赖约束）；前端用 npm 二维码包（Agent 卡片与开放 API 拿不到图，且违反同一约束）。

### 4.2 扫码登录会话

- 会话存在**服务进程内存**里：`{session_id: {scan_id, created_at, poll_count, status}}`，`SKLAND_QR_TTL_SECONDS = 180`、`SKLAND_QR_POLL_INTERVAL_MS = 2000`、单会话最多 120 次轮询；服务重启会话即失效（用户重新点「扫码绑定」即可，风险表已登记）。
- `POST /api/skland/login/qrcode`：调 `gen_scan/login` → 用返回的 `scanUrl` 生成 SVG → 返回 `{session_id, qr_svg, payload, expires_at, poll_interval_ms}`。
- `GET /api/skland/login/qrcode/{session_id}`：按上游 `status` 分流——`0` → 继续走第 3–5 步并落库，返回 `succeeded`；`100` → `waiting`；`102` → `expired`；超时/参数错 → `failed`（带 `message`）。
- 状态只有 **`waiting` / `succeeded` / `expired` / `failed`** 四态；上游 `status` 有三段，`100` 与 **`101`（已扫码待确认，真机实测）** 都归 `waiting`——`101` 时把上游文案放进响应的 `message`，前端在二维码旁显示「已扫码待确认」，**不当作失败、也不换码**。
- 成功落库后**立即销毁会话**，避免同一 `scanId` 被二次消费。

### 4.3 客户端与签名

- `services/skland/client.py::SklandClient`：`httpx.AsyncClient`（`timeout=15`，两个 `base_url` 常量），方法 `generate_scan` / `scan_status` / `token_by_scan_code` / `grant` / `generate_cred` / `refresh` / `binding` / `player_info`。
- 签名按 §2.2 的算法实现（`timestamp = int(time.time()) - 1`，HMAC-SHA256 用签名密钥作 key，再 MD5 十六进制串）；头 `cred` / `sign` / `timestamp` / `platform` / `vName`，`User-Agent` 用项目 UA（与 `services/external/http.py` 的取法一致）。
- 错误归一：鹰角侧 `status != 0`、zonai 侧 `code != 0`（含 401/10002）都抛 `AppError`，码见 §4.6；`message` 只带上游 `msg` / `message`，**不把原始 body 写进日志**（与 `services/external/http.py` 的日志口径一致）。
- 重试：只对网络层异常与 5xx 重试一次（指数退避），业务码不重试——重复调用取数接口没有副作用，但反复打非官方接口是风控面。

### 4.4 凭据落库与加密

- 新表 `skland_credential`（单行，`id` 固定 1）：`cred_encrypted`、`token_encrypted`（`cryptography.fernet` 的 token 字符串）、`bound_at`、`refreshed_at`、`updated_at`。
- 新表 `skland_role`：`uid`（PK）、`app_code`、`channel_master_id`、`nickname`、`level`、`is_default`、`synced_at`。
- 密钥：新配置项 `skland.credential_key`（Fernet key），与 `llm_encryption_key` 同款；**缺失时绑定端点回 409 `SKLAND_ENCRYPTION_KEY_INVALID`**，文案给生成命令（行为对齐 `api/routers/agent.py:688-692` 对 `llm_encryption_error` 的处理）。
- 明文只出现在内存与签名函数里；日志、事件、Agent 审计都不打印 `cred`/`token`。
- `DELETE /api/skland/binding`：删凭据与角色行，**不动 `account_operator`**——练度是账号数据，解绑不该抹掉它。
- **被否方案**：首次绑定时自动生成密钥并落盘。理由：静默改用户文件，且密钥与密文同机同目录，安全收益低而惊喜成本高。

### 4.5 同步与写库

- `services/skland/sync.py::SklandSyncService.sync()`：取凭据 → 必要时 `refresh` → `binding` 取 uid → `player_info(uid)` → `mapping.to_operator_updates(chars, catalog, equipment_catalog)` → 单事务写库。
- `services/skland/mapping.py` 是**纯函数**（可脱离数据库单测）：按 §2.3 的表做换算，产出 `list[OperatorTrainingUpdate]`。
- 写库走新方法 `OperatorBoxService.apply_skland_sync(updates)`，事务与 diff 语义仿 `apply_detail_scan`（`box.py:544-668`）：写 `own / elite / level / potential / skill_level / masteries / modules / favor_percent`，`field_sources[f] = "skland"`，`recognized_at = now`；与既有 `manual` / `agent` 值不同时记 `AccountOperatorDiff`（`recognized_value` 是新值，来源标 `skland`）。
- **四条硬护栏**：
  1. `len(chars) < SKLAND_MIN_CHARS`（初值 200，W0 按实测数量级定稿）→ 整轮不写、不置 `own=False`，回 `SKLAND_UPSTREAM_FAILED`（防半截响应毁数据）。
  2. 归一后的 `masteries` / `modules` 过一遍既有 `normalize_masteries` / `normalize_modules`（`rules.py:217,280`），越界值丢弃并计数（`dropped_invalid`），不猜。
  3. 只有整轮完整成功时才把「不在 `chars` 里的干员」置 `own = False`。
  4. 先算完再写，单事务；任何异常整轮回滚，库里不会出现写了一半的状态。
- `AccountRecognition` 记一条 `kind="skland"`（`item_count` = 本轮改动行数）；历史 `kind="training"` 行保留不动。
- 同步摘要 `SklandSyncSummary`：`total / created / updated / unchanged / ownership_cleared / dropped_invalid / duration_ms`。
- 频率护栏（不是功能）：全局 `asyncio.Lock` 互斥，忙时 409 `SKLAND_SYNC_BUSY`；60 秒冷却（`SKLAND_MIN_SYNC_INTERVAL_SECONDS = 60`），冷却内重复 `POST` 回 429 `RATE_LIMITED`。
- 定时：`skland.auto_sync_interval_minutes`（默认 0 ＝ 关，>0 时最短 60）由 lifespan 注册 apscheduler interval job；job 内同样受锁与冷却约束，失败只写 `last_error`，不改绑定状态。

### 4.6 接口契约

新 router `maa_api/api/routers/skland.py`（`prefix="/api/skland"`，`tags=["skland"]`，全部 `Depends(require_auth)`）：

| 方法 | 路径 | 说明 | 成功响应 |
| --- | --- | --- | --- |
| POST | `/api/skland/login/qrcode` | 生成扫码会话 | `{session_id, qr_svg, payload, expires_at, poll_interval_ms}` |
| GET | `/api/skland/login/qrcode/{session_id}` | 轮询扫码状态 | `{state, message?, roles?}` |
| POST | `/api/skland/login/manual` | 手动兜底绑定（`cred` 必填、`token` 必填，绑前用 `binding` 验一次） | 同 `GET /status` |
| GET | `/api/skland/status` | 绑定与同步状态 | `{bound, account?, roles[], last_sync_at, last_sync, last_error, qr_ttl_seconds, poll_interval_ms}` |
| POST | `/api/skland/sync` | 同步练度（**同步执行**，秒级） | `{status:"succeeded", summary:{…}}` |
| DELETE | `/api/skland/binding` | 解绑（删凭据与角色，保留练度） | `{bound:false}` |

**同步不做异步任务与进度轮询**：整轮只是一次上游取数加一次写库（实测单请求 141 ms），`POST` 等结果返回即可；前端按钮走 `loading`，不需要 `TrainingScanCard` 那套状态机。被否方案：异步提交 + `GET` 轮询（多一个服务层状态机、多一个端点，换不来任何东西）。

删除的端点（M36）：`POST /api/account/training-scan`（`account.py:547-585`）、`GET`（`:588-611`）、`DELETE`（`:614-627`），以及 `_scan_service`（`:495-507`）与 `TrainingScanRequest`（`:510-544`）。

新增错误码（`domain/errors.py`，命名与状态对齐既有风格）：`SKLAND_NOT_BOUND`(409)、`SKLAND_LOGIN_EXPIRED`(409)、`SKLAND_LOGIN_FAILED`(401)、`SKLAND_QR_SESSION_NOT_FOUND`(404)、`SKLAND_UPSTREAM_FAILED`(502)、`SKLAND_ENCRYPTION_KEY_INVALID`(409)、`SKLAND_SYNC_BUSY`(409)，另有复用既有 `RATE_LIMITED`(429)。

### 4.7 Agent 面

- 删：`scan_operator_training`（`agent/tools/account.py:276`）、`cancel_training_scan`（`:297`）、参数模型（`:16`/`:25`/`:165`）、`_scan_service`（`:430-445`）、`agent/policy.py` 的 `:39-47` / `:55` / `:343` 三处。
- 加：`get_skland_status`（只读，风险 `NONE`）、`sync_skland_training`（写，进 `ACCOUNT_WRITE_TOOLS`，**每次确认**，与旧 `scan_operator_training` 同款风险口径）。
- 计数：77 − 2 + 2 = **77**（对模型可见 76 不变），docs/11 `:231`、`:244-245` 回改。
- 知识库四篇改写：`playbook-promotion-planning.md:16,21`、`task-preconditions.md:27`、`screen-operator.md:25,30`、`operator-progression.md:20-41`——把「调 `scan_operator_training` 去读屏」改成「调 `sync_skland_training` 取官方数据；未绑定时引导用户到账号页扫码」，并说明数据来自森空岛、可能滞后于游戏内最新一次操作。

### 4.8 前端

- 删：`web/src/features/account/TrainingScanCard.tsx`（291 行）与 `TrainingScanCard.test.tsx`（378 行）；`api.ts` 扫描段（`:184-235`、`:263/:267/:278`、`:301`、`:484`、`:506-583`）；`test-utils.tsx` 扫描件（`:156/:174/:193/:211/:224`、`:266-273`、`:375-394`）。`constraints.ts` / `format.ts` 是练度字段通用件，**只清头注释里的 M36 提法**，不整删。
- 新增 `web/src/features/account/SklandBindingCard.tsx`：
  - 未绑定：`<img src={"data:image/svg+xml;charset=utf-8," + encodeURIComponent(qr_svg)} alt="森空岛登录二维码" />` + 2 秒轮询（照抄 `TrainingScanCard.tsx:108-114` 的 `refetchInterval` 口径）+ 剩余有效期 + 「手动输入 cred」`ResponsiveSheet`。
  - 已绑定：账号与角色、上次同步时间与摘要、「同步练度」按钮（`Button loading`）、「解绑」（`useConfirm`，`components/ui/confirm-dialog.tsx:66`）；错误用 `InlineAlert` 就地显示。
  - **不用 `dangerouslySetInnerHTML`**：SVG 由后端生成，走 data URI 更稳。
- `OperatorsTab.tsx:14,141-143` 的挂载点换成 `<SklandBindingCard />`；`FieldSource` 加 `'skland'`，连带 `api.ts:16`、`format.ts:30-41`、`OperatorsTab.tsx:37-41` 三张标签/提示/色调表。
- **不加子路由、不加新分段**（被否：`/more/account/skland` 独立页——绑定与练度是同一件事的上下文，多一次导航不划算）。
- `components/layout/nav.ts:36` 描述改为「干员识别、仓库识别、森空岛练度同步与手动修正」。
- 文案基线：删卡片会让用户可见计数下降（允许方向），仍按流程 `--write-baseline` 重设并提交 `docs/copy-audit/*`。

### 4.9 删除面

| 文件 / 位置 | 动作 |
| --- | --- |
| `maa_api/services/account/scan.py`、`scan_image.py`、`scan_tasks.py` | 整文件删 |
| `services/account/box.py` `apply_detail_scan` / `record_training_scan` | 删函数，调用点随扫描面消失 |
| `services/account/rules.py` `DETAIL_SCAN_FIELDS` | 换成 `SKLAND_FIELDS`（同一组三列，口径改成「森空岛能覆盖练度六列 + 信赖」） |
| `api/routers/account.py` 扫描段 | 删（§4.6） |
| `main.py:1013-1024` | 删装配 |
| `agent/tools/account.py`、`agent/policy.py` | 删工具与策略条目（§4.7） |
| `web/src/features/account/TrainingScanCard*.tsx` | 整文件删 |
| `scripts/probe_training_scan.py`、`probe_full_scan.py` | 整文件删 |
| 测试 | 删 `tests/services/test_account_training_scan.py`(2134)、`test_account_detail_scan.py`(386)、`test_account_scan_image.py`(148)、`tests/api/test_account_scan_router.py`(334)、`tests/agent/test_training_scan_tools.py`(248)；摘 `test_account_box.py:380-474`、`test_account_box_service.py:657-771`、`test_account_box_rules.py:121-232`、`tests/agent/test_tool_catalog.py:42` 里的扫描例 |
| **不删** | 迁移 `0018_unknown_training.py`、`AccountRecognition` 表与 `kind` 列、`main.py:978-1008` 的 OperBox/Depot 链路、`rules.UNKNOWN_TRAINING_FIELDS`、前端编辑器与差异横幅、`tests/db/test_unknown_training_migration.py` |

## 5. 兼容与风险

| 风险 | 处理 |
| --- | --- |
| 森空岛与鹰角接口都是非官方接口，随时变更或加风控 | 只读、低频（手动 + 定时 ≥ 60 min）、60 秒冷却、单实例互斥；形状变化由 `mapping.py` 的纯函数单测与 W0 形状锁定用例先抓住，失败不写库 |
| 凭据泄露（`cred` 等同账号凭证） | Fernet 加密落库、密钥独立配置项、日志与审计不打印、解绑即删 |
| `potentialRank` 与本地 `potential` 差 1 判错 | W0 用 OperBox 交叉验证，写库前断言落在 1–6 |
| `data.charInfoMap` 与本机 `character_table` 不一致 | 名称/星级/职业**以本机 catalog 为准**，缺项才用接口；映射不到就不写该字段（不猜） |
| 自研二维码写错，用户扫不出来或扫出乱码 | 金标向量单测 + ISO 表对照 + 真机扫码验收（W4） |
| 半截响应把整箱干员标成「未拥有」 | §4.5 护栏 ①③：数量阈值 + 只在完整轮次写 `own=False` |
| 扫描线删掉后 `skill_level/masteries/modules` 只剩人工 `PATCH` 与 `update_operator_box` 两个写入者 | 森空岛成为主写入者；未绑定时保留 `NULL ＝ 未识别` 语义，卡片与 Agent 都引导去绑定 |
| 定时同步在用户没预期时改数据 | 默认关闭；开启后仍走同一事务与护栏，摘要与 `last_error` 在状态端点可查 |
| 服务重启丢掉进行中的扫码会话 | 会话 TTL 180 秒、上游失效即 `expired`；前端提示重新生成，不假装成功 |

## 6. 文档同步

| 文档 | 改动 |
| --- | --- |
| `docs/superpowers/specs/2026-10-12-m42-skland-training.md` | 本文件 |
| `docs/superpowers/plans/2026-10-12-m42-skland-training.md` | 新增计划 |
| `docs/superpowers/ledgers/2026-10-12-m42-skland-training.md` | 计划完成后归档 |
| `docs/13-决策记录.md` | 从 **§8.61** 起：森空岛接入（凭据与签名、二维码自研、同步语义与护栏）、扫描线退役（删掉的端点/工具/卡片与知识库改写），六段式体例（§8.60 在 `:1980`，是当前最后一条） |
| `docs/README.md` | 决策速查表的账号数据段（**L122 起，其中练度扫描行在其后**）改写：自定义读屏扫描退役、改用森空岛；链条延伸到 §8.61+ |
| `docs/12-实施计划与里程碑.md` | 在 M41 条目后插入 M42 节（五段式：规格/来源/分支、交付 bullets、依赖、验证方式、验收结论），并更新 `## 3. 依赖关系`（**L722** 起）；注意 docs/12 目前还没有 M41 条目（它的 W5 才登记） |
| `docs/05-API规范与路由清单.md` | 账号端点表 **L794-818**（删 `training-scan` 三行与 M36/M39 说明段）、新增 `/api/skland` 六端点节、错误码表 **L361** 附近补 7 个新码 |
| `docs/04-数据模型与持久化.md` | `### 5.18 account_operator`（**L873**）补 `favor_percent` 与 `field_sources='skland'`；新增 `skland_credential` / `skland_role` 两节；`account_recognition.kind`（**L934**）补 `skland` |
| `docs/11-Agent模块设计.md` | **L231** 计数（77，可见 76 不变）、**L244-245** 换成 `get_skland_status` / `sync_skland_training` |
| `docs/09-前端重构方案.md` | **L525/L527/L529** 的扫描卡片与两类扫描段落改成森空岛绑定卡片与同步 |
| 生成物 | `web/openapi.json` 与 `web/src/types/api.d.ts`：先 `.venv/bin/python scripts/dump_openapi.py`（不带 `--check`）再 `cd web && pnpm gen:api`，两者都提交（删 3 个端点 + 加 6 个端点） |
| `docs/copy-audit/*` | 文案基线棘轮重设（`scripts/copy_inventory.py --write-baseline` 后复跑 `--check`） |

## 7. 验收

1. W0 十项取证结果落 `docs/superpowers/ledgers/2026-10-12-m42-skland-training.md`，含真机扫码一次抓到的 `scan_status` / `grant` / `generate_cred` / `binding` / `player/info` 形状与 `chars` 数量级。
2. `potential` 与 OperBox 的交叉对照结论写进台账，并有一条单测钉住「`potentialRank` → `potential` 差 1」。
3. 二维码编码器：金标矩阵单测逐模块通过 + 真机扫码进入森空岛确认页。
4. 单测：`client`（签名待签串与上游形状）、`mapping`（字段对位与越界丢弃）、`apply_skland_sync`（四条护栏，含「数量不足整轮不写」与「完整轮次清 `own`」的负向对照）、`qr`（金标）、`skland` 路由（六端点 + 错误码）。
5. 退役干净：`.venv/bin/python -m pytest -q` 全绿，`grep -rn "training-scan\|TrainingScan\|scan_operator_training\|probe_training_scan"` 在 `maa_api/ tests/ web/src/ scripts/ docs/` 只剩 docs/13 与 docs/12 的历史记录。
6. 门禁：`.venv/bin/python -m pytest -q`；`cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`；`.venv/bin/python scripts/dump_openapi.py --check`；`.venv/bin/python scripts/frontend_smoke.py`；`.venv/bin/python scripts/copy_inventory.py --check`。
7. 真机验收（用户执行）：账号页扫码绑定 → 显示角色 → 点「同步练度」→ 干员列表六列与信赖可见、字段来源徽章显示「森空岛」→ 重启服务后仍在。
8. 台账补实测数字，提交后 `git checkout refactor/v2 && git merge --no-ff m42-skland-training`，打 tag `v2-m42`。
