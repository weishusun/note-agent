import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import {
  FileTextIcon,
  GlobeIcon,
  ImageIcon,
  MessageCircleIcon,
  SearchIcon,
  TextIcon,
  XIcon,
} from "lucide-react"

import { api, categoryLabel, type IngestInput, type NoteRef } from "@/lib/api"
import { cn } from "@/lib/utils"

const SEARCH_DEBOUNCE_MS = 150
const SEARCH_LIMIT = 6
const URL_LINE = /^https?:\/\/\S+$/i

type Action =
  | { kind: "ask"; question: string }
  | { kind: "ingest"; input: IngestInput; label: string; icon: ReactNode }
  | { kind: "open"; note: NoteRef }

// Ctrl K 呼出的输入面板：同一个框里提问、搜笔记、录入（截图、网址、一段文字）
export function CommandPalette({
  onAsk,
  onOpenNote,
  onIngested,
  onClose,
}: {
  onAsk: (question: string) => void
  onOpenNote: (note: NoteRef) => void
  // 已经交给录入队列：label 是给提示用的名字
  onIngested: (label: string) => void
  onClose: () => void
}) {
  const [query, setQuery] = useState("")
  const [image, setImage] = useState<string | null>(null)
  const [results, setResults] = useState<NoteRef[]>([])
  const [active, setActive] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  const q = query.trim()

  useEffect(() => {
    if (q.length < 1 || URL_LINE.test(q)) {
      setResults([])
      return
    }
    let cancelled = false
    const timer = setTimeout(() => {
      api.search(q).then(
        (found) => !cancelled && setResults(found.slice(0, SEARCH_LIMIT)),
        () => !cancelled && setResults([]),
      )
    }, SEARCH_DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [q])

  // 面板开着时粘贴截图：直接当成要录入的截图
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const file = Array.from(e.clipboardData?.files ?? []).find((f) => f.type.startsWith("image/"))
      if (!file) return
      e.preventDefault()
      const reader = new FileReader()
      reader.onload = () => setImage(reader.result as string)
      reader.readAsDataURL(file)
    }
    window.addEventListener("paste", onPaste)
    return () => window.removeEventListener("paste", onPaste)
  }, [])

  const actions = useMemo<Action[]>(() => {
    const list: Action[] = []
    if (image) {
      list.push({
        kind: "ingest",
        input: { kind: "screenshot", image_b64: image },
        label: "把这张截图加入录入队列",
        icon: <ImageIcon />,
      })
    }
    const lines = q.split("\n").map((l) => l.trim()).filter(Boolean)
    if (lines.length === 1 && URL_LINE.test(lines[0])) {
      list.push({
        kind: "ingest",
        input: { kind: "url", url: lines[0] },
        label: "抓取这个网页，加入录入队列",
        icon: <GlobeIcon />,
      })
    } else if (q) {
      list.push({ kind: "ask", question: q })
    }
    list.push(...results.map((note): Action => ({ kind: "open", note })))
    if (q && !(lines.length === 1 && URL_LINE.test(lines[0]))) {
      list.push({
        kind: "ingest",
        input: { kind: "text", text: q },
        label: "把这段文字录入知识库",
        icon: <TextIcon />,
      })
    }
    return list
  }, [image, q, results])

  useEffect(() => setActive(0), [q, image])

  async function run(action: Action | undefined) {
    if (!action || busy) return
    if (action.kind === "ask") {
      onAsk(action.question)
      onClose()
    } else if (action.kind === "open") {
      onOpenNote(action.note)
      onClose()
    } else {
      setBusy(true)
      setError(null)
      try {
        await api.ingest(action.input)
        onIngested(action.input.kind === "screenshot" ? "截图" : action.input.kind === "url" ? "网页" : "文字")
        onClose()
      } catch (e) {
        setError((e as Error).message)
      } finally {
        setBusy(false)
      }
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault()
      onClose()
    } else if (e.key === "ArrowDown") {
      e.preventDefault()
      setActive((i) => Math.min(actions.length - 1, i + 1))
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setActive((i) => Math.max(0, i - 1))
    } else if (e.key === "Enter" && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void run(actions[active])
    }
  }

  const askIndex = actions.findIndex((a) => a.kind === "ask" || (a.kind === "ingest" && a.input.kind !== "text"))
  const firstNote = actions.findIndex((a) => a.kind === "open")
  const textIngest = actions.findIndex((a) => a.kind === "ingest" && a.input.kind === "text")

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-foreground/15 px-4 pt-[12vh] backdrop-blur-[1px]"
      onMouseDown={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="搜索、提问或录入"
        className="w-full max-w-[620px] overflow-hidden rounded-2xl border bg-popover shadow-[0_30px_80px_-20px_rgba(16,32,44,0.45)]"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b px-5 py-4">
          <SearchIcon className="size-[18px] flex-none text-muted-foreground" />
          <input
            ref={input}
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            aria-label="搜索、提问或录入"
            placeholder="搜笔记、问问题，或者粘贴截图、网址"
            className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground"
          />
          {busy && <span className="text-xs text-muted-foreground">提交中…</span>}
        </div>

        {image && (
          <div className="flex items-center gap-3 border-b bg-pane px-5 py-3">
            <img src={image} alt="粘贴进来的截图" className="h-14 rounded-md border object-contain" />
            <span className="flex-1 text-sm text-muted-foreground">已粘贴一张截图，回车加入录入队列。</span>
            <button
              type="button"
              aria-label="移除截图"
              className="rounded p-1 text-muted-foreground hover:text-foreground"
              onClick={() => {
                setImage(null)
                input.current?.focus()
              }}
            >
              <XIcon className="size-4" />
            </button>
          </div>
        )}

        <ul role="listbox" aria-label="可以做的事" className="max-h-[50vh] overflow-y-auto py-1.5">
          {actions.length === 0 && (
            <li className="px-5 py-6 text-center text-sm text-muted-foreground">
              输入关键词搜笔记，输入一句话提问，或者按 Ctrl+V 粘贴截图。
            </li>
          )}
          {actions.map((action, i) => (
            <li key={actionKey(action, i)}>
              {i === askIndex && <Group>{action.kind === "ask" ? "提问" : "录入"}</Group>}
              {i === firstNote && <Group>笔记</Group>}
              {i === textIngest && <Group>录入</Group>}
              <button
                type="button"
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={() => void run(action)}
                className={cn(
                  "flex w-full items-center gap-3 px-5 py-2.5 text-left text-sm text-muted-foreground [&_svg]:size-4 [&_svg]:flex-none",
                  i === active && "bg-muted text-foreground",
                )}
              >
                {action.kind === "ask" && (
                  <>
                    <MessageCircleIcon />
                    <span className="min-w-0 flex-1 truncate">
                      问知识库：「<span className="text-foreground">{action.question}</span>」
                    </span>
                  </>
                )}
                {action.kind === "open" && (
                  <>
                    <FileTextIcon />
                    <span className="min-w-0 flex-1 truncate">
                      <span className="text-foreground">{action.note.title}</span>
                      {action.note.category && (
                        <span className="ml-2 text-xs text-muted-foreground">
                          {categoryLabel(action.note.category)}
                        </span>
                      )}
                    </span>
                  </>
                )}
                {action.kind === "ingest" && (
                  <>
                    {action.icon}
                    <span className="flex-1">{action.label}</span>
                  </>
                )}
                {i === active && (
                  <kbd className="rounded bg-background px-1.5 text-[11px] shadow-[inset_0_0_0_1px_var(--input)]">
                    Enter
                  </kbd>
                )}
              </button>
            </li>
          ))}
        </ul>

        {error && <p className="border-t px-5 py-2.5 text-sm text-destructive">{error}</p>}
        <div className="flex gap-4 border-t bg-pane px-5 py-2 text-[11px] text-muted-foreground">
          <span>↑ ↓ 选择</span>
          <span>Enter 执行</span>
          <span>Esc 关闭</span>
        </div>
      </div>
    </div>
  )
}

function Group({ children }: { children: ReactNode }) {
  return <div className="px-5 pt-2.5 pb-1 text-[11px] tracking-[0.14em] text-muted-foreground/80">{children}</div>
}

function actionKey(action: Action, i: number): string {
  if (action.kind === "open") return `open:${action.note.path}`
  if (action.kind === "ask") return "ask"
  return `ingest:${action.input.kind}:${i}`
}
