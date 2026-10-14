// 一键打包：界面 → 后端 → 外壳，产出 desktop/release/note-agent/（双击 note-agent.exe 运行）。
// 在 desktop/ 下运行：npm run build
// 下载 Electron 慢时先设镜像：$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"

import { spawnSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { packager } from "@electron/packager"

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const repo = path.dirname(desktop)
const frontend = path.join(repo, "frontend")
const backend = path.join(repo, "backend")
const release = path.join(desktop, "release")
const target = path.join(release, "note-agent")
const pkg = JSON.parse(fs.readFileSync(path.join(desktop, "package.json"), "utf-8"))

function step(title) {
  console.log(`\n== ${title}`)
}

function run(title, command, args, cwd, shell = false) {
  step(title)
  const result = spawnSync(command, args, { cwd, stdio: "inherit", shell })
  if (result.status !== 0) {
    console.error(`\n${title}失败（退出代码 ${result.status}）`)
    process.exit(1)
  }
}

const python = path.join(backend, ".venv", "Scripts", "python.exe")
if (!fs.existsSync(python)) {
  console.error("找不到 backend/.venv。先在 backend 下运行：uv sync --group build")
  process.exit(1)
}

// 经 npm run build 启动时 npm_execpath 指向 npm 自己；直接 node scripts/build.mjs 时没有，退回 shell
if (process.env.npm_execpath) {
  run("构建界面", process.execPath, [process.env.npm_execpath, "run", "build"], frontend)
} else {
  run("构建界面", "npm run build", [], frontend, true)
}
run(
  "打包后端",
  python,
  ["-m", "PyInstaller", "--noconfirm", "--distpath", "dist", "--workpath", "build", "packaging/note-agent-backend.spec"],
  backend,
)

step("打包外壳")
const mirror = process.env.ELECTRON_MIRROR
const [out] = await packager({
  dir: desktop,
  out: release,
  name: "note-agent",
  executableName: "note-agent",
  platform: "win32",
  arch: "x64",
  appVersion: pkg.version,
  icon: path.join(desktop, "assets", "icon.ico"),
  asar: true,
  overwrite: true,
  ignore: [/^\/release($|\/)/, /^\/scripts($|\/)/, /^\/assets($|\/)/],
  win32metadata: {
    FileDescription: "知识库",
    ProductName: "知识库",
    InternalName: "note-agent",
    OriginalFilename: "note-agent.exe",
  },
  download: mirror ? { mirrorOptions: { mirror } } : undefined,
})

// main.js 在 resources/backend、resources/frontend 找后端和界面
fs.cpSync(path.join(backend, "dist", "note-agent-backend"), path.join(out, "resources", "backend"), {
  recursive: true,
})
fs.cpSync(path.join(frontend, "dist"), path.join(out, "resources", "frontend"), { recursive: true })
// 界面只有中文：Chromium 的其他语言包用不到，删掉省 40 多 MB（缺语言时它退回 en-US）
for (const name of fs.readdirSync(path.join(out, "locales"))) {
  if (!["zh-CN.pak", "en-US.pak"].includes(name)) fs.rmSync(path.join(out, "locales", name))
}

try {
  fs.rmSync(target, { recursive: true, force: true })
} catch (err) {
  console.error(`\n删不掉旧的 ${target}：${err.message}\n是不是知识库还开着？关掉再打包。`)
  process.exit(1)
}
fs.renameSync(out, target)
console.log(`\n完成：${path.join(target, "note-agent.exe")}`)
