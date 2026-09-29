import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { ItemIconsCard } from './MediaCards'
import type { ItemIconsCardData } from './media'

function installImages() {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    const url = new URL(request.url)
    requests.push(`${url.pathname}${url.search}`)
    return new Response(new Blob(['png'], { type: 'image/png' }), { headers: { 'Content-Type': 'image/png' } })
  }))
  return requests
}

function makeCard(): ItemIconsCardData {
  return {
    type: 'item_icons',
    caption: '这些材料够刷三次',
    items: [
      { itemId: '2001', name: '基础作战记录', icon: { kind: 'resource-icon', itemId: '2001' } },
      { itemId: '3003', name: '固源岩', icon: { kind: 'resource-icon', itemId: '3003' } },
    ],
  }
}

describe('ItemIconsCard', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('takes every icon through the authed image endpoint and opens one on click', async () => {
    const requests = installImages()
    render(<ItemIconsCard card={makeCard()} />)

    expect(await screen.findByRole('img', { name: '基础作战记录图标' })).toHaveAttribute('src')
    expect(await screen.findByRole('img', { name: '固源岩图标' })).toHaveAttribute('src')
    expect(screen.getByText('这些材料够刷三次')).toBeInTheDocument()
    // 每个图标各取一次，都是带 item_id 的同源地址（X-Token 由 api 客户端加）。
    expect(requests).toEqual([
      '/api/resources/items/icon?item_id=2001',
      '/api/resources/items/icon?item_id=3003',
    ])

    fireEvent.click(screen.getByRole('button', { name: '查看固源岩图标' }))
    const viewer = await screen.findByRole('dialog', { name: '固源岩图标' })
    expect(within(viewer).getByRole('img')).toHaveAttribute('src')
    expect(requests).toHaveLength(2)
  })

  it('keeps a broken icon as an unavailable mark without failing the grid', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { code: 'RESOURCE_ASSET_NOT_FOUND', message: '资源不存在' } }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })))
    render(<ItemIconsCard card={makeCard()} />)

    expect(await screen.findByText('基础作战记录图标不可用')).toBeInTheDocument()
    expect(screen.getByText('固源岩图标不可用')).toBeInTheDocument()
    expect(screen.getByText('基础作战记录')).toBeInTheDocument()
  })
})
