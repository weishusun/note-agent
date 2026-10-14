import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from "react"
import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force"
import { MaximizeIcon, MinusIcon, PlusIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { Button } from "@/components/ui/button"
import { api, categoryLabel, type Graph, type GraphNode } from "@/lib/api"
import { textWidth } from "@/lib/mindmap"
import { usePanZoom } from "@/lib/usePanZoom"
import { cn } from "@/lib/utils"

// 超过这么多条就不画了：点挤成一团，看不出东西，也拖不动
export const GRAPH_MAX_NOTES = 500
// 点不多时每个点都带名字；多了只给主题笔记和鼠标指着的那一片带
const LABEL_ALL_BELOW = 60
const LABEL_CHARS = 14
const LABEL_FONT = 11
const LEGEND_MAX = 12
const UNSORTED = "还没有分类"

interface SimNode extends SimulationNodeDatum {
  note: GraphNode
  radius: number
  // 画成什么颜色看它属于哪一组（见 groupOf）
  group: string
}

type SimLink = SimulationLinkDatum<SimNode> & { source: SimNode; target: SimNode }

interface Layout {
  nodes: SimNode[]
  links: SimLink[]
  neighbors: Map<string, Set<string>>
  // 组名 → 颜色
  colors: Map<string, string>
}

// 看全部笔记时按大块上色；看一个大块或小块时按小块上色
function groupOf(note: GraphNode, byParent: boolean): string {
  if (!note.category) return UNSORTED
  return byParent ? note.category.split("/")[0] : note.category
}

function buildLayout(graph: Graph, byParent: boolean): Layout {
  const degree = new Map<string, number>()
  for (const edge of graph.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1)
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1)
  }
  const nodes: SimNode[] = graph.nodes.map((note) => ({
    note,
    group: groupOf(note, byParent),
    // 主题笔记大一些；连线多的也稍大一些
    radius: (note.type === "topic" ? 8 : 5) + Math.min(4, Math.sqrt(degree.get(note.path) ?? 0)),
  }))
  const byPath = new Map(nodes.map((n) => [n.note.path, n]))
  const links: SimLink[] = []
  const neighbors = new Map<string, Set<string>>()
  for (const edge of graph.edges) {
    const source = byPath.get(edge.source)
    const target = byPath.get(edge.target)
    if (!source || !target) continue
    links.push({ source, target })
    if (!neighbors.has(edge.source)) neighbors.set(edge.source, new Set())
    if (!neighbors.has(edge.target)) neighbors.set(edge.target, new Set())
    neighbors.get(edge.source)!.add(edge.target)
    neighbors.get(edge.target)!.add(edge.source)
  }

  // 颜色只分给块内的笔记；笔记多的组排在前面。色相按黄金角错开，相邻两组不会撞色
  const sizes = new Map<string, number>()
  for (const n of nodes) if (!n.note.outside) sizes.set(n.group, (sizes.get(n.group) ?? 0) + 1)
  const colors = new Map<string, string>()
  ;[...sizes.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .forEach(([group], i) => colors.set(group, `hsl(${Math.round((210 + i * 137.5) % 360)} 62% 52%)`))

  return { nodes, links, neighbors, colors }
}

function createSimulation(layout: Layout): Simulation<SimNode, SimLink> {
  return forceSimulation(layout.nodes)
    .force(
      "link",
      forceLink<SimNode, SimLink>(layout.links)
        .distance((l) => 62 + l.source.radius + l.target.radius)
        .strength(0.5),
    )
    .force("charge", forceManyBody<SimNode>().strength(-190).distanceMax(460))
    // 碰撞半径留出名字的高度，上下相邻的两个点名字不会叠在一起
    .force("collide", forceCollide<SimNode>((n) => n.radius + 13))
    // 没有连线的笔记靠这两个弱的向心力留在画面附近，不会飘走
    .force("x", forceX<SimNode>(0).strength(0.045))
    .force("y", forceY<SimNode>(0).strength(0.045))
    .stop()
}

// 抓住指针，拖出元素范围也能继续收到移动事件。指针已经不在了（极快的点击）时会抛异常，忽略即可
function capture(e: PointerEvent<Element>) {
  try {
    e.currentTarget.setPointerCapture(e.pointerId)
  } catch {
    // 没抓住也只是拖出范围后跟不上，不影响别的
  }
}

function bounds(nodes: SimNode[]) {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const n of nodes) {
    minX = Math.min(minX, (n.x ?? 0) - n.radius)
    minY = Math.min(minY, (n.y ?? 0) - n.radius)
    // 名字画在点的右边：把它的宽度也算进去，「看全图」时才不会被右边缘裁掉
    maxX = Math.max(maxX, (n.x ?? 0) + n.radius + 4 + textWidth(shorten(n.note.title), LABEL_FONT))
    maxY = Math.max(maxY, (n.y ?? 0) + n.radius)
  }
  return nodes.length > 0 ? { minX, minY, maxX, maxY } : { minX: -1, minY: -1, maxX: 1, maxY: 1 }
}

function shorten(title: string): string {
  return title.length > LABEL_CHARS ? title.slice(0, LABEL_CHARS - 1) + "…" : title
}

// 关系图谱：一条笔记一个点，正文里有 [[双链]] 就连一条线（SCHEMA GET /api/graph）
export function GraphView({
  category,
  count,
  version,
  onOpenNote,
}: {
  // 「大块」或「大块/小块」；null 表示全部笔记
  category: string | null
  // 这个范围里有多少条笔记：太多就不画
  count: number
  // 笔记有变动时递增，重新取数据
  version: number
  onOpenNote: (note: { title: string; path: string }) => void
}) {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [error, setError] = useState<string | null>(null)
  const tooMany = count > GRAPH_MAX_NOTES

  useEffect(() => {
    if (tooMany) return
    let cancelled = false
    api.graph(category).then(
      (g) => {
        if (cancelled) return
        setGraph(g)
        setError(null)
      },
      (e: Error) => !cancelled && setError(e.message),
    )
    return () => {
      cancelled = true
    }
  }, [category, version, tooMany])

  if (tooMany) {
    return (
      <Alert tone="info">
        这里有 {count} 条笔记，全画出来会挤成一团。在左边选一个{category ? "小块" : "大块"}
        再看，最多能画 {GRAPH_MAX_NOTES} 条。
      </Alert>
    )
  }
  if (error) return <Alert tone="error">{error}</Alert>
  if (!graph) return <p className="text-sm text-muted-foreground">加载中…</p>
  if (graph.nodes.length === 0) {
    return <p className="text-sm text-muted-foreground">这里还没有笔记。</p>
  }
  return <GraphCanvas graph={graph} byParent={category === null} onOpenNote={onOpenNote} />
}

function GraphCanvas({
  graph,
  byParent,
  onOpenNote,
}: {
  graph: Graph
  byParent: boolean
  onOpenNote: (note: { title: string; path: string }) => void
}) {
  const svg = useRef<SVGSVGElement>(null)
  const { transform, toWorld, fit, zoomBy, panHandlers } = usePanZoom(svg)
  const [hover, setHover] = useState<string | null>(null)
  // 拖动节点时模拟在跑，靠它触发重画
  const [, setFrame] = useState(0)
  const simulation = useRef<Simulation<SimNode, SimLink> | null>(null)
  const drag = useRef<{ node: SimNode; moved: boolean; x: number; y: number } | null>(null)

  const layout = useMemo(() => buildLayout(graph, byParent), [graph, byParent])

  useEffect(() => {
    const sim = createSimulation(layout)
    // 先算到基本稳定再显示，免得一打开所有点从中间炸开
    sim.tick(Math.min(320, 80 + layout.nodes.length))
    sim.on("tick", () => setFrame((f) => f + 1))
    simulation.current = sim
    fit(bounds(layout.nodes))
    setFrame((f) => f + 1)
    return () => {
      sim.stop()
      simulation.current = null
    }
  }, [layout, fit])

  const refit = useCallback(() => fit(bounds(layout.nodes)), [fit, layout])

  function startDrag(e: PointerEvent<SVGGElement>, node: SimNode) {
    if (e.button !== 0) return
    e.stopPropagation()
    capture(e)
    drag.current = { node, moved: false, x: e.clientX, y: e.clientY }
  }

  function moveDrag(e: PointerEvent<SVGGElement>) {
    const state = drag.current
    if (!state) return
    if (!state.moved && Math.hypot(e.clientX - state.x, e.clientY - state.y) < 4) return
    state.moved = true
    const point = toWorld(e.clientX, e.clientY)
    // 被拖的点直接跟手（不等模拟的下一帧）；fx / fy 让模拟把它钉在这儿，别的点跟着让位
    state.node.x = state.node.fx = point.x
    state.node.y = state.node.fy = point.y
    setFrame((f) => f + 1)
    simulation.current?.alphaTarget(0.25).restart()
  }

  function endDrag() {
    const state = drag.current
    if (!state) return
    drag.current = null
    if (!state.moved) {
      // 没拖动就是点击：打开这条笔记
      onOpenNote({ title: state.node.note.title, path: state.node.note.path })
      return
    }
    state.node.fx = null
    state.node.fy = null
    simulation.current?.alphaTarget(0)
  }

  const near = hover ? layout.neighbors.get(hover) : undefined
  const lit = (path: string) => !hover || path === hover || !!near?.has(path)
  const labelAll = layout.nodes.length <= LABEL_ALL_BELOW || transform.k >= 1.4
  const isolated = layout.nodes.filter((n) => !layout.neighbors.has(n.note.path)).length
  const outside = layout.nodes.filter((n) => n.note.outside).length
  const legend = [...layout.colors.entries()]

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="relative min-h-96 flex-1 overflow-hidden rounded-xl border bg-card shadow-[0_1px_2px_rgba(16,32,44,0.04)]">
        <svg
          ref={svg}
          role="img"
          aria-label="关系图谱"
          className="size-full cursor-grab touch-none select-none text-foreground active:cursor-grabbing"
          {...panHandlers}
        >
          <g transform={`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
            <g stroke="currentColor">
              {layout.links.map((link) => {
                const on = hover
                  ? link.source.note.path === hover || link.target.note.path === hover
                  : true
                return (
                  <line
                    key={`${link.source.note.path}\n${link.target.note.path}`}
                    x1={link.source.x}
                    y1={link.source.y}
                    x2={link.target.x}
                    y2={link.target.y}
                    strokeOpacity={hover ? (on ? 0.7 : 0.05) : 0.22}
                    strokeWidth={on && hover ? 1.6 : 1}
                  />
                )
              })}
            </g>
            {layout.nodes.map((node) => {
              const { note } = node
              const on = lit(note.path)
              const color = note.outside ? undefined : layout.colors.get(node.group)
              const labelled = labelAll || note.type === "topic" || (hover !== null && on)
              return (
                <g
                  key={note.path}
                  data-path={note.path}
                  transform={`translate(${node.x ?? 0},${node.y ?? 0})`}
                  opacity={on ? (note.outside ? 0.55 : 1) : 0.12}
                  className="cursor-pointer"
                  onPointerDown={(e) => startDrag(e, node)}
                  onPointerMove={moveDrag}
                  onPointerUp={endDrag}
                  onPointerCancel={endDrag}
                  onPointerEnter={() => setHover(note.path)}
                  onPointerLeave={() => setHover((h) => (h === note.path ? null : h))}
                >
                  <title>
                    {note.title}
                    {note.category ? `（${categoryLabel(note.category)}）` : ""}
                  </title>
                  <circle
                    r={node.radius}
                    fill={color ?? "currentColor"}
                    fillOpacity={note.outside ? 0.25 : note.status === "inbox" ? 0.35 : 1}
                    stroke={note.type === "topic" ? "currentColor" : (color ?? "currentColor")}
                    strokeWidth={note.type === "topic" ? 2 : 1}
                    strokeDasharray={note.outside ? "3 2" : undefined}
                  />
                  {labelled && (
                    <text
                      x={node.radius + 4}
                      y={4}
                      fontSize={LABEL_FONT}
                      fill="currentColor"
                      fontWeight={note.type === "topic" ? 600 : 400}
                      // 字压在线上时用背景色描一圈边，保证看得清
                      className="pointer-events-none [paint-order:stroke] [stroke-linejoin:round] [stroke:var(--card)] [stroke-width:3px]"
                    >
                      {shorten(note.title)}
                    </text>
                  )}
                </g>
              )
            })}
          </g>
        </svg>

        <div className="absolute top-2 right-2 flex gap-1">
          <Button variant="outline" size="icon-sm" aria-label="放大" onClick={() => zoomBy(1.3)}>
            <PlusIcon />
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="缩小" onClick={() => zoomBy(1 / 1.3)}>
            <MinusIcon />
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="看全图" title="看全图" onClick={refit}>
            <MaximizeIcon />
          </Button>
        </div>

        {legend.length > 1 && (
          <ul
            aria-label="图例"
            className="absolute bottom-2 left-2 flex max-w-[60%] flex-wrap gap-x-3 gap-y-1 rounded-md bg-background/85 px-2 py-1.5 text-xs"
          >
            {legend.slice(0, LEGEND_MAX).map(([group, color]) => (
              <li key={group} className="flex items-center gap-1.5">
                <span className="size-2.5 rounded-full" style={{ background: color }} />
                {byParent || group === UNSORTED ? group : group.split("/")[1]}
              </li>
            ))}
            {legend.length > LEGEND_MAX && (
              <li className="text-muted-foreground">等 {legend.length} 个</li>
            )}
          </ul>
        )}
      </div>

      <p className={cn("text-xs text-muted-foreground")}>
        {layout.nodes.length - outside} 条笔记，{layout.links.length} 条连线
        {outside > 0 && `；另有 ${outside} 条别处的笔记和这里有链接（虚线圈）`}
        {isolated > 0 && `；${isolated} 条还没有和别的笔记连起来`}
        。大圆点是主题笔记。拖动空白处平移，滚轮缩放，点一下圆点打开笔记。
      </p>
    </div>
  )
}
