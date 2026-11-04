import { SLASH_COMMANDS } from './commands'

/** `/help` 的说明卡：四条输入辅助规则（M72 §4.6.7）。 */
export function HelpCard() {
  return (
    <div
      role="note"
      aria-label="可用命令"
      className="pointer-events-auto w-full max-w-3xl rounded-xl border bg-card px-3 py-2.5 text-sm shadow-float"
    >
      <p className="font-medium">可用命令</p>
      <ul className="mt-1.5 space-y-1 text-footnote">
        {SLASH_COMMANDS.map((command) => (
          <li key={command.name} className="flex gap-2">
            <span className="shrink-0 font-mono">{command.label}</span>
            {' '}
            <span className="text-muted-foreground">{command.description}</span>
          </li>
        ))}
        <li className="flex gap-2">
          <span className="shrink-0 font-mono">@</span>
          {' '}
          <span className="text-muted-foreground">引用工单、知识、干员、关卡或流水线</span>
        </li>
      </ul>
    </div>
  )
}
