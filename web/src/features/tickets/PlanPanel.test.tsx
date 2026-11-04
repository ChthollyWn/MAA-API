import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { createConsoleApi, renderAt, type Json } from '@/features/console/test-utils'
import { PlanPanel } from './PlanPanel'

function renderPlan(workerSessionId: string | null = 'w1') {
  return renderAt('/', [{
    path: '/',
    element: <PlanPanel workerSessionId={workerSessionId} />,
  }])
}

function plan(overrides: Json = {}): Json {
  return {
    id: 'p1',
    session_id: 'w1',
    ticket_id: 't1',
    title: '清日常',
    items: [
      { id: 'a1', title: '提交剿灭', status: 'in_progress', note: '先看体力' },
      { id: 'b1', title: '基建换班', status: 'pending', note: null },
    ],
    status: 'awaiting_approval',
    approved_by: null,
    approved_at: null,
    ...overrides,
  }
}

function approvalPost(requests: ReturnType<typeof createConsoleApi>['requests']) {
  return requests.find((item) => item.method === 'POST' && item.path === '/api/agent/sessions/w1/plan/approval')
}

describe('M75 计划块', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders the title, status and steps, with approve and reject while awaiting', async () => {
    createConsoleApi({ plan: plan() })
    renderPlan()

    expect(await screen.findByRole('heading', { name: '计划' })).toBeInTheDocument()
    expect(await screen.findByText('清日常')).toBeInTheDocument()
    expect(screen.getAllByText('待批准').length).toBeGreaterThan(0)
    expect(screen.getByText('提交剿灭')).toBeInTheDocument()
    expect(screen.getByText('先看体力')).toBeInTheDocument()
    expect(screen.getByText('待开始')).toBeInTheDocument()
    expect(screen.getAllByText('进行中').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '批准' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '退回' })).toBeEnabled()
  })

  it('approves the plan and shows the active status', async () => {
    const api = createConsoleApi({
      plan: plan({
        items: [{ id: 'a1', title: '提交剿灭', status: 'pending', note: null }],
      }),
    })
    renderPlan()

    expect(await screen.findByText('清日常')).toBeInTheDocument()
    expect(screen.queryByText('进行中')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '批准' }))

    await waitFor(() => expect(approvalPost(api.requests)?.body).toEqual({ approved: true }))
    expect(await screen.findByText(/由 console 批准于/)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '批准' })).not.toBeInTheDocument()
    })
    // 计划状态从「待批准」变成「进行中」，步骤本身仍是「待开始」。
    expect(screen.getAllByText('进行中').length).toBeGreaterThan(0)
    expect(screen.getByText('待开始')).toBeInTheDocument()
  })

  it('disables both actions while the approval request is in flight', async () => {
    let release = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    createConsoleApi({ plan: plan(), holdPlanApproval: () => gate })
    renderPlan()

    const approve = await screen.findByRole('button', { name: '批准' })
    fireEvent.click(approve)

    await waitFor(() => expect(approve).toBeDisabled())
    expect(screen.getByRole('button', { name: '退回' })).toBeDisabled()

    release()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '批准' })).not.toBeInTheDocument()
    })
  })

  it('requires a non-empty reason before rejecting and sends {approved:false,reason}', async () => {
    const api = createConsoleApi({ plan: plan() })
    renderPlan()

    fireEvent.click(await screen.findByRole('button', { name: '退回' }))
    const submit = screen.getByRole('button', { name: '提交退回' })
    expect(submit).toBeDisabled()

    fireEvent.change(screen.getByLabelText('退回原因'), { target: { value: '第二步改到下午' } })
    expect(submit).toBeEnabled()
    fireEvent.click(submit)

    await waitFor(() => expect(approvalPost(api.requests)?.body).toEqual({
      approved: false,
      reason: '第二步改到下午',
    }))
    expect((await screen.findAllByText('草稿')).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: '提交退回' })).not.toBeInTheDocument()
  })

  it('renders nothing and does not fetch without a worker session', () => {
    const api = createConsoleApi({ plan: plan() })
    renderPlan(null)

    expect(screen.queryByRole('heading', { name: '计划' })).not.toBeInTheDocument()
    expect(api.requests.filter((item) => item.path.includes('/plan'))).toHaveLength(0)
  })
})
