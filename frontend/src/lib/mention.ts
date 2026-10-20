// 「@ 链接笔记」：按输入的字在全部笔记的标题、别名里筛候选。导图节点和笔记正文的编辑器共用。

import type { NoteTitle } from "@/lib/api"

// 打头匹配的排前面，其次是包含；标题排在别名前面；同一档里短的在前（更可能就是要找的那条）
export function matchTitles(titles: NoteTitle[], query: string, limit = 8): NoteTitle[] {
  const q = query.trim().toLowerCase()
  if (!q) return titles.slice(0, limit)
  const scored: { t: NoteTitle; score: number }[] = []
  for (const t of titles) {
    const title = t.title.toLowerCase()
    const aliases = t.aliases.map((a) => a.toLowerCase())
    let score = -1
    if (title.startsWith(q)) score = 0
    else if (aliases.some((a) => a.startsWith(q))) score = 1
    else if (title.includes(q)) score = 2
    else if (aliases.some((a) => a.includes(q))) score = 3
    if (score >= 0) scored.push({ t, score })
  }
  scored.sort((a, b) => a.score - b.score || a.t.title.length - b.t.title.length)
  return scored.slice(0, limit).map((s) => s.t)
}
