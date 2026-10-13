import base64
import binascii
import logging
import mimetypes
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from app.ask.answer import AskResult, answer_question
from app.config import default_vault_root
from app.ingest.jobs import JobRegistry, JobState, Progress
from app.ingest.pipeline import input_hash, run_ingest
from app.ingest.queue import IngestQueue, JobNotRetryable, JobRunning, QueuedJob
from app.ingest.sources import MAX_FILE_BYTES, SUPPORTED_EXTENSIONS, file_extension
from app.ingest.web import normalize_url
from app.organize.api import build_router as build_organize_router
from app.organize.service import Organizer
from app.search.graph import Graph, note_graph
from app.search.index import SearchIndex
from app.search.query import list_inbox, list_recent, refs_by_ids, search
from app.settings_api import router as settings_router
from app.vault.models import NoteRef, SourceKind, Taxonomy
from app.vault.store import VaultStore
from app.vault.taxonomy import TaxonomyManager

logger = logging.getLogger(__name__)


class IngestRequest(BaseModel):
    kind: SourceKind
    text: str | None = None
    image_b64: str | None = None
    file_b64: str | None = None
    filename: str | None = None
    url: str | None = None
    force: bool = False


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question 不能为空白")
        return v.strip()


# 文本再长也只整理前 20 万字（digest.DOC_MAX_CHARS），这里只挡住明显不对劲的输入
TEXT_MAX_CHARS = 1_000_000
_LABEL_CHARS = 40


@dataclass(frozen=True)
class _Input:
    """一份校验过的输入，原样存进队列。"""

    kind: SourceKind
    payload: bytes
    # 队列列表里显示的名字
    label: str
    filename: str | None = None


def _decode_b64(value: str, field: str) -> bytes:
    # 允许前端直接传 data URL
    encoded = value.split(",", 1)[1] if value.startswith("data:") else value
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail=f"{field} 不是合法的 base64")
    if not data:
        raise HTTPException(status_code=400, detail=f"{field} 解码后为空")
    return data


def _parse_ingest(req: IngestRequest) -> _Input:
    """校验请求。不合法抛 400。"""
    if req.kind == "text":
        text = (req.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="kind=text 需要非空 text")
        if len(text) > TEXT_MAX_CHARS:
            raise HTTPException(status_code=400, detail=f"文本太长了，最多 {TEXT_MAX_CHARS} 字")
        flat = " ".join(text[: _LABEL_CHARS * 4].split())
        label = flat if len(flat) <= _LABEL_CHARS else flat[:_LABEL_CHARS] + "…"
        return _Input("text", text.encode("utf-8"), label)
    if req.kind == "screenshot":
        if not req.image_b64:
            raise HTTPException(status_code=400, detail="kind=screenshot 需要 image_b64")
        return _Input("screenshot", _decode_b64(req.image_b64, "image_b64"), "截图")
    if req.kind == "file":
        filename = PurePath((req.filename or "").replace("\\", "/")).name.strip()
        if not req.file_b64 or not filename:
            raise HTTPException(status_code=400, detail="kind=file 需要 file_b64 和 filename")
        if file_extension(filename) not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=400, detail="暂不支持这种文件，目前支持 PDF、Markdown 和纯文本"
            )
        data = _decode_b64(req.file_b64, "file_b64")
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=400, detail=f"文件太大了，最大 {MAX_FILE_BYTES // 1024 // 1024} MB"
            )
        return _Input("file", data, filename, filename)
    if req.kind == "url":
        try:
            url = normalize_url(req.url or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        bare = url.split("://", 1)[1]
        label = bare if len(bare) <= _LABEL_CHARS * 2 else bare[: _LABEL_CHARS * 2] + "…"
        return _Input("url", url.encode("utf-8"), label)
    raise HTTPException(status_code=400, detail=f"暂不支持的来源: {req.kind}")


def _split_refs(refs: list[NoteRef]) -> dict[str, list[NoteRef]]:
    return {
        "created": [r for r in refs if r.status != "inbox"],
        "inbox": [r for r in refs if r.status == "inbox"],
    }


def create_app(vault_root: Path | None = None) -> FastAPI:
    root = Path(vault_root) if vault_root is not None else default_vault_root()
    store = VaultStore(root)
    taxonomy = TaxonomyManager(root / "taxonomy.yaml")
    index = SearchIndex(store)
    organizer = Organizer(store, taxonomy, index)
    jobs = JobRegistry()

    def handle(
        job: QueuedJob, on_stage: Callable[[str], None], on_progress: Callable[[int, int], None]
    ) -> list[str]:
        """队列线程里执行：把一份排队的输入变成笔记。"""
        payload = job.payload or b""
        ids = run_ingest(
            store,
            index,
            organizer,
            kind=job.kind,  # type: ignore[arg-type]
            text=payload.decode("utf-8") if job.kind == "text" else None,
            image=payload if job.kind == "screenshot" else None,
            file=payload if job.kind == "file" else None,
            filename=job.filename,
            url=payload.decode("utf-8") if job.kind == "url" else None,
            on_stage=on_stage,
            on_progress=on_progress,
        )
        index.put_ingest_log(job.input_hash, ids, datetime.now().astimezone().isoformat())
        return ids

    queue = IngestQueue(store.state_dir / "queue.db", handle)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # 启动时以磁盘为准重建索引：用户可能在 Obsidian 里增删改过笔记（SCHEMA 不变量 13）。
        # 放在 lifespan 而不是 create_app 里：导入本模块不该去写 vault
        try:
            logger.info("启动时重建索引: %d 条笔记", index.rebuild_all())
            # 笔记上写着但 taxonomy.yaml 里没有的分类补登记，未锁定的空分类清掉
            organizer.reconcile()
        except Exception:
            logger.exception("启动时重建索引失败，沿用现有索引")
        try:
            pending = queue.resume()
            if pending:
                logger.info("上次没处理完的录入任务: %d 个，继续处理", pending)
        except Exception:
            logger.exception("恢复录入队列失败")
        yield

    app = FastAPI(title="note-agent", lifespan=lifespan)
    app.state.queue = queue

    app.include_router(settings_router)
    app.include_router(build_organize_router(organizer, jobs))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/taxonomy")
    def api_taxonomy() -> Taxonomy:
        return taxonomy.load()

    def job_view(job: QueuedJob) -> JobState:
        # 笔记每次现查：处理完之后被删、被合并的不再列出来，改过的显示改后的样子
        return JobState(
            id=job.id,
            kind=job.kind,
            label=job.label,
            created_at=job.created_at,
            finished_at=job.finished_at,
            status=job.status,
            stage=job.stage,
            duplicate=job.duplicate,
            error=job.error,
            progress=Progress(done=job.progress[0], total=job.progress[1]) if job.progress else None,
            **_split_refs(refs_by_ids(index, job.note_ids)),
        )

    @app.post("/api/ingest", status_code=202)
    def api_ingest(req: IngestRequest) -> dict[str, str]:
        parsed = _parse_ingest(req)
        digest = input_hash(parsed.kind, parsed.payload)
        entry = dict(
            kind=parsed.kind,
            label=parsed.label,
            payload=parsed.payload,
            input_hash=digest,
            filename=parsed.filename,
        )

        if not req.force:
            # 同一份输入还在队列里：不重复排队
            active = queue.find_active(digest)
            if active is not None:
                return {"job_id": active}
            previous = index.get_ingest_log(digest)
            if previous is not None:
                if refs_by_ids(index, previous):
                    return {"job_id": queue.add_duplicate(note_ids=previous, **entry)}
                # 当时建的笔记已全部不在了：按新输入处理
                index.delete_ingest_log(digest)

        return {"job_id": queue.submit(force=req.force, **entry)}

    @app.get("/api/jobs")
    def api_jobs(limit: int = Query(default=30, ge=1, le=200)) -> list[JobState]:
        return [job_view(job) for job in queue.list(limit)]

    @app.delete("/api/jobs")
    def api_clear_jobs() -> dict[str, int]:
        return {"removed": queue.clear_finished()}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str) -> JobState:
        queued = queue.get(job_id)
        if queued is not None:
            return job_view(queued)
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def api_retry_job(job_id: str) -> JobState:
        try:
            queue.retry(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="任务不存在")
        except JobRunning:
            raise HTTPException(status_code=409, detail="这个任务已经在队列里了")
        except JobNotRetryable:
            raise HTTPException(status_code=400, detail="这个任务不能重新处理")
        return api_job(job_id)

    @app.delete("/api/jobs/{job_id}")
    def api_remove_job(job_id: str) -> dict[str, bool]:
        try:
            queue.remove(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="任务不存在")
        except JobRunning:
            raise HTTPException(status_code=409, detail="这个任务正在处理，不能取消")
        return {"removed": True}

    @app.get("/api/search")
    def api_search(
        q: str = "",
        category: str | None = None,
        tag: str | None = None,
        include_inbox: bool = False,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, list[NoteRef] | str | None]:
        results = search(
            index, q, category=category, tag=tag, include_inbox=include_inbox, limit=limit
        )
        return {"results": results, "matched_category": taxonomy.match_category(q)}

    @app.post("/api/ask")
    def api_ask(req: AskRequest) -> AskResult:
        try:
            return answer_question(store, index, req.question, limit=req.limit)
        except Exception as exc:
            logger.exception("问答失败")
            raise HTTPException(status_code=502, detail=f"模型调用失败: {exc}")

    @app.get("/api/graph")
    def api_graph(category: str | None = None) -> Graph:
        return note_graph(index, category or None)

    @app.get("/api/notes")
    def api_notes(
        category: str | None = None,
        status: Literal["inbox"] | None = None,
        duplicates: bool = False,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[NoteRef]:
        return list_recent(
            index, limit, category=category, status=status, duplicates=duplicates
        )

    @app.post("/api/reindex")
    def api_reindex() -> dict[str, int]:
        return {"indexed": index.rebuild_all()}

    @app.get("/api/assets/{name}")
    def api_asset(name: str) -> Response:
        try:
            data = store.read_asset(name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="素材不存在")
        except ValueError:
            raise HTTPException(status_code=400, detail="非法的素材名")
        if PurePath(name).suffix.lower() in (".md", ".markdown", ".txt"):
            # 让浏览器直接显示，而不是当成未知类型去下载
            media_type = "text/plain"
        else:
            media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return Response(content=data, media_type=media_type)

    @app.get("/api/inbox")
    def api_inbox() -> list[NoteRef]:
        return list_inbox(index)

    @app.get("/api/notes/{path:path}")
    def api_note(path: str) -> dict[str, object]:
        try:
            note = store.read_note(path)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="笔记不存在")
        except ValueError:
            raise HTTPException(status_code=400, detail="非法路径")
        return {"frontmatter": note.frontmatter, "body": note.body}

    return app


app = create_app()
