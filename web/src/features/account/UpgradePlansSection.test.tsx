import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { jsonResponse } from './test-utils'
import { UpgradePlansSection } from './UpgradePlansSection'

const ACTIVE_PLAN = {
  id: 'plan-1',
  name: '精二阿米娅',
  status: 'active',
  targets: [{ operator: '阿米娅', elite: 2, skill_level: 7 }],
  requirements: { 固源岩: 12 },
  progress: {
    stages: [{ stage_code: '1-7', runs: 12, sanity: 216, items: [{ item_name: '固源岩' }] }],
    total_sanity: 216,
    days_estimated: 5,
    missing: { 固源岩: 12 },
  },
  schedule_id: 'sched-1',
  session_id: 'sess-1',
  created_at: '2026-10-10T02:00:00',
  updated_at: '2026-10-10T02:00:00',
  completed_at: null,
}

const COMPLETED_PLAN = {
  ...ACTIVE_PLAN,
  id: 'plan-2',
  name: '精二临光',
  status: 'completed',
  targets: [],
  progress: { stages: [], missing: {} },
  schedule_id: null,
  completed_at: '2026-10-09T02:00:00',
}

interface Call {
  path: string
  method: string
}

function renderSection(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <UpgradePlansSection />
    </QueryClientProvider>,
  )
}

/** 记录每次请求，按路径分派；未覆盖的路径回 404，避免测试静默打真网。 */
function installPlanApi(handler: (path: string, method: string) => Response | null) {
  const calls: Call[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const request = input instanceof Request ? input : new Request(String(input))
    const path = new URL(request.url).pathname
    const method = request.method
    calls.push({ path, method })
    return handler(path, method) ?? jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
  })
  return { fetchMock, calls }
}

describe('M31 养成计划区块', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('列出计划的状态、目标、缺失材料、剩余场次与定时任务', async () => {
    const { fetchMock } = installPlanApi((path, method) =>
      path === '/api/account/upgrade-plans' && method === 'GET'
        ? jsonResponse({ items: [ACTIVE_PLAN, COMPLETED_PLAN], total: 2 })
        : null,
    )
    renderSection(fetchMock)

    expect(await screen.findByText('精二阿米娅')).toBeInTheDocument()
    expect(screen.getByText('进行中')).toBeInTheDocument()
    expect(screen.getByText('已完成')).toBeInTheDocument()
    const list = screen.getByLabelText('养成计划列表')
    expect(list).toHaveTextContent('阿米娅')
    expect(screen.getByText('精二 · 技能 7')).toBeInTheDocument()
    expect(screen.getByText('固源岩 ×12')).toBeInTheDocument()
    expect(screen.getByText('1-7 ×12 · 共 12 场')).toBeInTheDocument()
    expect(screen.getByText('预计 5 天 · 共 216 理智')).toBeInTheDocument()
    expect(screen.getByText('sched-1')).toBeInTheDocument()
    // 已完成的计划没有定时任务，也没有取消入口。
    expect(screen.getByText('未创建')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '取消养成计划「精二阿米娅」' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '取消养成计划「精二临光」' })).not.toBeInTheDocument()
  })

  it('没有计划时显示空态', async () => {
    const { fetchMock } = installPlanApi((path, method) =>
      path === '/api/account/upgrade-plans' && method === 'GET' ? jsonResponse({ items: [], total: 0 }) : null,
    )
    renderSection(fetchMock)

    expect(await screen.findByText('还没有养成计划')).toBeInTheDocument()
    expect(screen.queryByLabelText('养成计划列表')).not.toBeInTheDocument()
  })

  it('读取失败时显示错误态，重试会重新发请求', async () => {
    const { fetchMock, calls } = installPlanApi(() =>
      jsonResponse({ error: { code: 'INTERNAL_ERROR', message: '计划存储不可用' } }, 500),
    )
    renderSection(fetchMock)

    expect(await screen.findByText(/计划存储不可用/)).toBeInTheDocument()
    expect(calls.filter((call) => call.method === 'GET')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: /重试/ }))
    await waitFor(() => expect(calls.filter((call) => call.method === 'GET').length).toBeGreaterThan(1))
  })

  it('取消计划要二次确认；先不取消就不发 DELETE', async () => {
    const { fetchMock, calls } = installPlanApi((path, method) =>
      path === '/api/account/upgrade-plans' && method === 'GET'
        ? jsonResponse({ items: [ACTIVE_PLAN], total: 1 })
        : null,
    )
    renderSection(fetchMock)

    fireEvent.click(await screen.findByRole('button', { name: '取消养成计划「精二阿米娅」' }))
    const dialog = await screen.findByRole('alertdialog', { name: '取消养成计划「精二阿米娅」？' })
    expect(dialog).toHaveTextContent('取消后会删掉这条计划的定时任务')
    fireEvent.click(within(dialog).getByRole('button', { name: '先不取消' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    expect(screen.getByText('精二阿米娅')).toBeInTheDocument()
  })

  it('确认取消后发 DELETE 并重新拉列表', async () => {
    let items = [ACTIVE_PLAN]
    const { fetchMock, calls } = installPlanApi((path, method) => {
      if (path === '/api/account/upgrade-plans' && method === 'GET') {
        return jsonResponse({ items, total: items.length })
      }
      if (path === '/api/account/upgrade-plans/plan-1' && method === 'DELETE') {
        items = []
        return jsonResponse({ ...ACTIVE_PLAN, status: 'cancelled' })
      }
      return null
    })
    renderSection(fetchMock)

    fireEvent.click(await screen.findByRole('button', { name: '取消养成计划「精二阿米娅」' }))
    const dialog = await screen.findByRole('alertdialog', { name: '取消养成计划「精二阿米娅」？' })
    fireEvent.click(within(dialog).getByRole('button', { name: '取消计划' }))

    await waitFor(() =>
      expect(calls.filter((call) => call.method === 'DELETE' && call.path === '/api/account/upgrade-plans/plan-1')).toHaveLength(1),
    )
    // 取消成功后失效重取：列表里不再有这条计划。
    expect(await screen.findByText('还没有养成计划')).toBeInTheDocument()
  })

  it('取消失败时把错误显示出来，计划仍在', async () => {
    const { fetchMock } = installPlanApi((path, method) => {
      if (path === '/api/account/upgrade-plans' && method === 'GET') {
        return jsonResponse({ items: [ACTIVE_PLAN], total: 1 })
      }
      if (path === '/api/account/upgrade-plans/plan-1' && method === 'DELETE') {
        return jsonResponse({ error: { code: 'UPGRADE_PLAN_NOT_FOUND', message: '计划不存在或已取消' } }, 404)
      }
      return null
    })
    renderSection(fetchMock)

    fireEvent.click(await screen.findByRole('button', { name: '取消养成计划「精二阿米娅」' }))
    const dialog = await screen.findByRole('alertdialog', { name: '取消养成计划「精二阿米娅」？' })
    fireEvent.click(within(dialog).getByRole('button', { name: '取消计划' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('计划不存在或已取消')
    expect(screen.getByText('精二阿米娅')).toBeInTheDocument()
  })
})
