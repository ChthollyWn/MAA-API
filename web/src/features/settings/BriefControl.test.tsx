import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster, toast } from '@/components/ui/toaster'
import { BriefControl } from './BriefControl'

interface RecordedRequest {
  method: string
  path: string
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function installApi(options: { status?: number; body?: unknown } = {}) {
  const requests: RecordedRequest[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request
      ? input
      : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    requests.push({ method: request.method, path: url.pathname })

    if (url.pathname === '/api/agent/brief/run' && request.method === 'POST') {
      if (options.status && options.status >= 400) {
        return json(options.body ?? { error: { code: 'SERVICE_UNAVAILABLE', message: '简报服务还在启动中' } }, options.status)
      }
      return json({ ticket_id: 'ticket-brief', artifact_id: 'artifact-brief' }, 202)
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { requests }
}

function renderControl() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <BriefControl />
      <Toaster />
    </QueryClientProvider>,
  )
}

describe('M84 每日简报入口', () => {
  beforeEach(() => {
    toast.dismiss()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('posts to the brief endpoint and shows the started toast', async () => {
    const api = installApi()
    renderControl()

    expect(screen.getByRole('heading', { name: '每日简报' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '生成今日简报' }))

    await waitFor(() => expect(api.requests).toEqual([{ method: 'POST', path: '/api/agent/brief/run' }]))
    expect(await screen.findByText('已开始生成简报')).toBeInTheDocument()
  })

  it('reports the failure reason in a toast', async () => {
    installApi({
      status: 503,
      body: { error: { code: 'SERVICE_UNAVAILABLE', message: '简报服务还在启动中' } },
    })
    renderControl()

    fireEvent.click(screen.getByRole('button', { name: '生成今日简报' }))

    expect(await screen.findByText('生成简报失败：简报服务还在启动中')).toBeInTheDocument()
  })
})
