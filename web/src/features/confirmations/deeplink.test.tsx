import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { ConfirmationsPanel } from './ConfirmationsPanel'

/**
 * 推送通知的深链是 `/?confirm=<id>`（docs/09 §13.2）：点通知体打开应用后必须直接
 * 定位到那张卡片，处置完再把查询参数清掉，否则刷新会再次高亮一个已处理的确认。
 */

const pending = {
  confirmation_id: 'confirm-42',
  action: 'consume_resource',
  risk_level: 'consume',
  reason: '此操作会使用一次理智药。',
  status: 'pending',
  expires_at: new Date(Date.now() + 600_000).toISOString(),
  caller: { kind: 'internal', name: '内置 Agent', session_id: 'session-1' },
  payload: { tool_name: 'submit_pipeline', arguments: { tasks: [] } },
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * fetch mock：入参可能是 Request 也可能是 URL 字符串，两种都要解析出路径。
 *
 * `afterResolve` 让用例能模拟「处置成功后服务端不再返回这条待确认」——
 * 否则卡片会一直留在列表里，测试就验证不到它消失。
 */
function respondWith(items: unknown[], options: { afterResolve?: unknown[] } = {}) {
  let current = items
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(String(input), init)
    const url = new URL(request.url)
    if (url.pathname === '/api/confirmations' && request.method === 'GET') {
      return json({ items: current, total: current.length, page: 1, size: 20 })
    }
    if (url.pathname.startsWith('/api/confirmations/') && request.method === 'POST') {
      current = options.afterResolve ?? []
      return json({ confirmation_id: 'confirm-42', status: 'approved' })
    }
    return json({ error: { message: `unexpected ${request.method} ${url.pathname}` } }, 404)
  })
}

function renderPanel(entry: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ConfirmationsPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('confirmation deep link', () => {
  beforeEach(() => {
    // jsdom 没有实现 scrollIntoView
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('highlights the card named by ?confirm= and clears the parameter after resolving', async () => {
    vi.stubGlobal('fetch', respondWith([pending]))
    renderPanel('/?confirm=confirm-42')

    expect(await screen.findByText(/此操作会使用一次理智药。/)).toBeInTheDocument()
    // 深链目标必须被高亮并滚动到可见位置
    const highlighted = document.querySelector('.ring-2')
    expect(highlighted).not.toBeNull()
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled()

    screen.getByRole('button', { name: '批准' }).click()
    await waitFor(() => {
      expect(screen.queryByText(/此操作会使用一次理智药。/)).not.toBeInTheDocument()
    })
  })

  it('explains that a deep-linked confirmation is already gone instead of showing nothing', async () => {
    vi.stubGlobal('fetch', respondWith([]))
    renderPanel('/?confirm=confirm-42')

    expect(
      await screen.findByText(/这条待确认操作已经处理或已过期/),
    ).toBeInTheDocument()
  })

  it('renders nothing when there is no pending confirmation and no deep link', async () => {
    vi.stubGlobal('fetch', respondWith([]))
    renderPanel('/')

    await waitFor(() => {
      expect(screen.queryByText(/已经处理或已过期/)).not.toBeInTheDocument()
    })
    expect(screen.queryByText('需要人工确认')).not.toBeInTheDocument()
  })

  it('does not scroll or highlight without a deep link', async () => {
    vi.stubGlobal('fetch', respondWith([pending]))
    renderPanel('/')

    expect(await screen.findByText(/此操作会使用一次理智药。/)).toBeInTheDocument()
    expect(document.querySelector('.ring-2')).toBeNull()
  })
})
