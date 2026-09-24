import { QueryClient } from '@tanstack/react-query'
import { toApiError } from '@/api/errors'

export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (failureCount >= 3) return false

  const status = toApiError(error).status
  if (status === undefined) return true
  if (status === 504) return false
  return status >= 500
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: shouldRetryQuery,
    },
    mutations: {
      retry: false,
    },
  },
})
