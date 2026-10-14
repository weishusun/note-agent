import { useCallback, useEffect, useRef } from "react"

import { NoteReader } from "@/components/NoteReader"
import type { NoteTarget } from "@/lib/api"

// 在笔记页以外的地方（提问的引用、录入结果、图谱、导图）点开一条笔记：从右边滑出来读，不离开当前页
export function NoteDrawer({
  target,
  onOpenLink,
  onChanged,
  onCompare,
  onClose,
}: {
  target: NoteTarget
  onOpenLink: (stem: string) => void
  onChanged: () => void
  onCompare: (ids: string[]) => void
  onClose: () => void
}) {
  // 编辑到一半时，Esc 和点空白处都不关，免得丢了改到一半的内容
  const editing = useRef(false)
  const onEditingChange = useCallback((value: boolean) => {
    editing.current = value
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !editing.current && onClose()
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-foreground/10 backdrop-blur-[1px]"
      onClick={() => !editing.current && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={target.title}
        className="h-full w-full max-w-[680px] overflow-y-auto border-l bg-background shadow-[-24px_0_60px_-30px_rgba(16,32,44,0.35)]"
        onClick={(e) => e.stopPropagation()}
      >
        <NoteReader
          key={target.paths.join("|")}
          target={target}
          onOpenLink={onOpenLink}
          onChanged={onChanged}
          onCompare={onCompare}
          onDeleted={onClose}
          onClose={onClose}
          onEditingChange={onEditingChange}
        />
      </div>
    </div>
  )
}
