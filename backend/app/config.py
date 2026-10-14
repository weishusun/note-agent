"""运行时配置。集中从 note-agent/.env 加载,导出模型/密钥配置。

.env 位于**项目根 note-agent/**(不是 backend/.env),用相对本文件的路径推导,
不依赖 cwd——无论从哪个目录跑都指向同一个 .env。

缺 key 的可读报错在**使用点**(client.require... )抛,不在导入期抛:
否则没配 key 时连不需要模型的 45 个测试都会因导入 config 失败而跑不起来。
模型名(EXTRACT_MODEL / CLASSIFY_MODEL)故意没有默认值——不允许硬编码模型名,
未配置就在解析时报错。base_url 给默认值(智谱 v4),它不是模型名。
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# backend/app/config.py:parents[2] = note-agent/(项目根),parents[3] = 其上一级
_THIS = Path(__file__).resolve()
_PROJECT_ROOT = _THIS.parents[2]
ENV_PATH = _PROJECT_ROOT / ".env"

# .env 不存在时 load_dotenv 静默返回 False,不报错(CI / 测试环境无 .env 是正常的)。
# 打包成桌面版后不认 .env:设置只来自外壳指定的 settings.json
if not getattr(sys, "frozen", False):
    load_dotenv(ENV_PATH)

ZHIPU_API_KEY: str | None = os.environ.get("ZHIPU_API_KEY")
GLM_BASE_URL: str = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
EXTRACT_MODEL: str | None = os.environ.get("EXTRACT_MODEL")
CLASSIFY_MODEL: str | None = os.environ.get("CLASSIFY_MODEL")
# 问答模型。未配置（或留空）时沿用 CLASSIFY_MODEL
ANSWER_MODEL: str | None = os.environ.get("ANSWER_MODEL") or CLASSIFY_MODEL

# 分类置信度低于此值的笔记强制落 inbox（SCHEMA 不变量 10）。待 eval 校准
INBOX_CONFIDENCE_THRESHOLD: float = 0.6

# 文本模型的辅助调用(分类、压缩、检索词扩展)是否发送智谱专有参数 thinking=disabled。
# 实测 glm-4.5-air 默认开着思考,这类小调用每次 5 秒以上,关掉后不到 1 秒。
# 换成不认这个参数的服务商时设为 false
DISABLE_THINKING: bool = os.environ.get("DISABLE_THINKING", "true").lower() != "false"


def require_api_key() -> str:
    """只给 evals 脚本用:它们只认 .env。应用本身经 app.settings 取密钥(设置页的值优先)。"""
    if not ZHIPU_API_KEY:
        raise RuntimeError("请在 note-agent/.env 中配置 ZHIPU_API_KEY")
    return ZHIPU_API_KEY


def default_vault_root() -> Path:
    """vault 与 note-agent 平级(见 SCHEMA 布局)。可用环境变量 VAULT_PATH 覆盖。"""
    env = os.environ.get("VAULT_PATH")
    if env:
        return Path(env)
    return _THIS.parents[3] / "vault"
