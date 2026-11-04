import type { MentionCandidate } from './mention-sources'
import type { MentionType } from './types'
import type { SlashCommand } from './commands'

/** 提及面板的分段标题（M72 §4.6.7）。 */
export const MENTION_GROUP_TITLES: Record<MentionType, string> = {
  ticket: '工单',
  knowledge: '知识',
  operator: '干员',
  stage: '关卡',
  pipeline: '流水线',
}

export interface MentionGroup {
  type: MentionType
  items: readonly MentionCandidate[]
  isPending: boolean
}

export type ComposerAssistProps =
  | {
    kind: 'command'
    commands: readonly SlashCommand[]
    activeIndex: number
    onPick: (index: number) => void
  }
  | {
    kind: 'mention'
    groups: readonly MentionGroup[]
    activeIndex: number
    onPick: (index: number) => void
  }

const ROW = 'flex w-full items-center gap-3 px-3 py-1.5 text-left text-sm'
const ACTIVE = 'bg-surface-2'

/**
 * 输入栏上方的辅助面板：斜杠命令列表，或五类 @ 提及候选。
 *
 * `activeIndex` 与 `onPick` 用的是**扁平序号**（按分段顺序数，空分段不占号），
 * 与 `ConversationComposer` 的键盘高亮共用同一套编号。
 */
export function ComposerAssist(props: ComposerAssistProps) {
  if (props.kind === 'command') {
    return (
      <div
        role="listbox"
        aria-label="命令"
        className="pointer-events-auto max-h-64 w-full max-w-3xl overflow-y-auto rounded-xl border bg-card py-1 shadow-float"
      >
        {props.commands.map((command, index) => (
          <button
            key={command.name}
            type="button"
            role="option"
            aria-selected={index === props.activeIndex}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => props.onPick(index)}
            className={`${ROW} ${index === props.activeIndex ? ACTIVE : ''}`}
          >
            <span className="font-mono text-[0.8125rem]">{command.label}</span>
            {' '}
            <span className="truncate text-muted-foreground">{command.description}</span>
          </button>
        ))}
      </div>
    )
  }

  let index = 0
  const groups = props.groups.map((group) => {
    const title = MENTION_GROUP_TITLES[group.type]
    const items = group.items.map((item) => ({ item, index: index++ }))
    return { ...group, title, items }
  })

  return (
    <div
      role="listbox"
      aria-label="提及"
      className="pointer-events-auto max-h-80 w-full max-w-3xl overflow-y-auto rounded-xl border bg-card py-1 shadow-float"
    >
      {groups.map((group) => (
        <section key={group.type} role="group" aria-label={group.title} className="py-0.5">
          <p className="px-3 py-1 text-xs font-medium text-muted-foreground">{group.title}</p>
          {group.isPending ? (
            <p className="px-3 py-1.5 text-footnote text-muted-foreground">正在读取…</p>
          ) : group.items.length === 0 ? (
            <p className="px-3 py-1.5 text-footnote text-muted-foreground">没有匹配的{group.title}</p>
          ) : group.items.map(({ item, index: rowIndex }) => (
            <button
              key={`${item.type}:${item.id}`}
              type="button"
              role="option"
              aria-selected={rowIndex === props.activeIndex}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => props.onPick(rowIndex)}
              className={`${ROW} ${rowIndex === props.activeIndex ? ACTIVE : ''}`}
            >
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
              <span className="shrink-0 text-footnote text-muted-foreground">{item.detail}</span>
            </button>
          ))}
        </section>
      ))}
    </div>
  )
}
