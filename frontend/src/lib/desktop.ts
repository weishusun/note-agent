// 桌面版外壳（desktop/src/preload.js）提供的功能。浏览器里打开时没有，值为 null。

export interface DesktopBridge {
  // 选一个新的知识库文件夹；选定后应用会重新打开。返回 false 表示没有换
  chooseVault(): Promise<boolean>
  // 在资源管理器里打开当前的知识库文件夹
  openVault(): Promise<void>
}

declare global {
  interface Window {
    noteAgentDesktop?: DesktopBridge
  }
}

export const desktop: DesktopBridge | null = window.noteAgentDesktop ?? null
