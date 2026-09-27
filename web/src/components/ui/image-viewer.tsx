import type { MouseEvent } from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'

export interface ImageViewerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  src: string | null
  alt: string
  caption?: string
}

/** 全屏看图：点空白处、关闭按钮或 Esc 退出。 */
export function ImageViewer({ open, onOpenChange, src, alt, caption }: ImageViewerProps) {
  const closeOnBackdrop = (event: MouseEvent<HTMLElement>) => {
    if (event.target === event.currentTarget) onOpenChange(false)
  }
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[70] bg-black/90 data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          onClick={closeOnBackdrop}
          className="fixed inset-0 z-[70] flex flex-col outline-none data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out"
        >
          <DialogPrimitive.Title className="sr-only">{alt}</DialogPrimitive.Title>
          <div className="flex items-center gap-3 px-3 pt-[max(0.75rem,env(safe-area-inset-top))]" onClick={closeOnBackdrop}>
            <p className="min-w-0 flex-1 truncate text-sm text-white/75">{caption}</p>
            <DialogPrimitive.Close asChild>
              <button type="button" aria-label="关闭" className="grid size-11 place-items-center rounded-full bg-white/10 text-white transition-colors hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white">
                <X aria-hidden="true" className="size-5" />
              </button>
            </DialogPrimitive.Close>
          </div>
          <div className="flex min-h-0 flex-1 items-center justify-center p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]" onClick={closeOnBackdrop}>
            {src ? <img src={src} alt={alt} className="max-h-full max-w-full rounded-lg object-contain" /> : null}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
