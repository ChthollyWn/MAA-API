import { create } from 'zustand'

interface ConfirmationUiState {
  /** 全局待确认面板是否打开。 */
  open: boolean
  /** 待确认数量，由外壳里的 ConfirmationDock 同步，标题栏与首页只读它，不各自发请求。 */
  pendingCount: number
  setOpen: (open: boolean) => void
  setPendingCount: (count: number) => void
}

export const useConfirmationUi = create<ConfirmationUiState>((set) => ({
  open: false,
  pendingCount: 0,
  setOpen: (open) => set({ open }),
  setPendingCount: (pendingCount) => set({ pendingCount }),
}))
