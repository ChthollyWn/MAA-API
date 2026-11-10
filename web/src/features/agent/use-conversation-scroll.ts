import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { matchesMedia } from '@/lib/media'

/** 离底部不超过这个距离就算「在底部」，新内容到达时继续跟随。 */
const FOLLOW_THRESHOLD = 96

/**
 * 滚动目标：
 * - `document`：手机布局没有自己的会话容器，跟随整页滚动。
 * - `container`：桌面布局只滚会话容器。容器由 ref 回调里的 setState 挂上，
 *   首次提交时还是 null；此时返回 null 表示「这一帧先不滚」，绝不能回退成整页滚动，
 *   否则二次进入时长会话会把整个 window 滚到文档底部（见 docs/v3/13 §8.185）。
 */
export type ConversationScrollTarget = 'container' | 'document'

/** 该滚的元素；桌面容器还没挂上时返回 null。 */
function resolveScroller(
  target: ConversationScrollTarget,
  container: HTMLElement | null,
): HTMLElement | null {
  if (container) return container
  if (target === 'document') return (document.scrollingElement as HTMLElement | null) ?? document.documentElement
  return null
}

export interface ConversationScrollOptions {
  /** 对话区自己的滚动容器（桌面）；为 null 时跟随整页滚动（手机）。 */
  container: HTMLElement | null
  /** 滚动目标；桌面传 `container` 时，容器就绪前不会回退到整页滚动。 */
  target: ConversationScrollTarget
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
export function useConversationScroll({ container, target, content, headKey, tailKey, resetKey }: ConversationScrollOptions) {
  const followRef = useRef(true)
  const anchorRef = useRef<{ height: number; top: number } | null>(null)
  const [atBottom, setAtBottom] = useState(true)

  useEffect(() => {
    const listenOn: HTMLElement | Window | null = container ?? (target === 'document' ? window : null)
    if (!listenOn) return
    const onScroll = () => {
      const scroller = resolveScroller(target, container)
      if (!scroller) return
      const near = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= FOLLOW_THRESHOLD
      followRef.current = near
      setAtBottom(near)
    }
    listenOn.addEventListener('scroll', onScroll, { passive: true })
    return () => listenOn.removeEventListener('scroll', onScroll)
  }, [container, target])

  useLayoutEffect(() => {
    followRef.current = true
    anchorRef.current = null
    setAtBottom(true)
  }, [resetKey])

  useLayoutEffect(() => {
    const anchor = anchorRef.current
    if (!anchor) return
    // 容器还没就绪也要把锚点消费掉，避免之后拿旧高度去纠正。
    anchorRef.current = null
    const scroller = resolveScroller(target, container)
    if (!scroller) return
    scroller.scrollTop = anchor.top + Math.max(0, scroller.scrollHeight - anchor.height)
  }, [container, target, headKey])

  useLayoutEffect(() => {
    if (!followRef.current) return
    const scroller = resolveScroller(target, container)
    if (!scroller) return
    scroller.scrollTop = scroller.scrollHeight
  }, [container, target, tailKey])

  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return
    const targets = [content, container].filter((element): element is HTMLElement => element != null)
    if (targets.length === 0) return
    const observer = new ResizeObserver(() => {
      if (!followRef.current) return
      const scroller = resolveScroller(target, container)
      if (!scroller) return
      scroller.scrollTop = scroller.scrollHeight
    })
    for (const element of targets) observer.observe(element)
    return () => observer.disconnect()
  }, [container, target, content])

  const scrollToBottom = useCallback((smooth = false) => {
    followRef.current = true
    setAtBottom(true)
    const scroller = resolveScroller(target, container)
    if (!scroller) return
    const top = scroller.scrollHeight
    if (smooth && typeof scroller.scrollTo === 'function' && !matchesMedia('(prefers-reduced-motion: reduce)')) {
      scroller.scrollTo({ top, behavior: 'smooth' })
    } else {
      scroller.scrollTop = top
    }
  }, [container, target])

  /** 在更早的消息写入之前调用，记下当前高度与滚动位置。 */
  const rememberPositionBeforePrepend = useCallback(() => {
    const scroller = resolveScroller(target, container)
    if (!scroller) return
    anchorRef.current = { height: scroller.scrollHeight, top: scroller.scrollTop }
  }, [container, target])

  return { atBottom, scrollToBottom, rememberPositionBeforePrepend }
}
