# knowledge 夹具

## m30_disposition.json（M30 追加，2026-10-10）

- **内容**：`maa_api/agent/knowledge/builtin/` 32 条内置条目逐条处置表的**快照**，每条只留
  `source`（原报告文件名）、`id`（条目 id）与 `numbers_to_remove`（该条目里按规格 §10.1
  必须删掉的真实数值片段）三列，共 32 条 / 369 个片段。
- **来源**：M30 的 T1 复核报告 `.superpowers/sdd/2026-10-10-m30-knowledge-convergence/`
  下的 `disposition-a.json`（A–L 15 条）与 `disposition-b.json`（M–Z 17 条）；报告完整版
  与台账同源，工作区目录是 gitignore 的临时目录，所以把两列快照复制进仓库。
- **抓取方式**：由主 agent 用 `.venv/bin/python` 从上述两份 JSON 里取
  `{source, id, numbers_to_remove}` 三列后写出，未手工改动内容。
- **用途**：`tests/agent/test_knowledge_no_numbers.py` 的反向用例（`test_reverse_samples_are_caught`
  与 `test_reverse_fragments_are_not_whitelisted`）从这里取真实数值片段，证明「无数值」扫描
  不是永真。片段是**当时**条目里的真实文字，条目后续被改写也不会让这些片段失效——它们
  只用来验证扫描器能命中，不要求现在还出现在条目里。
- **维护**：这是 M30 的一次性快照，不随条目演化更新；若要重新采样，改夹具前先确认新片段
  在扫描器判据下仍应命中。
