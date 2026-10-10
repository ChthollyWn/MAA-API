# M33 · 账号识别接线与可信度

- 计划：`docs/superpowers/plans/2026-10-10-m33-account-capture.md`
- 规格：`docs/superpowers/specs/2026-10-10-m33-account-capture.md`
- 分支：`m33-account-capture`（自 `refactor/v2`，基点 `1a2d6af` = tag `v2-m32`），tag `v2-m33`
- 来源：用户报告（规格 §1）——① 日志「识别到 380 名干员」而用户实际 383；② 识别完成后账号数据没更新

## 任务状态

| 工作项 | 产物 | 状态 |
| --- | --- | --- |
| W1 接线 | `maa_api/services/account/callback_wiring.py`（新）、`maa_api/main.py:988-995`、`tests/services/test_account_capture_wiring.py`（6 用例）、`tests/api/test_account_capture_wiring.py`（装配钉子） | 完成（含负向对照） |
| W2 计数 | `maa_api/services/callback_translator.py:386-407`、`tests/services/test_callback_translator.py`（5 用例） | 完成（含变异对照） |
| W3 护栏 | `maa_api/services/account/capture.py`（G1/G2）、`maa_api/services/account/box.py`（`keep_owned`）、`tests/services/test_account_capture.py`（2）、`tests/services/test_account_box_service.py`（3） | 完成 |
| W4 测试与冒烟 | `tests/services/test_account_capture_replay.py`（真机载荷重放）、`scripts/account_smoke.py`（第 9 步 CALLBACK 接线） | 完成 |
| W5 文档 | `docs/13` §8.48、`docs/12` M33 小节、`docs/README.md`、`docs/11` §6.5、`docs/04:883`、`box.py` 注释与两个测试名 | 完成（含验收数字） |
| 验收 | 后端全量、四处生成物、文案基线、五个冒烟、`pnpm typecheck` | 完成 |

## 实测结论

1. **计数口径的真机事实**（`resource/maa-api/debug/asst.log`，2026-10-10 17:19–17:20 那次识别）：同一条 `OperBoxInfo` 的 `all_opers` **463 条**、其中 `own: true` **383 条**（与用户说的 383 一致），`own_opers` 只有 **380 条**；差的 3 名是 `char_002_amiya`（阿米娅）、`char_1037_amiya3`（阿米娅·近卫）、`char_512_aprot`（暮落）——已拥有但不带练度详情。反方向不存在（`own_opers ⊆ all_opers` 的 `own=true` 子集）。所以所有权只认 `all_opers[].own`，`own_opers` 只补 `elite`/`level`/`potential`。
2. **接线缺失（用户报告的第二个问题）**：`AccountCaptureService` 自 M27 起构造好并放进 `app.state`，但**没有任何生产调用方**——`main.py` 里没有 `on("CALLBACK", ...)`，识别回调从来没走到落库，账号页因此永远不会更新。单测全绿而线上从不触发，与 M31 记下的「能力建好但没接线」是同一类缺陷。
3. **护栏强度＝用户裁决 A「识别只加不减」**：识别说「未拥有」而行里已是已拥有时保留 `True` 并记 warning（名单最多 5 个），保留值记进 `recognized_values`/`field_sources`；真要取消拥有走 `update_operator_box` 或页面。空识别（`all_opers` 非空但已拥有为 0）整次跳过、连识别记录都不写（`skipped="no_owned_operators"`），避免页面显示一次假的「识别到 0 名」。备选 B（阈值判断整次跳过）与 C（不改语义）未采用。
4. **回流验证靠真实载荷**：重放用例从 `debug/asst.log` 抽用户那次识别的原始 `OperBoxInfo`，钉住 383 / 3 名无详情 / 13 名残载荷（`kept_count=370`、表仍是 383）/ 0 名空载荷四种情形；没有日志时 `skip`。
5. **回调只做调度**：内核回调在同步上下文，`handle_callback` 是协程——用 `asyncio.get_running_loop().create_task` + task set 防 GC + `_finish` 记 warning，失败绝不影响流水线；没有事件循环时只记 warning 不落库。只按 `Message.SubTaskExtraInfo` 粗筛，`what`/`done` 交给采集服务的既有分发。

## 验收结果

主 agent 在所有写者停止后独占复跑（2026-10-10）。

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `.venv/bin/python -m pytest -o addopts= -q` | **2 failed / 3586 passed / 5 skipped**（198.55s）；两条失败是 M24 起已知的真资源用例 `tests/services/test_activity_index.py`；M32 基线 3568 passed，**+18 = 本轮新增 18 个用例**（翻译器 5、采集服务 2、账号服务 3、服务层接线 6、真机重放 1、装配钉子 1） |
| 计数负向对照 | 临时把 `_operbox_count` 换回旧实现 | 计数用例 **3 failed / 2 passed**（383 那条报 380）；换回后全绿 |
| 装配负向对照 | 把 `main.py:990-995` 的 `install_account_capture(...)` 换成 `pass` | `tests/api/test_account_capture_wiring.py` **FAILED**；还原后 `cmp` 逐字节一致（`grep -c install_account_capture` = 2） |
| 生成物 | `dump_openapi.py --check`、`generate_api_guide.py --check`、`generate_task_types_fixture.py --check`、`cd web && pnpm gen:api:check` | 四处全 `exit=0` |
| 文案基线 | `copy_inventory.py --check` → `--write-baseline` → `--check` | 先报文档正文与注释新增文案（`docs正文/bold` 2410 → 2458、`docs正文/em-dash` 476 → 495、`注释/em-dash` 97 → 100 等），重设为 **45 项**后「通过：无回潮，0 项较基线下降」（`docs/copy-audit/lint-baseline.json` 10 行更新） |
| 冒烟 | `agent_smoke.py`、`frontend_smoke.py`、`account_smoke.py`、`copilot_research_smoke.py`、`prts_smoke.py --m28` | 五个末行均 `SMOKE OK`（含 `account_smoke.py` 新增第 9 步「CALLBACK 接线：识别记录 operbox 带 pipeline=pipeline-smoke｜task=task-smoke｜渠道=Bilibili｜陈 own=True」） |
| 前端 | `pnpm typecheck` | 退出码 0（本轮前端零改动） |

## 遗留

1. **真机复跑待用户执行**：跑一条 `干员识别`，账号页应出现 383 名已拥有、日志那句也按 383 计数（本机不连设备，重放用例是间接证据）。
2. **`sanity_observation` / `stage_drop` 仍无写入方**（迁移 0007 建表至今为空），不在本轮范围。
3. **3 名无详情干员的 `elite`/`level`/`potential` 落库为 0/0/0**（非空列默认值，M27 既有表述）——是「有这名干员但没练度详情」的正常结果，若要区分「未知」与「0」需另立里程碑。
