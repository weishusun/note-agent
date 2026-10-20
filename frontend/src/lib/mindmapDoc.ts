// 「我的导图」的数据和编辑操作，都是纯函数（SCHEMA §8）。每个操作返回一份新的导图，旧的不动——撤销 / 重做靠它。

import type { MapNode } from "@/lib/mindmap"

export const COLORS = ["blue", "teal", "green", "amber", "orange", "red", "pink", "violet"] as const
export type NodeColor = (typeof COLORS)[number]

export interface MindNode {
  text: string
  children: string[]
  // 节点左上角在画布上的位置
  x: number
  y: number
  color: NodeColor | null
  note: string | null
  link: { id: string; title: string } | null
  collapsed: boolean
}

export interface MindDoc {
  title: string
  root: string
  nodes: Record<string, MindNode>
}

export interface Size {
  w: number
  h: number
}

// 新节点离父节点、兄弟节点的距离
export const GAP_X = 56
export const GAP_Y = 14
export const TEXT_MAX = 200

export function newNodeId(): string {
  return Math.random().toString(36).slice(2, 10)
}

export function blankNode(text = "", x = 0, y = 0): MindNode {
  return { text, children: [], x, y, color: null, note: null, link: null, collapsed: false }
}

// 还没量到实际大小时按字数估一个（中文一个字约 14px，左右留白 32px）
export function estimateSize(node: MindNode, isRoot = false): Size {
  const chars = [...(node.text || " ")].length
  const perLine = isRoot ? 16 : 18
  const lines = Math.max(1, Math.ceil(chars / perLine))
  const w = Math.min(perLine, chars) * (isRoot ? 16 : 14) + (isRoot ? 44 : 32)
  return { w: Math.max(isRoot ? 96 : 64, w), h: (isRoot ? 22 : 20) * lines + (isRoot ? 22 : 16) }
}

export type SizeOf = (id: string) => Size

export function parentOf(doc: MindDoc, id: string): string | null {
  for (const [pid, node] of Object.entries(doc.nodes)) if (node.children.includes(id)) return pid
  return null
}

export function subtreeIds(doc: MindDoc, id: string): string[] {
  const out: string[] = []
  const stack = [id]
  while (stack.length > 0) {
    const current = stack.pop()!
    out.push(current)
    stack.push(...(doc.nodes[current]?.children ?? []))
  }
  return out
}

// 画面上看得见的节点：收起的节点的后代不算
export function visibleIds(doc: MindDoc): string[] {
  const out: string[] = []
  const walk = (id: string) => {
    out.push(id)
    const node = doc.nodes[id]
    if (!node.collapsed) node.children.forEach(walk)
  }
  walk(doc.root)
  return out
}

function update(doc: MindDoc, id: string, change: Partial<MindNode>): MindDoc {
  return { ...doc, nodes: { ...doc.nodes, [id]: { ...doc.nodes[id], ...change } } }
}

export const setText = (doc: MindDoc, id: string, text: string) =>
  update(doc, id, { text: text.replace(/\s+/g, " ").trim().slice(0, TEXT_MAX) })
export const setNote = (doc: MindDoc, id: string, note: string) =>
  update(doc, id, { note: note.trim() ? note : null })
export const setLink = (doc: MindDoc, id: string, link: MindNode["link"]) => update(doc, id, { link })
export const toggleCollapsed = (doc: MindDoc, id: string) =>
  update(doc, id, { collapsed: !doc.nodes[id].collapsed })

export function setColor(doc: MindDoc, id: string, color: NodeColor | null, branch: boolean): MindDoc {
  const nodes = { ...doc.nodes }
  for (const target of branch ? subtreeIds(doc, id) : [id]) nodes[target] = { ...nodes[target], color }
  return { ...doc, nodes }
}

function overlaps(doc: MindDoc, sizeOf: SizeOf, x: number, y: number, size: Size, skip: Set<string>): boolean {
  return visibleIds(doc).some((other) => {
    if (skip.has(other)) return false
    const n = doc.nodes[other]
    const s = sizeOf(other)
    return x < n.x + s.w + 8 && x + size.w + 8 > n.x && y < n.y + s.h + 4 && y + size.h + 4 > n.y
  })
}

// 从 (x, y) 往下找一个不和别的节点重叠的位置
function freeSpot(doc: MindDoc, sizeOf: SizeOf, x: number, y: number, size: Size, skip: Set<string>) {
  let spot = y
  for (let i = 0; i < 200 && overlaps(doc, sizeOf, x, spot, size, skip); i++) spot += size.h / 2 + GAP_Y
  return spot
}

// 加一个子节点，排在最后；父节点收起着就先展开
export function addChild(doc: MindDoc, parentId: string, sizeOf: SizeOf, text = ""): { doc: MindDoc; id: string } {
  const parent = doc.nodes[parentId]
  const id = newNodeId()
  const node = blankNode(text)
  const size = estimateSize(node)
  const ps = sizeOf(parentId)
  const last = parent.collapsed ? undefined : parent.children.at(-1)
  node.x = last ? doc.nodes[last].x : parent.x + ps.w + GAP_X
  const wanted = last ? doc.nodes[last].y + sizeOf(last).h + GAP_Y : parent.y + (ps.h - size.h) / 2
  const opened = { ...doc, nodes: { ...doc.nodes, [parentId]: { ...parent, collapsed: false } } }
  node.y = freeSpot(opened, sizeOf, node.x, wanted, size, new Set())
  // 子节点继承父节点的颜色（根节点有自己的样式，不往下传）
  node.color = parentId === doc.root ? null : parent.color
  return {
    id,
    doc: {
      ...doc,
      nodes: {
        ...doc.nodes,
        [parentId]: { ...parent, collapsed: false, children: [...parent.children, id] },
        [id]: node,
      },
    },
  }
}

// 在 id 后面加一个同级节点；根节点没有同级，加成子节点
export function addSibling(doc: MindDoc, id: string, sizeOf: SizeOf): { doc: MindDoc; id: string } {
  const parentId = parentOf(doc, id)
  if (!parentId) return addChild(doc, id, sizeOf)
  const parent = doc.nodes[parentId]
  const current = doc.nodes[id]
  const newId = newNodeId()
  const node = blankNode()
  const size = estimateSize(node)
  node.x = current.x
  node.y = freeSpot(doc, sizeOf, node.x, current.y + sizeOf(id).h + GAP_Y, size, new Set())
  node.color = current.color
  const children = [...parent.children]
  children.splice(children.indexOf(id) + 1, 0, newId)
  return {
    id: newId,
    doc: { ...doc, nodes: { ...doc.nodes, [parentId]: { ...parent, children }, [newId]: node } },
  }
}

// 删掉节点和它下面的整个分支；根节点删不掉。返回删完之后该选中哪个
export function removeNode(doc: MindDoc, id: string): { doc: MindDoc; select: string } {
  const parentId = parentOf(doc, id)
  if (!parentId) return { doc, select: id }
  const parent = doc.nodes[parentId]
  const index = parent.children.indexOf(id)
  const nodes = { ...doc.nodes }
  for (const gone of subtreeIds(doc, id)) delete nodes[gone]
  const children = parent.children.filter((c) => c !== id)
  nodes[parentId] = { ...parent, children }
  return { doc: { ...doc, nodes }, select: children[Math.min(index, children.length - 1)] ?? parentId }
}

// 整个分支平移
export function moveBy(doc: MindDoc, id: string, dx: number, dy: number): MindDoc {
  const nodes = { ...doc.nodes }
  for (const target of subtreeIds(doc, id)) {
    nodes[target] = { ...nodes[target], x: nodes[target].x + dx, y: nodes[target].y + dy }
  }
  return { ...doc, nodes }
}

// 能不能把 id 挂到 target 下面：不能挂到自己或自己的后代下面
export function canReparent(doc: MindDoc, id: string, target: string): boolean {
  return id !== doc.root && !subtreeIds(doc, id).includes(target)
}

// 把 id 换到 target 下面，排在最后，整个分支挪到 target 旁边
export function reparent(doc: MindDoc, id: string, target: string, sizeOf: SizeOf): MindDoc {
  const from = parentOf(doc, id)
  if (!from || !canReparent(doc, id, target) || from === target) return doc
  let next: MindDoc = {
    ...doc,
    nodes: {
      ...doc.nodes,
      [from]: { ...doc.nodes[from], children: doc.nodes[from].children.filter((c) => c !== id) },
      [target]: { ...doc.nodes[target], collapsed: false, children: [...doc.nodes[target].children, id] },
    },
  }
  const parent = next.nodes[target]
  const siblings = parent.children.filter((c) => c !== id)
  const last = siblings.at(-1)
  const x = last ? next.nodes[last].x : parent.x + sizeOf(target).w + GAP_X
  const wanted = last ? next.nodes[last].y + sizeOf(last).h + GAP_Y : parent.y
  const y = freeSpot(next, sizeOf, x, wanted, sizeOf(id), new Set(subtreeIds(next, id)))
  next = moveBy(next, id, x - next.nodes[id].x, y - next.nodes[id].y)
  return next
}

// 同一个父节点下的顺序跟着上下位置走：拖动之后「自动排列」不会把顺序打乱
export function sortChildrenByY(doc: MindDoc, parentId: string): MindDoc {
  const parent = doc.nodes[parentId]
  const children = [...parent.children].sort((a, b) => doc.nodes[a].y - doc.nodes[b].y)
  if (children.every((c, i) => c === parent.children[i])) return doc
  return update(doc, parentId, { children })
}

// 自动排列：根在左，往右一层一层展开，每个分支占自己那一段高度。收起的分支只排它自己
export function autoLayout(doc: MindDoc, sizeOf: SizeOf): MindDoc {
  const nodes = { ...doc.nodes }
  const heights = new Map<string, number>()
  const heightOf = (id: string): number => {
    const node = doc.nodes[id]
    const own = sizeOf(id).h
    let total = own
    if (!node.collapsed && node.children.length > 0) {
      const kids = node.children.reduce((sum, c) => sum + heightOf(c), 0) + GAP_Y * (node.children.length - 1)
      total = Math.max(own, kids)
    }
    heights.set(id, total)
    return total
  }
  const place = (id: string, x: number, top: number) => {
    const node = doc.nodes[id]
    const size = sizeOf(id)
    const band = heights.get(id)!
    nodes[id] = { ...nodes[id], x, y: top + (band - size.h) / 2 }
    if (node.collapsed) return
    const kidsHeight = node.children.reduce((sum, c) => sum + heights.get(c)!, 0) + GAP_Y * Math.max(0, node.children.length - 1)
    let y = top + (band - kidsHeight) / 2
    for (const child of node.children) {
      place(child, x + size.w + GAP_X, y)
      y += heights.get(child)! + GAP_Y
    }
  }
  const total = heightOf(doc.root)
  const root = doc.nodes[doc.root]
  place(doc.root, root.x, root.y + sizeOf(doc.root).h / 2 - total / 2)
  return { ...doc, nodes }
}

export function bounds(doc: MindDoc, sizeOf: SizeOf) {
  const ids = visibleIds(doc)
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const id of ids) {
    const n = doc.nodes[id]
    const s = sizeOf(id)
    minX = Math.min(minX, n.x)
    minY = Math.min(minY, n.y)
    maxX = Math.max(maxX, n.x + s.w)
    maxY = Math.max(maxY, n.y + s.h)
  }
  return { minX, minY, maxX, maxY }
}

// 「从分类生成初稿」：目录导图那棵树（分类 → 小块 → 主题笔记 → 概念笔记）换成可编辑的导图，笔记节点带链接
export function docFromMapTree(title: string, tree: MapNode): MindDoc {
  const nodes: Record<string, MindNode> = {}
  const convert = (node: MapNode, depth: number): string => {
    const id = depth === 0 ? "root" : newNodeId()
    const own = blankNode(node.label.slice(0, TEXT_MAX))
    own.link = node.note?.id ? { id: node.note.id, title: node.note.title } : null
    // 太深、太多的分支先收起，打开时不至于铺满一屏
    own.collapsed = depth >= 2 && node.children.length > 0
    nodes[id] = own
    own.children = node.children.map((child) => convert(child, depth + 1))
    return id
  }
  const root = convert(tree, 0)
  const doc = { title, root, nodes }
  const sizes = (id: string) => estimateSize(doc.nodes[id], id === doc.root)
  return autoLayout(doc, sizes)
}
