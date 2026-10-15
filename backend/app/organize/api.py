"""目录树与分类调整的接口（SCHEMA 第 5 节）。

分类名含中文和「/」，所以分类路径一律放在请求体或查询参数里，不放进 URL 路径。
"""

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.ingest.jobs import JobRegistry, Progress
from app.organize.editor import NoteEditor
from app.organize.service import CategoryNotEmpty, NoteNotFound, Organizer, ReclassifyRunning
from app.organize.supplement import Supplementer
from app.search.query import find_by_id
from app.vault.models import NoteRef, Tree
from app.vault.taxonomy import CategoryExists, CategoryNotFound

logger = logging.getLogger(__name__)


class NoteCategoryBody(BaseModel):
    category: str


class NoteCreateBody(BaseModel):
    # 约束在 NoteEditor 里校验并返回 400；这里不加，免得 FastAPI 先一步回 422
    title: str
    summary: str
    body: str
    aliases: list[str] = []
    category: str | None = None


class NoteEditBody(BaseModel):
    """只带要改的字段。"""

    title: str | None = None
    summary: str | None = None
    aliases: list[str] | None = None
    body: str | None = None


class NoteMergeBody(BaseModel):
    into: str


class CategoryCreateBody(BaseModel):
    path: str


class CategoryRenameBody(BaseModel):
    path: str
    name: str


class ReclassifyBody(BaseModel):
    scope: Literal["unsorted", "all"]


def build_router(organizer: Organizer, jobs: JobRegistry) -> APIRouter:
    router = APIRouter()
    editor = NoteEditor(organizer)
    supplementer = Supplementer(organizer)

    @router.get("/api/refs/{note_id}")
    def api_ref(note_id: str) -> NoteRef:
        ref = find_by_id(organizer.index, note_id)
        if ref is None:
            raise HTTPException(status_code=404, detail="笔记不存在")
        return ref

    @router.post("/api/notes", status_code=201)
    def api_create_note(body: NoteCreateBody) -> NoteRef:
        try:
            return editor.create(
                title=body.title,
                summary=body.summary,
                body=body.body,
                aliases=body.aliases,
                category=body.category,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.put("/api/notes/{note_id}")
    def api_edit_note(note_id: str, body: NoteEditBody) -> NoteRef:
        try:
            return editor.edit(note_id, **body.model_dump(exclude_unset=True))
        except NoteNotFound:
            raise HTTPException(status_code=404, detail="笔记不存在")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.delete("/api/notes/{note_id}")
    def api_delete_note(note_id: str, replaced_by: str | None = None) -> dict[str, bool]:
        try:
            editor.delete(note_id, replaced_by=replaced_by or None)
        except NoteNotFound:
            raise HTTPException(status_code=404, detail="笔记不存在")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"deleted": True}

    @router.post("/api/notes/{note_id}/merge")
    def api_merge_note(note_id: str, body: NoteMergeBody) -> NoteRef:
        try:
            return editor.merge(note_id, body.into)
        except NoteNotFound:
            raise HTTPException(status_code=404, detail="笔记不存在")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/notes/{note_id}/keep")
    def api_keep_note(note_id: str) -> NoteRef:
        try:
            return editor.keep(note_id)
        except NoteNotFound:
            raise HTTPException(status_code=404, detail="笔记不存在")

    @router.post("/api/notes/{note_id}/supplement")
    def api_supplement_note(note_id: str) -> NoteRef:
        """（重新）写这条笔记的模型补充，写完才返回（十几到几十秒）。原来的补充整段换掉。"""
        try:
            return supplementer.supplement(note_id)
        except NoteNotFound:
            raise HTTPException(status_code=404, detail="笔记不存在")
        except Exception as exc:
            logger.exception("模型补充失败: %s", note_id)
            raise HTTPException(status_code=502, detail=f"模型补充没写成：{str(exc)[:300]}")

    @router.get("/api/tree")
    def api_tree() -> Tree:
        return organizer.tree()

    @router.put("/api/notes/{note_id}/category")
    def api_move_note(note_id: str, body: NoteCategoryBody) -> NoteRef:
        try:
            return organizer.move_note(note_id, body.category)
        except NoteNotFound:
            raise HTTPException(status_code=404, detail="笔记不存在")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/categories")
    def api_create_category(body: CategoryCreateBody) -> Tree:
        try:
            organizer.create_category(body.path)
        except CategoryExists as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return organizer.tree()

    @router.put("/api/categories")
    def api_rename_category(body: CategoryRenameBody) -> Tree:
        try:
            organizer.rename_category(body.path, body.name)
        except CategoryNotFound:
            raise HTTPException(status_code=404, detail="分类不存在")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return organizer.tree()

    @router.delete("/api/categories")
    def api_delete_category(path: str) -> Tree:
        try:
            organizer.delete_category(path)
        except CategoryNotFound:
            raise HTTPException(status_code=404, detail="分类不存在")
        except CategoryNotEmpty as exc:
            raise HTTPException(status_code=409, detail=f"「{exc}」里还有笔记，先把它们移走")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return organizer.tree()

    def run_reclassify(job_id: str, scope: str) -> None:
        jobs.update(job_id, status="running")
        try:
            organizer.reclassify(
                scope,
                on_progress=lambda done, total: jobs.update(
                    job_id, progress=Progress(done=done, total=total)
                ),
            )
            jobs.update(job_id, status="done")
        except Exception as exc:
            logger.exception("重新归类失败 job=%s", job_id)
            jobs.update(job_id, status="failed", error=str(exc))

    @router.post("/api/reclassify", status_code=202)
    def api_reclassify(body: ReclassifyBody, background: BackgroundTasks) -> dict[str, str]:
        try:
            organizer.start_reclassify()
        except ReclassifyRunning:
            raise HTTPException(status_code=409, detail="已经有一个重新归类的任务在进行")
        job_id = jobs.create(stage="归类", progress=Progress(done=0, total=0))
        background.add_task(run_reclassify, job_id, body.scope)
        return {"job_id": job_id}

    return router
