"""app/ingest/web.py：抓取网页并取出正文。网络用 httpx 的 MockTransport 替掉，不发真实请求。"""

import httpx
import pytest

from app.ingest.sources import SourceError
from app.ingest.web import extract_article, fetch_page, normalize_url
from tests.conftest import make_pdf

PARAGRAPH = "注意力机制让模型在处理一个位置时，可以直接参考序列里所有其他位置的信息。" * 6

ARTICLE_PAGE = f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>注意力机制详解 - 某某技术博客</title>
<style>body {{ color: red }}</style>
<script>var tracking = "不该出现的脚本内容";</script>
</head><body>
<header><h1>某某技术博客</h1><nav><a href="/">首页</a><a href="/about">关于</a></nav></header>
<aside><ul><li><a href="/a">推荐阅读一</a></li><li><a href="/b">推荐阅读二</a></li></ul></aside>
<article>
  <h1>注意力机制详解</h1>
  <p>{PARAGRAPH}</p>
  <h2>自注意力</h2>
  <ul><li>可以并行计算</li><li>参见 <a href="/t">Transformer</a> 一文</li></ul>
  <p>第二段&amp;结论：<b>长距离依赖</b>不再是问题。<br>换行之后的内容。</p>
</article>
<footer>版权所有 © 某某</footer>
</body></html>"""


def _transport(body: bytes | str, status: int = 200, content_type: str = "text/html; charset=utf-8"):
    content = body.encode("utf-8") if isinstance(body, str) else body
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=content, headers={"content-type": content_type})

    transport = httpx.MockTransport(handler)
    transport.seen = seen  # type: ignore[attr-defined]
    return transport


def test_article_text_is_extracted_without_navigation_scripts_or_footer() -> None:
    transport = _transport(ARTICLE_PAGE)
    page = fetch_page("https://blog.example.com/attention", transport=transport)

    assert (page.title, page.ext) == ("注意力机制详解", ".html")  # <title> 去掉了「 - 网站名」
    assert page.text == (
        f"# 注意力机制详解\n\n{PARAGRAPH}\n\n## 自注意力\n\n- 可以并行计算\n\n"
        "- 参见 Transformer 一文\n\n第二段&结论：长距离依赖不再是问题。\n换行之后的内容。"
    )
    # 像浏览器一样发请求：有些网站对不像浏览器的请求直接拒绝
    assert "Mozilla" in transport.seen[0].headers["user-agent"]


def test_page_without_article_tag_falls_back_to_the_whole_body_minus_link_menus() -> None:
    menu = "".join(f'<li><a href="/{i}">栏目{i}</a></li>' for i in range(6))
    html = (
        "<html><head><title>一篇文章</title></head><body>"
        f"<div><ul>{menu}</ul></div>"
        '<div><a href="/x">教程一</a> <a href="/y">教程二</a> <a href="/z">教程三</a> <a href="/w">教程四</a></div>'
        f"<div><p>{PARAGRAPH}</p><p>正文里提到的<a href='/ref'>参考资料</a>要留着。</p>"
        '<p><a href="/single">单独一行的链接也留着</a></p></div>'
        "</body></html>"
    )
    title, body = extract_article(html)
    assert title == "一篇文章"
    assert "栏目" not in body and "教程" not in body  # 成片的、横排的链接菜单都去掉
    assert body == f"{PARAGRAPH}\n\n正文里提到的参考资料要留着。\n\n单独一行的链接也留着"


def test_title_prefers_og_title_then_title_tag_then_h1() -> None:
    og = '<head><meta property="og:title" content="OG 标题"><title>页面标题 | 网站</title></head><h1>站名</h1>'
    assert extract_article(og)[0] == "OG 标题"
    assert extract_article("<title>页面标题 | 网站</title><h1>站名</h1>")[0] == "页面标题"
    assert extract_article("<title>没有后缀的标题</title>")[0] == "没有后缀的标题"
    assert extract_article("<body><h1>只有一个 h1¶</h1></body>")[0] == "只有一个 h1"


def test_gbk_page_declared_only_in_meta_is_decoded() -> None:
    html = f'<html><head><meta http-equiv="Content-Type" content="text/html; charset=gbk"></head><body><p>{PARAGRAPH}</p></body></html>'
    page = fetch_page("http://old.example.com/", transport=_transport(html.encode("gbk"), content_type="text/html"))
    assert page.text == PARAGRAPH


def test_plain_text_response_is_used_as_is() -> None:
    page = fetch_page(
        "https://example.com/readme.txt",
        transport=_transport(f"第一行\r\n\r\n{PARAGRAPH}", content_type="text/plain; charset=utf-8"),
    )
    assert (page.title, page.text) == ("", f"第一行\n\n{PARAGRAPH}")


def test_link_to_a_pdf_is_read_as_a_pdf() -> None:
    pdf = make_pdf(["Attention Is All You Need", "Vaswani et al."])
    page = fetch_page(
        "https://arxiv.org/pdf/1706.03762", transport=_transport(pdf, content_type="application/pdf")
    )
    assert (page.title, page.ext) == ("1706.03762", ".pdf")
    assert "Attention Is All You Need" in page.text


@pytest.mark.parametrize(
    "transport, message",
    [
        (_transport("<html><body><div id='app'></div><script>render()</script></body></html>"), "抓不到正文"),
        (_transport("请先登录", status=403), "拒绝了访问"),
        (_transport("没有这个页面", status=404), "HTTP 404"),
        (_transport(b"\x89PNG....", content_type="image/png"), "不是网页"),
    ],
)
def test_pages_that_cannot_be_read_say_why(transport, message: str) -> None:
    with pytest.raises(SourceError, match=message):
        fetch_page("https://example.com/page", transport=transport)


def test_network_failures_are_reported_in_plain_words() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("连接被拒绝")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("太慢了")

    with pytest.raises(SourceError, match="打不开"):
        fetch_page("https://example.com/", transport=httpx.MockTransport(refuse))
    with pytest.raises(SourceError, match="超时"):
        fetch_page("https://example.com/", transport=httpx.MockTransport(slow))


def test_normalize_url() -> None:
    assert normalize_url("  https://example.com/a?b=1#第二节 ") == "https://example.com/a?b=1"
    assert normalize_url("http://localhost:8080") == "http://localhost:8080"
    for bad in ("", "example.com", "ftp://example.com/x", "https://", "https://a.com/有 空格", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            normalize_url(bad)
    with pytest.raises(ValueError):
        normalize_url("https://example.com/" + "a" * 2000)
