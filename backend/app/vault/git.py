"""vault 仓库的 git 提交。vault 是独立仓库，与 note-agent 代码仓库无关。"""

import subprocess
import threading
from pathlib import Path

GIT_TIMEOUT_SECONDS = 120
# 录入队列、模型补充（并行几条）、界面上的编辑都会提交：同时跑会撞上 git 的 index.lock
_COMMIT_LOCK = threading.Lock()


def commit(vault_root: Path, message: str) -> bool:
    """git add -A + commit。无变更时静默返回 False，提交成功返回 True。

    失败或超时抛 subprocess.SubprocessError（或找不到 git 时的 OSError）。
    """
    root = Path(vault_root)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            # 不能继承标准输入：桌面版后端的标准输入是连着外壳的管道，
            # Windows 上 git 继承它会一直卡住（实测）
            stdin=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
        )

    with _COMMIT_LOCK:
        run("add", "-A")
        status = run("status", "--porcelain")
        if not status.stdout.strip():
            return False
        run("commit", "-m", message)
        return True
