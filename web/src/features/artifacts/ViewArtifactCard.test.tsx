import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { createConsoleApi, json, renderAt, type Json } from '@/features/console/test-utils'
import { toast } from '@/components/ui/toaster'
import { ViewArtifactCard } from './ViewArtifactCard'

/** jsdom 没有 ResizeObserver；Radix 开关会用它量尺寸。 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const VIEW_MIME = 'application/vnd.maa.view+json'

function viewComponents(): Json[] {
  return [
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
    {
      type: 'field',
      field: 'number',
      name: 'count',
      label: '次数',
      required: false,
      min: 1,
      max: 10,
      default: 2,
    },
    {
      type: 'field',
      field: 'toggle',
      name: 'potion',
      label: '可以用理智药',
      required: false,
      default: false,
    },
    { type: 'submit', label: '提交' },
  ]
}

function viewContent(components: Json[] = viewComponents()): string {
  return JSON.stringify({ version: 1, title: '刷哪一关', components })
}

function artifact(overrides: Json = {}): Json {
  return {
    id: 'view1',
    ticket_id: 't1',
    session_id: 'w1',
    kind: 'form',
    title: '这周刷哪一关',
    mime: VIEW_MIME,
    size_bytes: 512,
    storage: 'db',
    pinned: false,
    created_at: '2026-10-01T09:30:00Z',
    ...overrides,
  }
}

function detail(overrides: Json = {}): Json {
  return {
    ...artifact(),
    content: viewContent(),
    truncated: false,
    meta: { view_version: 1, submissions: [], last_submitted_at: null },
    ...overrides,
  }
}

function renderCard(ticketEnded = false) {
  return renderAt('/', [{
    path: '/',
    element: <ViewArtifactCard artifactId="view1" ticketEnded={ticketEnded} />,
  }])
}

function submitRequest(api: ReturnType<typeof createConsoleApi>) {
  return api.requests.find(
    (request) => request.method === 'POST' && request.path === '/api/agent/artifacts/view1/submit',
  )
}

describe('M90 视图卡', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  })

  afterEach(() => {
    toast.dismiss()
    cleanup()
    vi.unstubAllGlobals()
  })

  it('submits a form and shows the submitted state', async () => {
    const api = createConsoleApi({
      artifacts: [artifact()],
      artifactDetails: { view1: detail() },
    })
    renderCard()

    const stage = await screen.findByLabelText('关卡')
    fireEvent.change(stage, { target: { value: '1-8' } })
    const count = screen.getByLabelText('次数')
    fireEvent.change(count, { target: { value: '5' } })
    fireEvent.click(screen.getByRole('switch', { name: /可以用理智药/ }))
    fireEvent.click(screen.getByRole('button', { name: '提交' }))

    await waitFor(() => expect(submitRequest(api)).toBeDefined())
    expect(submitRequest(api)!.body).toEqual({
      values: { stage: '1-8', count: 5, potion: true },
    })
    expect(await screen.findByText('已提交表单')).toBeInTheDocument()
    expect(await screen.findByText(/已提交 · /)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '提交' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('关卡')).toBeDisabled()
  })

  it('maps server field errors onto the fields', async () => {
    createConsoleApi({
      artifacts: [artifact()],
      artifactDetails: { view1: detail() },
      overrides: [({ method, path }) => (
        method === 'POST' && path === '/api/agent/artifacts/view1/submit'
          ? json({
              error: {
                code: 'INVALID_PARAMETER',
                message: '表单有 1 处需要修改',
                details: { fields: { stage: '不在可选项里' } },
              },
            }, 400)
          : undefined
      )],
    })
    renderCard()

    fireEvent.click(await screen.findByRole('button', { name: '提交' }))

    expect(await screen.findByText('表单有 1 处需要修改')).toBeInTheDocument()
    expect(await screen.findByText('不在可选项里')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '提交' })).toBeInTheDocument()
  })

  it('reopens a submitted form for editing', async () => {
    const submissions = [
      { at: '2026-10-02T10:00:00Z', values: { stage: '1-7', count: 3, potion: false } },
      { at: '2026-10-03T10:00:00Z', values: { stage: '1-8', count: 7, potion: true } },
    ]
    createConsoleApi({
      artifacts: [artifact()],
      artifactDetails: {
        view1: detail({
          meta: { view_version: 1, submissions, last_submitted_at: submissions[1]!.at },
        }),
      },
    })
    renderCard()

    expect(await screen.findByText(/已提交 · /)).toBeInTheDocument()
    expect(screen.getByLabelText('关卡')).toBeDisabled()
    expect(screen.queryByRole('button', { name: '提交' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '修改后重新提交' }))

    expect(screen.getByLabelText('关卡')).toBeEnabled()
    expect(screen.getByLabelText('关卡')).toHaveValue('1-8')
    expect(screen.getByLabelText('次数')).toHaveValue(7)
    expect(screen.getByRole('switch', { name: /可以用理智药/ })).toHaveAttribute('aria-checked', 'true')
  })

  it('locks the form when the ticket has ended', async () => {
    createConsoleApi({
      artifacts: [artifact()],
      artifactDetails: { view1: detail() },
    })
    renderCard(true)

    expect(await screen.findByText('工单已结束，不能再提交')).toBeInTheDocument()
    expect(screen.getByLabelText('关卡')).toBeDisabled()
    expect(screen.queryByRole('button', { name: '提交' })).not.toBeInTheDocument()

    cleanup()
    createConsoleApi({
      artifacts: [artifact()],
      artifactDetails: { view1: detail() },
      overrides: [({ method, path }) => (
        method === 'POST' && path === '/api/agent/artifacts/view1/submit'
          ? json({ error: { code: 'TICKET_ENDED', message: '工单已结束', details: {} } }, 409)
          : undefined
      )],
    })
    renderCard()
    fireEvent.click(await screen.findByRole('button', { name: '提交' }))

    expect(await screen.findByText('工单已结束，不能再提交')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('关卡')).toBeDisabled())
    expect(screen.queryByRole('button', { name: '提交' })).not.toBeInTheDocument()
  })

  it('falls back to a chip for non-view artifacts', async () => {
    createConsoleApi({
      artifacts: [artifact({ id: 'note1', kind: 'note', title: '作战笔记', mime: 'text/markdown' })],
      artifactDetails: {
        note1: detail({ id: 'note1', kind: 'note', title: '作战笔记', mime: 'text/markdown', content: '正文' }),
      },
    })
    renderAt('/', [{
      path: '/',
      element: <ViewArtifactCard artifactId="note1" ticketEnded={false} />,
    }])

    const link = await screen.findByRole('link', { name: /作战笔记/ })
    expect(link).toHaveAttribute('href', '/more/artifacts/note1')
    expect(screen.getByText('笔记')).toBeInTheDocument()
  })

  it('shows the dropped component notice', async () => {
    createConsoleApi({
      artifacts: [artifact()],
      artifactDetails: {
        view1: detail({
          content: viewContent([
            { type: 'html', text: '<b>坏组件</b>' },
            { type: 'text', text: '能显示的部分' },
          ]),
        }),
      },
    })
    renderCard()

    expect(await screen.findByText('能显示的部分')).toBeInTheDocument()
    expect(screen.getByText('有 1 个组件无法显示')).toBeInTheDocument()
  })

  it('shows an alert when the view cannot be parsed', async () => {
    createConsoleApi({
      artifacts: [artifact()],
      artifactDetails: { view1: detail({ content: '不是 JSON' }) },
    })
    renderCard()

    expect(await screen.findByText('视图无法显示')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: '在产物页打开' }).some(
      (link) => link.getAttribute('href') === '/more/artifacts/view1',
    )).toBe(true)

    cleanup()
    createConsoleApi({
      artifacts: [artifact()],
      artifactDetails: { view1: detail({ meta: { view_version: 2 } }) },
    })
    renderCard()

    expect(await screen.findByText('视图无法显示')).toBeInTheDocument()
  })
})
