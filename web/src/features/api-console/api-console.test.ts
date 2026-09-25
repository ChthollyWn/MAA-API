import { describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { ModuleKind, ScriptTarget, transpileModule } from 'typescript'
import * as consoleHelpers from './utils'
import { validateJsonBody } from './json-validation'
import { filterSensitive, HISTORY_MAX_BYTES, makeCurl, responseTone, selectLogs, trimHistory } from './utils'

const future = consoleHelpers as unknown as Record<string, (...args: unknown[]) => unknown>

describe('API console safety and presentation helpers', () => {
  it('removes credential headers and query tokens before persistence', () => {
    expect(filterSensitive({ path: '/api/x?token=secret&stage=1', headers: {
      Authorization: 'secret', 'X-Token': 'secret', Cookie: 'secret', 'X-Api-Key': 'api-key-secret',
      'X-Password': 'password-secret', 'X-Secret': 'secret-value', Accept: 'application/json',
    } })).toEqual({
      path: '/api/x?stage=1', headers: { Accept: 'application/json' },
    })
  })

  it('detects credential header names even when users typed surrounding spaces', () => {
    expect(filterSensitive({ headers: { 'X-Token ': 'padded-secret', ' X-Api-Key': 'padded-key', Accept: 'application/json' } })).toEqual({
      headers: { Accept: 'application/json' },
    })
    const curl = makeCurl({ baseUrl: '', method: 'GET', path: '/api/x', headers: { 'X-Token ': 'padded-secret' }, token: 'session-token' })
    expect(curl).toContain('"X-Token: $MAA_TOKEN"')
    expect(curl).not.toContain('padded-secret')
    expect(curl).not.toContain('session-token')
  })

  it('keeps at most 100 history entries and marks entries truncated to 16KB', () => {
    const entries = trimHistory(Array.from({ length: 101 }, (_, i) => ({ id: String(i), body: 'x'.repeat(20_000) })))
    expect(entries).toHaveLength(100)
    expect(entries[0].id).toBe('100')
    expect(JSON.stringify(entries[0]).length).toBeLessThanOrEqual(16_400)
    expect((entries[0] as { truncated?: boolean }).truncated).toBe(true)
  })

  it('bounds UTF-8 history size even when an old id and payload contain huge multibyte strings', () => {
    const source = readFileSync('src/features/api-console/utils.ts', 'utf8')
    const compiled = transpileModule(source, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2022 } }).outputText
    const script = `const module = { exports: {} }; const exports = module.exports; ${compiled}\nconst rows = module.exports.trimHistory([{ id: '🧪'.repeat(20_000) }, { id: 'small', body: '漢'.repeat(20_000) }]); process.stdout.write(JSON.stringify(rows));`
    const run = spawnSync(process.execPath, ['-e', script], { encoding: 'utf8', timeout: 3000 })
    expect((run.error as NodeJS.ErrnoException | undefined)?.code).not.toBe('ETIMEDOUT')
    const entries = JSON.parse(run.stdout) as Array<Record<string, unknown>>
    expect(entries.every((entry) => new TextEncoder().encode(JSON.stringify(entry)).length <= HISTORY_MAX_BYTES)).toBe(true)
    const hugeIdEntry = entries.find((entry) => (entry.id as string).startsWith('🧪'))!
    expect((hugeIdEntry.id as string).length).toBeLessThan(20_000)
    expect(hugeIdEntry.truncated).toBe(true)
    expect(entries.find((entry) => entry.id === 'small')?.truncated).toBe(true)
  })

  it('uses a shell token placeholder in exported cURL by default', () => {
    expect(makeCurl({ baseUrl: 'http://maa:8002', method: 'POST', path: '/api/pipelines', body: '{"tasks":[]}', token: 'abc' })).toContain("$MAA_TOKEN")
    expect(makeCurl({ baseUrl: '', method: 'GET', path: '/api/system/health', token: 'abc' })).not.toContain('abc')
    expect(makeCurl({ baseUrl: '', method: 'GET', path: '/api/system/health', token: 'abc' })).toContain('"Authorization: Bearer $MAA_TOKEN"')
    expect(makeCurl({ baseUrl: '', method: 'GET', path: '/api/system/health', token: "it's-secret", includeToken: true })).toContain("'Authorization: Bearer it'\\''s-secret'")
  })

  it('preserves the selected auth channel when exporting redacted or explicit-token cURL', () => {
    const redacted = makeCurl({ baseUrl: '', method: 'GET', path: '/api/tasks/types', headers: { 'X-Token': 'bad-token' }, token: 'session-token' })
    expect(redacted).toContain('"X-Token: $MAA_TOKEN"')
    expect(redacted).not.toContain('bad-token')
    expect(redacted).not.toContain('session-token')

    const explicit = makeCurl({ baseUrl: '', method: 'GET', path: '/api/tasks/types', headers: { 'X-Token': 'bad-token' }, token: 'session-token', includeToken: true })
    expect(explicit).toContain("'X-Token: bad-token'")
    expect(explicit).not.toContain('session-token')
    expect(explicit).not.toContain('Authorization:')
  })

  it('omits API keys and other credential-like custom headers from exported cURL', () => {
    const curl = makeCurl({
      baseUrl: '', method: 'GET', path: '/api/x?token=url-secret',
      headers: { 'X-Api-Key': 'api-key-secret', 'X-Password': 'password-secret', 'X-Secret': 'secret-value', 'X-Trace-Id': 'trace-1' },
    })
    expect(curl).toContain('X-Trace-Id: trace-1')
    expect(curl).not.toContain('api-key-secret')
    expect(curl).not.toContain('password-secret')
    expect(curl).not.toContain('secret-value')
    expect(curl).not.toContain('url-secret')
  })

  it('classifies HTTP responses and scopes logs to request or pipeline ids', () => {
    expect(responseTone(503)).toBe('danger')
    expect(selectLogs([{ request_id: 'r1' }, { pipeline_id: 'p2' }, { request_id: 'r2' }], 'r1', 'p2')).toHaveLength(2)
  })

  it('builds a searchable operation tree from the live OpenAPI document', () => {
    const document = {
      tags: [{ name: 'pipelines', description: '流水线接口' }],
      paths: {
        '/api/z-last': { get: { tags: ['pipelines'], operationId: 'getLast', summary: '最后一个' } },
        '/api/a-first': { post: { tags: ['pipelines'], operationId: 'createFirst', summary: '创建一个' } },
        '/internal': { get: { operationId: 'internal', summary: '内部接口' } },
      },
    }
    const tree = future.buildOperationTree?.(document) as Array<{ name: string; description: string; operations: Array<{ path: string; method: string }> }>
    expect(tree).toMatchObject([
      { name: 'pipelines', description: '流水线接口', operations: [{ path: '/api/a-first', method: 'POST' }, { path: '/api/z-last', method: 'GET' }] },
      { name: '其他', operations: [{ path: '/internal', method: 'GET' }] },
    ])
    expect(future.searchOperations?.(tree, 'CREATEFIRST')).toHaveLength(1)
  })

  it('keeps JSON syntax or schema problems as warnings so the request can still be sent', async () => {
    const check = await validateJsonBody('{"items":[]}', {
      type: 'object', required: ['items'], additionalProperties: false,
      properties: { items: { type: 'array', minItems: 1 } },
    }) as { valid: boolean; sendable: boolean; errors: string[] }
    expect(check).toMatchObject({ valid: false, sendable: true })
    expect(check.errors.join(' ')).toContain('items')

    const malformed = await validateJsonBody('{', { type: 'object' })
    expect(malformed).toMatchObject({ valid: false, sendable: true })
    expect(malformed.parseError).toBeTruthy()
  })

  it('maps schema issues to a CodeMirror underline range in the offending JSON value', () => {
    const source = '{"message":"short","count":2}'
    const ranges = future.bodyDiagnosticRanges?.(source, [{ path: 'message', message: 'must NOT have fewer than 5 characters' }]) as Array<{ from: number; to: number; message: string; severity: string }>
    expect(ranges).toHaveLength(1)
    expect(source.slice(ranges[0].from, ranges[0].to)).toBe('"short"')
    expect(ranges[0]).toMatchObject({ message: 'must NOT have fewer than 5 characters', severity: 'error' })
  })

  it('builds endpoint URLs with encoded path values and only enabled query parameters', () => {
    expect(future.buildRequestUrl?.({
      baseUrl: 'http://maa.example:8002/prefix/',
      path: '/api/pipelines/{pipeline_id}',
      pathParams: { pipeline_id: 'run 1/2' },
      query: { page: { enabled: true, value: '2' }, stage: { enabled: false, value: '' }, empty: { enabled: true, value: '' } },
    })).toBe('http://maa.example:8002/api/pipelines/run%201%2F2?page=2&empty=')
  })

  it('keeps optional OpenAPI field descriptions outside the example body', () => {
    const schema = {
      type: 'object', required: ['pipeline'],
      properties: {
        pipeline: { type: 'object', description: '主流水线配置', properties: {
          tasks: { type: 'array', description: '按顺序执行的任务', items: { type: 'object' } },
          title: { type: 'string', description: '可读名称' },
        } },
      },
    }
    expect(future.schemaExample?.(schema)).toEqual({ pipeline: {}})
    expect(future.optionalFieldDescriptions?.(schema)).toEqual([
      { path: 'pipeline.tasks', description: '按顺序执行的任务' },
      { path: 'pipeline.title', description: '可读名称' },
    ])
  })

  it('resolves referenced OpenAPI parameters against the fetched document', () => {
    const document = { components: { parameters: { PipelineId: { name: 'pipeline_id', in: 'path', required: true, schema: { type: 'string', default: 'current' } } } } }
    expect(future.operationParameters?.({ parameters: [{ $ref: '#/components/parameters/PipelineId' }] }, document)).toMatchObject([
      { name: 'pipeline_id', in: 'path', required: true, schema: { type: 'string', default: 'current' } },
    ])
  })

  it('builds a request skeleton through component schema references', () => {
    const document = { components: { schemas: { Task: { type: 'object', required: ['name', 'params'], properties: { name: { type: 'string', example: 'Fight' }, params: { type: 'object', required: ['stage'], properties: { stage: { type: 'string', default: '1-7' }, optional: { type: 'boolean', description: '可选开关' } } } } } } } }
    expect(future.schemaExample?.({ $ref: '#/components/schemas/Task' }, 0, document)).toEqual({ name: 'Fight', params: { stage: '1-7' } })
    expect(future.optionalFieldDescriptions?.({ $ref: '#/components/schemas/Task' }, document)).toEqual([{ path: 'params.optional', description: '可选开关' }])
  })

  it('persists capped history only after removing sensitive headers and query tokens', () => {
    const storage = {
      values: new Map<string, string>(),
      getItem(key: string) { return this.values.get(key) ?? null },
      setItem(key: string, value: string) { this.values.set(key, value) },
    }
    const input = Array.from({ length: 101 }, (_, index) => ({
      id: String(index), method: 'GET', path: `/api/x?token=secret-${index}&stage=${index}`,
      headers: { Authorization: 'bearer secret', 'X-Token': 'secret', Cookie: 'secret', 'X-Api-Key': 'api-key-secret', Accept: 'application/json' },
      body: 'payload-' + index,
    }))
    future.saveHistory?.(storage, input)
    const entries = JSON.parse(storage.values.get('maa.api-console.history') ?? '[]') as Array<Record<string, unknown>>
    expect(entries).toHaveLength(100)
    expect(entries[0]).toMatchObject({ id: '100', path: '/api/x?stage=100', headers: { Accept: 'application/json' } })
    expect(JSON.stringify(entries)).not.toContain('secret')
    expect(entries.every((entry) => JSON.stringify(entry).length <= 16 * 1024)).toBe(true)
  })

  it('formats recent history timestamps as relative time', () => {
    const now = Date.parse('2026-09-25T12:00:00Z')
    expect(future.relativeTimeLabel?.(now - 30_000, now)).toBe('刚刚')
    expect(future.relativeTimeLabel?.(now - 120_000, now)).toBe('2 分钟前')
    expect(future.relativeTimeLabel?.(now - 7_200_000, now)).toBe('2 小时前')
  })

  it('extracts named pipeline tasks for a schedule template', () => {
    expect(future.pipelineTemplateFromBody?.({ tasks: [{ name: 'Fight', stage: '1-7' }, { type: 'unknown' }] })).toEqual([{ name: 'Fight', stage: '1-7' }])
    expect(future.pipelineTemplateFromBody?.('{"pipeline":{"tasks":[{"name":"StartUp"}]}}')).toEqual([{ name: 'StartUp' }])
  })

  it('builds an isolated remote WebSocket URL that follows the selected API base URL', () => {
    expect(future.consoleWebSocketUrl?.('https://maa.example:8002/base/', 'temp token')).toBe('wss://maa.example:8002/api/ws?token=temp+token')
    expect(future.consoleWebSocketUrl?.('http://127.0.0.1:8002', null)).toBe('ws://127.0.0.1:8002/api/ws')
  })

  it('extracts response timing, headers, and structured error details', () => {
    const response = future.describeResponse?.({
      status: 422, statusText: 'Unprocessable Entity', elapsedMs: 18.4,
      headers: { 'content-type': 'application/json', 'x-response-time-ms': '3.2' },
      body: { error: { code: 'INVALID_PARAMETER', message: '参数错误', details: { retries: 3 } } },
    }) as Record<string, unknown>
    expect(response).toMatchObject({ tone: 'warning', elapsedMs: 18.4, serverElapsedMs: 3.2 })
    expect(response.errorCard).toMatchObject({ code: 'INVALID_PARAMETER', message: '参数错误', details: { retries: 3 } })
  })
})
