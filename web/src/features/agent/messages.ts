import type { AgentMessage, PageEnvelope } from './types'

/** 服务端允许的最大 `size`（`maa_api/api/routers/agent.py` 的 `_page`）。 */
export const MESSAGE_PAGE_SIZE = 200

export interface MessagePage {
  page: number
  items: AgentMessage[]
}

/**
 * 已读取的一段连续消息页，页码升序。服务端按 seq 升序、按偏移分页，消息只追加不删改，
 * 所以除最后一页外的页都已写满、内容不会再变。
 */
export interface MessageWindow {
  pages: MessagePage[]
  total: number
}

export type FetchMessagePage = (page: number) => Promise<PageEnvelope<AgentMessage>>

export function pageCount(total: number): number {
  return Math.max(1, Math.ceil(total / MESSAGE_PAGE_SIZE))
}

function countItems(window: MessageWindow): number {
  return window.pages.reduce((sum, page) => sum + page.items.length, 0)
}

/** 从 `from` 页一直读到最新一页。某页没写满就停：读取期间有新消息写入，下次刷新再补。 */
async function readThrough(fetchPage: FetchMessagePage, from: number): Promise<MessageWindow> {
  let page = from
  let result = await fetchPage(page)
  const pages: MessagePage[] = [{ page, items: result.items }]
  while (result.items.length >= MESSAGE_PAGE_SIZE && page < pageCount(result.total)) {
    page += 1
    result = await fetchPage(page)
    pages.push({ page, items: result.items })
  }
  return { pages, total: result.total }
}

/** 首次读取：第一页拿到 total，再读最后一页；最后一页没写满时带上前一页，免得只看到寥寥几条。 */
async function readLatest(fetchPage: FetchMessagePage): Promise<MessageWindow> {
  const first = await fetchPage(1)
  const last = pageCount(first.total)
  if (last === 1) return { pages: [{ page: 1, items: first.items }], total: first.total }
  const tail = await readThrough(fetchPage, last)
  if (countItems(tail) >= MESSAGE_PAGE_SIZE) return tail
  const previousPage = last - 1
  const previous = previousPage === 1 ? first : await fetchPage(previousPage)
  return { pages: [{ page: previousPage, items: previous.items }, ...tail.pages], total: tail.total }
}

/**
 * 读取（或刷新）会话消息窗口，供消息查询的 queryFn 使用。
 *
 * 刷新时保留用户已经加载的更早页，只重读窗口的最后一页及其后新出现的页；读完再取一次
 * `readCurrent()`，这样刷新途中「加载更早」插入的页不会被覆盖掉。
 */
export async function loadMessageWindow(
  fetchPage: FetchMessagePage,
  readCurrent: () => MessageWindow | undefined,
): Promise<MessageWindow> {
  const current = readCurrent()
  const lastLoaded = current?.pages.at(-1)
  if (!current || !lastLoaded) return readLatest(fetchPage)
  const tail = await readThrough(fetchPage, lastLoaded.page)
  if (tail.total < current.total) return readLatest(fetchPage)
  const latest = readCurrent() ?? current
  const kept = latest.pages.filter((page) => page.page < lastLoaded.page)
  return { pages: [...kept, ...tail.pages], total: tail.total }
}

/** 窗口之前还有哪一页可以加载；已经从第一页开始时返回 null。 */
export function earlierPageNumber(window: MessageWindow | undefined): number | null {
  const first = window?.pages[0]?.page
  return first !== undefined && first > 1 ? first - 1 : null
}

/** 把紧挨在窗口前面的一页插到最前；页码对不上（窗口已被替换）时原样返回。 */
export function prependPage(window: MessageWindow, page: MessagePage): MessageWindow {
  if (earlierPageNumber(window) !== page.page) return window
  return { ...window, pages: [page, ...window.pages] }
}

/** 按顺序展开窗口里的消息；同一条消息只保留一次。 */
export function windowItems(window: MessageWindow | undefined): AgentMessage[] {
  if (!window) return []
  const seen = new Set<number>()
  const items: AgentMessage[] = []
  for (const page of window.pages) {
    for (const item of page.items) {
      if (seen.has(item.id)) continue
      seen.add(item.id)
      items.push(item)
    }
  }
  return items
}
