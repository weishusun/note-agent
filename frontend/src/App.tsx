import { useCallback, useState } from "react"

import { AskPanel } from "@/components/AskPanel"
import { DuplicateDialog } from "@/components/DuplicateDialog"
import { IngestPanel } from "@/components/IngestPanel"
import { NoteDialog } from "@/components/NoteDialog"
import { NotesPanel } from "@/components/NotesPanel"
import { SettingsPanel } from "@/components/SettingsPanel"
import { linkTarget, type NoteRef, type NoteTarget } from "@/lib/api"
import { cn } from "@/lib/utils"

const TABS = [
  { id: "ingest", label: "录入" },
  { id: "ask", label: "提问" },
  { id: "notes", label: "笔记" },
  { id: "settings", label: "设置" },
] as const

type TabId = (typeof TABS)[number]["id"]

function App() {
  const [tab, setTab] = useState<TabId>("ingest")
  const [openNote, setOpenNote] = useState<NoteTarget | null>(null)
  // 笔记在弹窗里被改动（改分类、编辑、删除、处理疑似重复）的次数：各页据此重新加载
  const [notesVersion, setNotesVersion] = useState(0)
  const noteChanged = useCallback(() => setNotesVersion((v) => v + 1), [])
  const openRef = useCallback(
    (note: NoteRef) => setOpenNote({ title: note.title, paths: [note.path] }),
    [],
  )
  const openLink = useCallback((stem: string) => setOpenNote(linkTarget(stem)), [])
  const closeNote = useCallback(() => setOpenNote(null), [])
  // 等着并排对比的疑似重复笔记的 id，第一条是正在看的
  const [compare, setCompare] = useState<string[]>([])
  const openCompare = useCallback((ids: string[]) => {
    setOpenNote(null)
    setCompare(ids)
  }, [])
  const compareResolved = useCallback(() => {
    setNotesVersion((v) => v + 1)
    setCompare((queue) => queue.slice(1))
  }, [])
  const closeCompare = useCallback(() => setCompare([]), [])

  return (
    <div
      className={cn(
        "mx-auto flex min-h-svh flex-col gap-6 px-4 py-8",
        // 笔记页左边有目录树，需要更宽
        tab === "notes" ? "max-w-5xl" : "max-w-2xl",
      )}
    >
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
          <IngestPanel
            active={tab === "ingest" && openNote === null && compare.length === 0}
            version={notesVersion}
            onOpenNote={openRef}
            onCompare={openCompare}
          />
        </div>
        <div hidden={tab !== "ask"}>
          <AskPanel onOpenNote={openRef} />
        </div>
        <div hidden={tab !== "notes"}>
          <NotesPanel
            active={tab === "notes"}
            version={notesVersion}
            onOpenNote={openRef}
            onCompare={openCompare}
          />
        </div>
        <div hidden={tab !== "settings"}>
          <SettingsPanel active={tab === "settings"} />
        </div>
      </main>

      {openNote && (
        <NoteDialog
          target={openNote}
          onOpenLink={openLink}
          onChanged={noteChanged}
          onCompare={openCompare}
          onClose={closeNote}
        />
      )}
      {compare.length > 0 && (
        <DuplicateDialog
          noteId={compare[0]}
          remaining={compare.length - 1}
          onResolved={compareResolved}
          onClose={closeCompare}
        />
      )}
    </div>
  )
}

export default App
