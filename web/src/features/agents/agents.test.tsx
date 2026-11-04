import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { createConsoleApi, renderAt, type Json } from '@/features/console/test-utils'
import AgentDetailPage from './AgentDetailPage'
import AgentsPage from './AgentsPage'

/** 列表项（`SpecSummary`）；四个必填字段之外按需覆盖。 */
function makeSpec(overrides: Json = {}): Json {
  return {
    name: 'worker',
    title: '通用 Worker',
    kind: 'worker',
    description: '执行工单',
    model_role: 'worker',
    spec_version: 'spec-worker',
    version: 1,
    tools: [],
    delegates: [],
    ...overrides,
  }
}

/** 详情（`SpecDetail`）：列表字段再加定义文件里的其余字段与正文。 */
function makeSpecDetail(overrides: Json = {}): Json {
  return {
    ...makeSpec(),
    body: '你是 Worker，先读工单。',
    budget: { max_cost_per_run: 1 },
    deferred_groups: [],
    device: 'none',
    max_steps: 8,
    output_schema: null,
    risk_ceiling: 'none',
    skills: [],
    ...overrides,
  }
}

function renderList() {
  return renderAt('/more/agents', [{ path: '/more/agents', element: <AgentsPage /> }])
}

function renderDetail(name: string) {
  return renderAt(`/more/agents/${name}`, [{ path: '/more/agents/:name', element: <AgentDetailPage /> }])
}

describe('M72 代理定义页', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('groups agent definitions by kind', async () => {
    createConsoleApi({
      specs: [
        makeSpec({ name: 'orchestrator', title: '总控代理', kind: 'orchestrator', description: '分派工单', version: 3 }),
        makeSpec({ name: 'general', title: '通用 Worker', kind: 'worker', description: '执行工单', version: 2 }),
        makeSpec({ name: 'researcher', title: '研究子代理', kind: 'subagent', description: '查资料', version: 1 }),
        makeSpec({ name: 'summarizer', title: '摘要代理', kind: 'background', description: '压缩历史', version: 4 }),
      ],
    })
    renderList()

    expect(await screen.findByRole('heading', { level: 1, name: '代理定义' })).toBeInTheDocument()

    const headings = await screen.findAllByRole('heading', { level: 3 })
    expect(headings.map((heading) => heading.textContent)).toEqual(['总控', 'Worker', '子代理', '后台代理'])

    const rows = [
      { title: '总控代理', description: '分派工单', name: 'orchestrator', version: 'v3' },
      { title: '通用 Worker', description: '执行工单', name: 'general', version: 'v2' },
      { title: '研究子代理', description: '查资料', name: 'researcher', version: 'v1' },
      { title: '摘要代理', description: '压缩历史', name: 'summarizer', version: 'v4' },
    ]
    for (const row of rows) {
      const link = screen.getByRole('link', { name: new RegExp(row.title) })
      expect(link).toHaveAttribute('href', `/more/agents/${row.name}`)
      expect(within(link).getByText(row.description)).toBeInTheDocument()
      expect(within(link).getByText(row.version)).toBeInTheDocument()
    }
  })

  it('shows tools, delegates and the prompt of one definition', async () => {
    createConsoleApi({
      specs: [
        makeSpec({ name: 'worker', title: '通用 Worker', version: 2 }),
        makeSpec({ name: 'researcher', title: '研究子代理', kind: 'subagent', description: '查资料' }),
      ],
      specDetails: {
        worker: makeSpecDetail({
          name: 'worker',
          title: '通用 Worker',
          version: 2,
          tools: ['submit_pipeline', 'custom_tool'],
          delegates: ['researcher'],
          skills: ['maa-rag'],
          device: 'read',
          risk_ceiling: 'consume',
        }),
      },
      modelRoles: {
        items: [{ role: 'worker', profile: 'qwen', model: 'qwen-max', source: 'setting' }],
      },
    })
    renderDetail('worker')

    expect(await screen.findByRole('heading', { level: 1, name: '通用 Worker' })).toBeInTheDocument()

    // 工具：中文名、未知工具退回原名、title 属性保留原代码名，数量写在标题旁。
    expect(screen.getByText('提交流水线')).toBeInTheDocument()
    expect(screen.getByTitle('submit_pipeline')).toHaveTextContent('提交流水线')
    expect(screen.getByText('custom_tool')).toBeInTheDocument()
    expect(screen.getByText('2 个')).toBeInTheDocument()

    // 可委派链到各自详情，名称来自定义列表。
    const delegate = await screen.findByRole('link', { name: /研究子代理/ })
    expect(delegate).toHaveAttribute('href', '/more/agents/researcher')

    // 基本信息：模型角色与解析后的实际模型。
    expect(screen.getByText('模型角色').nextElementSibling).toHaveTextContent('Worker')
    expect(await screen.findByText('档案 qwen · qwen-max')).toBeInTheDocument()
    expect(screen.getByText('只读设备画面')).toBeInTheDocument()
    expect(screen.getByText('最高到消耗类操作')).toBeInTheDocument()

    // 提示词正文默认折叠，点开后才展开；正文一直是等宽 pre。
    const prompt = screen.getByText('提示词正文').closest('details')
    expect(prompt).not.toBeNull()
    expect(prompt).not.toHaveAttribute('open')
    expect(screen.getByText('你是 Worker，先读工单。').tagName).toBe('PRE')
    fireEvent.click(screen.getByText('提示词正文').closest('summary')!)
    expect(prompt).toHaveAttribute('open')
  })

  it('shows a not-found state for an unknown definition', async () => {
    createConsoleApi({ specs: [], specDetails: {} })
    renderDetail('missing')

    expect(await screen.findByText('没有这个代理定义')).toBeInTheDocument()
    expect(screen.getByText('返回列表重新选择')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回代理定义' })).toHaveAttribute('href', '/more/agents')
  })
})
