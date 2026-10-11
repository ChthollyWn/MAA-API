# M36 · 干员详情练度扫描（实施计划）

规格：`docs/superpowers/specs/2026-10-11-m36-training-scan.md`
分支：`m36-training-scan`（从 `refactor/v2` 起，基点 `f786193` = tag `v2-m35`）
完成 tag：`v2-m36`（完成后 `git merge --no-ff` 回 `refactor/v2`；不 push、不碰 dev）

## 交付项

| 波次 | 内容 | 写范围 | 依赖 |
| --- | --- | --- | --- |
| W1 | 只读自定义节点定义 + 真机标定（卡名 OCR、技能等级、专精 0/1/2/3、精英化、模组编号与 STAGE） | `maa_api/services/account/scan_tasks.py`、`scripts/probe_training_scan.py` | 设备可用 |
| W2 | 写回口径：详情扫描字段集、`apply_detail_scan`、识别新 kind | `maa_api/services/account/rules.py`、`maa_api/services/account/box.py`、`maa_api/db/repositories/account.py`、对应测试 | 无 |
| W3 | 扫描服务：名单、逐屏读卡名、点开目标卡、读详情、写回、状态机、取消、超时 | `maa_api/services/account/scan.py`、`tests/services/test_account_scan*.py` | W1、W2 |
| W4 | HTTP 端点 + `main.py` 装配 + OpenAPI 快照与类型 | `maa_api/api/routers/account.py`、`maa_api/main.py`、`tests/api/test_account_scan_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/14` | W3 |
| W5 | Agent 工具 + 知识库 playbook | `maa_api/agent/tools/account.py`、`maa_api/agent/knowledge/builtin/*`、`tests/agent/*` | W3 |
| W6 | 前端扫描卡片：按钮、进度、取消、跳过摘要 | `web/src/features/account/*`（不含 `web/openapi.json` 与 `web/src/types/api.d.ts`） | 无（按契约先行） |
| W7 | 真机验收 + 文档 + 台账 + 合并 | `docs/*`、`.superpowers/sdd/2026-10-11-m36-training-scan/progress.md` | 全部 |

## 顺序与依赖

1. W1 与 W2 并行起步；W1 的真机结论（ROI 精标、模组缺失样本）回写规格 §2.2。
2. W3 在 W2 落地后开始（调用 `apply_detail_scan`），接口按规格 §4.4 固定。
3. W4 在 W3 服务类成型后接入；同一天重跑 OpenAPI 快照与前端类型生成。
4. W5、W6 可与 W4 并行（各自文件不重叠）。
5. W7 收口：全部门禁 + 真机扫描 + 文档 + 台账 + 合并。

## 验收

见规格 §7。要点：四处生成物 current、后端全量除既有两条真资源红外无新增失败、前端 typecheck/vitest/build 绿、两条 smoke 末行 OK、**真机扫描与游戏界面一致**、负向对照（内核不可用/取消/超时）不写脏数据。

## 风险与回退

- 模组面板在「无模组」干员上形态不同：W1 必须拿到样本；拿不到就退化成「模组字段不写、保持 `NULL`」，其余字段照常。
- 卡名 OCR 解析不出 `char_id`：跳过并汇报，不影响其它干员。
- 内核版本升级导致模板漂移：表现为读不到 → 保持 `NULL`；不猜、不兜底。
- 真机不可用（用户不在/未登录）：W1 与 W7 的真机项顺延，代码与单测照常推进，合并前必须补齐真机证据；补不齐则不合并。
