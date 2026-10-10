import { useEffect, useState } from 'react'

/** 输入防抖：搜索框每敲一下都请求太吵，停 300 ms 再写进查询条件。 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs)
    return () => window.clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}
