"""vault 仓库的 git 提交。vault 是独立仓库，与 note-agent 代码仓库无关。"""

import subprocess
from pathlib import Path


def commit(vault_root: Path, message: str) -> bool:
    """git add -A + commit。无变更时静默返回 False，提交成功返回 True。"""
    root = Path(vault_root)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )

    run("add", "-A")
    status = run("status", "--porcelain")
    if not status.stdout.strip():
        return False
    run("commit", "-m", message)
    return True
