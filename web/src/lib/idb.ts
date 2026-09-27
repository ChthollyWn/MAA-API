/**
 * IndexedDB 里的认证 token（M14）。
 *
 * Service Worker 不是 window 上下文，读不到 localStorage（那是同步 API）。而点击
 * 推送通知上的「准许 / 拒绝」时，处置逻辑跑在 Worker 的 `notificationclick` 里，
 * 页面可能根本没打开 —— 所以 token 必须额外写一份到 IndexedDB 供 Worker 读取。
 *
 * 写操作需要显式凭据头，同源 fetch 自动带的 cookie 不足以通过鉴权，因此这份副本
 * 是功能必需而非优化。
 *
 * **三处存储必须同步**：localStorage（页面）、Zustand 内存态、IndexedDB（Worker）。
 * 清除 token 时必须一起清，否则登出后 Worker 仍能用一个陈旧 token 提交确认。
 */

export const AUTH_DB_NAME = 'maa-api'
export const AUTH_DB_VERSION = 1
export const AUTH_DB_STORE = 'auth'
export const AUTH_TOKEN_KEY = 'token'

function hasIndexedDb(): boolean {
  return typeof indexedDB !== 'undefined'
}

function openAuthDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(AUTH_DB_NAME, AUTH_DB_VERSION)
    request.onupgradeneeded = () => {
      const database = request.result
      if (!database.objectStoreNames.contains(AUTH_DB_STORE)) {
        database.createObjectStore(AUTH_DB_STORE)
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('indexedDB open failed'))
  })
}

/** 把 token 写入 IndexedDB；`null` 表示清除。任何失败都静默（页面功能不受影响）。 */
export async function writeAuthTokenToIdb(token: string | null): Promise<void> {
  if (!hasIndexedDb()) return
  let database: IDBDatabase | null = null
  try {
    database = await openAuthDatabase()
    await new Promise<void>((resolve, reject) => {
      const transaction = database!.transaction(AUTH_DB_STORE, 'readwrite')
      const store = transaction.objectStore(AUTH_DB_STORE)
      const request = token ? store.put(token, AUTH_TOKEN_KEY) : store.delete(AUTH_TOKEN_KEY)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error ?? new Error('indexedDB write failed'))
    })
  } catch {
    // 隐私模式或存储被禁用时 Worker 拿不到 token，页面自身的推送处置仍可用。
  } finally {
    database?.close()
  }
}

/** 从 IndexedDB 读 token（Worker 处置路径也会用同样的键）。 */
export async function readAuthTokenFromIdb(): Promise<string | null> {
  if (!hasIndexedDb()) return null
  let database: IDBDatabase | null = null
  try {
    database = await openAuthDatabase()
    return await new Promise<string | null>((resolve, reject) => {
      const transaction = database!.transaction(AUTH_DB_STORE, 'readonly')
      const request = transaction.objectStore(AUTH_DB_STORE).get(AUTH_TOKEN_KEY)
      request.onsuccess = () =>
        resolve(typeof request.result === 'string' && request.result ? request.result : null)
      request.onerror = () => reject(request.error ?? new Error('indexedDB read failed'))
    })
  } catch {
    return null
  } finally {
    database?.close()
  }
}
