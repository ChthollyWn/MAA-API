import { useEffect, useId, useMemo, useState } from 'react'
import { Disclosure } from '@/components/ui/disclosure'
import { InlineAlert } from '@/components/ui/inline-alert'
import {
  countModified,
  groupTaskFields,
  isRuntimeImmutable,
  requiredFields,
  schemaProperties,
  setObjectValue,
  taskTypeSummary,
  type TaskTypeSchema,
} from '../form'
import { FieldEditor, fieldDomId } from './FieldEditor'

export interface TaskEditorProps {
  taskType: TaskTypeSchema
  /** 草稿参数：只包含用户改动过的字段。 */
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
  disabled?: boolean
  /** 字段名 → 错误消息（来自 mapFieldIssues）。 */
  errors?: Readonly<Record<string, string>>
  /** 编辑运行中的流水线：runtime_immutable 字段不可修改。 */
  runtimeEditing?: boolean
  /** 在分组上方显示任务类型的一句说明。 */
  showDescription?: boolean
}

export type DynamicTaskFormProps = TaskEditorProps

const NO_ERRORS: Readonly<Record<string, string>> = {}

function TaskEditorBody({ taskType, value, onChange, disabled = false, errors = NO_ERRORS, runtimeEditing = false, showDescription = true }: TaskEditorProps) {
  const idPrefix = useId()
  const groups = useMemo(() => groupTaskFields(taskType), [taskType])
  const siblings = useMemo(() => schemaProperties(taskType.schema), [taskType])
  const required = useMemo(() => requiredFields(taskType.schema), [taskType])
  const errorGroups = groups.filter((group) => group.fields.some(([name]) => errors[name])).map((group) => group.name)
  const [openGroups, setOpenGroups] = useState<ReadonlySet<string>>(() => new Set([...groups.slice(0, 1).map((group) => group.name), ...errorGroups]))
  // 只增不减：有错误的分组展开后不再自动收起，避免修改字段时分组突然折叠。
  const newlyFailing = errorGroups.filter((group) => !openGroups.has(group))
  if (newlyFailing.length > 0) setOpenGroups(new Set([...openGroups, ...newlyFailing]))

  const fieldNames = new Set(groups.flatMap((group) => group.fields.map(([name]) => name)))
  const strayErrors = Object.entries(errors).filter(([name]) => !fieldNames.has(name))
  const summary = showDescription ? taskTypeSummary(taskType) : ''

  // 只在打开编辑器时定位一次，之后错误变化不再抢滚动位置。
  useEffect(() => {
    const first = groups.flatMap((group) => group.fields).find(([name]) => errors[name])
    if (!first) return
    const node = document.getElementById(`${fieldDomId(idPrefix, [taskType.name, first[0]])}-field`)
    if (node && typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'center' })
  }, [])

  return (
    <div className="space-y-3">
      {summary ? <p className="text-footnote text-muted-foreground">{summary}</p> : null}
      {strayErrors.length > 0 ? (
        <InlineAlert tone="danger" title="有参数未通过校验">
          {strayErrors.map(([name, message]) => <p key={name}>{name}：{message}</p>)}
        </InlineAlert>
      ) : null}
      {groups.map((group) => {
        const modified = countModified(value, Object.fromEntries(group.fields))
        return (
          <Disclosure key={group.name} title={group.name} badge={modified > 0 ? `已修改 ${modified} 项` : undefined} defaultOpen={openGroups.has(group.name)}>
            <div className="divide-y [&>*]:py-4 [&>*:first-child]:pt-0 [&>*:last-child]:pb-0">
              {group.fields.map(([name, schema]) => (
                <FieldEditor
                  key={name}
                  name={name}
                  schema={schema}
                  root={taskType.schema}
                  path={[taskType.name, name]}
                  idPrefix={idPrefix}
                  value={value[name]}
                  formValues={value}
                  siblings={siblings}
                  disabled={disabled}
                  immutable={runtimeEditing && isRuntimeImmutable(taskType, name)}
                  required={required.has(name)}
                  error={errors[name]}
                  onChange={(next) => onChange(setObjectValue(value, [name], next))}
                />
              ))}
            </div>
          </Disclosure>
        )
      })}
    </div>
  )
}

/**
 * 一个任务的全部参数，按 schema 分组：第一组展开，其余折叠并显示「已修改 N 项」；有错误的分组自动展开，
 * 打开时滚动到第一个错误字段。只把用户改动过的字段写进 `value`。
 */
export function TaskEditor(props: TaskEditorProps) {
  return <TaskEditorBody key={props.taskType.name} {...props} />
}

/** 旧任务页的组件名，定时任务等调用方沿用它。 */
export const DynamicTaskForm = TaskEditor
