import { describe, expect, it } from 'vitest'
import { classifyPointer, longPressDuration, mapToDevice, swipeDuration } from './canvas'

describe('M89 示教画布', () => {
  it('maps_tap_to_device_pixels', () => {
    expect(mapToDevice(640, 360, { left: 0, top: 0, width: 1280, height: 720 }, { width: 1280, height: 720 }))
      .toEqual({ x: 640, y: 360 })
    expect(mapToDevice(999, 999, { left: 0, top: 0, width: 640, height: 360 }, { width: 1280, height: 720 }))
      .toEqual({ x: 1279, y: 719 })
  })

  it('classifies_swipe_and_long_press', () => {
    expect(classifyPointer(2, 2, 200)).toBe('tap')
    expect(classifyPointer(100, 0, 300)).toBe('swipe')
    expect(classifyPointer(3, 1, 700)).toBe('long_press')
    expect(swipeDuration(50)).toBe(100)
    expect(swipeDuration(9000)).toBe(2000)
    expect(longPressDuration(600)).toBe(600)
    expect(longPressDuration(9000)).toBe(3000)
  })
})
