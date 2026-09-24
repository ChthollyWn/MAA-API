import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RealtimeProvider } from '@/realtime/RealtimeProvider'
import { queryClient } from '@/lib/query-client'
import App from '@/App'
import '@/index.css'

const rootElement = document.getElementById('root')

if (!rootElement) throw new Error('Missing #root application mount point')

createRoot(rootElement).render(
  <StrictMode>
    <RealtimeProvider queryClient={queryClient}>
      <App />
    </RealtimeProvider>
  </StrictMode>,
)
