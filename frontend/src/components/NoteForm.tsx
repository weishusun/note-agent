import { useId, useState, type FormEvent, type ReactNode } from "react"

import { Alert } from "@/components/Alert"
import { Button } from "@/components/ui/button"

export interface NoteFormValues {
  title: string
  summary: string
  aliases: string[]
  body: string
}

const INPUT =
  "w-full rounded-lg border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
const TITLE_MAX = 60
const SUMMARY_MAX = 80

// 别名在一个输入框里用顿号或逗号分隔
function splitAliases(text: string): string[] {
  return text
    .split(/[、,，]/)
    .map((alias) => alias.trim())
    .filter(Boolean)
}

// 编辑已有笔记和手写新建共用的表单。extra 是放在正文上方的附加字段（新建时用来选分类）
export function NoteForm({
  initial,
  submitLabel,
  busy,
  error,
  extra,
  onSubmit,
  onCancel,
}: {
  initial: NoteFormValues
  submitLabel: string
  busy: boolean
  error: string | null
  extra?: ReactNode
  onSubmit: (values: NoteFormValues) => void
  onCancel: () => void
}) {
  const [title, setTitle] = useState(initial.title)
  const [summary, setSummary] = useState(initial.summary)
  const [aliases, setAliases] = useState(initial.aliases.join("、"))
  const [body, setBody] = useState(initial.body)
  const ids = { title: useId(), summary: useId(), aliases: useId(), body: useId() }

  const ready = title.trim() !== "" && summary.trim() !== ""

  function submit(e: FormEvent) {
    e.preventDefault()
    if (!ready) return
    onSubmit({ title: title.trim(), summary: summary.trim(), aliases: splitAliases(aliases), body })
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Field id={ids.title} label="标题">
        <input
          id={ids.title}
          className={INPUT + " h-9"}
          value={title}
          maxLength={TITLE_MAX}
          autoFocus
          onChange={(e) => setTitle(e.target.value)}
        />
      </Field>
      <Field id={ids.summary} label="一句话概要" hint={`${summary.trim().length} / ${SUMMARY_MAX}`}>
        <input
          id={ids.summary}
          className={INPUT + " h-9"}
          value={summary}
          maxLength={SUMMARY_MAX}
          onChange={(e) => setSummary(e.target.value)}
        />
      </Field>
      <Field id={ids.aliases} label="别名" hint="用顿号或逗号分隔。搜索和提问时会按别名匹配。">
        <input
          id={ids.aliases}
          className={INPUT + " h-9"}
          value={aliases}
          placeholder="如：监督微调、Supervised Fine-Tuning"
          onChange={(e) => setAliases(e.target.value)}
        />
      </Field>
      {extra}
      <Field id={ids.body} label="正文" hint="Markdown。用 [[笔记标题]] 链接到别的笔记。">
        <textarea
          id={ids.body}
          className={INPUT + " resize-y py-2 font-mono leading-relaxed"}
          rows={14}
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
      </Field>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="flex gap-2">
        <Button type="submit" size="lg" disabled={!ready || busy}>
          {busy ? "保存中…" : submitLabel}
        </Button>
        <Button type="button" size="lg" variant="ghost" disabled={busy} onClick={onCancel}>
          取消
        </Button>
      </div>
    </form>
  )
}

function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  )
}
