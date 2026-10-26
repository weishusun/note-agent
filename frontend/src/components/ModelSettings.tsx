import { useEffect, useId, useMemo, useState, type ReactNode } from "react"
import { CheckIcon, ExternalLinkIcon, PlugIcon, XIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { Button } from "@/components/ui/button"
import {
  api,
  CUSTOM_PROVIDER,
  type AccountView,
  type ModelKind,
  type ModelList,
  type ProbeResult,
  type ProviderInfo,
  type RoleChoice,
  type RoleName,
  type SettingsUpdate,
  type SettingsView,
  type VectorStatus,
} from "@/lib/api"
import { cn } from "@/lib/utils"

const INPUT =
  "h-9 w-full rounded-lg border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"

const KIND_LABEL: Record<ModelKind, string> = { vision: "看图", text: "文字", embed: "向量" }

interface RoleDef {
  role: RoleName
  kind: ModelKind
  label: string
  hint: string
  // 可以不选：选项里多一个「不使用 / 同文字模型」
  none?: string
}

const ROLE_DEFS: RoleDef[] = [
  { role: "extract", kind: "vision", label: "看图", hint: "读截图，必须是能看图的模型。" },
  {
    role: "classify",
    kind: "text",
    label: "文字",
    hint: "分类、整理长文、扩展检索词，调用最多，选快且便宜的。",
  },
  {
    role: "embed",
    kind: "embed",
    label: "向量",
    hint: "提问时换个说法也能搜到笔记，录入时能发现内容相近的重复。不选就只按字面检索。",
    none: "不使用（只做字面检索）",
  },
  {
    role: "answer",
    kind: "text",
    label: "问答",
    hint: "回答你的提问。默认和文字模型一样；想要回答更好，可以单独选一个强一点的。",
    none: "同文字模型",
  },
]

// 清单之外、自己填地址的服务商
const CUSTOM_INFO: ProviderInfo = {
  id: CUSTOM_PROVIDER,
  name: "其他（OpenAI 兼容）",
  base_url: "",
  key_url: null,
  key_hint: null,
  key_pattern: null,
  model_list: "all",
  verified: false,
  models: { vision: [], text: [], embed: [] },
  note: "任何兼容 OpenAI 接口的服务商都可以。没有推荐模型，模型名照它的文档填；关闭思考的参数不会发给它。",
}

type Roles = Record<RoleName, RoleChoice>
type Lists = Record<string, ModelList | "loading">
type Probes = Partial<Record<"text" | "vision" | "answer" | "embedding", ProbeResult>>

function rolesOf(view: SettingsView): Roles {
  return { extract: view.extract, classify: view.classify, answer: view.answer, embed: view.embed }
}

export function ModelSettings({ settingsPath }: { settingsPath: string | null }) {
  const [catalog, setCatalog] = useState<ProviderInfo[] | null>(null)
  const [saved, setSaved] = useState<SettingsView | null>(null)
  const [roles, setRoles] = useState<Roles | null>(null)
  const [thinking, setThinking] = useState({ disable_thinking: true, extract_disable_thinking: true })
  const [lists, setLists] = useState<Lists>({})
  // 展开的那张服务商卡片；切过去时可以带上刚粘贴的 Key
  const [open, setOpen] = useState<{ id: string; key?: string } | null>(null)
  const [busy, setBusy] = useState<"save" | "test" | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [probes, setProbes] = useState<Probes | null>(null)
  const [vectors, setVectors] = useState<VectorStatus | null>(null)
  // 每保存一次递增：让向量索引的进度重新开始轮询
  const [vectorsKey, setVectorsKey] = useState(0)

  const infos = useMemo(() => [...(catalog ?? []), CUSTOM_INFO], [catalog])
  const infoOf = (id: string) => infos.find((p) => p.id === id)

  function apply(view: SettingsView) {
    setSaved(view)
    setRoles(rolesOf(view))
    setThinking({
      disable_thinking: view.disable_thinking,
      extract_disable_thinking: view.extract_disable_thinking,
    })
  }

  function fetchList(id: string, body: { api_key?: string; base_url?: string } = {}) {
    setLists((prev) => ({ ...prev, [id]: "loading" }))
    return api.providerModels(id, body).then(
      (list) => {
        setLists((prev) => ({ ...prev, [id]: list }))
        return list
      },
      (e: Error) => {
        const list: ModelList = { status: "error", models: [], detail: e.message }
        setLists((prev) => ({ ...prev, [id]: list }))
        return list
      },
    )
  }

  useEffect(() => {
    let cancelled = false
    Promise.all([api.providers(), api.settings()])
      .then(([providers, view]) => {
        if (cancelled) return
        setCatalog(providers)
        apply(view)
        // 一家都没连上时直接展开第一家，引导第一次使用的人
        if (Object.keys(view.providers).length === 0) setOpen({ id: providers[0]?.id ?? CUSTOM_PROVIDER })
        // 已连上的各家：在后台拉一下模型列表，给下面的输入框做候选
        for (const id of Object.keys(view.providers)) void fetchList(id)
      })
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let tries = 0
    // 先让后端把缺的向量补上，再每两秒看一次进度，补齐了（或看了一分钟）就停
    const poll = (first: boolean) => {
      ;(first ? api.syncVectors() : api.vectors()).then(
        (status) => {
          if (cancelled) return
          setVectors(status)
          if (status.enabled && status.embedded < status.total && ++tries < 30) {
            timer = setTimeout(() => poll(false), 2000)
          }
        },
        () => undefined,
      )
    }
    poll(true)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [vectorsKey])

  if (!catalog || !saved || !roles) {
    return error ? <Alert tone="error">{error}</Alert> : <p className="text-sm text-muted-foreground">加载中…</p>
  }

  const accounts = saved.providers

  // 连上一家之后：保存 Key，并把还没着落的环节按这家的推荐填上
  async function connected(info: ProviderInfo, account: { api_key?: string; base_url?: string }) {
    if (!roles) return
    const update: SettingsUpdate = { providers: { [info.id]: account } }
    const filled: string[] = []
    for (const def of ROLE_DEFS) {
      if (def.role === "answer") continue
      const current = roles[def.role]
      const usable = current.model && current.provider in accounts
      const recommended = info.models[def.kind][0]
      if (!usable && recommended) {
        update[def.role] = { provider: info.id, model: recommended }
        filled.push(def.label)
      }
    }
    const view = await api.saveSettings(update)
    apply(view)
    setVectorsKey((k) => k + 1)
    setProbes(null)
    setOpen(null)
    setNotice(
      `已连上${info.name}。` +
        (filled.length ? `按推荐填好了${filled.join("、")}模型，点「保存并测试」确认能用。` : ""),
    )
  }

  async function disconnect(info: ProviderInfo) {
    if (!roles) return
    if (!window.confirm(`断开${info.name}？保存的 Key 会删掉，用它的环节要另选。`)) return
    const update: SettingsUpdate = { providers: { [info.id]: null } }
    for (const def of ROLE_DEFS) {
      if (roles[def.role].provider === info.id) update[def.role] = { provider: "", model: "" }
    }
    try {
      apply(await api.saveSettings(update))
      setLists((prev) => {
        const next = { ...prev }
        delete next[info.id]
        return next
      })
      setOpen(null)
      setNotice(`已断开${info.name}。`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  function setRole(role: RoleName, choice: RoleChoice) {
    setRoles((prev) => (prev ? { ...prev, [role]: choice } : prev))
    setNotice(null)
  }

  async function save(): Promise<boolean> {
    if (!roles) return false
    try {
      apply(await api.saveSettings({ ...roles, ...thinking }))
      setError(null)
      setVectorsKey((k) => k + 1)
      return true
    } catch (e) {
      setError((e as Error).message)
      return false
    }
  }

  async function onSave() {
    setBusy("save")
    setProbes(null)
    if (await save()) setNotice("已保存，立即生效。")
    setBusy(null)
  }

  async function onTest() {
    setBusy("test")
    setProbes(null)
    setNotice(null)
    // 测的是已保存的设置，所以先保存
    if (await save()) {
      try {
        setProbes(await api.testSettings())
      } catch (e) {
        setError((e as Error).message)
      }
    }
    setBusy(null)
  }

  const connectedCount = Object.keys(accounts).length
  const openInfo = open ? infoOf(open.id) : undefined

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3" aria-labelledby="providers-heading">
        <h2 id="providers-heading" className="font-heading text-lg">
          模型服务商
        </h2>
        <p className="-mt-1 text-sm text-muted-foreground">
          先连上至少一家：选服务商，粘贴它的 API Key，点「连接」。看图、文字、向量可以各用一家，比如文字用
          DeepSeek、看图和向量用智谱。Key 只发给你选的那一家，保存在本机的{" "}
          <code className="rounded bg-muted px-1 font-mono text-xs break-all">{settingsPath ?? "settings.json"}</code>
          ，不在知识库里，保存后不再显示。
        </p>
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
          {infos.map((info) => (
            <ProviderCard
              key={info.id}
              info={info}
              account={accounts[info.id]}
              selected={open?.id === info.id}
              onClick={() => setOpen(open?.id === info.id ? null : { id: info.id })}
            />
          ))}
        </div>
        {openInfo && (
          <ProviderPanel
            key={openInfo.id + (open?.key ?? "")}
            info={openInfo}
            account={accounts[openInfo.id]}
            list={lists[openInfo.id]}
            catalog={catalog}
            initialKey={open?.key ?? ""}
            onCheck={(body) => fetchList(openInfo.id, body)}
            onConnected={(account) => connected(openInfo, account)}
            onDisconnect={() => disconnect(openInfo)}
            onSwitch={(id, key) => setOpen({ id, key })}
          />
        )}
      </section>

      <section className="flex flex-col gap-4" aria-labelledby="roles-heading">
        <h2 id="roles-heading" className="font-heading text-lg">
          各环节用的模型
        </h2>
        {connectedCount === 0 ? (
          <p className="-mt-2 text-sm text-muted-foreground">先在上面连上一家服务商，这里会按推荐自动填好。</p>
        ) : (
          <>
            <p className="-mt-2 text-sm text-muted-foreground">
              连上服务商时已按推荐填好。模型名可以从候选里选，也可以照服务商的文档自己填。
            </p>
            {ROLE_DEFS.map((def) => (
              <RoleRow
                key={def.role}
                def={def}
                choice={roles[def.role]}
                accounts={accounts}
                infoOf={infoOf}
                lists={lists}
                onChange={(choice) => setRole(def.role, choice)}
              />
            ))}
            {vectors?.enabled && (
              <p role="status" className="-mt-2 text-xs text-muted-foreground">
                向量索引：{vectors.total} 条笔记里已有 {vectors.embedded} 条算好了向量
                {vectors.embedded < vectors.total ? "，其余的正在后台计算…" : "。"}
              </p>
            )}
            {vectors?.enabled && !vectors.calibrated && (
              <Alert tone="info">
                {"这个向量模型的相似度门槛还没实测过，暂用默认值：问答可能带回不太相关的笔记、或漏掉相关的，录入查重也可能不准。" +
                  "各家模型的相似度尺度差得很远，需要实测后写进 providers.yaml。"}
              </Alert>
            )}

            <details className="group rounded-lg border px-3 py-2">
              <summary className="cursor-pointer text-sm text-muted-foreground select-none">高级</summary>
              <div className="flex flex-col gap-3 pt-3 pb-1">
                <Check
                  label="分类等小调用关闭思考模式"
                  hint="关闭后每次通常不到 1 秒。各家的参数写法不同，已按服务商自动处理；自定义服务商不会收到这个参数。"
                  checked={thinking.disable_thinking}
                  onChange={(v) => setThinking({ ...thinking, disable_thinking: v })}
                />
                <Check
                  label="看图提取时关闭思考模式"
                  hint="默认勾选。实测（智谱）开着思考一张截图要 100 秒以上，内容并没有更完整。"
                  checked={thinking.extract_disable_thinking}
                  onChange={(v) => setThinking({ ...thinking, extract_disable_thinking: v })}
                />
              </div>
            </details>

            <div className="flex flex-wrap items-center gap-3">
              <Button type="button" size="lg" disabled={busy !== null} onClick={onTest}>
                {busy === "test" ? "测试中…" : "保存并测试"}
              </Button>
              <Button type="button" variant="outline" size="lg" disabled={busy !== null} onClick={onSave}>
                {busy === "save" ? "保存中…" : "只保存"}
              </Button>
            </div>
          </>
        )}
        {notice && (
          <p role="status" className="text-sm text-muted-foreground">
            {notice}
          </p>
        )}
        {error && <Alert tone="error">{error}</Alert>}
        {probes && (
          <ul className="flex flex-col gap-2" aria-label="连接测试结果">
            {probes.vision && <Probe name="看图模型" result={probes.vision} />}
            {probes.text && <Probe name="文字模型" result={probes.text} />}
            {probes.embedding && <Probe name="向量模型" result={probes.embedding} detailLabel="向量维度" />}
            {probes.answer && <Probe name="问答模型" result={probes.answer} />}
          </ul>
        )}
      </section>
    </div>
  )
}

function ProviderCard({
  info,
  account,
  selected,
  onClick,
}: {
  info: ProviderInfo
  account: AccountView | undefined
  selected: boolean
  onClick: () => void
}) {
  const custom = info.id === CUSTOM_PROVIDER
  return (
    <button
      type="button"
      aria-expanded={selected}
      onClick={onClick}
      className={cn(
        "flex flex-col gap-2 rounded-xl border bg-card p-3 text-left transition-colors hover:border-primary/40",
        selected && "border-primary ring-2 ring-primary/15",
      )}
    >
      <span className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{info.name}</span>
        {account ? (
          <span className="flex shrink-0 items-center gap-1 text-xs text-primary">
            <CheckIcon className="size-3.5" />
            已连接
          </span>
        ) : info.verified ? (
          <span className="shrink-0 text-[11px] text-muted-foreground">已实测</span>
        ) : null}
      </span>
      {custom ? (
        <span className="text-xs text-muted-foreground">自己填接口地址</span>
      ) : (
        <span className="flex flex-wrap gap-1">
          {(Object.keys(KIND_LABEL) as ModelKind[]).map((kind) => {
            const has = info.models[kind].length > 0
            return (
              <span
                key={kind}
                className={cn(
                  "rounded-full px-2 py-0.5 text-[11px]",
                  has ? "bg-accent text-accent-foreground" : "text-muted-foreground line-through",
                )}
                title={has ? undefined : `${info.name}没有${KIND_LABEL[kind]}模型`}
              >
                {KIND_LABEL[kind]}
              </span>
            )
          })}
        </span>
      )}
    </button>
  )
}

function ProviderPanel({
  info,
  account,
  list,
  catalog,
  initialKey,
  onCheck,
  onConnected,
  onDisconnect,
  onSwitch,
}: {
  info: ProviderInfo
  account: AccountView | undefined
  list: ModelList | "loading" | undefined
  catalog: ProviderInfo[]
  initialKey: string
  onCheck: (body: { api_key?: string; base_url?: string }) => Promise<ModelList>
  onConnected: (account: { api_key?: string; base_url?: string }) => Promise<void>
  onDisconnect: () => void
  onSwitch: (id: string, key: string) => void
}) {
  const custom = info.id === CUSTOM_PROVIDER
  const [key, setKey] = useState(initialKey)
  const [url, setUrl] = useState(account?.base_url ?? "")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const keyId = useId()
  const urlId = useId()

  // 粘贴进来的 Key 明显是别家的格式：提示切过去
  const trimmed = key.trim()
  const fitsHere = info.key_pattern ? new RegExp(info.key_pattern).test(trimmed) : false
  const elsewhere =
    trimmed && !fitsHere
      ? catalog.find((p) => p.id !== info.id && p.key_pattern && new RegExp(p.key_pattern).test(trimmed))
      : undefined

  async function connect() {
    setBusy(true)
    setError(null)
    const body = { api_key: trimmed || undefined, base_url: custom ? url.trim() : undefined }
    const result = await onCheck(body)
    if (result.status === "bad_key") {
      setError(`连不上：${result.detail}。确认 Key 是${info.name}的，并且复制完整。`)
    } else if (result.status === "error") {
      setError(`连不上：${result.detail}`)
    } else {
      // ok，或者这家不提供模型列表（unsupported）：Key 至少没被拒，先存下来，「测试」时再确认
      try {
        await onConnected(body)
      } catch (e) {
        setError((e as Error).message)
      }
    }
    setBusy(false)
  }

  const canConnect = !busy && (trimmed !== "" || (account !== undefined && !custom)) && (!custom || url.trim() !== "")

  return (
    <div className="flex flex-col gap-3 rounded-xl border bg-pane p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-medium">{info.name}</span>
        {info.key_url && (
          <a
            href={info.key_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-sm text-primary underline-offset-4 hover:underline"
          >
            去{info.name}创建 API Key
            <ExternalLinkIcon className="size-3.5" />
          </a>
        )}
        {!info.verified && !custom && (
          <span className="text-xs text-muted-foreground">推荐模型按官方文档整理，还没有实测过</span>
        )}
      </div>
      {info.note && <p className="-mt-1 text-xs text-muted-foreground">{info.note}</p>}

      {account && (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <CheckIcon className="size-4 text-primary" />
          已连接{account.api_key_hint ? `（Key 末四位 ${account.api_key_hint}）` : ""}
          <ListStatus list={list} info={info} />
        </p>
      )}

      {custom && (
        <div className="flex flex-col gap-1.5">
          <label htmlFor={urlId} className="text-sm font-medium">
            接口地址
          </label>
          <input
            id={urlId}
            className={INPUT}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…/v1"
          />
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <label htmlFor={keyId} className="text-sm font-medium">
          {account ? "换一把 Key（可不填）" : "API Key"}
        </label>
        <div className="flex gap-2">
          <input
            id={keyId}
            type="password"
            autoComplete="off"
            className={INPUT}
            value={key}
            onChange={(e) => {
              setKey(e.target.value)
              setError(null)
            }}
            onKeyDown={(e) => e.key === "Enter" && canConnect && void connect()}
            placeholder={info.key_hint ? `粘贴 API Key（${info.key_hint}）` : "粘贴 API Key"}
          />
          <Button type="button" disabled={!canConnect} onClick={connect}>
            <PlugIcon />
            {busy ? "连接中…" : account ? "重新连接" : "连接"}
          </Button>
        </div>
        {elsewhere && (
          <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            这看起来是{elsewhere.name}的 Key。
            <button
              type="button"
              className="text-primary underline-offset-4 hover:underline"
              onClick={() => onSwitch(elsewhere.id, trimmed)}
            >
              切换到{elsewhere.name}
            </button>
          </p>
        )}
      </div>
      {error && <Alert tone="error">{error}</Alert>}
      {account && (
        <button
          type="button"
          onClick={onDisconnect}
          className="self-start text-xs text-muted-foreground underline-offset-4 hover:text-destructive hover:underline"
        >
          断开{info.name}
        </button>
      )}
    </div>
  )
}

function ListStatus({ list, info }: { list: ModelList | "loading" | undefined; info: ProviderInfo }) {
  if (list === undefined) return null
  if (list === "loading") return <span className="text-xs text-muted-foreground">正在查可用模型…</span>
  if (list.status === "ok") {
    return (
      <span className="text-xs text-muted-foreground">
        · 能用的模型 {list.models.length} 个{info.model_list === "text" ? "（只列出文字模型，看图和向量的用推荐）" : ""}
      </span>
    )
  }
  return <span className="text-xs text-muted-foreground">· {list.detail}</span>
}

function RoleRow({
  def,
  choice,
  accounts,
  infoOf,
  lists,
  onChange,
}: {
  def: RoleDef
  choice: RoleChoice
  accounts: Record<string, AccountView>
  infoOf: (id: string) => ProviderInfo | undefined
  lists: Lists
  onChange: (choice: RoleChoice) => void
}) {
  const selectId = useId()
  const listId = useId()
  const connected = Object.keys(accounts)
  // 没选服务商 = 不使用（向量）/ 同文字模型（问答）
  const unused = def.none !== undefined && !choice.provider
  const provider = unused ? "" : choice.provider
  const info = provider ? infoOf(provider) : undefined
  const recommended = info?.models[def.kind] ?? []
  const fetched = provider ? lists[provider] : undefined
  const listed = fetched && fetched !== "loading" && fetched.status === "ok" ? fetched.models : []
  const candidates = [...recommended, ...listed.filter((m) => !recommended.includes(m))]
  // 这家的列表能覆盖这类模型、列表里又没有这个名字：多半写错了或改名了
  const coversKind = info !== undefined && (info.model_list === "all" || def.kind === "text")
  const missing = coversKind && listed.length > 0 && choice.model !== "" && !listed.includes(choice.model)
  const lacks = (id: string) => {
    const p = infoOf(id)
    return p !== undefined && p.id !== CUSTOM_PROVIDER && p.models[def.kind].length === 0
  }

  function pickProvider(id: string) {
    if (!id) {
      onChange({ provider: "", model: "" })
      return
    }
    const next = infoOf(id)
    const keep = id === choice.provider && choice.model
    onChange({ provider: id, model: keep ? choice.model : (next?.models[def.kind][0] ?? "") })
  }

  return (
    <div className="grid gap-x-3 gap-y-1.5 sm:grid-cols-[5rem_minmax(0,12rem)_minmax(0,1fr)] sm:items-center">
      <label htmlFor={selectId} className="text-sm font-medium">
        {def.label}
      </label>
      <select
        id={selectId}
        className={cn(INPUT, "pr-8")}
        value={provider}
        onChange={(e) => pickProvider(e.target.value)}
      >
        {def.none !== undefined ? (
          <option value="">{def.none}</option>
        ) : (
          !provider && <option value="">选服务商</option>
        )}
        {connected.map((id) => (
          <option key={id} value={id} disabled={lacks(id)}>
            {infoOf(id)?.name ?? id}
            {lacks(id) ? `（没有${KIND_LABEL[def.kind]}模型）` : ""}
          </option>
        ))}
      </select>
      <div className="min-w-0">
        <input
          aria-label={`${def.label}模型名`}
          className={INPUT}
          list={listId}
          value={unused ? "" : choice.model}
          disabled={unused}
          onChange={(e) => onChange({ provider, model: e.target.value })}
          placeholder={unused ? "" : "模型名"}
        />
        <datalist id={listId}>
          {candidates.map((m) => (
            <option key={m} value={m} label={recommended.includes(m) ? "推荐" : undefined} />
          ))}
        </datalist>
      </div>
      <p className="text-xs text-muted-foreground sm:col-start-2 sm:col-end-4">
        {missing ? (
          <span className="text-destructive">
            {info?.name}的模型列表里没有「{choice.model}」，可能写错了或者已经改名。
          </span>
        ) : (
          def.hint
        )}
      </p>
    </div>
  )
}

function Check({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string
  hint: string
  checked: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <label className="flex items-start gap-2 text-sm">
      <input
        type="checkbox"
        className="mt-1"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>
        <span className="font-medium">{label}</span>
        <span className="block text-xs text-muted-foreground">{hint}</span>
      </span>
    </label>
  )
}

function Probe({
  name,
  result,
  detailLabel = "模型回复",
}: {
  name: string
  result: ProbeResult
  detailLabel?: string
}): ReactNode {
  return (
    <li className="flex items-start gap-2 rounded-lg border p-3 text-sm">
      {result.ok ? (
        <CheckIcon className="mt-0.5 size-4 shrink-0 text-emerald-600" />
      ) : (
        <XIcon className="mt-0.5 size-4 shrink-0 text-destructive" />
      )}
      <span className="min-w-0">
        <span className="font-medium">
          {name}：{result.ok ? "正常" : "失败"}
        </span>
        <span className="ml-2 text-xs text-muted-foreground">{result.seconds} 秒</span>
        <span className="mt-0.5 block text-xs break-words text-muted-foreground">
          {result.ok ? `${detailLabel}：${result.detail}` : result.detail}
        </span>
      </span>
    </li>
  )
}
