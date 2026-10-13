"""app/ingest/sources.py：把上传的文件读成纯文本。"""

import pytest

from app.ingest.sources import SourceError, file_extension, read_document
from tests.conftest import make_pdf


def test_markdown_and_text_are_decoded_and_tidied() -> None:
    raw = "﻿# 第一章 注意力机制\r\n\r\n\r\n\r\n正文的第一行  \r\n正文的第二行\x00"
    doc = read_document("讲义.MD", raw.encode("utf-8"))
    assert (doc.title, doc.ext) == ("讲义", ".md")
    # BOM、\r、行尾空格、多余空行、NUL 都收拾掉
    assert doc.text == "# 第一章 注意力机制\n\n正文的第一行\n正文的第二行"


def test_gbk_text_is_still_readable() -> None:
    text = "这是一份用 GBK 存的记事本文件，内容足够长才不会被当成空文件。"
    assert read_document("旧文件.txt", text.encode("gbk")).text == text


def test_pdf_text_is_extracted_page_by_page() -> None:
    doc = read_document(
        "paper.pdf", make_pdf(["Attention Is All You Need", "The Transformer architecture"], pages=2)
    )
    assert (doc.title, doc.ext) == ("paper", ".pdf")
    assert doc.text.count("Attention Is All You Need") == 2  # 两页各一份
    assert "The Transformer architecture" in doc.text


@pytest.mark.parametrize(
    "filename, data, message",
    [
        ("扫描件.pdf", make_pdf([]), "扫描件"),
        ("坏的.pdf", b"%PDF-1.4 this is not really a pdf", "读不了"),
        ("空的.txt", b"   \n\n", "几乎没有文字"),
        ("表格.xlsx", b"PK\x03\x04", "暂不支持 .xlsx"),
        ("没有扩展名", b"hello", "没有扩展名"),
    ],
)
def test_unreadable_files_are_reported_in_plain_words(filename: str, data: bytes, message: str) -> None:
    with pytest.raises(SourceError, match=message):
        read_document(filename, data)


def test_file_extension_is_case_insensitive() -> None:
    assert file_extension("A.B.PDF") == ".pdf"
    assert file_extension("README") == ""
