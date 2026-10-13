"""把一个网址读成纯文本（SCHEMA 2.4）：抓取网页、去掉导航和脚本、取出正文。不读写 vault，不调用模型。

只做一次普通的 HTTP 请求，不执行页面里的脚本、不带登录状态——所以需要登录的页面、
内容靠脚本加载的页面抓不到正文，这种情况报 SourceError，让使用者改用截图。
"""

import logging
import re
from html.parser import HTMLParser
from urllib.parse import urldefrag, urlsplit

import httpx

from app.ingest.sources import MAX_FILE_BYTES, Document, SourceError, _tidy, read_document

logger = logging.getLogger(__name__)

URL_MAX_CHARS = 2000
_TIMEOUT_SECONDS = 20.0
_MAX_HTML_BYTES = 5 * 1024 * 1024
# 正文不到这么多字，就当没抓到
_MIN_TEXT_CHARS = 200
# 有些网站对不像浏览器的请求直接拒绝
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
        " Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,text/plain;q=0.8,*/*;q=0.5",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_NO_TEXT = "这个网页抓不到正文，可能需要登录，或者内容是靠脚本加载的。可以改用截图录入"


def normalize_url(url: str) -> str:
    """校验并规整网址：只接受 http / https，去掉 # 后面的片段（它不影响抓到的内容）。不合法抛 ValueError。"""
    url, _fragment = urldefrag((url or "").strip())
    if len(url) > URL_MAX_CHARS or re.search(r"\s", url):
        raise ValueError("这不是一个合法的网址")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("网址要以 http:// 或 https:// 开头")
    return url


def fetch_page(url: str, transport: httpx.BaseTransport | None = None) -> Document:
    """抓取 url 并取出正文。transport 只在测试里给，用来替掉真实的网络。"""
    try:
        with httpx.Client(
            follow_redirects=True, timeout=_TIMEOUT_SECONDS, headers=_HEADERS, transport=transport
        ) as client, client.stream("GET", url) as response:
            if response.status_code in (401, 403):
                raise SourceError(
                    f"这个网站拒绝了访问（HTTP {response.status_code}），多半需要登录或者限制了程序抓取。可以改用截图录入"
                )
            if response.status_code >= 400:
                raise SourceError(f"网页打不开（HTTP {response.status_code}）")
            content_type = response.headers.get("content-type", "").lower()
            is_pdf = "application/pdf" in content_type or urlsplit(url).path.lower().endswith(".pdf")
            limit = MAX_FILE_BYTES if is_pdf else _MAX_HTML_BYTES
            data = b""
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > limit:
                    if is_pdf:
                        raise SourceError(f"这个 PDF 太大了，最大 {limit // 1024 // 1024} MB")
                    break  # 网页只看前面这么多，正文一般都在
            charset = response.charset_encoding
    except SourceError:
        raise
    except httpx.TimeoutException as exc:
        raise SourceError("网页打开超时了，过一会儿再试，或者改用截图") from exc
    except httpx.HTTPError as exc:
        logger.warning("抓取 %s 失败: %r", url, exc)
        raise SourceError("网页打不开，检查一下网址和网络") from exc

    if is_pdf:
        name = urlsplit(url).path.rsplit("/", 1)[-1] or "document"
        return read_document(name if name.lower().endswith(".pdf") else name + ".pdf", data)
    if content_type and not any(kind in content_type for kind in ("html", "xml", "text/plain", "markdown")):
        raise SourceError("这个链接指向的不是网页，也不是 PDF")

    text = _decode(data, charset)
    if "text/plain" in content_type or "markdown" in content_type:
        title, body = "", _tidy(text)
    else:
        title, body = extract_article(text)
    if len(body) < _MIN_TEXT_CHARS:
        raise SourceError(_NO_TEXT)
    return Document(title=title, text=body, ext=".html")


_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)


def _decode(data: bytes, declared: str | None) -> str:
    """响应头里的编码优先，其次是页面自己在 <meta> 里声明的，再不行依次试 UTF-8 和 GB18030。"""
    candidates = [declared] if declared else []
    match = _META_CHARSET.search(data[:4096])
    if match:
        candidates.append(match.group(1).decode("ascii", errors="ignore"))
    for encoding in [*candidates, "utf-8", "gb18030"]:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


# 这些标签里的东西不是正文
_SKIP = frozenset(
    "script style noscript template svg canvas iframe nav header footer aside button"
    " select textarea dialog menu figure".split()
)
# 不成对出现的标签：没有结束标签，不能用来配对计数
_VOID = frozenset("br hr img input meta link source track wbr area base col embed param".split())
_BLOCK = frozenset(
    "p div section article main li ul ol tr table blockquote pre h1 h2 h3 h4 h5 h6 dd dt br hr".split()
)
_HEADINGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "#### ", "h6": "#### "}
_SITE_SUFFIX = re.compile(r"\s*[\-|_–—·]\s*[^\-|_–—·]{1,30}$")
# 链接里的文字用这两个控制字符包起来，_clean 靠它认出「整行都是链接」的导航项，最后再去掉
_LINK_OPEN, _LINK_CLOSE = "\x01", "\x02"
# 连着这么多行都是链接，才当成导航、目录、推荐阅读整片去掉；正文里零星的链接行留着
_NAV_RUN = 3
_NOT_CONTENT = re.compile(r"[\s\-#|»›·•/>¶]+")


class _TextExtractor(HTMLParser):
    """把 HTML 走一遍，收集可见文字。分别记下整页的文字和 <article> / <main> 里的文字。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.og_title = ""
        self.h1 = ""
        self.all: list[str] = []
        self.article: list[str] = []
        self._skip_depth = 0
        self._article_depth = 0
        self._in_title = False
        self._in_h1 = False
        self._link_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            fields = dict(attrs)
            if fields.get("property") == "og:title" and fields.get("content"):
                self.og_title = (fields["content"] or "").strip()
            return
        if tag in _VOID:
            if tag in _BLOCK:
                self._emit("\n")
            return
        if tag == "title":
            self._in_title = True
        if tag in _SKIP:
            self._skip_depth += 1
        if tag in ("article", "main"):
            self._article_depth += 1
        if self._skip_depth:
            return
        if tag == "a":
            self._link_depth += 1
            self._emit(_LINK_OPEN)
        if tag in _BLOCK:
            self._emit("\n")
        if tag in _HEADINGS:
            self._emit(_HEADINGS[tag])
            self._in_h1 = tag == "h1" and not self.h1
        elif tag == "li":
            self._emit("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "a" and self._link_depth:
            self._link_depth -= 1
            self._emit(_LINK_CLOSE)
        if not self._skip_depth and tag in _BLOCK:
            self._emit("\n")
        if tag in ("article", "main") and self._article_depth:
            self._article_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        if self._in_h1:
            self.h1 += data
        self._emit(re.sub(r"\s+", " ", data))

    def _emit(self, text: str) -> None:
        self.all.append(text)
        if self._article_depth:
            self.article.append(text)


def extract_article(html: str) -> tuple[str, str]:
    """从 HTML 里取出 (标题, 正文)。页面有 <article> 或 <main> 且里面的字够多时只取那一块，否则取整页的可见文字。"""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # 烂 HTML：有多少算多少
        logger.warning("解析 HTML 时出错，按已经读到的内容继续: %s", exc)

    article, page = _clean("".join(parser.article)), _clean("".join(parser.all))
    body = article if len(article) >= max(_MIN_TEXT_CHARS, len(page) // 4) else page

    # 标题：og:title 最干净；其次是 <title>（去掉「 - 网站名」这样的尾巴）；都没有才用第一个 h1——
    # 不少网站的 h1 是站名或口号
    page_title = " ".join(parser.title.split())
    title = (
        " ".join(parser.og_title.split())
        or _SITE_SUFFIX.sub("", page_title)
        or page_title
        or " ".join(parser.h1.replace("¶", "").split())
    )
    return title, body


def _clean(text: str) -> str:
    """去掉成片的导航链接和空的列表项，再去掉链接标记。"""
    kept: list[str] = []
    run: list[str] = []  # 一串连着的「整行都是链接」的行（中间夹着的空行也算在里面）

    def flush() -> None:
        if sum(1 for line in run if line) < _NAV_RUN:
            kept.extend(run)
        run.clear()

    for raw in text.split("\n"):
        line = raw.strip()
        plain = line.replace(_LINK_OPEN, "").replace(_LINK_CLOSE, "").replace("¶", "").strip()
        if not _NOT_CONTENT.sub("", plain):
            # 空行，或者只有列表符号、分隔符的行：不算内容，也不打断一串导航
            (run if run else kept).append("")
        elif _mostly_links(line):
            # 一行里挤着好几个链接的是横排的菜单，直接去掉；单个链接的行先攒着，看是不是成片出现
            if line.count(_LINK_OPEN) < _NAV_RUN + 1:
                run.append(plain)
        else:
            flush()
            kept.append(plain)
    flush()
    return _tidy("\n".join(kept))


def _mostly_links(line: str) -> bool:
    """这一行的文字是不是基本都在链接里。"""
    linked = total = 0
    inside = 0
    for char in line:
        if char == _LINK_OPEN:
            inside += 1
        elif char == _LINK_CLOSE:
            inside = max(0, inside - 1)
        elif not _NOT_CONTENT.fullmatch(char):
            total += 1
            linked += 1 if inside else 0
    return total > 0 and linked / total >= 0.8
