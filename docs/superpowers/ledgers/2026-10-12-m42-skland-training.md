# M42 · 退役自定义练度扫描，改用森空岛（Skland）账号练度 · 进度

- 分支：`m42-skland-training`（自 `refactor/v2`，基点 `26bd46d` = tag `v2-m40`），已 `--no-ff` 合并回 `refactor/v2`（merge `ecce1a2`）并打 tag **`v2-m42`**；分支内四个提交：`a5abfa6` 后端、`ee61042` 前端、`f2b4093` 文档、`673639c` 文案基线
- 规格：`docs/superpowers/specs/2026-10-12-m42-skland-training.md`
- 计划：`docs/superpowers/plans/2026-10-12-m42-skland-training.md`
- 来源：用户 m00077 要求「去除自定义练度扫描任务，全面接入森空岛扫码获取，不要外源项目依赖，只能参考别的项目，先编写 docs」；m00145 四项裁决（只退役详情扫描 / 自研二维码 / 扫码 + 手动兜底 / 手动为主 + 可选定时）。
- 真机：**不需要 Android 设备与模拟器**；只有 W0 需要用户拿手机用森空岛 App 扫一次码。

## 任务状态

| 工作项 | 写范围 | 状态 |
| --- | --- | --- |
| W0 事实取证 | `scripts/probe_skland_login.py`、本台账 | **部分完成**：脚本跑通 `new`，实测到会话约 3 分钟后失效（`status=102`）、`gen_scan` 返回 `scanUrl`、非法 `scanId` 400、zonai 未登录 401+10002；**扫码全链路（grant/generate_cred/binding/player_info）待用户扫码** |
| W1 离线基础 | `maa_api/util/qr.py`、`maa_api/services/skland/{__init__,client,mapping,store,sync,login}.py`、`tests/services/test_skland_{client,mapping,sync}.py` | 完成（client 34 例 + mapping 20 例 + sync/store 10 例全绿；qr.py 已产出并被 W0 探针实跑，金标测试见 W1 结论） |
| W2 服务层 | 迁移 `0019_skland_account.py`、`db/models.py`、`services/account/{box,rules}.py`、`services/skland/{store,sync,login}.py`、`api/routers/skland.py`、`settings.py`、`config.template.yaml`、`scripts/generate_skland_key.py`、`domain/errors.py`、`main.py`、六个新测试 | 完成（六端点 + 七个错误码 + 两张表 + `favor_percent` + 定时同步 job + 路由测试 13 例 + 写库语义 7 例全绿） |
| W3 契约与前端 | 生成物、`SklandBindingCard.tsx`、`api.ts`、`test-utils.tsx`、`OperatorsTab.tsx`、`format.ts`、`nav.ts`，删 `TrainingScanCard*` | 完成（typecheck / 777 例前端测试 / build 全绿；`grep TrainingScanCard web/src` 为空） |
| W4 退役扫描面与 Agent | `services/account/{scan,scan_image,scan_tasks}.py`（删）、`api/routers/account.py`、`agent/tools/account.py`、`agent/policy.py`、知识库一篇、探针两个、五个测试文件 | 完成（后端 3 文件 + 3 端点 + 2 方法 + 2 探针 + 5 测试删除；Agent 删 2 加 2，工具总数仍 77/可见 76） |
| W5 文档、台账、门禁与合并 | `docs/13` §8.61/§8.62、`docs/README`、`docs/12`、`docs/05`、`docs/04`、`docs/11`、`docs/09`、`docs/15`、`docs/copy-audit/*`、生成物 | 文档已写；门禁与合并进行中 |

## Rulings I made（实施中定的口径）

1. **不合并 M41，直接从 `refactor/v2` 开 M42**（偏离计划「前置：M41 合并打 `v2-m41`」）。理由：M41 全部提交要么是 `fix(scan)`（M42 W4 会整块删掉的行），要么是 `docs(m41)`；把一条即将退役的半成品线合进集成分支没有收益，反而会与 W4 的删除大面积冲突。`m41-identity-hardening` 分支原样留在本地作为历史，M41 的真机标定（W1）与后续波次随扫描线退役，§8.61 的「偏离与不做」会写明。
2. **测试文件用平铺命名**（`tests/services/test_skland_client.py`），不按计划里写的 `tests/services/skland/`：仓库 `tests/services/` 是平铺的，子目录会破坏既有约定。
3. **错误码与 docs/05 同一次改动**：`tests/domain/test_errors.py` 是机械门禁（解析 docs/05 §4 的表格，数量与集合必须逐条一致），所以加 7 个 `SKLAND_*` 码必须同时新增 `docs/05 §4.19` 并把常量从 114/113/18 改到 121/120/19。这条不是额外工作，是仓库既有的「表外的码一个都不许加」纪律。
4. **`potential = potentialRank + 1`（0 基 → 1 基）**先按 W0 待验证的口径实现，换算常量 `POTENTIAL_RANK_OFFSET` 单独导出、单测钉死。依据：真机库里 384 名已拥有干员的 `potential` 分布是 0–6、其中 184 名是 6（＝满潜），而森空岛给的是 0 基的 `potentialRank`（0–5）。W0 拿同账号 OperBox 读数交叉验证后定稿。
5. **模组类型不信本地资源**：仓库的 `resource/` 里**没有** `uniequip_table.json`（只有 `battle_data.json` 等），`GAMEDATA_FILES` 也不含它，所以 `equip[].id → X/Y/D/A` 只能取**同一次响应**里的 `equipmentInfoMap`（候选键 `type`/`moduleType`/`equipType`/`typeName`，前缀 `MOD_`/`UNIEQUIP_`/`EQUIP_` 归一）。映射不出来计一次 `dropped`，不猜类型。
6. **`skland.*` 三个 key 全进 `CONFIG_ONLY_SETTING_KEYS`**（不只 `credential_key`）：定时间隔与超时若只进 `SETTING_KEYS`，设置页会根据 schema 自动为它们生成表单控件，等于凭空做出规格 §3.2 明确不做的「前端定时开关」。代价是 `tests/test_vapid_settings.py` 那张钉死的集合要同步。

7. **不依赖未取证形状的两处加固**（W0 未完成时做掉的「从设计上消解」）：① 专精槽位从「按稀有度查 `SKILL_COUNT_BY_RARITY` 截槽」改成 **`len(skills)`**——稀有度要靠 `charInfoMap` 兜底，字段名没确认前按表截槽会把 6★ 的第 2、3 个技能专精写成 `0`（＝「确认没有专精」）；② 模组从「非空 `equip` 一律返回 `{}`」改成 **认不出任何类型时返回 `None`**——`equipmentInfoMap` 的类型键名同样没确认，认不出就落 `{}` 等于宣称「确认没装模组」。两条都补了正反用例（含「部分认出保留已知」与「超过三槽计数」）。

## 实施中修掉的问题（按发现顺序）

1. **加错误码会被文档门禁拦住**：`tests/domain/test_errors.py` 直接解析 docs/05 §4 的表格并断言 code 集合与数量一致（114 → 121 条、绑定位 113 → 120、表 18 → 19 张），所以「加 7 个 `SKLAND_*`」必须与 docs/05 §4.19 同一次改动落地。这正是仓库「表外的码一个都不许加」的设计，不是额外工作。
2. **新配置段不登记 `SETTING_KEYS` 会被整段忽略**（VAPID 的注释里写过这个坑）：`skland.*` 三个 key 必须进 `SETTING_KEYS`；同时因为 `credential_key` 敏感、另两项不做前端开关，三个都进 `CONFIG_ONLY_SETTING_KEYS`，连带更新 `tests/test_vapid_settings.py`（它钉了这张表的完整集合）与 `tests/test_settings.py` 的 `ENV_OVERRIDES` 钉表。
3. **新列会被 wire 形状门禁拦住**：`favor_percent` 进 `BOX_FIELDS` / `box_row_wire` 后，`tests/services/test_account_box_rules.py` 的整字典断言与 `tests/api/test_account_router.py` 的字段集合断言都会红——两处都已按新契约更新。
4. **`db/models.py` 的表数注释会过期**：文件头写「25 张业务表」，实际加两张后是 28 张（`SQLModel.metadata.tables` 是权威），已改。

## W0 剩余项与续跑方式（唯一未完成项）

W0 的十项里七项已由真机取证回答（`status` 100/101/102 三段、`scanUrl`、非法 `scanId` 400、
会话约 3 分钟失效、签名头的真实形状、`binding` 的真实形状、系统代理坑），并直接修掉了三个真 bug
（见上表与 §8.61）。**剩下四项需要一次成功的「扫码 → 确认 → 取数」**：

1. `potentialRank` 与本地 `potential` 的换算（`+1` 是否成立）——对照数据已备好：
   本机 OperBox 认出 **384** 名已拥有干员，`potential` 分布 0:3 / 1:71 / 2:49 / 3:36 / 4:19 / 5:22 / **6:184**；
2. `charInfoMap` 的真实字段名（只影响名称/星级/职业的补全，读不到就留空，不影响练度）；
3. `equipmentInfoMap` 里承载模组类型的键名（读不到时模组整列留 `NULL`＝不知道，不会写成「没有」）；
4. 一轮完整同步的真实 `chars` 数量级（验证 `SKLAND_MIN_CHARS = 200` 的余量）。

**续跑（一条命令，25 分钟窗口，过期自动换新码）：**

```bash
.venv/bin/python scripts/probe_skland_login.py watch --minutes 25 --session-timeout 150
# 屏幕上的 /tmp/m42_skland/qr.png 用森空岛 App 扫码 → 在 App 里点确认
# 成功后十项取证落在 /tmp/m42_skland/report.json
```

拿到 `report.json` 后按 §8.61 五、1 的清单逐条回填本节「真机复核」表与规格 §2.3/§2.5，
`potentialRank` 对照本机库的 `potential` 分布即可定稿；若发现与假设不符，改
`services/skland/mapping.py` 的 `POTENTIAL_RANK_OFFSET` 与 `module_type_of` 的候选键。

## 门禁实测（主 agent 亲自复跑，2026-10-12 11:20–11:35）

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `.venv/bin/python -m pytest -o addopts= -q --tb=no` | **2 failed / 3787 passed / 6 skipped（217 s）**；两条失败是 `tests/services/test_activity_index.py` 的真资源用例（M24 起的既有失败，docs/13 §8.40），与本轮无关。此前三次运行出现过 6–10 条 lifespan/资源并发抖动（`test_app_skeleton.py` / `test_app_log_wiring.py` / `test_device_lifecycle.py` / `tests/core/test_supervisor.py`，根因是 lifespan 的 `safe_reapply_overlay` 在挪 `resource/`，其它用例同时读它报 `FileNotFoundError: …/resource/lib/maa/Darwin/resource`），隔离复跑全绿，属既有环境抖动 |
| 抖动集隔离复跑 | `.venv/bin/python -m pytest tests/api/test_app_log_wiring.py tests/api/test_app_skeleton.py tests/api/test_device_lifecycle.py tests/core/test_supervisor.py -q` | **EXIT=0**（四个文件全绿）⇒ 上述失败与本轮改动无关 |
| 本轮新增用例 | `.venv/bin/python -m pytest tests/util/test_qr.py tests/services/test_skland_*.py tests/services/test_account_box_skland.py tests/api/test_skland_router.py tests/api/test_skland_e2e.py tests/db/test_skland_migration.py tests/agent/test_skland_tools.py -q` | 全绿：二维码 71 + 客户端 34 + 映射 20 + 同步/凭据 10 + 写库语义 7 + 路由 13 + **端到端 4** + 迁移 3 + Agent 工具 8 = **175 例** |
| 端到端集成（补） | `tests/api/test_skland_e2e.py` | 路由 → 服务 → **真 `SklandClient`**（签名 + 解析）→ 归一 → 写库 → 识别记录，只有 socket 是 `MockTransport`：扫码四态、`endfield` 角色被过滤、`charInfoMap` 兜底稀有度、数量护栏拦下 199 名、手动绑定 401 与成功路径各一条 |
| OpenAPI 快照 | `.venv/bin/python scripts/dump_openapi.py --check` | `OpenAPI snapshot is current: web/openapi.json` |
| 前端类型 | `cd web && pnpm gen:api:check` | `OpenAPI types are up to date.`（`api.d.ts` 里 training-scan 0 处、skland 7 处） |
| 前端类型检查 | `cd web && pnpm typecheck` | 退出码 0 |
| 前端单测 | `cd web && pnpm test --run` | `Test Files 74 passed (74)` / `Tests 777 passed (777)` |
| 前端构建 | `cd web && pnpm build` | 构建成功 + `[sw_harness] SERVICE WORKER OK` |
| 集成冒烟 | `.venv/bin/python scripts/frontend_smoke.py` | `SMOKE OK` |
| 账号冒烟 | `.venv/bin/python scripts/account_smoke.py` | `SMOKE OK`（含 OperBox 回调接线未受影响） |
| Agent 冒烟 | `.venv/bin/python scripts/agent_smoke.py` | `SMOKE OK` |
| 文案基线 | `.venv/bin/python scripts/copy_inventory.py --write-baseline && … --check` | 重设 45 项后 `通过：无回潮` |
| 残留 grep | `grep -rn "training-scan\|TrainingScan\|scan_operator_training\|apply_detail_scan\|probe_training_scan\|probe_full_scan" maa_api/ tests/ web/src/ scripts/` | 空（docs/12、docs/13 的历史条目除外） |

## 真机复核

| 项 | 结果 |
| --- | --- |
| 二维码生成（W0 探针，真机） | `scripts/probe_skland_login.py new` 跑通：`gen_scan/login` 200、返回 `scanId` 与 `scanUrl`、写出 37×37 模块的 `qr.png`（63 字节 payload、ECC M ⇒ 版本 5） |
| 会话寿命 | 10:54:10 生成 → 10:57:11 上游回 `status=102 已失效`（轮询 90 次全是 `100 未扫码`）⇒ TTL 取 180 秒有实测依据 |
| 组合签名客户端可用性 | 探针全程走本项目 `SklandClient`（不是 curl）：`gen_scan` 与 90 次 `scan_status` 全部 200，日志只记端点/状态码/耗时 |
| **`status=101`「已扫码待确认」（W0 最有价值的一条）** | 11:56:30Z 真机捕获 `{"msg":"已扫码待确认","status":101}`：用户扫码那一刻探针把它当失效换了新码 ⇒ **生产侧也会把它判成 `failed`**（原实现只认 0/100/102）。已修：服务把 101 归 `waiting` + 上游文案、会话不消费；探针继续等同一张码；前端显示「已扫码待确认」。见 §8.61 二、3b |
| **签名被上游接受、`binding` 401（W0 第二个发现）** | 11:59:03Z：用户扫码确认后 `token_by_scan_code` → `grant` → `generate_cred` → `auth/refresh` 全部 200（**说明签名算法本身对了**），紧接着 `binding` 回 **401**。根因：待签头的形状与社区实现不一致（漏 `dId`、`platform`/`vName` 写了非空值、键序也不同）。修法：`_signing_headers()` 固定四键顺序 `platform → timestamp → dId → vName`、默认空串；golden 签名值随之重算。 |
| **`binding` 响应形状（W0 第三个发现）** | 同轮按社区 schema 定稿：`data.list[].bindingList[]`（按 `appCode` 分组）、昵称键 `nickName`、等级在 `roles[].level`；旧假设（顶层 `bindingList` + `nickname` + 顶层 `level`）改为兜底路径并补了真形状用例。 |
| **系统代理坑（W0 第四个发现）** | 12:01 与 12:06 两轮窗口的 `gen_scan` 都是 5 秒超时后连接失败，而同时刻 `curl` 直连 200：httpx 默认 `trust_env=True` 走了本机 macOS 系统代理（当时不可达）。修法：客户端固定 `trust_env=False`，代理只认显式 `app.proxy`；探针加「上游偶发失败等 5 秒重来」的循环，两轮窗口不再被一次抖动掀掉。 |
| **扫码全链路（待用户执行）** | `grant` → `generate_cred` → `binding` → `player_info` 的真实响应形状**尚未取到**（超过 5 分钟轮询窗口，用户未在二维码有效期内扫码）。`potentialRank + 1`、`SKLAND_MIN_CHARS = 200`、`charInfoMap` 字段名、`equipmentInfoMap` 的类型键名仍是待验证假设，见 §8.61 五。用户扫码后把 `report.json` 的关键形状回填到本表 |
| 站点可达性 | `as.hypergryph.com` 125 ms、`zonai.skland.com` 141 ms（DNS 21 ms），无需代理 |

