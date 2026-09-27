import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { installTaskApi, renderTaskArea } from './test-utils'

async function openPendingMenu(title: string) {
  const pending = await screen.findByRole('region', { name: /待执行/ })
  fireEvent.click(within(pending).getByRole('button', { name: `「${title}」的更多操作` }))
  return screen.findByRole('dialog', { name: title })
}

describe('task queue', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the running and pending counts and pauses then resumes the queue (T12)', async () => {
    const network = installTaskApi()
    renderTaskArea('/tasks/queue')

    expect(await screen.findByText('队列运行中')).toBeInTheDocument()
    expect(screen.getByText('运行中 1 · 待执行 2')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '暂停队列' }))

    expect(await screen.findByRole('button', { name: '恢复队列' })).toBeInTheDocument()
    expect(screen.getByText('队列已暂停')).toBeInTheDocument()
    expect(network.find('POST', '/api/queue/pause')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '恢复队列' }))

    expect(await screen.findByRole('button', { name: '暂停队列' })).toBeInTheDocument()
    expect(network.find('POST', '/api/queue/resume')).toHaveLength(1)
  })

  it('pins the running pipeline with its source and start time and stops it only after confirmation', async () => {
    const network = installTaskApi({ current: { pipeline: { id: 'running-1', status: 'running', started_at: '2026-09-23T12:00:00Z' } } })
    renderTaskArea('/tasks/queue')

    const running = await screen.findByRole('region', { name: '正在运行' })
    expect(await within(running).findByText('日常流水线')).toBeInTheDocument()
    expect(within(running).getByText(/手动/)).toBeInTheDocument()
    expect(await within(running).findByText(/开始于/)).toBeInTheDocument()
    expect(within(running).getByRole('link', { name: '查看详情' })).toHaveAttribute('href', '/tasks/history/running-1')

    fireEvent.click(within(running).getByRole('button', { name: '停止' }))
    const dialog = await screen.findByRole('alertdialog', { name: '停止当前流水线？' })
    expect(network.find('DELETE', '/api/pipelines/running-1')).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: '停止流水线' }))
    await waitFor(() => expect(network.find('DELETE', '/api/pipelines/running-1')).toHaveLength(1))
  })

  it('lists pending pipelines in server order with source, priority and status and cancels one after confirmation (T12)', async () => {
    const network = installTaskApi()
    renderTaskArea('/tasks/queue')

    const pending = await screen.findByRole('region', { name: /待执行/ })
    const rows = await within(pending).findAllByRole('listitem')
    expect(rows).toHaveLength(2)
    for (const text of ['夜间定时', '定时 · 优先级 2（最低）', '等待执行']) expect(rows[0]).toHaveTextContent(text)
    for (const text of ['Agent 任务', 'Agent · 优先级 1（普通）', '等待执行']) expect(rows[1]).toHaveTextContent(text)
    expect(within(rows[0]).getByRole('link', { name: /夜间定时/ })).toHaveAttribute('href', '/tasks/history/queued-1')

    fireEvent.click(within(await openPendingMenu('夜间定时')).getByRole('button', { name: '取消' }))
    const dialog = await screen.findByRole('alertdialog', { name: '取消「夜间定时」？' })
    expect(network.find('DELETE', '/api/pipelines/queued-1')).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: '取消这条' }))
    await waitFor(() => expect(network.find('DELETE', '/api/pipelines/queued-1')).toHaveLength(1))
  })

  it('raises the target before stopping the current pipeline and explains the result in place (T8)', async () => {
    const network = installTaskApi()
    renderTaskArea('/tasks/queue')

    fireEvent.click(within(await openPendingMenu('夜间定时')).getByRole('button', { name: '停止当前并执行此条' }))
    const dialog = await screen.findByRole('alertdialog', { name: '停止当前并执行此条？' })
    expect(dialog).toHaveTextContent('不能保证目标紧接当前项执行')
    fireEvent.click(within(dialog).getByRole('button', { name: '停止并执行此条' }))

    expect(await screen.findByText('目标已调至优先级 0，当前流水线已请求取消；队列按显示顺序继续调度。')).toBeInTheDocument()
    const patchIndex = network.requests.findIndex((request) => request.method === 'PATCH' && request.url.endsWith('/api/queue/queued-1'))
    const cancelIndex = network.requests.findIndex((request) => request.method === 'DELETE' && request.url.endsWith('/api/pipelines/running-1'))
    expect(patchIndex).toBeGreaterThanOrEqual(0)
    expect(cancelIndex).toBeGreaterThan(patchIndex)
    expect(await network.requests[patchIndex].clone().json()).toEqual({ priority: 0 })
  })

  it('cannot jump ahead when nothing is running', async () => {
    installTaskApi({ queue: { running: null } })
    renderTaskArea('/tasks/queue')

    expect(await screen.findByText('当前没有运行中的流水线')).toBeInTheDocument()
    expect(within(await openPendingMenu('夜间定时')).getByRole('button', { name: '停止当前并执行此条' })).toBeDisabled()
  })

  it('clears every pending pipeline after a confirmation that states the count (T12)', async () => {
    const network = installTaskApi()
    renderTaskArea('/tasks/queue')

    fireEvent.click(await screen.findByRole('button', { name: '清空待执行' }))
    const dialog = await screen.findByRole('alertdialog', { name: '清空待执行？' })
    expect(dialog).toHaveTextContent('2 条')
    expect(network.find('DELETE', '/api/queue')).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: '清空 2 条待执行' }))

    await waitFor(() => expect(network.find('DELETE', '/api/queue')).toHaveLength(1))
    expect(await screen.findByText('没有待执行的流水线')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '清空待执行' })).toBeDisabled()
  })

  it('polls every five seconds and refreshes on demand', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const network = installTaskApi()
    renderTaskArea('/tasks/queue')

    expect(await screen.findByText('夜间定时')).toBeInTheDocument()
    const initial = network.find('GET', '/api/queue').length
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    await waitFor(() => expect(network.find('GET', '/api/queue').length).toBeGreaterThan(initial))

    const polled = network.find('GET', '/api/queue').length
    fireEvent.click(screen.getByRole('button', { name: '刷新队列' }))
    await waitFor(() => expect(network.find('GET', '/api/queue').length).toBeGreaterThan(polled))
  })
})
