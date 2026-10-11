"""vault 文件读写。整个代码库里唯一允许触碰 vault 文件的模块。

- 所有写入先写 .tmp 再 os.replace 原子替换
- 所有 IO 显式 encoding="utf-8"，路径一律 pathlib.Path
- create_note 只创建不覆盖，重名追加 " (2)"、" (3)"
- 笔记文件创建后不移动：分类和状态只写在 frontmatter 里（SCHEMA 第 1 节）
"""

import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import frontmatter as fm_lib
import yaml

from app.vault.ids import new_ulid
from app.vault.models import Note, NoteFrontmatter, NoteRef

# Windows 非法文件名字符，见 SCHEMA 2.1
_INVALID_FILENAME_CHARS = set('\\/:*?"<>|')

# _unique_path 抢占位的最大尝试次数，防止病态情况下无限循环
_MAX_UNIQUE_ATTEMPTS = 1000


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
        # tmp 名带 pid + uuid：并发写同一个 target 时各写各的 tmp，不互踩。
        # 固定名 <target>.tmp 在 Windows 上会直接 PermissionError。
        tmp = target.with_name(f"{target.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(tmp, target)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

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
    def _unique_path(
        directory: Path, stem: str, suffix: str, also_avoid: tuple[Path, ...] = ()
    ) -> Path:
        """独占创建一个 0 字节占位文件并返回它的路径，重名按 " (2)"、" (3)" 递增。

        also_avoid：别的目录里已有同名文件时也要跳过。双链只写文件名、不带目录，
        notes/ 和 inbox/ 里各有一个「RAG.md」的话链接就有歧义了。

        不用 exists() 判断：check-then-act 在并发下所有调用方都会看到「文件不存在」、
        全都选中同一个路径，再被 os.replace 静默互相覆盖。改成 open(..., "x")
        把唯一性裁决交给内核。

        调用方拿到占位后必须负责写入内容，中途失败要 unlink 掉占位文件。
        """
        directory.mkdir(parents=True, exist_ok=True)
        n = 1
        for _ in range(_MAX_UNIQUE_ATTEMPTS):
            name = f"{stem}{suffix}" if n == 1 else f"{stem} ({n}){suffix}"
            candidate = directory / name
            if any((other / name).exists() for other in also_avoid):
                n += 1
                continue
            try:
                with open(candidate, "x", encoding="utf-8"):
                    pass
            except FileExistsError:
                n += 1
                continue
            return candidate
        raise RuntimeError(
            f"{_MAX_UNIQUE_ATTEMPTS} 次内没能为 {stem}{suffix} 抢到唯一文件名: {directory}"
        )

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
        """只创建新文件，绝不覆盖。一律落 notes/，待整理的也一样（SCHEMA 第 1 节）。

        id 和 category_locked 是创建时语义，一律在这里定，忽略调用方传入的值。
        id 放在写入层而不是模型层生成，是因为模型层的 default_factory 会在解析时也触发——
        读一篇没有 id 键的笔记会被当场捏一个新 id，读两次还不一样（SCHEMA 2.2）。
        """
        note.frontmatter = note.frontmatter.model_copy(
            update={"id": new_ulid(), "category_locked": False}
        )
        fm = note.frontmatter
        stem = sanitize_filename(fm.title)
        target = self._unique_path(
            self.notes_dir, stem, ".md", also_avoid=(self.inbox_dir,)
        )
        try:
            self._atomic_write_text(target, self._serialize_note(note))
        except BaseException:
            # 占位抢到了但内容没写成：不能在 notes/ 里留下 0 字节的 .md
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        rel = target.relative_to(self.root)
        note.path = rel
        return rel

    def next_stem(self, title: str) -> str:
        """create_note 会给这个标题分配的文件名（不含 .md）。只看不占：
        建主题笔记之前，它的概念笔记就要先写上指向它的链接。
        """
        stem = sanitize_filename(title)
        n = 1
        while True:
            name = stem if n == 1 else f"{stem} ({n})"
            if not any((d / f"{name}.md").exists() for d in (self.notes_dir, self.inbox_dir)):
                return name
            n += 1

    def update_frontmatter(self, rel_path: str | Path, **changes: object) -> Note:
        """改写一条已有笔记的 frontmatter 字段，正文和文件名不动。"""
        return self.rewrite_note(rel_path, **changes)

    def rewrite_note(
        self, rel_path: str | Path, *, body: str | None = None, **changes: object
    ) -> Note:
        """原地改写一条已有笔记：body 给了就换正文，changes 是要改的 frontmatter 字段。

        文件名不动。改完的 frontmatter 会重新走一遍模型校验；旧版本留下的多余键在这次改写中消失。
        """
        note = self.read_note(rel_path)
        merged = {**note.frontmatter.model_dump(mode="python"), **changes}
        note.frontmatter = NoteFrontmatter.model_validate(merged)
        if body is not None:
            note.body = body
        self._atomic_write_text(self._resolve(rel_path), self._serialize_note(note))
        return note

    def rename_note(self, rel_path: str | Path, new_stem: str) -> Path:
        """把笔记文件改名（留在原目录里），返回新的相对路径。新名字已被占用时抛 FileExistsError。

        只动文件名。其他笔记里指向它的链接由调用方随后用 replace_links 改写（SCHEMA 2.6）。
        """
        source = self._resolve(rel_path)
        if not source.is_file():
            raise FileNotFoundError(f"笔记不存在: {rel_path}")
        name = f"{new_stem}.md"
        if any((d / name).exists() for d in (self.notes_dir, self.inbox_dir)):
            raise FileExistsError(f"文件名已被占用: {name}")
        target = source.with_name(name)
        os.replace(source, target)
        return target.relative_to(self.root.resolve())

    def delete_note(self, rel_path: str | Path) -> None:
        target = self._resolve(rel_path)
        if not target.is_file():
            raise FileNotFoundError(f"笔记不存在: {rel_path}")
        target.unlink()

    def replace_links(self, old_stem: str, new_stem: str | None) -> list[Note]:
        """改写全库正文里指向 old_stem 的 [[双链]]，返回被改写的笔记（调用方据此更新索引）。

        new_stem 给了就改成指向它（[[旧|显示名]] 的显示名保留）；为 None 表示目标已被删除，
        链接退化成纯文字：[[旧]] → 旧，[[旧|显示名]] → 显示名。读不了的笔记文件跳过。
        """
        pattern = re.compile(r"\[\[" + re.escape(old_stem) + r"(?:\|([^\]]*))?\]\]")

        def substitute(match: re.Match[str]) -> str:
            label = match.group(1)
            if new_stem is None:
                return label if label is not None else old_stem
            return f"[[{new_stem}|{label}]]" if label is not None else f"[[{new_stem}]]"

        rewritten: list[Note] = []
        for rel in self.list_notes(include_inbox=True):
            try:
                note = self.read_note(rel)
            except (ValueError, OSError, yaml.YAMLError):
                continue
            body = pattern.sub(substitute, note.body)
            if body != note.body:
                rewritten.append(self.rewrite_note(rel, body=body))
        return rewritten

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

    def read_asset(self, name: str) -> bytes:
        """读 assets/ 下的一个文件，供 HTTP 层回传截图。只接受文件名，不接受路径。"""
        if not name or Path(name).name != name:
            raise ValueError(f"非法的素材名: {name}")
        target = self.assets_dir / name
        if not target.is_file():
            raise FileNotFoundError(f"素材不存在: {name}")
        with open(target, "rb") as f:
            return f.read()

    def save_asset(self, data: bytes, ext: str) -> str:
        """存入 assets/，返回相对 vault 根的 posix 路径，如 assets/2026-07-30-142211.png。"""
        suffix = "." + ext.lstrip(".").lower()
        stem = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        target = self._unique_path(self.assets_dir, stem, suffix)
        try:
            self._atomic_write_bytes(target, data)
        except BaseException:
            # 同 create_note：_unique_path 抢到的是占位文件，写失败要清掉
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return target.relative_to(self.root).as_posix()
