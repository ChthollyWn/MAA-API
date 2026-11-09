import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { createConsoleApi, renderAt, type Json } from '@/features/console/test-utils'
import ArtifactsPage from './ArtifactsPage'

function artifact(index: number, overrides: Json = {}): Json {
  return {
    id: `a${index}`,
    ticket_id: 't1',
    session_id: 'w1',
    kind: 'note',
    title: `产物 ${index}`,
    mime: 'text/markdown',
    size_bytes: 812,
    storage: 'db',
    pinned: false,
    created_at: '2026-10-01T09:30:00Z',
    ...overrides,
  }
}

function renderPage(path = '/more/artifacts') {
  return renderAt(path, [{ path: '/more/artifacts', element: <ArtifactsPage /> }])
}

describe('M90 产物页', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('lists artifacts with a kind filter and the pinned badge', async () => {
    const api = createConsoleApi({
      artifacts: [
        artifact(1, { id: 'form1', kind: 'form', title: '本周表单', size_bytes: 512, pinned: true }),
        artifact(2),
      ],
    })
    renderPage('/more/artifacts?kind=form')

    const row = await screen.findByRole('link', { name: /本周表单/ })
    expect(row).toHaveAttribute('href', '/more/artifacts/form1')
    expect(screen.getByText(/表单 · 512 B · /)).toBeInTheDocument()
    expect(screen.getByText('已置顶')).toBeInTheDocument()
    expect(screen.queryByText('产物 2')).not.toBeInTheDocument()

    const request = api.requests.find((item) => item.method === 'GET' && item.path === '/api/agent/artifacts')
    expect(request?.query.get('kind')).toBe('form')
    expect(request?.query.get('page')).toBe('1')
    expect(request?.query.get('size')).toBe('20')
  })

  it('loads more pages', async () => {
    const api = createConsoleApi({
      artifacts: Array.from({ length: 25 }, (_, index) => artifact(index + 1)),
    })
    renderPage()

    expect(await screen.findByText('产物 20')).toBeInTheDocument()
    expect(screen.queryByText('产物 25')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '加载更多' }))

    expect(await screen.findByText('产物 25')).toBeInTheDocument()
    const pages = api.requests
      .filter((item) => item.method === 'GET' && item.path === '/api/agent/artifacts')
      .map((item) => item.query.get('page'))
    expect(pages).toContain('2')
  })

  it('shows and clears the ticket filter chip', async () => {
    createConsoleApi({ artifacts: [artifact(1)] })
    const { router } = renderPage('/more/artifacts?ticket_id=t1')

    expect(await screen.findByText('只看这个工单')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '移除筛选条件：只看这个工单' }))

    await waitFor(() => expect(router.state.location.search).toBe(''))
    expect(screen.queryByText('只看这个工单')).not.toBeInTheDocument()
  })

  it('shows the empty state', async () => {
    createConsoleApi({ artifacts: [] })
    renderPage()

    expect(await screen.findByText('还没有产物')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '前往指挥台' })).toHaveAttribute('href', '/')
  })
})
