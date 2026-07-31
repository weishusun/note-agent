"""运行时配置。集中从 note-agent/.env 加载,导出模型/密钥配置。

.env 位于**项目根 note-agent/**(不是 backend/.env),用相对本文件的路径推导,
不依赖 cwd——无论从哪个目录跑都指向同一个 .env。

缺 key 的可读报错在**使用点**(client.require... )抛,不在导入期抛:
否则没配 key 时连不需要模型的 45 个测试都会因导入 config 失败而跑不起来。
模型名(EXTRACT_MODEL / CLASSIFY_MODEL)故意没有默认值——不允许硬编码模型名,
未配置就在解析时报错。base_url 给默认值(智谱 v4),它不是模型名。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/app/config.py:parents[2] = note-agent/(项目根),parents[3] = 其上一级
_THIS = Path(__file__).resolve()
_PROJECT_ROOT = _THIS.parents[2]
ENV_PATH = _PROJECT_ROOT / ".env"

# .env 不存在时 load_dotenv 静默返回 False,不报错(CI / 测试环境无 .env 是正常的)
load_dotenv(ENV_PATH)

ZHIPU_API_KEY: str | None = os.environ.get("ZHIPU_API_KEY")
GLM_BASE_URL: str = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
EXTRACT_MODEL: str | None = os.environ.get("EXTRACT_MODEL")
CLASSIFY_MODEL: str | None = os.environ.get("CLASSIFY_MODEL")
# 结构化输出方式:function_calling | json_mode。GLM 对两者支持不一,撞墙时改 .env 即可
STRUCTURED_OUTPUT_METHOD: str = os.environ.get("STRUCTURED_OUTPUT_METHOD", "function_calling")


def require_api_key() -> str:
    """在真正建模型前调用。缺 key 立刻抛可读错误,不拖到调用时才 401。"""
    if not ZHIPU_API_KEY:
        raise RuntimeError("请在 note-agent/.env 中配置 ZHIPU_API_KEY")
    return ZHIPU_API_KEY


def default_vault_root() -> Path:
    """vault 与 note-agent 平级(见 SCHEMA 布局)。可用环境变量 VAULT_PATH 覆盖。"""
    env = os.environ.get("VAULT_PATH")
    if env:
        return Path(env)
    return _THIS.parents[3] / "vault"
