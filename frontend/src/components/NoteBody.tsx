import type { ReactNode } from "react"

// 笔记正文的轻量渲染：覆盖录入时会生成的写法（SCHEMA 2.3）——
// 标题、有序/无序/嵌套列表、表格、引用块、![[截图]]、![[原文件]]、[[双链]]、**加粗**、`代码`。其余按普通段落显示。

interface ListItem {
  // 缩进层级：每两个空格算一层
  indent: number
  // 有序列表项保留原来的序号，不重新编号——中间夹着嵌套的无序项时序号也不会断
  marker: string
  text: string
}

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "list"; items: ListItem[] }
  | { kind: "table"; header: string[] | null; rows: string[][] }
  | { kind: "quote"; lines: string[] }
  | { kind: "image"; name: string }
  | { kind: "paragraph"; lines: string[] }

// 单独成行、且是图片的素材才显示成图；别的素材（PDF、文本）显示成能点开的链接
const IMAGE = /^!\[\[assets\/([^\]|]+\.(?:png|jpe?g|gif|webp))\]\]$/i
const ASSET = /^!\[\[assets\/([^\]|]+)\]\]$/
const HEADING = /^(#{1,6})\s+(.*)$/
const LIST_ITEM = /^(\s*)([-*]|\d+[.)])\s+(.*)$/
const TABLE_DIVIDER = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/
const INLINE = /(!?\[\[[^\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim())
}

function parse(body: string): Block[] {
  const blocks: Block[] = []
  const lines = body.split(/\r?\n/)
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trimEnd()
    const trimmed = line.trim()
    const last = blocks[blocks.length - 1]
    const heading = HEADING.exec(line)
    const image = IMAGE.exec(trimmed)
    const item = LIST_ITEM.exec(line)

    if (!trimmed) {
      // 空行结束当前段落；用一个空段落占位，下一行非空时会另起一块
      if (last?.kind === "paragraph" && last.lines.length > 0) {
        blocks.push({ kind: "paragraph", lines: [] })
      }
    } else if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] })
    } else if (image) {
      blocks.push({ kind: "image", name: image[1] })
    } else if (trimmed.startsWith("|")) {
      // 连续的以 | 开头的行是一张表；第二行是分隔线时第一行是表头
      const tableLines = [trimmed]
      while (i + 1 < lines.length && lines[i + 1].trim().startsWith("|")) {
        tableLines.push(lines[++i].trim())
      }
      const hasHeader = tableLines.length > 1 && TABLE_DIVIDER.test(tableLines[1])
      blocks.push({
        kind: "table",
        header: hasHeader ? cells(tableLines[0]) : null,
        rows: tableLines.slice(hasHeader ? 2 : 0).map(cells),
      })
    } else if (item) {
      const entry: ListItem = {
        indent: Math.floor(item[1].replace(/\t/g, "  ").length / 2),
        marker: /^\d/.test(item[2]) ? item[2].replace(")", ".") : "•",
        text: item[3],
      }
      if (last?.kind === "list") last.items.push(entry)
      else blocks.push({ kind: "list", items: [entry] })
    } else if (line.startsWith(">")) {
      const text = line.replace(/^>\s?/, "")
      if (last?.kind === "quote") last.lines.push(text)
      else blocks.push({ kind: "quote", lines: [text] })
    } else if (last?.kind === "paragraph") {
      last.lines.push(trimmed)
    } else {
      blocks.push({ kind: "paragraph", lines: [trimmed] })
    }
  }
  return blocks.filter((b) => b.kind !== "paragraph" || b.lines.length > 0)
}

export function NoteBody({
  body,
  onOpenLink,
}: {
  body: string
  // 不给时双链只显示成文字、点不动（并排对比两条笔记时用）
  onOpenLink?: (target: string) => void
}) {
  const inline = (text: string): ReactNode[] =>
    text.split(INLINE).map((part, i) => {
      const asset = ASSET.exec(part)
      if (asset) {
        return (
          <a
            key={i}
            href={`/api/assets/${encodeURIComponent(asset[1])}`}
            target="_blank"
            rel="noreferrer"
            className="text-primary underline decoration-muted-foreground/50 underline-offset-4 hover:decoration-primary"
          >
            {asset[1]}
          </a>
        )
      }
      if (part.startsWith("[[") && part.endsWith("]]")) {
        const [target, label] = part.slice(2, -2).split("|")
        if (!onOpenLink) {
          return (
            <span key={i} className="underline decoration-muted-foreground/50 underline-offset-4">
              {(label ?? target).trim()}
            </span>
          )
        }
        return (
          <button
            key={i}
            type="button"
            onClick={() => onOpenLink(target.trim())}
            className="rounded text-primary underline decoration-muted-foreground/50 underline-offset-4 hover:decoration-primary"
          >
            {(label ?? target).trim()}
          </button>
        )
      }
      if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
        return <strong key={i}>{part.slice(2, -2)}</strong>
      }
      if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
        return (
          <code key={i} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
            {part.slice(1, -1)}
          </code>
        )
      }
      return part
    })

  return (
    <div className="flex flex-col gap-3 text-sm leading-relaxed">
      {parse(body).map((block, i) => {
        switch (block.kind) {
          case "heading":
            return (
              <h3
                key={i}
                className={
                  block.level <= 2
                    ? "mt-2 text-xs font-semibold tracking-wide text-muted-foreground first:mt-0"
                    : "font-semibold"
                }
              >
                {inline(block.text)}
              </h3>
            )
          case "list":
            return (
              <ul key={i} className="flex flex-col gap-1">
                {block.items.map((item, j) => (
                  <li
                    key={j}
                    className="flex gap-2"
                    style={{ paddingLeft: `${item.indent * 1.25}rem` }}
                  >
                    <span className="w-5 shrink-0 text-right text-muted-foreground tabular-nums">
                      {item.marker}
                    </span>
                    <span className="min-w-0 flex-1">{inline(item.text)}</span>
                  </li>
                ))}
              </ul>
            )
          case "table":
            return (
              <div key={i} className="overflow-x-auto rounded-lg border">
                <table className="w-full border-collapse text-left text-sm">
                  {block.header && (
                    <thead className="bg-muted">
                      <tr>
                        {block.header.map((cell, j) => (
                          <th key={j} className="border-b px-3 py-2 font-medium whitespace-nowrap">
                            {inline(cell)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                  )}
                  <tbody>
                    {block.rows.map((row, j) => (
                      <tr key={j} className="border-b last:border-b-0">
                        {row.map((cell, k) => (
                          <td key={k} className="px-3 py-2 align-top">
                            {inline(cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          case "quote":
            return (
              <blockquote
                key={i}
                className="border-l-2 pl-3 whitespace-pre-wrap text-muted-foreground"
              >
                {block.lines.map((line, j) => (
                  <span key={j} className="block min-h-[1em]">
                    {inline(line)}
                  </span>
                ))}
              </blockquote>
            )
          case "image":
            return (
              <img
                key={i}
                src={`/api/assets/${encodeURIComponent(block.name)}`}
                alt="录入时的原始截图"
                className="max-w-full rounded-md border"
              />
            )
          case "paragraph":
            return <p key={i}>{inline(block.lines.join("\n"))}</p>
        }
      })}
    </div>
  )
}
