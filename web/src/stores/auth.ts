import { create } from 'zustand'

export const AUTH_TOKEN_STORAGE_KEY = 'maa-api-token'
export const UNAUTHORIZED_EVENT = 'maa:unauthorized'

interface AuthState {
  token: string | null
  setToken: (token: string) => void
  clearToken: () => void
  onUnauthorized: () => void
}

function readStoredToken(): string | null {
  if (typeof window === 'undefined') return null

  try {
    return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
  } catch {
    return null
  }
}

function writeStoredToken(token: string | null): void {
  if (typeof window === 'undefined') return

  try {
    if (token) window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token)
    else window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
  } catch {
    // The in-memory store remains usable when browser storage is unavailable.
  }
}

export const useAuth = create<AuthState>((set, get) => ({
  token: readStoredToken(),
  setToken: (token) => {
    const normalizedToken = token.trim()
    if (!normalizedToken) {
      get().clearToken()
      return
    }

    writeStoredToken(normalizedToken)
    set({ token: normalizedToken })
  },
  clearToken: () => {
    writeStoredToken(null)
    set({ token: null })
  },
  onUnauthorized: () => {
    get().clearToken()
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
  },
}))
