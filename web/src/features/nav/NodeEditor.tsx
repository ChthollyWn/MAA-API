import { useState } from 'react'
import { Camera, Trash2 } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { Disclosure } from '@/components/ui/disclosure'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toaster'
import {
  NODE_ID_PATTERN,
  NODE_KINDS,
  kindLabel,
  putNavNode,
  type NavKind,
  type NavNode,
  type NavNodePut,
  type NavRecognizer,
} from './api'

export interface NodeEditorProps {
  /** null 表示新建；编辑时带当前节点。 */
  node: NavNode | null
  open: boolean
  onClose: () => void
  onSaved: () => void
}

interface Errors {
  id?: string
  title?: string
  advanced?: string
}

const EXEMPLAR_SOURCE_LABELS: Record<string, string> = {
  agent: '视觉认定',
  edge: '一步校验',
  user: '手动标记',
  anchor: '回首页',
}

function initialAdvanced(node: NavNode | null): string {
  const recognizer = node?.recognizer
  return JSON.stringify(
    {
      rois: recognizer?.rois ?? [[0, 0, 1, 1]],
      max_distance: recognizer?.max_distance ?? 10,
    },
    null,
    2,
  )
}

/** 解析高级区 JSON：只接受 `rois`（0–1 的 [x, y, w, h]）与 `max_distance`（1–32）。 */
function parseAdvanced(text: string): { rois?: number[][]; maxDistance?: number; error?: string } {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    return { error: '高级设置需是合法 JSON' }
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { error: '高级设置需是 JSON 对象' }
  }
  const value = raw as { rois?: unknown; max_distance?: unknown }
  if (!Array.isArray(value.rois) || value.rois.length < 1 || value.rois.length > 4) {
    return { error: 'rois 需是 1–4 组 [x, y, w, h]' }
  }
  for (const roi of value.rois) {
    if (
      !Array.isArray(roi) ||
      roi.length !== 4 ||
      roi.some((part) => typeof part !== 'number' || !Number.isFinite(part) || part < 0 || part > 1)
    ) {
      return { error: '每个 roi 需是 0–1 的 [x, y, w, h]' }
    }
  }
  if (
    typeof value.max_distance !== 'number' ||
    !Number.isInteger(value.max_distance) ||
    value.max_distance < 1 ||
    value.max_distance > 32
  ) {
    return { error: 'max_distance 需是 1–32 的整数' }
  }
  return { rois: value.rois as number[][], maxDistance: value.max_distance }
}

/**
 * 界面编辑弹层（M86 §4.6）。
 *
 * 改标题、类型与特征描述；「标记当前画面」用当前截图加一个 user 样本；每个样本可移除；
 * 高级区用 JSON 文本框改识别区域与阈值。新建时 id 按服务端的 `NODE_ID_PATTERN` 校验。
 */
export function NodeEditor({ node, open, onClose, onSaved }: NodeEditorProps) {
  const isNew = node === null
  const [nodeId, setNodeId] = useState('')
  const [title, setTitle] = useState(node?.title ?? '')
  const [kind, setKind] = useState<NavKind>((node?.kind as NavKind | undefined) ?? 'screen')
  const [description, setDescription] = useState(node?.recognizer.description ?? '')
  const [advanced, setAdvanced] = useState(() => initialAdvanced(node))
  const [errors, setErrors] = useState<Errors>({})
  const [saving, setSaving] = useState(false)
  const [marking, setMarking] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)

  const recognizerOf = (): NavRecognizer | null => {
    const parsed = parseAdvanced(advanced)
    if (parsed.error || parsed.rois === undefined || parsed.maxDistance === undefined) {
      setErrors({ advanced: parsed.error ?? '高级设置不合法' })
      return null
    }
    return {
      rois: parsed.rois,
      max_distance: parsed.maxDistance,
      description: description.trim(),
      generic: node?.recognizer.generic ?? false,
    }
  }

  const save = async () => {
    const found: Errors = {}
    const resolvedId = node?.id ?? nodeId.trim()
    if (isNew && !NODE_ID_PATTERN.test(resolvedId)) {
      found.id = 'id 需是 2–48 位小写字母、数字与下划线，关卡写成 stage:代号'
    }
    if (!title.trim()) found.title = '请填写标题'
    const parsed = parseAdvanced(advanced)
    if (parsed.error) found.advanced = parsed.error
    if (Object.keys(found).length > 0) {
      setErrors(found)
      return
    }
    if (parsed.rois === undefined || parsed.maxDistance === undefined) return
    const recognizer: NavRecognizer = {
      rois: parsed.rois,
      max_distance: parsed.maxDistance,
      description: description.trim(),
      generic: node?.recognizer.generic ?? false,
    }

    setErrors({})
    setSaving(true)
    try {
      const body: NavNodePut = { title: title.trim(), kind, recognizer }
      await putNavNode(resolvedId, body)
      toast.success('已保存界面')
      onSaved()
      onClose()
    } catch (caught) {
      toast.error(`保存界面失败：${toApiError(caught).message}`)
    } finally {
      setSaving(false)
    }
  }

  const markCurrent = async () => {
    const recognizer = recognizerOf()
    if (recognizer === null || node === null) return
    setMarking(true)
    try {
      await putNavNode(node.id, {
        title: title.trim() || node.title,
        kind,
        recognizer,
        add_exemplar: 'current',
      })
      toast.success('已保存界面')
      onSaved()
    } catch (caught) {
      toast.error(`保存界面失败：${toApiError(caught).message}`)
    } finally {
      setMarking(false)
    }
  }

  const removeExemplar = async (phash: string) => {
    if (!node) return
    const recognizer = recognizerOf()
    if (recognizer === null) return
    setRemoving(phash)
    try {
      await putNavNode(node.id, {
        title: title.trim() || node.title,
        kind,
        recognizer,
        remove_exemplars: [phash],
      })
      toast.success('已保存界面')
      onSaved()
    } catch (caught) {
      toast.error(`保存界面失败：${toApiError(caught).message}`)
    } finally {
      setRemoving(null)
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
      title={isNew ? '新建界面' : `编辑界面 ${node.title}`}
      footer={footer}
      desktop="dialog"
    >
      <div className="space-y-4 pb-2">
        {isNew ? (
          <Field
            label="id"
            htmlFor="nav-node-id"
            description="小写字母、数字与下划线；关卡写成 stage:代号"
            error={errors.id}
          >
            <Input
              id="nav-node-id"
              value={nodeId}
              onChange={(event) => setNodeId(event.target.value)}
              placeholder="例如 depot 或 stage:1-7"
              aria-invalid={Boolean(errors.id)}
            />
          </Field>
        ) : (
          <p className="text-footnote text-muted-foreground">
            id：{node.id} · {kindLabel(node.kind)} · {node.exemplars.length} 个样本
          </p>
        )}

        <Field label="标题" htmlFor="nav-node-title" error={errors.title}>
          <Input
            id="nav-node-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="界面的中文名"
            aria-invalid={Boolean(errors.title)}
          />
        </Field>

        <Field label="类型" htmlFor="nav-node-kind">
          <NativeSelect id="nav-node-kind" value={kind} onChange={(event) => setKind(event.target.value as NavKind)}>
            {NODE_KINDS.map((option) => (
              <option key={option} value={option}>
                {kindLabel(option)}
              </option>
            ))}
          </NativeSelect>
        </Field>

        <Field label="特征描述" htmlFor="nav-node-description" description="认不准时，视觉模型按这句描述在候选里选">
          <Textarea
            id="nav-node-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="例如：基建总览，左侧是控制中枢"
          />
        </Field>

        {!isNew ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium">画面样本</p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void markCurrent()}
                loading={marking}
              >
                <Camera aria-hidden="true" />
                标记当前画面
              </Button>
            </div>
            <p className="text-footnote text-muted-foreground">样本越多越认得出；换主题或改布局后可以删掉旧样本重拍</p>
            {node.exemplars.length === 0 ? (
              <p className="text-footnote text-muted-foreground">还没有样本，第一次到达时会自动收下</p>
            ) : (
              <ul className="divide-y rounded-lg border bg-surface">
                {node.exemplars.map((exemplar, index) => (
                  <li key={exemplar.phash} className="flex items-center gap-2 px-3 py-2">
                    <span className="min-w-0 flex-1 truncate text-footnote text-muted-foreground">
                      样本 {index + 1} · {EXEMPLAR_SOURCE_LABELS[exemplar.source] ?? '手动标记'}
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="text-danger"
                      aria-label={`移除样本 ${index + 1}`}
                      loading={removing === exemplar.phash}
                      onClick={() => void removeExemplar(exemplar.phash)}
                    >
                      <Trash2 aria-hidden="true" />
                      移除
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : null}

        <Disclosure title="高级设置" description="识别区域与阈值，通常不用改">
          <div className="space-y-2">
            <Field
              label="rois 与 max_distance"
              htmlFor="nav-node-advanced"
              description="rois 是 0–1 的 [x, y, w, h]，max_distance 是 1–32"
              error={errors.advanced}
            >
              <Textarea
                id="nav-node-advanced"
                className="min-h-32 font-mono text-sm"
                value={advanced}
                onChange={(event) => setAdvanced(event.target.value)}
                aria-invalid={Boolean(errors.advanced)}
              />
            </Field>
          </div>
        </Disclosure>
      </div>
    </ResponsiveSheet>
  )
}
