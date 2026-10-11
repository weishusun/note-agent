import type { ReactNode } from "react"

// 笔记正文的轻量渲染：只覆盖录入循环会生成的那几种写法（SCHEMA 2.3）——
// 标题、列表、引用块、![[截图]]、[[双链]]、**加粗**、`代码`。其余按普通段落显示。

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "quote"; lines: string[] }
  | { kind: "image"; name: string }
  | { kind: "paragraph"; lines: string[] }

const IMAGE = /^!\[\[assets\/([^\]|]+)\]\]$/
const HEADING = /^(#{1,6})\s+(.*)$/
const INLINE = /(\[\[[^\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/

function parse(body: string): Block[] {
  const blocks: Block[] = []
  for (const raw of body.split(/\r?\n/)) {
    const line = raw.trimEnd()
    const last = blocks[blocks.length - 1]
    const heading = HEADING.exec(line)
    const image = IMAGE.exec(line.trim())
    if (!line.trim()) {
      // 空行结束当前段落；用一个空段落占位，下一行非空时会另起一块
      if (last?.kind === "paragraph" && last.lines.length > 0) {
        blocks.push({ kind: "paragraph", lines: [] })
      }
    } else if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] })
    } else if (image) {
      blocks.push({ kind: "image", name: image[1] })
    } else if (/^[-*]\s+/.test(line)) {
      const item = line.replace(/^[-*]\s+/, "")
      if (last?.kind === "list") last.items.push(item)
      else blocks.push({ kind: "list", items: [item] })
    } else if (line.startsWith(">")) {
      const text = line.replace(/^>\s?/, "")
      if (last?.kind === "quote") last.lines.push(text)
      else blocks.push({ kind: "quote", lines: [text] })
    } else if (last?.kind === "paragraph") {
      last.lines.push(line)
    } else {
      blocks.push({ kind: "paragraph", lines: [line] })
    }
  }
  return blocks.filter((b) => b.kind !== "paragraph" || b.lines.length > 0)
}

export function NoteBody({
  body,
  onOpenLink,
}: {
  body: string
  onOpenLink: (target: string) => void
}) {
  const inline = (text: string): ReactNode[] =>
    text.split(INLINE).map((part, i) => {
      if (part.startsWith("[[") && part.endsWith("]]")) {
        const [target, label] = part.slice(2, -2).split("|")
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
              <ul key={i} className="flex list-disc flex-col gap-1 pl-5">
                {block.items.map((item, j) => (
                  <li key={j}>{inline(item)}</li>
                ))}
              </ul>
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
