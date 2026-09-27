import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { matchesMedia } from '@/lib/media'

/** 离底部不超过这个距离就算「在底部」，新内容到达时继续跟随。 */
const FOLLOW_THRESHOLD = 96

function resolveScroller(container: HTMLElement | null): HTMLElement {
  return container ?? (document.scrollingElement as HTMLElement | null) ?? document.documentElement
}

export interface ConversationScrollOptions {
  /** 对话区自己的滚动容器（桌面）；为 null 时跟随整页滚动（手机）。 */
  container: HTMLElement | null
  /** 对话内容；它的高度变化（确认卡读完详情、展开工具条）时，在底部就继续贴底。 */
  content: HTMLElement | null
  /** 最早一条消息的标识；变化说明顶部插入了更早的消息。 */
  headKey: string | number | undefined
  /** 变化说明底部有了新内容：新消息、流式文本、工具轨迹、确认卡或错误。 */
  tailKey: string
  /** 切换会话时变化：回到底部并重新开始跟随。 */
  resetKey: string | null
}

/**
 * 对话区滚动：在底部时自动跟随新内容；用户上滑查看历史时不强行拉回；
 * 顶部插入更早的消息后保持视线停在原来那条消息上。
 */
export function useConversationScroll({ container, content, headKey, tailKey, resetKey }: ConversationScrollOptions) {
  const followRef = useRef(true)
  const anchorRef = useRef<{ height: number; top: number } | null>(null)
  const [atBottom, setAtBottom] = useState(true)

  useEffect(() => {
    const target: HTMLElement | Window = container ?? window
    const onScroll = () => {
      const scroller = resolveScroller(container)
      const near = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= FOLLOW_THRESHOLD
      followRef.current = near
      setAtBottom(near)
    }
    target.addEventListener('scroll', onScroll, { passive: true })
    return () => target.removeEventListener('scroll', onScroll)
  }, [container])

  useLayoutEffect(() => {
    followRef.current = true
    anchorRef.current = null
    setAtBottom(true)
  }, [resetKey])

  useLayoutEffect(() => {
    const anchor = anchorRef.current
    if (!anchor) return
    anchorRef.current = null
    const scroller = resolveScroller(container)
    scroller.scrollTop = anchor.top + Math.max(0, scroller.scrollHeight - anchor.height)
  }, [container, headKey])

  useLayoutEffect(() => {
    if (!followRef.current) return
    const scroller = resolveScroller(container)
    scroller.scrollTop = scroller.scrollHeight
  }, [container, tailKey])

  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return
    const targets = [content, container].filter((element): element is HTMLElement => element != null)
    if (targets.length === 0) return
    const observer = new ResizeObserver(() => {
      if (!followRef.current) return
      const scroller = resolveScroller(container)
      scroller.scrollTop = scroller.scrollHeight
    })
    for (const target of targets) observer.observe(target)
    return () => observer.disconnect()
  }, [container, content])

  const scrollToBottom = useCallback((smooth = false) => {
    followRef.current = true
    setAtBottom(true)
    const scroller = resolveScroller(container)
    const top = scroller.scrollHeight
    if (smooth && typeof scroller.scrollTo === 'function' && !matchesMedia('(prefers-reduced-motion: reduce)')) {
      scroller.scrollTo({ top, behavior: 'smooth' })
    } else {
      scroller.scrollTop = top
    }
  }, [container])

  /** 在更早的消息写入之前调用，记下当前高度与滚动位置。 */
  const rememberPositionBeforePrepend = useCallback(() => {
    const scroller = resolveScroller(container)
    anchorRef.current = { height: scroller.scrollHeight, top: scroller.scrollTop }
  }, [container])

  return { atBottom, scrollToBottom, rememberPositionBeforePrepend }
}
