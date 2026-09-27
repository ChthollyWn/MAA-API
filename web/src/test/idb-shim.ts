/**
 * jsdom 没有实现 IndexedDB 的全局。
 *
 * 这里补一个**最小内存实现**，只覆盖本项目用到的 API 面：
 * `open` / `createObjectStore` / `transaction` / `objectStore` / `put` / `get` /
 * `delete` / `close` / `objectStoreNames.contains` / `onupgradeneeded`。
 *
 * 不引入 `fake-indexeddb` 的理由与 M14 不引入 `pywebpush` 相同：本机网络不稳定，
 * 而需要的语义只有这一点点。若将来需要游标、索引或事务隔离，应换成真正的实现
 * 而不是继续加宽这里。
 */

type Store = Map<string, unknown>

class ShimRequest<T> {
  result!: T
  error: Error | null = null
  onsuccess: ((event?: unknown) => void) | null = null
  onerror: ((event?: unknown) => void) | null = null

  settle(result: T, error: Error | null = null): void {
    this.result = result
    this.error = error
    // 用宏任务而不是微任务：调用方都是 `open()` 返回之后才挂 onsuccess/onerror，
    // 微任务可能赶在挂载之前触发，回调就永远不会被调用（表现为测试超时）。
    setTimeout(() => {
      if (error) this.onerror?.(error)
      else this.onsuccess?.({ target: this })
    }, 0)
  }
}

class ShimObjectStore {
  constructor(private readonly data: Store) {}

  put(value: unknown, key: string): ShimRequest<string> {
    const request = new ShimRequest<string>()
    this.data.set(key, value)
    request.settle(key)
    return request
  }

  delete(key: string): ShimRequest<undefined> {
    const request = new ShimRequest<undefined>()
    this.data.delete(key)
    request.settle(undefined)
    return request
  }

  get(key: string): ShimRequest<unknown> {
    const request = new ShimRequest<unknown>()
    request.settle(this.data.get(key))
    return request
  }
}

class ShimTransaction {
  constructor(private readonly store: ShimObjectStore) {}

  objectStore(): ShimObjectStore {
    return this.store
  }
}

class ShimDatabase {
  private readonly stores = new Map<string, Store>()

  readonly objectStoreNames = {
    contains: (name: string) => this.stores.has(name),
  }

  createObjectStore(name: string): void {
    if (!this.stores.has(name)) this.stores.set(name, new Map())
  }

  transaction(name: string): ShimTransaction {
    const store = this.stores.get(name)
    if (!store) throw new Error(`object store 不存在: ${name}`)
    return new ShimTransaction(new ShimObjectStore(store))
  }

  close(): void {}
}

class ShimOpenRequest extends ShimRequest<ShimDatabase> {
  onupgradeneeded: ((event?: unknown) => void) | null = null
}

const databases = new Map<string, ShimDatabase>()

const shim = {
  open(name: string): ShimOpenRequest {
    const request = new ShimOpenRequest()
    let database = databases.get(name)
    const isNew = database === undefined
    if (!database) {
      database = new ShimDatabase()
      databases.set(name, database)
    }
    setTimeout(() => {
      // result 必须先赋值：升级回调里调用方会直接读 `request.result.createObjectStore(...)`，
      // 顺序错了就会在读取 `objectStoreNames` 处抛 TypeError（表现为测试超时）。
      request.result = database as ShimDatabase
      if (isNew) request.onupgradeneeded?.({ target: request })
      request.settle(database as ShimDatabase)
    }, 0)
    return request
  },
  deleteDatabase(name: string): ShimRequest<undefined> {
    const request = new ShimRequest<undefined>()
    databases.delete(name)
    request.settle(undefined)
    return request
  },
}

// 无条件覆盖：jsdom 会挂一个「存在但没有 deleteDatabase」的 stub，
// 只判断 undefined 的话 shim 永远不会装上（测试会以超时而不是明确报错收场）。
Object.defineProperty(globalThis, 'indexedDB', {
  value: shim,
  configurable: true,
  writable: true,
})
