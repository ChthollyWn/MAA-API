/**
 * Web Push 订阅管理（M14，docs/09 §12.4 / §13.1）。
 *
 * 分级降级是这一层的核心约束：Service Worker、Push API、Notification API 都要求
 * **安全上下文**。局域网 HTTP 地址（`http://192.168.x.x:8002`）下
 * `serviceWorker.register()` 会直接失败，所以这里按能力探测决定是否渲染入口，
 * **不是**先调用再用 try-catch 吞异常 —— 后者会在控制台刷错误、也会让用户看到
 * 一个永远点不动的按钮。
 *
 * 权限申请只允许由真实用户手势触发（设置页的「启用推送通知」按钮）：iOS 会直接
 * 忽略非手势触发的请求，Chromium 会记一次被动拒绝并影响后续提示。
 */

import { api } from '@/api/client'

/** Service Worker 与离线缓存是否可用。 */
export const PWA_AVAILABLE =
  typeof window !== 'undefined' &&
  window.isSecureContext &&
  'serviceWorker' in navigator

/** Web Push 是否可用（比 PWA_AVAILABLE 更严格）。 */
export const PUSH_AVAILABLE =
  PWA_AVAILABLE && typeof window !== 'undefined' && 'PushManager' in window

export type PushPermission = 'unsupported' | 'default' | 'granted' | 'denied'

export function currentPermission(): PushPermission {
  if (!PUSH_AVAILABLE) return 'unsupported'
  const permission = Notification.permission
  if (permission === 'granted' || permission === 'denied') return permission
  return 'default'
}

/**
 * `applicationServerKey` 必须是 VAPID 公钥的原始字节。
 *
 * 显式用 `ArrayBuffer` 构造：TypeScript 6 的 `BufferSource` 不接受
 * `ArrayBufferLike`（可能是 `SharedArrayBuffer`），直接传 `Uint8Array` 会报类型错。
 */
function base64UrlToArrayBuffer(value: string): ArrayBuffer {
  const padding = '='.repeat((4 - (value.length % 4)) % 4)
  const normalized = (value + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = window.atob(normalized)
  const buffer = new ArrayBuffer(raw.length)
  const bytes = new Uint8Array(buffer)
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index)
  }
  return buffer
}

async function registration(): Promise<ServiceWorkerRegistration> {
  // PwaUpdatePrompt 负责真正的注册；这里等它完成，避免重复 register。
  const existing = await navigator.serviceWorker.getRegistration()
  if (existing) return existing
  return navigator.serviceWorker.ready
}

async function fetchVapidPublicKey(): Promise<string> {
  const { data, error, response } = await api.GET(
    '/api/notifications/vapid-public-key',
  )
  if (error) {
    const status = response?.status ?? 0
    if (status === 503) {
      throw new Error('服务端尚未配置 VAPID 密钥，推送不可用。请先运行 scripts/generate_vapid_keys.py。')
    }
    throw new Error('读取推送公钥失败。')
  }
  const publicKey = (data as { public_key?: string } | undefined)?.public_key
  if (!publicKey) throw new Error('服务端返回的公钥为空。')
  return publicKey
}

/**
 * 申请权限并登记订阅。**必须**由用户手势调用。
 *
 * 返回登记后的订阅；已经存在订阅时直接复用（浏览器对同一 Service Worker 返回同一个
 * endpoint，服务端按 endpoint 幂等更新，因此重复调用是安全的）。
 */
export async function enablePush(): Promise<PushSubscription> {
  if (!PUSH_AVAILABLE) {
    throw new Error('当前不是安全上下文，推送通知不可用。请通过 HTTPS 访问。')
  }
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    throw new Error(
      permission === 'denied'
        ? '浏览器已拒绝通知权限。请在浏览器设置里重新允许后再试。'
        : '未获得通知权限，推送未启用。',
    )
  }

  const worker = await registration()
  const publicKey = await fetchVapidPublicKey()
  const subscription =
    (await worker.pushManager.getSubscription()) ??
    (await worker.pushManager.subscribe({
      // userVisibleOnly: true 是浏览器强制的，Service Worker 必须显示可见通知。
      userVisibleOnly: true,
      applicationServerKey: base64UrlToArrayBuffer(publicKey),
    }))

  const { error, response } = await api.POST(
    '/api/notifications/push-subscriptions',
    { body: subscription.toJSON() as never },
  )
  if (error) {
    // 服务端登记失败时撤销本地订阅，避免留下一个服务端不知道的订阅。
    await subscription.unsubscribe().catch(() => undefined)
    throw new Error(
      response?.status === 422
        ? '服务端拒绝了订阅参数，请更新应用后重试。'
        : '登记订阅失败，请稍后重试。',
    )
  }
  return subscription
}

/** 注销当前浏览器的订阅并从服务端移除。 */
export async function disablePush(): Promise<void> {
  if (!PUSH_AVAILABLE) return
  const worker = await registration()
  const subscription = await worker.pushManager.getSubscription()
  if (!subscription) return
  const endpoint = subscription.endpoint

  // 按 endpoint 精确注销：列表接口不回显 endpoint，而同一推送服务（如 FCM）下
  // 多台设备的主机名相同，按主机名删除会误伤其他设备的订阅。
  await api.POST('/api/notifications/push-subscriptions/delete', {
    body: { endpoint },
  })
  await subscription.unsubscribe().catch(() => undefined)
}

/** 当前浏览器是否已经登记过订阅（用于设置页显示状态）。 */
export async function hasActiveSubscription(): Promise<boolean> {
  if (!PUSH_AVAILABLE) return false
  const worker = await registration()
  const subscription = await worker.pushManager.getSubscription()
  return subscription !== null
}
