import { describe, expect, it } from 'vitest'
import {
  MESSAGE_PAGE_SIZE,
  earlierPageNumber,
  loadMessageWindow,
  prependPage,
  windowItems,
  type MessageWindow,
} from './messages'
import type { AgentMessage } from './types'

const N = MESSAGE_PAGE_SIZE

function message(index: number): AgentMessage {
  return { id: index + 1, session_id: 'session-1', seq: index, role: index % 2 ? 'assistant' : 'user', content: `消息 ${index}` }
}

/** 与服务端一致：按 seq 升序、offset 分页。 */
function server(total: number) {
  let all = Array.from({ length: total }, (_, index) => message(index))
  const calls: number[] = []
  return {
    calls,
    fetchPage: async (page: number) => {
      calls.push(page)
      return { items: all.slice((page - 1) * N, page * N), total: all.length, page, size: N }
    },
    append(count: number) {
      const start = all.length
      all = [...all, ...Array.from({ length: count }, (_, index) => message(start + index))]
    },
  }
}

const seqs = (window: MessageWindow) => windowItems(window).map((item) => item.seq)

describe('agent message window', () => {
  it('reads a short conversation with a single request', async () => {
    const api = server(3)
    const window = await loadMessageWindow(api.fetchPage, () => undefined)

    expect(api.calls).toEqual([1])
    expect(seqs(window)).toEqual([0, 1, 2])
    expect(earlierPageNumber(window)).toBeNull()
  })

  it('reads the first page for the total and then only the last page', async () => {
    const api = server(3 * N)
    const window = await loadMessageWindow(api.fetchPage, () => undefined)

    expect(api.calls).toEqual([1, 3])
    expect(seqs(window)[0]).toBe(2 * N)
    expect(seqs(window).at(-1)).toBe(3 * N - 1)
    expect(earlierPageNumber(window)).toBe(2)
  })

  it('adds the page before a partly filled last page so the window never shows just a few messages', async () => {
    const far = server(2 * N + 5)
    const window = await loadMessageWindow(far.fetchPage, () => undefined)
    expect(far.calls).toEqual([1, 3, 2])
    expect(windowItems(window)).toHaveLength(N + 5)
    expect(earlierPageNumber(window)).toBe(1)

    // 最后一页前面就是第一页时，直接复用已经读到的第一页，不再多发请求。
    const near = server(N + 5)
    const both = await loadMessageWindow(near.fetchPage, () => undefined)
    expect(near.calls).toEqual([1, 2])
    expect(windowItems(both)).toHaveLength(N + 5)
    expect(earlierPageNumber(both)).toBeNull()
  })

  it('refreshes from the last loaded page and follows new pages across a page boundary without gaps', async () => {
    const api = server(2 * N)
    const first = await loadMessageWindow(api.fetchPage, () => undefined)
    expect(api.calls).toEqual([1, 2])
    expect(seqs(first)).toHaveLength(N)

    api.append(3)
    const refreshed = await loadMessageWindow(api.fetchPage, () => first)

    expect(api.calls).toEqual([1, 2, 2, 3])
    expect(seqs(refreshed)).toEqual(Array.from({ length: N + 3 }, (_, index) => N + index))
  })

  it('keeps earlier pages that were prepended while a refresh was in flight', async () => {
    const api = server(3 * N)
    const initial = await loadMessageWindow(api.fetchPage, () => undefined)
    let current: MessageWindow = initial
    api.append(1)
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => { release = resolve })
    const gatedFetch = async (page: number) => {
      await gate
      return api.fetchPage(page)
    }

    const refreshing = loadMessageWindow(gatedFetch, () => current)
    const earlier = await api.fetchPage(2)
    current = prependPage(current, { page: 2, items: earlier.items })
    release()
    const refreshed = await refreshing

    expect(earlierPageNumber(refreshed)).toBe(1)
    expect(seqs(refreshed)[0]).toBe(N)
    expect(seqs(refreshed).at(-1)).toBe(3 * N)
    expect(new Set(seqs(refreshed)).size).toBe(seqs(refreshed).length)
  })

  it('reloads from scratch when the conversation became shorter', async () => {
    const api = server(3 * N)
    const initial = await loadMessageWindow(api.fetchPage, () => undefined)
    const shrunk = server(5)
    const reloaded = await loadMessageWindow(shrunk.fetchPage, () => initial)

    expect(seqs(reloaded)).toEqual([0, 1, 2, 3, 4])
    expect(earlierPageNumber(reloaded)).toBeNull()
  })

  it('only prepends a page that directly precedes the window and never repeats a message', () => {
    const window: MessageWindow = { pages: [{ page: 3, items: [message(2 * N)] }], total: 2 * N + 1 }
    expect(prependPage(window, { page: 1, items: [message(0)] })).toBe(window)

    const merged = prependPage(window, { page: 2, items: [message(2 * N - 1), message(2 * N)] })
    expect(seqs(merged)).toEqual([2 * N - 1, 2 * N])
  })
})
