"""模型客户端工厂(OpenAI 兼容接口,默认智谱 GLM) + 结构化输出封装。

接口地址、密钥、模型名一律从 app.settings 现读(.env 给默认值,设置页保存的值覆盖),
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

_NO_THINKING = {"thinking": {"type": "disabled"}}


def _resolve_model(override: str | None, configured: str | None, label: str) -> str:
    """override 优先,否则用设置里的值。两者皆空则报错——不允许默认模型名。"""
    name = override or configured
    if not name:
        raise RuntimeError(f"还没有配置{label}:请到设置页填写,或在 note-agent/.env 里配置")
    return name


def _build(
    model_name: str,
    current: settings.Settings,
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = 1,
    no_thinking: bool = False,
):
    from langchain_openai import ChatOpenAI

    if not current.api_key:
        raise RuntimeError(
            "还没有配置 API Key:请到设置页填写,或在 note-agent/.env 里配置 ZHIPU_API_KEY"
        )
    return ChatOpenAI(
        model=model_name,
        api_key=current.api_key,
        base_url=current.base_url,
        temperature=_TEMPERATURE,
        timeout=timeout,
        max_retries=max_retries,
        extra_body=_NO_THINKING if no_thinking else None,
    )


def get_extract_model(override: str | None = None):
    """视觉/提取模型。默认关闭思考(设置里可以打开):开着一张图要 100 秒以上。"""
    current = settings.load()
    name = _resolve_model(override, current.extract_model, "提取模型")
    return _build(name, current, no_thinking=current.extract_disable_thinking)


def get_classify_model(override: str | None = None):
    """文本/分类模型,默认关闭思考。

    实测 glm-4.5-air 默认开着思考模式,分类一条草稿要 5 秒左右;关掉后不到 1 秒。
    关闭思考后它的 function calling 输出是坏的,所以调用方必须用 bind_json。
    """
    current = settings.load()
    name = _resolve_model(override, current.classify_model, "分类模型")
    return _build(name, current, no_thinking=current.disable_thinking)


def get_expand_model(override: str | None = None):
    """检索词扩展用的文本模型(同分类模型),超时短、不重试、默认关闭思考。"""
    current = settings.load()
    name = _resolve_model(override, current.classify_model, "分类模型")
    return _build(
        name,
        current,
        timeout=_EXPAND_TIMEOUT_SECONDS,
        max_retries=0,
        no_thinking=current.disable_thinking,
    )


def get_answer_model(override: str | None = None):
    """问答模型(未配置时沿用分类模型)。保持思考开启:回答质量是用户直接看到的。"""
    current = settings.load()
    return _build(_resolve_model(override, current.effective_answer_model, "问答模型"), current)


def bind_json(model, schema):
    """所有结构化输出一律用 json_mode,始终 include_raw 以便读 usage 和在失败时看清模型实际返回。

    不用 function calling:真实接口上实测,智谱在关闭思考后它的输出是坏的,开着思考时遇到嵌套结构
    也会把整段内容塞成一个字符串。json_mode 下模型看不到 schema,提示词里必须写明要输出的 JSON 结构。
    """
    return model.with_structured_output(schema, method="json_mode", include_raw=True)


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
        current = settings.load()
        name = _resolve_model(None, current.classify_model, "分类模型")
        return _build(
            name,
            current,
            timeout=_PROBE_TIMEOUT_SECONDS,
            max_retries=0,
            no_thinking=current.disable_thinking,
        )

    return _probe(build, "只回复两个字:正常")


def probe_vision() -> dict:
    """给提取模型发一张纯色小图,确认它真的能看图。"""

    def build():
        current = settings.load()
        name = _resolve_model(None, current.extract_model, "提取模型")
        return _build(
            name,
            current,
            timeout=_PROBE_TIMEOUT_SECONDS,
            max_retries=0,
            no_thinking=current.extract_disable_thinking,
        )

    url = "data:image/png;base64," + base64.b64encode(_png()).decode("ascii")
    return _probe(
        build,
        [
            {"type": "text", "text": "这张图是什么颜色?只回答颜色。"},
            {"type": "image_url", "image_url": {"url": url}},
        ],
    )
