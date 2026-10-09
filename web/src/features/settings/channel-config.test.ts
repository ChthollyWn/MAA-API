import { describe, expect, it } from 'vitest'
import {
  configFromFormValues,
  defaultChannelConfig,
  formValuesFromConfig,
  normalizeChannels,
  parseConfigText,
  validateChannelForm,
  type HeaderRow,
} from './channel-config'

const maskedEmail = {
  server: 'smtp.qq.com',
  port: 465,
  use_tls: true,
  username: 'me@qq.com',
  password: '***',
  to: ['a@example.com'],
}

describe('channel defaults', () => {
  it('follow the backend config models', () => {
    expect(defaultChannelConfig('email')).toEqual({ server: '', use_tls: true, username: '', password: '', to: [] })
    expect(defaultChannelConfig('webhook')).toEqual({ url: '', method: 'POST', headers: {}, body_template: '{title}\n{body}' })
    expect(defaultChannelConfig('bark')).toEqual({ server: 'https://api.day.app', device_key: '' })
    expect(defaultChannelConfig('dingtalk')).toEqual({ webhook: '', secret: '', at_mobiles: [] })
    expect(defaultChannelConfig('wecom')).toEqual({ webhook: '', mentioned_list: [] })
    expect(defaultChannelConfig('feishu')).toEqual({ webhook: '', secret: '', at_user_ids: [] })
  })
})

describe('form values and config', () => {
  it('round-trips a masked email config and keeps the masked password for the server to merge', () => {
    const values = formValuesFromConfig('email', maskedEmail)
    expect(values.password).toBe('')
    expect(values.port).toBe('465')
    expect(configFromFormValues('email', values, maskedEmail)).toEqual(maskedEmail)
  })

  it('sends a newly typed secret instead of the mask', () => {
    const values = { ...formValuesFromConfig('email', maskedEmail), password: 'new-app-password' }
    expect(configFromFormValues('email', values, maskedEmail).password).toBe('new-app-password')
  })

  it('omits optional Bark fields that are left empty and converts the port to a number', () => {
    const bark = { ...formValuesFromConfig('bark', defaultChannelConfig('bark')), device_key: ' abc123 ', group: 'MAA' }
    expect(configFromFormValues('bark', bark, defaultChannelConfig('bark'))).toEqual({
      server: 'https://api.day.app',
      device_key: 'abc123',
      group: 'MAA',
    })
    const email = { ...formValuesFromConfig('email', defaultChannelConfig('email')), port: '587' }
    expect(configFromFormValues('email', email, defaultChannelConfig('email')).port).toBe(587)
  })

  it('turns header rows into an object and keeps keys the form does not know', () => {
    const source = { url: 'https://example.com/hook?token=***', method: 'PUT', headers: { Authorization: '***' }, body_template: '{title}', extra: 1 }
    const values = formValuesFromConfig('webhook', source)
    const rows = values.headers as HeaderRow[]
    expect(rows.map(({ name, value }) => [name, value])).toEqual([['Authorization', '***']])
    const next = { ...values, headers: [...rows, { id: 99, name: ' X-Source ', value: ' maa ' }, { id: 100, name: '', value: '' }] }
    expect(configFromFormValues('webhook', next, source)).toEqual({
      url: 'https://example.com/hook?token=***',
      method: 'PUT',
      headers: { Authorization: '***', 'X-Source': 'maa' },
      body_template: '{title}',
      extra: 1,
    })
  })
})

describe('validateChannelForm', () => {
  it('reports required fields and invalid formats per field', () => {
    expect(validateChannelForm('email', formValuesFromConfig('email', defaultChannelConfig('email')), {})).toEqual({
      server: '请填写 SMTP 服务器。',
      port: '请填写端口。',
      to: '请至少添加一个收件人。',
    })
    const badPort = { ...formValuesFromConfig('email', maskedEmail), port: '70000' }
    expect(validateChannelForm('email', badPort, maskedEmail)).toEqual({ port: '请输入 1 ~ 65535 之间的端口号。' })
    const httpDingTalk = { ...formValuesFromConfig('dingtalk', defaultChannelConfig('dingtalk')), webhook: 'http://oapi.dingtalk.com/robot/send' }
    expect(validateChannelForm('dingtalk', httpDingTalk, {})).toEqual({ webhook: '请输入以 https:// 开头的地址。' })
  })

  it('accepts a masked secret that the server will keep, but requires one for a new channel', () => {
    const saved = { server: 'https://api.day.app', device_key: '***' }
    expect(validateChannelForm('bark', formValuesFromConfig('bark', saved), saved)).toEqual({})
    expect(validateChannelForm('bark', formValuesFromConfig('bark', defaultChannelConfig('bark')), defaultChannelConfig('bark'))).toEqual({ device_key: '请填写设备密钥。' })
  })

  it('rejects header rows without a valid name and duplicated names', () => {
    const base = formValuesFromConfig('webhook', { ...defaultChannelConfig('webhook'), url: 'https://example.com' })
    expect(validateChannelForm('webhook', { ...base, headers: [{ id: 1, name: '', value: 'x' }] }, {})).toEqual({ headers: '请填写请求头名称。' })
    expect(validateChannelForm('webhook', { ...base, headers: [{ id: 1, name: 'Bad Name', value: 'x' }] }, {})).toEqual({ headers: '请只用字母、数字和 - 等符号填写请求头名称。' })
    expect(validateChannelForm('webhook', { ...base, headers: [{ id: 1, name: 'X-A', value: '1' }, { id: 2, name: 'x-a', value: '2' }] }, {})).toEqual({ headers: '请不要填写重复的请求头名称。' })
  })
})

describe('raw config text', () => {
  it('accepts JSON objects only', () => {
    expect(parseConfigText('{"device_key":"***"}')).toEqual({ config: { device_key: '***' } })
    expect(parseConfigText('[]')).toEqual({ error: '请填写一个 JSON 对象。' })
    expect(parseConfigText('{"a":')).toEqual({ error: '请输入有效的 JSON，检查括号、引号和逗号。' })
  })
})

describe('normalizeChannels', () => {
  it('reads channels and masks any credential the server left in place', () => {
    const [channel] = normalizeChannels({ items: [{
      id: 'channel-1',
      type: 'webhook',
      name: '告警',
      enabled: false,
      config: { url: 'https://example.com/hook?token=plain-secret', headers: { Authorization: 'Bearer plain-secret' } },
      events: ['pipeline_failed', 7],
      last_status: 'failed',
      last_sent_at: '2026-09-27T06:30:00Z',
      last_error: 'HTTP 500',
    }] })
    expect(channel).toMatchObject({ id: 'channel-1', type: 'webhook', name: '告警', enabled: false, events: ['pipeline_failed'], lastStatus: 'failed', lastError: 'HTTP 500' })
    expect(JSON.stringify(channel?.config)).not.toContain('plain-secret')
  })

  it('leaves message templates untouched so a list toggle can round-trip them', () => {
    const template = 'chat_id=1&token=abc&text={body}'
    const [channel] = normalizeChannels({ items: [{
      id: 'channel-2',
      type: 'webhook',
      name: '机器人',
      config: { url: 'https://example.com/hook', body_template: template },
    }] })
    expect(channel?.config.body_template).toBe(template)
  })
})
