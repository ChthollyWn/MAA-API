import { useSyncExternalStore } from 'react'

/** jsdom 与部分旧浏览器没有 matchMedia，此时一律视为不匹配（按手机布局处理）。 */
export function matchesMedia(query: string): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches
}

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (notify) => {
      if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
      const media = window.matchMedia(query)
      media.addEventListener?.('change', notify)
      return () => media.removeEventListener?.('change', notify)
    },
    () => matchesMedia(query),
    () => false,
  )
}

export const DESKTOP_QUERY = '(min-width: 1024px)'
export const COARSE_POINTER_QUERY = '(pointer: coarse)'

/** 触屏为主的设备：虚拟键盘、Enter 换行等行为按它判断。 */
export function isCoarsePointer(): boolean {
  return matchesMedia(COARSE_POINTER_QUERY)
}
