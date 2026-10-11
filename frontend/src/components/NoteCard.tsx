import type { ReactNode } from "react"
import { LockIcon } from "lucide-react"

import { categoryLabel, type NoteRef } from "@/lib/api"

export function NoteCard({
  note,
  badge,
  onOpen,
}: {
  note: NoteRef
  badge?: ReactNode
  onOpen: (note: NoteRef) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(note)}
      className="flex w-full items-start gap-3 rounded-lg border bg-card p-3 text-left transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      {badge}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{note.title}</span>
        <span className="mt-0.5 block text-sm text-muted-foreground">{note.summary}</span>
        <span className="mt-1.5 flex items-center gap-1 text-xs text-muted-foreground">
          {placement(note)}
          {note.category_locked && (
            <LockIcon className="size-3" aria-label="你手动指定的分类，不会被重新归类改动" />
          )}
        </span>
      </span>
    </button>
  )
}

function placement(note: NoteRef): string {
  if (note.status !== "inbox") return note.category ? categoryLabel(note.category) : ""
  if (note.category === null) return "待整理 · 还没有分类"
  return `待整理 · 可能属于 ${categoryLabel(note.category)}`
}
