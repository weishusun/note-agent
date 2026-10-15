"""模型客户端工厂(OpenAI 兼容接口,各环节可以用不同的服务商) + 结构化输出封装。

服务商、密钥、模型名一律从 app.settings 现读(.env 给默认值,设置页保存的值覆盖),
**代码里绝不出现硬编码模型名**。langchain_openai 的 import 放在函数内(惰性):
测试 mock 掉本工厂时无需导入 SDK,也不碰网络。
"""

import base64
import struct
import time
import zlib

from app import settings

# 温度设 0:提取要忠实、分类要在冷启动时稳定复用已有领域名,都不需要发散
_TEMPERATURE = 0.0

# 单次请求的超时(秒)。SDK 默认 10 分钟且自动重试 2 次,真实接口上实测出现过请求挂住不返回、
# 一次提问卡五分钟以上的情况。看图提取最慢(实测 30-60 秒),留足余量
_TIMEOUT_SECONDS = 120.0
# 检索词扩展只是锦上添花,慢了就放弃,不能拖住整次问答
_EXPAND_TIMEOUT_SECONDS = 10.0
_PROBE_TIMEOUT_SECONDS = 60.0
_EMBED_TIMEOUT_SECONDS = 30.0
# 整理长资料:一次调用要读一两万字、写出上千字
_DIGEST_TIMEOUT_SECONDS = 240.0
# 模型补充:写 600-1000 字
_SUPPLEMENT_TIMEOUT_SECONDS = 180.0
_SUPPLEMENT_MAX_TOKENS = 4096

def _build(
    role: settings.RoleName,
    override: str | None = None,
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = 1,
    no_thinking: bool = False,
    max_tokens: int | None = None,
):
    """按设置里这个环节选的服务商和模型建客户端。override 只换模型名，服务商不变。

    关闭思考的参数各家写法不同，来自 providers.yaml；自定义服务商不带（不认识的参数有的会报错）。
    """
    from langchain_openai import ChatOpenAI

    current = settings.load()
    if override:
        current.choice(role).model = override
    target = current.endpoint(role)
    return ChatOpenAI(
        model=target.model,
        api_key=target.api_key,
        base_url=target.base_url,
        temperature=_TEMPERATURE,
        timeout=timeout,
        max_retries=max_retries,
        extra_body=target.no_thinking if no_thinking else None,
        max_tokens=max_tokens,
    )


def get_extract_model(override: str | None = None):
    """视觉/提取模型。默认关闭思考(设置里可以打开):开着一张图要 100 秒以上。"""
    return _build("extract", override, no_thinking=settings.load().extract_disable_thinking)


def get_classify_model(override: str | None = None):
    """文本/分类模型,默认关闭思考。

    实测 glm-4.5-air 默认开着思考模式,分类一条草稿要 5 秒左右;关掉后不到 1 秒。
    关闭思考后它的 function calling 输出是坏的,所以调用方必须用 bind_json。
    """
    return _build("classify", override, no_thinking=settings.load().disable_thinking)


def get_digest_model(override: str | None = None):
    """整理长资料用的文本模型(同分类模型)。输入长、输出也长,超时放宽。"""
    return _build(
        "classify",
        override,
        timeout=_DIGEST_TIMEOUT_SECONDS,
        no_thinking=settings.load().disable_thinking,
    )


def get_expand_model(override: str | None = None):
    """检索词扩展用的文本模型(同分类模型),超时短、不重试、默认关闭思考。"""
    return _build(
        "classify",
        override,
        timeout=_EXPAND_TIMEOUT_SECONDS,
        max_retries=0,
        no_thinking=settings.load().disable_thinking,
    )


def get_answer_model(override: str | None = None):
    """问答模型(没选时沿用文字模型)。保持思考开启:回答质量是用户直接看到的。"""
    return _build("answer", override)


def get_supplement_model(override: str | None = None):
    """写模型补充用问答模型(没选时就是文字模型):写的是给人读的讲解,和回答问题一样看重质量。

    关不关思考跟着小调用的设置走:录入时每条笔记都要补一次,开着思考太慢。
    输出要 600-1000 字,max_tokens 给足,免得被服务商的默认上限截断。
    """
    return _build(
        "answer",
        override,
        timeout=_SUPPLEMENT_TIMEOUT_SECONDS,
        no_thinking=settings.load().disable_thinking,
        max_tokens=_SUPPLEMENT_MAX_TOKENS,
    )


def bind_json(model, schema):
    """所有结构化输出一律用 json_mode,始终 include_raw 以便读 usage 和在失败时看清模型实际返回。

    不用 function calling:真实接口上实测,智谱在关闭思考后它的输出是坏的,开着思考时遇到嵌套结构
    也会把整段内容塞成一个字符串。json_mode 下模型看不到 schema,提示词里必须写明要输出的 JSON 结构。
    """
    return model.with_structured_output(schema, method="json_mode", include_raw=True)


# ---------- 向量 ----------


def embed_texts(texts: list[str], override: str | None = None) -> list[list[float]]:
    """把每段文字变成一个向量,顺序与 texts 一致。用设置里的向量模型,走 OpenAI 兼容的 /embeddings 接口。

    不传 dimensions:各家支持的维度不一样,用模型默认的。没配向量模型、没配密钥、调用失败都抛异常。
    """
    from openai import OpenAI

    current = settings.load()
    if override:
        current.embed.model = override
    target = current.endpoint("embed")
    api = OpenAI(
        api_key=target.api_key,
        base_url=target.base_url,
        timeout=_EMBED_TIMEOUT_SECONDS,
        max_retries=1,
    )
    response = api.embeddings.create(model=target.model, input=texts)
    vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    if len(vectors) != len(texts):
        raise RuntimeError(f"向量接口返回了 {len(vectors)} 个向量,应该是 {len(texts)} 个")
    return vectors


def probe_embedding() -> dict:
    """给向量模型发一句话,返回 {ok, detail, seconds};detail 里是向量的维度。"""
    started = time.perf_counter()
    try:
        [vector] = embed_texts(["测试"])
        ok, detail = True, f"{len(vector)} 维"
    except Exception as exc:
        ok, detail = False, str(exc)[:300]
    return {"ok": ok, "detail": detail, "seconds": round(time.perf_counter() - started, 1)}


# ---------- 连接测试(设置页用) ----------


def _png(width: int = 64, height: int = 64) -> bytes:
    """一张纯红色的小图,测试提取模型能不能看图。不引 Pillow,手写最小 PNG。"""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + b"\xff\x00\x00" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def _probe(build, content) -> dict:
    from langchain_core.messages import HumanMessage

    started = time.perf_counter()
    try:
        reply = build().invoke([HumanMessage(content=content)])
        text = reply.content if isinstance(reply.content, str) else str(reply.content)
        ok, detail = True, text.strip()[:80]
    except Exception as exc:
        ok, detail = False, str(exc)[:300]
    return {"ok": ok, "detail": detail, "seconds": round(time.perf_counter() - started, 1)}


def probe_text() -> dict:
    """用分类模型发一句话,返回 {ok, detail, seconds}。"""

    def build():
        return _build(
            "classify",
            timeout=_PROBE_TIMEOUT_SECONDS,
            max_retries=0,
            no_thinking=settings.load().disable_thinking,
        )

    return _probe(build, "只回复两个字:正常")


def probe_vision() -> dict:
    """给提取模型发一张纯色小图,确认它真的能看图。"""

    def build():
        return _build(
            "extract",
            timeout=_PROBE_TIMEOUT_SECONDS,
            max_retries=0,
            no_thinking=settings.load().extract_disable_thinking,
        )

    url = "data:image/png;base64," + base64.b64encode(_png()).decode("ascii")
    return _probe(
        build,
        [
            {"type": "text", "text": "这张图是什么颜色?只回答颜色。"},
            {"type": "image_url", "image_url": {"url": url}},
        ],
    )


def probe_answer() -> dict:
    """问答模型单独选了的时候测它（和文字模型同一句话）。"""
    return _probe(
        lambda: _build("answer", timeout=_PROBE_TIMEOUT_SECONDS, max_retries=0),
        "只回复两个字:正常",
    )


# ---------- 列出服务商的模型(设置页用) ----------

_LIST_TIMEOUT_SECONDS = 15.0


def list_models(base_url: str, api_key: str) -> dict:
    """GET {base_url}/models,顺便验证 Key。返回 {status, models, detail}:

    status 为 ok(models 是模型名,按名字排好)、bad_key(401/403)、unsupported(这家不提供列表接口)
    或 error(网络等其他问题,detail 里是原因)。不是每家都能列全:智谱只列文字模型,看图、向量的列不出来。
    """
    import httpx

    url = base_url.rstrip("/") + "/models"
    try:
        resp = httpx.get(
            url, headers={"Authorization": f"Bearer {api_key}"}, timeout=_LIST_TIMEOUT_SECONDS
        )
    except httpx.HTTPError as exc:
        return {"status": "error", "models": [], "detail": f"连不上 {base_url}：{exc}"[:300]}
    if resp.status_code in (401, 403):
        return {"status": "bad_key", "models": [], "detail": "Key 不对，或者没有权限"}
    if resp.status_code in (404, 405):
        return {"status": "unsupported", "models": [], "detail": "这家不提供模型列表，模型名请照推荐或文档填"}
    if resp.status_code >= 400:
        return {"status": "error", "models": [], "detail": f"HTTP {resp.status_code}：{resp.text[:200]}"}
    try:
        data = resp.json()
        items = data.get("data", []) if isinstance(data, dict) else data
        models = sorted({str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")})
    except (ValueError, TypeError, KeyError):
        return {"status": "unsupported", "models": [], "detail": "模型列表的格式看不懂"}
    return {"status": "ok", "models": models, "detail": f"{len(models)} 个模型"}
