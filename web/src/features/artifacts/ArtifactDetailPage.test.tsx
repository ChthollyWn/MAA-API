import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { keys } from '@/api/keys'
import { toast } from '@/components/ui/toaster'
import {
  createConsoleApi,
  makeTicket,
  makeTicketDetail,
  renderAt,
  type Json,
} from '@/features/console/test-utils'
import ArtifactDetailPage from './ArtifactDetailPage'

/** jsdom 没有 ResizeObserver；Radix 开关会用它量尺寸。 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const VIEW_MIME = 'application/vnd.maa.view+json'

function artifactDetail(overrides: Json = {}): Json {
  return {
    id: 'a1',
    ticket_id: 't1',
    session_id: 'w1',
    kind: 'note',
    title: '作战笔记',
    mime: 'text/markdown',
    size_bytes: 812,
    storage: 'db',
    pinned: false,
    created_at: '2026-10-01T09:30:00Z',
    content: '# 标题\n\n正文内容',
    truncated: false,
    meta: {},
    ...overrides,
  }
}

function viewDetail(overrides: Json = {}): Json {
  return artifactDetail({
    kind: 'form',
    title: '这周刷哪一关',
    mime: VIEW_MIME,
    content: JSON.stringify({
      version: 1,
      title: '这周刷哪一关',
      components: [
        {
          type: 'field',
          field: 'select',
          name: 'stage',
          label: '关卡',
          required: true,
          options: [
            { value: '1-7', label: '1-7' },
            { value: '1-8', label: '1-8' },
          ],
          multiple: false,
          default: '1-7',
        },
        { type: 'submit', label: '提交' },
      ],
    }),
    meta: { view_version: 1, submissions: [], last_submitted_at: null },
    ...overrides,
  })
}

function renderDetail(artifactId = 'a1') {
  return renderAt(`/more/artifacts/${artifactId}`, [{
    path: '/more/artifacts/:artifactId',
    element: <ArtifactDetailPage />,
  }])
}

describe('M90 产物详情页', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  })

  afterEach(() => {
    toast.dismiss()
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders markdown, text and view artifacts by mime', async () => {
    createConsoleApi({ artifacts: [artifactDetail()], artifactDetails: { a1: artifactDetail() } })
    renderDetail()
    expect(await screen.findByRole('heading', { level: 1, name: '标题' })).toBeInTheDocument()
    expect(screen.getByText('正文内容')).toBeInTheDocument()

    cleanup()
    createConsoleApi({
      artifacts: [artifactDetail({ mime: 'text/plain', content: '第一行\n第二行' })],
      artifactDetails: { a1: artifactDetail({ mime: 'text/plain', content: '第一行\n第二行' }) },
    })
    renderDetail()
    const pre = (await screen.findByText(/第一行/)).closest('pre')
    expect(pre).not.toBeNull()
    expect(pre).toHaveTextContent('第一行 第二行')

    cleanup()
    createConsoleApi({ artifacts: [viewDetail()], artifactDetails: { a1: viewDetail() } })
    renderDetail()
    expect(await screen.findByLabelText('关卡')).toHaveValue('1-7')
    expect(screen.getByRole('button', { name: '提交' })).toBeInTheDocument()
  })

  it('shows a truncation notice with a download button', async () => {
    const detail = artifactDetail({ truncated: true })
    createConsoleApi({ artifacts: [detail], artifactDetails: { a1: detail } })
    renderDetail()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('内容较长，只显示前 64 KiB')
    expect(within(alert).getByRole('button', { name: '下载' })).toBeInTheDocument()
  })

  it('shows the not-found state', async () => {
    createConsoleApi({ artifacts: [] })
    renderDetail()

    expect(await screen.findByText('没有这个产物')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回产物列表' })).toHaveAttribute('href', '/more/artifacts')
  })

  it('submits a form from the detail page', async () => {
    const detail = viewDetail()
    const api = createConsoleApi({ artifacts: [detail], artifactDetails: { a1: detail } })
    renderDetail()

    fireEvent.change(await screen.findByLabelText('关卡'), { target: { value: '1-8' } })
    fireEvent.click(screen.getByRole('button', { name: '提交' }))

    await waitFor(() => expect(api.requests.find(
      (request) => request.method === 'POST' && request.path === '/api/agent/artifacts/a1/submit',
    )).toBeDefined())
    expect(await screen.findByText('已提交表单')).toBeInTheDocument()
    expect(await screen.findByText(/已提交 · /)).toBeInTheDocument()
    expect(screen.getByLabelText('关卡')).toBeDisabled()
  })

  it('links the owning ticket', async () => {
    const detail = artifactDetail()
    createConsoleApi({
      artifacts: [detail],
      artifactDetails: { a1: detail },
      ticketDetails: { t1: makeTicketDetail({ ticket: makeTicket({ id: 't1', title: '日常' }) }) },
    })
    renderDetail()

    const link = await screen.findByRole('link', { name: '所属工单「日常」' })
    expect(link).toHaveAttribute('href', '/tickets/t1')
  })

  it('keeps rendering when the owning ticket is gone', async () => {
    const detail = artifactDetail({ ticket_id: 't9' })
    createConsoleApi({ artifacts: [detail], artifactDetails: { a1: detail } })
    renderDetail()

    expect(await screen.findByRole('heading', { level: 1, name: '标题' })).toBeInTheDocument()
    expect(await screen.findByText('所属工单 t9')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '所属工单 t9' })).toHaveAttribute('href', '/tickets/t9')
  })

  it('pins and unpins from the detail page', async () => {
    const detail = artifactDetail()
    const api = createConsoleApi({ artifacts: [detail], artifactDetails: { a1: detail } })
    const { queryClient } = renderDetail()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    fireEvent.click(await screen.findByRole('button', { name: '置顶' }))

    await waitFor(() => expect(api.requests.find(
      (request) => request.method === 'PUT' && request.path === '/api/agent/artifacts/a1/pin',
    )).toBeDefined())
    expect(api.requests.find(
      (request) => request.method === 'PUT' && request.path === '/api/agent/artifacts/a1/pin',
    )!.body).toEqual({ pinned: true })
    expect(await screen.findByText('已置顶')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '取消置顶' })).toBeInTheDocument()
    expect(invalidate).toHaveBeenCalledWith({ queryKey: keys.agent.artifactsAll() })

    fireEvent.click(screen.getByRole('button', { name: '取消置顶' }))
    await waitFor(() => expect(api.requests.filter(
      (request) => request.method === 'PUT' && request.path === '/api/agent/artifacts/a1/pin',
    )).toHaveLength(2))
    expect(api.requests.filter(
      (request) => request.method === 'PUT' && request.path === '/api/agent/artifacts/a1/pin',
    ).at(-1)!.body).toEqual({ pinned: false })
    expect(await screen.findByText('已取消置顶')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '置顶' })).toBeInTheDocument()
  })

  it('downloads the raw content', async () => {
    const detail = artifactDetail()
    const api = createConsoleApi({ artifacts: [detail], artifactDetails: { a1: detail } })
    const createObjectURL = vi.fn(() => 'blob:artifact')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }))
    const downloads: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      downloads.push(this.download)
    })
    renderDetail()

    fireEvent.click(await screen.findByRole('button', { name: '下载' }))

    await waitFor(() => expect(api.requests.find(
      (request) => request.method === 'GET' && request.path === '/api/agent/artifacts/a1/content',
    )).toBeDefined())
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled())
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:artifact')
    expect(downloads).toEqual(['作战笔记.md'])
    expect(await screen.findByText('已下载 作战笔记.md')).toBeInTheDocument()
  })
})
