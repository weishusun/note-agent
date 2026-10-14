import { useEffect, useState } from "react"
import { FolderOpenIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { ModelSettings } from "@/components/ModelSettings"
import { Button } from "@/components/ui/button"
import { api, type Locations } from "@/lib/api"
import { desktop } from "@/lib/desktop"

export function SettingsPanel() {
  const [locations, setLocations] = useState<Locations | null>(null)

  useEffect(() => {
    api.locations().then(setLocations, () => undefined)
  }, [])

  return (
    <div className="flex flex-col gap-8">
      <ModelSettings settingsPath={locations?.settings ?? null} />
      {locations && <VaultLocation path={locations.vault} />}
      {desktop && <DesktopShortcut />}
    </div>
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
