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

  it('start_upgrade_plan 渲染预演：刷关清单、每日模板、用药、天数与冲突', async () => {
    renderCard(confirmation({
      action: 'start_upgrade_plan',
      reason: '养成计划写入：1 个目标',
      payload: {
        tool_name: 'start_upgrade_plan',
        arguments: { name: '精二阿米娅', reason: '用户手动启动养成计划' },
        summary: '启动养成计划「精二阿米娅」：1 个目标，每日 08:00',
        targets: [{ operator: '阿米娅', elite: 2 }],
        run_times: ['08:00'],
        preview: {
          today_tasks: [{ name: 'Fight', stage: '1-7', times: 4 }, { name: 'Depot' }],
          daily_template: [{ name: 'StartUp' }, { name: 'Fight', stage: '1-7', times: 12, expiring_medicine: 999 }],
          medicine: { today: 0, stone: 0, expiring_medicine: 999 },
          days_estimated: 5,
          total_sanity: 216,
          stages: [{ stage_code: '1-7', runs: 12, sanity: 216, items: [{ item_name: '固源岩' }] }],
          conflicts: [{ schedule_id: 'sched-1', name: '夜间刷关' }],
          unobtainable: [{ name: '聚酸酯', amount: 10, have: 4, missing: 6, reason: '仓库不足', hint: '先刷 4-4' }],
          notes: ['预览：没有提交流水线，也没有创建定时任务'],
        },
      },
    }))

    expect(await screen.findByText('养成计划摘要')).toBeInTheDocument()
    const today = await screen.findByLabelText('今天要提交的刷关清单')
    expect(today).toHaveTextContent('Fight')
    expect(today).toHaveTextContent('关卡 1-7')
    expect(today).toHaveTextContent('×4')
    const template = screen.getByLabelText('每日定时任务模板')
    expect(template).toHaveTextContent('StartUp')
    expect(template).toHaveTextContent('临期理智药 999')
    expect(screen.getByText('预计 5 天 · 共 216 理智 · 剩余 12 场')).toBeInTheDocument()
    expect(screen.getByText('今天 理智药 0、源石 0、临期理智药 999')).toBeInTheDocument()
    const stages = screen.getByLabelText('关卡与场次')
    expect(stages).toHaveTextContent('固源岩')
    expect(stages).toHaveTextContent('216 理智')
    expect(screen.getByRole('alert')).toHaveTextContent('与现有定时任务争抢理智：夜间刷关（sched-1）')
    expect(screen.getByLabelText('刷不出来的材料')).toHaveTextContent('聚酸酯：缺 6（需求 10，已有 4）——仓库不足；先刷 4-4')
    expect(screen.getByLabelText('预演说明')).toHaveTextContent('预览：没有提交流水线，也没有创建定时任务')
    expect(screen.getByText('理由：用户手动启动养成计划')).toBeInTheDocument()
    expect(screen.queryByText('查看操作参数')).not.toBeInTheDocument()
  })

  it('reason 里已经有冲突提示时不重复渲染一遍', async () => {
    renderCard(confirmation({
      action: 'start_upgrade_plan',
      reason: '养成计划写入：1 个目标；与现有定时任务争抢理智：夜间刷关（sched-1）',
      payload: {
        tool_name: 'start_upgrade_plan',
        arguments: { name: '精二阿米娅' },
        summary: '启动养成计划「精二阿米娅」：1 个目标，每日 08:00',
        targets: [{ operator: '阿米娅', elite: 2 }],
        run_times: ['08:00'],
        preview: {
          today_tasks: [{ name: 'Fight', stage: '1-7', times: 4 }],
          conflicts: [{ schedule_id: 'sched-1', name: '夜间刷关' }],
        },
      },
    }))

    expect(await screen.findByText('养成计划摘要')).toBeInTheDocument()
    // 冲突句只出现一次（顶部说明），预演区不再弹一遍。
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getAllByText((_, node) => node?.textContent === '养成计划写入：1 个目标；与现有定时任务争抢理智：夜间刷关（sched-1）')).toHaveLength(1)
  })

  it('预演失败时保留摘要，末尾说明预演不可用', async () => {
    renderCard(confirmation({
      action: 'start_upgrade_plan',
      payload: {
        tool_name: 'start_upgrade_plan',
        arguments: { name: '精二阿米娅' },
        summary: '启动养成计划「精二阿米娅」：1 个目标，每日 08:00',
        targets: [{ operator: '阿米娅', elite: 2 }],
        run_times: ['08:00'],
        preview_error: 'UpgradePlanPreviewError：仓库里没有固源岩',
      },
    }))

    expect(await screen.findByText('养成计划摘要')).toBeInTheDocument()
    expect(screen.getByText('08:00')).toBeInTheDocument()
    expect(screen.getByText('精二')).toBeInTheDocument()
    expect(screen.getByText('预演不可用：UpgradePlanPreviewError：仓库里没有固源岩')).toBeInTheDocument()
    expect(screen.queryByLabelText('今天要提交的刷关清单')).not.toBeInTheDocument()
  })

  it('没有预演信息时只渲染摘要，不显示预演区也不报错', async () => {
    renderCard(confirmation({
      action: 'start_upgrade_plan',
      payload: {
        tool_name: 'start_upgrade_plan',
        summary: '启动养成计划「精二阿米娅」',
        targets: [{ name: '阿米娅', elite: 2 }],
        run_times: [],
      },
    }))

    expect(await screen.findByText('养成计划摘要')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.queryByText(/预演不可用/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText('今天要提交的刷关清单')).not.toBeInTheDocument()
  })

  it('预演里的字段形状不认识时降级成文本，不抛错', async () => {
    renderCard(confirmation({
      action: 'start_upgrade_plan',
      payload: {
        tool_name: 'start_upgrade_plan',
        summary: '启动养成计划「精二阿米娅」',
        targets: [{ name: '阿米娅', elite: 2 }],
        run_times: [],
        preview: {
          today_tasks: ['Fight 1-7 x4', { weird: true }],
          stages: ['CE-6'],
          medicine: '今天不吃药',
          conflicts: 'sched-9',
        },
      },
    }))

    expect(await screen.findByText('养成计划摘要')).toBeInTheDocument()
    const today = screen.getByLabelText('今天要提交的刷关清单')
    expect(today).toHaveTextContent('Fight 1-7 x4')
    expect(today).toHaveTextContent('未命名任务')
    expect(screen.getByText('今天不吃药')).toBeInTheDocument()
    expect(screen.getByLabelText('关卡与场次')).toHaveTextContent('CE-6')
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
