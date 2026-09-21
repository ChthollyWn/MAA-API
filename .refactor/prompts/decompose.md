# 拆卡提示词模板（里程碑级）

用法：某个里程碑的卡片全部 done、且下一个里程碑还没有卡片时，**开一个全新会话**，
把下面整段复制进去，把 `<M>` 替换成里程碑号（如 `M5`）、`<标题>` 替换成它的中文标题。

拆卡是**一个里程碑一次**的独立动作，不要和实现卡混在同一个会话里做。

---

你是 MAA-API 重构的**拆卡者**。唯一任务：把里程碑 `<M>`「`<标题>`」拆成一组自包含任务卡。

仓库：`/Users/chtholly/Developer/WorkSpace/MAA-API`
分支：`refactor/v2`（已切好，不要切换、不要 push）

## 必读（按需读，不要通读 docs/ 全目录）

- `.refactor/PROTOCOL.md` —— 卡片协议与编排约定，必须严格遵守
- `.refactor/ENVIRONMENT.md` —— 已实测的环境事实，避免重复踩坑
- `docs/12-实施计划与里程碑.md` 中里程碑 `<M>` 的一节（权威工作项清单）
- `docs/13-决策记录.md` 中与本里程碑相关的 ADR（硬约束来源）
- `docs/` 中本里程碑指向的专题文档的对应章节
- 已完成的代码与契约：用 glob/grep 查看既有模块与类型定义，不要通读源码

## 产出

**一、卡片** `.refactor/tasks/<M>-01.json`、`<M>-02.json` …（序号连续，两位数字）：

```json
{
  "id": "<M>-01", "milestone": "<M>", "title": "一句话标题",
  "depends": [], "reads": ["docs/xx.md#章节锚点"], "constraints": ["硬约束原文"],
  "deliverables": ["path/to/file"], "verify": ["可执行命令"],
  "prompt_extra": "", "status": "pending", "attempts": 0, "commit": null, "blocked_reason": null
}
```

**二、里程碑级验收** `.refactor/milestones/<M>.json`：

```json
{
  "milestone": "<M>",
  "deliverable": "<docs/12 里该里程碑「可交付状态」的原文>",
  "acceptance": ["硬门禁：确定性、必须通过，不过就不打 tag 的命令"],
  "advisory": ["不阻塞：真机/网络等天然有抖动的验收命令"]
}
```

## 硬性要求

1. `reads` 用**精确章节锚点**，绝不允许「读 docs/」。一张卡注入 300–800 行，不要更多。
2. `verify` 必须可执行，且能在改动前失败、改动后通过。禁止主观验收。
3. `deliverables` 互不重叠；两卡产出同一文件说明切分错了。
4. `constraints` 只摘录本卡相关的决策，2–5 条。
5. 卡片数量按里程碑规模：S/M 级 4–8 张，L 级 8–14 张，XL 级 12–20 张。
6. `depends` 表达真实依赖。依赖前序里程碑已建立的契约（先读已有代码），不要重新发明。
7. 里程碑级验收的**硬门禁应当是「整个里程碑真的能跑起来」的证据**（端到端冒烟脚本、全量测试），
   不是单卡产物的存在性检查。真机与联网类放 `advisory`。

## 三条用血换来的质量纪律

1. **「先验证再实现」的事项必须拆成独立卡、排在实现卡之前。** docs 里点名的实测项
   （如 M7 的通道 B 合并语义）在错误假设上投 XL 工作量是最大的风险。
2. **`verify` 不得依赖未言明的实现约定。** 若一条 verify 只有在某种约定下才成立
   （例如路由前缀写在各 router 模块里而不是挂载处），必须把该约定同时写进该卡的
   `constraints`。否则 worker 会选另一种同样合法的约定，门禁永远不会通过。
   实测教训：M3-07/M3-08 的 verify 隐含要求「各 router 自带 prefix」，两个不同上下文的
   worker 都按「挂载时给前缀」实现，连续 blocked。
3. **每条 `verify` 都要亲手跑一次，确认它是「可失败的」而不是「坏掉的」。**
   在当前尚未实现该卡的状态下逐条执行，预期它以「断言不成立 / 文件不存在」这类
   **业务原因**失败；若它以 `AttributeError` / `TypeError` 这类**门禁自身写错**的原因
   失败，这条门禁就是坏的，必须改到能用为止。实测教训：M3-07/M3-08 用
   `{r.path for r in a.routes}` 取路径，而 FastAPI 0.141 下是 `_IncludedRouter`、没有
   `.path` —— 三条 worker 运行全被判死，却没人看得出是门禁自己的问题。

## 其他

- 每张卡做完后仓库必须仍处于可安装、可 import、测试可跑的状态。
- 不要发明文档里没有的工作项，也不要提前实现后续里程碑的内容。
- 完成后逐个用 `python3 -m json.tool` 确认 JSON 合法，然后
  `git add .refactor/tasks .refactor/milestones` 并 commit（message：`chore(cards): 拆解里程碑 <M>`）。
- 输出 200 字以内回执：卡片数量、依赖链要点、里程碑级验收命令、你判断的高风险卡。
