import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfirmationCard } from './ConfirmationCard'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function confirmation(overrides: Record<string, unknown>) {
  return {
    confirmation_id: 'confirm-1',
    action: 'update_operator_box',
    risk_level: 'none',
    reason: '修改账号干员数据：2 项改动',
    status: 'pending',
    requested_by: 'internal',
    expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
    execution: null,
    ...overrides,
  }
}

function renderCard(detail: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input) => {
    const request = input instanceof Request ? input : new Request(String(input))
    if (new URL(request.url).pathname === '/api/confirmations/confirm-1') return json(detail)
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  }))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(
    <QueryClientProvider client={queryClient}>
      <ConfirmationCard confirmationId="confirm-1" busy={null} onResolve={() => {}} />
    </QueryClientProvider>,
  )
}

describe('M27 确认卡片', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('update_operator_box 渲染逐字段差异，不展开成 JSON', async () => {
    renderCard(confirmation({
      payload: {
        tool_name: 'update_operator_box',
        arguments: { changes: [{ operator: '阿米娅', elite: 2 }], reason: '手动修正识别结果' },
        diff: [
          { operator: '阿米娅', field: 'elite', from: 1, to: 2 },
          { operator: '阿米娅', field: 'level', from: 60, to: 80 },
        ],
        summary: '修改账号干员数据：2 项改动',
      },
    }))

    expect(await screen.findByText('修改账号干员数据')).toBeInTheDocument()
    const rows = await screen.findByLabelText('逐字段差异')
    expect(rows).toHaveTextContent('阿米娅')
    expect(rows).toHaveTextContent('精英化')
    expect(rows).toHaveTextContent('精一')
    expect(rows).toHaveTextContent('精二')
    expect(rows).toHaveTextContent('等级')
    expect(rows).toHaveTextContent('60')
    expect(rows).toHaveTextContent('80')
    expect(screen.getByText('理由：手动修正识别结果')).toBeInTheDocument()
    expect(screen.queryByText('查看操作参数')).not.toBeInTheDocument()
  })

  it('start_upgrade_plan 渲染摘要：目标数与每日时刻', async () => {
    renderCard(confirmation({
      action: 'start_upgrade_plan',
      payload: {
        tool_name: 'start_upgrade_plan',
        arguments: { name: '精二阿米娅', targets: [{ operator: '阿米娅', elite: 2 }] },
        summary: '启动养成计划「精二阿米娅」：2 个目标，每日 08:00、20:00',
        targets: [
          { operator: '阿米娅', elite: 2, skill_level: 7, masteries: { '2': 3 }, modules: [{ type: 'X', level: 2 }] },
          { operator: '临光', elite: 1 },
        ],
        run_times: ['08:00', '20:00'],
      },
    }))

    expect(await screen.findByText('启动养成计划')).toBeInTheDocument()
    expect(screen.getByText('启动养成计划「精二阿米娅」：2 个目标，每日 08:00、20:00')).toBeInTheDocument()
    expect(screen.getByText('目标')).toBeInTheDocument()
    expect(screen.getByText('2 个')).toBeInTheDocument()
    expect(screen.getByText('08:00、20:00')).toBeInTheDocument()
    expect(screen.getByText('精二 · 技能 7 · S2 3 · 模组 X2')).toBeInTheDocument()
    expect(screen.getByText('精一')).toBeInTheDocument()
    expect(screen.queryByText('查看操作参数')).not.toBeInTheDocument()
  })

  it('其它动作保持现状：仍可展开操作参数', async () => {
    renderCard(confirmation({
      action: 'submit_pipeline',
      reason: '碎石 2 颗',
      payload: { tool_name: 'submit_pipeline', arguments: { tasks: [{ name: 'Fight', stone: 2 }] } },
    }))

    expect(await screen.findByText('查看操作参数')).toBeInTheDocument()
    expect(screen.getByText('碎石 2 颗')).toBeInTheDocument()
    expect(screen.getByText(/"stone": 2/)).toBeInTheDocument()
  })
})
