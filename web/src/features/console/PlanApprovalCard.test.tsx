import type { ComponentProps } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import type { PlanView } from '@/features/tickets/workspace-api'
import type { TicketView } from './api'
import { PlanApprovalCard } from './PlanApprovalCard'

const ticket: TicketView = {
  id: 't1',
  title: '清日常',
  kind: 'general',
  status: 'waiting',
  origin: 'user',
  summary: null,
  lastReportStatus: null,
  humanAction: { type: 'decision', prompt: '批准计划「清日常」？', ref_id: 'p1' },
  createdAt: null,
  updatedAt: null,
  endedAt: null,
}

const plan: PlanView = {
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
}

function renderCard(overrides: Partial<ComponentProps<typeof PlanApprovalCard>> = {}) {
  return render(
    <MemoryRouter>
      <PlanApprovalCard
        ticket={ticket}
        plan={plan}
        workerSessionId="w1"
        busy={null}
        onResolve={vi.fn()}
        {...overrides}
      />
    </MemoryRouter>,
  )
}

describe('计划待批准卡片', () => {
  afterEach(cleanup)

  it('renders the plan title, steps and source ticket', () => {
    renderCard()

    expect(screen.getByRole('heading', { name: '计划待批准' })).toBeInTheDocument()
    expect(screen.getByText('清日常')).toBeInTheDocument()
    expect(screen.getByText('提交剿灭')).toBeInTheDocument()
    expect(screen.getByText('先看体力')).toBeInTheDocument()
    expect(screen.getByText('进行中')).toBeInTheDocument()
    expect(screen.getByText('待开始')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '打开工单' })).toHaveAttribute('href', '/tickets/t1')
    expect(screen.getByRole('button', { name: '批准计划' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '退回计划' })).toBeEnabled()
  })

  it('approves the plan', () => {
    const onResolve = vi.fn()
    renderCard({ onResolve })

    fireEvent.click(screen.getByRole('button', { name: '批准计划' }))

    expect(onResolve).toHaveBeenCalledWith('w1', 't1', { approved: true })
  })

  it('requires a non-empty reason before rejecting', () => {
    const onResolve = vi.fn()
    renderCard({ onResolve })

    fireEvent.click(screen.getByRole('button', { name: '退回计划' }))
    const submit = screen.getByRole('button', { name: '提交退回' })
    expect(submit).toBeDisabled()

    fireEvent.change(screen.getByLabelText('退回原因'), { target: { value: '第二步改到下午' } })
    expect(submit).toBeEnabled()
    fireEvent.click(submit)

    expect(onResolve).toHaveBeenCalledWith('w1', 't1', { approved: false, reason: '第二步改到下午' })
  })

  it('closes the reject form and clears the reason', () => {
    renderCard()

    fireEvent.click(screen.getByRole('button', { name: '退回计划' }))
    fireEvent.change(screen.getByLabelText('退回原因'), { target: { value: '改一下' } })
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(screen.queryByLabelText('退回原因')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '退回计划' }))
    expect(screen.getByLabelText('退回原因')).toHaveValue('')
  })

  it('disables both actions while the request is in flight', () => {
    renderCard({ busy: 'approve' })

    expect(screen.getByRole('button', { name: '批准计划' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '退回计划' })).toBeDisabled()
  })

  it('limits the reject reason to 200 characters', () => {
    renderCard()

    fireEvent.click(screen.getByRole('button', { name: '退回计划' }))
    expect(screen.getByLabelText('退回原因')).toHaveAttribute('maxlength', '200')
  })
})
