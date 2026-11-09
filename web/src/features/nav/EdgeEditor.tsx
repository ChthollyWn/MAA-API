import { useState } from 'react'
import { toApiError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { toast } from '@/components/ui/toaster'
import { putNavEdge, type NavNode } from './api'

export interface EdgeEditorProps {
  open: boolean
  nodes: NavNode[]
  onClose: () => void
  onSaved: () => void
}

type ActionType = 'tap' | 'key'

interface Errors {
  from?: string
  to?: string
  x?: string
  y?: string
}

/** 客户端生成跳转 id：服务端要求路径上的 id 是 UUID（spec §4.3.2）。 */
function newEdgeId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16)
    return (token === 'x' ? value : (value & 0x3) | 0x8).toString(16)
  })
}

/**
 * 新建跳转弹层（M86 §4.6）：选起点与终点，动作选「点击」时填 0–1 的 x、y。
 *
 * 新跳转的 id 由页面生成 UUID，`PUT /api/agent/nav/edges/{id}` 一次建成。
 */
export function EdgeEditor({ open, nodes, onClose, onSaved }: EdgeEditorProps) {
  const [from, setFrom] = useState(() => nodes[0]?.id ?? '')
  const [to, setTo] = useState(() => nodes[1]?.id ?? nodes[0]?.id ?? '')
  const [actionType, setActionType] = useState<ActionType>('tap')
  const [x, setX] = useState('0.5')
  const [y, setY] = useState('0.5')
  const [errors, setErrors] = useState<Errors>({})
  const [saving, setSaving] = useState(false)

  const save = async () => {
    const found: Errors = {}
    if (!from) found.from = '请选择起点'
    if (!to) found.to = '请选择终点'
    let action: Record<string, unknown> | null = null
    if (actionType === 'tap') {
      const parsedX = Number(x)
      const parsedY = Number(y)
      if (!Number.isFinite(parsedX) || parsedX < 0 || parsedX > 1) found.x = 'x 需是 0–1 的小数'
      if (!Number.isFinite(parsedY) || parsedY < 0 || parsedY > 1) found.y = 'y 需是 0–1 的小数'
      action = { type: 'tap', x: parsedX, y: parsedY }
    } else {
      action = { type: 'key', key: 'BACK' }
    }
    if (Object.keys(found).length > 0) {
      setErrors(found)
      return
    }
    if (action === null) return

    setErrors({})
    setSaving(true)
    try {
      await putNavEdge(newEdgeId(), { from_node: from, to_node: to, action })
      onSaved()
      onClose()
    } catch (caught) {
      toast.error(`保存跳转失败：${toApiError(caught).message}`)
    } finally {
      setSaving(false)
    }
  }

  const footer = (
    <div className="flex gap-2">
      <Button type="button" variant="outline" className="flex-1" onClick={onClose} disabled={saving}>
        取消
      </Button>
      <Button type="button" className="flex-1" onClick={() => void save()} loading={saving}>
        保存
      </Button>
    </div>
  )

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      title="新建跳转"
      footer={footer}
      desktop="dialog"
    >
      <div className="space-y-4 pb-2">
        <Field label="从" htmlFor="nav-edge-from" error={errors.from}>
          <NativeSelect id="nav-edge-from" value={from} onChange={(event) => setFrom(event.target.value)}>
            {nodes.length === 0 ? <option value="">还没有界面</option> : null}
            {nodes.map((node) => (
              <option key={node.id} value={node.id}>
                {node.title}
              </option>
            ))}
          </NativeSelect>
        </Field>

        <Field label="到" htmlFor="nav-edge-to" error={errors.to}>
          <NativeSelect id="nav-edge-to" value={to} onChange={(event) => setTo(event.target.value)}>
            {nodes.length === 0 ? <option value="">还没有界面</option> : null}
            {nodes.map((node) => (
              <option key={node.id} value={node.id}>
                {node.title}
              </option>
            ))}
          </NativeSelect>
        </Field>

        <Field label="动作" htmlFor="nav-edge-action">
          <NativeSelect
            id="nav-edge-action"
            value={actionType}
            onChange={(event) => setActionType(event.target.value as ActionType)}
          >
            <option value="tap">点击</option>
            <option value="key">返回键</option>
          </NativeSelect>
        </Field>

        {actionType === 'tap' ? (
          <div className="grid grid-cols-2 gap-3">
            <Field label="x" htmlFor="nav-edge-x" description="0–1，从左到右" error={errors.x}>
              <Input
                id="nav-edge-x"
                inputMode="decimal"
                value={x}
                onChange={(event) => setX(event.target.value)}
                aria-invalid={Boolean(errors.x)}
              />
            </Field>
            <Field label="y" htmlFor="nav-edge-y" description="0–1，从上到下" error={errors.y}>
              <Input
                id="nav-edge-y"
                inputMode="decimal"
                value={y}
                onChange={(event) => setY(event.target.value)}
                aria-invalid={Boolean(errors.y)}
              />
            </Field>
          </div>
        ) : null}
      </div>
    </ResponsiveSheet>
  )
}
