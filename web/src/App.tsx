import { ApplicationRouter } from '@/routes/router'
import { PwaUpdatePrompt } from '@/components/pwa/PwaUpdatePrompt'
import { Toaster } from '@/components/ui/toaster'

export default function App() {
  return <><ApplicationRouter /><PwaUpdatePrompt /><Toaster /></>
}
