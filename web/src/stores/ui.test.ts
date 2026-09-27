import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

function stubColorScheme(prefersLight: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('light') ? prefersLight : !prefersLight,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

async function loadStore() {
  vi.resetModules()
  return import('@/stores/ui')
}

describe('theme preference store', () => {
  beforeEach(() => {
    localStorage.clear()
    document.head.innerHTML = '<meta name="theme-color" content="#000000" />'
    document.documentElement.className = ''
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.head.replaceChildren()
  })

  it('follows the system color scheme when nothing is stored', async () => {
    stubColorScheme(true)
    const { useUiStore } = await loadStore()

    expect(useUiStore.getState().theme).toBe('system')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#f6f7f9')
  })

  it('resolves the system preference to the graphite dark palette', async () => {
    stubColorScheme(false)
    const { useUiStore } = await loadStore()

    expect(useUiStore.getState().theme).toBe('system')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#0e1014')
  })

  it('keeps an explicit stored choice over the system preference', async () => {
    stubColorScheme(true)
    localStorage.setItem('maa-api-theme', 'dark')
    const { useUiStore } = await loadStore()

    expect(useUiStore.getState().theme).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('persists a new choice and updates the browser chrome color', async () => {
    stubColorScheme(false)
    const { useUiStore, THEME_STORAGE_KEY } = await loadStore()

    useUiStore.getState().setTheme('light')

    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(document.documentElement.style.colorScheme).toBe('light')
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#f6f7f9')
  })
})
