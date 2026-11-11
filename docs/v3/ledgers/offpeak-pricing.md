# 模型峰谷计价（台账）

- 分支：`offpeak-pricing`；基点：`dev` = `31c1fa7`；合并：`2fd28c8`（`--no-ff`）
- spec：[offpeak-pricing](../specs/offpeak-pricing.md)
- 计划：[offpeak-pricing](../plans/offpeak-pricing.md)

## 做了什么

- 数据：`model_profile` 加三个可空列 `price_input_offpeak`/`price_cached_input_offpeak`/`price_output_offpeak`（`NULL` 回退高峰价），迁移 `0044_offpeak_prices`（`down_revision=0043_capability_insights`，可逆）；业务表仍 61。
- 配置：`LLMSettings` 加 6 个峰谷价与 `llm.peak_holidays` 共 7 个键，`SETTING_KEYS` 144 → **151**（config-only 仍 13），`_FIELDS` 渲染在 LLM 分组，`config.template.yaml` 同步注释。
- 档案：`ResolvedProfile`/`ModelProfileView`/`ModelProfileCreate`/`ModelProfileUpdate`/`_view_from_row`/`_resolved_from_row`/`create`/`update` 同步；`default` 档案六个价格改由 `llm.*` 读取（六个值 0 均视作未配置返回 `null`：峰价 input/output 按 0、cached 回退同段 input；谷价逐项回退峰价），仍只读、不入表；自定义档案三列 `null` 清空、不传保持原值。
- 成本引擎：`PEAK_TIMEZONE`（`Asia/Shanghai`）、`PEAK_WINDOWS`（工作日 `[09:00,12:00)`、`[14:00,18:00)`）、`parse_peak_holidays`、`is_peak_time`；`compute_cost_micros` 加 `at`/`holidays`，空闲项逐项回退高峰价，`at=None` 保持旧语义（全按高峰价）；新增 `compute_cost_micros_at` 作为“当前配置”入口；`CostLedger.__init__` 注入 `settings_provider`、`record` 加 `at`；trace 的 `llm.chat` span 成本与账本共用 `compute_cost_micros_at`（span 用结束时刻，账本用记账时刻）。
- 前端：`ProfileEditorSheet` 在三个高峰价后加「空闲时段价格」三个输入（label 与「留空表示回退高峰价」说明按 spec），`initialForm`/`commonFields`/创建与更新请求体同步；`models.test.tsx` 覆盖回填与提交。
- 契约文档：03 §2 加 0044 行并把 M91–M98 顺延为 0045–0049、§4.1 加三列与「高峰时段价格」；04 §2.1 加六个价格字段、§6 加 7 行与 151/13 计数；10 §4.1/§4.5 补峰谷规则、DeepSeek 官方价表（2026-11-11 抓取）、trace/账本同口径与历史不回填；11 §9 补档案编辑器三输入与 LLM 分组 7 项；12 §2 顺延迁移号并注明补丁不占 M 号；13 新增 §8.187；14 的 POST 请求字段枚举补三字段；15 由 `scripts/generate_api_guide.py` 重新生成（无 diff）；README 未改（无维护性补丁索引位）。
- OpenAPI 与 `web/src/types/api.d.ts` 由前序任务生成，本次重生成后 `--check` 均通过（无 diff）。

## 验证证据

| 命令 | 结果 |
|---|---|
| 后端全量 pytest（安静环境复跑 `/tmp/offpeak-t6-pytest2.log`；`/tmp/offpeak-t6-pytest.log` 为首跑） | `1`；仅 3 条 Task 1 登记的环境基线失败（`test_activity_index` 2 条、`test_stage_layout` 1 条），无其他失败、无 ERROR；`test_incidents` flake 未出现。`pytest.ini` 的 addopts 已含 `-q`，命令行再带 `-q` 变 `-qq`，日志没有 passed 计数 |
| `dump_openapi.py` + `--check`（`/tmp/offpeak-t6-openapi.log`） | 两个 `0`；`web/openapi.json` 无 diff |
| `pnpm gen:api:check`（`/tmp/offpeak-t6-genapi.log`） | `0`；`OpenAPI types are up to date` |
| `pnpm install --frozen-lockfile`（`/tmp/offpeak-t6-pnpm-install.log`） | `0` |
| `pnpm typecheck`（`/tmp/offpeak-t6-typecheck.log`） | `0` |
| `pnpm test --run`（`/tmp/offpeak-t6-web-test.log`） | `0`；125 files / 1067 tests passed |
| `pnpm build`（`/tmp/offpeak-t6-web-build.log`） | `0`；SW harness `SERVICE WORKER OK` |
| `scripts/frontend_smoke.py`（`/tmp/offpeak-t6-frontend-smoke.log`） | `0`，末行 `SMOKE OK` |
| `scripts/agent_smoke.py`（`/tmp/offpeak-t6-agent-smoke.log`） | `0`，末行 `SMOKE OK`；首次在全量 pytest 并行时失败（诊断 Worker 10 秒内没等到 Fake LLM 往返），静默重跑通过 |
| `agent_eval --suite regression --mode replay`（`/tmp/offpeak-t6-agent-eval.log`） | `0`，`5/5`（100.0%），`EVAL OK`，结果 `20261111-080553-regression-replay-71ee` |
| `generate_api_guide.py` + `--check`（`/tmp/offpeak-t6-apiguide.log`、`/tmp/offpeak-t6-apiguide-check.log`） | 两个 `0`；15 无 diff（错误码与路由未变） |
| `scripts/check_doc_links.py`（`/tmp/offpeak-t6-doclinks.log`） | `0`，末行 `BROKEN 0`（建台账前曾报 `BROKEN 1`，指向尚未创建的 `ledgers/offpeak-pricing.md`） |
| `git diff --check` | `0` |
| 手工验收（开发库 + 真实 `create_app()` TestClient，`/tmp/offpeak-t6-manual.log`） | 见下 |

### 手工验收（2026-11-11，不发起真实付费调用）

命令（`/tmp/offpeak_t6_manual.py`，真实 `create_app()` + 开发库 `resource/maa_api.db`；用环境变量注入 DeepSeek 官方峰谷价）：

```bash
MAA_LLM_PRICE_INPUT=2 MAA_LLM_PRICE_CACHED_INPUT=0.04 MAA_LLM_PRICE_OUTPUT=8 \
MAA_LLM_PRICE_INPUT_OFFPEAK=1 MAA_LLM_PRICE_CACHED_INPUT_OFFPEAK=0.02 \
MAA_LLM_PRICE_OUTPUT_OFFPEAK=4 .venv/bin/python /tmp/offpeak_t6_manual.py
```

输出摘要（`/tmp/offpeak-t6-manual.log`）：

- `GET /api/agent/model-profiles` → 200；`default.readonly=True`；`default` 的六个价格字段为 `2.0 / 0.04 / 8.0 / 1.0 / 0.02 / 4.0`，与同进程 `settings.llm` 逐字段一致（证明来自 `llm.*`，不是表里的行）。
- 固定 `Usage(prompt=1,000,000, cached=100,000, completion=100,000)`：北京周三 09:00 高峰成本 `2,604,000` micros；北京周三 12:00 空闲成本 `1,302,000`；同一 09:00 时刻注入 `peak_holidays=2026-11-11` 后按空闲 `1,302,000`。
- 未发送模型消息、未调用真实 LLM；运行后开发库 `cost_ledger` 仍 219 行、`agent_run` 仍 86 行。

## Rulings I made

- 04 §6 原表附近没有配置键计数句；按任务要求在表前补一句「当前 151 个、config-only 13 个」。
- 03 §2 表下原注把 M99 的 0043 与 M91–M98 顺延写在一起；本补丁占用 0044 后改为分别记录 0043（M99）与 0044（本补丁），避免读数歧义。
- 14 的 POST `/api/agent/model-profiles` 行列出了请求体字段；虽然 spec §9 未列 14，仍补三个 `*_offpeak`（不改路由/计数/错误码，沿用「现行接口清单」的活文档口径）。
- 手工验收用环境变量 `MAA_LLM_PRICE_*` 注入 DeepSeek 官方峰谷价，不修改开发 `config.yaml`，也不发起真实模型调用；`default` 的返回值与同进程 `settings.llm` 逐字段比对。
- `ResolvedProfile` 的三个新字段用 `kw_only=True`（dataclass 默认值字段不能后跟无默认值字段，且不改写范围外 15 个构造方）；`_default_view` 复用 `_synthesize_default` 的结果。
- 迁移三列在 SQLite 的物理列序位于表尾（原生 `ADD COLUMN` 固有行为），模型字段序仍在 `price_output` 后；迁移测试只断言列定义与既有列不变，不断言物理列序。
- 03 §1 的「当前 head」顺带从过时的 `0040_question_expiry_historical` 更新为 `0044_offpeak_prices`（迁移文档同步的一部分）。
- 15 重新生成后没有 diff，因此提交不带 15 的改动；`web/openapi.json` 与 `web/src/types/api.d.ts` 同理。
- 最终复审 fix wave（2026-11-11）：六个 `llm.price_*` 的 0 统一按未配置折成 `None`（缓存命中价回退同一时段输入价，不再按 0 计），补齐设置说明与 `price_cached_input` 的 `NULL` 语义，并让 `compute_cost_micros_at` 的 settings 入口延迟解析以便测试节假日路径；细节与验证证据见 SDD 台账 `task-6-report.md` 的「Final-review fix wave」。

## 偏离（已记 13 §8.187）

- 无最终实现偏离。复审中发现 trace `llm.chat` span 未走峰谷取价、会破坏 10 §4.1 等式，控制器先扩 spec/plan（`05ec089`）再修复（`4ec18b3`）；最终实现与扩展后的 spec 一致。
- 迁移号：本补丁先于 P5 执行，占用 `0044`；03 §2 与 12 §2 的 M91–M98 建议号由 `0044`–`0048` 顺延为 `0045`–`0049`。
- 计数：配置键 144 → 151（config-only 13 不变）；业务表 61、HTTP 端点 200（agent 97）、错误码 174、枚举/工具/评测场景 31 均不变。

## 遗留

- 历史成本不回填：开发库既有 219 行 `cost_ledger`（86 次运行）仍为 0；不做一次性补算，新调用才按峰谷价记实际成本。
- 节假日手工维护：`llm.peak_holidays` 默认空，未填的法定节假日按高峰价计（只会高估），需要每年人工更新。
- 规则写死 DeepSeek：北京时间工作日两个窗口；换 provider 或官方改规则需新补丁，其他档案可把空闲价留空退化为全天高峰价。
- 未做真实付费模型调用：`default` 档案的 6 个价格字段与峰谷成本只用 TestClient + 固定 `Usage` 验证，未验证真实调用产生的新账本行。
- 未做真机/真实浏览器手工验收；前端由 jsdom 测试、`pnpm build` 与 `frontend_smoke` 覆盖。
- 前序任务延后的 Minor：迁移测试未断言 `dflt_value` 为 `NULL`；一处测试内局部导入；空闲价 hint 未与输入 `aria` 关联、测试未断言标题/hint；mock PUT 无 404 分支。
- 首跑噪声（安静环境复跑未复现）：与前端 build/smoke 并行时，`tests/api/test_orchestrator_e2e.py::test_second_message_during_orchestrator_run_streams_both_turns` 在 teardown 报 `sqlite3.OperationalError: database is locked`（ERROR，非断言失败）；该单测复跑通过（8.19s）。同一次首跑因台账尚未创建出现 2 条 `test_doc_links` 失败，建台账后消失。
- `scripts/agent_smoke.py` 首次与全量 pytest 并行时，诊断 Worker 的 10 秒轮询窗口内没有 Fake LLM 往返；安静重跑 `SMOKE OK`。


## 合并后复验（dev，2026-11-11 17:2x）

- 合并提交 `2fd28c8`（`--no-ff`），分支 `offpeak-pricing` 已删；`dev` 领先 `origin/dev` 12 个提交；未 push。
- 合并树与分支 HEAD `25fa7e3` 相同（merge commit 只多合并信息），分支上控制器最终验收：全量 pytest exit 1、仅 3 条基线环境失败；前端 install/typecheck/test/build 全 0（125 files/1067 tests）；`frontend_smoke`/`agent_smoke` `SMOKE OK`；regression replay 5/5 `EVAL OK`；OpenAPI/`gen:api` check 0；`generate_api_guide` gen/check 0；doc links `BROKEN 0`；`git diff --check` 0。
- 合并后 dev 复跑：`dump_openapi --check` 0、`pnpm gen:api:check` 0、`tests/domain/test_route_inventory.py`+`tests/domain/test_errors.py`、`check_doc_links` 0（见 `/tmp/offpeak-postmerge-*.log`）。
- 最终整支复审 Verdict **With fixes**（无 Critical/Important 功能缺陷）；final fix wave `d85b6ab` + 控制器 `25fa7e3` 全部落地并被 scoped re-review 判 ADDRESSED；剩余 Minor 见「遗留」。
- 配置字段 `default` 六价格：本机 `config.yaml` 已填 DeepSeek 官方峰谷价（峰值 input 2 / cached 0.04 / output 8，谷值 1 / 0.02 / 4）并重启服务验证（launchd 旧 pid 73525 → 新 pid 27468）：`GET /api/agent/model-profiles` 的 `default` 返回 6 个价格（2 / 0.04 / 8；1 / 0.02 / 4，CNY），`GET /api/settings` 的 7 个键 `source=yaml`；固定用量 100 万 prompt（缓存 10 万）+ 10 万 completion 的验证值为高峰 2,604,000 micros、空闲 1,302,000 micros。该动作不属于仓库改动。

## SDD 执行明细（摘自 progress.md）

- Task 1: complete（commits `301238e`）— spec/plan 入库；基线快检通过，全量 pytest 基线待补记
- Task 2: complete（commits `301238e`..`de05205`，review Approved，0 Critical/Important）。Minor 延后：迁移测试未断言 pragma dflt_value 为 NULL。开发库已迁到 0044（备份 /tmp/maa_api.db.offpeak-t2.bak）；模型物理列序在表尾（SQLite 固有）。
- Task 3: complete（commits `de05205`..`1a6ba95`，review Approved，0 Critical/Important）。Minor 延后：一处测试内局部导入。实现细节裁决：`ResolvedProfile` 三字段用 `kw_only=True`（不改写范围外 15 个构造），`_default_view` 复用 `_synthesize_default`；配置键 151/13。
- Task 4: 实现 + fix round 1（commits `1a6ba95`..`4ec18b3`）；review 指出 Important：trace `llm.chat` span 成本未走峰谷（docs/10 §4.1 等式会破），控制器判定为计划级缺口，扩展 spec/plan（`05ec089`）并派修复；`4ec18b3` 新增 `compute_cost_micros_at`，账本与 TraceMiddleware 共用，补了 trace 空闲时段用例与账本注入测试。scoped re-review 判定 ADDRESSED、无新 Critical/Important（commits `1a6ba95`..`4ec18b3` 含控制器 spec 补充 `05ec089` 与残句修正）。
- Task 4: complete（review Approved + fix round 1/5 ADDRESSED，0 open）。Minor 延后：test_ledger 环境隔离已修、warning 放大未缓存、insert 顺序依赖已加 order_by。
- Task 5: complete（commits `f6eedbf`..`d4de61b`，review Approved，0 Critical/Important）。Minor 延后：hint 未与输入 aria 关联、测试未断言标题/hint、mock PUT 无 404 分支。
- Task 6: complete（commits `d4de61b`..`ae954d3`，docs/台账/验收）；最终整支复审 Verdict **With fixes**（无 Critical/Important 功能缺陷；2 条 Important：docs 的 cached NULL 语义写错、六个价格设置 0 语义静默少计）。final fix wave `d85b6ab`（默认档案六项 0→None、设置说明、docs 校正、trace 节假日测试、迁移 dflt_value、16/specs README 随车）；scoped re-review 全部 ADDRESSED，剩 1 条 Minor（空闲价设置说明与峰价共用文案不准确）由控制器修 `25fa7e3`。Lead 最终验收（HEAD `25fa7e3`）：全量 pytest 仅 3 条基线环境失败；OpenAPI/typecheck/test/build/smoke/eval/doc links 全过（细节见台账）。
