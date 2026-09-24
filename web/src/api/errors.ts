export interface ApiErrorEnvelope {
  error?: {
    code?: unknown
    message?: unknown
    details?: unknown
  }
}

export class ApiError extends Error {
  readonly status: number | undefined
  readonly code: string | undefined
  readonly details: unknown
  readonly body: unknown

  constructor(options: {
    message: string
    status?: number
    code?: string
    details?: unknown
    body?: unknown
    cause?: unknown
  }) {
    super(options.message, { cause: options.cause })
    this.name = 'ApiError'
    this.status = options.status
    this.code = options.code
    this.details = options.details
    this.body = options.body
  }
}

function parseJson(value: unknown): unknown {
  if (typeof value !== 'string') return value

  try {
    return JSON.parse(value) as unknown
  } catch {
    return value
  }
}

function readEnvelope(value: unknown): ApiErrorEnvelope['error'] | undefined {
  const parsed = parseJson(value)
  if (!parsed || typeof parsed !== 'object' || !('error' in parsed)) return undefined

  const error = (parsed as ApiErrorEnvelope).error
  if (!error || typeof error !== 'object') return undefined
  return error
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

export function apiErrorFromResponse(
  response: Pick<Response, 'status' | 'statusText'>,
  body: unknown,
): ApiError {
  const envelope = readEnvelope(body)
  const status = response.status

  return new ApiError({
    status,
    code: asString(envelope?.code),
    message:
      asString(envelope?.message) ||
      response.statusText ||
      `Request failed with status ${status}`,
    details: envelope?.details,
    body: parseJson(body),
  })
}

/** Convert openapi-fetch error payloads and network errors into one retryable shape. */
export function toApiError(error: unknown, response?: Pick<Response, 'status' | 'statusText'>): ApiError {
  if (error instanceof ApiError) return error
  if (response) return apiErrorFromResponse(response, error)

  const envelope = readEnvelope(error)
  if (envelope) {
    return new ApiError({
      code: asString(envelope.code),
      message: asString(envelope.message) || 'The API request failed',
      details: envelope.details,
      body: parseJson(error),
      cause: error,
    })
  }

  if (error instanceof Error) {
    return new ApiError({ message: error.message || 'The network request failed', cause: error })
  }

  return new ApiError({
    message: typeof error === 'string' ? error : 'The network request failed',
    body: error,
    cause: error,
  })
}
