"""后台任务的状态（SCHEMA 第 5 节 GET /api/jobs）。

录入任务存在 app/ingest/queue.py 的持久队列里；这里的 JobRegistry 是进程内的登记表，
只给重新归类这种不需要跨重启的任务用，服务重启即清空。
"""

import threading
from typing import Literal

from pydantic import BaseModel

from app.vault.ids import new_ulid
from app.vault.models import NoteRef

JobStatus = Literal["pending", "running", "done", "failed"]
Stage = Literal["解析", "拆分", "分类", "写入", "归类", "模型补充"]


class Progress(BaseModel):
    done: int
    total: int


class JobState(BaseModel):
    # 录入任务才有下面五项；重新归类的任务为 None / 空
    id: str | None = None
    kind: str | None = None
    label: str = ""
    created_at: str | None = None
    finished_at: str | None = None
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
