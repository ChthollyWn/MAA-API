import { toApiError } from '@/api/errors'

/** 就地显示的错误文案；连不上服务时给中文说明，而不是浏览器的英文报错。 */
export function errorText(error: unknown): string {
  const failure = toApiError(error)
  if (failure.status === undefined && !failure.code) return '无法连接服务，请检查网络后重试。'
  return failure.message
}

const timeFormat = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

export function formatTime(value?: string | null): string | null {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : timeFormat.format(parsed)
}

/** 把 User-Agent 缩成「iPhone · Safari」这样的称呼，方便认出是哪台设备。 */
export function describeUserAgent(userAgent?: string | null): string {
  if (!userAgent) return '未知浏览器'
  const device = /iPhone/.test(userAgent)
    ? 'iPhone'
    : /iPad/.test(userAgent)
      ? 'iPad'
      : /Android/.test(userAgent)
        ? 'Android'
        : /Macintosh|Mac OS X/.test(userAgent)
          ? 'Mac'
          : /Windows/.test(userAgent)
            ? 'Windows'
            : /Linux/.test(userAgent)
              ? 'Linux'
              : ''
  const browser = /Edg(e|A|iOS)?\//.test(userAgent)
    ? 'Edge'
    : /Firefox\/|FxiOS\//.test(userAgent)
      ? 'Firefox'
      : /Chrome\/|CriOS\//.test(userAgent)
        ? 'Chrome'
        : /Safari\//.test(userAgent)
          ? 'Safari'
          : ''
  return [device, browser].filter(Boolean).join(' · ') || '未知浏览器'
}
