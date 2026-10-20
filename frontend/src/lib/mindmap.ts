// 思维导图的数据和排版，都是纯函数：分类树 + 笔记之间的链接 → 一棵树 → 每个节点画在哪。
// 层次是 大块 → 小块 → 主题笔记 → 概念笔记，每条笔记在树里只出现一次。

import type { Graph, GraphNode, Tree } from "@/lib/api"

export type MapKind = "root" | "parent" | "child" | "group" | "topic" | "concept"

export interface MapNode {
  key: string
  kind: MapKind
  label: string
  // 笔记节点才有：点它打开这条笔记
  note?: { title: string; path: string; id: string | null }
  // 分类节点下面一共有多少条笔记（含折叠起来的）
  count: number
  children: MapNode[]
}

const collator = new Intl.Collator("zh-Hans-CN")
const byTitle = (a: GraphNode, b: GraphNode) => collator.compare(a.title, b.title)

function noteNode(note: GraphNode, children: MapNode[] = []): MapNode {
  return {
    key: `note:${note.path}`,
    kind: note.type,
    label: note.title,
    note: { title: note.title, path: note.path, id: note.id },
    count: children.length,
    children,
  }
}

// category：「大块」「大块/小块」或 null（全部笔记）。graph 是同一个范围的 GET /api/graph 结果
export function buildMindMap(tree: Tree, graph: Graph, category: string | null): MapNode {
  const linked = new Map<string, Set<string>>()
  for (const { source, target } of graph.edges) {
    if (!linked.has(source)) linked.set(source, new Set())
    if (!linked.has(target)) linked.set(target, new Set())
    linked.get(source)!.add(target)
    linked.get(target)!.add(source)
  }

  // 已归类的笔记按小块分组；待整理的（模型拿不准、或没分上类）单独一组
  const byCategory = new Map<string, GraphNode[]>()
  const unsorted: GraphNode[] = []
  for (const note of graph.nodes) {
    if (note.outside) continue
    if (note.status !== "active" || !note.category) {
      unsorted.push(note)
      continue
    }
    if (!byCategory.has(note.category)) byCategory.set(note.category, [])
    byCategory.get(note.category)!.push(note)
  }

  // 一个小块里的笔记：主题笔记在前，各自带着出自它的概念笔记；其余概念笔记直接挂在小块下
  function notesOf(path: string): MapNode[] {
    const notes = byCategory.get(path) ?? []
    const topics = notes.filter((n) => n.type === "topic").sort(byTitle)
    const owned = new Map<string, GraphNode[]>(topics.map((t) => [t.path, []]))
    const loose: GraphNode[] = []
    for (const concept of notes.filter((n) => n.type !== "topic").sort(byTitle)) {
      // 一个概念出现在好几份资料里时，只挂到第一条主题笔记下面，保证它在树里只出现一次
      const owner = topics.find((t) => linked.get(concept.path)?.has(t.path))
      if (owner) owned.get(owner.path)!.push(concept)
      else loose.push(concept)
    }
    return [
      ...topics.map((t) => noteNode(t, owned.get(t.path)!.map((c) => noteNode(c)))),
      ...loose.map((c) => noteNode(c)),
    ]
  }

  function leaf(name: string, path: string): MapNode {
    return {
      key: `cat:${path}`,
      kind: "child",
      label: name,
      count: byCategory.get(path)?.length ?? 0,
      children: notesOf(path),
    }
  }

  function parent(name: string): MapNode {
    // 目录里登记的小块，加上笔记上写着但目录里没有的——两边不一致时也不能让笔记消失
    const names = new Set(
      tree.categories.find((p) => p.name === name)?.children.map((c) => c.name) ?? [],
    )
    for (const path of byCategory.keys()) {
      const [p, c] = path.split("/")
      if (p === name && c) names.add(c)
    }
    const children = [...names].map((child) => leaf(child, `${name}/${child}`))
    children.sort((a, b) => b.count - a.count || collator.compare(a.label, b.label))
    return {
      key: `cat:${name}`,
      kind: "parent",
      label: name,
      count: children.reduce((sum, c) => sum + c.count, 0),
      children,
    }
  }

  if (category?.includes("/")) return leaf(category.split("/")[1], category)
  if (category) return parent(category)

  const names = new Set(tree.categories.map((p) => p.name))
  for (const path of byCategory.keys()) names.add(path.split("/")[0])
  const parents = [...names].map(parent)
  parents.sort((a, b) => b.count - a.count || collator.compare(a.label, b.label))
  if (unsorted.length > 0) {
    parents.push({
      key: "group:unsorted",
      kind: "group",
      label: "待整理",
      count: unsorted.length,
      children: unsorted.sort(byTitle).map((n) => noteNode(n)),
    })
  }
  return {
    key: "root",
    kind: "root",
    label: "全部笔记",
    count: parents.reduce((sum, p) => sum + p.count, 0),
    children: parents,
  }
}

function visibleLeaves(node: MapNode, expanded: ReadonlySet<string>): number {
  if (node.children.length === 0 || !expanded.has(node.key)) return 1
  return node.children.reduce((sum, child) => sum + visibleLeaves(child, expanded), 0)
}

// 一打开时展开到哪一层：从根往下一层一层展开，直到再展开一层就超过 maxRows 行为止。
// 笔记少时整棵树都摊开；笔记多时停在大块或小块这一层，由使用者自己点开
export function defaultExpanded(root: MapNode, maxRows = 34): Set<string> {
  const expanded = new Set<string>([root.key])
  let level = root.children
  while (level.length > 0) {
    const next = new Set(expanded)
    for (const node of level) if (node.children.length > 0) next.add(node.key)
    if (next.size === expanded.size || visibleLeaves(root, next) > maxRows) break
    for (const key of next) expanded.add(key)
    level = level.flatMap((node) => node.children)
  }
  return expanded
}

// 汉字约一个字号宽，字母数字约 0.6 个字号。只用来估计，不求精确
export function textWidth(text: string, fontSize: number): number {
  let width = 0
  for (const char of text) width += char.charCodeAt(0) > 0x2e7f ? fontSize : fontSize * 0.6
  return width
}

export interface Placed {
  node: MapNode
  depth: number
  // 节点方框的左上角和尺寸
  x: number
  y: number
  width: number
  height: number
  expanded: boolean
  parent: Placed | null
}

export const MAP_FONT = 12.5
const ROW = 30
const HEIGHT = 24
const PADDING = 10
const COLUMN_GAP = 44
const LABEL_MAX = 22

export function mapLabel(node: MapNode): string {
  const text =
    node.label.length > LABEL_MAX ? node.label.slice(0, LABEL_MAX - 1) + "…" : node.label
  // 分类后面带上笔记数；笔记节点不带
  return node.note ? text : `${text}  ${node.count}`
}

// 从左到右的树：同一层的节点左边对齐，叶子一行一个往下排，父节点居中对着它的孩子们
export function layoutMindMap(
  root: MapNode,
  expanded: ReadonlySet<string>,
): { nodes: Placed[]; width: number; height: number } {
  const nodes: Placed[] = []
  const widest: number[] = []
  let row = 0

  function place(node: MapNode, depth: number, parent: Placed | null): Placed {
    const open = node.children.length > 0 && expanded.has(node.key)
    const placed: Placed = {
      node,
      depth,
      x: 0,
      y: 0,
      width: textWidth(mapLabel(node), MAP_FONT) + PADDING * 2,
      height: HEIGHT,
      expanded: open,
      parent,
    }
    nodes.push(placed)
    widest[depth] = Math.max(widest[depth] ?? 0, placed.width)
    if (open) {
      const kids = node.children.map((child) => place(child, depth + 1, placed))
      placed.y = (kids[0].y + kids[kids.length - 1].y) / 2
    } else {
      placed.y = row++ * ROW
    }
    return placed
  }
  place(root, 0, null)

  const left: number[] = []
  let x = 0
  for (let depth = 0; depth < widest.length; depth++) {
    left[depth] = x
    // 留出折叠按钮和连线的位置
    x += widest[depth] + COLUMN_GAP
  }
  for (const placed of nodes) placed.x = left[placed.depth]
  return { nodes, width: x - COLUMN_GAP, height: Math.max(1, row) * ROW - (ROW - HEIGHT) }
}
