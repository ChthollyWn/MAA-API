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

function buildEnvironment({ online = true } = {}) {
  const listeners = new Map()
  const caches = new Map()
  const serverFiles = new Set(
    ['/index.html', '/assets/index-abc.js', '/manifest.webmanifest'].filter((relative) =>
      existsSync(path.join(STATIC_ROOT, relative.replace(/^\//, ''))),
    ),
  )
  serverFiles.add('/index.html')

  const context = {
    console,
    URL,
    indexedDB: undefined,
    fetch: async (input) => {
      if (!online) throw new Error('offline')
      const url = typeof input === 'string' ? input : input.url
      const pathname = new URL(url, 'https://example.test').pathname
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
      registration: { showNotification: async () => {} },
      clients: { matchAll: async () => [], openWindow: async () => {} },
      skipWaiting: async () => {},
    },
  }
  context.self.self = context.self
  context.globalThis = context

  vm.createContext(context)
  vm.runInContext(readFileSync(SW_PATH, 'utf8'), context, { filename: 'sw.js' })
  return { context, listeners, caches }
}

/** 触发一个事件并等待 waitUntil 链完成。 */
async function dispatch(listeners, type, event = {}) {
  const pending = []
  const wrapped = {
    ...event,
    waitUntil(promise) {
      pending.push(promise)
    },
  }
  for (const handler of listeners.get(type) ?? []) handler(wrapped)
  await Promise.all(pending)
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
let responded
await dispatch(listeners, 'fetch', {
  request: { method: 'GET', mode: 'cors', url: 'https://example.test/index.html' },
  respondWith(promise) {
    responded = promise
  },
})
check(responded !== undefined, '预缓存资源的 fetch 被接管')
check((await responded)?.body === 'precached:/index.html', '预缓存资源由缓存应答')

// 导航请求：API 路径不接管
let apiIntercepted = false
await dispatch(listeners, 'fetch', {
  request: { method: 'GET', mode: 'navigate', url: 'https://example.test/api/system/health' },
  respondWith() {
    apiIntercepted = true
  },
})
check(!apiIntercepted, '/api 导航不被 Service Worker 接管')

let docsIntercepted = false
await dispatch(listeners, 'fetch', {
  request: { method: 'GET', mode: 'navigate', url: 'https://example.test/docs' },
  respondWith() {
    docsIntercepted = true
  },
})
check(!docsIntercepted, '/docs 导航不被 Service Worker 接管')

// 非预缓存路径不接管
let otherIntercepted = false
await dispatch(listeners, 'fetch', {
  request: { method: 'GET', mode: 'cors', url: 'https://example.test/api/logs' },
  respondWith() {
    otherIntercepted = true
  },
})
check(!otherIntercepted, '未预缓存路径不被接管')

// 离线导航回退到外壳
const offline = buildEnvironment({ online: false })
await dispatch(offline.listeners, 'install')
let shell
await dispatch(offline.listeners, 'fetch', {
  request: { method: 'GET', mode: 'navigate', url: 'https://example.test/tasks' },
  respondWith(promise) {
    shell = promise
  },
})
check((await shell)?.body === 'precached:/index.html', '断网导航回退到缓存的外壳')

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
