"""真实 API 冒烟脚本。用法:

    uv run python -m evals.smoke <图片路径或文本>

参数是已存在的文件路径 → 当图片读;否则当文本。

这一步会**真的**打 GLM。它的全部价值在于失败时能看见现场:配置、请求体、
原始响应逐段打印;异常连同 HTTP 状态码和响应体原文一起抛;结构化解析失败时
先把模型实际返回的全文打出来再抛。**任何静默降级都会毁掉这个脚本的意义。**

刻意直接调 runnable(而非走 extractor.extract_drafts/classify),这样能拿到
include_raw 的 raw 那一路——那是排查的关键。故不含重试,每步只调一次。
"""

import base64
import json
import sys
import time

from app import config
from app.extract.client import bind_structured, get_extract_model
from app.extract.extractor import (
    _DraftBatch,
    _extract_messages,
    _image_mime,
    classify_batch,
)
from app.vault.taxonomy import TaxonomyManager


def _mask(key: str | None) -> str:
    if not key:
        return "(未配置)"
    if len(key) <= 10:
        return "*" * len(key)
    return f"{key[:6]}{'*' * (len(key) - 10)}{key[-4:]}"


def _read_input(arg: str) -> tuple[bytes | None, str | None]:
    from pathlib import Path

    p = Path(arg)
    if p.exists() and p.is_file():
        return p.read_bytes(), None
    return None, arg


def _pretty(model: object) -> str:
    return json.dumps(model.model_dump(), ensure_ascii=False, indent=2)  # type: ignore[attr-defined]


def _raw_dump(raw: object) -> str:
    """把 raw AIMessage 尽量完整打出来:function_calling 下真正的 JSON 常在 tool_calls
    / additional_kwargs 里,content 可能是空串,所以三处都打。"""
    if raw is None:
        return "(无 raw)"
    lines = [f"content: {getattr(raw, 'content', '')!r}"]
    tool_calls = getattr(raw, "tool_calls", None)
    if tool_calls:
        lines.append(f"tool_calls: {tool_calls}")
    extra = getattr(raw, "additional_kwargs", None)
    if extra:
        lines.append(f"additional_kwargs: {extra}")
    meta = getattr(raw, "response_metadata", None)
    if meta:
        lines.append(f"response_metadata: {meta}")
    return "\n".join(lines)


def _acc_usage(total: dict, raw: object) -> None:
    meta = getattr(raw, "usage_metadata", None) or {}
    total["input_tokens"] += int(meta.get("input_tokens", 0) or 0)
    total["output_tokens"] += int(meta.get("output_tokens", 0) or 0)


def _dump_http_error(label: str, exc: Exception) -> None:
    print(f"\n!! {label} 调用异常: {type(exc).__name__}")
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    print(f"   HTTP 状态码: {status}")
    resp = getattr(exc, "response", None)
    body = None
    if resp is not None:
        body = getattr(resp, "text", None) or getattr(resp, "content", None)
    body = body or getattr(exc, "body", None)
    print(f"   响应体原文: {body}")
    print(f"   完整异常: {exc!r}")


def _invoke_verbose(runnable: object, messages: object, label: str) -> dict:
    """调一次并暴露一切:调用异常打 HTTP 现场后抛;解析失败打模型返回全文后抛。"""
    try:
        out = runnable.invoke(messages)  # type: ignore[attr-defined]
    except Exception as exc:
        _dump_http_error(label, exc)
        raise
    if not isinstance(out, dict):  # 理论上 include_raw 一定是 dict,兜底
        return {"raw": None, "parsed": out, "parsing_error": None}
    if out.get("parsing_error") is not None or out.get("parsed") is None:
        print(f"\n!! {label} 结构化输出解析失败。模型实际返回全文:")
        print(_raw_dump(out.get("raw")))
        raise RuntimeError(f"{label} 结构化输出解析失败: {out.get('parsing_error')!r}")
    return out


def main() -> None:
    args = sys.argv[1:]
    if not args:
        raise SystemExit("用法: uv run python -m evals.smoke <图片路径或文本>")
    image, text = _read_input(" ".join(args))

    t0 = time.perf_counter()
    usage = {"input_tokens": 0, "output_tokens": 0}

    # [1] 配置回显
    print("=" * 64)
    print("[1] 配置")
    print(f"  base_url                 : {config.GLM_BASE_URL}")
    print(f"  EXTRACT_MODEL            : {config.EXTRACT_MODEL}")
    print(f"  CLASSIFY_MODEL           : {config.CLASSIFY_MODEL}")
    print(f"  STRUCTURED_OUTPUT_METHOD : {config.STRUCTURED_OUTPUT_METHOD}")
    print(f"  ZHIPU_API_KEY            : {_mask(config.ZHIPU_API_KEY)}")
    print(f"  .env                     : {config.ENV_PATH}")
    config.require_api_key()  # 缺 key 立刻可读报错,不拖到 401

    # [2] 请求侧
    print("\n[2] 请求侧")
    if image is not None:
        b64 = base64.b64encode(image).decode("ascii")
        url = f"data:{_image_mime(image)};base64,{b64}"
        print(f"  图片字节数     : {len(image)}")
        print(f"  base64 长度    : {len(b64)}")
        print(f"  image_url[:80] : {url[:80]}")  # 肉眼确认 data:image/...;base64, 前缀
    else:
        print(f"  文本输入       : {text!r}")

    # [3][4] extract
    print("\n[3] extract 原始响应")
    ext_model = get_extract_model()
    ext_runnable = bind_structured(ext_model, _DraftBatch)
    ext_out = _invoke_verbose(ext_runnable, _extract_messages(image, text), "extract")
    print(_raw_dump(ext_out["raw"]))
    _acc_usage(usage, ext_out["raw"])
    drafts = ext_out["parsed"].drafts

    print("\n[4] 解析后的 NoteDraft")
    print(f"  共 {len(drafts)} 条")
    for i, draft in enumerate(drafts, 1):
        print(f"  --- draft {i} ---")
        print(_pretty(draft))

    # [5] classify —— 走 classify_batch:单次录入内逐条累积 known_domains,
    #     后一条能看到前一条定的领域名,避免同一张图分出「AI工程」「人工智能」两个值。
    taxonomy = TaxonomyManager(config.default_vault_root() / "taxonomy.yaml")
    leaves = taxonomy.leaf_categories()
    tags = taxonomy.load().tags
    print(f"\n[5] classify_batch 解析结果(叶子数={len(leaves)}, 标签数={len(tags)})")
    print("    (batch 路径不单独回传 raw/usage;解析失败时全文会随 ExtractError 抛出。")
    print("     要逐条看原始响应,单独对某条 draft 调 classify)")
    classifications = classify_batch(drafts, leaves, tags, [])
    for i, cr in enumerate(classifications, 1):
        print(f"  --- draft {i} 分类 ---")
        print(_pretty(cr))

    # [6] token 用量(仅 extract;classify 走 batch 未单独回传 usage)
    print("\n[6] token 用量(extract)")
    print(f"  input={usage['input_tokens']}  output={usage['output_tokens']}"
          f"  total={usage['input_tokens'] + usage['output_tokens']}")

    # [7] 耗时
    print(f"\n[7] 总耗时: {time.perf_counter() - t0:.2f}s")
    print("=" * 64)


if __name__ == "__main__":
    main()
