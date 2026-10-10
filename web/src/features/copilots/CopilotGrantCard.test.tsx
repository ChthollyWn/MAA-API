import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { ConfirmationCard } from '@/features/agent/ConfirmationCard'
import {
  CopilotGrantCard,
  parseCopilotGrantPayload,
  type CopilotGrantOptions,
  type CopilotGrantRequest,
} from './CopilotGrantCard'
import { jsonResponse, renderWithQuery } from './test-utils'

/** 后端 policy 写进确认 payload 的字段（§9.6）。 */
const GRANT_PAYLOAD = {
  tool_name: 'run_copilot_attempt',
  target_kind: 'copilot',
  session_id: 'sess-1',
  stage_id: 'main_01-07',
  stage_code: '1-7',
  attempts: 3,
  success: 'three_star',
  support: 'fill_one',
  window_minutes: 60,
}

function request(overrides: Partial<CopilotGrantRequest> = {}): CopilotGrantRequest {
  return { ...parseCopilotGrantPayload(GRANT_PAYLOAD), ...overrides }
}

function Harness({ value: initial, disabled = false }: { value?: Partial<CopilotGrantOptions>; disabled?: boolean }) {
  const grant = request(initial)
  const [value, setValue] = useState<CopilotGrantOptions>({
    attempts: grant.attempts,
    success: grant.success,
    support: grant.support,
  })
  return <CopilotGrantCard request={grant} value={value} onChange={setValue} disabled={disabled} />
}

describe('M29 作业授权卡片', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('解析 payload：三项预填值、关卡、有效期都读出来，缺项用默认值', () => {
    expect(parseCopilotGrantPayload(GRANT_PAYLOAD)).toMatchObject({
      attempts: 3,
      success: 'three_star',
      support: 'fill_one',
      sessionId: 'sess-1',
      stageId: 'main_01-07',
      stageCode: '1-7',
      windowMinutes: 60,
      toolName: 'run_copilot_attempt',
    })

    // 没有 payload 字段时退回 arguments 的预填值，再缺就用默认 3 次 / 三星 / 允许助战
    expect(parseCopilotGrantPayload({
      arguments: { grant_attempts: 8, success: 'clear', support: 'none', stage_id: 'main_04-04' },
    })).toMatchObject({ attempts: 8, success: 'clear', support: 'none', stageId: 'main_04-04' })
    expect(parseCopilotGrantPayload({})).toMatchObject({ attempts: 3, success: 'three_star', support: 'fill_one' })

    // 越界与认不出的值一律收敛回合法范围
    expect(parseCopilotGrantPayload({ attempts: 99, success: 'perfect', support: 'maybe' })).toMatchObject({
      attempts: 10,
      success: 'three_star',
      support: 'fill_one',
    })
    expect(parseCopilotGrantPayload({ attempts: 0 })).toMatchObject({ attempts: 1 })

    // 只给秒数时换算成分钟
    expect(parseCopilotGrantPayload({ window_seconds: 1800 })).toMatchObject({ windowMinutes: 30 })
  })

  it('尝试次数可以在 1–10 里选，默认 3 次', async () => {
    render(<Harness />)

    const select = screen.getByLabelText('尝试次数')
    expect(select).toHaveValue('3')
    expect(within(select).getAllByRole('option')).toHaveLength(10)
    expect(within(select).getByRole('option', { name: '1 次' })).toBeInTheDocument()
    expect(within(select).getByRole('option', { name: '10 次' })).toBeInTheDocument()

    fireEvent.change(select, { target: { value: '7' } })
    expect(screen.getByLabelText('尝试次数')).toHaveValue('7')
  })

  it('成功标准在「必须三星」与「通关即可」之间切换', async () => {
    render(<Harness />)

    const group = screen.getByRole('radiogroup', { name: '成功标准' })
    expect(within(group).getByRole('radio', { name: '必须三星' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText('必须三星才算成功；二星与通关不计。')).toBeInTheDocument()

    fireEvent.click(within(group).getByRole('radio', { name: '通关即可' }))

    expect(within(group).getByRole('radio', { name: '通关即可' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText('通关即可：二星或三星都算成功。')).toBeInTheDocument()
  })

  it('助战补位可以关掉', async () => {
    render(<Harness />)

    const toggle = screen.getByRole('switch', { name: /允许助战补位/ })
    expect(toggle).toBeChecked()

    fireEvent.click(toggle)
    expect(toggle).not.toBeChecked()
    expect(screen.getByText(/借不到算一次失败的尝试/)).toBeInTheDocument()
  })

  it('有效期只展示、不可改：文案里带分钟数，页面上没有对应的输入控件', async () => {
    render(<Harness />)

    expect(screen.getByTestId('copilot-grant-window')).toHaveTextContent('有效期 60 分钟')
    expect(screen.getByTestId('copilot-grant-window')).toHaveTextContent('只在开始下一次尝试时检查')

    // 卡片上只有尝试次数一个下拉；有效期没有 select / spinbutton / textbox
    expect(screen.getAllByRole('combobox')).toHaveLength(1)
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.getByTestId('copilot-grant-stage')).toHaveTextContent('关卡 1-7')
  })

  it('end-to-end：确认卡片渲染授权视图，改动三项后批准时带上覆盖参数', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input) => {
      const path = new URL(input instanceof Request ? input.url : String(input)).pathname
      if (path === '/api/confirmations/confirm-1') {
        return jsonResponse({
          confirmation_id: 'confirm-1',
          action: 'grant_copilot_attempts',
          risk_level: 'consume',
          reason: '执行作业尝试需要授权：3 次以内',
          status: 'pending',
          requested_by: 'internal',
          expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
          execution: null,
          payload: GRANT_PAYLOAD,
        })
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found' } }, 404)
    }))
    const onResolve = vi.fn()
    renderWithQuery(<ConfirmationCard confirmationId="confirm-1" busy={null} onResolve={onResolve} />)

    expect(await screen.findByText('作业执行授权')).toBeInTheDocument()
    expect(screen.getByText('执行作业尝试需要授权：3 次以内')).toBeInTheDocument()
    expect(screen.getByTestId('copilot-grant-window')).toHaveTextContent('有效期 60 分钟')
    // 授权卡片自己带控件，不再展开原始参数
    expect(screen.queryByText('查看操作参数')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('尝试次数'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('radio', { name: '通关即可' }))
    fireEvent.click(screen.getByRole('switch', { name: /允许助战补位/ }))
    fireEvent.click(screen.getByRole('button', { name: /批准并继续/ }))

    expect(onResolve).toHaveBeenCalledWith('confirm-1', true, {
      attempts: 5,
      success: 'clear',
      support: 'none',
    })
  })
})
