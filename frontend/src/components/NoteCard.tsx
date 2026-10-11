import type { ReactNode } from "react"

import type { NoteRef } from "@/lib/api"

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
        <span className="mt-1.5 block text-xs text-muted-foreground">{placement(note)}</span>
      </span>
    </button>
  )
}

function placement(note: NoteRef): string {
  if (note.status !== "inbox") return note.category ?? ""
  if (note.category === null) return `待整理 · ${note.suggested_domain ?? "未知领域"}`
  return `待整理 · ${note.category}（分类把握不足）`
}
