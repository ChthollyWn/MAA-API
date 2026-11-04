import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { createConsoleApi, renderAt, type Json } from '@/features/console/test-utils'
import { ArtifactList } from './ArtifactList'

function renderArtifacts(ticketId = 't1') {
  return renderAt('/', [{
    path: '/',
    element: <ArtifactList ticketId={ticketId} />,
  }])
}

function artifact(overrides: Json = {}): Json {
  return {
    id: 'a1',
    ticket_id: 't1',
    session_id: 'w1',
    kind: 'note',
    title: '作战笔记',
    mime: 'text/markdown',
    size_bytes: 1536,
    storage: 'db',
    pinned: false,
    created_at: '2026-10-01T09:30:00Z',
    ...overrides,
  }
}

function artifactDetail(overrides: Json = {}): Json {
  return {
    ...artifact(),
    content: '正文内容',
    truncated: true,
    ...overrides,
  }
}

describe('M75 产物列表', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('lists title, kind, size and time for the ticket', async () => {
    const api = createConsoleApi({ artifacts: [artifact()] })
    renderArtifacts()

    expect(await screen.findByText('作战笔记')).toBeInTheDocument()
    expect(screen.getByText('笔记')).toBeInTheDocument()
    expect(screen.getByText(/1\.5 KB/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看产物 作战笔记' })).toBeInTheDocument()

    const listRequest = api.requests.find((item) => item.path === '/api/agent/artifacts')
    expect(listRequest?.query.get('ticket_id')).toBe('t1')
  })

  it('opens a drawer with the body and downloads when truncated', async () => {
    const createObjectURL = vi.fn(() => 'blob:artifact')
    vi.stubGlobal('URL', Object.assign(URL, {
      createObjectURL,
      revokeObjectURL: vi.fn(),
    }))
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const api = createConsoleApi({
      artifacts: [artifact()],
      artifactDetails: { a1: artifactDetail() },
    })
    renderArtifacts()

    fireEvent.click(await screen.findByRole('button', { name: '查看产物 作战笔记' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('正文内容')).toBeInTheDocument()
    expect(within(dialog).getByText(/只显示前 64 KiB/)).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: '下载全文' }))
    await waitFor(() => {
      expect(api.requests.some(
        (item) => item.method === 'GET' && item.path === '/api/agent/artifacts/a1/content',
      )).toBe(true)
    })
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled())
    expect(click).toHaveBeenCalled()
  })

  it('does not offer a download when the body is complete', async () => {
    createConsoleApi({
      artifacts: [artifact()],
      artifactDetails: { a1: artifactDetail({ truncated: false, content: '短正文' }) },
    })
    renderArtifacts()

    fireEvent.click(await screen.findByRole('button', { name: '查看产物 作战笔记' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('短正文')).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: '下载全文' })).not.toBeInTheDocument()
  })

  it('shows the empty state when the ticket has no artifacts', async () => {
    createConsoleApi({ artifacts: [] })
    renderArtifacts()

    expect(await screen.findByText('还没有产物')).toBeInTheDocument()
  })
})
