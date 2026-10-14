// 整个应用共用的「正在看知识库的哪一部分」：左栏目录里选中的那一项。
// 笔记、图谱、导图三个页面都按它来决定范围。

import type { Tree } from "@/lib/api"

export type Selection =
  | { kind: "all" }
  | { kind: "inbox" }
  | { kind: "duplicates" }
  | { kind: "category"; path: string }

export type Page = "notes" | "ingest" | "ask" | "graph" | "map" | "settings"

// 图谱和导图只认「全部」和某个分类；待整理、疑似重复不是分类，在那两页里当作全部
export function scopeOf(selection: Selection): string | null {
  return selection.kind === "category" ? selection.path : null
}

// 所选范围里有多少条笔记（目录树上的数字）
export function countOf(tree: Tree | null, selection: Selection): number {
  if (!tree) return 0
  if (selection.kind === "all") return tree.total
  if (selection.kind === "inbox") return tree.inbox
  if (selection.kind === "duplicates") return tree.duplicates
  for (const parent of tree.categories) {
    if (parent.name === selection.path) return parent.count
    const child = parent.children.find((c) => c.path === selection.path)
    if (child) return child.count
  }
  return 0
}

export function findCategory(
  tree: Tree | null,
  path: string,
): { locked: boolean; count: number } | null {
  for (const parent of tree?.categories ?? []) {
    if (parent.name === path) return parent
    const child = parent.children.find((c) => c.path === path)
    if (child) return child
  }
  return null
}

// 大块的颜色：按名字算，分类多了少了、排序变了，同一个大块的颜色都不变
const PALETTE = ["#3d5a8a", "#3f7f74", "#7a5f97", "#a0664f", "#5b7f9e", "#8a7a4a", "#9a5b78", "#4f7d4f"]

export function categoryColor(name: string): string {
  let hash = 0
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  return PALETTE[hash % PALETTE.length]
}
