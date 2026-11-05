import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toaster'
import {
  describeTriggerError,
  saveTriggerRule,
  triggerRuleFieldError,
  type TriggerRule,
} from './api'
import {
  buildRuleWrite,
  emptyRuleForm,
  MODE_OPTIONS,
  parseConditions,
  RULE_ID_PATTERN,
  ruleFormValues,
  SOURCE_OPTIONS,
  type RuleFormValues,
} from './format'
import { invalidateTriggerRules, triggerSpecsQuery } from './queries'

export interface RuleEditorProps {
  /** null 表示新建；编辑时带完整生效内容。 */
  rule: TriggerRule | null
  onClose: () => void
}

type Errors = Partial<Record<keyof RuleFormValues, string>>

const CONDITIONS_DESCRIPTION = '按 payload 字段写，例如 {"target": ["core", "resource"]}'

function validate(values: RuleFormValues, isNew: boolean): Errors {
  const errors: Errors = {}
  if (isNew && !RULE_ID_PATTERN.test(values.id.trim())) {
    errors.id = 'id 需为 2–48 位小写字母、数字与连字符'
  }
  if (!values.title.trim()) errors.title = '请填写名称'
  if (!values.kindPattern.trim()) errors.kindPattern = '请填写事件种类'
  const parsed = parseConditions(values.conditions)
  if (parsed.error) errors.conditions = parsed.error
  if (!values.worker.trim()) errors.worker = '请选择执行代理'
  const cooldown = Number(values.cooldown)
  if (!Number.isFinite(cooldown) || cooldown < 0) errors.cooldown = '冷却需是不小于 0 的整数'
  if (values.source === 'schedule') {
    if (!values.cron.trim()) errors.cron = '定时规则必须写 cron'
  }
  return errors
}

/**
 * 规则编辑弹层（M81 §4.6）：写完整的生效内容。`TRIGGER_RULE_INVALID` 的
 * `details.field` 落到对应字段上，其余错误弹 Toast。
 *
 * 新建时 id 也要填：`PUT /api/agent/trigger-rules/{rule_id}` 的路径参数就是规则 id，
 * 后端只接受 2–48 位小写字母、数字与连字符（§4.3）。
 */
export function RuleEditor({ rule, onClose }: RuleEditorProps) {
  const queryClient = useQueryClient()
  const specs = useQuery(triggerSpecsQuery)
  const isNew = rule === null
  const [form, setForm] = useState<RuleFormValues>(() =>
    rule === null ? emptyRuleForm() : ruleFormValues(rule),
  )
  const [errors, setErrors] = useState<Errors>({})
  const [saving, setSaving] = useState(false)

  const workers = (specs.data ?? []).filter((spec) => spec.kind === 'worker')
  if (form.worker && !workers.some((spec) => spec.name === form.worker)) {
    // 规则引用的代理可能已经不在定义里；保留当前值，避免保存时被悄悄改掉。
    workers.push({
      name: form.worker,
      title: form.worker,
      kind: 'worker',
      description: '',
      model_role: 'worker',
      spec_version: '',
      tools: [],
      delegates: [],
      version: 0,
    })
  }

  const change = (patch: Partial<RuleFormValues>) => {
    setForm((current) => ({ ...current, ...patch }))
    setErrors({})
  }

  const save = async () => {
    const found = validate(form, isNew)
    setErrors(found)
    if (Object.keys(found).length > 0) return
    setSaving(true)
    try {
      const ruleId = isNew ? form.id.trim() : rule.id
      await saveTriggerRule(ruleId, buildRuleWrite(form))
      toast.success('已保存规则')
      onClose()
      await invalidateTriggerRules(queryClient)
    } catch (caught) {
      const fieldError = triggerRuleFieldError(caught)
      if (fieldError) setErrors({ [fieldError.field]: fieldError.message })
      else toast.error(describeTriggerError(caught))
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
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      title={isNew ? '新建规则' : '编辑规则'}
      footer={footer}
      desktop="dialog"
    >
      <div className="space-y-4 pb-2">
        {isNew ? (
          <Field label="id" htmlFor="trigger-rule-id" description="2–48 位小写字母、数字与连字符" error={errors.id}>
            <Input
              id="trigger-rule-id"
              value={form.id}
              autoComplete="off"
              aria-invalid={errors.id ? true : undefined}
              onChange={(event) => change({ id: event.target.value })}
            />
          </Field>
        ) : null}

        <Field label="名称" htmlFor="trigger-rule-title" error={errors.title}>
          <Input
            id="trigger-rule-title"
            value={form.title}
            maxLength={64}
            aria-invalid={errors.title ? true : undefined}
            onChange={(event) => change({ title: event.target.value })}
          />
        </Field>

        <Field label="来源" htmlFor="trigger-rule-source" error={errors.source}>
          <NativeSelect
            id="trigger-rule-source"
            value={form.source ?? ''}
            onChange={(event) =>
              change({ source: (event.target.value || null) as RuleFormValues['source'] })
            }
          >
            <option value="">任意</option>
            {SOURCE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </NativeSelect>
        </Field>

        <Field
          label="事件种类"
          htmlFor="trigger-rule-kind"
          description="支持通配符，例如 pipeline.*"
          error={errors.kindPattern}
        >
          <Input
            id="trigger-rule-kind"
            value={form.kindPattern}
            maxLength={64}
            className="font-mono"
            aria-invalid={errors.kindPattern ? true : undefined}
            onChange={(event) => change({ kindPattern: event.target.value })}
          />
        </Field>

        <Field
          label="条件"
          htmlFor="trigger-rule-conditions"
          description={CONDITIONS_DESCRIPTION}
          error={errors.conditions}
        >
          <Textarea
            id="trigger-rule-conditions"
            value={form.conditions}
            className="min-h-28 font-mono text-sm"
            aria-invalid={errors.conditions ? true : undefined}
            onChange={(event) => change({ conditions: event.target.value })}
          />
        </Field>

        <Field label="档位" error={errors.mode}>
          <SegmentedControl
            ariaLabel="档位"
            value={form.mode}
            onValueChange={(mode) => change({ mode })}
            options={MODE_OPTIONS}
            fullWidth
          />
        </Field>

        <Field label="执行代理" htmlFor="trigger-rule-worker" error={errors.worker}>
          <NativeSelect
            id="trigger-rule-worker"
            value={form.worker}
            aria-invalid={errors.worker ? true : undefined}
            onChange={(event) => change({ worker: event.target.value })}
          >
            <option value="">请选择</option>
            {workers.map((spec) => (
              <option key={spec.name} value={spec.name}>
                {spec.title || spec.name}
              </option>
            ))}
          </NativeSelect>
        </Field>

        <Field label="研判提示" htmlFor="trigger-rule-policy" error={errors.policy}>
          <Textarea
            id="trigger-rule-policy"
            value={form.policy}
            maxLength={2000}
            onChange={(event) => change({ policy: event.target.value })}
          />
        </Field>

        <Field label="冷却（秒）" htmlFor="trigger-rule-cooldown" error={errors.cooldown}>
          <Input
            id="trigger-rule-cooldown"
            type="number"
            min={0}
            max={604800}
            value={form.cooldown}
            aria-invalid={errors.cooldown ? true : undefined}
            onChange={(event) => change({ cooldown: event.target.value })}
          />
        </Field>

        {form.source === 'schedule' ? (
          <Field label="cron" htmlFor="trigger-rule-cron" error={errors.cron}>
            <Input
              id="trigger-rule-cron"
              value={form.cron}
              maxLength={64}
              className="font-mono"
              aria-invalid={errors.cron ? true : undefined}
              onChange={(event) => change({ cron: event.target.value })}
            />
          </Field>
        ) : null}
      </div>
    </ResponsiveSheet>
  )
}
