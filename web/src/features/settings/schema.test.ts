import { describe, expect, it } from 'vitest'
import {
  MASK,
  hotActionLabel,
  initialInputValue,
  isFieldChanged,
  parseFieldValue,
  settingEntries,
  settingSchema,
  sourceLabel,
  type SettingField,
} from './schema'
import { fieldsForSection, findSection, groupFields, hasUnmappedGroups, sectionForGroup } from './sections'

function field(overrides: Partial<SettingField> = {}): SettingField {
  return {
    key: 'test.key',
    label: '测试项',
    group: '设备',
    type: 'string',
    options: [],
    sensitive: false,
    readonly: false,
    hotAction: 'none',
    ...overrides,
  }
}

describe('parseFieldValue', () => {
  it('rejects empty, non-finite and non-integer numbers and enforces the range', () => {
    const integer = field({ type: 'integer', minimum: 1, maximum: 95 })
    expect(parseFieldValue(integer, '')).toEqual({ error: '请填写数值。' })
    expect(parseFieldValue(integer, '   ')).toEqual({ error: '请填写数值。' })
    expect(parseFieldValue(integer, 'abc')).toEqual({ error: '请输入有效数值。' })
    expect(parseFieldValue(integer, 'Infinity')).toEqual({ error: '请输入有效数值。' })
    expect(parseFieldValue(integer, '2.5')).toEqual({ error: '请输入整数。' })
    expect(parseFieldValue(integer, '0')).toEqual({ error: '请填写不小于 1 的值。' })
    expect(parseFieldValue(integer, '96')).toEqual({ error: '请填写不大于 95 的值。' })
    expect(parseFieldValue(integer, ' 25 ')).toEqual({ value: 25 })
    expect(parseFieldValue(integer, 30)).toEqual({ value: 30 })
  })

  it('accepts decimals for number fields', () => {
    const number = field({ type: 'number', minimum: 0.05, maximum: 60 })
    expect(parseFieldValue(number, '0.5')).toEqual({ value: 0.5 })
    expect(parseFieldValue(number, '0.01')).toEqual({ error: '请填写不小于 0.05 的值。' })
    expect(parseFieldValue(number, '61')).toEqual({ error: '请填写不大于 60 的值。' })
  })

  it('validates every element of an integer array', () => {
    const ports = field({ type: 'integer[]', minimum: 1, maximum: 65535 })
    expect(parseFieldValue(ports, [5555, 16384])).toEqual({ value: [5555, 16384] })
    expect(parseFieldValue(ports, '5555, 5556')).toEqual({ value: [5555, 5556] })
    expect(parseFieldValue(ports, [])).toEqual({ error: '请至少添加一个整数。' })
    expect(parseFieldValue(ports, '')).toEqual({ error: '请至少添加一个整数。' })
    expect(parseFieldValue(ports, [5555, 1.5])).toEqual({ error: '请把每一项都填成整数。' })
    expect(parseFieldValue(ports, '5555,,5556')).toEqual({ error: '请把每一项都填成整数。' })
    expect(parseFieldValue(ports, [0])).toEqual({ error: '请填写不小于 1 的值。' })
    expect(parseFieldValue(ports, [70000])).toEqual({ error: '请填写不大于 65535 的值。' })
    expect(parseFieldValue(ports, [5555, 5555])).toEqual({ error: '请不要添加重复的值。' })
  })

  it('submits string arrays as arrays of trimmed, unique entries and allows an empty list', () => {
    const origins = field({ type: 'string[]' })
    expect(parseFieldValue(origins, [' https://a.example ', 'https://b.example'])).toEqual({ value: ['https://a.example', 'https://b.example'] })
    expect(parseFieldValue(origins, [])).toEqual({ value: [] })
    expect(parseFieldValue(origins, ['https://a.example', ' '])).toEqual({ error: '请不要添加空白项。' })
    expect(parseFieldValue(origins, ['https://a.example', 'https://a.example'])).toEqual({ error: '请不要添加重复的值。' })
  })

  it('normalizes booleans and rejects values outside the enum', () => {
    expect(parseFieldValue(field({ type: 'boolean' }), false)).toEqual({ value: false })
    expect(parseFieldValue(field({ type: 'boolean' }), 'true')).toEqual({ value: true })
    const level = field({ options: ['INF', 'DBG'] })
    expect(parseFieldValue(level, 'DBG')).toEqual({ value: 'DBG' })
    expect(parseFieldValue(level, 'VERBOSE')).toEqual({ error: '请选择列表中的有效选项。' })
    expect(parseFieldValue(field(), '  127.0.0.1:5555 ')).toEqual({ value: '127.0.0.1:5555' })
  })
})

describe('setting payloads', () => {
  const fields = settingSchema({ items: [
    { key: 'adb.address', label: '设备地址', group: '设备', type: 'string', hot_action: 'reconnect' },
    { key: 'llm.api_key', label: 'LLM API 密钥', group: 'LLM', type: 'string', sensitive: true },
    { key: 'app.access_token', label: '访问密钥', group: '应用', type: 'string', sensitive: true, readonly: true },
    { key: 'log.core_min_level', label: '内核日志最低级别', group: '日志', type: 'string', enum: ['INF', 'DBG'] },
  ] })

  it('reads the schema with defaults for missing metadata', () => {
    expect(fields[0]).toMatchObject({ key: 'adb.address', group: '设备', hotAction: 'reconnect', sensitive: false, readonly: false, options: [] })
    expect(fields[2]).toMatchObject({ sensitive: true, readonly: true, hotAction: 'none' })
    expect(fields[3]?.options).toEqual(['INF', 'DBG'])
  })

  it('never keeps a returned secret and tells configured secrets from unset ones', () => {
    const values = settingEntries({ items: [
      { key: 'adb.address', value: '127.0.0.1:5555', source: 'db' },
      { key: 'llm.api_key', value: 'fixture-real-secret', source: 'env' },
      { key: 'app.access_token', value: MASK, source: 'default' },
    ] }, fields)
    expect(values['adb.address']).toMatchObject({ value: '127.0.0.1:5555', source: 'db' })
    expect(values['llm.api_key']).toMatchObject({ value: MASK, configured: true, masked: false })
    expect(values['app.access_token']).toMatchObject({ value: '', configured: false })
    expect(JSON.stringify(values)).not.toContain('fixture-real-secret')
  })

  it('accepts the map form of the settings payload', () => {
    const values = settingEntries({ items: { 'adb.address': { value: '10.0.0.2:5555', source: 'yaml' } } }, fields)
    expect(values['adb.address']).toMatchObject({ value: '10.0.0.2:5555', source: 'yaml' })
  })

  it('describes sources and hot actions without raw identifiers', () => {
    expect(sourceLabel('db')).toBe('数据库覆盖')
    expect(sourceLabel('yaml')).toBe('配置文件')
    expect(sourceLabel('env')).toBe('环境变量')
    expect(sourceLabel('default')).toBe('默认值')
    expect(sourceLabel('unknown')).toBe('来源未知')
    expect(hotActionLabel('none')).toBe('保存后立即生效')
    expect(hotActionLabel('reconnect')).toBe('保存后会重新连接设备')
    expect(hotActionLabel('restart_required')).toBe('重启服务后生效')
  })

  it('counts a field as changed only when its parsed value differs from the server value', () => {
    const quality = field({ type: 'integer', minimum: 1, maximum: 95 })
    const state = { value: 25, source: 'default', configured: true, masked: false }
    expect(initialInputValue(quality, state)).toBe('25')
    expect(isFieldChanged(quality, undefined, state)).toBe(false)
    expect(isFieldChanged(quality, '25', state)).toBe(false)
    expect(isFieldChanged(quality, '25.0', state)).toBe(false)
    expect(isFieldChanged(quality, '30', state)).toBe(true)
    expect(isFieldChanged(quality, '', state)).toBe(true)
    const secret = field({ sensitive: true })
    expect(isFieldChanged(secret, '', { value: MASK, source: 'env', configured: true, masked: true })).toBe(false)
    expect(isFieldChanged(secret, 'new-key', { value: MASK, source: 'env', configured: true, masked: true })).toBe(true)
    const ports = field({ type: 'integer[]', minimum: 1, maximum: 65535 })
    expect(isFieldChanged(ports, [5555], { value: [5555], source: 'default', configured: true, masked: false })).toBe(false)
    expect(isFieldChanged(ports, [5555, 5556], { value: [5555], source: 'default', configured: true, masked: false })).toBe(true)
  })
})

describe('settings sections', () => {
  it('maps schema groups to sections and sends unknown groups to other', () => {
    expect(sectionForGroup('设备')).toBe('device')
    expect(sectionForGroup('渠道')).toBe('device')
    expect(sectionForGroup('LLM')).toBe('agent')
    expect(sectionForGroup('Agent')).toBe('agent')
    expect(sectionForGroup('外部数据源')).toBe('sources')
    expect(sectionForGroup('更新')).toBe('maintenance')
    expect(sectionForGroup('日志')).toBe('maintenance')
    expect(sectionForGroup('应用')).toBe('system')
    expect(sectionForGroup('部署')).toBe('system')
    expect(sectionForGroup('实验')).toBe('other')
    expect(sectionForGroup('constructor')).toBe('other')
  })

  it('finds known sections only', () => {
    expect(findSection('device')?.title).toBe('设备与渠道')
    expect(findSection('appearance')?.title).toBe('外观')
    expect(findSection('sources')?.title).toBe('外部数据源')
    expect(findSection('nope')).toBeUndefined()
    expect(findSection(undefined)).toBeUndefined()
  })

  it('keeps the M25 external-source keys in their own section', () => {
    const fields = [
      field({ key: 'sources.prts_enabled', group: '外部数据源' }),
      field({ key: 'app.proxy', group: '应用' }),
      field({ key: 'sources.cache_max_mb', group: '外部数据源' }),
    ]
    expect(fieldsForSection(fields, 'sources').map((item) => item.key)).toEqual(['sources.prts_enabled', 'sources.cache_max_mb'])
    expect(hasUnmappedGroups(fields)).toBe(false)
  })

  it('keeps the schema order of groups inside a section', () => {
    const fields = [
      field({ key: 'channel.server', group: '渠道' }),
      field({ key: 'adb.address', group: '设备' }),
      field({ key: 'llm.model', group: 'LLM' }),
      field({ key: 'adb.path', group: '设备' }),
    ]
    const device = fieldsForSection(fields, 'device')
    expect(groupFields(device).map((entry) => [entry.group, entry.fields.map((item) => item.key)])).toEqual([
      ['渠道', ['channel.server']],
      ['设备', ['adb.address', 'adb.path']],
    ])
    expect(hasUnmappedGroups(fields)).toBe(false)
    expect(hasUnmappedGroups([...fields, field({ group: '实验' })])).toBe(true)
  })
})
