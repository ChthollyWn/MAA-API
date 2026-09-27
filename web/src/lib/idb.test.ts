import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  AUTH_DB_NAME,
  AUTH_DB_STORE,
  AUTH_TOKEN_KEY,
  readAuthTokenFromIdb,
  writeAuthTokenToIdb,
} from './idb'

/**
 * IndexedDB 是 Service Worker 唯一能读到的 token 位置（Worker 读不到 localStorage），
 * 所以这一层的读写必须真的落到 `maa-api` / `auth` / `token` 这对键库上：
 * 页面写、Worker 读，键名一旦漂移，通知上的「准许 / 拒绝」就会静默 401。
 */

function deleteDatabase(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') {
      resolve()
      return
    }
    const request = indexedDB.deleteDatabase(AUTH_DB_NAME)
    request.onsuccess = () => resolve()
    request.onerror = () => resolve()
    request.onblocked = () => resolve()
  })
}

describe('auth token IndexedDB mirror', () => {
  beforeEach(async () => {
    await deleteDatabase()
  })

  afterEach(async () => {
    // 先还原全局再清理：`stays silent` 用例会把 indexedDB 置空，
    // 反过来的话 afterEach 自己就会撞在 undefined 上。
    vi.unstubAllGlobals()
    await deleteDatabase()
  })

  it('writes and reads the token under the keys the Service Worker uses', async () => {
    await writeAuthTokenToIdb('token-abc')
    expect(await readAuthTokenFromIdb()).toBe('token-abc')

    // 键名与 sw.ts 里的 DB_NAME/DB_STORE/TOKEN_KEY 常量必须一致
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(AUTH_DB_NAME)
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    expect(database.objectStoreNames.contains(AUTH_DB_STORE)).toBe(true)
    const stored = await new Promise<unknown>((resolve, reject) => {
      const transaction = database.transaction(AUTH_DB_STORE, 'readonly')
      const get = transaction.objectStore(AUTH_DB_STORE).get(AUTH_TOKEN_KEY)
      get.onsuccess = () => resolve(get.result)
      get.onerror = () => reject(get.error)
    })
    database.close()
    expect(stored).toBe('token-abc')
  })

  it('clears the token when written null (logout must not leave a usable copy)', async () => {
    await writeAuthTokenToIdb('token-abc')
    await writeAuthTokenToIdb(null)
    expect(await readAuthTokenFromIdb()).toBeNull()
  })

  it('returns null when nothing was ever written', async () => {
    expect(await readAuthTokenFromIdb()).toBeNull()
  })

  it('ignores empty strings instead of storing them', async () => {
    await writeAuthTokenToIdb('token-abc')
    await writeAuthTokenToIdb('')
    expect(await readAuthTokenFromIdb()).toBeNull()
  })

  it('stays silent when IndexedDB is unavailable', async () => {
    vi.stubGlobal('indexedDB', undefined)
    await expect(writeAuthTokenToIdb('token-abc')).resolves.toBeUndefined()
    await expect(readAuthTokenFromIdb()).resolves.toBeNull()
  })
})
