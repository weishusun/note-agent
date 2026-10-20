import base64
import binascii
import hashlib
import json
import logging
import mimetypes
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator

from app import settings as app_settings
from app.ask.answer import AskResult, answer_question
from app.ask.expand import Turn
from app.config import default_vault_root
from app.ingest.jobs import JobRegistry, JobState, Progress
from app.ingest.pipeline import SHORT_TEXT_MAX_CHARS, input_hash, run_ingest
from app.ingest.queue import IngestQueue, JobNotRetryable, JobRunning, QueuedJob
from app.ingest.sources import MAX_FILE_BYTES, SUPPORTED_EXTENSIONS, file_extension
from app.ingest.web import normalize_url
from app.organize.api import build_router as build_organize_router
from app.organize.service import Organizer
from app.organize.supplement import BatchScope, Supplementer
from app.search.graph import Graph, note_graph
from app.search.index import SearchIndex
from app.search.vectors import VectorIndex
from app.search.query import NoteTitle, list_inbox, list_recent, note_titles, refs_by_ids, search
from app.settings_api import router as settings_router
from app.mindmaps_api import build_router as build_mindmaps_router
from app.vault.mindmaps import MindmapStore
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
    # 之前的几轮问答（从早到晚）。带上它，这次的问题就按追问处理
    history: list[Turn] = Field(default_factory=list, max_length=50)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question 不能为空白")
        return v.strip()


# 录入完之后排的「模型补充」任务（不是使用者提交的输入）
SUPPLEMENT_KIND = "supplement"
SUPPLEMENT_STAGE = "模型补充"
# 批量补充已有笔记时，每个任务几条
SUPPLEMENT_BATCH = 10


class BatchSupplementRequest(BaseModel):
    # outdated：还没有补充、或只有提取时写的短句的；all：全部重写
    scope: BatchScope = "outdated"

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


def create_app(
    vault_root: Path | None = None, static_dir: Path | None = None, auto_supplement: bool = True
) -> FastAPI:
    """static_dir 给了就连界面一起提供（桌面版）；浏览器开发时界面由 Vite 提供，不用给。

    auto_supplement：录入完是否自动排「模型补充」任务（测试录入流程本身时关掉）。
    """
    root = Path(vault_root) if vault_root is not None else default_vault_root()
    store = VaultStore(root)
    taxonomy = TaxonomyManager(root / "taxonomy.yaml")
    index = SearchIndex(store)
    vectors = VectorIndex(index)
    organizer = Organizer(store, taxonomy, index)
    supplementer = Supplementer(organizer)
    jobs = JobRegistry()

    def handle(
        job: QueuedJob, on_stage: Callable[[str], None], on_progress: Callable[[int, int], None]
    ) -> list[str]:
        """队列线程里执行：把一份排队的输入变成笔记；kind=supplement 的是给刚录入的笔记写模型补充。"""
        payload = job.payload or b""
        if job.kind == SUPPLEMENT_KIND:
            on_stage(SUPPLEMENT_STAGE)
            done, errors = supplementer.supplement_many(json.loads(payload), on_progress)
            if errors:
                raise RuntimeError(f"{len(errors)} 条笔记的模型补充没写成：{errors[0]}")
            return done
        ids = run_ingest(
            store,
            index,
            organizer,
            kind=job.kind,  # type: ignore[arg-type]
            text=payload.decode("utf-8") if job.kind == "text" else None,
            image=payload if job.kind == "screenshot" else None,
            file=payload if job.kind == "file" else None,
            filename=job.filename,
            vectors=vectors,
            url=payload.decode("utf-8") if job.kind == "url" else None,
            on_stage=on_stage,
            on_progress=on_progress,
        )
        index.put_ingest_log(job.input_hash, ids, datetime.now().astimezone().isoformat())
        if auto_supplement:
            queue_supplements(job, ids, payload)
        return ids

    def queue_supplements(job: QueuedJob, ids: list[str], payload: bytes) -> None:
        """录入完排一个「模型补充」任务：笔记先出来，补充随后补上（SCHEMA 2.1）。

        概念笔记都补；主题笔记只补截图和短文本的——长资料的主题笔记是摘要和大纲，补充只会把资料再讲一遍。
        """
        short = job.kind == "screenshot" or (
            job.kind == "text" and len(payload.decode("utf-8").strip()) < SHORT_TEXT_MAX_CHARS
        )
        wanted = [r.id for r in refs_by_ids(index, ids) if r.type == "concept" or short]
        if wanted:
            submit_supplements(wanted, f"模型补充 · {job.label}"[: _LABEL_CHARS + 7])

    def submit_supplements(ids: list[str], label: str) -> str:
        digest = hashlib.sha256(("supplement\0" + ",".join(ids)).encode("utf-8")).hexdigest()
        return queue.submit(
            kind=SUPPLEMENT_KIND, label=label, payload=json.dumps(ids).encode("utf-8"), input_hash=digest
        )

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
    app.state.vectors = vectors

    app.include_router(settings_router)
    app.include_router(build_mindmaps_router(MindmapStore(root), index, root))
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
            return answer_question(
                store, index, req.question, limit=req.limit, vectors=vectors, history=req.history
            )
        except Exception as exc:
            logger.exception("问答失败")
            raise HTTPException(status_code=502, detail=f"模型调用失败: {exc}")

    @app.get("/api/graph")
    def api_graph(category: str | None = None) -> Graph:
        return note_graph(index, category or None)

    @app.get("/api/titles")
    def api_titles() -> list[NoteTitle]:
        """全部笔记的标题和别名：编辑导图、正文时输入 @ 选笔记用（SCHEMA §8）。"""
        return note_titles(index)

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

    @app.get("/api/vectors")
    def api_vectors() -> dict[str, object]:
        """向量索引的状态：{enabled, model, total, embedded}。"""
        return vectors.status()

    @app.post("/api/vectors/sync", status_code=202)
    def api_sync_vectors() -> dict[str, object]:
        """在后台把缺的、过期的向量补上（比如刚在设置页填了向量模型）。马上返回当前状态。"""
        vectors.request_sync()
        return vectors.status()

    @app.get("/api/supplements")
    def api_supplement_candidates(scope: BatchScope = "outdated") -> dict[str, int]:
        """批量补充之前先看看有多少条要补。"""
        return {"count": len(supplementer.candidates(scope))}

    @app.post("/api/supplements", status_code=202)
    def api_batch_supplement(req: BatchSupplementRequest) -> dict[str, int]:
        """给已有的笔记批量写模型补充：每 SUPPLEMENT_BATCH 条排一个任务，排在队尾。
        分成小批是为了某条失败时重试的代价小，进度也看得清楚。"""
        ids = supplementer.candidates(req.scope)
        batches = [ids[i : i + SUPPLEMENT_BATCH] for i in range(0, len(ids), SUPPLEMENT_BATCH)]
        for n, batch in enumerate(batches, 1):
            part = f" {n}/{len(batches)}" if len(batches) > 1 else ""
            submit_supplements(batch, f"模型补充 · 已有笔记{part}（{len(batch)} 条）")
        return {"count": len(ids), "jobs": len(batches)}

    @app.get("/api/locations")
    def api_locations() -> dict[str, str]:
        # 设置页显示用：知识库在哪、设置（含密钥）存在哪
        return {"vault": str(root.resolve()), "settings": str(app_settings.settings_path())}

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

    if static_dir is not None:
        # 放在最后：兜底路由不能挡在接口前面
        _serve_frontend(app, static_dir)
    return app


def _serve_frontend(app: FastAPI, static_dir: Path) -> None:
    """界面的构建产物和接口同源提供。找不到的路径一律回 index.html。"""
    root = Path(static_dir).resolve()
    index_html = root / "index.html"
    if not index_html.is_file():
        raise RuntimeError(f"找不到界面文件: {index_html}")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        target = (root / path).resolve()
        if path and target.is_relative_to(root) and target.is_file():
            return FileResponse(target)
        # 重新打包后要拿到新的 index.html，它引用的文件名带哈希，可以放心缓存
        return FileResponse(index_html, headers={"Cache-Control": "no-cache"})


app = create_app()
