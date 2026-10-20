"""「我的导图」的接口（SCHEMA §8）。"""

import logging
import subprocess
import time
from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.search.index import SearchIndex
from app.search.query import refs_by_ids
from app.vault import git
from app.vault.ids import new_ulid
from app.vault.mindmaps import TITLE_MAX, Mindmap, MindmapBody, MindmapNode, MindmapStore, MindmapSummary
from app.vault.models import NoteRef

logger = logging.getLogger(__name__)

# 编辑时每改一下就自动保存一次：git 提交不跟着每次保存，同一张导图隔这么久才提交一次
COMMIT_INTERVAL_SECONDS = 600


class MindmapCreate(BaseModel):
    """只给标题就是一张只有根节点的空导图；也可以直接带上内容（比如从分类生成的初稿）。"""

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    root: str | None = None
    nodes: dict[str, MindmapNode] | None = None


class MindmapView(BaseModel):
    map: Mindmap
    # 导图里链到的笔记现在的样子；已经删掉的不在里面
    notes: dict[str, NoteRef]


def build_router(store: MindmapStore, index: SearchIndex, vault_root, clock: Callable[[], float] = time.monotonic) -> APIRouter:
    router = APIRouter()
    last_commit: dict[str, float] = {}

    def commit(message: str) -> None:
        try:
            git.commit(vault_root, message)
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("vault git 提交失败: %s", exc)

    def view(mindmap: Mindmap) -> MindmapView:
        ids = sorted({n.link.id for n in mindmap.nodes.values() if n.link is not None})
        return MindmapView(map=mindmap, notes={r.id: r for r in refs_by_ids(index, ids)})

    def load(map_id: str) -> Mindmap:
        try:
            return store.get(map_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="导图不存在")
        except ValueError:
            raise HTTPException(status_code=400, detail="非法的导图 id")

    @router.get("/api/mindmaps")
    def api_list() -> list[MindmapSummary]:
        return store.list()

    @router.post("/api/mindmaps", status_code=201)
    def api_create(req: MindmapCreate) -> MindmapView:
        now = datetime.now().astimezone()
        if req.nodes is None:
            body = MindmapBody(title=req.title, root="root", nodes={"root": MindmapNode(text=req.title.strip())})
        else:
            try:
                body = MindmapBody(title=req.title, root=req.root or "", nodes=req.nodes)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        mindmap = store.save(Mindmap(id=new_ulid(), created=now, updated=now, **body.model_dump()))
        commit(f"mindmap: 新建「{mindmap.title}」")
        last_commit[mindmap.id] = clock()
        return view(mindmap)

    @router.get("/api/mindmaps/{map_id}")
    def api_get(map_id: str) -> MindmapView:
        return view(load(map_id))

    @router.put("/api/mindmaps/{map_id}")
    def api_save(map_id: str, body: MindmapBody) -> MindmapView:
        current = load(map_id)
        mindmap = store.save(
            Mindmap(
                id=current.id,
                created=current.created,
                updated=datetime.now().astimezone(),
                **body.model_dump(),
            )
        )
        if clock() - last_commit.get(map_id, float("-inf")) >= COMMIT_INTERVAL_SECONDS:
            commit(f"mindmap: 编辑「{mindmap.title}」")
            last_commit[map_id] = clock()
        return view(mindmap)

    @router.delete("/api/mindmaps/{map_id}")
    def api_delete(map_id: str) -> dict[str, bool]:
        mindmap = load(map_id)
        store.delete(map_id)
        commit(f"mindmap: 删除「{mindmap.title}」")
        last_commit.pop(map_id, None)
        return {"deleted": True}

    return router
