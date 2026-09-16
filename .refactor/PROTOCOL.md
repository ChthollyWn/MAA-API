# 重构编排协议

本目录是 M0–M15 重构的**执行台账**。它是跨会话的唯一记忆：每个 worker 都是全新上下文，只靠一张任务卡自包含地完成工作。

## 为什么这样设计

一个会话跑完 16 个里程碑必然超窗，压缩会丢掉 `docs/13-决策记录.md` 里那些"为什么"——于是后期会做出与既定决策冲突的改动且自己发现不了。所以：

- **记忆搬到磁盘**：卡片 + 台账 + git 历史，而不是对话上下文。
- **质量搬到门禁**：每卡有可执行的 verify 命令，由编排器（不是 worker）执行，worker 无法自证。
- **执行单位是卡片**：一张卡 = 一个全新 `dsh --profile headless` 进程 = 一个干净上下文，做完即弃。

## 目录

```
.refactor/
├── PROTOCOL.md          本文件
├── tasks/<id>.yaml      任务卡（提交进 git）
├── PROGRESS.md          编排器自动生成的人读总览
├── PAUSE                存在则编排器跳过本轮（急停开关）
├── state.json           编排器运行状态（gitignored）
└── logs/<id>.log        worker 完整输出（gitignored）
```

## 任务卡格式

卡片是 **JSON**（`.refactor/tasks/<id>.json`），不是 YAML —— 编排器是零依赖纯 JS，只有 `JSON.parse` 可用。少一个解析器就少一类无人值守下的静默故障。

```json
{
  "id": "M0-01",
  "milestone": "M0",
  "title": "一句话标题",
  "depends": [],
  "reads": ["docs/xx.md#章节锚点"],
  "constraints": ["从 docs/13 摘录的硬约束原文"],
  "deliverables": ["path/to/file"],
  "verify": ["python -c \"import pydantic; assert pydantic.VERSION.startswith('2')\""],
  "prompt_extra": "",
  "status": "pending",
  "attempts": 0,
  "commit": null,
  "blocked_reason": null
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 唯一，`M<里程碑号>-<两位序号>` |
| `milestone` | string | 如 `M0` |
| `depends` | string[] | 前置卡 id；全部 `done` 后本卡才就绪 |
| `reads` | string[] | worker 只读这些文档锚点 —— 上下文预算的阀门 |
| `constraints` | string[] | 从 docs/13 摘录的硬约束，防止 worker 违反既定决策 |
| `deliverables` | string[] | 预期产出文件（相对仓库根），用于冲突检测与 `git add` |
| `verify` | string[] | 编排器在 worker 退出后逐条执行的 shell 命令，必须全部 exit 0 |
| `prompt_extra` | string | 可选，写给 worker 的额外指令 |
| `status` | string | `pending` \| `running` \| `done` \| `blocked` |
| `attempts` | number | 已尝试次数，超上限转 `blocked` |
| `commit` | string\|null | 完成时的 commit sha |
| `blocked_reason` | string\|null | 阻塞原因 |

### 字段纪律

- `reads` 必须是**精确章节**，不是整个文档。一张卡平均注入 300–800 行，绝不允许"读 docs/"。
- `verify` 必须是**可执行命令**，退出码即结论。禁止"看起来对"这类主观验收。
- `constraints` 只摘录与本卡相关的决策，不要整篇复制 13-决策记录。
- `deliverables` 必须互不重叠；两卡产出同一文件说明切分有误。

## 编排器每轮算法

1. 读 `PAUSE` 存在则跳过。
2. 载入全部卡片，算就绪集合：`status == pending` 且所有 `depends` 均为 `done`。
3. 若无就绪卡：全部 done → 里程碑收尾（打 tag）；有 blocked → 停下报告。
4. 取里程碑序最小的就绪卡，置 `running`，写 `state.json`。
5. spawn worker（全新 headless 进程，`cwd` = 仓库根）。
6. worker 退出后，**编排器**逐条跑 `verify`；并要求 HEAD 产生新 commit。
7. 全绿 → `done` + 记录 commit；否则 `attempts++`，未超上限则退回 `pending` 重试，超限则 `blocked`。
8. 更新 `PROGRESS.md`、追加日志、提交台账变更。
9. 睡到下一轮。

## worker 契约

worker 的提示词由编排器从卡片生成，固定包含：

- 只做这一张卡，做完即止
- 只读 `reads` 列出的锚点，不读其他文档
- 遵守 `constraints`
- 结束后自行运行 `verify` 命令确认通过
- `git add` **只加自己的 deliverables** 后 commit，不 push、不切分支
- 不修改 `.refactor/` 下任何文件（台账归编排器）
- 输出 200 字以内回执

## 里程碑 tag

同一里程碑全部卡 done 后，编排器打 `v2-m<N>` tag。任何阶段都能回到上一个 tag；`dev` 分支始终保持重构前可用状态。
