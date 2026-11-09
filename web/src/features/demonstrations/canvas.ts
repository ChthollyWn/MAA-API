/** 示教画布的坐标映射与手势判定（M89 规格 §4.6）。 */

export interface CanvasRect {
  left: number
  top: number
  width: number
  height: number
}

export interface ScreenSize {
  width: number
  height: number
}

export interface DevicePoint {
  x: number
  y: number
}

export type PointerKind = 'tap' | 'swipe' | 'long_press'

/** 位移超过这个值算滑动，不再等长按。 */
export const MOVE_THRESHOLD_PX = 8
/** 按住这么久未移动算长按。 */
export const LONG_PRESS_MS = 600

const SWIPE_DURATION_MIN = 100
const SWIPE_DURATION_MAX = 2_000
const LONG_PRESS_DURATION_MIN = 600
const LONG_PRESS_DURATION_MAX = 3_000

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum)
}

/**
 * 把画布上的指针坐标换算成设备像素。
 *
 * 只按 `rect` 的显示尺寸映射，不读 `naturalWidth`：`size=mobile` 的截图会被缩到
 * 长边 1280，但页面里显示多大由布局决定。结果夹在设备分辨率内，避免边界点击
 * 因四舍五入越界。
 */
export function mapToDevice(
  clientX: number,
  clientY: number,
  rect: CanvasRect,
  screen: ScreenSize,
): DevicePoint {
  if (rect.width <= 0 || rect.height <= 0 || screen.width <= 0 || screen.height <= 0) {
    return { x: 0, y: 0 }
  }
  const x = Math.round(((clientX - rect.left) / rect.width) * screen.width)
  const y = Math.round(((clientY - rect.top) / rect.height) * screen.height)
  return {
    x: clamp(x, 0, screen.width - 1),
    y: clamp(y, 0, screen.height - 1),
  }
}

/** 松手时按最终位移与按住时长判定手势。 */
export function classifyPointer(dx: number, dy: number, heldMs: number): PointerKind {
  if (Math.hypot(dx, dy) > MOVE_THRESHOLD_PX) return 'swipe'
  return heldMs >= LONG_PRESS_MS ? 'long_press' : 'tap'
}

/** 滑动的实际时长夹到 100–2000 毫秒。 */
export function swipeDuration(heldMs: number): number {
  return clamp(Math.round(heldMs), SWIPE_DURATION_MIN, SWIPE_DURATION_MAX)
}

/** 长按的实际时长夹到 600–3000 毫秒。 */
export function longPressDuration(heldMs: number): number {
  return clamp(Math.round(heldMs), LONG_PRESS_DURATION_MIN, LONG_PRESS_DURATION_MAX)
}
