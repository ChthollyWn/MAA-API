import { createElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useMentionCandidates } from './mention-sources'
import type { MentionType } from './types'

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
}

function wrapperFor(client: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return { wrapper: wrapperFor(client) }
}

function calledUrls(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>): URL[] {
  return fetchMock.mock.calls.map(([input, init]) => {
    const request = input instanceof Request
      ? input
      : new Request(new URL(String(input), window.location.origin), init)
    return new URL(request.url)
  })
}

function hook(type: MentionType, query: string, enabled = true) {
  const { wrapper } = setup()
  return renderHook(() => useMentionCandidates(type, query, enabled), { wrapper })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('M72 mention sources requests', () => {
  it('reads one page of tickets for the ticket group', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => json({
      items: [{ id: 't1', title: '日常', status: 'open' }],
      total: 1,
      page: 1,
      size: 20,
    }))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = hook('ticket', '日')

    await waitFor(() => expect(result.current.items).toHaveLength(1))
    expect(result.current.items[0]).toMatchObject({ type: 'ticket', id: 't1', label: '日常', detail: '进行中' })
    const [url] = calledUrls(fetchMock)
    expect(url?.pathname).toBe('/api/agent/tickets')
    expect(url?.searchParams.get('page')).toBe('1')
    expect(url?.searchParams.get('size')).toBe('20')
  })

  it('reads the knowledge list and keeps only enabled entries', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => json({
      items: [
        { id: 'kb-1', title: '日常', enabled: true },
        { id: 'kb-2', title: '剧情', enabled: false },
      ],
      total: 2,
    }))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = hook('knowledge', '')

    await waitFor(() => expect(result.current.items).toHaveLength(1))
    const [url] = calledUrls(fetchMock)
    expect(url?.pathname).toBe('/api/agent/knowledge')
  })

  it('asks the server for operators only after a non-empty debounced query', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => json({
      items: [{ char_id: 'char_002_amiya', name: '阿米娅', own: true }],
      total: 1,
      limit: 8,
      offset: 0,
    }))
    vi.stubGlobal('fetch', fetchMock)

    const empty = hook('operator', '')
    expect(empty.result.current.items).toEqual([])
    expect(fetchMock).not.toHaveBeenCalled()

    const filled = hook('operator', '阿米')
    await waitFor(() => expect(filled.result.current.items).toHaveLength(1))
    expect(filled.result.current.items[0]).toMatchObject({ type: 'operator', detail: '持有' })
    const url = calledUrls(fetchMock)[0]
    expect(url?.pathname).toBe('/api/account/operators')
    expect(url?.searchParams.get('query')).toBe('阿米')
    expect(url?.searchParams.get('owned_only')).toBe('false')
    expect(url?.searchParams.get('limit')).toBe('8')
  })

  it('never sends a request for stages', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => json({}))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = hook('stage', '1-7')

    expect(result.current.items).toEqual([
      { type: 'stage', id: '1-7', label: '1-7', detail: '关卡' },
    ])
    expect(result.current.isPending).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('reads ten pipelines and filters titles and id prefixes locally', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => json({
      items: [
        { id: 'abcdefgh1234', title: '每日刷图', status: 'completed' },
        { id: 'zzzzzzzz9999', title: '剿灭', status: 'running' },
      ],
      total: 2,
    }))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = hook('pipeline', 'ABCDEF')

    await waitFor(() => expect(result.current.items).toHaveLength(1))
    expect(result.current.items[0]).toMatchObject({ type: 'pipeline', id: 'abcdefgh1234', label: '每日刷图' })
    const [url] = calledUrls(fetchMock)
    expect(url?.pathname).toBe('/api/pipelines')
    expect(url?.searchParams.get('page')).toBe('1')
    expect(url?.searchParams.get('size')).toBe('10')
  })
})
