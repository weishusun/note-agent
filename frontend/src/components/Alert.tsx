import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

export function Alert({ tone, children }: { tone: "error" | "info"; children: ReactNode }) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "flex items-center gap-3 rounded-lg border p-3 text-sm",
        tone === "error" && "border-destructive/30 bg-destructive/10 text-destructive",
        tone === "info" && "bg-muted",
      )}
    >
      {children}
    </div>
  )
}
