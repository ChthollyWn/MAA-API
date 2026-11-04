import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from 'react'
import { useNavigate } from 'react-router'
import { Composer } from '@/features/agent/Composer'
import { ComposerAssist, type MentionGroup } from './ComposerAssist'
import { matchSlashCommands, parseSlashCommand, type SlashCommandName } from './commands'
import {
  activeMentions,
  findAssistToken,
  insertMention,
  type AssistToken,
} from './composer-assist'
import { HelpCard } from './HelpCard'
import { useMentionCandidates } from './mention-sources'
import type { MentionDraft, ConversationScope } from './types'
import type { ConversationController } from './use-conversation'

export interface ConversationComposerProps {
  scope: ConversationScope
  controller: ConversationController
  docked: boolean
  textareaRef: RefObject<HTMLTextAreaElement | null>
  /** 「回到底部」按钮；提及与命令面板由组件自己叠加。 */
  overlay?: ReactNode
  /** 工单已结束时传入：不渲染输入栏，改渲染这句提示。 */
  disabledReason?: string
  /** 快捷开场：`nonce` 变化时用 `text` 替换草稿并聚焦，不发送。 */
  seed?: { text: string; nonce: number }
}

const NO_RUNNING_TURN = '现在没有在跑的一轮'
const NOTICE_MS = 3_000

/**
 * 会话输入栏（M72 §4.5.6）：斜杠命令、五类 @ 提及、状态行与发送 / 停止按钮。
 *
 * 草稿与提及状态收在这里，页面只负责传 `controller` 与滚动用的 `overlay`。
 */
export function ConversationComposer({
  scope,
  controller,
  docked,
  textareaRef,
  overlay,
  disabledReason,
  seed,
}: ConversationComposerProps) {
  const navigate = useNavigate()
  const [draft, setDraft] = useState('')
  const [mentions, setMentions] = useState<MentionDraft[]>([])
  const [assistToken, setAssistToken] = useState<AssistToken | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const [helpOpen, setHelpOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const pendingCaretRef = useRef<number | null>(null)
  const noticeTimerRef = useRef<number | null>(null)
  const seedNonce = seed?.nonce

  // 提及候选：五类都挂在面板打开时才查询，关掉面板后请求也不发。
  const mentionQuery = assistToken?.trigger === '@' ? assistToken.query : ''
  const mentionEnabled = assistToken?.trigger === '@'
  const ticketSource = useMentionCandidates('ticket', mentionQuery, mentionEnabled)
  const knowledgeSource = useMentionCandidates('knowledge', mentionQuery, mentionEnabled)
  const operatorSource = useMentionCandidates('operator', mentionQuery, mentionEnabled)
  const stageSource = useMentionCandidates('stage', mentionQuery, mentionEnabled)
  const pipelineSource = useMentionCandidates('pipeline', mentionQuery, mentionEnabled)

  const mentionGroups: MentionGroup[] = [
    { type: 'ticket', items: ticketSource.items, isPending: ticketSource.isPending },
    { type: 'knowledge', items: knowledgeSource.items, isPending: knowledgeSource.isPending },
    { type: 'operator', items: operatorSource.items, isPending: operatorSource.isPending },
    { type: 'stage', items: stageSource.items, isPending: stageSource.isPending },
    { type: 'pipeline', items: pipelineSource.items, isPending: pipelineSource.isPending },
  ]
  const mentionItems = mentionGroups.flatMap((group) => group.items)

  const commandMatches = assistToken?.trigger === '/' ? matchSlashCommands(draft) : []
  const assistOpen = assistToken?.trigger === '@'
    || (assistToken?.trigger === '/' && commandMatches.length > 0)
  const assistCount = assistToken?.trigger === '/' ? commandMatches.length : mentionItems.length
  // 结果到达后候选可能变少：高亮只是夹在有效范围内，状态本身不追着候选改。
  const highlighted = assistCount > 0 ? Math.min(activeIndex, assistCount - 1) : 0

  const showNotice = (message: string) => {
    if (noticeTimerRef.current !== null) window.clearTimeout(noticeTimerRef.current)
    setNotice(message)
    noticeTimerRef.current = window.setTimeout(() => {
      noticeTimerRef.current = null
      setNotice(null)
    }, NOTICE_MS)
  }

  useEffect(() => () => {
    if (noticeTimerRef.current !== null) window.clearTimeout(noticeTimerRef.current)
  }, [])

  // 快捷开场：只换草稿并聚焦，不发送。光标在随后的 layout effect 里补到末尾。
  useEffect(() => {
    if (!seed) return
    setDraft(seed.text)
    setAssistToken(null)
    setActiveIndex(0)
    pendingCaretRef.current = seed.text.length
    textareaRef.current?.focus()
  }, [seedNonce])

  useLayoutEffect(() => {
    const caret = pendingCaretRef.current
    if (caret === null) return
    pendingCaretRef.current = null
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.focus()
    textarea.setSelectionRange(caret, caret)
  }, [draft, textareaRef])

  const runCommand = (name: SlashCommandName) => {
    setDraft('')
    setAssistToken(null)
    setActiveIndex(0)
    if (name === 'stop') {
      setHelpOpen(false)
      if (controller.canStop) {
        setNotice(null)
        controller.stop()
      } else {
        showNotice(NO_RUNNING_TURN)
      }
      return
    }
    if (name === 'tickets') {
      setHelpOpen(false)
      void navigate('/tickets')
      return
    }
    setHelpOpen(true)
  }

  const selectCandidate = (index: number) => {
    if (!assistToken) return
    if (assistToken.trigger === '/') {
      const command = commandMatches[index]
      if (command) runCommand(command.name)
      return
    }
    const candidate = mentionItems[index]
    if (!candidate) return
    const inserted = insertMention(draft, assistToken, candidate.label)
    setMentions((current) => [
      ...current,
      { type: candidate.type, id: candidate.id, label: candidate.label },
    ])
    setDraft(inserted.value)
    setAssistToken(null)
    setActiveIndex(0)
    pendingCaretRef.current = inserted.caret
  }

  const submit = async () => {
    const command = parseSlashCommand(draft)
    if (command) {
      runCommand(command.name)
      return
    }
    if (!draft.trim()) return
    const content = draft
    setDraft('')
    setAssistToken(null)
    setActiveIndex(0)
    const sent = await controller.send({
      content,
      mentions: activeMentions(content, mentions),
    })
    // 发送没成（例如首屏历史还没到）：草稿还是空的就把原文放回（v2 口径）。
    if (!sent) setDraft((current) => (current.trim() ? current : content))
  }

  const onValueChange = (value: string) => {
    setDraft(value)
    // 任意再次输入都收起 /help 说明卡；Esc 也能关。
    setHelpOpen(false)
    const caret = textareaRef.current?.selectionStart ?? value.length
    setAssistToken(findAssistToken(value, caret))
    setActiveIndex(0)
  }

  const interceptKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    // 输入法组字的按键交给 IME；Shift+Enter 仍是换行，不当作选中。
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return false
    if (assistOpen) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActiveIndex(assistCount > 0 ? (highlighted + 1) % assistCount : 0)
        return true
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActiveIndex(assistCount > 0 ? (highlighted - 1 + assistCount) % assistCount : 0)
        return true
      }
      if ((event.key === 'Enter' || event.key === 'Tab') && !event.shiftKey && assistCount > 0) {
        event.preventDefault()
        selectCandidate(highlighted)
        return true
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setAssistToken(null)
        setActiveIndex(0)
        return true
      }
    }
    if (event.key === 'Escape' && helpOpen) {
      event.preventDefault()
      setHelpOpen(false)
      return true
    }
    return false
  }

  // 状态行优先级（规格 §4.5.6 的 T7 口径）：
  // 正在停止 > 本地这一轮在跑（确认 > 提问 > 处理中）> 本轮已停止 > 后台在跑。
  let status: string | undefined
  if (notice) {
    status = notice
  } else if (controller.stopping) {
    status = '正在停止本轮…'
  } else if (controller.live?.running) {
    status = controller.live.pendingConfirmation
      ? '请先处理待确认的操作'
      : controller.live.pendingQuestion
        ? '请先回答等待中的提问'
        : scope.kind === 'orchestrator' ? '总控正在处理…' : 'Worker 正在处理…'
  } else if (controller.live?.interrupted) {
    status = '本轮已停止'
  } else if (!controller.live && controller.canStop) {
    status = scope.kind === 'orchestrator' ? '总控正在处理收到的消息' : 'Worker 正在后台执行'
  }

  // 没有本地流、但后端说还有一轮在跑（刷新/换设备回来）时同样要能停：M22 的停止入口
  // 不能只依赖本地实时副本。
  const backgroundRunning = !controller.live && controller.canStop && !controller.stopping
  const busy = Boolean(controller.live?.running && !draft.trim()) || controller.stopping || backgroundRunning
  const orchestrator = scope.kind === 'orchestrator'

  const assistPanel = assistToken?.trigger === '@'
    ? (
      <ComposerAssist
        kind="mention"
        groups={mentionGroups}
        activeIndex={highlighted}
        onPick={selectCandidate}
      />
    )
    : commandMatches.length > 0
      ? (
        <ComposerAssist
          kind="command"
          commands={commandMatches}
          activeIndex={highlighted}
          onPick={selectCandidate}
        />
      )
      : null

  const overlayContent = helpOpen || assistPanel || overlay
    ? (
      <div className="flex w-full flex-col items-center gap-2">
        {helpOpen ? <HelpCard /> : null}
        {assistPanel}
        {overlay}
      </div>
    )
    : undefined

  if (disabledReason) {
    return (
      <p role="status" className="border-t px-4 py-3 text-center text-sm text-muted-foreground">
        {disabledReason}
      </p>
    )
  }

  return (
    <Composer
      value={draft}
      onValueChange={onValueChange}
      onSend={() => void submit()}
      busy={busy}
      onStop={controller.canStop ? controller.stop : undefined}
      stopping={controller.stopping}
      status={status}
      docked={docked}
      textareaRef={textareaRef}
      overlay={overlayContent}
      placeholder={orchestrator ? '告诉总控要做什么' : '给 Worker 发消息'}
      label={orchestrator ? '发送给总控' : '发送给 Worker'}
      interceptKeyDown={interceptKeyDown}
    />
  )
}
