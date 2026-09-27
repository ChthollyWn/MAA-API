/**
 * 全局测试环境补丁。
 *
 * jsdom 没有 IndexedDB，而 `src/lib/idb.ts` 与 Service Worker 的确认处置路径都依赖
 * 它。补一个最小内存实现（见 `idb-shim.ts` 的说明），让这些测试验证真实键名与
 * 读写语义，而不是把 `indexedDB` 整个 mock 掉。
 */
import { configure } from '@testing-library/react'
import './idb-shim'

// 页面都是懒加载分块；全量并行时冷加载一个页面会超过 findBy / waitFor 默认的 1 秒。
configure({ asyncUtilTimeout: 5_000 })
