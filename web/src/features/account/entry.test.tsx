import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { primaryNav, secondaryNav } from '@/components/layout/nav'
import { routeConfig } from '@/routes/router'
import MorePage from '@/routes/more'
import { ACCOUNT_PATHS, accountView } from './AccountPage'
import { jsonResponse } from './test-utils'

vi.mock('@/realtime/RealtimeProvider', () => ({
  useRealtimeStatus: () => ({ status: 'CONNECTED', reconnectAttempt: 0, rtt: null, truncated: false, closeCode: null, error: null }),
}))

describe('M27 账号数据入口', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('「更多」里有账号数据入口，主导航仍是 5 项', async () => {
    expect(primaryNav).toHaveLength(5)
    expect(primaryNav.some((item) => item.to.includes('account'))).toBe(false)
    expect(secondaryNav.find((item) => item.to === ACCOUNT_PATHS.operators)?.label).toBe('账号数据')

    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input) => {
      const request = input instanceof Request ? input : new Request(String(input))
      if (new URL(request.url).pathname === '/api/system/health') {
        return jsonResponse({ auth_enabled: false, status: 'ok', version: 'test' })
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
    }))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/more']}>
          <MorePage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('link', { name: /账号数据/ })).toHaveAttribute('href', '/more/account')
  })

  it('路由注册了干员页与仓库页', () => {
    const children = routeConfig.find((route) => route.path === '/')?.children ?? []
    expect(children.some((route) => route.path === 'more/account')).toBe(true)
    expect(children.some((route) => route.path === 'more/account/depot')).toBe(true)
  })

  it('路径映射到标签页', () => {
    expect(accountView('/more/account')).toBe('operators')
    expect(accountView('/more/account/')).toBe('operators')
    expect(accountView('/more/account/depot')).toBe('depot')
  })
})
