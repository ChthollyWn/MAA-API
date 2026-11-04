import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, type QueryKey } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { keys } from '@/api/keys'
import { useResolveConfirmation, useResolveQuestion } from './card-resolvers'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return { queryClient, invalidate, wrapper }
}

function invalidatedKeys(invalidate: { mock: { calls: unknown[][] } }): QueryKey[] {
  return invalidate.mock.calls.map((call) => (call[0] as { queryKey: QueryKey }).queryKey)
}

describe('M72 卡片处理', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('posts the confirmation decision, invalidates caches and reports the card resolved', async () => {
    const request = vi.fn<typeof fetch>(async () => json({}))
    vi.stubGlobal('fetch', request)
    const { invalidate, wrapper } = setup()
    const onResolved = vi.fn()
    const { result } = renderHook(() => useResolveConfirmation(onResolved), { wrapper })

    act(() => result.current.resolve('cf1', true, { attempts: 3, success: 'three_star', support: 'none' }))

    await waitFor(() => expect(onResolved).toHaveBeenCalledWith('cf1'))
    const sent = request.mock.calls[0]![0] as Request
    expect(new URL(sent.url).pathname).toBe('/api/confirmations/cf1')
    expect(sent.method).toBe('POST')
    expect(await sent.clone().json()).toEqual({
      approved: true,
      overrides: { attempts: 3, success: 'three_star', support: 'none' },
    })
    expect(invalidatedKeys(invalidate)).toEqual(expect.arrayContaining([
      keys.agent.pendingConfirmations(),
      [...keys.agent.confirmations(), 'cf1'],
      keys.agent.audits(),
    ]))
    expect(result.current.error).toBeNull()
  })

  it('invalidates the caches on a 409 without calling onResolved', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () =>
      json({ error: { code: 'CONFIRMATION_EXPIRED', message: '确认已过期' } }, 409)))
    const { invalidate, wrapper } = setup()
    const onResolved = vi.fn()
    const { result } = renderHook(() => useResolveConfirmation(onResolved), { wrapper })

    act(() => result.current.resolve('cf1', false))

    await waitFor(() => expect(result.current.error?.message).toBe('确认已过期'))
    expect(onResolved).not.toHaveBeenCalled()
    expect(invalidatedKeys(invalidate)).toContainEqual(keys.agent.pendingConfirmations())
    expect(invalidatedKeys(invalidate)).toContainEqual([...keys.agent.confirmations(), 'cf1'])
  })

  it('removes a resolved question from the attention cache and refreshes pending questions', () => {
    const { queryClient, invalidate, wrapper } = setup()
    queryClient.setQueryData(keys.agent.attentionQuestions(), [
      { question_id: 'q1', question: '第一条' },
      { question_id: 'q2', question: '第二条' },
    ])
    const onResolved = vi.fn()
    const { result } = renderHook(() => useResolveQuestion(onResolved), { wrapper })

    act(() => result.current('q1'))

    expect(queryClient.getQueryData(keys.agent.attentionQuestions()))
      .toEqual([{ question_id: 'q2', question: '第二条' }])
    expect(invalidatedKeys(invalidate)).toContainEqual(keys.agent.pendingQuestions())
    expect(onResolved).toHaveBeenCalledWith('q1')
  })
})
