import { useEffect, useState } from "react"
import { FolderOpenIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { ModelSettings } from "@/components/ModelSettings"
import { Button } from "@/components/ui/button"
import { api, type Locations } from "@/lib/api"
import { desktop } from "@/lib/desktop"

// onQueued：批量补充排进了录入队列，让队列状态刷新一下
export function SettingsPanel({ onQueued }: { onQueued?: () => void }) {
  const [locations, setLocations] = useState<Locations | null>(null)

  useEffect(() => {
    api.locations().then(setLocations, () => undefined)
  }, [])

  return (
    <div className="flex flex-col gap-8">
      <ModelSettings settingsPath={locations?.settings ?? null} />
      <BatchSupplement onQueued={onQueued} />
      {locations && <VaultLocation path={locations.vault} />}
      {desktop && <DesktopShortcut />}
    </div>
  )
}

// 粗估：一条十几秒，三条一起写
const SECONDS_PER_NOTE = 5

function BatchSupplement({ onQueued }: { onQueued?: () => void }) {
  const [count, setCount] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.supplementCandidates().then((r) => setCount(r.count), () => undefined)
  }, [])

  async function start(scope: "outdated" | "all") {
    if (scope === "all" && !window.confirm("给所有笔记重新写模型补充？原来的补充会整段换掉。")) return
    setBusy(true)
    setError(null)
    try {
      const r = await api.batchSupplement(scope)
      setNotice(
        r.count === 0
          ? "没有要补的笔记。"
          : `已排进录入队列：${r.count} 条笔记，分 ${r.jobs} 个任务。可以在「录入」页看进度，关掉应用下次会接着补。`,
      )
      if (scope === "outdated") setCount(0)
      onQueued?.()
    } catch (e) {
      setError((e as Error).message)
    }
    setBusy(false)
  }

  if (count === null) return null
  const minutes = Math.max(1, Math.round((count * SECONDS_PER_NOTE) / 60))
  return (
    <section className="flex flex-col gap-2" aria-labelledby="batch-supplement">
      <h2 id="batch-supplement" className="font-heading text-lg">
        模型补充
      </h2>
      <p className="text-sm text-muted-foreground">
        新录入的笔记会自动补上。
        {count > 0
          ? `已有的笔记里有 ${count} 条还没有补充，或者只有以前那种几句话的短补充，全部补上大约要 ${minutes} 分钟。`
          : "已有的笔记都补过了。"}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        {count > 0 && (
          <Button type="button" disabled={busy} onClick={() => void start("outdated")}>
            给这 {count} 条补上
          </Button>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={() => void start("all")}
          className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          全部重写
        </button>
      </div>
      {notice && (
        <p role="status" className="text-sm text-muted-foreground">
          {notice}
        </p>
      )}
      {error && <Alert tone="error">{error}</Alert>}
    </section>
  )
}

type ShortcutStatus = Awaited<ReturnType<NonNullable<typeof desktop>["shortcutStatus"]>>

const SHORTCUT_TEXT: Record<ShortcutStatus, string> = {
  ok: "桌面上已有「知识库」快捷方式。",
  stale: "桌面上的「知识库」快捷方式指向别的位置（可能挪过文件夹），重新创建就会指向现在这个。",
  missing: "桌面上还没有「知识库」快捷方式。",
}

function DesktopShortcut() {
  const [status, setStatus] = useState<ShortcutStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    desktop?.shortcutStatus().then(setStatus, () => undefined)
  }, [])

  async function create() {
    if (!desktop) return
    setBusy(true)
    setError(null)
    try {
      const result = await desktop.createShortcut()
      if (!result.ok) setError(`没能创建快捷方式：${result.error ?? "未知原因"}`)
      setStatus(await desktop.shortcutStatus())
    } catch (e) {
      setError((e as Error).message)
    }
    setBusy(false)
  }

  if (!status) return null
  return (
    <section className="flex flex-col gap-2" aria-labelledby="desktop-shortcut">
      <h2 id="desktop-shortcut" className="font-heading text-lg">
        桌面快捷方式
      </h2>
      <div className="flex flex-wrap items-center gap-3">
        <p role="status" className="min-w-0 flex-1 text-sm text-muted-foreground">
          {SHORTCUT_TEXT[status]}
        </p>
        <Button type="button" variant="outline" disabled={busy} onClick={create}>
          {busy ? "创建中…" : status === "missing" ? "创建桌面快捷方式" : "重新创建"}
        </Button>
      </div>
      {error && <Alert tone="error">{error}</Alert>}
    </section>
  )
}

function VaultLocation({ path }: { path: string }) {
  const [busy, setBusy] = useState(false)
  async function change() {
    if (!desktop) return
    setBusy(true)
    // 换成功时应用会重新打开，这里就不用再管了
    await desktop.chooseVault().catch(() => false)
    setBusy(false)
  }
  return (
    <section className="flex flex-col gap-2" aria-labelledby="vault-location">
      <h2 id="vault-location" className="font-heading text-lg">
        知识库位置
      </h2>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 rounded-lg border bg-muted/50 px-3 py-2 font-mono text-xs break-all">
          {path}
        </code>
        {desktop && (
          <>
            <Button type="button" variant="outline" onClick={() => desktop?.openVault()}>
              <FolderOpenIcon />
              打开文件夹
            </Button>
            <Button type="button" variant="outline" disabled={busy} onClick={change}>
              {busy ? "选择中…" : "更换…"}
            </Button>
          </>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {desktop
          ? "笔记都是这个文件夹里的 Markdown 文件。更换后应用会重新打开，原来的知识库不会被改动。"
          : "浏览器开发模式下默认是 note-agent 旁边的 vault 文件夹，可以用环境变量 VAULT_PATH 改。"}
      </p>
    </section>
  )
}
