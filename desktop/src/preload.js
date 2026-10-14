// 界面里能调用的桌面功能。浏览器开发模式下没有 window.noteAgentDesktop，界面要自己判断。

const { contextBridge, ipcRenderer } = require("electron")

contextBridge.exposeInMainWorld("noteAgentDesktop", {
  // 选一个新的知识库文件夹；选定后应用会重新打开。返回 false 表示没有换
  chooseVault: () => ipcRenderer.invoke("vault:choose"),
  // 在资源管理器里打开当前的知识库文件夹
  openVault: () => ipcRenderer.invoke("vault:open"),
})
