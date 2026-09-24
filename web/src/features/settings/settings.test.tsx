import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import SettingsPage from './SettingsPage'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  api: {
    GET: mocks.get,
    PUT: mocks.put,
    POST: mocks.post,
    DELETE: mocks.delete,
  },
}))

describe('SettingsPage', () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    mocks.get.mockReset()
    mocks.put.mockReset()
    mocks.post.mockReset()
    mocks.delete.mockReset()
    mocks.get.mockImplementation((path: string) => {
      if (path === '/api/settings/schema') {
        return Promise.resolve({ data: { items: [
          { key: 'adb.address', label: '设备地址', group: '设备', type: 'string', hot_action: 'reconnect' },
          { key: 'llm.api_key', label: 'LLM API 密钥', group: 'LLM', type: 'string', sensitive: true, hot_action: 'none' },
        ] } })
      }
      if (path === '/api/settings') {
        return Promise.resolve({ data: { items: {
          'adb.address': { value: '127.0.0.1:5555', source: 'db' },
          'llm.api_key': { value: '***', source: 'env' },
        } } })
      }
      if (path === '/api/notifications/channels') return Promise.resolve({ data: { items: [] } })
      return Promise.resolve({ data: {} })
    })
  })

  it('renders schema-backed settings and keeps masked secrets out of the editor', async () => {
    render(<SettingsPage />)

    const address = await screen.findByLabelText('设备地址')
    await waitFor(() => expect((address as HTMLInputElement).value).toBe('127.0.0.1:5555'))
    expect(document.body.contains(screen.getByText('来源：数据库覆盖'))).toBe(true)

    const secret = screen.getByLabelText('LLM API 密钥') as HTMLInputElement
    expect(secret.type).toBe('password')
    expect(secret.value).toBe('')
    expect(document.body.contains(screen.getByText('已配置，已脱敏'))).toBe(true)
    expect(document.body.textContent).not.toContain('fixture-real-secret')
  })

  it('saves only edited settings and leaves an unchanged masked key untouched', async () => {
    mocks.put.mockResolvedValue({ data: { items: {} } })
    render(<SettingsPage />)

    const address = await screen.findByLabelText('设备地址')
    fireEvent.change(address, { target: { value: '127.0.0.1:5556' } })
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }))

    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(1))
    expect(mocks.put.mock.calls[0]?.[1]?.body).toEqual({ items: { 'adb.address': '127.0.0.1:5556' } })
  })
})
