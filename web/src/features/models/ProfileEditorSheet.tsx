import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { SwitchRow } from '@/components/ui/switch'
import { toast } from '@/components/ui/toaster'
import { errorText } from '@/features/settings/format'
import {
  useCreateProfile,
  useUpdateProfile,
  type ModelProfile,
  type ModelProfileCreate,
  type ModelProfileUpdate,
} from './api'

interface ProfileForm {
  name: string
  kind: ModelProfile['kind']
  provider: ModelProfile['provider']
  base_url: string
  api_key: string
  model: string
  context_window: string
  max_output_tokens: string
  supports_tools: boolean
  dynamic_context_position: ModelProfile['dynamic_context_position']
  price_input: string
  price_cached_input: string
  price_output: string
  currency: string
  embedding_dim: string
  local_model_path: string
  enabled: boolean
}

function numberText(value: number | null): string {
  return value === null ? '' : String(value)
}

function initialForm(profile: ModelProfile | null): ProfileForm {
  return {
    name: profile?.name ?? '',
    kind: profile?.kind ?? 'chat',
    provider: profile?.provider ?? 'openai_compatible',
    base_url: profile?.base_url ?? '',
    api_key: '',
    model: profile?.model ?? '',
    context_window: numberText(profile?.context_window ?? null),
    max_output_tokens: numberText(profile?.max_output_tokens ?? null),
    supports_tools: profile?.supports_tools ?? true,
    dynamic_context_position: profile?.dynamic_context_position ?? 'tail_system',
    price_input: numberText(profile?.price_input ?? null),
    price_cached_input: numberText(profile?.price_cached_input ?? null),
    price_output: numberText(profile?.price_output ?? null),
    currency: profile?.currency ?? 'CNY',
    embedding_dim: numberText(profile?.embedding_dim ?? null),
    local_model_path: profile?.local_model_path ?? '',
    enabled: profile?.enabled ?? true,
  }
}

function optionalText(value: string): string | null {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

function optionalNumber(value: string): number | null {
  const trimmed = value.trim()
  if (trimmed === '') return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function commonFields(form: ProfileForm) {
  return {
    kind: form.kind,
    provider: form.provider,
    base_url: optionalText(form.base_url),
    model: form.model.trim(),
    context_window: optionalNumber(form.context_window),
    max_output_tokens: optionalNumber(form.max_output_tokens),
    supports_tools: form.supports_tools,
    dynamic_context_position: form.dynamic_context_position,
    price_input: optionalNumber(form.price_input),
    price_cached_input: optionalNumber(form.price_cached_input),
    price_output: optionalNumber(form.price_output),
    currency: form.currency.trim().toUpperCase() || 'CNY',
    embedding_dim: optionalNumber(form.embedding_dim),
    local_model_path: optionalText(form.local_model_path),
    enabled: form.enabled,
  }
}

function createPayload(form: ProfileForm): ModelProfileCreate {
  return {
    name: form.name.trim(),
    api_key: optionalText(form.api_key),
    ...commonFields(form),
  }
}

function updatePayload(form: ProfileForm): ModelProfileUpdate {
  return {
    // 「***」表示不改动已保存的密钥；编辑时输入框留空即保持原样。
    api_key: form.api_key.trim() || '***',
    ...commonFields(form),
  }
}

export interface ProfileEditorSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 编辑已有档案；新建时为 null。 */
  profile: ModelProfile | null
}

/** 新建或编辑档案的表单；字段与 ModelProfileCreate 一一对应。 */
export function ProfileEditorSheet({ open, onOpenChange, profile }: ProfileEditorSheetProps) {
  const create = useCreateProfile()
  const update = useUpdateProfile()
  const [form, setForm] = useState(() => initialForm(profile))
  const [failure, setFailure] = useState<string | null>(null)

  const editing = profile !== null
  const busy = create.isPending || update.isPending

  const change = (patch: Partial<ProfileForm>) => {
    setForm((current) => ({ ...current, ...patch }))
    setFailure(null)
  }

  const submit = () => {
    if (busy) return
    setFailure(null)
    if (profile) {
      update.mutate(
        { name: profile.name, body: updatePayload(form) },
        {
          onSuccess: () => {
            toast.success('已保存档案')
            onOpenChange(false)
          },
          onError: (caught) => setFailure(`保存档案失败：${errorText(caught)}`),
        },
      )
      return
    }
    create.mutate(createPayload(form), {
      onSuccess: () => {
        toast.success('已新建档案')
        onOpenChange(false)
      },
      onError: (caught) => setFailure(`保存档案失败：${errorText(caught)}`),
    })
  }

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? '编辑档案' : '新建档案'}
      footer={<Button className="w-full" loading={busy} onClick={submit}>保存</Button>}
    >
      <div className="space-y-4 pb-2">
        {failure ? <InlineAlert tone="danger">{failure}</InlineAlert> : null}

        <Field label="名称" htmlFor="model-profile-name">
          <Input
            id="model-profile-name"
            value={form.name}
            readOnly={editing}
            disabled={busy}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            onChange={(event) => change({ name: event.target.value })}
          />
        </Field>

        <Field label="类型" htmlFor="model-profile-kind">
          <NativeSelect
            id="model-profile-kind"
            value={form.kind}
            disabled={busy}
            onChange={(event) => change({ kind: event.target.value as ProfileForm['kind'] })}
          >
            <option value="chat">对话</option>
            <option value="embedding">向量</option>
          </NativeSelect>
        </Field>

        <Field label="接入方式" htmlFor="model-profile-provider">
          <NativeSelect
            id="model-profile-provider"
            value={form.provider}
            disabled={busy}
            onChange={(event) => change({ provider: event.target.value as ProfileForm['provider'] })}
          >
            <option value="openai_compatible">OpenAI 兼容接口</option>
            <option value="local_onnx">本机 ONNX</option>
          </NativeSelect>
        </Field>

        <Field label="API 地址" htmlFor="model-profile-base-url">
          <Input
            id="model-profile-base-url"
            value={form.base_url}
            disabled={busy}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            onChange={(event) => change({ base_url: event.target.value })}
          />
        </Field>

        <Field label="API 密钥" htmlFor="model-profile-api-key">
          <Input
            id="model-profile-api-key"
            type="password"
            value={form.api_key}
            placeholder={editing ? '***' : undefined}
            disabled={busy}
            autoComplete="new-password"
            onChange={(event) => change({ api_key: event.target.value })}
          />
        </Field>

        <Field label="模型" htmlFor="model-profile-model">
          <Input
            id="model-profile-model"
            value={form.model}
            disabled={busy}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            onChange={(event) => change({ model: event.target.value })}
          />
        </Field>

        <Field label="上下文窗口（token）" htmlFor="model-profile-context-window">
          <Input
            id="model-profile-context-window"
            value={form.context_window}
            inputMode="numeric"
            disabled={busy}
            autoComplete="off"
            onChange={(event) => change({ context_window: event.target.value })}
          />
        </Field>

        <Field label="最大输出（token）" htmlFor="model-profile-max-output">
          <Input
            id="model-profile-max-output"
            value={form.max_output_tokens}
            inputMode="numeric"
            disabled={busy}
            autoComplete="off"
            onChange={(event) => change({ max_output_tokens: event.target.value })}
          />
        </Field>

        <SwitchRow
          label="支持工具调用"
          checked={form.supports_tools}
          disabled={busy}
          onCheckedChange={(supports_tools) => change({ supports_tools })}
        />

        <Field
          label="尾部状态位置"
          htmlFor="model-profile-position"
          hint="档案第一次使用时会自动检测，一般不用改"
        >
          <NativeSelect
            id="model-profile-position"
            value={form.dynamic_context_position}
            disabled={busy}
            onChange={(event) => change({ dynamic_context_position: event.target.value as ProfileForm['dynamic_context_position'] })}
          >
            <option value="tail_system">系统消息</option>
            <option value="tail_user">并入最后一条消息</option>
          </NativeSelect>
        </Field>

        <Field label="输入价格" htmlFor="model-profile-price-input" description="每百万 token">
          <Input
            id="model-profile-price-input"
            value={form.price_input}
            inputMode="decimal"
            disabled={busy}
            autoComplete="off"
            onChange={(event) => change({ price_input: event.target.value })}
          />
        </Field>

        <Field label="缓存命中输入价格" htmlFor="model-profile-price-cached" description="每百万 token">
          <Input
            id="model-profile-price-cached"
            value={form.price_cached_input}
            inputMode="decimal"
            disabled={busy}
            autoComplete="off"
            onChange={(event) => change({ price_cached_input: event.target.value })}
          />
        </Field>

        <Field label="输出价格" htmlFor="model-profile-price-output" description="每百万 token">
          <Input
            id="model-profile-price-output"
            value={form.price_output}
            inputMode="decimal"
            disabled={busy}
            autoComplete="off"
            onChange={(event) => change({ price_output: event.target.value })}
          />
        </Field>

        <Field label="币种" htmlFor="model-profile-currency" description="只有人民币计入预算">
          <Input
            id="model-profile-currency"
            value={form.currency}
            maxLength={3}
            disabled={busy}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            onChange={(event) => change({ currency: event.target.value })}
          />
        </Field>

        <Field label="向量维度" htmlFor="model-profile-embedding-dim">
          <Input
            id="model-profile-embedding-dim"
            value={form.embedding_dim}
            inputMode="numeric"
            disabled={busy}
            autoComplete="off"
            onChange={(event) => change({ embedding_dim: event.target.value })}
          />
        </Field>

        <Field label="本机模型路径" htmlFor="model-profile-local-path">
          <Input
            id="model-profile-local-path"
            value={form.local_model_path}
            disabled={busy}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            onChange={(event) => change({ local_model_path: event.target.value })}
          />
        </Field>

        <SwitchRow
          label="启用"
          checked={form.enabled}
          disabled={busy}
          onCheckedChange={(enabled) => change({ enabled })}
        />
      </div>
    </ResponsiveSheet>
  )
}
