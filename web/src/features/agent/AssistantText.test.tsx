import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { AssistantText } from './MessageViews'

afterEach(cleanup)

/**
 * 这条回复是从真实运行的 Agent 抓下来的原文（deepseek-flash，含二级标题、无序列表、
 * 加粗、行内代码、bash 代码块与链接），用来锁住「接入后确实渲染成元素」：
 * 单元测试只覆盖解析器，这里覆盖 `AssistantText` → `Markdown` 的整条渲染链。
 */
const LIVE_REPLY = [
  '## 回答说明',
  '',
  '本次未调用任何工具，也未对设备或流水线执行任何操作，因此下面的内容仅是一份 Markdown 排版示例。',
  '',
  '- 第一个要点：本回复没有提交队列、没有重启内核、没有进行截图。',
  '- 第二个要点：`resolve_stage`、`submit_pipeline` 等工具均未被调用。',
  '- 第三个要点：如需真实执行任务，请另行告知具体需求。',
  '',
  '上面的列表用于演示格式。**注意：我无法在此处声称任何设备操作成功**，一切以工具返回结果为准。',
  '',
  '行内代码示例：`MaaCore` 与设备状态需通过工具查询确认。',
  '',
  '```bash',
  '# 仅作 Markdown 格式演示，未在本次会话中运行',
  'adb devices',
  'echo "示例输出"',
  '```',
  '',
  '参考链接：[https://example.com](https://example.com)',
].join('\n')

describe('AssistantText renders an agent reply as markdown', () => {
  it('turns the live reply into elements instead of literal markers', () => {
    const { container } = render(<AssistantText text={LIVE_REPLY} messageId={42} />)

    // 结构：标题、列表、代码块都成了真实元素。
    expect(screen.getByRole('heading', { level: 2, name: '回答说明' })).toBeInTheDocument()
    expect(container.querySelectorAll('li')).toHaveLength(3)
    expect(container.querySelector('pre')?.textContent).toBe(
      '# 仅作 Markdown 格式演示，未在本次会话中运行\nadb devices\necho "示例输出"',
    )

    // 行内标记：加粗与行内代码各有元素，且正文里不再残留标记符号。
    expect(screen.getByText(/我无法在此处声称任何设备操作成功/).tagName).toBe('STRONG')
    expect(screen.getByText('resolve_stage').tagName).toBe('CODE')
    expect(container.textContent).not.toContain('**')
    expect(container.textContent).not.toContain('##')

    // 链接：可点击且带安全属性。
    const link = screen.getByRole('link', { name: 'https://example.com' })
    expect(link).toHaveAttribute('href', 'https://example.com')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noreferrer'))

    // 无障碍标签与 message id 仍然保留（原实现的行为不能丢）。
    expect(screen.getByText('Agent：')).toBeInTheDocument()
    expect(container.firstElementChild).toHaveAttribute('data-message-id', '42')
  })

  it('renders a half-streamed reply without losing the text', () => {
    // 流式中间态：斜体标记与代码块都还没闭合。
    const { container } = render(<AssistantText text={'正在**处理\n\n```bash\nadb devi'} />)

    expect(container.textContent).toContain('正在**处理')
    expect(container.querySelector('pre')?.textContent).toBe('adb devi')
  })
})

/**
 * 第二条真实回复（deepseek-flash 生成，只截取表格部分）：Agent 经常用 Markdown 表格
 * 汇总状态，以前会被原样渲染成一堆竖线与分隔行。这里锁住「表格 → 真实 table 元素」。
 */
const LIVE_TABLE_REPLY = "以下内容仅依据当前已知的状态摘要整理，未调用任何工具核验：\n\n| 项目 | 状态 | 说明 |\n| --- | --- | --- |\n| MaaCore 内核 | `ready` | 内核已就绪，可接受任务提交 |\n| 设备连接 | 已连接 | 地址 `127.0.0.1:5555`，分辨率 2560×1440，重试次数 0，无最近连接错误 |\n| 流水线 / 队列 | **空闲（无运行中任务，待执行 0 项）** | `current_pipeline` 为空，队列未暂停 |\n| 最近错误 | error | 记录为「任务失败 [StartUp]」（2026-09-28 06:20:46 UTC），原因未明，需查日志确认 |\n\n补充说明：\n\n- “已连接”“空闲”等均为状态快照中的描述性字段，不代表我实际执行过任何设备操作或任务提交。\n- 那条 `StartUp` 失败记录与当前空闲状态并不矛盾，但为何失败、是否需要处理，需进一步查询日志才能判断；如果你希望，我可以再去读取日志或截图确认。"

describe('AssistantText renders a table reply as a table', () => {
  it('turns pipes and the delimiter row into table elements', () => {
    const { container } = render(<AssistantText text={LIVE_TABLE_REPLY} />)

    expect(container.querySelectorAll('table')).toHaveLength(1)
    expect([...container.querySelectorAll('th')].map((cell) => cell.textContent)).toEqual([
      '项目',
      '状态',
      '说明',
    ])
    // 表头 1 行 + 4 行数据。
    expect(container.querySelectorAll('tbody tr')).toHaveLength(4)
    // 单元格里的行内格式仍然生效。
    expect(container.querySelectorAll('strong').length).toBeGreaterThan(0)
    expect(container.querySelectorAll('code').length).toBeGreaterThan(0)
    // 分隔行与竖线不再作为原文出现。
    expect(container.textContent).not.toContain('| --- |')
  })
})

/**
 * 第三条真实回复（deepseek-flash，2026-09-28 会话 `e1ba2b83`，原文取自库里落库的
 * assistant 消息）：模型写的表格**带外框**，而分隔行是紧凑写法 `|---|---|`。
 *
 * 这一格以前整块渲染成原文——`| 字段 | 值 |` 同时符合列表项的形状（`|` 是合法的
 * 无序列表标记），列表匹配器先一步把表头吃掉，表格判定根本没轮到。这里锁住
 * 「带外框 + 紧凑分隔行」也算表格，且不再出现竖线与分隔行。
 */
const LIVE_FRAMED_TABLE_REPLY = [
  '已创建成功。以下是确认结果（均来自工具返回，非推测）：',
  '',
  '**新定时任务**',
  '| 字段 | 值 |',
  '|---|---|',
  '| 名称 | 日常刷剿灭（周一早） |',
  '| ID | `0b042900-5e8f-485d-8717-5e7594dc07d0` |',
  '| cron | `0 7 * * 1`（周一 07:00） |',
  '| 时区 | Asia/Shanghai |',
  '| 状态 | enabled=true，priority=2 |',
  '| 下次执行 | 2026-10-04T23:00:00Z，即北京时间 2026-10-05（周一）07:00 |',
  '',
  '**任务链**',
  '1. StartUp（B 服，自动启动客户端）',
  '2. Recruit（公招 4 次）',
].join('\n')

describe('AssistantText renders a framed compact table reply as a table', () => {
  it('turns a leading-pipe header and |---|---| delimiter into table elements', () => {
    const { container } = render(<AssistantText text={LIVE_FRAMED_TABLE_REPLY} />)

    expect(container.querySelectorAll('table')).toHaveLength(1)
    expect([...container.querySelectorAll('th')].map((cell) => cell.textContent)).toEqual(['字段', '值'])
    expect(container.querySelectorAll('tbody tr')).toHaveLength(6)
    // 单元格里的行内代码仍然生效，且表格后面的有序列表没有被表格吞掉。
    expect(container.querySelectorAll('code').length).toBeGreaterThan(0)
    expect(container.querySelectorAll('ol li')).toHaveLength(2)
    // 表头、分隔行与正文里的竖线都不再作为原文出现。
    expect(container.textContent).not.toContain('|')
    expect(container.textContent).not.toContain('---')
  })
})

