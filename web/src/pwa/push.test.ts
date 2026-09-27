import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * `web/src/pwa/push.ts` 的能力探测与订阅流程。
 *
 * 重点在分级降级：非安全上下文下不能调用 `register()` / `requestPermission()`，
 * 否则控制台会刷错误、界面上也会出现点不动的按钮。
 */

const ORIGIN = 'https://example.test'
const PUBLIC_KEY_BYTES = 65

/** api 客户端在 import 期读 `window.location.origin`，所以 window stub 必须带 location。 */
function windowStub(options: { secure?: boolean; pushManager?: boolean } = {}) {
  const { secure = true, pushManager = true } = options
  return {
    isSecureContext: secure,
    location: { origin: ORIGIN },
    atob: (value: string) => Buffer.from(value, 'base64').toString('binary'),
    ...(pushManager ? { PushManager: function PushManager() {} } : {}),
  }
}

interface RegistrationStub {
  pushManager: {
    getSubscription: ReturnType<typeof vi.fn>
    subscribe: ReturnType<typeof vi.fn>
  }
}

let registrationStub: RegistrationStub

function resetRegistration(): void {
  registrationStub = {
    pushManager: { getSubscription: vi.fn(async () => null), subscribe: vi.fn() },
  }
}

function makeSubscription(endpoint = 'https://fcm.googleapis.com/fcm/send/x') {
  return {
    endpoint,
    toJSON: () => ({ endpoint, keys: { p256dh: 'p'.repeat(87), auth: 'a'.repeat(22) } }),
    unsubscribe: vi.fn(async () => true),
  }
}

function installServiceWorker(options: { present?: boolean } = {}): void {
  const { present = true } = options
  // 用一个干净的 navigator stub：jsdom 自带的 navigator 上有只读的 serviceWorker，
  // 直接 stubGlobal('navigator', {}) 也能让 `'serviceWorker' in navigator` 为 false。
  vi.stubGlobal('navigator', {
    ...(present
      ? {
          serviceWorker: {
            getRegistration: vi.fn(async () => registrationStub),
            ready: Promise.resolve(registrationStub),
          },
        }
      : {}),
  })
}

function base64Key(byteLength: number): string {
  return Buffer.alloc(byteLength, 4).toString('base64url')
}

describe('push capability detection', () => {
  beforeEach(() => {
    vi.resetModules()
    resetRegistration()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.doUnmock('@/api/client')
    vi.resetModules()
  })

  it('reports PUSH_AVAILABLE=false outside a secure context', async () => {
    vi.stubGlobal('window', windowStub({ secure: false }))
    installServiceWorker()
    const module = await import('./push')
    expect(module.PWA_AVAILABLE).toBe(false)
    expect(module.PUSH_AVAILABLE).toBe(false)
    expect(module.currentPermission()).toBe('unsupported')
  })

  it('reports PUSH_AVAILABLE=false when the Service Worker API is missing', async () => {
    vi.stubGlobal('window', windowStub())
    installServiceWorker({ present: false })
    const module = await import('./push')
    expect(module.PWA_AVAILABLE).toBe(false)
    expect(module.PUSH_AVAILABLE).toBe(false)
  })

  it('reports PUSH_AVAILABLE=false when PushManager is missing', async () => {
    vi.stubGlobal('window', windowStub({ pushManager: false }))
    installServiceWorker()
    const module = await import('./push')
    expect(module.PWA_AVAILABLE).toBe(true)
    expect(module.PUSH_AVAILABLE).toBe(false)
    expect(module.currentPermission()).toBe('unsupported')
  })

  it('maps the browser permission to a display state', async () => {
    vi.stubGlobal('window', windowStub())
    installServiceWorker()
    vi.stubGlobal('Notification', { permission: 'denied', requestPermission: vi.fn() })
    const module = await import('./push')
    expect(module.PUSH_AVAILABLE).toBe(true)
    expect(module.currentPermission()).toBe('denied')
  })

  it('refuses to subscribe when the environment is not supported', async () => {
    vi.stubGlobal('window', windowStub({ secure: false }))
    installServiceWorker()
    const requestPermission = vi.fn()
    vi.stubGlobal('Notification', { permission: 'default', requestPermission })

    const module = await import('./push')
    await expect(module.enablePush()).rejects.toThrow(/安全上下文/)
    expect(requestPermission).not.toHaveBeenCalled()
  })
})

describe('enablePush', () => {
  beforeEach(() => {
    vi.resetModules()
    resetRegistration()
    vi.stubGlobal('window', windowStub())
    installServiceWorker()
    vi.stubGlobal('Notification', {
      permission: 'granted',
      requestPermission: vi.fn(async () => 'granted'),
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.doUnmock('@/api/client')
    vi.resetModules()
  })

  function mockApi(overrides: Record<string, unknown> = {}) {
    const api = {
      GET: vi.fn(async () => ({
        data: { public_key: base64Key(PUBLIC_KEY_BYTES), subject: 'mailto:x@example.com' },
      })),
      POST: vi.fn(async () => ({ data: { id: 'sub-1' } })),
      DELETE: vi.fn(),
      ...overrides,
    }
    vi.doMock('@/api/client', () => ({ api }))
    return api
  }

  it('requests permission only when called, and registers the subscription', async () => {
    const subscription = makeSubscription()
    registrationStub.pushManager.subscribe = vi.fn(async () => subscription)
    const api = mockApi()

    const module = await import('./push')
    const result = await module.enablePush()

    expect(Notification.requestPermission).toHaveBeenCalledTimes(1)
    expect(api.GET).toHaveBeenCalledWith('/api/notifications/vapid-public-key')
    expect(registrationStub.pushManager.subscribe).toHaveBeenCalledTimes(1)
    const options = registrationStub.pushManager.subscribe.mock.calls[0][0]
    expect(options.userVisibleOnly).toBe(true)
    // 公钥以 ArrayBuffer 传入（TypeScript 6 的 BufferSource 不接受 ArrayBufferLike）
    expect(options.applicationServerKey).toBeInstanceOf(ArrayBuffer)
    expect((options.applicationServerKey as ArrayBuffer).byteLength).toBe(PUBLIC_KEY_BYTES)
    expect(api.POST).toHaveBeenCalledWith('/api/notifications/push-subscriptions', {
      body: expect.objectContaining({ endpoint: subscription.endpoint }),
    })
    expect(result).toBe(subscription)
  })

  it('reuses an existing subscription instead of subscribing twice', async () => {
    const existing = makeSubscription()
    registrationStub.pushManager.getSubscription = vi.fn(async () => existing)
    const api = mockApi()

    const module = await import('./push')
    await module.enablePush()

    expect(registrationStub.pushManager.subscribe).not.toHaveBeenCalled()
    expect(api.POST).toHaveBeenCalledTimes(1)
  })

  it('reports a missing VAPID key with an actionable message', async () => {
    mockApi({
      GET: vi.fn(async () => ({
        error: { code: 'PUSH_NOT_CONFIGURED' },
        response: { status: 503 },
      })),
    })

    const module = await import('./push')
    await expect(module.enablePush()).rejects.toThrow(/VAPID/)
    expect(registrationStub.pushManager.subscribe).not.toHaveBeenCalled()
  })

  it('unsubscribes locally when the server rejects the subscription', async () => {
    const subscription = makeSubscription()
    registrationStub.pushManager.subscribe = vi.fn(async () => subscription)
    mockApi({
      POST: vi.fn(async () => ({
        error: { code: 'PUSH_SUBSCRIPTION_INVALID' },
        response: { status: 422 },
      })),
    })

    const module = await import('./push')
    await expect(module.enablePush()).rejects.toThrow(/拒绝了订阅参数/)
    expect(subscription.unsubscribe).toHaveBeenCalledTimes(1)
  })

  it('refuses to continue when the user denies permission', async () => {
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: vi.fn(async () => 'denied'),
    })
    const api = mockApi()

    const module = await import('./push')
    await expect(module.enablePush()).rejects.toThrow(/拒绝/)
    expect(api.POST).not.toHaveBeenCalled()
  })
})

describe('disablePush', () => {
  beforeEach(() => {
    vi.resetModules()
    resetRegistration()
    vi.stubGlobal('window', windowStub())
    installServiceWorker()
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn() })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.doUnmock('@/api/client')
    vi.resetModules()
  })

  it('deletes by endpoint, not by host, and unsubscribes locally', async () => {
    const subscription = makeSubscription('https://fcm.googleapis.com/fcm/send/mine')
    registrationStub.pushManager.getSubscription = vi.fn(async () => subscription)
    const api = {
      GET: vi.fn(),
      POST: vi.fn(async () => ({ data: undefined })),
      DELETE: vi.fn(),
    }
    vi.doMock('@/api/client', () => ({ api }))

    const module = await import('./push')
    await module.disablePush()

    // 同一推送服务下多台设备主机名相同，按主机名删除会误伤别人的订阅
    expect(api.POST).toHaveBeenCalledWith('/api/notifications/push-subscriptions/delete', {
      body: { endpoint: subscription.endpoint },
    })
    expect(api.GET).not.toHaveBeenCalled()
    expect(subscription.unsubscribe).toHaveBeenCalledTimes(1)
  })

  it('is a no-op when there is no local subscription', async () => {
    const api = { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn() }
    vi.doMock('@/api/client', () => ({ api }))

    const module = await import('./push')
    await module.disablePush()
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('reports whether this browser already has a subscription', async () => {
    const subscription = makeSubscription()
    registrationStub.pushManager.getSubscription = vi.fn(async () => subscription)
    vi.doMock('@/api/client', () => ({
      api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn() },
    }))

    const module = await import('./push')
    expect(await module.hasActiveSubscription()).toBe(true)
  })
})
