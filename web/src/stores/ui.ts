import { create } from 'zustand'

export type ThemePreference = 'dark' | 'light' | 'system'
export const THEME_STORAGE_KEY = 'maa-api-theme'
const THEME_COLOR = { dark: '#101411', light: '#f4f6f3' } as const

function readTheme(): ThemePreference {
  if (typeof window === 'undefined') return 'dark'
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY)
    if (value === 'dark' || value === 'light' || value === 'system') return value
  } catch {
    // Keep the dark default when browser storage is unavailable.
  }
  return 'dark'
}

function resolvedTheme(theme: ThemePreference): 'dark' | 'light' {
  if (theme !== 'system') return theme
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: light)').matches
    ? 'light'
    : 'dark'
}

export function applyTheme(theme: ThemePreference): void {
  if (typeof document === 'undefined') return
  const colorTheme = resolvedTheme(theme)
  document.documentElement.classList.toggle('dark', colorTheme === 'dark')
  document.documentElement.style.colorScheme = colorTheme
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute('content', THEME_COLOR[colorTheme])
}

interface UiState {
  theme: ThemePreference
  setTheme: (theme: ThemePreference) => void
}

export const useUiStore = create<UiState>((set) => ({
  theme: readTheme(),
  setTheme: (theme) => {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme)
    } catch {
      // Theme selection still applies for the current session.
    }
    applyTheme(theme)
    set({ theme })
  },
}))

if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
  const systemTheme = window.matchMedia('(prefers-color-scheme: light)')
  const applySystemTheme = () => {
    if (useUiStore.getState().theme === 'system') applyTheme('system')
  }
  systemTheme.addEventListener?.('change', applySystemTheme)
  applyTheme(useUiStore.getState().theme)
}
