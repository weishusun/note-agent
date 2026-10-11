import base64
import binascii
import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from app.ask.answer import AskResult, answer_question
from app.config import default_vault_root
from app.ingest.jobs import JobRegistry, JobState
from app.ingest.pipeline import SHORT_TEXT_MAX_CHARS, input_hash, run_ingest
from app.organize.api import build_router as build_organize_router
from app.organize.service import Organizer
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


def _parse_ingest(req: IngestRequest) -> tuple[str | None, bytes | None]:
    """校验请求并取出 (text, image)，恰有一个非空。不合法抛 400。"""
    if req.kind == "text":
        text = (req.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="kind=text 需要非空 text")
        if len(text) >= SHORT_TEXT_MAX_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"暂只支持 {SHORT_TEXT_MAX_CHARS} 字以内的短文本",
            )
        return text, None
    if req.kind == "screenshot":
        if not req.image_b64:
            raise HTTPException(status_code=400, detail="kind=screenshot 需要 image_b64")
        # 允许前端直接传 data URL
        encoded = req.image_b64.split(",", 1)[1] if req.image_b64.startswith("data:") else req.image_b64
        try:
            image = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="image_b64 不是合法的 base64")
        if not image:
            raise HTTPException(status_code=400, detail="image_b64 解码后为空")
        return None, image
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
        yield

    app = FastAPI(title="note-agent", lifespan=lifespan)

    app.include_router(settings_router)
    app.include_router(build_organize_router(organizer, jobs))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/taxonomy")
    def api_taxonomy() -> Taxonomy:
        return taxonomy.load()

    def run_job(job_id: str, kind: SourceKind, text: str | None, image: bytes | None, digest: str) -> None:
        jobs.update(job_id, status="running")
        try:
            ids = run_ingest(
                store,
                index,
                organizer,
                kind=kind,
                text=text,
                image=image,
                on_stage=lambda stage: jobs.update(job_id, stage=stage),
            )
            index.put_ingest_log(digest, ids, datetime.now().astimezone().isoformat())
            jobs.update(job_id, status="done", **_split_refs(refs_by_ids(index, ids)))
        except Exception as exc:
            logger.exception("录入失败 job=%s", job_id)
            jobs.update(job_id, status="failed", error=str(exc))

    @app.post("/api/ingest", status_code=202)
    def api_ingest(req: IngestRequest, background: BackgroundTasks) -> dict[str, str]:
        text, image = _parse_ingest(req)
        digest = input_hash(req.kind, image if image is not None else text.encode("utf-8"))

        if not req.force:
            previous = index.get_ingest_log(digest)
            if previous is not None:
                refs = refs_by_ids(index, previous)
                if refs:
                    job_id = jobs.create(
                        status="done", stage="写入", duplicate=True, **_split_refs(refs)
                    )
                    return {"job_id": job_id}
                # 当时建的笔记已全部不在了：按新输入处理
                index.delete_ingest_log(digest)

        job_id = jobs.create()
        background.add_task(run_job, job_id, req.kind, text, image, digest)
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str) -> JobState:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job

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

    @app.get("/api/notes")
    def api_notes(
        category: str | None = None,
        status: Literal["inbox"] | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[NoteRef]:
        return list_recent(index, limit, category=category, status=status)

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
