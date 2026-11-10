import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { keys } from '@/api/keys'
import { toast } from '@/components/ui/toaster'
import { createConsoleApi, json, renderAt, type Json } from '@/features/console/test-utils'
import FeedbackSection from './FeedbackSection'

const FEEDBACK_PATH = '/api/agent/runs/run-1/feedback'

function feedbackItem(overrides: Json = {}): Json {
  return {
    id: 7,
    run_id: 'run-1',
    rating: 4,
    reasons: ['incomplete', 'other'],
    comment: '答复没有引用资料',
    created_at: '2026-11-10T08:00:00Z',
    updated_at: '2026-11-10T08:00:00Z',
    ...overrides,
  }
}

interface FeedbackApiOptions {
  item?: Json | null
  getFailure?: Json
  /** `getFailure` 只失败前几次；省略时每次 GET 都失败。 */
  getFailureTimes?: number
  putFailure?: Json
  /** PUT 挂起，直到测试调用 `releasePut`。 */
  holdPut?: boolean
}

/** 假后端：GET 读当前值，PUT 覆盖当前值；挂起模式用来断言提交中的禁用状态。 */
function feedbackApi(options: FeedbackApiOptions = {}) {
  let stored: Json | null = options.item ?? null
  let getFailuresLeft = options.getFailure ? options.getFailureTimes ?? Number.POSITIVE_INFINITY : 0
  let release: (() => void) | null = null
  const api = createConsoleApi({
    overrides: [({ method, path, body }) => {
      if (path !== FEEDBACK_PATH) return undefined
      if (method === 'GET') {
        if (getFailuresLeft > 0) {
          getFailuresLeft -= 1
          return json(options.getFailure!, 500)
        }
        return json({ item: stored })
      }
      if (method === 'PUT') {
        if (options.putFailure) return json(options.putFailure, 500)
        const input = body as Json
        stored = feedbackItem({
          id: 9,
          rating: input.rating,
          reasons: input.reasons ?? [],
          comment: input.comment ?? null,
          updated_at: '2026-11-10T08:05:00Z',
        })
        if (!options.holdPut) return json(stored)
        return new Promise<Response>((resolve) => {
          release = () => resolve(json(stored))
        }) as unknown as Response
      }
      return undefined
    }],
  })
  return { ...api, releasePut: () => release?.() }
}

function renderSection() {
  return renderAt('/', [{ path: '/', element: <FeedbackSection runId="run-1" /> }])
}

describe('M99 运行评价', () => {
  beforeEach(() => {
    toast.dismiss()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('submits rating and reasons then invalidates the dashboard cache', async () => {
    const api = feedbackApi()
    const { queryClient } = renderSection()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    fireEvent.click(await screen.findByRole('radio', { name: '很好' }))
    fireEvent.click(screen.getByRole('button', { name: '不准确' }))
    fireEvent.click(screen.getByRole('button', { name: '太慢' }))
    fireEvent.change(screen.getByLabelText('补充说明'), { target: { value: '回答里编了一个关卡' } })
    fireEvent.click(screen.getByRole('button', { name: '提交评价' }))

    expect(await screen.findByText('已记录你的评价')).toBeInTheDocument()

    const puts = api.requests.filter((request) => request.method === 'PUT')
    expect(puts).toHaveLength(1)
    expect(puts[0]?.path).toBe(FEEDBACK_PATH)
    expect(puts[0]?.body).toEqual({
      rating: 5,
      reasons: ['inaccurate', 'slow'],
      comment: '回答里编了一个关卡',
    })

    expect(screen.getByText('已评价，提交会覆盖')).toBeInTheDocument()
    await waitFor(() => expect(
      api.requests.filter((request) => request.method === 'GET' && request.path === FEEDBACK_PATH).length,
    ).toBeGreaterThan(1))
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: keys.agent.runFeedback('run-1') }))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: keys.agent.insightsAll() })
  })

  it('prefills an existing rating, reasons and comment', async () => {
    feedbackApi({ item: feedbackItem() })
    renderSection()

    expect(await screen.findByText('已评价，提交会覆盖')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '较好' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('button', { name: '不完整' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '其他' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '不准确' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByLabelText('补充说明')).toHaveValue('答复没有引用资料')
  })

  it('requires a rating before submitting', async () => {
    const api = feedbackApi()
    renderSection()

    fireEvent.click(await screen.findByRole('button', { name: '提交评价' }))

    expect(await screen.findByText('请选择评分后再提交')).toBeInTheDocument()
    expect(api.requests.filter((request) => request.method === 'PUT')).toHaveLength(0)
  })

  it('shows the submit failure without leaving the form', async () => {
    feedbackApi({ putFailure: { error: { code: 'VALIDATION_ERROR', message: '评分超出范围' } } })
    renderSection()

    fireEvent.click(await screen.findByRole('radio', { name: '一般' }))
    fireEvent.click(screen.getByRole('button', { name: '提交评价' }))

    expect(await screen.findByText('提交评价失败：评分超出范围')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '提交评价' })).not.toBeDisabled()
  })

  it('disables submit while the request is pending', async () => {
    const api = feedbackApi({ holdPut: true })
    renderSection()

    fireEvent.click(await screen.findByRole('radio', { name: '一般' }))
    const submitButton = screen.getByRole('button', { name: '提交评价' })
    fireEvent.click(submitButton)

    await waitFor(() => expect(submitButton).toBeDisabled())
    await waitFor(() => expect(api.requests.filter((request) => request.method === 'PUT')).toHaveLength(1))
    await act(async () => { api.releasePut() })

    expect(await screen.findByText('已记录你的评价')).toBeInTheDocument()
    await waitFor(() => expect(submitButton).not.toBeDisabled())
  })

  it('hides the form on a read failure and prefills it after a successful retry', async () => {
    const api = feedbackApi({
      item: feedbackItem(),
      getFailure: { error: { code: 'SERVICE_UNAVAILABLE', message: '反馈服务暂时不可用' } },
      getFailureTimes: 1,
    })
    renderSection()

    expect(await screen.findByText('读取评价失败：反馈服务暂时不可用')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
    // 读取失败时没有可提交的表单，避免提交覆盖用户没看到的评价。
    expect(screen.queryByRole('button', { name: '提交评价' })).not.toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: '较好' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByText('已评价，提交会覆盖')).toBeInTheDocument()
    expect(screen.queryByText('读取评价失败：反馈服务暂时不可用')).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '较好' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('button', { name: '不完整' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '其他' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '不准确' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByLabelText('补充说明')).toHaveValue('答复没有引用资料')
    expect(api.requests.filter((request) => request.method === 'GET' && request.path === FEEDBACK_PATH)).toHaveLength(2)

    // 重试成功后的表单带着服务端值，提交不会用空表单覆盖。
    fireEvent.click(screen.getByRole('button', { name: '提交评价' }))
    await waitFor(() => expect(api.requests.filter((request) => request.method === 'PUT')).toHaveLength(1))
    expect(api.requests.find((request) => request.method === 'PUT')?.body).toEqual({
      rating: 4,
      reasons: ['incomplete', 'other'],
      comment: '答复没有引用资料',
    })
  })
})
