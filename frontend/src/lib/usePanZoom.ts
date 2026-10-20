import { useCallback, useEffect, useRef, useState, type PointerEvent, type RefObject } from "react"

export interface Transform {
  x: number
  y: number
  k: number
}

const MIN_SCALE = 0.15
const MAX_SCALE = 4

// 给一块画布加上「拖动空白处平移、滚轮缩放」。关系图谱、目录导图（SVG）和我的导图（HTML）共用。
// 内容画在 translate(x,y) scale(k) 的一层里；toWorld 把屏幕坐标换算回内容坐标。
export function usePanZoom(svg: RefObject<HTMLElement | SVGSVGElement | null>) {
  const [transform, setTransform] = useState<Transform>({ x: 0, y: 0, k: 1 })
  const current = useRef(transform)
  current.current = transform
  const pan = useRef<{ startX: number; startY: number; x: number; y: number } | null>(null)

  useEffect(() => {
    // SVG 和 HTML 元素的 addEventListener 类型合不到一起，按 HTMLElement 用（滚轮事件两边都一样）
    const element = svg.current as HTMLElement | null
    if (!element) return
    // React 的 onWheel 是 passive 的，拦不住页面跟着滚动，所以用原生监听
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = element.getBoundingClientRect()
      const px = e.clientX - rect.left
      const py = e.clientY - rect.top
      setTransform((t) => {
        const k = Math.min(MAX_SCALE, Math.max(MIN_SCALE, t.k * Math.exp(-e.deltaY * 0.0015)))
        // 鼠标指着的那一点缩放前后保持不动
        return { k, x: px - ((px - t.x) / t.k) * k, y: py - ((py - t.y) / t.k) * k }
      })
    }
    element.addEventListener("wheel", onWheel, { passive: false })
    return () => element.removeEventListener("wheel", onWheel)
  }, [svg])

  const toWorld = useCallback(
    (clientX: number, clientY: number) => {
      const rect = svg.current?.getBoundingClientRect()
      const t = current.current
      return {
        x: (clientX - (rect?.left ?? 0) - t.x) / t.k,
        y: (clientY - (rect?.top ?? 0) - t.y) / t.k,
      }
    },
    [svg],
  )

  // 让 (minX, minY)–(maxX, maxY) 这块内容正好放进画布，四周留一点边
  const fit = useCallback(
    (box: { minX: number; minY: number; maxX: number; maxY: number }, maxScale = 1.2) => {
      const element = svg.current
      if (!element) return
      const width = element.clientWidth
      const height = element.clientHeight
      const margin = 40
      const w = Math.max(1, box.maxX - box.minX)
      const h = Math.max(1, box.maxY - box.minY)
      const k = Math.min(
        maxScale,
        Math.max(MIN_SCALE, Math.min((width - margin * 2) / w, (height - margin * 2) / h)),
      )
      setTransform({
        k,
        x: width / 2 - ((box.minX + box.maxX) / 2) * k,
        y: height / 2 - ((box.minY + box.maxY) / 2) * k,
      })
    },
    [svg],
  )

  const zoomBy = useCallback(
    (factor: number) => {
      const element = svg.current
      if (!element) return
      const cx = element.clientWidth / 2
      const cy = element.clientHeight / 2
      setTransform((t) => {
        const k = Math.min(MAX_SCALE, Math.max(MIN_SCALE, t.k * factor))
        return { k, x: cx - ((cx - t.x) / t.k) * k, y: cy - ((cy - t.y) / t.k) * k }
      })
    },
    [svg],
  )

  // 绑在画布上：在空白处按下开始平移
  const panHandlers = {
    onPointerDown: (e: PointerEvent<Element>) => {
      if (e.button !== 0) return
      try {
        e.currentTarget.setPointerCapture(e.pointerId)
      } catch {
        // 指针已经不在了：没抓住也只是拖出画布后跟不上
      }
      const t = current.current
      pan.current = { startX: e.clientX, startY: e.clientY, x: t.x, y: t.y }
    },
    onPointerMove: (e: PointerEvent<Element>) => {
      const start = pan.current
      if (!start) return
      setTransform((t) => ({
        ...t,
        x: start.x + e.clientX - start.startX,
        y: start.y + e.clientY - start.startY,
      }))
    },
    onPointerUp: () => {
      pan.current = null
    },
    onPointerCancel: () => {
      pan.current = null
    },
  }

  return { transform, setTransform, toWorld, fit, zoomBy, panHandlers }
}
