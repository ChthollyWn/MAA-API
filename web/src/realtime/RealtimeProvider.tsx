import { useEffect, type PropsWithChildren } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useShallow } from 'zustand/react/shallow'
import { queryClient as defaultQueryClient } from '@/lib/query-client'
import { handleServerEvent } from '@/realtime/query-bridge'
import { realtimeClient, useRealtimeStatusStore, type RealtimeStatus } from '@/realtime/client'
import { useAuth } from '@/stores/auth'

export interface RealtimeProviderProps extends PropsWithChildren {
  queryClient?: QueryClient
}

export function RealtimeProvider({
  children,
  queryClient = defaultQueryClient,
}: RealtimeProviderProps) {
  useEffect(() => {
    realtimeClient.setEventHandler((event) => handleServerEvent(event, queryClient))
    realtimeClient.connect()

    const onVisibilityChange = () => realtimeClient.handleVisibilityChange()
    document.addEventListener('visibilitychange', onVisibilityChange)
    const unsubscribeAuth = useAuth.subscribe((state, previous) => {
      if (state.token && state.token !== previous.token) realtimeClient.retryNow()
    })

    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange)
      unsubscribeAuth()
      realtimeClient.setEventHandler(undefined)
      realtimeClient.disconnect()
    }
  }, [queryClient])

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

export function useRealtimeStatus(): RealtimeStatus {
  return useRealtimeStatusStore(useShallow(({ status, reconnectAttempt, rtt, truncated, closeCode, error }) => ({
    status,
    reconnectAttempt,
    rtt,
    truncated,
    closeCode,
    error,
  })))
}
