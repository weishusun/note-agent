// 笔记正文 Markdown ⇄ 所见即所得编辑器里的 HTML。
// 笔记存盘仍然是 Markdown（SCHEMA 2.3）：[[双链]]、![[assets/截图]]、表格、代码块都要原样来回。
// 只保留界面能显示的写法：没有斜体、删除线、[文字](网址) 链接——阅读区（NoteBody）不认它们。

import { marked } from "marked"
import TurndownService from "turndown"
import { tables } from "turndown-plugin-gfm"

const EMBED_LINE = /^!\[\[(assets\/[^\]|]+)\]\]\s*$/gm
const WIKILINK = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
}

// 代码块和行内代码里的 [[…]] 不是双链：先把它们挖出来，换完再放回去
function protectCode(md: string): { text: string; restore: (s: string) => string } {
  const saved: string[] = []
  const text = md.replace(/```[\s\S]*?```|`[^`\n]+`/g, (m) => {
    saved.push(m)
    return `${saved.length - 1}`
  })
  // 占位用 Unicode 私用区的字符，正文里不会出现
  return { text, restore: (s) => s.replace(/(\d+)/g, (_, i) => saved[Number(i)]) }
}

export function markdownToHtml(md: string): string {
  const { text, restore } = protectCode(md.replace(/\r\n/g, "\n"))
  const withNodes = text
    // div 里放上文件名：空元素会被 turndown 当成空白直接丢掉
    .replace(EMBED_LINE, (_, src: string) => `<div data-embed="${escapeHtml(src)}">${escapeHtml(src)}</div>\n`)
    .replace(WIKILINK, (_, target: string, label?: string) => {
      const shown = (label ?? target).trim()
      return `<span data-wikilink="${escapeHtml(target.trim())}" data-label="${escapeHtml(shown)}">${escapeHtml(shown)}</span>`
    })
  return marked.parse(restore(withNodes), { gfm: true, breaks: false, async: false }) as string
}

let service: TurndownService | null = null

function turndown(): TurndownService {
  if (service) return service
  const td = new TurndownService({
    headingStyle: "atx",
    codeBlockStyle: "fenced",
    bulletListMarker: "-",
    strongDelimiter: "**",
    hr: "---",
  })
  td.use(tables)
  // 不转义：阅读区不认反斜杠转义，「d_model」写成「d\_model」会原样显示出来
  td.escape = (text: string) => text
  td.addRule("wikilink", {
    filter: (node) => node.nodeName === "SPAN" && (node as HTMLElement).dataset.wikilink !== undefined,
    replacement: (_content, node) => {
      const el = node as HTMLElement
      const target = el.dataset.wikilink ?? ""
      const label = el.dataset.label ?? el.textContent ?? target
      return label && label !== target ? `[[${target}|${label}]]` : `[[${target}]]`
    },
  })
  td.addRule("embed", {
    filter: (node) => node.nodeName === "DIV" && (node as HTMLElement).dataset.embed !== undefined,
    replacement: (_content, node) => `\n\n![[${(node as HTMLElement).dataset.embed}]]\n\n`,
  })
  // 列表：「- 」后面只空一格，嵌套一层缩进两格（阅读区按两格算一层）
  td.addRule("listItem", {
    filter: "li",
    replacement: (content, node) => {
      const parent = node.parentNode as HTMLElement | null
      let marker = "- "
      if (parent?.nodeName === "OL") {
        const start = Number(parent.getAttribute("start") ?? 1)
        marker = `${start + Array.prototype.indexOf.call(parent.children, node)}. `
      }
      // 续行和嵌套列表缩进到标记后面的文字处：「1. 」下面要缩三格，少了下次打开就不算嵌套了
      const indent = " ".repeat(marker.length)
      // 编辑器里每个列表项都包着 <p>：段落前后的空行收掉，否则文字和嵌套列表之间会多出一个空行
      const body = content
        .replace(/^\n+/, "")
        .replace(/\n+$/, "\n")
        .replace(/\n{2,}/g, "\n")
        .replace(/\n(?=.)/g, `\n${indent}`)
      return marker + body.trimEnd() + (node.nextSibling ? "\n" : "")
    },
  })
  service = td
  return td
}

// 编辑器（TipTap）给的表格带 <colgroup>、单元格里包着 <p>、表头行放在 <tbody> 里：
// 这样 turndown-plugin-gfm 认不出表头，会把整张表原样留成 HTML。先收拾成它认得的样子
function tidyTables(html: string): string {
  if (!html.includes("<table")) return html
  const doc = new DOMParser().parseFromString(html, "text/html")
  for (const table of doc.querySelectorAll("table")) {
    table.querySelectorAll("colgroup").forEach((el) => el.remove())
    for (const cell of table.querySelectorAll("td, th")) {
      const paragraphs = [...cell.children].filter((el) => el.tagName === "P")
      // 一格里有几段时用空格连起来：表格的一行在 Markdown 里只能占一行
      if (paragraphs.length > 0 && paragraphs.length === cell.children.length) {
        cell.innerHTML = paragraphs.map((p) => p.innerHTML).join(" ")
      }
    }
    const first = table.rows[0]
    if (!table.tHead && first && [...first.cells].every((c) => c.tagName === "TH")) {
      table.createTHead().appendChild(first)
    }
  }
  return doc.body.innerHTML
}

export function htmlToMarkdown(html: string): string {
  const md = turndown()
    .turndown(tidyTables(html))
    // 和录入时写出来的笔记一个样子：标题下面紧跟内容，表格分隔线不带空格
    .replace(/^(## .+)\n\n/gm, "$1\n")
    .replace(/^\|(?: *:?-{3,}:? *\|)+$/gm, (line) => line.replace(/ /g, ""))
  return md.replace(/\n{3,}/g, "\n\n").trim() + "\n"
}

// 双链指向笔记文件名（不带 .md）；NoteTitle.path 是 notes/xxx.md
export function stemOf(path: string): string {
  return path.replace(/^.*\//, "").replace(/\.md$/, "")
}
