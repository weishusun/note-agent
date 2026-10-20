import { useEffect, useRef, useState, type ReactNode } from "react"
import { Extension, type Editor } from "@tiptap/core"
import { EditorContent, useEditor } from "@tiptap/react"
import { Placeholder } from "@tiptap/extensions"
import Suggestion, { type SuggestionKeyDownProps, type SuggestionProps } from "@tiptap/suggestion"
import {
  AtSignIcon,
  BoldIcon,
  CodeIcon,
  Heading2Icon,
  Heading3Icon,
  ListIcon,
  ListOrderedIcon,
  PilcrowIcon,
  QuoteIcon,
  Redo2Icon,
  SquareCodeIcon,
  TableIcon,
  Undo2Icon,
} from "lucide-react"

import { api, type NoteTitle } from "@/lib/api"
import { noteExtensions } from "@/lib/editorNodes"
import { htmlToMarkdown, markdownToHtml, stemOf } from "@/lib/markdown"
import { matchTitles } from "@/lib/mention"
import { cn } from "@/lib/utils"

// 笔记正文的所见即所得编辑器。进出都是 Markdown（lib/markdown.ts），只提供阅读区能显示的格式

interface MentionState {
  items: NoteTitle[]
  index: number
  rect: DOMRect | null
  command: (item: NoteTitle) => void
}

export function RichEditor({
  id,
  value,
  onChange,
  placeholder = "写点什么……输入 @ 链接到别的笔记",
}: {
  id?: string
  value: string
  onChange: (markdown: string) => void
  placeholder?: string
}) {
  const titles = useRef<NoteTitle[]>([])
  const [mention, setMention] = useState<MentionState | null>(null)
  const mentionRef = useRef<MentionState | null>(null)
  mentionRef.current = mention
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange

  // @ 的候选：每次打开编辑器现拿一次（一两千条标题也就几十 KB），笔记新建、改名都能跟上
  useEffect(() => {
    api.titles().then((list) => (titles.current = list), () => undefined)
  }, [])

  const editor = useEditor({
    extensions: [
      ...noteExtensions(),
      Placeholder.configure({ placeholder }),
      Extension.create({
        name: "noteMention",
        addProseMirrorPlugins() {
          return [
            Suggestion<NoteTitle>({
              editor: this.editor,
              char: "@",
              items: ({ query }) => matchTitles(titles.current, query),
              command: ({ editor, range, props }) => {
                editor
                  .chain()
                  .focus()
                  .insertContentAt(range, [
                    { type: "wikilink", attrs: { target: stemOf(props.path), label: props.title } },
                    { type: "text", text: " " },
                  ])
                  .run()
              },
              render: () => ({
                onStart: (props: SuggestionProps<NoteTitle>) =>
                  setMention({ items: props.items, index: 0, rect: props.clientRect?.() ?? null, command: props.command }),
                onUpdate: (props: SuggestionProps<NoteTitle>) =>
                  setMention({ items: props.items, index: 0, rect: props.clientRect?.() ?? null, command: props.command }),
                onKeyDown: ({ event }: SuggestionKeyDownProps) => {
                  const m = mentionRef.current
                  if (!m || m.items.length === 0) return false
                  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                    const step = event.key === "ArrowDown" ? 1 : m.items.length - 1
                    setMention({ ...m, index: (m.index + step) % m.items.length })
                    return true
                  }
                  if (event.key === "Enter" || event.key === "Tab") {
                    m.command(m.items[m.index])
                    return true
                  }
                  if (event.key === "Escape") {
                    setMention(null)
                    return true
                  }
                  return false
                },
                onExit: () => setMention(null),
              }),
            }),
          ]
        },
      }),
    ],
    content: markdownToHtml(value),
    shouldRerenderOnTransaction: true,
    editorProps: { attributes: { class: "rich-editor", ...(id ? { id } : {}) } },
    onUpdate: ({ editor }) => onChangeRef.current(htmlToMarkdown(editor.getHTML())),
  })

  if (!editor) return null

  return (
    <div className="rounded-lg border bg-background focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
      <Toolbar editor={editor} />
      <EditorContent editor={editor} />
      {mention && mention.rect && (
        <ul
          role="listbox"
          className="fixed z-50 max-h-72 w-72 overflow-y-auto rounded-lg border bg-popover py-1 text-sm text-popover-foreground shadow-lg"
          style={{ left: mention.rect.left, top: mention.rect.bottom + 6 }}
        >
          {mention.items.length === 0 && <li className="px-3 py-1.5 text-muted-foreground">没有对得上的笔记</li>}
          {mention.items.map((t, i) => (
            <li
              key={t.id}
              role="option"
              aria-selected={i === mention.index}
              onMouseDown={(e) => {
                e.preventDefault()
                mention.command(t)
              }}
              className={cn("cursor-pointer truncate px-3 py-1.5", i === mention.index && "bg-accent text-accent-foreground")}
            >
              {t.title}
              <span className="ml-2 text-xs text-muted-foreground">{t.type === "topic" ? "主题" : "概念"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Toolbar({ editor }: { editor: Editor }) {
  const chain = () => editor.chain().focus()
  const inTable = editor.isActive("table")
  return (
    <div className="flex flex-wrap items-center gap-0.5 border-b px-1.5 py-1">
      <Btn label="正文" active={editor.isActive("paragraph")} onClick={() => chain().setParagraph().run()}>
        <PilcrowIcon />
      </Btn>
      <Btn label="大标题（区块）" active={editor.isActive("heading", { level: 2 })} onClick={() => chain().toggleHeading({ level: 2 }).run()}>
        <Heading2Icon />
      </Btn>
      <Btn label="小标题" active={editor.isActive("heading", { level: 3 })} onClick={() => chain().toggleHeading({ level: 3 }).run()}>
        <Heading3Icon />
      </Btn>
      <Sep />
      <Btn label="加粗（Ctrl B）" active={editor.isActive("bold")} onClick={() => chain().toggleBold().run()}>
        <BoldIcon />
      </Btn>
      <Btn label="行内代码" active={editor.isActive("code")} onClick={() => chain().toggleCode().run()}>
        <CodeIcon />
      </Btn>
      <Sep />
      <Btn label="无序列表" active={editor.isActive("bulletList")} onClick={() => chain().toggleBulletList().run()}>
        <ListIcon />
      </Btn>
      <Btn label="有序列表" active={editor.isActive("orderedList")} onClick={() => chain().toggleOrderedList().run()}>
        <ListOrderedIcon />
      </Btn>
      <Btn label="引用" active={editor.isActive("blockquote")} onClick={() => chain().toggleBlockquote().run()}>
        <QuoteIcon />
      </Btn>
      <Btn label="代码块" active={editor.isActive("codeBlock")} onClick={() => chain().toggleCodeBlock().run()}>
        <SquareCodeIcon />
      </Btn>
      <Btn label="插入表格" onClick={() => chain().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}>
        <TableIcon />
      </Btn>
      <Sep />
      <Btn label="链接笔记（也可以直接输入 @）" onClick={() => chain().insertContent(" @").run()}>
        <AtSignIcon />
      </Btn>
      <Sep />
      <Btn label="撤销（Ctrl Z）" disabled={!editor.can().undo()} onClick={() => chain().undo().run()}>
        <Undo2Icon />
      </Btn>
      <Btn label="重做（Ctrl Y）" disabled={!editor.can().redo()} onClick={() => chain().redo().run()}>
        <Redo2Icon />
      </Btn>
      {inTable && (
        <>
          <Sep />
          <TextBtn onClick={() => chain().addRowAfter().run()}>加一行</TextBtn>
          <TextBtn onClick={() => chain().addColumnAfter().run()}>加一列</TextBtn>
          <TextBtn onClick={() => chain().deleteRow().run()}>删行</TextBtn>
          <TextBtn onClick={() => chain().deleteColumn().run()}>删列</TextBtn>
          <TextBtn onClick={() => chain().deleteTable().run()}>删表格</TextBtn>
        </>
      )}
    </div>
  )
}

function Btn({
  label,
  active,
  disabled,
  onClick,
  children,
}: {
  label: string
  active?: boolean
  disabled?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={cn(
        "grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40 [&_svg]:size-4",
        active && "bg-accent text-accent-foreground",
      )}
    >
      {children}
    </button>
  )
}

function TextBtn({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className="rounded-md px-1.5 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
    >
      {children}
    </button>
  )
}

function Sep() {
  return <span className="mx-1 h-4 w-px bg-border" />
}
