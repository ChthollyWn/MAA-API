import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { useMutation } from '@tanstack/react-query'
import { CopilotGrantBar } from './CopilotGrantBar'
import { normalizeCopilotGrant, revokeCopilotGrant, type CopilotGrantView } from './api'
import { installCopilotsApi, renderWithQuery } from './test-utils'

const NOW = Date.parse('2026-10-10T06:00:00Z')

function grant(overrides: Partial<CopilotGrantView> = {}): CopilotGrantView {
  return {
    grantId: 'grant-1',
    stageId: 'main_01-07',
    stageCode: '1-7',
    attemptsTotal: 3,
    attemptsUsed: 1,
    remainingAttempts: 2,
    success: 'three_star',
    support: 'fill_one',
    status: 'active',
    expiresAt: '2026-10-10T06:45:00Z',
    ...overrides,
  }
}

function RevokeHarness({ sessionId }: { sessionId: string }) {
  const [revoked, setRevoked] = useState(false)
  const revoke = useMutation({
    mutationFn: () => revokeCopilotGrant(sessionId),
    onSuccess: () => setRevoked(true),
  })
  return (
    <>
      <CopilotGrantBar grant={grant()} now={NOW} revoking={revoke.isPending} onRevoke={() => revoke.mutate()} />
      {revoked ? <p>已撤销</p> : null}
      {revoke.error ? <p role="alert">撤销失败</p> : null}
    </>
  )
}

describe('M29 作业授权条', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('显示关卡、剩余次数与剩余时间', () => {
    renderWithQuery(<CopilotGrantBar grant={grant()} now={NOW} revoking={false} onRevoke={() => {}} />)

    const bar = screen.getByTestId('copilot-grant-bar')
    expect(bar).toHaveTextContent('已授权作业尝试')
    expect(bar).toHaveTextContent('关卡 1-7')
    expect(bar).toHaveTextContent('剩余 2/3 次')
    expect(bar).toHaveTextContent('剩余 45 分钟')
    // 成功标准与助战写在悬浮说明里，不挤占这一行的宽度
    expect(bar.querySelector('p')).toHaveAttribute('title', '必须三星 · 允许助战补位')
  })

  it('时间走过有效期后显示已过期，没有次数时显示未知', () => {
    renderWithQuery(
      <CopilotGrantBar
        grant={grant({ expiresAt: '2026-10-10T05:00:00Z', attemptsTotal: 0, remainingAttempts: 0 })}
        now={NOW}
        revoking={false}
        onRevoke={() => {}}
      />,
    )

    expect(screen.getByTestId('copilot-grant-bar')).toHaveTextContent('已过期')
    expect(screen.getByTestId('copilot-grant-bar')).toHaveTextContent('剩余次数未知')
  })

  it('点「撤销」交给调用方处理', () => {
    const onRevoke = vi.fn()
    renderWithQuery(<CopilotGrantBar grant={grant()} now={NOW} revoking={false} onRevoke={onRevoke} />)

    fireEvent.click(screen.getByRole('button', { name: '撤销作业授权' }))
    expect(onRevoke).toHaveBeenCalledTimes(1)
  })

  it('撤销走 DELETE /api/agent/sessions/{id}/copilot-grant', async () => {
    const { find } = installCopilotsApi()
    renderWithQuery(<RevokeHarness sessionId="sess-1" />)

    fireEvent.click(screen.getByRole('button', { name: '撤销作业授权' }))

    await waitFor(() => expect(find('DELETE', '/api/agent/sessions/sess-1/copilot-grant')).toHaveLength(1))
    expect(await screen.findByText('已撤销')).toBeInTheDocument()
  })

  it('会话 wire 的 copilot_grant 转成授权条的输入：只认生效中的授权', () => {
    expect(normalizeCopilotGrant({
      grant_id: 'grant-1',
      stage_id: 'main_01-07',
      stage_code: '1-7',
      attempts_total: 3,
      attempts_used: 2,
      success: 'clear',
      support: 'none',
      status: 'active',
      expires_at: '2026-10-10T06:45:00Z',
    }, NOW)).toMatchObject({ grantId: 'grant-1', remainingAttempts: 1, stageCode: '1-7', success: 'clear' })

    // 结束态、已过期、没有授权、字段读不出：都不显示授权条
    expect(normalizeCopilotGrant({ status: 'exhausted' }, NOW)).toBeNull()
    expect(normalizeCopilotGrant({ status: 'active', expires_at: '2026-10-10T05:00:00Z' }, NOW)).toBeNull()
    expect(normalizeCopilotGrant({ granted: false }, NOW)).toBeNull()
    expect(normalizeCopilotGrant(null, NOW)).toBeNull()

    // 用过次数比总数多时剩余次数收敛到 0
    expect(normalizeCopilotGrant({ attempts_total: 2, attempts_used: 5, status: 'active' }, NOW))
      .toMatchObject({ remainingAttempts: 0 })
  })
})
