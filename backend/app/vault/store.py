"""vault 文件读写。整个代码库里唯一允许触碰 vault 文件的模块。

- 所有写入先写 .tmp 再 os.replace 原子替换
- 所有 IO 显式 encoding="utf-8"，路径一律 pathlib.Path
- create_note 只创建不覆盖，重名追加 " (2)"、" (3)"
"""

import os
from datetime import datetime
from pathlib import Path

import frontmatter as fm_lib
import yaml

from app.vault.models import Note, NoteFrontmatter, NoteRef

# Windows 非法文件名字符，见 SCHEMA 2.1
_INVALID_FILENAME_CHARS = set('\\/:*?"<>|')


def sanitize_filename(title: str) -> str:
    """按 SCHEMA 2.1：移除非法字符、首尾空格和结尾的 "."。"""
    name = "".join(ch for ch in title if ch not in _INVALID_FILENAME_CHARS)
    name = name.strip().rstrip(". ")
    return name or "untitled"


class VaultStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.notes_dir = self.root / "notes"
        self.inbox_dir = self.root / "inbox"
        self.maps_dir = self.root / "maps"
        self.assets_dir = self.root / "assets"
        self.state_dir = self.root / ".noteagent"

    # ---------- 内部工具 ----------

    def _resolve(self, rel_path: str | Path) -> Path:
        """把相对 vault 根的路径解析成绝对路径，拒绝越界。"""
        rel = Path(rel_path)
        if rel.is_absolute():
            raise ValueError(f"只接受相对 vault 根的路径: {rel_path}")
        target = (self.root / rel).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise ValueError(f"路径越出 vault 根: {rel_path}")
        return target

    def _atomic_write_text(self, target: Path, text: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, target)

    def _atomic_write_bytes(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)

    @staticmethod
    def _serialize_note(note: Note) -> str:
        fm = note.frontmatter.model_dump(mode="python")
        fm["created"] = note.frontmatter.created.isoformat()
        fm["updated"] = note.frontmatter.updated.isoformat()
        yaml_text = yaml.safe_dump(
            fm, sort_keys=False, allow_unicode=True, default_flow_style=None
        )
        return f"---\n{yaml_text}---\n\n{note.body.strip()}\n"

    @staticmethod
    def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
        candidate = directory / f"{stem}{suffix}"
        n = 2
        while candidate.exists():
            candidate = directory / f"{stem} ({n}){suffix}"
            n += 1
        return candidate

    # ---------- 笔记 ----------

    def read_note(self, rel_path: str | Path) -> Note:
        target = self._resolve(rel_path)
        if not target.is_file():
            raise FileNotFoundError(f"笔记不存在: {rel_path}")
        with open(target, encoding="utf-8") as f:
            post = fm_lib.load(f)
        frontmatter = NoteFrontmatter.model_validate(post.metadata)
        return Note(
            frontmatter=frontmatter,
            body=post.content,
            path=target.relative_to(self.root.resolve()),
        )

    def list_notes(self, include_inbox: bool = False) -> list[Path]:
        """返回相对 vault 根的笔记路径。默认只列 notes/。"""
        dirs = [self.notes_dir]
        if include_inbox:
            dirs.append(self.inbox_dir)
        paths: list[Path] = []
        for d in dirs:
            if d.is_dir():
                paths.extend(p.relative_to(self.root) for p in sorted(d.glob("*.md")))
        return paths

    def create_note(self, note: Note) -> Path:
        """只创建新文件，绝不覆盖。status=inbox 落 inbox/，其余落 notes/。"""
        fm = note.frontmatter
        directory = self.inbox_dir if fm.status == "inbox" else self.notes_dir
        stem = sanitize_filename(fm.title)
        target = self._unique_path(directory, stem, ".md")
        self._atomic_write_text(target, self._serialize_note(note))
        rel = target.relative_to(self.root)
        note.path = rel
        return rel

    # ---------- MOC ----------

    def write_moc(self, category: str, refs: list[NoteRef]) -> Path:
        """maps/ 下该分类的索引页，整页重写。"""
        target = self.maps_dir / f"{category.replace('/', '__')}.md"
        lines = [
            f"# {category}",
            "",
            "<!-- 本文件由程序生成，手写内容会在下次重建时丢失 -->",
            "",
        ]
        for ref in refs:
            lines.append(f"- [[{ref.title}]] — {ref.summary}")
        self._atomic_write_text(target, "\n".join(lines) + "\n")
        return target.relative_to(self.root)

    # ---------- 素材 ----------

    def save_asset(self, data: bytes, ext: str) -> str:
        """存入 assets/，返回相对 vault 根的 posix 路径，如 assets/2026-07-30-142211.png。"""
        suffix = "." + ext.lstrip(".").lower()
        stem = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        target = self._unique_path(self.assets_dir, stem, suffix)
        self._atomic_write_bytes(target, data)
        return target.relative_to(self.root).as_posix()
