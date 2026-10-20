import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react"
import { toSvg } from "html-to-image"
import {
  ArrowLeftIcon,
  ImageDownIcon,
  LayoutGridIcon,
  Link2Icon,
  Link2OffIcon,
  MaximizeIcon,
  MinusIcon,
  PaletteIcon,
  PlusIcon,
  Redo2Icon,
  StickyNoteIcon,
  Trash2Icon,
  Undo2Icon,
  XIcon,
} from "lucide-react"

import { Alert } from "@/components/Alert"
import { Button } from "@/components/ui/button"
import { api, type NoteRef, type NoteTitle } from "@/lib/api"
import {
  COLORS,
  addChild,
  addSibling,
  autoLayout,
  bounds,
  canReparent,
  estimateSize,
  moveBy,
  parentOf,
  removeNode,
  reparent,
  setColor,
  setLink,
  setNote,
  setText,
  sortChildrenByY,
  toggleCollapsed,
  visibleIds,
  type MindDoc,
  type NodeColor,
  type Size,
} from "@/lib/mindmapDoc"
import { matchTitles } from "@/lib/mention"
import { usePanZoom } from "@/lib/usePanZoom"
import { cn } from "@/lib/utils"

// 节点颜色：浅色、深色模式各一套，存的是名字（SCHEMA §8）
const COLOR_STYLE: Record<NodeColor, string> = {
  blue: "bg-[#e6eef9] border-[#a9bfe0] text-[#1f3557] dark:bg-[#1c2a3d] dark:border-[#3b5378] dark:text-[#cfe0f7]",
  teal: "bg-[#e2f2ef] border-[#9fcfc5] text-[#1d4740] dark:bg-[#17302c] dark:border-[#2f6158] dark:text-[#c5ece4]",
  green: "bg-[#e7f3e3] border-[#a9d19c] text-[#2a4a22] dark:bg-[#1d2f19] dark:border-[#3d6432] dark:text-[#d2edc8]",
  amber: "bg-[#fbf1d9] border-[#e3c47c] text-[#5a4210] dark:bg-[#33290f] dark:border-[#6d5622] dark:text-[#f3e1b0]",
  orange: "bg-[#fbe8dc] border-[#e9b08c] text-[#5f3216] dark:bg-[#36210f] dark:border-[#734424] dark:text-[#f5d2bb]",
  red: "bg-[#f9e3e3] border-[#e2a3a3] text-[#5c2020] dark:bg-[#361818] dark:border-[#733434] dark:text-[#f2c9c9]",
  pink: "bg-[#f8e4ef] border-[#dfa6c6] text-[#5a1f40] dark:bg-[#34182a] dark:border-[#6e3558] dark:text-[#f0c9df]",
  violet: "bg-[#eee6f8] border-[#bfa9df] text-[#3a2560] dark:bg-[#251c38] dark:border-[#4f3d74] dark:text-[#ddd0f3]",
}
const COLOR_SWATCH: Record<NodeColor, string> = {
  blue: "#7d9fd4",
  teal: "#6fb3a6",
  green: "#86b977",
  amber: "#d9b257",
  orange: "#df9264",
  red: "#d77d7d",
  pink: "#d283ae",
  violet: "#a487d1",
}
const COLOR_NAME: Record<NodeColor, string> = {
  blue: "蓝",
  teal: "青",
  green: "绿",
  amber: "黄",
  orange: "橙",
  red: "红",
  pink: "粉",
  violet: "紫",
}

const HISTORY_MAX = 100
const SAVE_DELAY_MS = 700
// 按下后移动超过这么多像素才算拖动，否则是点击
const DRAG_THRESHOLD = 4

interface History {
  past: MindDoc[]
  present: MindDoc
  future: MindDoc[]
}

interface Editing {
  id: string
  text: string
  // 刚加出来的空节点：取消编辑就把它撤掉
  fresh: boolean
  // 正在 @ 选笔记：高亮第几个候选
  pick: number
}

type SaveState = "saved" | "pending" | "saving" | "error"

export function MindmapEditor({
  mapId,
  titles,
  onBack,
  onOpenNote,
}: {
  mapId: string
  titles: NoteTitle[]
  onBack: () => void
  onOpenNote: (note: NoteRef) => void
}) {
  const [history, setHistory] = useState<History | null>(null)
  const [notes, setNotes] = useState<Record<string, NoteRef>>({})
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [editing, setEditing] = useState<Editing | null>(null)
  const [panel, setPanel] = useState<"note" | "color" | "link" | null>(null)
  const [branch, setBranch] = useState(false)
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  const [saveState, setSaveState] = useState<SaveState>("saved")
  const [saveError, setSaveError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const canvas = useRef<HTMLDivElement>(null)
  const world = useRef<HTMLDivElement>(null)
  const { transform, toWorld, fit, zoomBy, panHandlers } = usePanZoom(canvas)
  // 正在拖的节点
  const drag = useRef<{
    id: string
    pointer: number
    startX: number
    startY: number
    origin: MindDoc
    moved: boolean
  } | null>(null)

  // 同一次编辑只确认一次：按 Enter 确认后输入框消失还会触发一次 blur
  const editingRef = useRef<Editing | null>(null)
  editingRef.current = editing

  // 每个节点实际画出来多大：量到之前按字数估
  const sizes = useRef(new Map<string, Size>())
  const [sizeTick, setSizeTick] = useState(0)
  const observer = useMemo(
    () =>
      new ResizeObserver((entries) => {
        let changed = false
        for (const entry of entries) {
          const id = (entry.target as HTMLElement).dataset.node
          if (!id) continue
          const el = entry.target as HTMLElement
          const size = { w: el.offsetWidth, h: el.offsetHeight }
          const old = sizes.current.get(id)
          if (!old || old.w !== size.w || old.h !== size.h) {
            sizes.current.set(id, size)
            changed = true
          }
        }
        if (changed) setSizeTick((t) => t + 1)
      }),
    [],
  )
  useEffect(() => () => observer.disconnect(), [observer])

  const doc = history?.present ?? null
  const sizeOf = useCallback(
    (id: string): Size => {
      const known = sizes.current.get(id)
      if (known) return known
      const node = history?.present.nodes[id]
      return node ? estimateSize(node, id === history?.present.root) : { w: 80, h: 36 }
    },
    // sizeTick：量到新尺寸后，依赖它的连线、排列要重新算
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [history, sizeTick],
  )

  // ---------- 读取、自动保存 ----------

  useEffect(() => {
    let cancelled = false
    api.mindmap(mapId).then(
      (view) => {
        if (cancelled) return
        const { title, root, nodes } = view.map
        setHistory({ past: [], present: { title, root, nodes }, future: [] })
        setNotes(view.notes)
        setSelected(root)
      },
      (e: Error) => !cancelled && setLoadError(e.message),
    )
    return () => {
      cancelled = true
    }
  }, [mapId])

  const pendingSave = useRef<MindDoc | null>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const flush = useCallback(async () => {
    clearTimeout(saveTimer.current)
    const next = pendingSave.current
    if (!next) return
    pendingSave.current = null
    setSaveState("saving")
    try {
      const view = await api.saveMindmap(mapId, next)
      setNotes(view.notes)
      setSaveState(pendingSave.current ? "pending" : "saved")
      setSaveError(null)
    } catch (e) {
      setSaveState("error")
      setSaveError((e as Error).message)
    }
  }, [mapId])
  useEffect(() => () => void flush(), [flush])

  function schedule(next: MindDoc) {
    pendingSave.current = next
    setSaveState("pending")
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => void flush(), SAVE_DELAY_MS)
  }

  // record=false：拖动过程中的每一步、改标题的每个字不进撤销记录
  function apply(next: MindDoc, record = true) {
    setHistory((h) => {
      if (!h || next === h.present) return h
      return record
        ? { past: [...h.past, h.present].slice(-HISTORY_MAX), present: next, future: [] }
        : { ...h, present: next }
    })
    schedule(next)
  }

  // 在最新的那份导图上改：组件卸载时才保存的备注拿不到最新的 current
  function applyLatest(change: (doc: MindDoc) => MindDoc) {
    setHistory((h) => {
      if (!h) return h
      const next = change(h.present)
      if (next === h.present) return h
      schedule(next)
      return { past: [...h.past, h.present].slice(-HISTORY_MAX), present: next, future: [] }
    })
  }

  function undo() {
    setHistory((h) => {
      if (!h || h.past.length === 0) return h
      const present = h.past[h.past.length - 1]
      schedule(present)
      return { past: h.past.slice(0, -1), present, future: [h.present, ...h.future] }
    })
  }

  function redo() {
    setHistory((h) => {
      if (!h || h.future.length === 0) return h
      const present = h.future[0]
      schedule(present)
      return { past: [...h.past, h.present], present, future: h.future.slice(1) }
    })
  }

  // 第一次量完尺寸后把整张图放进画布
  const fitted = useRef(false)
  useLayoutEffect(() => {
    if (!doc || fitted.current || sizeTick === 0) return
    fitted.current = true
    fit(bounds(doc, sizeOf), 1)
  }, [doc, sizeTick, fit, sizeOf])

  if (loadError) return <Alert tone="error">{loadError}</Alert>
  if (!doc || !history) return <p className="text-sm text-muted-foreground">加载中…</p>

  const current = doc
  const visible = visibleIds(current)
  const sel = selected && current.nodes[selected] ? selected : current.root

  // ---------- 结构操作 ----------

  function startEdit(id: string, fresh = false) {
    setSelected(id)
    setPanel(null)
    setEditing({ id, text: current.nodes[id].text, fresh, pick: 0 })
  }

  function addChildTo(id: string) {
    const { doc: next, id: child } = addChild(current, id, sizeOf)
    apply(next)
    startEditOn(next, child)
  }

  function addSiblingOf(id: string) {
    const { doc: next, id: sibling } = addSibling(current, id, sizeOf)
    apply(next)
    startEditOn(next, sibling)
  }

  function startEditOn(next: MindDoc, id: string) {
    setSelected(id)
    setPanel(null)
    setEditing({ id, text: next.nodes[id].text, fresh: true, pick: 0 })
  }

  function remove(id: string) {
    if (id === current.root) return
    const { doc: next, select } = removeNode(current, id)
    apply(next)
    setSelected(select)
    setPanel(null)
  }

  function commitEdit(text: string, link?: { id: string; title: string } | null) {
    const editing = editingRef.current
    if (!editing) return
    editingRef.current = null
    let next = setText(current, editing.id, text)
    if (link !== undefined) next = setLink(next, editing.id, link)
    setEditing(null)
    // 新节点什么都没写就确认：当成取消
    if (editing.fresh && !next.nodes[editing.id].text) {
      cancelEdit()
      return
    }
    if (editing.fresh) {
      // 加节点那一步已经进了撤销记录：把文字并进去，撤销一次就整个撤掉
      setHistory((h) => (h ? { ...h, present: next } : h))
      schedule(next)
    } else {
      apply(next)
    }
    canvas.current?.focus()
  }

  function cancelEdit() {
    const editing = editingRef.current
    editingRef.current = null
    if (editing?.fresh) {
      const parent = parentOf(current, editing.id)
      undo()
      setSelected(parent)
    }
    setEditing(null)
    canvas.current?.focus()
  }

  // ---------- 键盘 ----------

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (editing || e.target !== canvas.current) return
    const ctrl = e.ctrlKey || e.metaKey
    if (ctrl && (e.key === "z" || e.key === "Z")) {
      e.preventDefault()
      if (e.shiftKey) redo()
      else undo()
      return
    }
    if (ctrl && (e.key === "y" || e.key === "Y")) {
      e.preventDefault()
      redo()
      return
    }
    if (ctrl || e.altKey) return
    const node = current.nodes[sel]
    const parent = parentOf(current, sel)
    const siblings = parent ? current.nodes[parent].children : [sel]
    const at = siblings.indexOf(sel)
    const keys: Record<string, () => void> = {
      Tab: () => addChildTo(sel),
      Enter: () => addSiblingOf(sel),
      Insert: () => addChildTo(sel),
      Delete: () => remove(sel),
      Backspace: () => remove(sel),
      F2: () => startEdit(sel),
      " ": () => startEdit(sel),
      ArrowLeft: () => parent && setSelected(parent),
      ArrowRight: () => {
        if (node.children.length === 0) return
        if (node.collapsed) apply(toggleCollapsed(current, sel))
        setSelected(node.children[0])
      },
      ArrowUp: () => at > 0 && setSelected(siblings[at - 1]),
      ArrowDown: () => at < siblings.length - 1 && setSelected(siblings[at + 1]),
      Escape: () => setPanel(null),
    }
    const action = keys[e.key]
    if (action) {
      e.preventDefault()
      action()
    }
  }

  // ---------- 拖动节点 ----------

  function onNodePointerDown(e: PointerEvent<HTMLDivElement>, id: string) {
    if (e.button !== 0 || editing?.id === id) return
    e.stopPropagation()
    canvas.current?.focus()
    setSelected(id)
    if (editing) commitEdit(editing.text)
    const p = toWorld(e.clientX, e.clientY)
    drag.current = { id, pointer: e.pointerId, startX: p.x, startY: p.y, origin: current, moved: false }
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      // 指针已经不在了：没抓住也只是拖出节点后跟不上
    }
  }

  function nodeAt(clientX: number, clientY: number, skip: Set<string>): string | null {
    const p = toWorld(clientX, clientY)
    for (const id of [...visible].reverse()) {
      if (skip.has(id)) continue
      const n = current.nodes[id]
      const s = sizeOf(id)
      if (p.x >= n.x && p.x <= n.x + s.w && p.y >= n.y && p.y <= n.y + s.h) return id
    }
    return null
  }

  function onNodePointerMove(e: PointerEvent<HTMLDivElement>) {
    const d = drag.current
    if (!d || d.pointer !== e.pointerId) return
    const p = toWorld(e.clientX, e.clientY)
    const dx = p.x - d.startX
    const dy = p.y - d.startY
    if (!d.moved && Math.hypot(dx, dy) * transform.k < DRAG_THRESHOLD) return
    d.moved = true
    const moved = moveBy(d.origin, d.id, dx, dy)
    setHistory((h) => (h ? { ...h, present: moved } : h))
    const skip = new Set(Object.keys(d.origin.nodes).filter((id) => !canReparent(d.origin, d.id, id)))
    const target = nodeAt(e.clientX, e.clientY, skip)
    setDropTarget(target && target !== parentOf(d.origin, d.id) ? target : null)
  }

  function onNodePointerUp(e: PointerEvent<HTMLDivElement>) {
    const d = drag.current
    drag.current = null
    if (!d || d.pointer !== e.pointerId || !d.moved) return
    const target = dropTarget
    setDropTarget(null)
    let next = current
    if (target) {
      // 放到另一个节点上：换个父节点
      next = reparent(d.origin, d.id, target, sizeOf)
    } else {
      const parent = parentOf(next, d.id)
      if (parent) next = sortChildrenByY(next, parent)
    }
    setHistory((h) => (h ? { past: [...h.past, d.origin].slice(-HISTORY_MAX), present: next, future: [] } : h))
    schedule(next)
  }

  // ---------- 导出图片 ----------

  async function exportPng() {
    const el = world.current
    if (!el) return
    setExporting(true)
    setEditing(null)
    try {
      const box = bounds(current, sizeOf)
      const pad = 32
      const bg = getComputedStyle(document.body).backgroundColor
      const width = box.maxX - box.minX + pad * 2
      const height = box.maxY - box.minY + pad * 2
      const svg = await toSvg(el, {
        width,
        height,
        style: { transform: `translate(${pad - box.minX}px, ${pad - box.minY}px)` },
        backgroundColor: bg,
        // 不嵌网页字体：思源宋体拆成了上百个子集文件，全部下载嵌进去要很久；图里的字用系统字体
        skipFonts: true,
        filter: (node) => !(node instanceof HTMLElement && node.dataset.export === "skip"),
      })
      const url = await rasterize(svg, width, height, 2)
      const a = document.createElement("a")
      a.href = url
      a.download = `${current.title}.png`
      a.click()
    } catch (e) {
      setSaveError(`导出图片失败：${(e as Error).message}`)
    } finally {
      setExporting(false)
    }
  }

  // ---------- 画 ----------

  const box = bounds(current, sizeOf)
  const edges = visible.flatMap((id) => {
    const node = current.nodes[id]
    if (node.collapsed) return []
    const ps = sizeOf(id)
    return node.children.map((child) => {
      const c = current.nodes[child]
      const cs = sizeOf(child)
      const right = c.x + cs.w / 2 >= node.x + ps.w / 2
      const x1 = right ? node.x + ps.w : node.x
      const y1 = node.y + ps.h / 2
      const x2 = right ? c.x : c.x + cs.w
      const y2 = c.y + cs.h / 2
      const mid = (x1 + x2) / 2
      return { key: `${id}-${child}`, d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`, color: c.color }
    })
  })

  const selNode = current.nodes[sel]
  const canUndo = history.past.length > 0
  const canRedo = history.future.length > 0

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1">
        <Button variant="ghost" size="sm" onClick={() => void flush().then(onBack)}>
          <ArrowLeftIcon />
          全部导图
        </Button>
        <input
          aria-label="导图名字"
          className="h-8 min-w-40 flex-1 rounded-md bg-transparent px-2 font-heading text-lg outline-none hover:bg-muted focus:bg-muted"
          value={current.title}
          maxLength={80}
          onChange={(e) => e.target.value.trim() && apply({ ...current, title: e.target.value }, false)}
        />
        <span role="status" className="mr-2 text-xs text-muted-foreground">
          {{ saved: "已保存", pending: "有改动…", saving: "保存中…", error: "保存失败" }[saveState]}
        </span>
        <Tool label="加子节点（Tab）" onClick={() => addChildTo(sel)}>
          <PlusIcon />
        </Tool>
        <Tool label="加同级节点（Enter）" onClick={() => addSiblingOf(sel)} disabled={sel === current.root}>
          <span className="text-xs font-medium">同级</span>
        </Tool>
        <Tool label="删除（Delete）" onClick={() => remove(sel)} disabled={sel === current.root}>
          <Trash2Icon />
        </Tool>
        <Divider />
        <Tool label="颜色" active={panel === "color"} onClick={() => setPanel(panel === "color" ? null : "color")}>
          <PaletteIcon />
        </Tool>
        <Tool label="备注" active={panel === "note"} onClick={() => setPanel(panel === "note" ? null : "note")}>
          <StickyNoteIcon />
        </Tool>
        <Tool label="链接笔记（编辑时输入 @）" active={panel === "link"} onClick={() => setPanel(panel === "link" ? null : "link")}>
          <Link2Icon />
        </Tool>
        <Divider />
        <Tool label="撤销（Ctrl Z）" onClick={undo} disabled={!canUndo}>
          <Undo2Icon />
        </Tool>
        <Tool label="重做（Ctrl Y）" onClick={redo} disabled={!canRedo}>
          <Redo2Icon />
        </Tool>
        <Divider />
        <Tool label="自动排列" onClick={() => apply(autoLayout(current, sizeOf))}>
          <LayoutGridIcon />
        </Tool>
        <Tool label="适应画布" onClick={() => fit(box, 1.2)}>
          <MaximizeIcon />
        </Tool>
        <Tool label="缩小" onClick={() => zoomBy(1 / 1.25)}>
          <MinusIcon />
        </Tool>
        <Tool label="放大" onClick={() => zoomBy(1.25)}>
          <PlusIcon />
        </Tool>
        <Tool label="导出图片（PNG）" onClick={() => void exportPng()} disabled={exporting}>
          <ImageDownIcon />
        </Tool>
      </div>
      {saveError && <Alert tone="error">{saveError}</Alert>}

      <div className="relative min-h-96 flex-1 overflow-hidden rounded-xl border bg-card shadow-[0_1px_2px_rgba(16,32,44,0.04)]">
        <div
          ref={canvas}
          tabIndex={0}
          aria-label="导图画布：方向键选节点，Tab 加子节点，Enter 加同级，F2 编辑，Delete 删除"
          onKeyDown={onKeyDown}
          className="size-full cursor-grab touch-none outline-none select-none active:cursor-grabbing"
          {...panHandlers}
          onPointerDown={(e) => {
            if (editing) commitEdit(editing.text)
            canvas.current?.focus()
            panHandlers.onPointerDown(e)
          }}
        >
          <div
            ref={world}
            className="absolute top-0 left-0 origin-top-left"
            style={{ transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.k})` }}
          >
            <svg
              className="pointer-events-none absolute overflow-visible"
              style={{ left: box.minX, top: box.minY, width: box.maxX - box.minX, height: box.maxY - box.minY }}
            >
              <g transform={`translate(${-box.minX},${-box.minY})`} fill="none" strokeWidth={1.6}>
                {edges.map((edge) => (
                  <path
                    key={edge.key}
                    d={edge.d}
                    stroke={edge.color ? COLOR_SWATCH[edge.color] : "var(--input)"}
                  />
                ))}
              </g>
            </svg>
            {visible.map((id) => {
              const node = current.nodes[id]
              const isRoot = id === current.root
              const note = node.link ? notes[node.link.id] : undefined
              const isEditing = editing?.id === id
              return (
                <div
                  key={id}
                  ref={(el) => {
                    if (el) observer.observe(el)
                  }}
                  data-node={id}
                  role="treeitem"
                  aria-selected={sel === id}
                  aria-expanded={node.children.length > 0 ? !node.collapsed : undefined}
                  onPointerDown={(e) => onNodePointerDown(e, id)}
                  onPointerMove={onNodePointerMove}
                  onPointerUp={onNodePointerUp}
                  onDoubleClick={(e) => {
                    e.stopPropagation()
                    startEdit(id)
                  }}
                  className={cn(
                    "absolute flex max-w-[22rem] cursor-default items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm leading-snug whitespace-pre-wrap shadow-[0_1px_1px_rgba(16,32,44,0.05)]",
                    isRoot
                      ? "rounded-xl border-primary bg-primary px-5 py-2.5 text-[15px] font-semibold text-primary-foreground"
                      : node.color
                        ? COLOR_STYLE[node.color]
                        : "border-border bg-background",
                    sel === id && "ring-2 ring-ring/70 ring-offset-1 ring-offset-card",
                    dropTarget === id && "ring-2 ring-emerald-500 ring-offset-1",
                  )}
                  style={{ left: node.x, top: node.y }}
                >
                  {isEditing ? (
                    <NodeInput
                      editing={editing}
                      titles={titles}
                      onChange={(text) => setEditing({ ...editing, text, pick: 0 })}
                      onPick={(pick) => setEditing({ ...editing, pick })}
                      onCommit={commitEdit}
                      onCancel={cancelEdit}
                    />
                  ) : (
                    <span className={cn("min-w-4", !node.text && "text-muted-foreground")}>
                      {node.text || "未命名"}
                    </span>
                  )}
                  {node.link && !isEditing && (
                    <button
                      type="button"
                      data-export="skip"
                      title={note ? `打开笔记「${note.title}」` : `笔记「${node.link.title}」已经删除`}
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={() => note && onOpenNote(note)}
                      className={cn("shrink-0 rounded p-0.5 hover:bg-foreground/10", !note && "opacity-50")}
                    >
                      {note ? <Link2Icon className="size-3.5" /> : <Link2OffIcon className="size-3.5" />}
                    </button>
                  )}
                  {node.note && !isEditing && (
                    <button
                      type="button"
                      data-export="skip"
                      title="看备注"
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={() => {
                        setSelected(id)
                        setPanel("note")
                      }}
                      className="shrink-0 rounded p-0.5 hover:bg-foreground/10"
                    >
                      <StickyNoteIcon className="size-3.5" />
                    </button>
                  )}
                  {node.children.length > 0 && (
                    <button
                      type="button"
                      data-export="skip"
                      title={node.collapsed ? `展开（${node.children.length}）` : "收起"}
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={() => apply(toggleCollapsed(current, id))}
                      className="absolute top-1/2 -right-3 grid size-5 -translate-y-1/2 place-items-center rounded-full border bg-background text-[10px] text-muted-foreground hover:text-foreground"
                    >
                      {node.collapsed ? node.children.length : "−"}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {panel === "color" && (
          <Panel title="颜色" onClose={() => setPanel(null)}>
            <div className="grid grid-cols-5 gap-2">
              <button
                type="button"
                title="默认"
                onClick={() => apply(setColor(current, sel, null, branch))}
                className={cn("size-7 rounded-full border bg-background", !selNode.color && "ring-2 ring-ring")}
              />
              {COLORS.map((color) => (
                <button
                  key={color}
                  type="button"
                  title={COLOR_NAME[color]}
                  onClick={() => apply(setColor(current, sel, color, branch))}
                  className={cn("size-7 rounded-full", selNode.color === color && "ring-2 ring-ring ring-offset-2 ring-offset-popover")}
                  style={{ background: COLOR_SWATCH[color] }}
                />
              ))}
            </div>
            <label className="mt-3 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={branch} onChange={(e) => setBranch(e.target.checked)} />
              整个分支一起改
            </label>
          </Panel>
        )}
        {panel === "note" && (
          <Panel title={`备注 · ${selNode.text || "未命名"}`} onClose={() => setPanel(null)}>
            <NoteEditor
              key={sel}
              value={selNode.note ?? ""}
              onSave={(text) => {
                const target = sel
                applyLatest((d) => (d.nodes[target] ? setNote(d, target, text) : d))
              }}
            />
          </Panel>
        )}
        {panel === "link" && (
          <Panel title="链接笔记" onClose={() => setPanel(null)}>
            {selNode.link && (
              <div className="mb-3 flex items-center gap-2 text-sm">
                <span className="min-w-0 flex-1 truncate">
                  已链到「{notes[selNode.link.id]?.title ?? `${selNode.link.title}（已删除）`}」
                </span>
                <Button size="sm" variant="ghost" onClick={() => apply(setLink(current, sel, null))}>
                  取消链接
                </Button>
              </div>
            )}
            <LinkPicker
              titles={titles}
              onPick={(t) => {
                let next = setLink(current, sel, { id: t.id, title: t.title })
                if (!selNode.text) next = setText(next, sel, t.title)
                apply(next)
                setPanel(null)
              }}
            />
          </Panel>
        )}

        <p className="pointer-events-none absolute bottom-2 left-3 text-[11px] text-muted-foreground">
          双击编辑 · Tab 子节点 · Enter 同级 · 拖到别的节点上换父节点 · 编辑时输入 @ 链接笔记
        </p>
      </div>
    </div>
  )
}

// SVG 画成 PNG。不用 html-to-image 的 toPng：它要等 requestAnimationFrame，窗口没在绘制时（被挡住、最小化）一直等不到
async function rasterize(svgUrl: string, width: number, height: number, ratio: number): Promise<string> {
  const img = new Image()
  await new Promise<void>((resolve, reject) => {
    img.onload = () => resolve()
    img.onerror = () => reject(new Error("图片生成失败"))
    img.src = svgUrl
  })
  const canvas = document.createElement("canvas")
  canvas.width = Math.ceil(width * ratio)
  canvas.height = Math.ceil(height * ratio)
  const ctx = canvas.getContext("2d")
  if (!ctx) throw new Error("浏览器不支持画布")
  ctx.scale(ratio, ratio)
  ctx.drawImage(img, 0, 0, width, height)
  return canvas.toDataURL("image/png")
}

function NodeInput({
  editing,
  titles,
  onChange,
  onPick,
  onCommit,
  onCancel,
}: {
  editing: Editing
  titles: NoteTitle[]
  onChange: (text: string) => void
  onPick: (index: number) => void
  onCommit: (text: string, link?: { id: string; title: string } | null) => void
  onCancel: () => void
}) {
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    input.current?.focus()
    input.current?.select()
  }, [])
  // 光标前面是「@ 加几个字」：在选笔记
  const mention = /@([^@\s]*)$/.exec(editing.text)
  const options = mention ? matchTitles(titles, mention[1]) : []

  function pick(t: NoteTitle) {
    const before = editing.text.slice(0, mention!.index).trimEnd()
    onCommit(before ? `${before} ${t.title}` : t.title, { id: t.id, title: t.title })
  }

  return (
    <span className="relative" onPointerDown={(e) => e.stopPropagation()}>
      <input
        ref={input}
        aria-label="节点文字"
        className="w-48 bg-transparent outline-none"
        value={editing.text}
        maxLength={200}
        placeholder="输入文字，@ 链接笔记"
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (options.length > 0 && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault()
            onPick((editing.pick + (e.key === "ArrowDown" ? 1 : options.length - 1)) % options.length)
          } else if (options.length > 0 && (e.key === "Enter" || e.key === "Tab")) {
            e.preventDefault()
            pick(options[editing.pick] ?? options[0])
          } else if (e.key === "Enter") {
            e.preventDefault()
            onCommit(editing.text)
          } else if (e.key === "Escape") {
            e.preventDefault()
            onCancel()
          }
        }}
        onBlur={() => onCommit(editing.text)}
      />
      {options.length > 0 && (
        <ul
          role="listbox"
          className="absolute top-full left-0 z-10 mt-2 w-64 overflow-hidden rounded-lg border bg-popover py-1 text-sm font-normal text-popover-foreground shadow-lg"
        >
          {options.map((t, i) => (
            <li
              key={t.id}
              role="option"
              aria-selected={i === editing.pick}
              onPointerDown={(e) => {
                // 先于输入框失去焦点，不然 blur 会把「@…」当文字存掉
                e.preventDefault()
                pick(t)
              }}
              className={cn("cursor-pointer truncate px-3 py-1.5", i === editing.pick && "bg-accent text-accent-foreground")}
            >
              {t.title}
              <span className="ml-2 text-xs text-muted-foreground">{t.type === "topic" ? "主题" : "概念"}</span>
            </li>
          ))}
        </ul>
      )}
    </span>
  )
}

function LinkPicker({ titles, onPick }: { titles: NoteTitle[]; onPick: (t: NoteTitle) => void }) {
  const [q, setQ] = useState("")
  const options = matchTitles(titles, q, 12)
  return (
    <div className="flex flex-col gap-2">
      <input
        autoFocus
        className="h-8 rounded-md border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        placeholder="搜笔记标题或别名"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === "Enter" && options[0]) onPick(options[0])
        }}
      />
      <ul className="max-h-64 overflow-y-auto">
        {options.map((t) => (
          <li key={t.id}>
            <button
              type="button"
              onClick={() => onPick(t)}
              className="w-full truncate rounded px-2 py-1.5 text-left text-sm hover:bg-accent"
            >
              {t.title}
            </button>
          </li>
        ))}
        {options.length === 0 && <li className="px-2 py-1.5 text-sm text-muted-foreground">没有对得上的笔记</li>}
      </ul>
    </div>
  )
}

function NoteEditor({ value, onSave }: { value: string; onSave: (text: string) => void }) {
  const [text, setText] = useState(value)
  // 关掉面板、换了节点（组件卸掉）时也要存：那时不会再有 blur
  const latest = useRef({ text, value, onSave })
  latest.current = { text, value, onSave }
  useEffect(
    () => () => {
      const { text, value, onSave } = latest.current
      if (text !== value) onSave(text)
    },
    [],
  )
  return (
    <textarea
      autoFocus
      className="h-48 w-full resize-y rounded-md border bg-background p-2 text-sm leading-relaxed outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      placeholder="写点长一些的说明，导图上只显示一个小图标"
      maxLength={5000}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => e.stopPropagation()}
      onBlur={() => {
        if (text === value) return
        // 存过了：卸载时别再存一遍
        latest.current = { ...latest.current, value: text }
        onSave(text)
      }}
    />
  )
}

function Panel({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="absolute top-3 right-3 w-80 rounded-xl border bg-popover p-3 text-popover-foreground shadow-lg">
      <div className="mb-2 flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{title}</span>
        <button type="button" title="关闭" onClick={onClose} className="rounded p-1 text-muted-foreground hover:bg-muted">
          <XIcon className="size-4" />
        </button>
      </div>
      {children}
    </div>
  )
}

function Tool({
  label,
  onClick,
  disabled,
  active,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  active?: boolean
  children: React.ReactNode
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      title={label}
      aria-label={label}
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={cn(active && "bg-accent text-accent-foreground")}
    >
      {children}
    </Button>
  )
}

function Divider() {
  return <span className="mx-1 h-5 w-px bg-border" />
}
