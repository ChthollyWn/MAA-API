import { useEffect, useMemo, useState } from 'react'
import { autocompletion, type CompletionContext } from '@codemirror/autocomplete'
import { json } from '@codemirror/lang-json'
import { linter } from '@codemirror/lint'
import { EditorView, keymap } from '@codemirror/view'
import CodeMirror, { basicSetup } from '@uiw/react-codemirror'
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
        try {
          const formatted = JSON.stringify(JSON.parse(view.state.doc.toString()), null, 2)
          view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: formatted } })
          return true
        } catch { return false }
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
      basicSetup({ foldGutter: true }),
      json(),
      autocompletion({ override: [completion] }),
      validationLint,
      EditorView.lineWrapping,
      keymap.of([formatKey]),
      ...(readOnly ? [EditorView.editable.of(false)] : []),
    ]
  }, [schema, openApi, readOnly])

  return <div className="space-y-2">
    <div aria-label={ariaLabel} data-testid={ariaLabel.replaceAll(' ', '-')} className="overflow-hidden rounded-lg border bg-background text-sm [&_.cm-editor]:max-h-[34rem] [&_.cm-editor]:min-h-44 [&_.cm-editor]:text-sm [&_.cm-scroller]:overflow-auto">
      <CodeMirror value={value} height="auto" extensions={extensions} onChange={readOnly ? undefined : onChange} basicSetup={false} aria-label={ariaLabel} />
    </div>
    {!readOnly && validation.errors.length > 0 && <div className="rounded-lg border border-warning/40 bg-warning/5 px-3 py-2 text-xs text-warning" role="status">
      <p className="font-medium">请求体校验警告；仍可发送</p>
      {validation.errors.slice(0, 8).map((message, index) => <p key={index} className="mt-1 break-words">{message}</p>)}
      {validation.errors.length > 8 && <p className="mt-1">另有 {validation.errors.length - 8} 个问题</p>}
    </div>}
  </div>
}
