import { useEffect, useState } from 'react'
import { isCoarsePointer } from '@/lib/media'

const TEXT_ENTRY =
  'input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]):not([type=reset]):not([type=range]):not([type=file]):not([type=color]), textarea, [contenteditable=""], [contenteditable="true"]'

/** 超过这个高度的 visualViewport 缺口才算虚拟键盘，排除地址栏收起等小幅变化。 */
const KEYBOARD_MIN_INSET = 120

function isTextEntry(element: Element | null): boolean {
  return element instanceof HTMLElement && (element.matches(TEXT_ENTRY) || element.isContentEditable)
}

/**
 * 跟踪触屏设备上的虚拟键盘：
 * - 在 `<html>` 上写 `data-tabbar="hidden|visible"`，底部 Tab 栏据此让位；
 * - 写 `--keyboard-inset`：iOS 键盘遮住的高度（Android 用 `interactive-widget=resizes-content`
 *   时布局视口本身会缩小，这里为 0），贴底元素据此上移到键盘之上。
 */
export function useVirtualKeyboard(): boolean {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const root = document.documentElement
    const viewport = window.visualViewport
    let pending: number | undefined

    const update = () => {
      const inset = viewport ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) : 0
      const keyboardInset = inset >= KEYBOARD_MIN_INSET ? inset : 0
      const focused = isCoarsePointer() && isTextEntry(document.activeElement)
      const keyboard = focused || keyboardInset > 0
      root.dataset.tabbar = keyboard ? 'hidden' : 'visible'
      root.style.setProperty('--keyboard-inset', `${Math.round(keyboardInset)}px`)
      setOpen(keyboard)
    }
    // 焦点在两个输入框之间移动时会先 focusout 再 focusin，稍等一下避免 Tab 栏闪一下。
    const scheduleUpdate = () => {
      window.clearTimeout(pending)
      pending = window.setTimeout(update, 60)
    }

    document.addEventListener('focusin', update)
    document.addEventListener('focusout', scheduleUpdate)
    viewport?.addEventListener('resize', update)
    viewport?.addEventListener('scroll', update)
    update()
    return () => {
      window.clearTimeout(pending)
      document.removeEventListener('focusin', update)
      document.removeEventListener('focusout', scheduleUpdate)
      viewport?.removeEventListener('resize', update)
      viewport?.removeEventListener('scroll', update)
      delete root.dataset.tabbar
      root.style.removeProperty('--keyboard-inset')
    }
  }, [])

  return open
}
