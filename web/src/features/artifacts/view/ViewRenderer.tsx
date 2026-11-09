import { ChartCard } from '@/features/agent/MediaCards'
import { Button } from '@/components/ui/button'
import { ViewField } from './ViewField'
import { ViewStats } from './ViewStats'
import { ViewTable } from './ViewTable'
import type { FormValue, FormValues, ParsedView, ViewComponent } from './schema'

export interface ViewRendererProps {
  view: ParsedView
  mode: 'interactive' | 'readonly'
  values: FormValues
  errors: Record<string, string>
  onChange: (name: string, value: FormValue) => void
  onSubmit?: () => void
  submitting?: boolean
}

const textToneClass = {
  default: 'text-[0.9375rem] leading-relaxed',
  muted: 'text-footnote text-muted-foreground',
  warning: 'text-warning',
} as const

function ViewComponentView({
  component,
  mode,
  values,
  errors,
  onChange,
  submitting,
}: {
  component: ViewComponent
  mode: ViewRendererProps['mode']
  values: FormValues
  errors: Record<string, string>
  onChange: ViewRendererProps['onChange']
  submitting: boolean
}) {
  switch (component.type) {
    case 'heading':
      return component.level === 2
        ? <h3 className="text-headline font-semibold">{component.text}</h3>
        : <h4 className="text-[0.9375rem] font-semibold">{component.text}</h4>
    case 'text':
      return (
        <p className={`whitespace-pre-wrap break-words ${textToneClass[component.tone]}`}>
          {component.text}
        </p>
      )
    case 'stats':
      return <ViewStats items={component.items} />
    case 'table':
      return <ViewTable component={component} />
    case 'chart':
      return <ChartCard card={component.card} />
    case 'field':
      return (
        <ViewField
          spec={component.spec}
          value={values[component.spec.name] ?? null}
          error={errors[component.spec.name]}
          disabled={mode === 'readonly'}
          onChange={(value) => onChange(component.spec.name, value)}
        />
      )
    case 'submit':
      if (mode === 'readonly') return null
      return (
        <div>
          <Button type="submit" loading={submitting}>
            {component.label}
          </Button>
        </div>
      )
  }
}

/**
 * 视图渲染器（M90 §4.6.2）：只产出标题、段落、dl、table、表单控件、按钮与 ChartCard。
 *
 * 文本一律按纯文本显示，不解析 Markdown，也不把视图里的字符串当 URL、类名或
 * 事件处理；渲染期不发任何网络请求。表单字段存在时整张视图包在 `<form>` 里。
 */
export function ViewRenderer({
  view,
  mode,
  values,
  errors,
  onChange,
  onSubmit,
  submitting = false,
}: ViewRendererProps) {
  const content = (
    <>
      {view.description ? (
        <p className="whitespace-pre-wrap break-words text-footnote text-muted-foreground">
          {view.description}
        </p>
      ) : null}
      {view.components.map((component, index) => (
        <ViewComponentView
          key={`${component.type}-${index}`}
          component={component}
          mode={mode}
          values={values}
          errors={errors}
          onChange={onChange}
          submitting={submitting}
        />
      ))}
    </>
  )

  if (view.fields.length === 0) {
    return <div className="space-y-4">{content}</div>
  }

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit?.()
      }}
    >
      {content}
    </form>
  )
}
