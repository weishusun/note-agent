"""app/ingest/queue.py：排队、按顺序处理、失败与重试、取消、跨重启恢复。处理函数全部是假的。"""

import threading
from pathlib import Path

import pytest

from app.ingest import queue as queue_module
from app.ingest.queue import IngestQueue, JobNotRetryable, JobRunning, QueuedJob


class Handler:
    """假的处理函数：记下处理顺序；可以让它在某个任务上卡住、或者报错。"""

    def __init__(self) -> None:
        self.seen: list[tuple[str, bytes | None, bool]] = []
        self.fail: set[str] = set()
        self.gate = threading.Event()
        self.gate.set()
        self.started = threading.Event()

    def __call__(self, job: QueuedJob, on_stage) -> list[str]:
        self.started.set()
        assert self.gate.wait(5), "测试没有放行"
        self.seen.append((job.label, job.payload, job.force))
        on_stage("拆分")
        if job.label in self.fail:
            raise RuntimeError(f"{job.label} 处理失败")
        return [f"note-of-{job.label}"]


@pytest.fixture
def handler() -> Handler:
    return Handler()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "state" / "queue.db"


@pytest.fixture
def queue(db: Path, handler: Handler) -> IngestQueue:
    return IngestQueue(db, handler)


def _submit(queue: IngestQueue, label: str, **kw) -> str:
    kw.setdefault("payload", label.encode("utf-8"))
    return queue.submit(kind="text", label=label, input_hash=f"hash-{label}", **kw)


def test_jobs_are_processed_one_at_a_time_in_submission_order(
    queue: IngestQueue, handler: Handler
) -> None:
    handler.gate.clear()
    ids = [_submit(queue, label) for label in ("甲", "乙", "丙")]
    assert handler.started.wait(5)

    # 第一份卡在处理中，后两份排着
    assert [queue.get(i).status for i in ids] == ["running", "pending", "pending"]
    assert [j.label for j in queue.list()] == ["丙", "乙", "甲"]  # 列表是最近提交的在前

    handler.gate.set()
    assert queue.wait_idle()

    assert [label for label, _, _ in handler.seen] == ["甲", "乙", "丙"]
    done = queue.get(ids[0])
    assert (done.status, done.stage, done.note_ids, done.error) == ("done", "写入", ["note-of-甲"], None)
    assert done.finished_at is not None and done.finished_at >= done.created_at


def test_a_failed_job_does_not_stop_the_queue_and_can_be_retried(
    queue: IngestQueue, handler: Handler
) -> None:
    handler.fail.add("甲")
    first, second = _submit(queue, "甲"), _submit(queue, "乙")
    assert queue.wait_idle()

    failed = queue.get(first)
    assert (failed.status, failed.error, failed.note_ids) == ("failed", "甲 处理失败", [])
    assert queue.get(second).status == "done"

    handler.fail.clear()
    queue.retry(first)
    assert queue.wait_idle()
    retried = queue.get(first)
    assert (retried.status, retried.error, retried.note_ids) == ("done", None, ["note-of-甲"])
    # 输入一直留到处理成功为止，重试时原样交给处理函数；重试带着 force
    assert handler.seen[-1] == ("甲", "甲".encode("utf-8"), True)


def test_only_failed_or_duplicate_jobs_can_be_retried(queue: IngestQueue, handler: Handler) -> None:
    done = _submit(queue, "甲")
    assert queue.wait_idle()
    # 成功处理完的任务输入已经清掉了
    with pytest.raises(JobNotRetryable):
        queue.retry(done)
    with pytest.raises(KeyError):
        queue.retry("不存在")

    handler.gate.clear()
    running, pending = _submit(queue, "乙"), _submit(queue, "丙")
    assert handler.started.wait(5)
    for job_id in (running, pending):
        with pytest.raises(JobRunning):
            queue.retry(job_id)
    handler.gate.set()
    assert queue.wait_idle()


def test_duplicate_is_recorded_as_done_and_can_be_reprocessed(
    queue: IngestQueue, handler: Handler
) -> None:
    job_id = queue.add_duplicate(
        kind="text", label="甲", payload=b"x", input_hash="h", note_ids=["旧-1", "旧-2"]
    )
    job = queue.get(job_id)
    assert (job.status, job.duplicate, job.note_ids) == ("done", True, ["旧-1", "旧-2"])
    assert handler.seen == []  # 没有交给处理函数

    queue.retry(job_id)
    assert queue.wait_idle()
    again = queue.get(job_id)
    assert (again.status, again.duplicate, again.note_ids) == ("done", False, ["note-of-甲"])
    assert handler.seen == [("甲", b"x", True)]


def test_remove_cancels_a_pending_job_but_not_a_running_one(
    queue: IngestQueue, handler: Handler
) -> None:
    handler.gate.clear()
    running, pending = _submit(queue, "甲"), _submit(queue, "乙")
    assert handler.started.wait(5)

    with pytest.raises(JobRunning):
        queue.remove(running)
    queue.remove(pending)
    with pytest.raises(KeyError):
        queue.remove(pending)

    handler.gate.set()
    assert queue.wait_idle()
    assert [label for label, _, _ in handler.seen] == ["甲"]
    assert queue.get(pending) is None

    queue.remove(running)  # 处理完的记录可以清掉
    assert queue.list() == []


def test_clear_finished_keeps_failed_jobs(queue: IngestQueue, handler: Handler) -> None:
    handler.fail.add("乙")
    _submit(queue, "甲")
    failed = _submit(queue, "乙")
    assert queue.wait_idle()

    assert queue.clear_finished() == 1
    assert [j.id for j in queue.list()] == [failed]


def test_find_active_only_sees_jobs_still_in_the_queue(queue: IngestQueue, handler: Handler) -> None:
    handler.gate.clear()
    first, second = _submit(queue, "甲"), _submit(queue, "乙")
    assert handler.started.wait(5)
    assert (queue.find_active("hash-甲"), queue.find_active("hash-乙")) == (first, second)
    assert queue.find_active("hash-丙") is None

    handler.gate.set()
    assert queue.wait_idle()
    assert queue.find_active("hash-甲") is None


def test_unfinished_jobs_survive_a_restart(db: Path, handler: Handler) -> None:
    before = IngestQueue(db, handler)
    handler.gate.clear()
    running, pending = _submit(before, "甲"), _submit(before, "乙")
    assert handler.started.wait(5)
    # 进程在这时没了：「甲」停在处理中，「乙」还在排队。旧进程的线程不会再写任何东西
    before._update = lambda *args, **kw: None  # type: ignore[method-assign]
    before._claim_next = lambda: None  # type: ignore[method-assign]
    handler.gate.set()
    assert before.wait_idle()

    restarted_handler = Handler()
    after = IngestQueue(db, restarted_handler)
    assert [after.get(i).status for i in (running, pending)] == ["running", "pending"]

    assert after.resume() == 2
    assert after.wait_idle()
    # 处理到一半的那份从头重做，顺序不变，输入还在
    assert restarted_handler.seen == [("甲", "甲".encode("utf-8"), False), ("乙", "乙".encode("utf-8"), False)]
    assert [after.get(i).status for i in (running, pending)] == ["done", "done"]
    assert after.resume() == 0


def test_old_finished_records_are_pruned(
    queue: IngestQueue, handler: Handler, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue_module, "KEEP_FINISHED", 2)
    for label in ("甲", "乙", "丙"):
        _submit(queue, label)
        assert queue.wait_idle()
    assert [j.label for j in queue.list()] == ["丙", "乙"]
