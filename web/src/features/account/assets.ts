/**
 * 卡片上要用的图片地址与展示换算（M43 规格 §4.4）。
 *
 * 全部集中在这一个文件里，换源（官方 CDN 换自建代理、或以后加模组图标）只动这里。
 *
 * **模组图标本轮不做**（规格 §3.2）：官方 CDN 试过六条候选路径全部 404，yituliu 的自有
 * CDN 无可再分发许可。模组用文字标签表达，所以这里没有 `moduleIconUrl`。
 */

/** 官方资源 CDN：头像与技能图标都在这个域下（实测 200，见规格 §2.2）。 */
const ASSET_BASE = 'https://web.hycdn.cn/arknights/game/assets'

/** 干员头像（小方图，65 KB 量级）。`charId` 如 `char_002_amiya`。 */
export function avatarUrl(charId: string): string {
  return `${ASSET_BASE}/char/avatar/${encodeURIComponent(charId)}.png`
}

/** 技能图标（17 KB 量级）。`skillId` 如 `skchr_amiya_3`，来自森空岛同步的 `skill_ids`。 */
export function skillIconUrl(skillId: string): string {
  return `${ASSET_BASE}/char_skill/${encodeURIComponent(skillId)}.png`
}

/**
 * 模组类型字母 → 游戏里的显示字形（规格 §2.4，照一图流的口径）：
 * `X` / `Y` 原样，`D` 是 Δ、`A` 是 α。未知类型原样返回，不猜。
 */
export function moduleTypeGlyph(type: string): string {
  if (type === 'D') return 'Δ'
  if (type === 'A') return 'α'
  return type
}

/** 稀有度 → 卡片主色/副色（规格 §2.4，与游戏截图口径一致）。 */
export const RARITY_COLORS: Record<number, { primary: string; secondary: string }> = {
  6: { primary: '#FF6B35', secondary: '#FF9800' },
  5: { primary: '#E5C100', secondary: '#FFD700' },
  4: { primary: '#A855F7', secondary: '#C084FC' },
  3: { primary: '#3B82F6', secondary: '#60A5FA' },
  2: { primary: '#6EE7B7', secondary: '#A7F3D0' },
  1: { primary: '#9CA3AF', secondary: '#D1D5DB' },
}

/** 未拥有/稀有度读不出来时的灰色。`null` 与 `0` 都走这里。 */
export const UNOWNED_COLORS = { primary: '#4B5563', secondary: '#6B7280' }
