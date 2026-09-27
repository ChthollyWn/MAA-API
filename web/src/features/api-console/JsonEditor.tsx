import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { autocompletion, type CompletionContext } from '@codemirror/autocomplete'
import { json } from '@codemirror/lang-json'
import { linter } from '@codemirror/lint'
import { EditorView, keymap } from '@codemirror/view'
import CodeMirror, { basicSetup, Prec } from '@uiw/react-codemirror'
import { InlineAlert } from '@/components/ui/inline-alert'
import { cn } from '@/lib/utils'
import { formatJson } from './console-model'
import { bodyDiagnosticRanges, type JsonSchema } from './utils'
import { validateJsonBody, type JsonValidationResult } from './json-validation'

function objectOf(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function schemaKeys(schema: JsonSchema | undefined): string[] {
  if (!schema) return []
  const keys: string[] = []
  const visit = (current: Record<string, unknown>, depth: number) => {
    if (depth > 6) return
    const properties = objectOf(current.properties)
    for (const [name, child] of Object.entries(properties)) {
      keys.push(name)
      if (child && typeof child === 'object' && !Array.isArray(child)) visit(child as Record<string, unknown>, depth + 1)
    }
    if (current.items && typeof current.items === 'object' && !Array.isArray(current.items)) visit(current.items as Record<string, unknown>, depth + 1)
  }
  visit(schema, 0)
  return [...new Set(keys)]
}

function subscribeToThemeClass(notify: () => void): () => void {
  if (typeof MutationObserver === 'undefined') return () => {}
  const observer = new MutationObserver(notify)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  return () => observer.disconnect()
}

/** 跟随 `<html>` 上实际生效的 dark class（含「跟随系统」时的切换）。 */
function useDocumentDark(): boolean {
  return useSyncExternalStore(subscribeToThemeClass, () => document.documentElement.classList.contains('dark'), () => false)
}

/** 编辑器外观取自设计令牌；语法高亮沿用明暗两套内置配色，只覆盖底色、行号栏、选区与浮层。 */
const editorChrome = Prec.highest(EditorView.theme({
  '&': { backgroundColor: 'var(--surface)', color: 'var(--foreground)' },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': { fontFamily: 'var(--app-font-mono)', lineHeight: '1.6' },
  '.cm-content': { caretColor: 'var(--foreground)' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--foreground)' },
  '.cm-gutters': { backgroundColor: 'var(--surface-2)', color: 'var(--muted-foreground)', borderRight: '1px solid var(--border)' },
  '.cm-activeLine': { backgroundColor: 'color-mix(in srgb, var(--surface-2) 60%, transparent)' },
  '.cm-activeLineGutter': { backgroundColor: 'transparent', color: 'var(--foreground)' },
  '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection': {
    backgroundColor: 'color-mix(in srgb, var(--primary) 26%, transparent)',
  },
  '.cm-foldPlaceholder': { backgroundColor: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--muted-foreground)' },
  '.cm-tooltip': { backgroundColor: 'var(--surface-raised)', color: 'var(--foreground)', border: '1px solid var(--border)', borderRadius: '0.5rem', overflow: 'hidden' },
  '.cm-tooltip-autocomplete > ul > li[aria-selected]': { backgroundColor: 'var(--primary-soft)', color: 'var(--primary-text)' },
}))

export interface JsonEditorProps {
  value: string
  onChange?: (value: string) => void
  schema?: JsonSchema
  openApi?: unknown
  readOnly?: boolean
  ariaLabel: string
}

function syntaxState(value: string): JsonValidationResult {
  try { return { valid: true, sendable: true, errors: [], value: JSON.parse(value) as unknown } }
  catch (error) {
    return { valid: false, sendable: true, errors: ['JSON 格式不正确'], parseError: error instanceof Error ? error.message : 'JSON 格式不正确' }
  }
}

export function JsonEditor({ value, onChange, schema, openApi, readOnly = false, ariaLabel }: JsonEditorProps) {
  const dark = useDocumentDark()
  const [validation, setValidation] = useState<JsonValidationResult>(() => syntaxState(value))
  useEffect(() => {
    let active = true
    const result = readOnly
      ? Promise.resolve(syntaxState(value))
      : validateJsonBody(value, schema ?? {}, openApi)
    void result.then((next) => { if (active) setValidation(next) })
    return () => { active = false }
  }, [value, schema, openApi, readOnly])
  const extensions = useMemo(() => {
    const completion = (context: CompletionContext) => {
      const word = context.matchBefore(/[\w-]*/)
      if (!word || (word.from === word.to && !context.explicit)) return null
      const keys = schemaKeys(schema)
      const insideQuotedKey = context.state.doc.sliceString(Math.max(0, word.from - 1), word.from) === '"'
      return {
        from: word.from,
        options: keys.map((name) => ({ label: name, type: 'property', apply: insideQuotedKey ? name : JSON.stringify(name) })),
      }
    }
    const formatKey = {
      key: 'Mod-Shift-f',
      run(view: EditorView) {
        const formatted = formatJson(view.state.doc.toString())
        if (formatted === null) return false
        view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: formatted } })
        return true
      },
    }
    const validationLint = linter(async (view) => {
      if (readOnly) return []
      const source = view.state.doc.toString()
      const result = await validateJsonBody(source, schema ?? {}, openApi)
      if (result.valid) return []
      if (result.parseError) {
        const match = result.parseError.match(/position\s+(\d+)/i)
        const from = Math.min(Number(match?.[1] ?? source.length), Math.max(0, source.length - 1))
        return [{ from, to: Math.min(source.length, from + 1), message: result.parseError, severity: 'error' as const }]
      }
      return bodyDiagnosticRanges(source, result.diagnostics ?? []).map((diagnostic) => ({ ...diagnostic, severity: 'error' as const }))
    }, { delay: 250 })
    return [
      basicSetup({ foldGutter: true, highlightActiveLine: !readOnly, highlightActiveLineGutter: !readOnly }),
      json(),
      autocompletion({ override: [completion] }),
      validationLint,
      EditorView.lineWrapping,
      keymap.of([formatKey]),
      editorChrome,
      ...(readOnly ? [EditorView.editable.of(false)] : []),
    ]
  }, [schema, openApi, readOnly])

  return <div className="space-y-2">
    <div
      aria-label={ariaLabel}
      data-testid={ariaLabel.replaceAll(' ', '-')}
      className={cn(
        'overflow-hidden rounded-lg border border-border-strong bg-surface [&_.cm-editor]:max-h-[34rem] [&_.cm-scroller]:overflow-auto',
        readOnly
          ? 'text-[0.8125rem] [&_.cm-editor]:min-h-16'
          // 可编辑时手机上用 16px，避免 iOS 聚焦时整页放大。
          : 'text-base transition-[border-color,box-shadow] focus-within:border-primary focus-within:ring-3 focus-within:ring-primary/20 lg:text-[0.8125rem] [&_.cm-editor]:min-h-44',
      )}
    >
      <CodeMirror value={value} height="auto" theme={dark ? 'dark' : 'light'} extensions={extensions} onChange={readOnly ? undefined : onChange} basicSetup={false} aria-label={ariaLabel} />
    </div>
    {!readOnly && validation.errors.length > 0 ? (
      <InlineAlert tone="warning" role="status" title="请求体校验警告；仍可发送">
        <ul className="space-y-0.5">
          {validation.errors.slice(0, 8).map((message, index) => <li key={index} className="font-mono text-footnote [overflow-wrap:anywhere]">{message}</li>)}
          {validation.errors.length > 8 ? <li className="text-footnote">另有 {validation.errors.length - 8} 个问题</li> : null}
        </ul>
      </InlineAlert>
    ) : null}
  </div>
}
