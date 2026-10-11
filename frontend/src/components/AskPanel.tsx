import { useEffect, useRef, useState, type FormEvent } from "react"

import { Alert } from "@/components/Alert"
import { NoteCard } from "@/components/NoteCard"
import { Button } from "@/components/ui/button"
import { api, type AskResult, type NoteRef } from "@/lib/api"

const QUESTION_LIMIT = 500

interface Exchange {
  id: number
  question: string
  result?: AskResult
  error?: string
}

export function AskPanel({ onOpenNote }: { onOpenNote: (note: NoteRef) => void }) {
  const [question, setQuestion] = useState("")
  const [exchanges, setExchanges] = useState<Exchange[]>([])
  const [busy, setBusy] = useState(false)
  const nextId = useRef(1)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" })
  }, [exchanges])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    const q = question.trim()
    if (!q || busy) return
    const id = nextId.current++
    setExchanges((prev) => [...prev, { id, question: q }])
    setQuestion("")
    setBusy(true)
    const patch = await api
      .ask(q)
      .then((result) => ({ result }))
      .catch((err: Error) => ({ error: err.message }))
    setExchanges((prev) => prev.map((x) => (x.id === id ? { ...x, ...patch } : x)))
    setBusy(false)
  }

  return (
    <section className="flex flex-col gap-6">
      {exchanges.length === 0 && (
        <p className="text-sm text-muted-foreground">
          提一个问题，我会先在你的知识库里找相关笔记，再基于它们回答并标出引用。
        </p>
      )}

      {exchanges.map((x) => (
        <article key={x.id} className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold">{x.question}</h3>
          {!x.result && !x.error && <p className="text-sm text-muted-foreground">正在检索并回答…</p>}
          {x.error && <Alert tone="error">{x.error}</Alert>}
          {x.result && <Answer result={x.result} onOpenNote={onOpenNote} />}
        </article>
      ))}
      <div ref={bottom} />

      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={QUESTION_LIMIT}
          aria-label="问题"
          placeholder="例如：RAG 和微调有什么区别？"
          className="h-9 min-w-0 flex-1 rounded-lg border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        />
        <Button type="submit" size="lg" disabled={busy || !question.trim()}>
          提问
        </Button>
      </form>
    </section>
  )
}

function Answer({
  result,
  onOpenNote,
}: {
  result: AskResult
  onOpenNote: (note: NoteRef) => void
}) {
  // 把回答里的 [1]、[2] 变成可点的引用；编号对不上来源的保持原样
  const parts = result.answer.split(/(\[\d+\])/)
  return (
    <>
      {!result.grounded && (
        <Alert tone="info">知识库里没有找到相关笔记，下面的回答来自模型自身的知识，未经核实。</Alert>
      )}
      <p className="text-sm leading-relaxed whitespace-pre-wrap">
        {parts.map((part, i) => {
          const source = result.sources[Number(part.slice(1, -1)) - 1]
          if (!/^\[\d+\]$/.test(part) || !source) return part
          return (
            <button
              key={i}
              type="button"
              title={source.title}
              aria-label={`引用 ${part.slice(1, -1)}：${source.title}`}
              onClick={() => onOpenNote(source)}
              className="mx-0.5 rounded bg-muted px-1 text-xs font-medium text-foreground hover:bg-primary hover:text-primary-foreground"
            >
              {part.slice(1, -1)}
            </button>
          )
        })}
      </p>
      {result.sources.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">检索到的笔记</p>
          {result.sources.map((note, i) => (
            <NoteCard
              key={note.path}
              note={note}
              onOpen={onOpenNote}
              badge={
                <span className="mt-0.5 rounded bg-muted px-1.5 text-xs font-medium">{i + 1}</span>
              }
            />
          ))}
        </div>
      )}
    </>
  )
}
