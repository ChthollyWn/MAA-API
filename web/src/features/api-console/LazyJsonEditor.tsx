import { lazy, Suspense } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import type { JsonEditorProps } from './JsonEditor'

// CodeMirror 体积较大，单独分块，打开接口或收到响应时才加载。
const JsonEditor = lazy(() => import('./JsonEditor').then(({ JsonEditor: Editor }) => ({ default: Editor })))

export function LazyJsonEditor(props: JsonEditorProps) {
  return (
    <Suspense
      fallback={(
        <div role="status" className="space-y-2">
          <span className="sr-only">{props.readOnly ? '正在载入 JSON 查看器…' : '正在载入 JSON 编辑器…'}</span>
          <Skeleton className={props.readOnly ? 'h-16' : 'h-44'} />
        </div>
      )}
    >
      <JsonEditor {...props} />
    </Suspense>
  )
}
