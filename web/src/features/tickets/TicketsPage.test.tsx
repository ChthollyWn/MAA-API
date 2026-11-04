import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { useLocation } from 'react-router'
import { createConsoleApi, makeTicket, renderAt } from '@/features/console/test-utils'
import TicketsPage from './TicketsPage'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.search}</output>
}

function renderTickets(path = '/tickets') {
  return renderAt(path, [{
    path: '/tickets',
    element: <><TicketsPage /><LocationProbe /></>,
  }])
}

describe('M72 工单列表', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('lists active tickets with status, worker title and the last summary', async () => {
    const api = createConsoleApi({
      tickets: [
        makeTicket({ id: 't1', title: '日常', status: 'open', kind: 'general', origin: 'user', summary: '刷完了 1-7' }),
        makeTicket({ id: 't2', title: '剿灭', status: 'waiting', kind: 'general', origin: 'trigger', summary: '等你选关卡' }),
        makeTicket({ id: 't3', title: '旧账', status: 'done', kind: 'general', summary: '早就结束了' }),
      ],
    })
    renderTickets()

    const first = await screen.findByRole('link', { name: /日常/ })
    expect(first).toHaveAttribute('href', '/tickets/t1')
    expect(within(first).getByText('进行中')).toBeInTheDocument()
    expect(first).toHaveTextContent('通用 Worker')
    expect(first).toHaveTextContent('你发起')
    expect(within(first).getByText('刷完了 1-7')).toBeInTheDocument()

    const second = screen.getByRole('link', { name: /剿灭/ })
    expect(within(second).getByText('等你处理')).toBeInTheDocument()
    expect(within(second).getByText('等你选关卡')).toBeInTheDocument()
    expect(second).toHaveTextContent('事件')

    // 默认只看进行中的三类。
    expect(screen.queryByText('旧账')).not.toBeInTheDocument()
    const statuses = api.ticketListQueries()[0]!.query.getAll('status')
    expect(statuses).toEqual(['open', 'waiting', 'blocked'])
  })

  it('switches between active, ended and all tickets', async () => {
    const api = createConsoleApi({
      tickets: [
        makeTicket({ id: 't1', title: '日常', status: 'open' }),
        makeTicket({ id: 't3', title: '旧账', status: 'done' }),
      ],
    })
    renderTickets()
    expect(await screen.findByText('日常')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: '已结束' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?status=ended'))
    expect(await screen.findByText('旧账')).toBeInTheDocument()
    expect(screen.queryByText('日常')).not.toBeInTheDocument()
    expect(api.ticketListQueries().some(
      (request) => request.query.getAll('status').join(',') === 'done,failed,cancelled',
    )).toBe(true)

    fireEvent.click(screen.getByRole('radio', { name: '全部' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?status=all'))
    expect(await screen.findByText('日常')).toBeInTheDocument()
    expect(screen.getByText('旧账')).toBeInTheDocument()
    expect(api.ticketListQueries().some(
      (request) => request.query.getAll('status').length === 0,
    )).toBe(true)

    fireEvent.click(screen.getByRole('radio', { name: '进行中' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?status=active'))
    expect(await screen.findByText('日常')).toBeInTheDocument()
    expect(screen.queryByText('旧账')).not.toBeInTheDocument()
  })

  it('loads more tickets until the total is reached', async () => {
    const tickets = Array.from({ length: 25 }, (_, index) => makeTicket({
      id: `t${index + 1}`,
      title: `工单 ${index + 1}`,
      status: 'open',
    }))
    createConsoleApi({ tickets })
    renderTickets()

    expect(await screen.findByText('工单 1')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(20)
    fireEvent.click(screen.getByRole('button', { name: '加载更多' }))

    expect(await screen.findByText('工单 25')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(25)
    expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument()
    expect(screen.getByText('已显示全部 25 条')).toBeInTheDocument()
  })

  it('shows an empty state with a link to the console', async () => {
    createConsoleApi({ tickets: [] })
    renderTickets()

    expect(await screen.findByText('还没有工单')).toBeInTheDocument()
    expect(screen.getByText('在指挥台告诉总控要做什么')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '前往指挥台' })).toHaveAttribute('href', '/')
  })
})
