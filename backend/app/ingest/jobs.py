"""后台任务（录入、重新归类）的进程内登记表。只存在内存里，服务重启即清空。"""

import threading
from typing import Literal

from pydantic import BaseModel

from app.vault.ids import new_ulid
from app.vault.models import NoteRef

JobStatus = Literal["pending", "running", "done", "failed"]
Stage = Literal["解析", "拆分", "分类", "写入", "归类"]


class Progress(BaseModel):
    done: int
    total: int


class JobState(BaseModel):
    status: JobStatus = "pending"
    stage: Stage = "解析"
    created: list[NoteRef] = []
    inbox: list[NoteRef] = []
    duplicate: bool = False
    error: str | None = None
    # 只有重新归类的任务有进度；录入任务为 None
    progress: Progress | None = None


class JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, JobState] = {}

    def create(self, **fields: object) -> str:
        job_id = new_ulid()
        with self._lock:
            self._jobs[job_id] = JobState(**fields)
        return job_id

    def update(self, job_id: str, **fields: object) -> None:
        with self._lock:
            self._jobs[job_id] = self._jobs[job_id].model_copy(update=fields)

    def get(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)
