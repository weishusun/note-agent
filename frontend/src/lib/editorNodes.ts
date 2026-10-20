// 笔记正文编辑器（RichEditor）的内容结构：哪些格式可用，[[双链]] 和 ![[素材]] 怎么表示。
// 单独放一个文件，测试时可以用同一套配置把笔记在编辑器里过一遍。

import { Node, mergeAttributes, type Extensions } from "@tiptap/core"
import StarterKit from "@tiptap/starter-kit"
import { TableKit } from "@tiptap/extension-table"

// [[双链]]：一个不可拆的行内块，显示标题
export const Wikilink = Node.create({
  name: "wikilink",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,
  addAttributes() {
    return {
      target: {
        default: "",
        parseHTML: (el) => el.getAttribute("data-wikilink") ?? "",
        renderHTML: (attrs) => ({ "data-wikilink": attrs.target }),
      },
      label: {
        default: "",
        parseHTML: (el) => el.getAttribute("data-label") ?? el.textContent ?? "",
        renderHTML: (attrs) => ({ "data-label": attrs.label }),
      },
    }
  },
  parseHTML() {
    return [{ tag: "span[data-wikilink]" }]
  },
  renderHTML({ node, HTMLAttributes }) {
    return ["span", mergeAttributes(HTMLAttributes, { class: "wikilink" }), node.attrs.label || node.attrs.target]
  },
  renderText({ node }) {
    return node.attrs.label || node.attrs.target
  },
})

const IMAGE = /\.(png|jpe?g|gif|webp)$/i

// ![[assets/截图]]：单独一块，图片显示出来，别的文件显示文件名
export const Embed = Node.create({
  name: "embed",
  group: "block",
  atom: true,
  selectable: true,
  addAttributes() {
    return {
      src: {
        default: "",
        parseHTML: (el) => el.getAttribute("data-embed") ?? "",
        renderHTML: (attrs) => ({ "data-embed": attrs.src }),
      },
    }
  },
  parseHTML() {
    return [{ tag: "div[data-embed]" }]
  },
  renderHTML({ node, HTMLAttributes }) {
    // 带上文件名做内容：空 div 转 Markdown 时会被当成空白丢掉
    return ["div", mergeAttributes(HTMLAttributes), node.attrs.src]
  },
  addNodeView() {
    return ({ node }) => {
      const dom = document.createElement("div")
      dom.className = "embed"
      const src = String(node.attrs.src)
      const name = src.replace(/^assets\//, "")
      if (IMAGE.test(src)) {
        const img = document.createElement("img")
        img.src = `/api/assets/${encodeURIComponent(name)}`
        img.alt = name
        dom.append(img)
      } else {
        dom.textContent = `📎 ${name}`
      }
      return { dom }
    }
  },
})

export function noteExtensions(): Extensions {
  return [
    StarterKit.configure({
      heading: { levels: [2, 3] },
      // 阅读区不显示斜体、删除线、下划线和 [文字](网址) 链接：编辑器里也不提供，免得存进去显示成符号
      italic: false,
      strike: false,
      underline: false,
      link: false,
    }),
    TableKit.configure({ table: { resizable: false } }),
    Wikilink,
    Embed,
  ]
}
