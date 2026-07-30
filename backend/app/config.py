"""运行时配置。vault 根目录可用环境变量 VAULT_PATH 覆盖。"""

import os
from pathlib import Path


def default_vault_root() -> Path:
    env = os.environ.get("VAULT_PATH")
    if env:
        return Path(env)
    # <repo 根的上一级>/vault，即 note-agent 与 vault 平级
    return Path(__file__).resolve().parents[3] / "vault"
