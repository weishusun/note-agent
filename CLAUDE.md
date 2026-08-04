# CLAUDE.md

## 铁律

- 所有文件读写必须显式 `encoding="utf-8"`，路径统一用 `pathlib.Path`
- vault 的读写必须经过 `backend/app/vault/`，禁止在别处 `open()`
- 分类只能取自 `taxonomy.yaml`，禁止在代码里硬编码分类名

## 模型与外部服务

- 服务商：智谱开放平台，OpenAI 兼容接口
- 视觉/提取模型：读环境变量 EXTRACT_MODEL（当前值 glm-4.6v）
- 文本/分类模型：读环境变量 CLASSIFY_MODEL（当前值 glm-4.5-air）
- base_url 读 GLM_BASE_URL，key 读 ZHIPU_API_KEY
- 代码中禁止出现任何硬编码的模型名字符串，一律从环境变量读
- 本项目不使用 Anthropic / OpenAI 的模型。pyproject.toml 里的 langchain-anthropic
  是骨架期残留，未被任何代码 import，请一并移除

## 开发节奏

- 一次只做一层，不要一次性实现整个阶段
- 当前顺序：提取层（纯函数，不写盘）→ LangGraph 编排 → 写入 API → 前端
- 阶段 3 中，文件输入 / PDF / generated 骨架模式暂缓，第一版只支持
  screenshot 和短文本两条路
- 本项目在 Windows PowerShell 下开发。执行 git commit 等需要多行文本的命令时，
  一律用 bash heredoc，禁止使用 PowerShell here-string 语法（@'...'@）
- 提交前必须先跑 git status 并复核。禁止无脑 git add -A —— 用显式路径添加本次
  改动涉及的文件。如果 status 里出现你本次没有改过的删除或改名，停下来问我，
  不要自行提交或恢复。
- 涉及全局工具安装、虚拟环境创建、PATH 或注册表修改的操作，一律交给我在
  普通 PowerShell 里执行，你只负责给出命令。你的进程可能运行在沙箱中，
  对 %APPDATA% / %LOCALAPPDATA% 的写入会被重定向到私有存储，
  产出的环境我的终端看不见。D 盘上的项目文件不受此影响，正常读写即可。
