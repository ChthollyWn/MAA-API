/**
 * 在 Node 里执行构建产物 static/sw.js，验证 Service Worker 的预缓存与离线外壳。
 *
 * 单元测试跑的是源码，而 sw.js 是「注入清单 + 压缩」之后的产物 —— 前导斜杠缺失
 * 这类问题只在产物里出现（安装看起来成功、caches.open 也不报错，只有真断网才发现
 * 什么都没缓存）。所以这里必须执行真产物。
 */
import { readFileSync, existsSync } from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const WEB_ROOT = path.dirname(fileURLToPath(import.meta.url))
const SW_PATH = path.resolve(WEB_ROOT, '../../static/sw.js')
const STATIC_ROOT = path.resolve(WEB_ROOT, '../../static')

const checks = []
function check(condition, label) {
  checks.push([Boolean(condition), label])
}

if (!existsSync(SW_PATH)) {
  console.error(`[sw_harness] 缺少构建产物 ${SW_PATH}，请先运行 pnpm build`)
  process.exit(1)
}

/** 极简 Cache API：只实现本 Service Worker 用到的部分。 */
function createCacheStore() {
  const entries = new Map()
  return {
    entries,
    async addAll(urls) {
      // 真实现会去 fetch 每个 URL，任何一个 404 都会让 install 整体失败。
      // 替身至少校验文件确实存在于构建产物里，否则「清单里有、磁盘上没有」这种
      // 会让线上安装直接失败的组合在这里是测不出来的。
      const missing = urls.filter(
        (url) => !existsSync(path.join(STATIC_ROOT, url.replace(/^\//, ''))),
      )
      if (missing.length > 0) {
        throw new Error(`预缓存清单里有 ${missing.length} 个 URL 在构建产物中不存在：${missing.slice(0, 5)}`)
      }
      for (const url of urls) entries.set(url, { url, body: `precached:${url}` })
    },
    async match(url) {
      return entries.get(url) ?? undefined
    },
    async put(url, response) {
      entries.set(url, response)
    },
  }
}

function buildEnvironment({ online = true, token = 'harness-token', clients = [] } = {}) {
  const listeners = new Map()
  const caches = new Map()
  const notifications = []
  const fetches = []
  const opened = []
  const focused = []
  const navigated = []
  const serverFiles = new Set(
    ['/index.html', '/assets/index-abc.js', '/manifest.webmanifest'].filter((relative) =>
      existsSync(path.join(STATIC_ROOT, relative.replace(/^\//, ''))),
    ),
  )
  serverFiles.add('/index.html')

  // 最小 IndexedDB 替身：只服务 Service Worker 取 token 的那条路径，
  // 用来验证它确实从 IndexedDB（而不是别处）拿凭据。
  const idbStore = new Map()
  if (token !== null) idbStore.set('token', token)
  const indexedDBStub = {
    open() {
      const request = {}
      queueMicrotask(() => {
        request.result = {
          objectStoreNames: { contains: () => true },
          transaction: () => ({
            objectStore: () => ({
              get(key) {
                const get = {}
                queueMicrotask(() => {
                  get.result = idbStore.get(key)
                  get.onsuccess?.()
                })
                return get
              },
            }),
          }),
          close() {},
        }
        request.onsuccess?.()
      })
      return request
    },
  }

  const context = {
    console,
    URL,
    indexedDB: indexedDBStub,
    fetch: async (input, init) => {
      const url = typeof input === 'string' ? input : input.url
      const pathname = new URL(url, 'https://example.test').pathname
      fetches.push({ url, method: init?.method, headers: init?.headers, body: init?.body })
      if (!online) throw new Error('offline')
      if (pathname.startsWith('/api/')) {
        return { ok: true, status: 200, clone() { return this }, body: 'api' }
      }
      if (!serverFiles.has(pathname)) return { ok: false, status: 404 }
      return { ok: true, status: 200, clone() { return this }, body: `network:${pathname}` }
    },
    caches: {
      async open(name) {
        if (!caches.has(name)) caches.set(name, createCacheStore())
        return caches.get(name)
      },
      async keys() {
        return [...caches.keys()]
      },
      async delete(name) {
        return caches.delete(name)
      },
    },
    self: {
      addEventListener(type, handler) {
        if (!listeners.has(type)) listeners.set(type, [])
        listeners.get(type).push(handler)
      },
      location: { origin: 'https://example.test' },
      registration: {
        showNotification: async (title, options) => {
          notifications.push({ title, options })
        },
      },
      clients: {
        matchAll: async () => clients,
        openWindow: async (url) => {
          opened.push(url)
          return null
        },
      },
      skipWaiting: async () => {},
    },
  }
  context.self.self = context.self
  context.globalThis = context

  vm.createContext(context)
  vm.runInContext(readFileSync(SW_PATH, 'utf8'), context, { filename: 'sw.js' })
  return { context, listeners, caches, notifications, fetches, opened, focused, navigated }
}

/**
 * 调用一次 `fetch` 监听器并取回它的 `respondWith` 结果。
 *
 * 守卫在这里而不是散落在调用点：任何一个 respondWith 被拒绝（例如离线且没有
 * 缓存外壳）都不该让整份报告在打印前中断 —— 那会只剩一句原始堆栈、一条标签都看不到。
 */
async function dispatchFetch(listeners, event) {
  let responded
  let intercepted = false
  await dispatch(listeners, 'fetch', {
    ...event,
    respondWith(promise) {
      intercepted = true
      responded = promise
    },
  })
  if (!intercepted) return { intercepted: false, response: undefined, error: undefined }
  try {
    return { intercepted: true, response: await responded, error: undefined }
  } catch (error) {
    return { intercepted: true, response: undefined, error }
  }
}

/**
 * 触发一个事件并等待 waitUntil 链完成。
 *
 * 拒绝**不向上抛**：抛出去会让整份报告在打印前中断（曾经表现为只看到一句
 * `offline and no cached shell`、20 项标签一条都不打印）。这里把它记成一条失败
 * 检查，让后面的用例继续跑完。
 */
async function dispatch(listeners, type, event = {}) {
  const pending = []
  const wrapped = {
    ...event,
    waitUntil(promise) {
      pending.push(promise)
    },
  }
  for (const handler of listeners.get(type) ?? []) handler(wrapped)
  const settled = await Promise.allSettled(pending)
  for (const item of settled) {
    if (item.status === 'rejected') {
      check(false, `${type} 的 waitUntil 被拒绝：${item.reason?.message ?? item.reason}`)
    }
  }
}

const { listeners, caches } = buildEnvironment()

for (const type of ['install', 'activate', 'fetch', 'push', 'notificationclick']) {
  check(listeners.has(type), `sw.js 注册了 ${type} 监听器`)
}

await dispatch(listeners, 'install')
const [cacheName] = [...caches.keys()]
const store = caches.get(cacheName)
check(caches.size === 1, 'install 建立了唯一预缓存')
check(store.entries.size > 10, `预缓存条数 > 10（实际 ${store.entries.size}）`)
check(store.entries.has('/index.html'), '预缓存包含带前导斜杠的 /index.html')
check(
  [...store.entries.keys()].every((url) => url.startsWith('/')),
  '预缓存的所有 URL 都带前导斜杠',
)
check(
  [...store.entries.keys()].some((url) => url.startsWith('/assets/')),
  '预缓存包含 /assets/ 下的构建产物',
)

await dispatch(listeners, 'activate')

// 预缓存资源：缓存优先
const precached = await dispatchFetch(listeners, {
  request: { method: 'GET', mode: 'cors', url: 'https://example.test/index.html' },
})
check(precached.intercepted, '预缓存资源的 fetch 被接管')
check(precached.response?.body === 'precached:/index.html', '预缓存资源由缓存应答')

// 导航请求：API 文档路径不接管
const api = await dispatchFetch(listeners, {
  request: { method: 'GET', mode: 'navigate', url: 'https://example.test/api/system/health' },
})
check(!api.intercepted, '/api 导航不被 Service Worker 接管')

const docs = await dispatchFetch(listeners, {
  request: { method: 'GET', mode: 'navigate', url: 'https://example.test/docs' },
})
check(!docs.intercepted, '/docs 导航不被 Service Worker 接管')

// 非预缓存路径不接管
const other = await dispatchFetch(listeners, {
  request: { method: 'GET', mode: 'cors', url: 'https://example.test/api/logs' },
})
check(!other.intercepted, '未预缓存路径不被接管')

// 离线导航回退到外壳
const offline = buildEnvironment({ online: false })
await dispatch(offline.listeners, 'install')
const shell = await dispatchFetch(offline.listeners, {
  request: { method: 'GET', mode: 'navigate', url: 'https://example.test/tasks' },
})
check(shell.error === undefined, `离线导航没有抛错（${shell.error?.message}）`)
check(shell.response?.body === 'precached:/index.html', '断网导航回退到缓存的外壳')

// ---------------------------------------------------------------------------
// notificationclick：三种入口（approve / reject / 点通知体）
// ---------------------------------------------------------------------------

/** 造一个已打开的窗口替身，记录 focus 与 navigate。 */
function windowClient({ url = 'https://example.test/agent', navigateRejects = false } = {}) {
  const record = { focused: false, navigated: [] }
  return {
    url,
    record,
    async focus() {
      record.focused = true
    },
    async navigate(target) {
      if (navigateRejects) throw new TypeError('not controlled by this worker')
      record.navigated.push(target)
    },
  }
}

function clickEvent({ action = '', data = {} }) {
  return {
    action,
    notification: { data, close() {} },
  }
}

async function runNotificationClickChecks() {
  const confirmationId = 'confirm-abc'
  const notificationData = {
    confirmationId,
    url: `/?confirm=${confirmationId}`,
  }

  // (a) action=approve：必须带 X-Token，且 approved 必须是 true
  {
    const env = buildEnvironment({ token: 'harness-token' })
    await dispatch(env.listeners, 'install')
    await dispatch(env.listeners, 'notificationclick', clickEvent({
      action: 'approve',
      data: notificationData,
    }))
    const call = env.fetches.find((item) => item.url.includes('/api/confirmations/'))
    check(call !== undefined, 'notificationclick approve 提交了确认')
    check(
      call?.url.endsWith(`/api/confirmations/${confirmationId}`),
      `approve 提交到正确的确认端点（实际 ${call?.url}）`,
    )
    check(call?.method === 'POST', 'approve 用 POST 提交')
    check(
      call?.headers?.['X-Token'] === 'harness-token',
      'approve 从 IndexedDB 取 token 注入 X-Token（写操作不能只靠 cookie）',
    )
    check(
      JSON.parse(call?.body ?? '{}').approved === true,
      'approve 的 approved 必须是 true',
    )
    check(env.notifications.length === 1, 'approve 后回了一条结果通知')
  }

  // (b) action=reject：approved 必须是 false
  {
    const env = buildEnvironment({ token: 'harness-token' })
    await dispatch(env.listeners, 'install')
    await dispatch(env.listeners, 'notificationclick', clickEvent({
      action: 'reject',
      data: notificationData,
    }))
    const call = env.fetches.find((item) => item.url.includes('/api/confirmations/'))
    check(
      JSON.parse(call?.body ?? '{}').approved === false,
      'reject 的 approved 必须是 false（极性不能反）',
    )
  }

  // (c) 没有 token 时不带 X-Token，但仍然提交
  {
    const env = buildEnvironment({ token: null })
    await dispatch(env.listeners, 'install')
    await dispatch(env.listeners, 'notificationclick', clickEvent({
      action: 'approve',
      data: notificationData,
    }))
    const call = env.fetches.find((item) => item.url.includes('/api/confirmations/'))
    check(call !== undefined, '缺少 token 时仍提交确认（由服务端判定鉴权）')
    check(
      call?.headers?.['X-Token'] === undefined,
      '缺少 token 时不应注入空的 X-Token',
    )
  }

  // (d) 点击通知体（action 为空字符串）：复用已打开窗口并导航，不开新窗口
  {
    const existing = windowClient()
    const env = buildEnvironment({ clients: [existing] })
    await dispatch(env.listeners, 'install')
    await dispatch(env.listeners, 'notificationclick', clickEvent({ data: notificationData }))
    check(existing.record.focused, '点通知体时聚焦已打开的窗口')
    check(
      existing.record.navigated.includes(`https://example.test/?confirm=${confirmationId}`),
      '点通知体时在已打开窗口里导航到深链',
    )
    check(env.opened.length === 0, '已有窗口时不再开新窗口')
    check(
      env.fetches.every((item) => !item.url.includes('/api/confirmations/')),
      '点通知体只做深链，不代为处置确认',
    )
  }

  // (e) 没有已打开窗口：开新窗口
  {
    const env = buildEnvironment({ clients: [] })
    await dispatch(env.listeners, 'install')
    await dispatch(env.listeners, 'notificationclick', clickEvent({ data: notificationData }))
    check(
      env.opened.includes(`https://example.test/?confirm=${confirmationId}`),
      '没有已打开窗口时开新窗口到深链',
    )
  }

  // (f) 未受控窗口拒绝 navigate：必须退回开新窗口，而不是静默失败
  {
    const uncontrolled = windowClient({ navigateRejects: true })
    const env = buildEnvironment({ clients: [uncontrolled] })
    await dispatch(env.listeners, 'install')
    await dispatch(env.listeners, 'notificationclick', clickEvent({ data: notificationData }))
    check(
      env.opened.includes(`https://example.test/?confirm=${confirmationId}`),
      '未受控窗口无法导航时退回 openWindow（否则点通知毫无反应）',
    )
  }

  // (g) 处置失败（409 已过期）要有结果通知，不能静默
  {
    const env = buildEnvironment({ token: 'harness-token' })
    env.context.self.registration.showNotification = async (title, options) => {
      env.notifications.push({ title, options })
    }
    const originalFetch = env.context.fetch
    env.context.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url
      if (url.includes('/api/confirmations/')) {
        return { ok: false, status: 409, clone() { return this }, body: '' }
      }
      return originalFetch(input, init)
    }
    await dispatch(env.listeners, 'install')
    await dispatch(env.listeners, 'notificationclick', clickEvent({
      action: 'approve',
      data: notificationData,
    }))
    const result = env.notifications.at(-1)
    check(
      result?.title?.includes('过期') === true,
      `确认已过期时回一条说明结果的通知（实际 ${result?.title}）`,
    )
  }

  // (h) 通知上没有 actions 时（iOS）仍能靠点击通知体走深链
  {
    const existing = windowClient()
    const env = buildEnvironment({ clients: [existing] })
    await dispatch(env.listeners, 'install')
    await dispatch(env.listeners, 'notificationclick', clickEvent({ action: '', data: notificationData }))
    check(existing.record.focused, 'iOS 路径（无 action）仍然走深链')
  }
}

await runNotificationClickChecks()

// push 必须显示通知（userVisibleOnly 的硬性要求）
const notifications = []
const pushEnv = buildEnvironment()
pushEnv.context.self.registration.showNotification = async (title, options) => {
  notifications.push({ title, options })
}
await dispatch(pushEnv.listeners, 'install')
await dispatch(pushEnv.listeners, 'push', {
  data: {
    json: () => ({
      title: '需要确认：碎石 3 颗',
      body: '消耗 3 颗源石',
      tag: 'confirm:c-1',
      data: { confirmationId: 'c-1', url: '/?confirm=c-1' },
      actions: [{ action: 'approve', title: '准许' }],
    }),
  },
})
check(notifications.length === 1, 'push 事件显示了一条通知')
check(
  notifications[0]?.options?.tag === 'confirm:c-1',
  'push 通知使用 confirmation 的 tag 做同类合并',
)
check(
  notifications[0]?.options?.data?.confirmationId === 'c-1',
  'push 通知的 data 带 confirmationId',
)

// push 载荷损坏时降级成通用通知而不是静默丢弃
notifications.length = 0
await dispatch(pushEnv.listeners, 'push', {
  data: {
    json: () => {
      throw new Error('bad json')
    },
    text: () => 'raw',
  },
})
check(notifications.length === 1, '损坏的 push 载荷仍然显示通知')

let failures = 0
for (const [ok, label] of checks) {
  if (!ok) failures += 1
  console.log(`${ok ? '[ok]' : '[FAIL]'} ${label}`)
}
if (failures > 0) {
  console.error(`[sw_harness] 失败 ${failures} 项`)
  process.exit(1)
}
console.log('[sw_harness] SERVICE WORKER OK')
