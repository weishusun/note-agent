from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from app.config import default_vault_root
from app.search.index import SearchIndex
from app.search.query import search
from app.vault.models import NoteRef, Taxonomy
from app.vault.store import VaultStore
from app.vault.taxonomy import TaxonomyManager


def create_app(vault_root: Path | None = None) -> FastAPI:
    root = Path(vault_root) if vault_root is not None else default_vault_root()
    store = VaultStore(root)
    taxonomy = TaxonomyManager(root / "taxonomy.yaml")
    index = SearchIndex(store)

    app = FastAPI(title="note-agent")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/taxonomy")
    def api_taxonomy() -> Taxonomy:
        return taxonomy.load()

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
        matched = category if taxonomy.is_valid_category(category) else None
        return {"results": results, "matched_category": matched}

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
