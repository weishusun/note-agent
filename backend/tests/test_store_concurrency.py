"""H1 回归：40 线程用同一个标题并发 create_note，一条都不能丢。

REVIEW-2026-07-31 H1 实测：check-then-act 的 _unique_path 让 40 个线程全部选中
同一个路径，os.replace 静默互相覆盖，最后只剩 1 条。
"""

import threading
from pathlib import Path

from app.vault.store import VaultStore
from tests.conftest import make_note

THREADS = 40
TITLE = "并发同名笔记"


def test_concurrent_create_note_same_title(store: VaultStore) -> None:
    barrier = threading.Barrier(THREADS)
    results: list[Path | None] = [None] * THREADS
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        try:
            note = make_note(title=TITLE, body=f"第 {i} 号线程的正文")
            barrier.wait(timeout=30)
            results[i] = store.create_note(note)
        except BaseException as exc:  # noqa: BLE001 — 线程里的异常必须带回主线程断言
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    # 零异常抛出
    assert errors == [], f"{len(errors)} 个线程抛异常: {errors[:3]}"
    assert all(t.is_alive() is False for t in threads)

    # 落盘的 .md 文件数 == 40
    files = sorted(store.notes_dir.glob("*.md"))
    assert len(files) == THREADS, f"落盘 {len(files)} 个文件，期望 {THREADS}"

    # create_note 返回的路径去重后也是 40 个，且与落盘文件一一对应
    returned = [r for r in results if r is not None]
    assert len(returned) == THREADS
    assert len(set(returned)) == THREADS
    assert {store.root / r for r in returned} == set(files)

    # 40 份正文全部不同、全部能读回，一条都不能丢
    bodies = {store.read_note(rel).body.strip() for rel in returned}
    assert bodies == {f"第 {i} 号线程的正文" for i in range(THREADS)}

    # 没有残留 tmp
    assert list(store.root.rglob("*.tmp")) == []

    # notes/ 下没有 0 字节文件（抢到的占位都被真实内容替换掉了）
    assert [p for p in store.notes_dir.iterdir() if p.stat().st_size == 0] == []
