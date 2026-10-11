import { useCallback, useState } from "react"

import { AskPanel } from "@/components/AskPanel"
import { IngestPanel } from "@/components/IngestPanel"
import { NoteDialog } from "@/components/NoteDialog"
import { NotesPanel } from "@/components/NotesPanel"
import { linkTarget, type NoteRef, type NoteTarget } from "@/lib/api"
import { cn } from "@/lib/utils"

const TABS = [
  { id: "ingest", label: "录入" },
  { id: "ask", label: "提问" },
  { id: "notes", label: "笔记" },
] as const

type TabId = (typeof TABS)[number]["id"]

function App() {
  const [tab, setTab] = useState<TabId>("ingest")
  const [openNote, setOpenNote] = useState<NoteTarget | null>(null)
  const openRef = useCallback(
    (note: NoteRef) => setOpenNote({ title: note.title, paths: [note.path] }),
    [],
  )
  const openLink = useCallback((stem: string) => setOpenNote(linkTarget(stem)), [])
  const closeNote = useCallback(() => setOpenNote(null), [])

  return (
    <div className="mx-auto flex min-h-svh max-w-2xl flex-col gap-6 px-4 py-8">
      <header className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">note-agent</h1>
        <nav role="tablist" className="flex gap-1 rounded-lg bg-muted p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                "rounded-md px-3 py-1 text-sm font-medium text-muted-foreground transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                tab === t.id && "bg-background text-foreground shadow-sm",
              )}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      {/* 面板都保持挂载，切换标签不丢输入和结果 */}
      <main>
        <div hidden={tab !== "ingest"}>
          <IngestPanel active={tab === "ingest" && openNote === null} onOpenNote={openRef} />
        </div>
        <div hidden={tab !== "ask"}>
          <AskPanel onOpenNote={openRef} />
        </div>
        <div hidden={tab !== "notes"}>
          <NotesPanel active={tab === "notes"} onOpenNote={openRef} />
        </div>
      </main>

      {openNote && <NoteDialog target={openNote} onOpenLink={openLink} onClose={closeNote} />}
    </div>
  )
}

export default App
