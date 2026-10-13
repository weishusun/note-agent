import { useCallback, useEffect, useRef, useState } from "react"

import { api, type IngestJob } from "@/lib/api"

const POLL_INTERVAL_MS = 1000

const unfinished = (job: IngestJob) => job.status === "pending" || job.status === "running"

// 录入队列的状态。队列里还有没处理完的任务时每秒刷新一次，否则不轮询。
// version：笔记在别处被改动时递增——任务里列的笔记是现查的，要跟着刷新。
// onFinished：某个任务从「排队 / 处理中」变成「完成 / 失败」时调用一次。
export function useIngestJobs(version: number, onFinished: (job: IngestJob) => void) {
  const [jobs, setJobs] = useState<IngestJob[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const refresh = useCallback(() => setTick((t) => t + 1), [])
  const seen = useRef(new Map<string, IngestJob["status"]>())
  const notify = useRef(onFinished)
  notify.current = onFinished

  useEffect(() => {
    let cancelled = false
    api.jobs().then(
      (list) => {
        if (cancelled) return
        for (const job of list) {
          const before = seen.current.get(job.id)
          if (before && before !== job.status && !unfinished(job)) notify.current(job)
        }
        seen.current = new Map(list.map((job) => [job.id, job.status]))
        setJobs(list)
        setError(null)
      },
      (e: Error) => !cancelled && setError(e.message),
    )
    return () => {
      cancelled = true
    }
  }, [tick, version])

  const active = jobs?.filter(unfinished).length ?? 0

  useEffect(() => {
    if (active === 0) return
    const timer = setInterval(refresh, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [active, refresh])

  return { jobs, error, active, refresh }
}
