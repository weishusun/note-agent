import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react"
import { CheckIcon, XIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { Button } from "@/components/ui/button"
import { api, type ProbeResult, type SettingsUpdate, type SettingsView } from "@/lib/api"

type Form = Omit<SettingsView, "api_key_set" | "api_key_hint"> & { api_key: string }

const INPUT =
  "h-9 w-full rounded-lg border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"

function toForm(view: SettingsView): Form {
  return {
    base_url: view.base_url,
    api_key: "",
    extract_model: view.extract_model,
    classify_model: view.classify_model,
    answer_model: view.answer_model,
    disable_thinking: view.disable_thinking,
    extract_disable_thinking: view.extract_disable_thinking,
  }
}

export function SettingsPanel({ active }: { active: boolean }) {
  const [saved, setSaved] = useState<SettingsView | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [busy, setBusy] = useState<"save" | "test" | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [probes, setProbes] = useState<{ text: ProbeResult; vision: ProbeResult } | null>(null)

  useEffect(() => {
    if (!active || saved) return
    let cancelled = false
    api
      .settings()
      .then((view) => {
        if (cancelled) return
        setSaved(view)
        setForm(toForm(view))
      })
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [active, saved])

  if (!form || !saved) {
    return error ? <Alert tone="error">{error}</Alert> : <p className="text-sm text-muted-foreground">加载中…</p>
  }

  const set = <K extends keyof Form>(key: K, value: Form[K]) => {
    setForm({ ...form, [key]: value })
    setNotice(null)
  }

  async function save(): Promise<boolean> {
    if (!form) return false
    // 密钥留空就不带这一项，后端保持原值
    const { api_key, ...rest } = form
    const update: SettingsUpdate = api_key.trim() ? { ...rest, api_key } : rest
    try {
      const view = await api.saveSettings(update)
      setSaved(view)
      setForm(toForm(view))
      setError(null)
      return true
    } catch (e) {
      setError((e as Error).message)
      return false
    }
  }

  async function onSave(e: FormEvent) {
    e.preventDefault()
    setBusy("save")
    setProbes(null)
    if (await save()) setNotice("已保存，立即生效。")
    setBusy(null)
  }

  async function onTest() {
    setBusy("test")
    setProbes(null)
    setNotice(null)
    // 测的是已保存的设置，所以先保存当前表单
    if (await save()) {
      try {
        setProbes(await api.testSettings())
      } catch (e) {
        setError((e as Error).message)
      }
    }
    setBusy(null)
  }

  const keyPlaceholder = saved.api_key_set
    ? `已设置${saved.api_key_hint ? `（末四位 ${saved.api_key_hint}）` : ""}，留空则不修改`
    : "尚未设置"

  return (
    <form onSubmit={onSave} className="flex flex-col gap-5">
      <p className="text-sm text-muted-foreground">
        可以换成任何兼容 OpenAI 接口的服务商。设置保存在本机的{" "}
        <code className="rounded bg-muted px-1 font-mono text-xs">note-agent/settings.json</code>
        ，不会提交到 git；密钥保存后不再显示。
      </p>

      <Field label="接口地址">
        {(id) => (
          <input
            id={id}
            className={INPUT}
            value={form.base_url}
            onChange={(e) => set("base_url", e.target.value)}
            placeholder="https://open.bigmodel.cn/api/paas/v4/"
          />
        )}
      </Field>
      <Field label="API Key">
        {(id) => (
          <input
            id={id}
            type="password"
            autoComplete="off"
            className={INPUT}
            value={form.api_key}
            onChange={(e) => set("api_key", e.target.value)}
            placeholder={keyPlaceholder}
          />
        )}
      </Field>
      <Field label="提取模型" hint="负责看截图，必须支持图片输入。">
        {(id) => (
          <input
            id={id}
            className={INPUT}
            value={form.extract_model}
            onChange={(e) => set("extract_model", e.target.value)}
          />
        )}
      </Field>
      <Field label="分类模型" hint="也用于压缩超长文本和扩展检索词，选便宜快速的即可。">
        {(id) => (
          <input
            id={id}
            className={INPUT}
            value={form.classify_model}
            onChange={(e) => set("classify_model", e.target.value)}
          />
        )}
      </Field>
      <Field label="问答模型" hint="留空则使用分类模型。">
        {(id) => (
          <input
            id={id}
            className={INPUT}
            value={form.answer_model}
            onChange={(e) => set("answer_model", e.target.value)}
            placeholder={form.classify_model}
          />
        )}
      </Field>

      <fieldset className="flex flex-col gap-3 rounded-lg border p-3">
        <legend className="px-1 text-xs text-muted-foreground">高级</legend>
        <Check
          label="分类等小调用关闭思考模式"
          hint="智谱专有参数，关闭后每次不到 1 秒。换成不认这个参数的服务商时取消勾选。"
          checked={form.disable_thinking}
          onChange={(v) => set("disable_thinking", v)}
        />
        <Check
          label="看图提取时关闭思考模式"
          hint="默认勾选。取消后模型会先思考再输出，一张截图要 100 秒以上，实测内容并没有更完整。"
          checked={form.extract_disable_thinking}
          onChange={(v) => set("extract_disable_thinking", v)}
        />
      </fieldset>

      <div className="flex items-center gap-3">
        <Button type="submit" size="lg" disabled={busy !== null}>
          {busy === "save" ? "保存中…" : "保存"}
        </Button>
        <Button type="button" variant="outline" size="lg" disabled={busy !== null} onClick={onTest}>
          {busy === "test" ? "测试中…" : "保存并测试连接"}
        </Button>
        {notice && (
          <span role="status" className="text-sm text-muted-foreground">
            {notice}
          </span>
        )}
      </div>

      {error && <Alert tone="error">{error}</Alert>}
      {probes && (
        <ul className="flex flex-col gap-2" aria-label="连接测试结果">
          <Probe name="分类模型（文本）" result={probes.text} />
          <Probe name="提取模型（看图）" result={probes.vision} />
        </ul>
      )}
    </form>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: (id: string) => ReactNode
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children(id)}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
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

function Probe({ name, result }: { name: string; result: ProbeResult }) {
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
          {result.ok ? `模型回复：${result.detail}` : result.detail}
        </span>
      </span>
    </li>
  )
}
