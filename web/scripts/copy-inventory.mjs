#!/usr/bin/env node
/**
 * 抽取前端源码里所有可能出现在界面上的文案。
 *
 * 用法：node scripts/copy-inventory.mjs [--out <json>]
 *
 * 判据：含中日韩字符的字符串一律收；纯 ASCII 只在「文案位」（JSX 文本、JSX 属性、
 * 文案字段、toast/confirm/Error 等调用）才收，避免把 tailwind class、路由路径、
 * 枚举值收进来。注释不是 AST 节点，因此不会混进结果。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const WEB_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const REPO_ROOT = path.resolve(WEB_ROOT, '..')
const SRC_ROOT = path.join(WEB_ROOT, 'src')

const CJK = /[\u2e80-\u303f\u3040-\u9fff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]/
const SKIP_FILE = /(\.test\.(ts|tsx)$|\.d\.ts$|^test\/)/

/** 属性名/字段名命中这些键 → 值按文案处理（纯 ASCII 也收）。 */
const COPY_KEYS = new Set([
  'alt', 'aria-label', 'ariaLabel', 'body', 'cancelText', 'caption', 'confirmText',
  'desc', 'description', 'detail', 'emptyText', 'error', 'footer', 'header', 'help',
  'helpText', 'hint', 'label', 'legend', 'message', 'note', 'okText', 'placeholder',
  'prompt', 'reason', 'retryLabel', 'subtitle', 'summary', 'text', 'tip', 'title',
  'toast', 'tooltip', 'undefinedText', 'warning',
])

/** 命中这些调用 → 字符串实参按文案处理（纯 ASCII 也收）。 */
const COPY_CALLS = new Set([
  'alert', 'confirm', 'toast', 'toast.error', 'toast.info', 'toast.loading',
  'toast.message', 'toast.success', 'toast.warning', 'window.alert', 'window.confirm',
  'Error', 'window.Error',
])

function isCopyCall(name) {
  if (COPY_CALLS.has(name)) return true
  const tail = name.split('.').pop()
  return COPY_CALLS.has(tail)
}

function calleeName(node) {
  if (ts.isIdentifier(node)) return node.text
  if (ts.isPropertyAccessExpression(node)) {
    const left = calleeName(node.expression)
    return left ? `${left}.${node.name.text}` : node.name.text
  }
  if (ts.isCallExpression(node)) return calleeName(node.expression)
  return ''
}

function normalize(text) {
  return text.replace(/\s+/g, ' ').trim()
}

/** 判断纯 ASCII 字符串是不是「给人看的英文句子」，而不是 class / 标识符 / 路径。 */
function looksLikeProse(value) {
  if (value.length < 6 || !/\s/.test(value)) return false
  if (/^[a-z]/.test(value)) return false
  if (
    /^(?:size|flex|grid|text|bg|border|rounded|overflow|shadow|ring|z|opacity|transition|duration|ease|hover|focus|active|disabled|group|peer|dark|data|aria|absolute|relative|inline|block|hidden|w|h|min|max|p|m|gap|space)-/.test(
      value,
    )
  ) {
    return false
  }
  if (/^[A-Z]/.test(value) || /[.!?]$/.test(value)) return true
  return /\b(?:the|is|was|are|not|failed|error|cannot|unable|invalid|missing|unknown|request|connect|token|origin)\b/i.test(
    value,
  )
}

function surfaceOf(relPath) {
  const parts = relPath.split('/')
  if (parts[1] !== 'src') return parts.slice(0, 2).join('/')
  const rest = parts.slice(2)
  if (rest[0] === 'features') return `features/${rest[1] ?? ''}`
  if (rest[0] === 'components') return `components/${rest[1] ?? ''}`
  if (rest[0] === 'routes') return `routes/${rest[1] ?? ''}`
  if (rest[0] === 'api') return 'api'
  if (rest[0] === 'stores') return 'stores'
  if (rest[0] === 'lib') return 'lib'
  if (rest[0] === 'pwa') return 'pwa'
  if (rest[0] === 'realtime') return 'realtime'
  if (rest[0] === 'sw.ts' || rest[0] === 'main.tsx' || rest[0] === 'App.tsx') return 'app'
  return rest[0] ?? 'root'
}

function collectFile(filePath) {
  const rel = path.relative(REPO_ROOT, filePath)
  const source = fs.readFileSync(filePath, 'utf8')
  const scriptKind = filePath.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const sf = ts.createSourceFile(filePath, source, ts.ScriptTarget.Latest, true, scriptKind)
  const found = []

  const push = (node, position, name, text) => {
    const value = normalize(text)
    if (!value) return
    const { line, character } = sf.getLineAndCharacterOfPosition(node.getStart(sf))
    const hasCjk = CJK.test(value)
    if (!hasCjk) {
      const keyOk = COPY_KEYS.has(name)
      const callOk = position === 'call' && isCopyCall(name)
      const proseOk =
        (position === 'expr' || position === 'jsx-expr') && looksLikeProse(value)
      if (!keyOk && !callOk && !proseOk) return
      // 纯 ASCII 的文案位也要排除代码味值：标识符、路径、class、CSS 变量、枚举。
      if (/^[\w./:@%[\]()#-]+$/.test(value)) return
    }
    found.push({
      surface: surfaceOf(rel),
      file: rel,
      line: line + 1,
      column: character + 1,
      position,
      context: name,
      text: value,
    })
  }

  const pushLiteral = (node, text) => {
    const parent = node.parent
    if (ts.isJsxAttribute(parent)) {
      push(node, 'jsx-attr', parent.name.getText(sf), text)
    } else if (ts.isPropertyAssignment(parent) && parent.initializer === node) {
      push(node, 'prop', parent.name.getText(sf).replace(/['"]/g, ''), text)
    } else if (ts.isCallExpression(parent) && parent.arguments.includes(node)) {
      push(node, 'call', calleeName(parent.expression), text)
    } else if (ts.isConditionalExpression(parent) || ts.isJsxExpression(parent)) {
      push(node, 'jsx-expr', '', text)
    } else {
      push(node, 'expr', '', text)
    }
  }

  const visit = (node) => {
    if (ts.isJsxText(node)) {
      push(node, 'jsx-text', '', node.getText(sf))
      return
    }
    if (ts.isTemplateExpression(node)) {
      // 整条模板一起收（`共 ${n} 条`），不把 ${} 两侧的碎片当成两条文案。
      let text = node.head.text
      for (const span of node.templateSpans) {
        text += `\${${span.expression.getText(sf)}}${span.literal.text}`
      }
      pushLiteral(node, text)
      for (const span of node.templateSpans) visit(span.expression)
      return
    }
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      pushLiteral(node, node.text)
      return
    }
    ts.forEachChild(node, visit)
  }
  ts.forEachChild(sf, visit)
  return found
}

/** index.html 与 vite.config.ts 里写死、构建后才出现在界面上的元数据文案。 */
function collectStaticMeta() {
  const found = []
  const add = (file, line, context, text) => {
    const value = normalize(text)
    if (value) found.push({ surface: 'static-meta', file, line, column: 1, position: 'meta', context, text: value })
  }
  const html = fs.readFileSync(path.join(WEB_ROOT, 'index.html'), 'utf8')
  const meta = [
    [/<title>([^<]*)<\/title>/, 'title'],
    [/<meta name="description" content="([^"]*)"/, 'meta-description'],
    [/<meta name="apple-mobile-web-app-title" content="([^"]*)"/, 'apple-app-title'],
  ]
  for (const [pattern, name] of meta) {
    const match = pattern.exec(html)
    if (match) {
      const line = html.slice(0, match.index).split('\n').length
      add('web/index.html', line, name, match[1])
    }
  }
  const vite = fs.readFileSync(path.join(WEB_ROOT, 'vite.config.ts'), 'utf8')
  for (const key of ['name', 'short_name', 'description']) {
    const pattern = new RegExp(`\\n\\s*${key}: '([^']*)'`)
    const match = pattern.exec(vite)
    if (match) {
      const line = vite.slice(0, match.index).split('\n').length
      add('web/vite.config.ts', line, `manifest.${key}`, match[1])
    }
  }
  return found
}

function walk(dir) {
  const out = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    const rel = path.relative(SRC_ROOT, full)
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules') continue
      out.push(...walk(full))
    } else if (/\.(ts|tsx)$/.test(entry.name) && !SKIP_FILE.test(rel)) {
      out.push(full)
    }
  }
  return out
}

const files = walk(SRC_ROOT).sort()
const records = []
for (const file of files) records.push(...collectFile(file))
records.push(...collectStaticMeta())

// 同一个文件里去重（同一行同一文本只留一条）。
const seen = new Set()
const unique = records.filter((record) => {
  const key = `${record.file}:${record.line}:${record.column}:${record.text}`
  if (seen.has(key)) return false
  seen.add(key)
  return true
})

const args = process.argv.slice(2)
const outIndex = args.indexOf('--out')
const outPath = outIndex >= 0 ? args[outIndex + 1] : null
const payload = {
  generated_from: 'web/src',
  file_count: files.length,
  string_count: unique.length,
  strings: unique,
}
if (outPath) {
  fs.mkdirSync(path.dirname(outPath), { recursive: true })
  fs.writeFileSync(outPath, JSON.stringify(payload, null, 2) + '\n', 'utf8')
  process.stderr.write(`web 文案 ${unique.length} 条 / ${files.length} 个文件 → ${outPath}\n`)
} else {
  process.stdout.write(JSON.stringify(payload, null, 2) + '\n')
}
