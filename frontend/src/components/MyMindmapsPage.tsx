import { useCallback, useEffect, useState, type FormEvent } from "react"
import { NetworkIcon, PlusIcon, Trash2Icon, WandSparklesIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { MindmapEditor } from "@/components/MindmapEditor"
import { Button } from "@/components/ui/button"
import { api, type MindmapSummary, type NoteRef, type NoteTitle, type Tree } from "@/lib/api"
import { buildMindMap } from "@/lib/mindmap"
import { docFromMapTree } from "@/lib/mindmapDoc"

const INPUT =
  "h-9 rounded-lg border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"

// 「我的导图」：使用者自己做的思维导图。先是列表，点开一张进编辑器（SCHEMA §8）
export function MyMindmapsPage({
  tree,
  version,
  onOpenNote,
}: {
  tree: Tree | null
  // 笔记在别处改过：@ 的候选要跟着刷新
  version: number
  onOpenNote: (note: NoteRef) => void
}) {
  const [maps, setMaps] = useState<MindmapSummary[] | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [titles, setTitles] = useState<NoteTitle[]>([])
  const [form, setForm] = useState<"new" | "draft" | null>(null)
  const [name, setName] = useState("")
  const [category, setCategory] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(() => {
    api.mindmaps().then(setMaps, (e: Error) => setError(e.message))
  }, [])
  useEffect(reload, [reload])
  useEffect(() => {
    api.titles().then(setTitles, () => undefined)
  }, [version])

  if (open) {
    return (
      <MindmapEditor
        key={open}
        mapId={open}
        titles={titles}
        onOpenNote={onOpenNote}
        onBack={() => {
          setOpen(null)
          reload()
        }}
      />
    )
  }

  async function create(e: FormEvent) {
    e.preventDefault()
    const title = name.trim()
    if (!title) return
    setBusy(true)
    setError(null)
    try {
      const view = await api.createMindmap({ title })
      setOpen(view.map.id)
    } catch (err) {
      setError((err as Error).message)
    }
    setBusy(false)
  }

  async function draft(e: FormEvent) {
    e.preventDefault()
    if (!tree || !category) return
    setBusy(true)
    setError(null)
    try {
      // 和「目录导图」同一棵树：分类 → 小块 → 主题笔记 → 概念笔记，笔记节点带上链接
      const graph = await api.graph(category)
      const label = category.split("/").pop() ?? category
      const doc = docFromMapTree(`${label}（初稿）`, buildMindMap(tree, graph, category))
      const view = await api.createMindmap(doc)
      setOpen(view.map.id)
    } catch (err) {
      setError((err as Error).message)
    }
    setBusy(false)
  }

  async function remove(map: MindmapSummary) {
    if (!window.confirm(`删除导图「${map.title}」？删除后不能在应用里撤销。`)) return
    try {
      await api.deleteMindmap(map.id)
      reload()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => setForm(form === "new" ? null : "new")} variant={form === "new" ? "secondary" : "default"}>
          <PlusIcon />
          新建导图
        </Button>
        <Button variant={form === "draft" ? "secondary" : "outline"} onClick={() => setForm(form === "draft" ? null : "draft")}>
          <WandSparklesIcon />
          从分类生成初稿
        </Button>
      </div>

      {form === "new" && (
        <form onSubmit={create} className="flex flex-wrap items-center gap-2">
          <input
            autoFocus
            className={`${INPUT} w-72`}
            placeholder="导图名字，也是中心主题"
            maxLength={80}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" disabled={busy || !name.trim()}>
            {busy ? "创建中…" : "创建"}
          </Button>
        </form>
      )}
      {form === "draft" && (
        <form onSubmit={draft} className="flex flex-wrap items-center gap-2">
          <select
            aria-label="选一个分类"
            className={`${INPUT} w-72`}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="">选一个分类…</option>
            {tree?.categories.map((parent) => (
              <optgroup key={parent.name} label={parent.name}>
                <option value={parent.name}>{parent.name}（整个大块）</option>
                {parent.children.map((child) => (
                  <option key={child.path} value={child.path}>
                    {child.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          <Button type="submit" disabled={busy || !category}>
            {busy ? "生成中…" : "生成"}
          </Button>
          <span className="text-xs text-muted-foreground">把这个分类下的小块和笔记排成一张导图，笔记节点带链接，之后随便改。</span>
        </form>
      )}
      {error && <Alert tone="error">{error}</Alert>}

      {maps === null ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : maps.length === 0 ? (
        <p className="text-sm text-muted-foreground">还没有导图。新建一张，或者从一个分类生成初稿再改。</p>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2">
          {maps.map((map) => (
            <li key={map.id} className="group flex items-center gap-3 rounded-xl border bg-card p-3 hover:border-primary/40">
              <button type="button" onClick={() => setOpen(map.id)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground">
                  <NetworkIcon className="size-4" />
                </span>
                <span className="min-w-0">
                  <span className="block truncate font-medium">{map.title}</span>
                  <span className="text-xs text-muted-foreground">
                    {map.node_count} 个节点 · {map.updated.slice(0, 10)} 更新
                  </span>
                </span>
              </button>
              <Button
                variant="ghost"
                size="icon-sm"
                title="删除"
                aria-label={`删除导图「${map.title}」`}
                onClick={() => void remove(map)}
                className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
              >
                <Trash2Icon />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
