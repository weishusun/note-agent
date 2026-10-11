"""提取:看懂输入(截图或短文本)并拆成草稿。归类在 app/extract/classifier.py。

刻意不合并:提取用视觉模型、忠实归纳;归类用文本模型、对着分类树选。
两段的失败模式和重试语义也不同,合在一起会互相污染。

重试策略:
- 结构化输出解析失败 → 带着错误原因重试一次,仍失败抛 ExtractError。
- 字数/条数超限**不算失败**:解析时不校验,事后由 _fit_drafts 压缩或截断。

所有对模型的调用都用 with_structured_output(..., include_raw=True),
raw 那一路用来读 usage_metadata(token 消耗),parsed 那一路是校验后的对象。
"""

import base64
import logging
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.extract.client import bind_json, get_classify_model, get_extract_model
from app.extract.models import (
    ALIASES_MAX,
    CONCEPTS_MAX,
    POINT_MAX,
    POINTS_MAX,
    RELATED_MAX,
    SUMMARY_MAX,
    SUPPLEMENT_MAX,
    SUPPLEMENT_POINT_MAX,
    TITLE_MAX,
    TOPIC_CONTENT_MAX,
    TOPIC_SUPPLEMENT_MAX,
    Extraction,
    NoteDraft,
    TopicDraft,
)
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_TRUNCATED_NOTE = "\n\n…(内容过长,已截断;完整内容见原始输入)"


class ExtractError(RuntimeError):
    """模型无法产出合法的结构化结果(含重试后仍失败)。提取和归类共用。"""


def _as_list(value: object) -> object:
    """模型把列表字段写成空字符串、单个字符串或 null 都是实测出现过的:一律宽容地收成列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return value


def _as_text(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if item is not None)
    return value


class _WireDraft(BaseModel):
    """模型原样返回的概念笔记。字数上限只写进字段描述,**不做校验**。

    真实接口上实测:模型经常把概要写到六七十字。若在这里硬校验,一个字段超长就会让
    整次录入失败(连同已经花掉的几十秒)。超长由 _fit 事后压缩,不在解析时拦。
    """

    title: str = Field(description=f"不超过 {TITLE_MAX} 字")
    summary: str = Field(description=f"一句话,不超过 {SUMMARY_MAX} 字")
    points: list[str] = Field(description=f"1 到 {POINTS_MAX} 条,每条不超过 {POINT_MAX} 字")
    aliases: list[str] = Field(default_factory=list, description=f"最多 {ALIASES_MAX} 个")
    related: list[str] = Field(default_factory=list, description=f"最多 {RELATED_MAX} 个")
    supplement: list[str] = Field(
        default_factory=list,
        description=f"模型用自身知识补充的内容,0 到 {SUPPLEMENT_MAX} 条,每条不超过 {SUPPLEMENT_POINT_MAX} 字",
    )

    _lists = field_validator("points", "aliases", "related", "supplement", mode="before")(_as_list)


class _WireTopic(BaseModel):
    """模型原样返回的主题笔记。"""

    title: str = Field(description=f"不超过 {TITLE_MAX} 字")
    summary: str = Field(description=f"一句话,不超过 {SUMMARY_MAX} 字")
    content: str = Field(description="保留输入原有结构的 Markdown:表格、带箭头的有序列表、嵌套列表")
    supplement: str = Field(
        default="", description=f"模型用自身知识补充的背景,不超过 {TOPIC_SUPPLEMENT_MAX} 字,可留空"
    )

    _texts = field_validator("content", "supplement", mode="before")(_as_text)


class _DraftBatch(BaseModel):
    """extract 的结构化输出容器:0–1 条主题笔记 + 0–6 条概念笔记(SCHEMA 2.3)。"""

    # 两个字段都必填(topic 可以是 null,concepts 可以是空数组):真实接口上实测,
    # 都设成可选时模型会直接返回一个空对象
    topic: _WireTopic | None = Field(description="主题笔记;输入只讲一个概念时为 null")
    concepts: list[_WireDraft] = Field(description=f"概念笔记,0 到 {CONCEPTS_MAX} 条")

    @field_validator("topic", mode="before")
    @classmethod
    def _empty_topic(cls, value: object) -> object:
        if value in ("", "null", "None", {}, []):
            return None
        return value

    @field_validator("concepts", mode="before")
    @classmethod
    def _no_concepts(cls, value: object) -> object:
        return [] if value is None else value


class _Compressed(BaseModel):
    items: list[str]


# ---------- 公开函数 ----------
# 返回 (结果, usage):usage 形如 {"input_tokens", "output_tokens", "model"}。


def extract_drafts(
    image: bytes | None, text: str | None, override: str | None = None
) -> tuple[Extraction, dict]:
    """看懂输入,整理成 0–1 条主题笔记草稿 + 若干条概念笔记草稿。不碰分类。"""
    if image is None and not (text and text.strip()):
        raise ValueError("extract_drafts 需要 image 或非空 text 至少之一")

    model = get_extract_model(override)
    runnable = bind_json(model, _DraftBatch)
    messages = _extract_messages(image, text)
    batch, usage = _invoke_structured(runnable, messages, _model_id(model))
    extraction = _fit(batch)
    for draft in extraction.concepts:
        _warn_draft_quality(draft)
    return extraction, usage


def _fit(batch: _DraftBatch) -> Extraction:
    """把模型原样返回的内容收进草稿的上限里:超长的短文本先交给文本模型压缩,压不下来再截断
    (以「…」结尾,原文仍在笔记的「原始输入」里)。只删减,不新增内容。"""
    wires = batch.concepts
    if len(wires) > CONCEPTS_MAX:
        logger.warning("模型返回 %d 条概念笔记,只保留前 %d 条", len(wires), CONCEPTS_MAX)
        wires = wires[:CONCEPTS_MAX]

    concepts = []
    for wire in wires:
        title, from_title = _split_title(wire.title.strip())
        summary = wire.summary.strip()
        wire.aliases = [a for a in _dedupe(from_title + wire.aliases) if a.casefold() != title.casefold()]
        if not title or not summary:
            raise ExtractError(f"模型返回了空的标题或概要: {wire.model_dump()}")
        points = [p.strip() for p in wire.points if p.strip()]
        if len(points) > POINTS_MAX:
            logger.warning("draft %r 有 %d 条要点,只保留前 %d 条", title, len(points), POINTS_MAX)
        # 一条要点都没有时用概要顶上:NoteDraft 至少要一条
        concepts.append(
            (title, summary, points[:POINTS_MAX] or [summary], _dedupe(wire.supplement)[:SUPPLEMENT_MAX], wire)
        )

    topic = batch.topic
    if topic is not None:
        if not topic.title.strip() or not topic.summary.strip():
            raise ExtractError(f"模型返回了空的标题或概要: {topic.model_dump()}")
        if not topic.content.strip():
            logger.warning("主题笔记 %r 没有内容,丢弃", topic.title)
            topic = None
        elif len(concepts) == 1 and concepts[0][0].casefold() == topic.title.strip().casefold():
            # 整份输入只讲这一个概念:按 SCHEMA 2.3 只留概念笔记
            logger.warning("主题笔记与唯一的概念笔记同名(%r),只保留概念笔记", topic.title)
            topic = None
    if topic is None and not concepts:
        raise ExtractError("模型没有返回任何笔记")

    # 收集所有超长的短文本,一次调用压缩完(主题笔记的 content 不在此列:它是结构化正文,只截断)
    too_long: list[tuple[str, int]] = []

    def check(value: str, limit: int) -> None:
        if len(value) > limit and (value, limit) not in too_long:
            too_long.append((value, limit))

    for title, summary, points, supplement, _wire in concepts:
        check(title, TITLE_MAX)
        check(summary, SUMMARY_MAX)
        for point in points:
            check(point, POINT_MAX)
        for extra in supplement:
            check(extra, SUPPLEMENT_POINT_MAX)
    if topic is not None:
        check(topic.title.strip(), TITLE_MAX)
        check(topic.summary.strip(), SUMMARY_MAX)
        check(topic.supplement.strip(), TOPIC_SUPPLEMENT_MAX)
    shortened = dict(zip(too_long, _compress(too_long))) if too_long else {}

    def fit(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        candidate = shortened.get((value, limit), "").strip()
        if candidate and len(candidate) <= limit:
            return candidate
        logger.warning("文本压缩失败,截断到 %d 字: %r", limit, value)
        return value[: limit - 1] + "…"

    return Extraction(
        topic=None
        if topic is None
        else TopicDraft(
            title=fit(topic.title.strip(), TITLE_MAX),
            summary=fit(topic.summary.strip(), SUMMARY_MAX),
            content=_clip_content(topic.content.strip()),
            supplement=fit(topic.supplement.strip(), TOPIC_SUPPLEMENT_MAX),
        ),
        concepts=[
            NoteDraft(
                title=fit(title, TITLE_MAX),
                summary=fit(summary, SUMMARY_MAX),
                points=[fit(p, POINT_MAX) for p in points],
                aliases=_dedupe(wire.aliases)[:ALIASES_MAX],
                related=_dedupe(wire.related)[:RELATED_MAX],
                supplement=[fit(extra, SUPPLEMENT_POINT_MAX) for extra in supplement],
            )
            for title, summary, points, supplement, wire in concepts
        ],
    )


# 「对齐 (RLHF/DPO)」这样的标题：括号里的是别名，不该留在标题里（提示词要求过，模型不总是照做）
_TITLE_WITH_PARENS = re.compile(r"^(.+?)\s*[（(]([^（()）]+)[)）]$")


def _split_title(title: str) -> tuple[str, list[str]]:
    """返回 (去掉括号的标题, 括号里拆出来的别名)。"""
    match = _TITLE_WITH_PARENS.match(title)
    if not match:
        return title, []
    return match.group(1).strip(), [a.strip() for a in re.split(r"[/、,，]", match.group(2)) if a.strip()]


def _clip_content(content: str) -> str:
    if len(content) <= TOPIC_CONTENT_MAX:
        return content
    logger.warning("主题笔记的内容有 %d 字,截断到 %d 字", len(content), TOPIC_CONTENT_MAX)
    return content[: TOPIC_CONTENT_MAX - len(_TRUNCATED_NOTE)] + _TRUNCATED_NOTE


def _dedupe(values: list[str]) -> list[str]:
    kept: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in kept:
            kept.append(value)
    return kept


def _compress(items: list[tuple[str, int]]) -> list[str]:
    """让文本模型把每条文本压到各自上限以内。任何失败都返回空串列表,由调用方截断兜底。"""
    listing = "\n".join(f"{i}. (上限 {limit} 字) {text}" for i, (text, limit) in enumerate(items, 1))
    body = _load_prompt("compress_zh.md").format(count=len(items), items=listing)
    try:
        model = get_classify_model()
        parsed, _usage = _invoke_structured(
            bind_json(model, _Compressed), [HumanMessage(content=body)], _model_id(model)
        )
    except Exception as exc:
        logger.warning("压缩超长文本的调用失败: %s", exc)
        return [""] * len(items)
    if len(parsed.items) != len(items):
        logger.warning("压缩结果条数不符: 期望 %d,得到 %d", len(items), len(parsed.items))
        return [""] * len(items)
    return parsed.items


def _warn_draft_quality(draft: NoteDraft) -> None:
    """非阻断的质量提示。别名本该是可独立检索的短词,过长通常意味着模型把
    「缩写(全称)」塞成了一条,而不是拆开——只告警,不改数据、不拦。"""
    for alias in draft.aliases:
        if len(alias) > 20:
            logger.warning(
                "draft %r 的 alias 过长(%d 字),疑似未拆成可独立检索的短词: %r",
                draft.title,
                len(alias),
                alias,
            )


# ---------- 结构化调用 + 重试 + usage ----------


class _ParseFailure(RuntimeError):
    """reason 是给人和给模型看的简短原因;完整现场(模型返回全文)在异常消息里,只进日志。"""

    def __init__(self, parsing_error: object, raw_text: str) -> None:
        super().__init__(f"parsing_error={parsing_error!r}; 模型返回: {raw_text}")
        text = str(parsing_error) if parsing_error is not None else "模型没有返回结构化结果"
        self.reason = "\n".join(
            line for line in text.splitlines() if "errors.pydantic.dev" not in line
        )


_RETRY_FEEDBACK = (
    "你上一次的输出没有通过校验:\n{reason}\n\n"
    "请修正这些问题后重新输出完整结果。字数和条数上限是硬性要求,超出就压缩表述,不要删掉知识点。"
)


def _invoke_structured(runnable, messages, model_id: str) -> tuple[BaseModel, dict]:
    """调用一次,失败重试一次,仍失败抛 ExtractError。返回 (parsed, usage)。

    输出不合法时,重试会把校验错误原样告诉模型——温度为 0,不说错在哪只会得到同样的输出。
    模型实际返回的全文记进日志(失败现场看不见就无从排查),但不塞进 ExtractError:
    那条消息会直接显示给用户。
    """
    last: Exception | None = None
    attempt = messages
    for _ in range(2):
        try:
            out = runnable.invoke(attempt)
            parsed, raw = _split_output(out)
            return parsed, _usage_from_raw(raw, model_id)
        except _ParseFailure as exc:
            last = exc
            logger.warning("模型输出不合法: %s", exc)
            attempt = [*messages, HumanMessage(content=_RETRY_FEEDBACK.format(reason=exc.reason))]
        except Exception as exc:  # 网络、鉴权等调用异常:原样再试一次
            last = exc
    reason = last.reason if isinstance(last, _ParseFailure) else str(last)
    raise ExtractError(f"模型输出不合法,已重试一次仍失败: {reason}") from last


def _split_output(out: object) -> tuple[BaseModel, object]:
    """拆开 include_raw=True 的返回:{raw, parsed, parsing_error}。"""
    if isinstance(out, dict):
        if out.get("parsing_error") is not None or out.get("parsed") is None:
            raise _ParseFailure(out.get("parsing_error"), _raw_text(out.get("raw")))
        return out["parsed"], out.get("raw")
    # 兜底:非 include_raw 直接是 parsed 对象
    return out, None  # type: ignore[return-value]


def _raw_text(raw: object) -> str:
    """尽量完整地把 raw 打成一行:function_calling 下 JSON 常在 tool_calls 里。"""
    if raw is None:
        return "(无 raw)"
    parts = [f"content={getattr(raw, 'content', '')!r}"]
    tool_calls = getattr(raw, "tool_calls", None)
    if tool_calls:
        parts.append(f"tool_calls={tool_calls}")
    extra = getattr(raw, "additional_kwargs", None)
    if extra:
        parts.append(f"additional_kwargs={extra}")
    return " ".join(parts)


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
    # json_mode 下模型看不到 schema,必须在提示词里写明输出结构
    system = _load_prompt("extract_zh.md") + _load_prompt("extract_format_json.md")
    parts: list[dict] = []
    if text and text.strip():
        parts.append({"type": "text", "text": "# 待处理输入\n" + text.strip()})
    if image is not None:
        b64 = base64.b64encode(image).decode("ascii")
        url = f"data:{_image_mime(image)};base64,{b64}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return [SystemMessage(content=system), HumanMessage(content=parts)]


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
