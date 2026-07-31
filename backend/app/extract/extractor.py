"""提取层的两个独立函数:extract_drafts(看懂+拆分)与 classify(分类)。

刻意不合并:提取用视觉模型、只忠实归纳;分类用文本模型、注入封闭枚举。
两段的失败模式和重试语义也不同,合在一起会互相污染。

重试策略:
- 结构化输出解析失败 → 重试一次,仍失败抛 ExtractError(对两个函数都适用)。
- classify 额外的语义重试:模型给的 category 不在叶子表里、或 category 为 null 时
  suggested_domain 长度不在 2-8 字 → 再问一次;仍不合法则把 category 规整成 null。

所有对模型的调用都用 with_structured_output(..., include_raw=True),
raw 那一路用来读 usage_metadata(token 消耗),parsed 那一路是校验后的对象。
"""

import base64
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.extract.client import bind_structured, get_classify_model, get_extract_model
from app.extract.models import ClassifyResult, NoteDraft
from pydantic import BaseModel, ConfigDict, Field

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class ExtractError(RuntimeError):
    """提取/分类过程无法产出合法结构化结果(含重试后仍失败)。"""


class _DraftBatch(BaseModel):
    """extract 的结构化输出容器:一次 1-5 条草稿。"""

    model_config = ConfigDict(extra="forbid")

    drafts: list[NoteDraft] = Field(min_length=1, max_length=5)


# ---------- 公开函数 ----------
# 都返回 (结果, usage):usage 形如 {"input_tokens", "output_tokens", "model"},
# 供编排层 / evals 统计 token 消耗。


def extract_drafts(
    image: bytes | None, text: str | None, override: str | None = None
) -> tuple[list[NoteDraft], dict]:
    """看懂输入并拆成 1-5 条原子草稿。不碰分类。"""
    if image is None and not (text and text.strip()):
        raise ValueError("extract_drafts 需要 image 或非空 text 至少之一")

    model = get_extract_model(override)
    runnable = bind_structured(model, _DraftBatch)
    messages = _extract_messages(image, text)
    batch, usage = _invoke_structured(runnable, messages, _model_id(model))
    return batch.drafts, usage


def classify(
    draft: NoteDraft,
    leaves: list[str],
    tags: list[str],
    known_domains: list[tuple[str, int]],
    override: str | None = None,
) -> tuple[ClassifyResult, dict]:
    """给单条草稿分类。leaves 为空是正常情况,直接走 suggested_domain 路径。"""
    model = get_classify_model(override)
    runnable = bind_structured(model, ClassifyResult)
    messages = _classify_messages(draft, leaves, tags, known_domains)
    model_id = _model_id(model)

    raw, usage = _invoke_structured(runnable, messages, model_id)
    if _needs_retry(raw, leaves):
        raw, again = _invoke_structured(runnable, messages, model_id)
        usage = _add_usage(usage, again)
    return _finalize(raw, leaves, tags), usage


# ---------- 分类后置校验 ----------


def _effective_category(raw: ClassifyResult, leaves: list[str]) -> str | None:
    """只认真正落在叶子表里的 category,其余(含叶子表为空)一律 None。"""
    if raw.category and leaves and raw.category in leaves:
        return raw.category
    return None


def _needs_retry(raw: ClassifyResult, leaves: list[str]) -> bool:
    eff = _effective_category(raw, leaves)
    # 模型给了 category 但不合法 → 再问一次
    if raw.category is not None and eff is None:
        return True
    # 走 suggested_domain 路径但长度不合规 → 再问一次
    if eff is None:
        sd = raw.suggested_domain or ""
        if not 2 <= len(sd) <= 8:
            return True
    return False


def _finalize(raw: ClassifyResult, leaves: list[str], tags: list[str]) -> ClassifyResult:
    """把模型输出规整成合法结果:非法分类降级为 suggested_domain 路径,
    词表外标签直接丢弃(最多 6 个),分类路径下 suggested_domain 强制为空、
    suggested_domain 路径下 confidence 强制为 0。"""
    eff = _effective_category(raw, leaves)
    allowed = set(tags)
    kept: list[str] = []
    for t in raw.tags:
        if t in allowed and t not in kept:
            kept.append(t)
    kept = kept[:6]

    if eff is None:
        return ClassifyResult(
            category=None,
            suggested_domain=raw.suggested_domain,
            tags=kept,
            confidence=0.0,
            reason=raw.reason,
        )
    return ClassifyResult(
        category=eff,
        suggested_domain=None,
        tags=kept,
        confidence=raw.confidence,
        reason=raw.reason,
    )


# ---------- 结构化调用 + 重试 + usage ----------


class _ParseFailure(RuntimeError):
    pass


def _invoke_structured(runnable, messages, model_id: str) -> tuple[BaseModel, dict]:
    """调用一次,解析失败重试一次,仍失败抛 ExtractError。返回 (parsed, usage)。"""
    last: Exception | None = None
    for _ in range(2):
        try:
            out = runnable.invoke(messages)
            parsed, raw = _split_output(out)
            return parsed, _usage_from_raw(raw, model_id)
        except Exception as exc:  # 解析失败、校验失败、调用异常都重试一次
            last = exc
    raise ExtractError("结构化输出解析失败,已重试一次仍失败") from last


def _split_output(out: object) -> tuple[BaseModel, object]:
    """拆开 include_raw=True 的返回:{raw, parsed, parsing_error}。"""
    if isinstance(out, dict):
        if out.get("parsing_error") is not None or out.get("parsed") is None:
            raise _ParseFailure(str(out.get("parsing_error")))
        return out["parsed"], out.get("raw")
    # 兜底:非 include_raw 直接是 parsed 对象
    return out, None  # type: ignore[return-value]


def _usage_from_raw(raw: object, model_id: str) -> dict:
    meta = getattr(raw, "usage_metadata", None) or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0) or 0),
        "output_tokens": int(meta.get("output_tokens", 0) or 0),
        "model": model_id,
    }


def _add_usage(a: dict, b: dict) -> dict:
    return {
        "input_tokens": a["input_tokens"] + b["input_tokens"],
        "output_tokens": a["output_tokens"] + b["output_tokens"],
        "model": b.get("model") or a.get("model", ""),
    }


def _model_id(model: object) -> str:
    return getattr(model, "model_name", None) or getattr(model, "model", None) or ""


# ---------- 消息构造 ----------


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _extract_messages(image: bytes | None, text: str | None) -> list:
    system = _load_prompt("extract_zh.md")
    parts: list[dict] = []
    if text and text.strip():
        parts.append({"type": "text", "text": "# 待处理输入\n" + text.strip()})
    if image is not None:
        b64 = base64.b64encode(image).decode("ascii")
        url = f"data:{_image_mime(image)};base64,{b64}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return [SystemMessage(content=system), HumanMessage(content=parts)]


def _classify_messages(
    draft: NoteDraft,
    leaves: list[str],
    tags: list[str],
    known_domains: list[tuple[str, int]],
) -> list:
    template = _load_prompt("classify_zh.md")
    body = template.format(
        title=draft.title,
        summary=draft.summary,
        points="\n".join(f"- {p}" for p in draft.points),
        related="、".join(draft.related) or "(无)",
        leaves=_render_enum(leaves, "(当前没有任何分类叶子,请直接走 suggested_domain)"),
        tags=_render_enum(tags, "(当前没有可用标签)"),
        known_domains=_render_domains(known_domains),
    )
    return [HumanMessage(content=body)]


def _render_enum(items: list[str], empty_hint: str) -> str:
    if not items:
        return empty_hint
    return "\n".join(f"- {it}" for it in items)


def _render_domains(known_domains: list[tuple[str, int]]) -> str:
    if not known_domains:
        return "(暂无已知领域,如需新造请给一个简洁的领域名)"
    return "\n".join(f"- {name}({count} 条)" for name, count in known_domains)


def _image_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"  # 兜底,截图绝大多数是 png
