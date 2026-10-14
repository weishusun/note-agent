import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { MaximizeIcon, MinusIcon, PlusIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { Button } from "@/components/ui/button"
import { api, type Graph, type Tree } from "@/lib/api"
import {
  buildMindMap,
  defaultExpanded,
  layoutMindMap,
  MAP_FONT,
  mapLabel,
  type MapNode,
  type Placed,
} from "@/lib/mindmap"
import { usePanZoom } from "@/lib/usePanZoom"
import { cn } from "@/lib/utils"

const KNOB = 7
const MARGIN = 24

// 思维导图：把所选范围展开成 大块 → 小块 → 主题笔记 → 概念笔记 的树
export function MindMapView({
  tree,
  category,
  version,
  onOpenNote,
}: {
  tree: Tree | null
  // 「大块」或「大块/小块」；null 表示全部笔记
  category: string | null
  // 笔记有变动时递增，重新取数据
  version: number
  onOpenNote: (note: { title: string; path: string }) => void
}) {
  const [loaded, setLoaded] = useState<{ category: string | null; graph: Graph } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.graph(category).then(
      (graph) => {
        if (cancelled) return
        setLoaded({ category, graph })
        setError(null)
      },
      (e: Error) => !cancelled && setError(e.message),
    )
    return () => {
      cancelled = true
    }
  }, [category, version])

  // 换了范围、数据还没到的那一瞬间，不拿上一个范围的数据来画
  const root = useMemo(
    () =>
      tree && loaded && loaded.category === category
        ? buildMindMap(tree, loaded.graph, category)
        : null,
    [tree, loaded, category],
  )

  if (error) return <Alert tone="error">{error}</Alert>
  if (!root) return <p className="text-sm text-muted-foreground">加载中…</p>
  if (root.count === 0 && root.children.length === 0) {
    return <p className="text-sm text-muted-foreground">这里还没有笔记。</p>
  }
  // key：换了范围就重新决定默认展开到哪一层、重新对准画面
  return <MindMapCanvas key={root.key} root={root} onOpenNote={onOpenNote} />
}

function MindMapCanvas({
  root,
  onOpenNote,
}: {
  root: MapNode
  onOpenNote: (note: { title: string; path: string }) => void
}) {
  const svg = useRef<SVGSVGElement>(null)
  const { transform, setTransform, zoomBy, panHandlers } = usePanZoom(svg)
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => defaultExpanded(root))
  const layout = useMemo(() => layoutMindMap(root, expanded), [root, expanded])

  // 对准画面：尽量整棵放进来，但不缩到字看不清；放不下就靠左上，剩下的靠拖动
  const reset = useCallback(() => {
    const element = svg.current
    if (!element) return
    const width = element.clientWidth
    const height = element.clientHeight
    const k = Math.min(
      1,
      Math.max(
        0.6,
        Math.min(
          (width - MARGIN * 2) / (layout.width + KNOB * 2),
          (height - MARGIN * 2) / layout.height,
        ),
      ),
    )
    const tall = layout.height * k > height - MARGIN * 2
    setTransform({
      k,
      x: MARGIN,
      // 整棵树放得下就上下居中；放不下就让根节点（它在树的正中间）出现在画面里
      y: tall
        ? height / 2 - (layout.nodes[0].y + layout.nodes[0].height / 2) * k
        : (height - layout.height * k) / 2,
    })
  }, [layout, setTransform])

  const first = useRef(true)
  useEffect(() => {
    // 只在第一次对准；之后展开、折叠时画面不跳，免得找不到刚才点的地方
    if (!first.current) return
    first.current = false
    reset()
  }, [reset])

  function toggle(key: string) {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const allKeys = useMemo(() => {
    const keys: string[] = []
    const walk = (node: MapNode) => {
      if (node.children.length > 0) keys.push(node.key)
      node.children.forEach(walk)
    }
    walk(root)
    return keys
  }, [root])
  const allOpen = allKeys.every((key) => expanded.has(key))

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="relative min-h-96 flex-1 overflow-hidden rounded-xl border bg-card shadow-[0_1px_2px_rgba(16,32,44,0.04)]">
        <svg
          ref={svg}
          role="img"
          aria-label="思维导图"
          className="size-full cursor-grab touch-none select-none text-foreground active:cursor-grabbing"
          {...panHandlers}
        >
          <g transform={`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
            <g fill="none" className="stroke-border" strokeWidth={1.5}>
              {layout.nodes.map((placed) => placed.parent && <Branch key={placed.node.key} to={placed} />)}
            </g>
            {layout.nodes.map((placed) => (
              <MapBox
                key={placed.node.key}
                placed={placed}
                onToggle={() => toggle(placed.node.key)}
                onOpenNote={onOpenNote}
              />
            ))}
          </g>
        </svg>

        <div className="absolute top-2 right-2 flex gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setExpanded(allOpen ? new Set([root.key]) : new Set(allKeys))}
          >
            {allOpen ? "全部收起" : "全部展开"}
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="放大" onClick={() => zoomBy(1.25)}>
            <PlusIcon />
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="缩小" onClick={() => zoomBy(1 / 1.25)}>
            <MinusIcon />
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="回到起点" title="回到起点" onClick={reset}>
            <MaximizeIcon />
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        粗框是主题笔记，它右边连着的是出自它的概念。点分类或圆钮展开、收起，点笔记打开它。拖动空白处平移，滚轮缩放。
      </p>
    </div>
  )
}

// 父节点右边缘中点 → 子节点左边缘中点的一条 S 形曲线
function Branch({ to }: { to: Placed }) {
  const from = to.parent!
  const x1 = from.x + from.width
  const y1 = from.y + from.height / 2
  const x2 = to.x
  const y2 = to.y + to.height / 2
  const mid = (x1 + x2) / 2
  return <path d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`} />
}

function MapBox({
  placed,
  onToggle,
  onOpenNote,
}: {
  placed: Placed
  onToggle: () => void
  onOpenNote: (note: { title: string; path: string }) => void
}) {
  const { node } = placed
  const foldable = node.children.length > 0
  const isCategory = !node.note
  return (
    <g
      transform={`translate(${placed.x},${placed.y})`}
      data-key={node.key}
      // 在节点上按下不开始平移，否则指针被画布抓走，下面的点击就收不到了
      onPointerDown={(e) => e.stopPropagation()}
    >
      <g
        role="button"
        tabIndex={0}
        aria-label={node.note ? `打开笔记 ${node.label}` : `${node.label}，${node.count} 条笔记`}
        aria-expanded={isCategory && foldable ? placed.expanded : undefined}
        className="cursor-pointer outline-none [&:focus-visible>rect]:stroke-ring [&:hover>rect]:stroke-foreground/50"
        onClick={() => (node.note ? onOpenNote(node.note) : foldable && onToggle())}
        onKeyDown={(e) => {
          if (e.key !== "Enter" && e.key !== " ") return
          e.preventDefault()
          if (node.note) onOpenNote(node.note)
          else if (foldable) onToggle()
        }}
      >
        <title>{node.label}</title>
        <rect
          width={placed.width}
          height={placed.height}
          rx={node.kind === "root" || node.kind === "parent" ? placed.height / 2 : 6}
          strokeWidth={node.kind === "topic" ? 2 : 1}
          className={cn(
            "stroke-border",
            node.kind === "root" && "fill-primary stroke-primary",
            (node.kind === "parent" || node.kind === "group") && "fill-muted",
            node.kind === "child" && "fill-muted/50",
            node.kind === "topic" && "fill-background stroke-foreground/60",
            node.kind === "concept" && "fill-background",
          )}
        />
        <text
          x={10}
          y={placed.height / 2}
          dominantBaseline="central"
          fontSize={MAP_FONT}
          fontWeight={isCategory || node.kind === "topic" ? 600 : 400}
          className={cn("pointer-events-none", node.kind === "root" ? "fill-primary-foreground" : "fill-foreground")}
          style={{ whiteSpace: "pre" }}
        >
          {mapLabel(node)}
        </text>
      </g>
      {foldable && (
        <g
          role="button"
          tabIndex={0}
          aria-label={placed.expanded ? `收起 ${node.label}` : `展开 ${node.label}（${node.children.length} 项）`}
          transform={`translate(${placed.width + KNOB + 3},${placed.height / 2})`}
          className="cursor-pointer outline-none [&:focus-visible>circle]:stroke-ring [&:hover>circle]:stroke-foreground/60"
          onClick={onToggle}
          onKeyDown={(e) => {
            if (e.key !== "Enter" && e.key !== " ") return
            e.preventDefault()
            onToggle()
          }}
        >
          <circle r={KNOB} className="fill-background stroke-border" strokeWidth={1.5} />
          <path
            d={placed.expanded ? "M-3.5,0 H3.5" : "M-3.5,0 H3.5 M0,-3.5 V3.5"}
            className="stroke-muted-foreground"
            strokeWidth={1.5}
            strokeLinecap="round"
          />
        </g>
      )}
    </g>
  )
}
