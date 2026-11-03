# M42 计划：退役自定义练度扫描，改用森空岛（Skland）账号练度

- 规格：`docs/superpowers/specs/2026-10-12-m42-skland-training.md`
- 分支：`m42-skland-training`（从 `refactor/v2` 新建）
- 前置：M41 合并并打 `v2-m41`。M42 删的正是 M36–M41 那批行，M41 没合流就开分支会在 `scan.py` / `account.py` 上直接撞车；若用户要求不等 M41，则从 `m41-identity-hardening` 起分支并在台账记一条裁决。
- 设备占用：**不占 Android 设备与模拟器，不启动内核**。只有 W0 需要用户本人拿手机用森空岛 App 扫一次码。

## 波次

### W0 事实取证（先做，不占设备）

- 为什么：规格 §2.5 有十项只能在真机上确认（`potentialRank` 语义、`charInfoMap` 字段、模组映射、`chars` 数量级与是否含未拥有干员）。这些结论直接决定 §4.5 的阈值与 §2.3 的对位表，猜错会把整箱干员写坏。
- 写范围：`scripts/probe_skland_login.py`（新增）、`docs/superpowers/ledgers/2026-10-12-m42-skland-training.md`（新增）。
- 做法：
  1. 脚本只做只读：`gen_scan/login` → 打印 `scanUrl` 与二维码内容 → 提示用户扫码 → 轮询 `scan_status` → 断言 `status` 从 `100` 到 `0` → `token_by_scan_code` → `grant` → `generate_cred_by_code` → `binding` → `player_info`。
  2. 每步只打印**形状与计数**（键名、`len(chars)`、`status`/`code`、耗时），不打印 `token`/`cred` 明文，不落库、不改仓库文件。
  3. 把 `data.chars[0]` 与 `charInfoMap` / `equipmentInfoMap` 的各一条样例（脱敏后）写进台账，作为 §2.3 对位表的证据。
  4. 用同一账号跑一次 `OperBox`（保留的 M33 链路）或直接看现有 `account_operator` 行，与森空岛的 `evolvePhase` / `level` / `potentialRank` / `mainSkillLvl` 逐条交叉对照，把结论写进台账。
- 验收：台账里十项逐条有结论（含未确认项的「仍是未知」标注）；`potential` 差 1 的对照有具体两名干员的数字；`SKLAND_MIN_CHARS` 有实测依据的初值。
- blocked_by：无。

### W1 离线基础：二维码编码器 + 签名客户端 + 字段映射（不占设备）

- 为什么：规格 §4.1 要求零依赖自研二维码；§4.3 的签名与 §4.5 的字段换算都是纯逻辑，能在没有网络与没有设备的情况下先钉死，后面服务层只做编排。
- 写范围：`maa_api/util/qr.py`（新增）、`maa_api/services/skland/__init__.py`、`client.py`、`mapping.py`（新增）、`tests/util/test_qr.py`、`tests/services/skland/test_client.py`、`test_mapping.py`（新增）。
- 做法：
  1. `qr_svg(payload, *, ecc="M", scale=6, border=4) -> str`：byte 模式、版本 1–10 自适应、GF(256) RS 纠错、8 掩码罚分选优、格式/版本信息 BCH，输出 `<svg>`（黑模块一条 `<path>`）。
  2. 金标：临时 venv `pip install segno`（**不进 `pyproject.toml`**），生成三个向量的模块矩阵（含 §2.1 实测的真 `scanUrl`）硬编码进 `tests/util/test_qr.py` 逐模块比对；另加 RS 生成多项式与分块表对照 ISO 表的用例。
  3. `SklandClient`：`generate_scan` / `scan_status` / `token_by_scan_code` / `grant` / `generate_cred` / `refresh` / `binding` / `player_info`；签名按规格 §2.2 逐字实现；两套错误归一（鹰角 `status` / zonai `code`）；只对网络异常与 5xx 重试一次。
  4. `mapping.to_operator_updates(chars, catalog, equipment_catalog)`：纯函数，按规格 §2.3 对位，`potentialRank + 1`，越界值丢弃计数；名称/星级/职业以本机 catalog 为准。
- 验收：`tests/util/test_qr.py`、`tests/services/skland/test_client.py`、`test_mapping.py` 全过（签名待签串、两套错误形状、差 1 换算、越界丢弃各有具名用例）；`grep -rn "segno\|qrcode" pyproject.toml` 无输出。
- blocked_by：W0（对位表与阈值要证据）。

### W2 服务层：迁移、凭据、同步与六个端点（不占设备）

- 为什么：把「绑一个森空岛账号、点一下同步、干员练度进库」这条链打通；这是取代扫描线的**新写入者**，必须在拆旧之前先立起来。
- 写范围：`maa_api/db/migrations/versions/0019_skland_account.py`（新增）、`maa_api/db/models.py`、`maa_api/db/repositories/account.py`、`maa_api/settings.py`（`SklandSettings` + `config.template.yaml`）、`maa_api/services/account/box.py`（`apply_skland_sync`）、`maa_api/services/account/rules.py`（`SKLAND_FIELDS`）、`maa_api/services/skland/sync.py`（新增）、`maa_api/api/routers/skland.py`（新增）、`maa_api/domain/errors.py`、`maa_api/main.py`（装配 + 定时 job）、`tests/db/test_skland_migration.py`、`tests/services/skland/test_sync.py`、`tests/services/test_account_box_skland.py`、`tests/api/test_skland_router.py`（新增）。
- 做法：
  1. 迁移 0019 建 `skland_credential`（单行，密文列）与 `skland_role`，并给 `account_operator` 加可空 `favor_percent`（不删任何既有列，`0018` 不动）。
  2. 配置段 `skland`：`credential_key`（Fernet，缺省时绑定端点回 409）、`auto_sync_interval_minutes`（默认 0＝关，>0 时校验 ≥60）、`request_timeout`；同步改 `config.template.yaml`。
  3. `apply_skland_sync(updates)`：仿 `apply_detail_scan` 的事务与 diff 语义，写八列 + `field_sources='skland'`；四条护栏（数量阈值、越界丢弃、只在完整轮次清 `own`、单事务）逐条实现。
  4. 六个端点按规格 §4.6；扫码会话在内存（TTL 180 s、轮询 2 s、上限 120 次）；`POST /sync` 同步执行，60 秒冷却 + 全局锁；定时 job 走 apscheduler，与手动同步共用同一实现。
  5. 日志只记端点、状态码、耗时、改动行数；`cred`/`token` 一律不进日志。
- 验收：`tests/api/test_skland_router.py`（六端点 + 7 个新错误码）、`tests/services/skland/test_sync.py`（含「`len(chars)` 不足整轮不写」与「完整轮次清 `own`」两条负向对照）、`tests/services/test_account_box_skland.py`（diff 与字段来源）、`tests/db/test_skland_migration.py`（升级/降级）全过；`grep -rn "cred\|token" logs` 手检无明文。
- blocked_by：W1。

### W3 契约与前端：生成物、绑定卡片、删扫描卡片（不占设备）

- 为什么：先让前端不再依赖 `training-scan` 三端点，W4 才能安全删端点；同时把「扫码绑定 + 同步」做成用户唯一入口。
- 写范围：`web/openapi.json`、`web/src/types/api.d.ts`（生成物）、`web/src/features/account/SklandBindingCard.tsx`（新增）、`SklandBindingCard.test.tsx`（新增）、`web/src/features/account/api.ts`、`test-utils.tsx`、`OperatorsTab.tsx`、`format.ts`、`web/src/components/layout/nav.ts`、删除 `web/src/features/account/TrainingScanCard.tsx` 与 `TrainingScanCard.test.tsx`。
- 做法：
  1. `.venv/bin/python scripts/dump_openapi.py`（不带 `--check`）→ `cd web && pnpm gen:api`。
  2. 删扫描段的类型、`AccountEndpoints` 三处覆盖签名、`accountKeys.trainingScan()`、四个 `normalizeTrainingScan*`、三个 wrapper；`test-utils.tsx` 摘扫描夹具与假后端分支（`installAccountApi` / `renderAccount` 保留）。
  3. `SklandBindingCard.tsx`：未绑定走二维码 + 2 秒轮询 + 有效期 + 手动 `cred` Sheet；已绑定显示账号/角色/上次同步摘要 + 「同步练度」+「解绑」（`useConfirm`）。
  4. `FieldSource` 加 `'skland'`，同步 `api.ts:16`、`format.ts:30-41`、`OperatorsTab.tsx:37-41`；挂载点 `OperatorsTab.tsx:143` 换成新卡片；`nav.ts:36` 描述改写。
- 验收：`pnpm gen:api:check`、`pnpm typecheck`、`pnpm test --run`（含新的绑定卡片用例：二维码渲染、轮询状态四态、同步按钮 `loading`、解绑确认）、`pnpm build`、`.venv/bin/python scripts/frontend_smoke.py` 全绿。
- blocked_by：W2。

### W4 退役扫描面与 Agent 换血（不占设备）

- 为什么：新写入者已上线且前端已改口，这时才能整块拆掉 M36–M41；否则会出现「界面还在调已删端点」的窗口。
- 写范围：`maa_api/services/account/scan.py`、`scan_image.py`、`scan_tasks.py`（删）、`maa_api/api/routers/account.py`、`maa_api/services/account/box.py`、`rules.py`、`maa_api/main.py`、`maa_api/agent/tools/account.py`、`maa_api/agent/policy.py`、`maa_api/agent/knowledge/builtin/*.md`（四篇）、`scripts/probe_training_scan.py`、`scripts/probe_full_scan.py`（删）、`tests/services/test_account_training_scan.py`、`test_account_detail_scan.py`、`test_account_scan_image.py`、`tests/api/test_account_scan_router.py`、`tests/agent/test_training_scan_tools.py`（删）与相关摘除。
- 做法：
  1. 删三个服务文件与两个探针；删 `account.py` 的 `_scan_service` / `TrainingScanRequest` / 三个路由；删 `main.py:1013-1024` 装配。
  2. 删 `box.apply_detail_scan` 与 `record_training_scan`；`rules.DETAIL_SCAN_FIELDS` 换成 `SKLAND_FIELDS`；`UNKNOWN_TRAINING_FIELDS` 与迁移 `0018` 保留。
  3. Agent 删 `scan_operator_training` / `cancel_training_scan` 与参数模型、`_scan_service`、policy 三处；加 `get_skland_status`（只读）与 `sync_skland_training`（写，每次确认）；四篇知识库改为引导 `sync_skland_training` 与扫码绑定。
  4. 删五个测试文件，摘掉 `test_account_box*.py`、`test_tool_catalog.py:42` 里的扫描用例。
- 验收：`.venv/bin/python -m pytest -q` 全绿；`grep -rn "training-scan\|TrainingScan\|scan_operator_training\|cancel_training_scan\|probe_training_scan" maa_api/ tests/ web/src/ scripts/` 无输出；`grep -rn "training-scan" docs/` 只剩 docs/12 与 docs/13 的历史条目。
- blocked_by：W3。

### W5 文档、台账、门禁与合并（不占设备）

- 为什么：规格 §6 列的同步面必须与代码同一个提交落地；台账要带上主 agent 亲自复跑的实测数字。
- 写范围：`docs/13-决策记录.md`（§8.61 起）、`docs/README.md`（L122 起账号数据段）、`docs/12-实施计划与里程碑.md`（M42 节 + L722 起依赖关系）、`docs/05-API规范与路由清单.md`（L794-818 + 错误码表）、`docs/04-数据模型与持久化.md`（§5.18 + 两张新表 + `kind`）、`docs/11-Agent模块设计.md`（L231/L244-245）、`docs/09-前端重构方案.md`（L525-529）、`docs/copy-audit/*`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/superpowers/ledgers/2026-10-12-m42-skland-training.md`。
- 做法与顺序：
  1. 改文档 → `.venv/bin/python scripts/dump_openapi.py` → `cd web && pnpm gen:api` → `pnpm gen:api:check` → `.venv/bin/python scripts/copy_inventory.py --check`（有回潮就 `--write-baseline` 后复跑）。
  2. 全门禁：`.venv/bin/python -m pytest -q`、`cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`、`.venv/bin/python scripts/frontend_smoke.py`、`.venv/bin/python scripts/copy_inventory.py --check`。
  3. 台账补门禁实测数字与真机结论（扫码一次、同步一次、`potential` 对照、重启后字段仍在）。
  4. 提交（`feat(m42): …` / `docs(13): §8.61 …` / `chore(docs): 文案基线按 §8.61 重设`）→ `git checkout refactor/v2 && git merge --no-ff m42-skland-training` → `git tag v2-m42`。
- 验收：生成物四处 `--check` 全 0；文案基线 `--check` 通过；台账含主 agent 亲自复跑的门禁数字；合并提交为单行 `merge: M42 退役自定义练度扫描改用森空岛（见 docs/13 §8.61）`，tag `v2-m42` 指向它。
- blocked_by：W4。

## 与扫描线的关系

M42 合并后，M36–M41 那条读屏扫描线不再有任何调用点：真机全量扫描轮次、读屏稳定门（M41 §4.0c）、拉丁名重锚、`class_verified` 计数与 `TrainingScanCard` 摘要全部退出历史舞台，M41 尚未完成的真机复跑项随之一并作废（在 docs/13 §8.61 的「偏离与不做」里写明）。`OperBox` / `DepotInfo` 两条回调链路与账号页的其余部分不受影响。

## 验收

见规格 §7：W0 十项取证落台账、二维码金标 + 真机扫码、单测、五条门禁命令、退役 grep 检查、真机绑定与同步验收、`--no-ff` 合并打 `v2-m42`。
