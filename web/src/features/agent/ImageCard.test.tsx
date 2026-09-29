import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ImageCard } from './MediaCards'
import type { ImageCardData } from './media'

const SHA = 'c'.repeat(64)

/** 记录取图请求并按用例的规则作答的 fetch 替身。 */
function installImages(respond: (path: string) => Response) {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    requests.push(`${url.pathname}${url.search}`)
    return respond(`${url.pathname}${url.search}`)
  }))
  return requests
}

function imageResponse(): Response {
  return new Response(new Blob(['jpeg'], { type: 'image/jpeg' }), { headers: { 'Content-Type': 'image/jpeg' } })
}

function missingResponse(): Response {
  return new Response(JSON.stringify({ error: { code: 'SCREENSHOT_NOT_FOUND', message: '截图不存在' } }), {
    status: 404,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** jsdom 没有 IntersectionObserver：手动触发进入视野，验证「懒」这件事真的成立。 */
class FakeIntersectionObserver {
  static all: FakeIntersectionObserver[] = []
  observe = vi.fn()
  unobserve = vi.fn()
  disconnect = vi.fn()
  takeRecords = vi.fn(() => [])
  root = null
  rootMargin = ''
  thresholds: number[] = []
  private readonly callback: IntersectionObserverCallback

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    FakeIntersectionObserver.all.push(this)
  }

  enter() {
    this.callback([{ isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver)
  }
}

function makeCard(overrides: Partial<ImageCardData> = {}): ImageCardData {
  return {
    type: 'image',
    screenshotId: 'shot-1',
    sha256: SHA,
    thumb: { kind: 'image', sha256: SHA, size: 'thumb' },
    full: { kind: 'image', sha256: SHA, size: 'full' },
    alt: '关卡失败画面',
    caption: '第 3 步失败',
    width: 1920,
    height: 1080,
    ...overrides,
  }
}

describe('ImageCard', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    FakeIntersectionObserver.all = []
  })

  it('waits for the thumbnail to enter the viewport, then opens the full image on click', async () => {
    const requests = installImages(() => imageResponse())
    vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver)
    render(<ImageCard card={makeCard()} />)

    // 还没进视野：不取图。
    expect(requests).toEqual([])
    expect(screen.getByText('正在加载图片')).toBeInTheDocument()
    expect(FakeIntersectionObserver.all).toHaveLength(1)

    await act(async () => {
      FakeIntersectionObserver.all[0]!.enter()
    })
    const image = await screen.findByRole('img', { name: '关卡失败画面' })
    expect(image).toHaveAttribute('src')
    expect(requests).toEqual([`/api/images/${SHA}/thumb`])
    expect(screen.getByText('第 3 步失败')).toBeInTheDocument()
    expect(screen.getByText('1920×1080')).toBeInTheDocument()

    // 点开才取原图（缩略图已经在手，先顶着显示）。
    fireEvent.click(screen.getByRole('button', { name: '查看大图：关卡失败画面' }))
    const viewer = await screen.findByRole('dialog')
    expect(within(viewer).getByRole('img')).toBeInTheDocument()
    await waitFor(() => expect(requests).toContain(`/api/images/${SHA}/full`))
  })

  it('shows 图片不可用 with a retry that recovers', async () => {
    let broken = true
    const requests = installImages(() => (broken ? missingResponse() : imageResponse()))
    render(<ImageCard card={makeCard()} />)

    expect(await screen.findByText('图片不可用')).toBeInTheDocument()
    expect(requests).toEqual([`/api/images/${SHA}/thumb`])

    broken = false
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByRole('img', { name: '关卡失败画面' })).toBeInTheDocument()
    expect(requests).toHaveLength(2)
  })

  it('uses the screenshot record endpoint when the card has no content hash', async () => {
    const requests = installImages(() => imageResponse())
    render(<ImageCard card={makeCard({
      sha256: null,
      thumb: { kind: 'screenshot', id: 'shot-7', size: 'thumb' },
      full: { kind: 'screenshot', id: 'shot-7', size: 'full' },
    })} />)

    expect(await screen.findByRole('img', { name: '关卡失败画面' })).toBeInTheDocument()
    expect(requests).toEqual(['/api/screenshots/shot-7'])

    fireEvent.click(screen.getByRole('button', { name: '查看大图：关卡失败画面' }))
    await screen.findByRole('dialog')
    // 截图记录只有一个地址：不重复请求。
    expect(requests).toEqual(['/api/screenshots/shot-7'])
  })
})
