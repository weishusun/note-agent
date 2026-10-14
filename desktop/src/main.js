// 桌面外壳：一个窗口装界面，后端（note-agent-backend.exe）随窗口启动和退出。
// 数据位置和启动约定见 docs/SCHEMA.md §7。

const { app, BrowserWindow, dialog, ipcMain, Menu, nativeTheme, screen, session, shell } = require("electron")
const { spawn } = require("node:child_process")
const fs = require("node:fs")
const net = require("node:net")
const path = require("node:path")

// 设置、密钥、日志都放在 %APPDATA%\note-agent。测试时用 NOTE_AGENT_DATA_DIR 指到别处
const dataDir = process.env.NOTE_AGENT_DATA_DIR || path.join(app.getPath("appData"), "note-agent")
app.setPath("userData", dataDir)
app.setPath("sessionData", path.join(dataDir, "window"))
app.setAppUserModelId("note-agent")

const configPath = path.join(dataDir, "desktop.json")
const settingsPath = path.join(dataDir, "settings.json")
const logDir = path.join(dataDir, "logs")
const logPath = path.join(logDir, "backend.log")
const LOG_MAX_BYTES = 5 * 1024 * 1024

// 固定端口优先：界面存在浏览器存储里的东西（比如提问页的对话）按「地址 + 端口」区分，
// 端口每次都变就每次都丢。被占用时才换一个空闲的
const PREFERRED_PORT = 47815
const READY_TIMEOUT_MS = 60_000
const STOP_TIMEOUT_MS = 5_000
const BACKGROUND = { light: "#fcfdfe", dark: "#121820" }

let mainWindow = null
let backend = null
let port = null
let vaultPath = null
let quitting = false

// ---------- 配置文件 ----------

function readConfig() {
  try {
    return JSON.parse(fs.readFileSync(configPath, "utf-8"))
  } catch {
    return {}
  }
}

function writeConfig(update) {
  const next = { ...readConfig(), ...update }
  fs.mkdirSync(dataDir, { recursive: true })
  const tmp = `${configPath}.tmp`
  fs.writeFileSync(tmp, JSON.stringify(next, null, 2), "utf-8")
  fs.renameSync(tmp, configPath)
}

// ---------- 选择知识库文件夹 ----------

function looksLikeVault(dir) {
  return ["taxonomy.yaml", ".noteagent", "notes"].some((name) => fs.existsSync(path.join(dir, name)))
}

function isEmptyDir(dir) {
  try {
    return fs.readdirSync(dir).length === 0
  } catch {
    return false
  }
}

function samePath(a, b) {
  return path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase()
}

async function pickVault(parent) {
  for (;;) {
    const options = {
      title: "选择知识库文件夹",
      defaultPath: vaultPath || app.getPath("documents"),
      properties: ["openDirectory", "createDirectory", "dontAddToRecent"],
    }
    const picked = parent
      ? await dialog.showOpenDialog(parent, options)
      : await dialog.showOpenDialog(options)
    if (picked.canceled || picked.filePaths.length === 0) return null
    const dir = picked.filePaths[0]
    if (path.parse(dir).root === dir) {
      await message(parent, {
        type: "warning",
        message: "不能直接用整个磁盘",
        detail: "请在里面新建一个文件夹，再选那个文件夹。",
      })
      continue
    }
    if (looksLikeVault(dir) || isEmptyDir(dir)) return dir
    const { response } = await message(parent, {
      type: "question",
      buttons: ["仍然使用", "重新选择", "取消"],
      defaultId: 1,
      cancelId: 2,
      message: "这个文件夹里已经有别的文件",
      detail: `${dir}\n\n它看起来不是知识库。继续的话，笔记会和里面已有的文件放在一起。建议新建一个空文件夹。`,
    })
    if (response === 0) return dir
    if (response === 2) return null
  }
}

function message(parent, options) {
  const full = { title: "知识库", buttons: ["确定"], noLink: true, ...options }
  return parent ? dialog.showMessageBox(parent, full) : dialog.showMessageBox(full)
}

function createDesktopShortcut() {
  // 开发时 execPath 是 electron.exe，不建
  if (!app.isPackaged) return
  shell.writeShortcutLink(path.join(app.getPath("desktop"), "知识库.lnk"), "create", {
    target: process.execPath,
    cwd: path.dirname(process.execPath),
    description: "知识库",
    icon: process.execPath,
    iconIndex: 0,
    appUserModelId: "note-agent",
  })
}

// 返回要打开的知识库；用户放弃时返回 null
async function ensureVault() {
  const saved = readConfig().vaultPath
  if (saved && fs.existsSync(saved)) return saved

  if (saved) {
    const { response } = await message(null, {
      type: "warning",
      buttons: ["重新选择…", "退出"],
      defaultId: 0,
      cancelId: 1,
      message: "找不到知识库文件夹",
      detail: `${saved}\n\n如果它在移动硬盘或网络盘上，请先连上再打开。也可以重新选一个文件夹。`,
    })
    if (response !== 0) return null
  } else {
    const { response, checkboxChecked } = await message(null, {
      type: "info",
      buttons: ["选择文件夹…", "退出"],
      defaultId: 0,
      cancelId: 1,
      message: "先选一个文件夹存放知识库",
      detail:
        "可以选已有的知识库文件夹（比如之前浏览器版用的 vault），也可以新建一个空文件夹。" +
        "笔记都是普通的 Markdown 文件。之后可以在设置页更换。",
      checkboxLabel: "在桌面创建快捷方式",
      checkboxChecked: true,
    })
    if (response !== 0) return null
    if (checkboxChecked) {
      try {
        createDesktopShortcut()
      } catch (err) {
        console.error("创建快捷方式失败", err)
      }
    }
  }

  const dir = await pickVault(null)
  if (dir) writeConfig({ vaultPath: dir })
  return dir
}

// ---------- 后端进程 ----------

function backendCommand() {
  if (app.isPackaged) {
    const resources = process.resourcesPath
    return {
      command: path.join(resources, "backend", "note-agent-backend.exe"),
      args: [],
      cwd: path.join(resources, "backend"),
      staticDir: path.join(resources, "frontend"),
    }
  }
  // 开发时直接用源码和 venv：npm start 前先在 frontend 下 npm run build
  const repo = path.resolve(__dirname, "..", "..")
  return {
    command: path.join(repo, "backend", ".venv", "Scripts", "python.exe"),
    args: ["-m", "app.desktop"],
    cwd: path.join(repo, "backend"),
    staticDir: path.join(repo, "frontend", "dist"),
  }
}

function portIsFree(candidate) {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.once("error", () => resolve(false))
    server.listen(candidate, "127.0.0.1", () => server.close(() => resolve(true)))
  })
}

function anyFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once("error", reject)
    server.listen(0, "127.0.0.1", () => {
      const { port: free } = server.address()
      server.close(() => resolve(free))
    })
  })
}

function openLog() {
  fs.mkdirSync(logDir, { recursive: true })
  try {
    if (fs.statSync(logPath).size > LOG_MAX_BYTES) {
      fs.renameSync(logPath, path.join(logDir, "backend.old.log"))
    }
  } catch {
    // 还没有日志
  }
  const fd = fs.openSync(logPath, "a")
  fs.writeSync(fd, `\n===== ${new Date().toISOString()} 启动，知识库 ${vaultPath} =====\n`)
  return fd
}

async function startBackend() {
  port = (await portIsFree(PREFERRED_PORT)) ? PREFERRED_PORT : await anyFreePort()
  const { command, args, cwd, staticDir } = backendCommand()
  const log = openLog()
  const child = spawn(
    command,
    [
      ...args,
      "--vault", vaultPath,
      "--settings", settingsPath,
      "--port", String(port),
      "--static", staticDir,
      "--exit-with-stdin",
    ],
    { cwd, windowsHide: true, stdio: ["pipe", log, log] },
  )
  fs.closeSync(log)
  backend = child

  const exited = new Promise((resolve) => {
    child.once("error", (err) => resolve(`无法启动后台服务：${err.message}`))
    child.once("exit", (code) => resolve(`后台服务退出，退出代码 ${code}`))
  })
  child.once("exit", (code) => {
    backend = null
    if (!quitting && mainWindow && mainWindow.webContents.getURL().startsWith(appOrigin())) {
      fail("后台服务意外退出", `退出代码 ${code}。`)
    }
  })

  const ready = waitForHealth()
  const outcome = await Promise.race([ready.then(() => null), exited])
  ready.cancel()
  if (outcome) throw new Error(outcome)
}

function waitForHealth() {
  let cancelled = false
  const promise = (async () => {
    const deadline = Date.now() + READY_TIMEOUT_MS
    while (!cancelled && Date.now() < deadline) {
      try {
        const resp = await fetch(`${appOrigin()}/health`, { signal: AbortSignal.timeout(1000) })
        if (resp.ok) return
      } catch {
        // 还没起来
      }
      await new Promise((r) => setTimeout(r, 250))
    }
    if (!cancelled) throw new Error(`后台服务 ${READY_TIMEOUT_MS / 1000} 秒内没有启动完成`)
  })()
  promise.cancel = () => {
    cancelled = true
  }
  promise.catch(() => {})
  return promise
}

function stopBackend() {
  const child = backend
  if (!child) return Promise.resolve()
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      // 正常退出不了才强制结束；录入队列存在磁盘上，下次打开会接着处理
      child.kill()
      resolve()
    }, STOP_TIMEOUT_MS)
    child.once("exit", () => {
      clearTimeout(timer)
      resolve()
    })
    // 后端在标准输入关闭时自己退出（--exit-with-stdin）
    child.stdin.end()
  })
}

function appOrigin() {
  return `http://127.0.0.1:${port}`
}

async function activeJobs() {
  if (!backend) return 0
  try {
    const resp = await fetch(`${appOrigin()}/api/jobs?limit=200`, { signal: AbortSignal.timeout(2000) })
    const jobs = await resp.json()
    return jobs.filter((job) => job.status === "pending" || job.status === "running").length
  } catch {
    return 0
  }
}

// ---------- 窗口 ----------

function visibleBounds(saved) {
  if (!saved || !saved.width || !saved.height) return {}
  const bounds = { width: saved.width, height: saved.height }
  if (Number.isInteger(saved.x) && Number.isInteger(saved.y)) {
    // 上次所在的显示器拔掉了，就不用旧位置，交给系统居中
    const area = screen.getDisplayMatching({ ...bounds, x: saved.x, y: saved.y }).workArea
    const inside =
      saved.x + 80 > area.x &&
      saved.y + 40 > area.y &&
      saved.x < area.x + area.width - 80 &&
      saved.y < area.y + area.height - 40
    if (inside) Object.assign(bounds, { x: saved.x, y: saved.y })
  }
  return bounds
}

// 窗口一有移动、缩放就记下来，退出时写进 desktop.json。
// 不在退出时现取：有些关闭方式（比如页面自己调 window.close()）到那时窗口已经销毁了
let windowState = null

function trackWindowState(win) {
  const update = () => {
    if (win.isDestroyed() || win.isMinimized()) return
    const maximized = win.isMaximized()
    windowState = { ...(maximized ? win.getNormalBounds() : win.getBounds()), maximized }
  }
  for (const name of ["resize", "move", "maximize", "unmaximize", "show"]) win.on(name, update)
}

function saveWindowState() {
  if (windowState) writeConfig({ window: windowState })
}

function openExternal(url) {
  if (/^https?:\/\//i.test(url)) shell.openExternal(url)
}

function createWindow() {
  const saved = readConfig().window
  const win = new BrowserWindow({
    width: 1360,
    height: 860,
    ...visibleBounds(saved),
    minWidth: 1024,
    minHeight: 640,
    title: "知识库",
    show: false,
    autoHideMenuBar: true,
    backgroundColor: nativeTheme.shouldUseDarkColors ? BACKGROUND.dark : BACKGROUND.light,
    icon: app.isPackaged ? undefined : path.join(__dirname, "..", "assets", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  })
  trackWindowState(win)
  win.once("ready-to-show", () => {
    if (saved && saved.maximized) win.maximize()
    win.show()
  })

  const inApp = (url) => port !== null && url.startsWith(`${appOrigin()}/`)
  // 笔记里的网址用系统浏览器打开；知识库里的素材（截图、PDF）开一个新窗口看
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (inApp(url)) {
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          autoHideMenuBar: true,
          webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
        },
      }
    }
    openExternal(url)
    return { action: "deny" }
  })
  win.webContents.on("will-navigate", (event, url) => {
    if (inApp(url)) return
    event.preventDefault()
    openExternal(url)
  })

  win.webContents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown") return
    const wc = win.webContents
    if (input.control && !input.alt) {
      const step = { "=": 0.5, "+": 0.5, "-": -0.5 }[input.key]
      if (step !== undefined) {
        wc.setZoomLevel(Math.max(-3, Math.min(3, wc.getZoomLevel() + step)))
        event.preventDefault()
      } else if (input.key === "0") {
        wc.setZoomLevel(0)
        event.preventDefault()
      }
    }
    if (!app.isPackaged && input.key === "F12") wc.toggleDevTools()
  })

  win.on("close", (event) => {
    if (quitting) return
    event.preventDefault()
    confirmAndQuit()
  })

  win.loadFile(path.join(__dirname, "loading.html"))
  return win
}

async function confirmAndQuit({ relaunch = false } = {}) {
  const active = await activeJobs()
  if (active > 0) {
    const { response } = await message(mainWindow, {
      type: "question",
      buttons: [relaunch ? "重新打开" : "退出", "取消"],
      defaultId: 0,
      cancelId: 1,
      message: `还有 ${active} 条录入在处理`,
      detail: "现在退出的话，下次打开这个知识库时会接着处理。",
    })
    if (response !== 0) return false
  }
  await shutdown({ relaunch })
  return true
}

async function shutdown({ relaunch = false } = {}) {
  if (quitting) return
  quitting = true
  saveWindowState()
  await stopBackend()
  if (relaunch) app.relaunch()
  app.exit(0)
}

async function fail(title, detail) {
  if (quitting) return
  const { response } = await message(mainWindow, {
    type: "error",
    buttons: ["打开日志文件夹", "退出"],
    defaultId: 1,
    message: title,
    detail: `${detail}\n\n详细信息在日志里：\n${logPath}`,
  })
  if (response === 0) await shell.openPath(logDir)
  await shutdown()
}

// ---------- 给界面用的几个操作（preload.js） ----------

function fromApp(event) {
  return port !== null && event.senderFrame !== null && event.senderFrame.url.startsWith(`${appOrigin()}/`)
}

ipcMain.handle("vault:open", async (event) => {
  if (!fromApp(event) || !vaultPath) return
  await shell.openPath(vaultPath)
})

ipcMain.handle("vault:choose", async (event) => {
  if (!fromApp(event)) return false
  const dir = await pickVault(mainWindow)
  if (!dir || samePath(dir, vaultPath)) return false
  const { response } = await message(mainWindow, {
    type: "question",
    buttons: ["重新打开", "取消"],
    defaultId: 0,
    cancelId: 1,
    message: "换成这个知识库？",
    detail: `${dir}\n\n应用会重新打开。原来的知识库不会被改动，随时可以换回来。`,
  })
  if (response !== 0) return false
  const previous = readConfig().vaultPath
  writeConfig({ vaultPath: dir })
  const done = await confirmAndQuit({ relaunch: true })
  if (!done) writeConfig({ vaultPath: previous })
  return done
})

// ---------- 启动 ----------

async function start() {
  Menu.setApplicationMenu(null)
  // 界面不需要摄像头、定位、通知之类的权限
  session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false))

  vaultPath = await ensureVault()
  if (!vaultPath) {
    app.exit(0)
    return
  }
  mainWindow = createWindow()
  try {
    await startBackend()
  } catch (err) {
    await fail("启动失败", err.message)
    return
  }
  mainWindow.loadURL(`${appOrigin()}/`)
}

if (!app.requestSingleInstanceLock()) {
  // 已经开着一个：把那个窗口叫到前面（second-instance），这个直接退出
  app.exit(0)
} else {
  app.on("second-instance", () => {
    if (!mainWindow || mainWindow.isDestroyed()) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
  })
  app.on("window-all-closed", () => {
    if (!quitting) shutdown()
  })
  app.whenReady().then(start)
}
