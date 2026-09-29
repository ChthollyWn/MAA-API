import { describe, expect, it } from 'vitest'
import {
  FULLTEXT_KINDS,
  KIND_LABELS,
  MAX_BODY_CHARS,
  MAX_ENABLED_ENTRIES,
  MAX_FULLTEXT_CHARS,
  MAX_TITLE_CHARS,
  MAX_WHEN_TO_USE_CHARS,
  describeKnowledgeError,
  emptyKnowledgeForm,
  formatUpdatedAt,
  groupByKind,
  indexLine,
  knowledgeEntryPath,
  knowledgeFormFromDetail,
  knowledgeLimits,
  parseUtcTimestamp,
  serverFieldErrors,
  sourceBadge,
  validateKnowledgeForm,
  type KnowledgeForm,
} from './format'

function form(overrides: Partial<KnowledgeForm> = {}): KnowledgeForm {
  return { ...emptyKnowledgeForm(), id: 'my-note', title: '我的笔记', whenToUse: '用户问起时', body: '正文', ...overrides }
}

const NO_OTHERS = { otherEnabledEntries: 0, otherFulltextChars: 0 }

describe('knowledge entry paths and kinds', () => {
  it('builds the settings paths the router serves', () => {
    expect(knowledgeEntryPath('glossary')).toBe('/more/settings/knowledge/glossary')
    expect(knowledgeEntryPath('a/b')).toBe('/more/settings/knowledge/a%2Fb')
  })

  it('labels every kind and groups entries in the fixed order', () => {
    const entries = [
      { id: 'profile', kind: 'profile' },
      { id: 'playbook', kind: 'playbook' },
      { id: 'glossary', kind: 'glossary' },
      { id: 'screen', kind: 'screen' },
      { id: 'rule', kind: 'rule' },
      { id: 'unknown', kind: 'mystery' },
    ]
    const groups = groupByKind(entries)
    expect(groups.map((group) => group.kind)).toEqual(['playbook', 'screen', 'rule', 'glossary', 'profile', 'other'])
    expect(groups.map((group) => group.label)).toEqual(['操作剧本', '界面知识', '规则', '术语表', '用户档案', '其他'])
    expect(groups.map((group) => group.items.map((item) => item.id))).toEqual([
      ['playbook'],
      ['screen'],
      ['rule'],
      ['glossary'],
      ['profile'],
      ['unknown'],
    ])
    // 认不出的类型不静默丢掉，落在末尾的「其他」组里。
    expect(groups).toHaveLength(6)
    expect(groups[5]).toMatchObject({ kind: 'other', label: '其他' })
    expect(groups[5]?.items.map((item) => item.id)).toEqual(['unknown'])
    expect(KIND_LABELS.playbook).toBe('操作剧本')
    expect(FULLTEXT_KINDS).toEqual(['glossary', 'profile'])
  })
})

describe('source badges', () => {
  it('reads 内置 / 已修改 / 自定义 out of source and modified', () => {
    expect(sourceBadge({ source: 'builtin', modified: false })).toMatchObject({ label: '内置', tone: 'neutral' })
    expect(sourceBadge({ source: 'builtin', modified: true })).toMatchObject({ label: '已修改', tone: 'warning' })
    expect(sourceBadge({ source: 'custom', modified: false })).toMatchObject({ label: '自定义', tone: 'primary' })
    // 接口升级后多出的来源不崩，按自定义显示。
    expect(sourceBadge({ source: 'plugin', modified: false }).label).toBe('自定义')
  })
})

describe('index preview', () => {
  it('renders the exact line the runtime injects', () => {
    expect(indexLine({ id: 'account-resources', title: '查询账号资源', when_to_use: '用户问抽卡资源时' }))
      .toBe('- account-resources｜查询账号资源：用户问抽卡资源时')
  })
})

describe('form helpers', () => {
  it('seeds a new entry with an empty form and 启用', () => {
    expect(emptyKnowledgeForm()).toEqual({ id: '', kind: 'playbook', title: '', whenToUse: '', body: '', enabled: true })
  })

  it('reads a detail payload into the form fields', () => {
    const detail = {
      id: 'screen-home',
      kind: 'screen',
      title: '首页布局',
      when_to_use: '需要在游戏首页读取货币时',
      body: '正文',
      enabled: false,
      source: 'builtin',
      modified: true,
      updated_at: null,
    }
    expect(knowledgeFormFromDetail(detail)).toEqual({
      id: 'screen-home',
      kind: 'screen',
      title: '首页布局',
      whenToUse: '需要在游戏首页读取货币时',
      body: '正文',
      enabled: false,
    })
  })
})

describe('limits', () => {
  it('caps title, when_to_use and body with readable messages', () => {
   expect(validateKnowledgeForm(form({ title: 'x'.repeat(MAX_TITLE_CHARS + 1) }), NO_OTHERS, { isNew: false }).title)
      .toBe(`标题最多 ${MAX_TITLE_CHARS} 字，现在 ${MAX_TITLE_CHARS + 1} 字。`)
    expect(validateKnowledgeForm(form({ whenToUse: '' }), NO_OTHERS, { isNew: false }).whenToUse).toBe('什么时候用不能为空。')
    expect(validateKnowledgeForm(form({ whenToUse: 'x'.repeat(MAX_WHEN_TO_USE_CHARS + 1) }), NO_OTHERS, { isNew: false }).whenToUse)
      .toBe(`什么时候用最多 ${MAX_WHEN_TO_USE_CHARS} 字，现在 ${MAX_WHEN_TO_USE_CHARS + 1} 字。`)
    expect(validateKnowledgeForm(form({ body: '' }), NO_OTHERS, { isNew: false }).body).toBe('正文不能为空。')
    expect(validateKnowledgeForm(form({ body: 'x'.repeat(MAX_BODY_CHARS + 1) }), NO_OTHERS, { isNew: false }).body)
      .toBe(`正文最多 ${MAX_BODY_CHARS} 字，现在 ${MAX_BODY_CHARS + 1} 字。`)
    expect(validateKnowledgeForm(form(), NO_OTHERS, { isNew: false })).toEqual({})
  })

  it('checks the identifier pattern only when creating', () => {
    expect(validateKnowledgeForm(form({ id: '' }), NO_OTHERS, { isNew: true }).id).toBe('标识不能为空。')
    expect(validateKnowledgeForm(form({ id: 'Bad_ID' }), NO_OTHERS, { isNew: true }).id).toBe('标识用 2–48 位小写字母、数字或连字符，并以字母或数字开头。')
    expect(validateKnowledgeForm(form({ id: 'a' }), NO_OTHERS, { isNew: true }).id).toBe('标识用 2–48 位小写字母、数字或连字符，并以字母或数字开头。')
    expect(validateKnowledgeForm(form({ id: 'bad-id' }), NO_OTHERS, { isNew: true })).toEqual({})
    // 已有的条目不该因为标识被改而被拦下：UI 上它本来就只读。
    expect(validateKnowledgeForm(form({ id: '' }), NO_OTHERS, { isNew: false }).id).toBeUndefined()
  })

  it('refuses to enable the 51st entry and points at the count', () => {
    const errors = validateKnowledgeForm(form({ enabled: true }), { otherEnabledEntries: MAX_ENABLED_ENTRIES, otherFulltextChars: 0 }, { isNew: true })
    expect(errors.enabled).toBe(`启用的知识条目不能超过 ${MAX_ENABLED_ENTRIES} 条；先停用一条再启用。`)
    // 停用时不再受限。
    expect(validateKnowledgeForm(form({ enabled: false }), { otherEnabledEntries: MAX_ENABLED_ENTRIES, otherFulltextChars: 0 }, { isNew: true }).enabled).toBeUndefined()
  })

  it('counts glossary + profile bodies together', () => {
    const limits = { otherEnabledEntries: 1, otherFulltextChars: MAX_FULLTEXT_CHARS - 10 }
    const over = validateKnowledgeForm(form({ kind: 'glossary', body: 'x'.repeat(11) }), limits, { isNew: false })
    expect(over.fulltext).toBe(`术语表与用户档案的正文合计最多 ${MAX_FULLTEXT_CHARS} 字，现在 ${MAX_FULLTEXT_CHARS + 1} 字。`)
    // 恰好到上限可以通过。
    expect(validateKnowledgeForm(form({ kind: 'glossary', body: 'x'.repeat(10) }), limits, { isNew: false }).fulltext).toBeUndefined()
    // 停用后不计入，也不受限。
    expect(validateKnowledgeForm(form({ kind: 'glossary', body: 'x'.repeat(500) , enabled: false }), limits, { isNew: false }).fulltext).toBeUndefined()
    // 其他类型不参与这条合计。
    expect(validateKnowledgeForm(form({ kind: 'playbook', body: 'x'.repeat(500) }), limits, { isNew: false }).fulltext).toBeUndefined()
  })

  it('summarizes the limits from the list payload, excluding the entry being edited', () => {
    const entries = [
      { id: 'glossary', kind: 'glossary', enabled: true, body_chars: 900 },
      { id: 'profile', kind: 'profile', enabled: true, body_chars: 400 },
      { id: 'screen-home', kind: 'screen', enabled: true, body_chars: 800 },
      { id: 'off-rule', kind: 'rule', enabled: false, body_chars: 100 },
      { id: 'off-profile', kind: 'profile', enabled: false, body_chars: 700 },
    ]
    expect(knowledgeLimits(entries, 'glossary')).toEqual({ otherEnabledEntries: 2, otherFulltextChars: 400 })
    expect(knowledgeLimits(entries, null)).toEqual({ otherEnabledEntries: 3, otherFulltextChars: 1300 })
    expect(knowledgeLimits([], null)).toEqual({ otherEnabledEntries: 0, otherFulltextChars: 0 })
  })
})

describe('timestamps', () => {
  it('reads the naive UTC strings the API returns as UTC, not local time', () => {
    expect(parseUtcTimestamp('2026-09-29T02:00:00')?.toISOString()).toBe('2026-09-29T02:00:00.000Z')
    expect(parseUtcTimestamp('2026-09-29T02:00:00Z')?.toISOString()).toBe('2026-09-29T02:00:00.000Z')
    expect(parseUtcTimestamp('2026-09-29T10:00:00+08:00')?.toISOString()).toBe('2026-09-29T02:00:00.000Z')
    expect(parseUtcTimestamp('')).toBeNull()
    expect(parseUtcTimestamp('not-a-time')).toBeNull()
    expect(formatUpdatedAt('2026-09-29T02:00:00')).toBe(formatUpdatedAt('2026-09-29T02:00:00Z'))
    expect(formatUpdatedAt(null)).toBeNull()
  })
})

describe('error text', () => {
  it('reads the two 409 codes even when the server message is generic', () => {
    expect(describeKnowledgeError({ error: { code: 'KNOWLEDGE_LIMIT_EXCEEDED', message: '与当前状态冲突' } }))
      .toContain('50 条')
    expect(describeKnowledgeError({ error: { code: 'KNOWLEDGE_LIMIT_EXCEEDED', message: '与当前状态冲突' } }))
      .toContain('3000 字')
    expect(describeKnowledgeError({ error: { code: 'KNOWLEDGE_BUILTIN_READONLY', message: '与当前状态冲突' } }))
      .toContain('内置条目')
    expect(describeKnowledgeError({ error: { code: 'KNOWLEDGE_NOT_FOUND', message: '资源不存在' } })).toContain('不存在')
  })

  it('prefers the specific message the backend sends', () => {
    expect(describeKnowledgeError({ error: { code: 'KNOWLEDGE_LIMIT_EXCEEDED', message: '启用的知识条目不能超过 50 条' } }))
      .toBe('启用的知识条目不能超过 50 条')
  })

  it('falls back to a readable line for connection failures', () => {
    expect(describeKnowledgeError(new TypeError('Failed to fetch'))).toBe('无法连接服务，请检查网络后重试。')
    expect(describeKnowledgeError(undefined)).toBe('无法连接服务，请检查网络后重试。')
  })

  it('maps a 422 field list onto the form fields', () => {
    const errors = serverFieldErrors({
      error: {
        code: 'VALIDATION_ERROR',
        message: '请求参数校验失败（2 处）',
        details: {
          fields: [
            { loc: ['body', 'title'], msg: 'String should have at most 32 characters', type: 'string_too_long' },
            { loc: ['path', 'entry_id'], msg: 'String should match pattern', type: 'string_pattern_mismatch' },
          ],
        },
      },
    })
    expect(errors.title).toBe('服务端拒绝了「标题」：String should have at most 32 characters')
    expect(errors.id).toBe('服务端拒绝了「标识」：String should match pattern')
    expect(serverFieldErrors(new Error('offline'))).toEqual({})
  })
})
