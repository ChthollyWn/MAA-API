/**
 * 全局测试环境补丁。
 *
 * jsdom 没有 IndexedDB，而 `src/lib/idb.ts` 与 Service Worker 的确认处置路径都依赖
 * 它。补一个最小内存实现（见 `idb-shim.ts` 的说明），让这些测试验证真实键名与
 * 读写语义，而不是把 `indexedDB` 整个 mock 掉。
 */
import './idb-shim'
