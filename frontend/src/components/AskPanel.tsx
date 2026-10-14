import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react"
import { MessageSquarePlusIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { NoteCard } from "@/components/NoteCard"
import { Button } from "@/components/ui/button"
import { api, type AskResult, type NoteRef } from "@/lib/api"

const QUESTION_LIMIT = 500
// 当前这段对话存在浏览器里，刷新、重开都还在；点「新对话」才清掉
const STORAGE_KEY = "note-agent.conversation.v1"

interface Exchange {
  id: number
  question: string
  result?: AskResult
  error?: string
}

function loadConversation(): Exchange[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]")
    if (!Array.isArray(saved)) return []
    return saved
      .filter(
        (x): x is { question: string; result: AskResult } =>
          typeof x?.question === "string" &&
          typeof x?.result?.answer === "string" &&
          Array.isArray(x.result.sources),
      )
      .map((x, i) => ({ id: i + 1, question: x.question, result: x.result }))
  } catch {
    return []
  }
}

export function AskPanel({
  onOpenNote,
  request,
}: {
  onOpenNote: (note: NoteRef) => void
  // 从 Ctrl K 面板来的问题：id 变了就问一次
  request?: { id: number; question: string } | null
}) {
  const [question, setQuestion] = useState("")
  const [exchanges, setExchanges] = useState<Exchange[]>(loadConversation)
  const [busy, setBusy] = useState(false)
  const nextId = useRef(exchanges.length + 1)
  const bottom = useRef<HTMLDivElement>(null)
  // 「新对话」之后，还在路上的那次回答回来时不该再冒出来
  const generation = useRef(0)

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" })
    try {
      // 只存问完了的；失败的、还在等的不存
      const done = exchanges.filter((x) => x.result)
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify(done.map(({ question, result }) => ({ question, result }))),
      )
    } catch {
      // 存不下（隐私模式、空间满了）只是刷新后会丢，不影响这次对话
    }
  }, [exchanges])

  async function send(text = question) {
    const q = text.trim()
    if (!q || busy) return
    const id = nextId.current++
    const mine = generation.current
    // 之前问完了的几轮一起带上，后端据此把这次的问题当追问来理解
    const history = exchanges.flatMap((x) =>
      x.result ? [{ question: x.question, answer: x.result.answer }] : [],
    )
    setExchanges((prev) => [...prev, { id, question: q }])
    if (text === question) setQuestion("")
    setBusy(true)
    const patch = await api
      .ask(q, history)
      .then((result) => ({ result }))
      .catch((err: Error) => ({ error: err.message }))
    if (mine === generation.current) {
      setExchanges((prev) => prev.map((x) => (x.id === id ? { ...x, ...patch } : x)))
    }
    setBusy(false)
  }

  const lastRequest = useRef(0)
  useEffect(() => {
    if (!request || request.id === lastRequest.current) return
    lastRequest.current = request.id
    void send(request.question)
    // 只在来了新问题时触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request])

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    void send()
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // 回车发送，Shift+回车换行；输入法正在选字时的回车不算
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void send()
    }
  }

  function startOver() {
    generation.current++
    setExchanges([])
    setBusy(false)
  }

  const asked = exchanges.length > 0

  return (
    <section className="flex flex-col gap-6">
      {asked ? (
        <div className="flex items-center gap-2">
          <p className="flex-1 text-xs text-muted-foreground">
            这是一段连续的对话，可以接着上面的回答追问。
          </p>
          <Button
            variant="outline"
            size="sm"
            title="清空这段对话，从头开始问"
            onClick={startOver}
          >
            <MessageSquarePlusIcon />
            新对话
          </Button>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          提一个问题，我会先在你的知识库里找相关笔记，再基于它们回答并标出引用。答完可以接着追问。
        </p>
      )}

      {exchanges.map((x, i) => (
        <article key={x.id} className="flex flex-col gap-3">
          <h3 className="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-muted px-3.5 py-2 text-sm whitespace-pre-wrap">
            {x.question}
          </h3>
          {!x.result && !x.error && (
            <p className="text-sm text-muted-foreground">正在检索并回答…</p>
          )}
          {x.error && <Alert tone="error">{x.error}</Alert>}
          {x.result && (
            <Answer
              result={x.result}
              // 只有最新的回答默认展开它检索到的笔记，免得对话一长满屏都是卡片
              open={i === exchanges.length - 1}
              onOpenNote={onOpenNote}
            />
          )}
        </article>
      ))}
      <div ref={bottom} />

      <form onSubmit={onSubmit} className="flex items-end gap-2">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKeyDown}
          maxLength={QUESTION_LIMIT}
          rows={Math.min(5, Math.max(1, question.split("\n").length))}
          aria-label="问题"
          placeholder={asked ? "接着问，比如：它和上面说的那个有什么区别？" : "例如：RAG 和微调有什么区别？"}
          className="min-h-9 min-w-0 flex-1 resize-none rounded-lg border bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        />
        <Button type="submit" size="lg" disabled={busy || !question.trim()}>
          {asked ? "发送" : "提问"}
        </Button>
      </form>
    </section>
  )
}

function Answer({
  result,
  open,
  onOpenNote,
}: {
  result: AskResult
  open: boolean
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
      {(result.rewritten || result.keywords.length > 0) && (
        <p className="text-xs text-muted-foreground">
          {result.rewritten && <>结合上文，按「{result.rewritten}」去检索。</>}
          {result.keywords.length > 0 && <>检索词：{result.keywords.join(" · ")}</>}
        </p>
      )}
      {result.sources.length > 0 && (
        <details open={open} className="group flex flex-col">
          <summary className="cursor-pointer text-xs text-muted-foreground select-none hover:text-foreground">
            检索到的 {result.sources.length} 条笔记
          </summary>
          <div className="mt-2 flex flex-col gap-2">
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
        </details>
      )}
    </>
  )
}
