import type { JsonSchema } from './utils'

function objectOf(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

export interface JsonValidationResult {
  valid: boolean
  sendable: true
  errors: string[]
  diagnostics?: Array<{ path: string; message: string }>
  parseError?: string
  value?: unknown
}

export async function validateJsonBody(raw: string, schema: JsonSchema, openApi?: unknown): Promise<JsonValidationResult> {
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch (error) {
    return { valid: false, sendable: true, errors: ['JSON 格式不正确'], parseError: error instanceof Error ? error.message : 'JSON 格式不正确' }
  }
  try {
    const { default: Ajv } = await import('ajv')
    const root = objectOf(openApi)
    const wrapper = {
      components: objectOf(root.components),
      $defs: objectOf(root.$defs),
      consoleRequestBody: schema,
      $ref: '#/consoleRequestBody',
    }
    const validate = new Ajv({ allErrors: true, strict: false, validateFormats: false }).compile(wrapper)
    if (validate(value)) return { valid: true, sendable: true, errors: [], value }
    const diagnostics = (validate.errors ?? []).map((error) => {
      const path = error.instancePath.replace(/^\//, '').replaceAll('/', '.')
      const missing = error.params?.missingProperty
      const field = typeof missing === 'string' ? `${path ? `${path}.` : ''}${missing}` : path
      return { path: field, message: error.message ?? '不符合 schema' }
    })
    return { valid: false, sendable: true, errors: diagnostics.map(({ path, message }) => `${path || 'body'} ${message}`), diagnostics, value }
  } catch {
    return { valid: true, sendable: true, errors: [], value }
  }
}
