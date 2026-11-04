/**
 * 压缩标记里的 token 数（M74 §4.6）：小于 10 000 写整数，否则写一位小数的「万」。
 * 例如 98213 → `9.8 万`，5000 → `5000`。
 */
export function formatTokens(value: number): string {
  if (!Number.isFinite(value)) return '0'
  const tokens = Math.max(0, value)
  if (tokens < 10_000) return String(Math.round(tokens))
  return `${(tokens / 10_000).toFixed(1)} 万`
}
