import { useState, type FormEvent, type ReactNode } from "react"
import {
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  InboxIcon,
  LockIcon,
  MessageCircleIcon,
  NetworkIcon,
  NotebookTextIcon,
  PlusIcon,
  SettingsIcon,
  UploadIcon,
  WaypointsIcon,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import type { Tree } from "@/lib/api"
import { categoryColor, type Page, type Selection } from "@/lib/library"
import { cn } from "@/lib/utils"

const NAME_MAX = 12

// 左栏：几个页面的入口，加上整个知识库的分类目录
export function Sidebar({
  page,
  onPage,
  tree,
  selection,
  onSelect,
  activeJobs,
  onCreateCategory,
}: {
  page: Page
  onPage: (page: Page) => void
  tree: Tree | null
  selection: Selection
  onSelect: (selection: Selection) => void
  // 录入队列里还没处理完的份数
  activeJobs: number
  // 新建分类；失败时抛出错误消息
  onCreateCategory: (path: string) => Promise<void>
}) {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const [creating, setCreating] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 图谱和导图也按目录里选中的范围来画：在那两页里点分类，留在原页
  const scoped = page === "notes" || page === "graph" || page === "map"
  const selectedPath = scoped && selection.kind === "category" ? selection.path : null

  function select(next: Selection) {
    onSelect(next)
    if (!scoped || (next.kind !== "category" && next.kind !== "all")) onPage("notes")
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const value = creating?.trim()
    if (!value) return
    try {
      await onCreateCategory(value)
      setCreating(null)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <aside className="flex w-60 flex-none flex-col overflow-hidden border-r bg-sidebar">
      <nav aria-label="页面" className="flex flex-col gap-0.5 px-3 pt-3">
        <NavItem icon={<NotebookTextIcon />} label="笔记" count={tree?.total} active={page === "notes"} onClick={() => onPage("notes")} />
        <NavItem
          icon={<UploadIcon />}
          label="录入"
          active={page === "ingest"}
          onClick={() => onPage("ingest")}
          badge={activeJobs > 0 ? activeJobs : undefined}
        />
        <NavItem icon={<MessageCircleIcon />} label="提问" active={page === "ask"} onClick={() => onPage("ask")} />
        <NavItem icon={<WaypointsIcon />} label="图谱" active={page === "graph"} onClick={() => onPage("graph")} />
        <NavItem icon={<NetworkIcon />} label="导图" active={page === "map"} onClick={() => onPage("map")} />
      </nav>

      <div className="flex items-center px-6 pt-5 pb-1.5">
        <span className="flex-1 text-[11px] tracking-[0.14em] text-muted-foreground/80">分类</span>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="新建分类"
          title="新建分类"
          className="text-muted-foreground"
          onClick={() => setCreating(creating === null ? "" : null)}
        >
          <PlusIcon />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3" aria-label="分类目录" role="tree">
        {creating !== null && (
          <form onSubmit={submit} className="mb-2 flex flex-col gap-1.5 px-1">
            <input
              autoFocus
              value={creating}
              maxLength={NAME_MAX * 2 + 1}
              onChange={(e) => {
                setCreating(e.target.value)
                setError(null)
              }}
              onKeyDown={(e) => e.key === "Escape" && setCreating(null)}
              aria-label="新分类的路径"
              placeholder="大块，或 大块/小块"
              className="h-8 rounded-md border bg-background px-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/40"
            />
            {error && <p className="text-xs text-destructive">{error}</p>}
          </form>
        )}

        <Row
          label="全部笔记"
          count={tree?.total}
          selected={scoped && selection.kind === "all"}
          onClick={() => select({ kind: "all" })}
        />
        {tree?.categories.map((parent) => {
          const open = !collapsed.has(parent.name)
          return (
            <div key={parent.name} role="group">
              <Row
                label={parent.name}
                count={parent.count}
                locked={parent.locked}
                selected={selectedPath === parent.name}
                onClick={() => select({ kind: "category", path: parent.name })}
                lead={
                  <span className="flex w-4 items-center justify-center">
                    <span className="size-1.5 rounded-full" style={{ background: categoryColor(parent.name) }} />
                  </span>
                }
                toggle={
                  parent.children.length > 0 && (
                    <button
                      type="button"
                      aria-label={open ? `收起 ${parent.name}` : `展开 ${parent.name}`}
                      aria-expanded={open}
                      className="rounded p-0.5 text-muted-foreground/70 hover:text-foreground"
                      onClick={(e) => {
                        e.stopPropagation()
                        const next = new Set(collapsed)
                        if (open) next.add(parent.name)
                        else next.delete(parent.name)
                        setCollapsed(next)
                      }}
                    >
                      {open ? <ChevronDownIcon className="size-3.5" /> : <ChevronRightIcon className="size-3.5" />}
                    </button>
                  )
                }
              />
              {open &&
                parent.children.map((child) => (
                  <Row
                    key={child.path}
                    indent
                    label={child.name}
                    count={child.count}
                    locked={child.locked}
                    selected={selectedPath === child.path}
                    onClick={() => select({ kind: "category", path: child.path })}
                  />
                ))}
            </div>
          )
        })}
        {tree && tree.categories.length === 0 && (
          <p className="px-3 py-2 text-xs text-muted-foreground">还没有分类。录入笔记时会自动生成。</p>
        )}
      </div>

      <div className="flex flex-col gap-0.5 border-t px-3 py-3">
        {!!tree?.inbox && (
          <NavItem
            icon={<InboxIcon />}
            label="待整理"
            count={tree.inbox}
            active={page === "notes" && selection.kind === "inbox"}
            onClick={() => select({ kind: "inbox" })}
          />
        )}
        {!!tree?.duplicates && (
          <NavItem
            icon={<CopyIcon />}
            label="疑似重复"
            count={tree.duplicates}
            tone="accent"
            active={page === "notes" && selection.kind === "duplicates"}
            onClick={() => select({ kind: "duplicates" })}
          />
        )}
        <NavItem icon={<SettingsIcon />} label="设置" active={page === "settings"} onClick={() => onPage("settings")} />
      </div>
    </aside>
  )
}

function NavItem({
  icon,
  label,
  count,
  badge,
  active,
  tone,
  onClick,
}: {
  icon: ReactNode
  label: string
  count?: number
  badge?: number
  active: boolean
  tone?: "accent"
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-current={active ? "page" : undefined}
      onClick={onClick}
      className={cn(
        "flex h-8 items-center gap-2.5 rounded-lg px-2.5 text-[13px] text-muted-foreground transition-colors outline-none hover:bg-sidebar-accent hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/40 [&_svg]:size-4 [&_svg]:flex-none",
        active && "bg-background text-foreground shadow-[0_1px_2px_rgba(16,32,44,0.06),0_0_0_1px_var(--border)] hover:bg-background",
        tone === "accent" && "text-accent-foreground",
      )}
    >
      {icon}
      <span className="flex-1 text-left">{label}</span>
      {badge !== undefined && (
        <span className="rounded-full bg-primary px-1.5 text-[11px] leading-[18px] text-primary-foreground tabular-nums">
          {badge}
        </span>
      )}
      {count !== undefined && badge === undefined && (
        <span className="text-[11px] text-muted-foreground/70 tabular-nums">{count}</span>
      )}
    </button>
  )
}

function Row({
  label,
  count,
  locked,
  selected,
  indent,
  lead,
  toggle,
  onClick,
}: {
  label: string
  count?: number
  locked?: boolean
  selected: boolean
  indent?: boolean
  lead?: ReactNode
  toggle?: ReactNode
  onClick: () => void
}) {
  return (
    <div
      role="treeitem"
      aria-selected={selected}
      className={cn(
        "group flex h-8 items-center gap-1.5 rounded-lg pr-2 text-[13px] text-muted-foreground hover:bg-sidebar-accent hover:text-foreground",
        indent ? "pl-8" : "pl-2",
        selected && "bg-accent text-accent-foreground hover:bg-accent hover:text-accent-foreground",
      )}
    >
      {lead ?? (!indent && <span className="w-4" />)}
      <button
        type="button"
        onClick={onClick}
        className="flex h-full min-w-0 flex-1 items-center gap-1.5 text-left outline-none focus-visible:underline"
      >
        <span className="truncate">{label}</span>
        {locked && <LockIcon className="size-3 flex-none opacity-60" aria-label="已锁定" />}
        <span className="ml-auto pl-2 text-[11px] opacity-70 tabular-nums">{count}</span>
      </button>
      {toggle}
    </div>
  )
}
