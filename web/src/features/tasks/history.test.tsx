import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { completedDetail, installTaskApi, renderTaskArea, type PageEnvelope } from './test-utils'

const completedRow = {
  id: 'history-1', title: '完成的日常任务', source: 'manual', priority: 0, status: 'COMPLETED', task_count: 1,
  created_at: '2026-09-23T11:59:00Z', started_at: '2026-09-23T12:00:00Z', finished_at: '2026-09-23T12:00:42Z',
}

const twoPages: Record<number, PageEnvelope> = {
  1: { items: [completedRow], total: 21, page: 1, size: 20 },
  2: { items: [{ id: 'history-2', title: '最后一条', source: 'scheduled', priority: 2, status: 'FAILED', task_count: 2 }], total: 21, page: 2, size: 20 },
}

describe('task history', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads more pages with page, size and total and stops at the end (T9)', async () => {
    const network = installTaskApi({ historyPages: twoPages })
    renderTaskArea('/tasks/history')

    const first = await screen.findByRole('link', { name: /完成的日常任务/ })
    expect(first).toHaveAttribute('href', '/tasks/history/history-1')
    for (const text of ['已完成', '42 秒', '1 个任务']) expect(first).toHaveTextContent(text)
    fireEvent.click(screen.getByRole('button', { name: '加载更多' }))

    expect(await screen.findByRole('link', { name: /最后一条/ })).toHaveTextContent('执行失败')
    expect(screen.getByRole('link', { name: /完成的日常任务/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument()
    expect(screen.getByText('已显示全部 21 条')).toBeInTheDocument()
    const pages = network.find('GET', '/api/pipelines')
    expect(pages.map((request) => new URL(request.url).searchParams.get('page'))).toEqual(['1', '2'])
    expect(pages.every((request) => new URL(request.url).searchParams.get('size') === '20')).toBe(true)
  })

  it('names untitled pipelines after their schedule or source without reading each detail', async () => {
    const network = installTaskApi({
      historyPages: {
        1: {
          items: [
            { id: 'history-3', title: null, source: 'scheduled', schedule_id: 'schedule-1', priority: 2, status: 'completed', task_count: 2 },
            { id: 'history-4', title: null, source: 'manual', schedule_id: null, priority: 1, status: 'completed', task_count: 3 },
            { id: 'history-5', title: null, source: 'agent', schedule_id: null, priority: 1, status: 'failed', task_count: 1 },
          ],
          total: 3,
          page: 1,
          size: 20,
        },
      },
      schedules: [{ id: 'schedule-1', name: '日常', cron: '0 4 * * *', timezone: 'Asia/Shanghai', enabled: true, priority: 2, template: [] }],
    })
    renderTaskArea('/tasks/history')

    expect(await screen.findByRole('link', { name: /定时 · 日常/ })).toHaveAttribute('href', '/tasks/history/history-3')
    expect(screen.getByRole('link', { name: /手动任务 · 3 项/ })).toHaveAttribute('href', '/tasks/history/history-4')
    expect(screen.getByRole('link', { name: /Agent 任务 · 1 项/ })).toHaveAttribute('href', '/tasks/history/history-5')
    expect(network.find('GET', '/api/pipelines/history-3')).toHaveLength(0)
    expect(network.find('GET', '/api/pipelines/history-4')).toHaveLength(0)
  })

  it('shows an empty history with a way to create a task', async () => {
    installTaskApi()
    renderTaskArea('/tasks/history')

    expect(await screen.findByText('还没有执行记录')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '新建任务' })).toHaveAttribute('href', '/tasks')
  })

  it('opens the detail page with its summary, task results, recent logs and screenshots (T9)', async () => {
    const network = installTaskApi({ historyPages: { 1: { items: [completedRow], total: 1, page: 1, size: 20 } } })
    const { router } = renderTaskArea('/tasks/history')

    fireEvent.click(await screen.findByRole('link', { name: /完成的日常任务/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/history/history-1'))

    const summary = await screen.findByRole('region', { name: '完成的日常任务' })
    for (const text of ['已完成', '0（最高）', '手动', '42 秒']) expect(within(summary).getByText(text)).toBeInTheDocument()
    const tasks = screen.getByRole('region', { name: '任务结果' })
    expect(within(tasks).getByText('刷理智')).toBeInTheDocument()
    expect(within(tasks).getByText('42 秒')).toBeInTheDocument()

    expect(await screen.findByText('已进入关卡 1-7')).toBeInTheDocument()
    const [logs] = network.find('GET', '/api/pipelines/history-1/logs')
    expect(new URL(logs.url).searchParams.get('size')).toBe('100')
    expect(new URL(logs.url).searchParams.get('order')).toBe('desc')
    expect(screen.getByRole('link', { name: '在日志页查看' })).toHaveAttribute('href', '/logs/task')
    expect(network.find('GET', '/api/pipelines/history-1/screenshots')).toHaveLength(1)

    expect(await screen.findByRole('img', { name: /流水线截图 1/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /查看流水线截图 1/ }))
    const viewer = await screen.findByRole('dialog', { name: /流水线截图 1/ })
    expect(within(viewer).getByRole('img')).toHaveAttribute('src')
  })

  it('opens a deep link that is not on the first history page', async () => {
    installTaskApi({ historyPages: twoPages })
    renderTaskArea('/tasks/history/history-1')

    expect(await screen.findByRole('region', { name: completedDetail.title })).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: '历史' })).not.toBeInTheDocument()
  })

  it('reports a pipeline that no longer exists', async () => {
    installTaskApi()
    renderTaskArea('/tasks/history/missing')

    expect(await screen.findByText(/读取流水线失败/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('cancels a running pipeline and retries a failed one from the action bar (T12)', async () => {
    const network = installTaskApi({
      details: {
        'pipeline-running': { id: 'pipeline-running', title: '运行中的流水线', status: 'running', source: 'manual', priority: 0, task_count: 1, tasks: [] },
        'pipeline-failed': { id: 'pipeline-failed', title: '失败的流水线', status: 'failed', source: 'scheduled', priority: 2, task_count: 1, error: { code: 'TASK_FAILED', message: '刷理智失败' }, tasks: [] },
        'pipeline-retry': { id: 'pipeline-retry', title: '失败的流水线', status: 'pending', source: 'manual', priority: 2, task_count: 1, tasks: [] },
      },
    })
    const { router } = renderTaskArea('/tasks/history/pipeline-running')

    fireEvent.click(await screen.findByRole('button', { name: '取消流水线' }))
    const cancel = await screen.findByRole('alertdialog', { name: '取消这条流水线？' })
    expect(network.find('DELETE', '/api/pipelines/pipeline-running')).toHaveLength(0)
    fireEvent.click(within(cancel).getByRole('button', { name: '取消流水线' }))
    await waitFor(() => expect(network.find('DELETE', '/api/pipelines/pipeline-running')).toHaveLength(1))
    expect(await screen.findByText('已发送取消请求。')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument()

    await act(async () => router.navigate('/tasks/history/pipeline-failed'))
    expect(await screen.findByText('刷理智失败')).toBeInTheDocument()
    expect(screen.queryByText('已发送取消请求。')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '取消流水线' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    const retry = await screen.findByRole('alertdialog', { name: '重新执行这条流水线？' })
    fireEvent.click(within(retry).getByRole('button', { name: '重新加入队列' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/history/pipeline-retry'))
    expect(network.find('POST', '/api/pipelines/pipeline-failed/retry')).toHaveLength(1)
    expect(await screen.findByText('等待执行')).toBeInTheDocument()
  })

  it('keeps the failed pipeline open and explains why a retry was refused', async () => {
    installTaskApi({
      retryStatus: 409,
      details: { 'pipeline-failed': { id: 'pipeline-failed', title: '失败的流水线', status: 'failed', source: 'manual', priority: 0, task_count: 1, tasks: [] } },
    })
    const { router } = renderTaskArea('/tasks/history/pipeline-failed')

    fireEvent.click(await screen.findByRole('button', { name: '重试' }))
    fireEvent.click(within(await screen.findByRole('alertdialog', { name: '重新执行这条流水线？' })).getByRole('button', { name: '重新加入队列' }))

    await waitFor(() => expect(screen.queryAllByRole('alert').some((node) => node.textContent?.includes('重试失败：队列已暂停'))).toBe(true))
    expect(router.state.location.pathname).toBe('/tasks/history/pipeline-failed')
  })
})
