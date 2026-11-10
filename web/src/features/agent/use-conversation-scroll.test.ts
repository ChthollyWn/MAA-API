import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, renderHook } from '@testing-library/react'
import { useConversationScroll } from './use-conversation-scroll'

type ScrollTarget = 'container' | 'document'
interface ScrollWrite {
  target: ScrollTarget
  value: number
}

/**
 * jsdom 没有布局：给滚动元素补上可观测的 scrollHeight/clientHeight/scrollTop，
 * 用来断言「谁被滚了、滚到哪」。
 */
function stubScrollMetrics(element: HTMLElement, options: { height: number; target: ScrollTarget; writes: ScrollWrite[] }) {
  Object.defineProperty(element, 'scrollHeight', { configurable: true, get: () => options.height })
  Object.defineProperty(element, 'clientHeight', { configurable: true, get: () => 0 })
  Object.defineProperty(element, 'scrollTop', {
    configurable: true,
    get: () => 0,
    set: (value: number) => options.writes.push({ target: options.target, value }),
  })
}

/** 桌面端传 `container`，容器未就绪的首次提交里它是 null；此时绝不能回退成整页滚动。 */
describe('useConversationScroll 的滚动目标', () => {
  afterEach(() => {
    cleanup()
    document.body.replaceChildren()
  })

  function setupDocument(height = 5000) {
    const documentWrites: ScrollWrite[] = []
    const scroller = (document.scrollingElement as HTMLElement | null) ?? document.documentElement
    stubScrollMetrics(scroller, { height, target: 'document', writes: documentWrites })
    return { documentWrites }
  }

  function makeContainer(height: number) {
    const element = document.createElement('div')
    document.body.append(element)
    const writes: ScrollWrite[] = []
    stubScrollMetrics(element, { height, target: 'container', writes })
    return { element, writes }
  }

  it('桌面端容器还没挂上时，不会把整页滚到文档底部', () => {
    const { documentWrites } = setupDocument()

    renderHook(() => useConversationScroll({
      container: null,
      content: null,
      headKey: undefined,
      tailKey: 'tail',
      resetKey: 'orchestrator',
      target: 'container',
    }))

    expect(documentWrites).toEqual([])
  })

  it('容器挂上后只滚会话容器，不再碰整页', () => {
    const { documentWrites } = setupDocument()
    const { element, writes: containerWrites } = makeContainer(1200)

    const { rerender } = renderHook(
      ({ targetElement }: { targetElement: HTMLElement | null }) => useConversationScroll({
        container: targetElement,
        content: null,
        headKey: undefined,
        tailKey: 'tail',
        resetKey: 'orchestrator',
        target: 'container',
      }),
      { initialProps: { targetElement: null as HTMLElement | null } },
    )

    expect(documentWrites).toEqual([])

    rerender({ targetElement: element })

    expect(containerWrites).toContainEqual({ target: 'container', value: 1200 })
    expect(documentWrites).toEqual([])
  })

  it('手机布局（document）仍然在尾部变化时跟随整页滚动', () => {
    const { documentWrites } = setupDocument()

    renderHook(() => useConversationScroll({
      container: null,
      content: null,
      headKey: undefined,
      tailKey: 'tail',
      resetKey: 'orchestrator',
      target: 'document',
    }))

    expect(documentWrites).toContainEqual({ target: 'document', value: 5000 })
  })

  it('桌面端容器未就绪时 scrollToBottom 也不会落到整页', () => {
    const { documentWrites } = setupDocument()

    const { result } = renderHook(() => useConversationScroll({
      container: null,
      content: null,
      headKey: undefined,
      tailKey: 'tail',
      resetKey: 'orchestrator',
      target: 'container',
    }))

    result.current.scrollToBottom()

    expect(documentWrites).toEqual([])
  })
})
