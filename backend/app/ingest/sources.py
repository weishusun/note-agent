"""把上传的文件读成纯文本（SCHEMA 2.4）。纯函数，不读写 vault，不调用模型。

支持 PDF、Markdown、纯文本。扫描件 PDF（没有文字层）读不出字，直接报错，不做 OCR。
"""

import io
import logging
import re
from dataclasses import dataclass
from pathlib import PurePath

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = (".pdf", ".md", ".markdown", ".txt")
MAX_FILE_BYTES = 30 * 1024 * 1024
# 平均每页不到这么多字，就当它是扫描件
_MIN_CHARS_PER_PAGE = 20
_MIN_CHARS = 20


class SourceError(Exception):
    """输入本身的问题（读不了、是空的、不支持）。消息会原样显示给使用者。"""


@dataclass(frozen=True)
class Document:
    # 文件名去掉扩展名，给模型起标题时参考
    title: str
    text: str
    ext: str


def file_extension(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def read_document(filename: str, data: bytes) -> Document:
    ext = file_extension(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise SourceError(f"暂不支持 {ext or '没有扩展名'} 的文件，目前支持 PDF、Markdown 和纯文本")
    text = _read_pdf(data) if ext == ".pdf" else _decode(data)
    text = _tidy(text)
    if len(text) < _MIN_CHARS:
        raise SourceError("文件里几乎没有文字")
    return Document(title=PurePath(filename).stem, text=text, ext=ext)


def _read_pdf(data: bytes) -> str:
    # 惰性导入：只有处理 PDF 时才需要这个库
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise SourceError("这个 PDF 有密码保护，读不了")
        pages = [page.extract_text() or "" for page in reader.pages]
    except SourceError:
        raise
    except (PyPdfError, ValueError, OSError, KeyError) as exc:
        logger.warning("读取 PDF 失败: %s", exc)
        raise SourceError("这个 PDF 读不了，文件可能已经损坏") from exc
    text = "\n\n".join(page.strip() for page in pages if page.strip())
    if len(text) < max(_MIN_CHARS, _MIN_CHARS_PER_PAGE * len(pages)):
        raise SourceError("这个 PDF 里读不出文字，多半是扫描件。可以改用截图录入")
    return text


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()
