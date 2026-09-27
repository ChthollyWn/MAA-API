/// <reference lib="webworker" />
/**
 * 自定义 Service Worker（M14）。
 *
 * 为什么必须自己写而不是用 `generateSW`：生成的 Worker 只有预缓存与导航回退，
 * **没有 `push` 事件处理**，而 Web Push 的通知完全依赖它。浏览器要求
 * `userVisibleOnly: true` 的订阅每次推送都必须显示一条可见通知，否则订阅会被撤销，
 * 所以 `showNotification()` 不能省。
 *
 * 预缓存不用 Workbox：`vite-plugin-pwa` 的 `injectManifest` 只是在源码里把
 * `self.__WB_MANIFEST` 替换成构建清单（纯文本注入），因此这里直接用 Cache API
 * 实现「安装时预缓存 + 清理旧版本缓存」，不引入额外运行时依赖。
 *
 * 与页面共享的约定：
 * - token 存在 IndexedDB（`maa-api` / `auth` / `token`），不是 localStorage ——
 *   Service Worker 不是 window 上下文，读不到 localStorage。
 * - 首页确认卡片接受 `/?confirm=<id>` 深链。
 */

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<{ url: string; revision: string | null }>
}

/** 构建时被注入：`[{ url, revision }]`。 */
const PRECACHE_ENTRIES = self.__WB_MANIFEST ?? []

const CACHE_PREFIX = 'maa-api-precache'
/** 清单里没有版本号，用条目集合的指纹当缓存名，内容变了就换缓存。 */
const CACHE_NAME = `${CACHE_PREFIX}-${PRECACHE_ENTRIES.length}-${simpleHash(
  PRECACHE_ENTRIES.map((entry) => entry.url).sort().join('|'),
)}`

/** 稳定的小指纹：只为区分构建产物，不用于任何安全用途。 */
function simpleHash(value: string): string {
  let hash = 5381
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) + hash + value.charCodeAt(index)) >>> 0
  }
  return hash.toString(36)
}

/** 这些路径永远不走离线外壳：它们必须由服务端实时回答。 */
const NAVIGATION_DENYLIST = [
  /^\/api(?:\/|$)/,
  /^\/docs(?:\/|$)/,
  /^\/redoc(?:\/|$)/,
  /^\/openapi\.json$/,
]

/**
 * 清单里的 URL **不带前导斜杠**（`assets/foo.js`、`index.html`），而 `URL.pathname`
 * 一定带，所以两边都先归一化再比较 —— 直接比较会让预缓存静默为空：安装看起来成功、
 * `caches.open` 也不报错，只有真断网时才发现什么都没缓存。
 */
function precachePath(rawUrl: string): string {
  const path = rawUrl.split('?')[0]
  return path.startsWith('/') ? path : `/${path}`
}

const PRECACHE_PATHS = new Set(PRECACHE_ENTRIES.map((entry) => precachePath(entry.url)))

function isPrecached(url: URL): boolean {
  return PRECACHE_PATHS.has(url.pathname)
}

self.addEventListener('install', (event) => {
  // 更新由页面的 prompt 组件驱动：这里只准备缓存，不自行 skipWaiting，
  // 否则用户会在没点「立即更新」的情况下被换到新版本。
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME)
      await cache.addAll([...PRECACHE_PATHS])
    })(),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys()
      await Promise.all(
        names
          .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
          .map((name) => caches.delete(name)),
      )
    })(),
  )
})

self.addEventListener('message', (event) => {
  if ((event.data as { type?: string } | null)?.type === 'SKIP_WAITING') {
    void self.skipWaiting()
  }
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return

  // 导航：离线时回退到外壳，但 API 与文档路径必须打到网络。
  if (request.mode === 'navigate') {
    if (NAVIGATION_DENYLIST.some((pattern) => pattern.test(url.pathname))) return
    event.respondWith(
      (async () => {
        try {
          return await fetch(request)
        } catch {
          const cache = await caches.open(CACHE_NAME)
          const shell = await cache.match('/index.html')
          if (shell) return shell
          throw new Error('offline and no cached shell')
        }
      })(),
    )
    return
  }

  // 预缓存资源：缓存优先；未命中再走网络。
  if (!isPrecached(url)) return
  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE_NAME)
      const cached = await cache.match(url.pathname)
      if (cached) return cached
      const response = await fetch(request)
      if (response.ok) await cache.put(url.pathname, response.clone())
      return response
    })(),
  )
})

interface PushPayload {
  title?: string
  body?: string
  tag?: string
  data?: { confirmationId?: string; url?: string }
  actions?: Array<{ action: string; title: string }>
}

const DEFAULT_TITLE = 'MAA-API'
const DEFAULT_BODY = '有一项操作需要你确认。'

/**
 * TypeScript 的 DOM lib 在 `NotificationOptions` 里没有声明 `actions`
 * （它在 Notification API 规范里，但类型定义滞后），而 Chromium 会渲染它。
 * 这里显式扩展而不是 `as any`，保证其余字段仍受类型检查。
 */
type NotificationOptionsWithActions = NotificationOptions & {
  actions?: Array<{ action: string; title: string; icon?: string }>
}

/** 读取推送载荷；解析失败时降级成一条通用通知而不是静默丢弃。 */
function readPayload(event: PushEvent): PushPayload {
  try {
    return (event.data?.json() ?? {}) as PushPayload
  } catch {
    return { body: event.data?.text() }
  }
}

self.addEventListener('push', (event) => {
  const payload = readPayload(event)
  const confirmationId = payload.data?.confirmationId
  // actions 只是 Chromium 上的渐进增强：WebKit 会静默忽略它们，
  // 所以 title/body 必须自带完整信息，流程也不能以按钮为前提。
  const actions = (payload.actions ?? []).slice(0, 2)
  event.waitUntil(
    self.registration.showNotification(payload.title || DEFAULT_TITLE, {
      body: payload.body || DEFAULT_BODY,
      tag: payload.tag || (confirmationId ? `confirm:${confirmationId}` : undefined),
      data: payload.data ?? {},
      actions,
      icon: '/pwa-192x192.png',
      badge: '/pwa-192x192.png',
    } as NotificationOptionsWithActions),
  )
})

const DB_NAME = 'maa-api'
const DB_STORE = 'auth'
const TOKEN_KEY = 'token'

/** 从 IndexedDB 取认证 token（Worker 读不到 localStorage）。 */
function readToken(): Promise<string | null> {
  return new Promise((resolve) => {
    let request: IDBOpenDBRequest
    try {
      request = indexedDB.open(DB_NAME, 1)
    } catch {
      resolve(null)
      return
    }
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(DB_STORE)) db.createObjectStore(DB_STORE)
    }
    request.onerror = () => resolve(null)
    request.onsuccess = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(DB_STORE)) {
        db.close()
        resolve(null)
        return
      }
      const transaction = db.transaction(DB_STORE, 'readonly')
      const get = transaction.objectStore(DB_STORE).get(TOKEN_KEY)
      get.onsuccess = () => {
        db.close()
        resolve(typeof get.result === 'string' && get.result ? get.result : null)
      }
      get.onerror = () => {
        db.close()
        resolve(null)
      }
    }
  })
}

/**
 * 打开应用已存在的窗口并导航，找不到才新开一个。
 *
 * 复用窗口必须能失败：``includeUncontrolled: true`` 会返回**不受本 Worker 控制**的
 * 窗口（``clientsClaim`` 保持 false，安装/更新后第一次点通知就是这种情况），而按
 * 规范 ``navigate()`` 对这类窗口会以 ``TypeError`` 拒绝。不接住它，深链既打不开
 * 也不会退回 ``openWindow`` —— 用户点了通知什么都不会发生。
 */
async function focusOrOpen(url: string): Promise<void> {
  const target = new URL(url, self.location.origin).href
  const windows = await self.clients.matchAll({
    type: 'window',
    includeUncontrolled: true,
  })
  for (const client of windows) {
    if (new URL(client.url).origin !== self.location.origin) continue
    try {
      await client.focus()
      const navigable = client as WindowClient
      if (typeof navigable.navigate === 'function') await navigable.navigate(target)
      return
    } catch {
      // 未受控窗口或导航被拒：继续尝试下一个，最后退回开新窗口。
      continue
    }
  }
  await self.clients.openWindow(target)
}

/**
 * 在通知上直接处置确认（Chromium 渐进增强）。
 *
 * 必须显式带 `X-Token`：写操作不走 cookie 鉴权，同源 fetch 自动携带的 cookie
 * 不足以通过。无论成功失败都要回一条结果通知 —— 确认可能已在此期间超时，
 * 静默失败会让用户以为批准成功了。
 */
async function resolveConfirmation(
  confirmationId: string,
  approved: boolean,
): Promise<void> {
  const token = await readToken()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['X-Token'] = token
  const resultTag = `confirm-result:${confirmationId}`
  try {
    const response = await fetch(
      `/api/confirmations/${encodeURIComponent(confirmationId)}`,
      {
        method: 'POST',
        headers,
        credentials: 'same-origin',
        body: JSON.stringify({ approved }),
      },
    )
    const expired = response.status === 409
    await self.registration.showNotification(
      response.ok
        ? approved
          ? '已准许'
          : '已拒绝'
        : expired
          ? '操作已过期，未执行'
          : '操作未能提交',
      {
        body: response.ok
          ? 'Agent 已按你的选择继续。'
          : expired
            ? '这项确认在等待期间已超时，副作用没有发生。'
            : `服务返回 ${response.status}，请打开应用查看。`,
        tag: resultTag,
        data: { url: `/?confirm=${encodeURIComponent(confirmationId)}` },
      },
    )
  } catch {
    await self.registration.showNotification('操作未能提交', {
      body: '网络不可达，请打开应用重试。',
      tag: resultTag,
      data: { url: `/?confirm=${encodeURIComponent(confirmationId)}` },
    })
  }
}

self.addEventListener('notificationclick', (event) => {
  const notification = event.notification
  notification.close()
  const data = (notification.data ?? {}) as {
    confirmationId?: string
    url?: string
  }
  const url =
    data.url || `/?confirm=${encodeURIComponent(data.confirmationId ?? '')}`

  // 注意 action 在「点击通知体」时是空字符串而不是 undefined，
  // 所以要按具体值判断，不能判空。
  if (event.action === 'approve' || event.action === 'reject') {
    if (!data.confirmationId) return
    event.waitUntil(
      resolveConfirmation(data.confirmationId, event.action === 'approve'),
    )
    return
  }
  event.waitUntil(focusOrOpen(url))
})
