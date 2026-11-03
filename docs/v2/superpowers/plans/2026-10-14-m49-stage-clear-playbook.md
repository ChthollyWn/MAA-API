# M49 攻克关卡的固定决策链（知识库条目）（计划）

- 规格：[2026-10-14-m49-stage-clear-playbook.md](../specs/2026-10-14-m49-stage-clear-playbook.md)
- 分支：`m49-stage-clear-playbook`（基点 `refactor/v2` = `b8948dd`）
- 完成后：`git merge --no-ff` 回 `refactor/v2`、打 tag `v2-m49`，台账复制到 `docs/superpowers/ledgers/2026-10-14-m49-stage-clear-playbook.md`

来源：用户 2026-10-14 问「让 agent 攻克一个关卡，该采用怎样的方式成功率更高？」→ 答复后用户要求把结论固化成知识条目（「可以」）。W1–W3 顺序依赖，W4 收尾。

## W1 条目落地

写范围：`maa_api/agent/knowledge/builtin/playbook-stage-clear.md`（新建）

- frontmatter：`id=playbook-stage-clear`、`kind=playbook`、`title=攻克指定关卡`（≤ 32 字）、`when_to_use`（≤ 120 字）。
- 正文六节：原则（让内核去打 + 三条硬约束）／1 动手前先看现状／2 先搜现成作业（可行性三档分流）／3 搜不到就自己写／4 执行（含执行前提与 `not_started` 不计次）／5 失败之后（含授权与汇总）／原子操作的位置。
- 硬约束：不写具体游戏数值（指回工具与既有条目）；只引用真实存在的工具名。
- 验收：`.venv/bin/python -m pytest -o addopts="" -q tests/agent/test_knowledge_no_numbers.py tests/services/test_knowledge_page_pointers.py`。

## W2 登记点同步（不改就红）

写范围：`tests/services/test_knowledge_page_pointers.py`、`tests/agent/test_knowledge_no_numbers.py`、`tests/agent/test_knowledge_service.py`、`tests/api/test_agent_knowledge_router.py`、本规格文件

- 条目数 33 → 34（两处计数注释 + `EXPECTED_ENTRY_COUNT`）。
- 三份注册表副本（`BUILTIN_IDS` ×2、`BUILTIN_LIST_ORDER` ×2）各加一条：`playbook-sanity-planning` 之后（同 kind 内 id 升序）。
- `test_builtin_files_match_the_spec_registry_and_frontmatter` 要求「每个内置条目都有一份规格原文」，因此规格 §4.6 必须给出该条目的 markdown 原文块，并把 M49 规格加进 `_spec_blocks()` 的路径元组。
- 验收：`.venv/bin/python -m pytest -o addopts="" -q tests/agent/test_knowledge_service.py tests/agent/test_knowledge_no_numbers.py tests/services/test_knowledge_page_pointers.py tests/api/test_agent_knowledge_router.py tests/agent/test_knowledge_convergence.py`。

## W3 文档口径

写范围：`docs/11-Agent模块设计.md`、`docs/12-实施计划与里程碑.md`

- docs/11 两处条目数口径 33 → 34（§6.1 的「只讲怎么做，不带游戏数值」段与 §6.4 的条目清单段），并点名 M49 这条。
- docs/12 在 M48 小节之后追加 M49 小节（形制照 M47/M48）。
- 验收：`grep -n "34 条" docs/11-Agent模块设计.md` 命中；`grep -n "M49" docs/12-实施计划与里程碑.md` 命中。

## W4 全量验收与合并（主 agent 亲自跑）

- `.venv/bin/python -m pytest -o addopts="" -q`（只剩既有两条真资源失败）。
- `.venv/bin/python -m pytest -o addopts="" -q tests/services/test_knowledge_page_pointers.py tests/agent/test_knowledge_no_numbers.py`。
- `scripts/dump_openapi.py --check` 不变；`scripts/copy_inventory.py --check` 棘轮不破（新条目正文进「用户可见」桶）。
- 功能性验证：`read_knowledge("playbook-stage-clear")` 能读到全文，索引里出现 `playbook-stage-clear｜攻克指定关卡`。
- 台账归档、`git merge --no-ff` 回 `refactor/v2`、tag `v2-m49`。
