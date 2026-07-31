"""提取层评测。

    uv run python -m evals.run [--extract-model X] [--classify-model Y] [--limit N]

每个样本 = 一张图(或一个 .txt) + 同名 .yaml。yaml 字段:
    expected_domain_group: 量子力学          # 一致性检验的分组标签
    expected_title_keywords: [角动量, 对易]
    expected_note_count: [1, 3]             # 合理条数区间 [下限, 上限]
    expected_category: physics/quantum-mechanics   # 可选,taxonomy 非空时才有意义

**一致性指标模拟真实累积过程**:按 fixture 顺序逐个处理,每处理完一个就把它产出的
suggested_domain 加进 known_domains 再处理下一个——测的是冷启动时领域名会不会收敛。
一次性把所有已知领域喂进去是测不出来的。

会调用真实 GLM API,消耗 token。结果写 evals/results/{时间戳}_{模型名}.json。
"""

import argparse
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from app.config import default_vault_root
from app.extract.extractor import classify, extract_drafts
from app.extract.models import ClassifyResult, ExtractResult
from app.vault.taxonomy import TaxonomyManager

_HERE = Path(__file__).parent
_FIXTURES = _HERE / "fixtures"
_RESULTS = _HERE / "results"
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


@dataclass
class Fixture:
    name: str
    meta: dict
    image: bytes | None
    text: str | None


def load_fixtures(limit: int | None = None) -> list[Fixture]:
    fixtures: list[Fixture] = []
    for yaml_path in sorted(_FIXTURES.glob("*.yaml")):
        with open(yaml_path, encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        stem = yaml_path.stem
        image, text = None, None
        txt_path = yaml_path.with_suffix(".txt")
        if txt_path.exists():
            text = txt_path.read_text(encoding="utf-8")
        else:
            for ext in _IMAGE_EXTS:
                candidate = yaml_path.with_suffix(ext)
                if candidate.exists():
                    image = candidate.read_bytes()
                    break
        if image is None and text is None:
            raise FileNotFoundError(f"{stem}.yaml 找不到配套的 .txt 或图片")
        fixtures.append(Fixture(name=stem, meta=meta, image=image, text=text))
    if limit is not None:
        fixtures = fixtures[:limit]
    return fixtures


def process(
    fixtures: list[Fixture],
    leaves: list[str],
    tags: list[str],
    extract_model: str | None,
    classify_model: str | None,
) -> list[tuple[Fixture, ExtractResult]]:
    """按顺序处理,累积 known_domains。"""
    known: Counter[str] = Counter()
    out: list[tuple[Fixture, ExtractResult]] = []

    for fx in fixtures:
        # 快照:本样本只能看到「它之前」积累的领域,倒序传入
        known_domains = [(name, count) for name, count in known.most_common()]

        drafts, usage = extract_drafts(fx.image, fx.text, override=extract_model)
        classifications: list[ClassifyResult] = []
        agg = dict(usage)
        for draft in drafts:
            cr, u = classify(draft, leaves, tags, known_domains, override=classify_model)
            classifications.append(cr)
            agg["input_tokens"] += u["input_tokens"]
            agg["output_tokens"] += u["output_tokens"]

        out.append((fx, ExtractResult(drafts=drafts, classifications=classifications, usage=agg)))

        # 处理完再累积,喂给下一个样本
        for cr in classifications:
            if cr.suggested_domain:
                known[cr.suggested_domain] += 1

    return out


# ---------- 指标 ----------


def metric_domain_consistency(results: list[tuple[Fixture, ExtractResult]]) -> dict:
    """【最重要】每个 expected_domain_group 内,模型产出的 suggested_domain 众数占比。

    1.0 = 完全一致。同时列出每组实际产出的所有不同取值,方便看它在哪儿分叉。
    """
    by_group: dict[str, list[str]] = {}
    for fx, er in results:
        group = fx.meta.get("expected_domain_group")
        if group is None:
            continue
        for cr in er.classifications:
            if cr.suggested_domain:
                by_group.setdefault(group, []).append(cr.suggested_domain)

    per_group = {}
    shares = []
    for group, domains in by_group.items():
        counter = Counter(domains)
        top_count = counter.most_common(1)[0][1] if counter else 0
        share = top_count / len(domains) if domains else 0.0
        shares.append(share)
        per_group[group] = {
            "mode_share": round(share, 3),
            "values": dict(counter),
        }
    return {
        "mean_mode_share": round(sum(shares) / len(shares), 3) if shares else None,
        "per_group": per_group,
    }


def metric_granularity(results: list[tuple[Fixture, ExtractResult]]) -> dict:
    hits, total = 0, 0
    for fx, er in results:
        rng = fx.meta.get("expected_note_count")
        if not rng:
            continue
        total += 1
        lo, hi = rng[0], rng[1]
        if lo <= len(er.drafts) <= hi:
            hits += 1
    return {"in_range_rate": round(hits / total, 3) if total else None, "n": total}


def metric_title_keywords(results: list[tuple[Fixture, ExtractResult]]) -> dict:
    scores = []
    for fx, er in results:
        keywords = fx.meta.get("expected_title_keywords") or []
        if not keywords:
            continue
        titles = " ".join(d.title for d in er.drafts)
        found = sum(1 for kw in keywords if kw in titles)
        scores.append(found / len(keywords))
    return {"hit_rate": round(sum(scores) / len(scores), 3) if scores else None, "n": len(scores)}


def metric_category_accuracy(results: list[tuple[Fixture, ExtractResult]], taxonomy_empty: bool) -> dict:
    if taxonomy_empty:
        return {"note": "taxonomy 为空,分类准确率不适用"}
    leaf_hits = leaf_total = top_hits = top_total = 0
    for fx, er in results:
        expected = fx.meta.get("expected_category")
        if not expected:
            continue
        for cr in er.classifications:
            leaf_total += 1
            top_total += 1
            if cr.category == expected:
                leaf_hits += 1
            if cr.category and cr.category.split("/")[0] == expected.split("/")[0]:
                top_hits += 1
    return {
        "leaf_accuracy": round(leaf_hits / leaf_total, 3) if leaf_total else None,
        "top_level_accuracy": round(top_hits / top_total, 3) if top_total else None,
        "n": leaf_total,
    }


def metric_confidence_calibration(results: list[tuple[Fixture, ExtractResult]]) -> dict:
    buckets = {"<0.5": [0, 0], "0.5-0.7": [0, 0], "0.7-0.85": [0, 0], ">0.85": [0, 0]}

    def bucket_of(c: float) -> str:
        if c < 0.5:
            return "<0.5"
        if c < 0.7:
            return "0.5-0.7"
        if c <= 0.85:
            return "0.7-0.85"
        return ">0.85"

    for fx, er in results:
        expected = fx.meta.get("expected_category")
        for cr in er.classifications:
            b = buckets[bucket_of(cr.confidence)]
            b[1] += 1  # total in bucket
            if expected and cr.category == expected:
                b[0] += 1  # correct
    return {
        name: {
            "count": total,
            "accuracy": round(correct / total, 3) if total and any(
                fx.meta.get("expected_category") for fx, _ in results
            ) else None,
        }
        for name, (correct, total) in buckets.items()
    }


def metric_tokens(results: list[tuple[Fixture, ExtractResult]]) -> dict:
    if not results:
        return {"avg_input": None, "avg_output": None}
    n = len(results)
    tin = sum(er.usage["input_tokens"] for _, er in results)
    tout = sum(er.usage["output_tokens"] for _, er in results)
    return {
        "avg_input": round(tin / n, 1),
        "avg_output": round(tout / n, 1),
        "avg_total": round((tin + tout) / n, 1),
    }


def build_report(
    results: list[tuple[Fixture, ExtractResult]],
    extract_model: str,
    classify_model: str,
    taxonomy_empty: bool,
) -> dict:
    return {
        "timestamp": datetime.now().astimezone().isoformat(),
        "extract_model": extract_model,
        "classify_model": classify_model,
        "taxonomy_empty": taxonomy_empty,
        "num_samples": len(results),
        "metrics": {
            "domain_consistency": metric_domain_consistency(results),
            "granularity": metric_granularity(results),
            "title_keywords": metric_title_keywords(results),
            "category_accuracy": metric_category_accuracy(results, taxonomy_empty),
            "confidence_calibration": metric_confidence_calibration(results),
            "tokens": metric_tokens(results),
        },
        "samples": [
            {
                "name": fx.name,
                "expected_domain_group": fx.meta.get("expected_domain_group"),
                "expected_note_count": fx.meta.get("expected_note_count"),
                "produced_note_count": len(er.drafts),
                "titles": [d.title for d in er.drafts],
                "domains": [cr.suggested_domain for cr in er.classifications],
                "categories": [cr.category for cr in er.classifications],
                "confidences": [cr.confidence for cr in er.classifications],
                "usage": er.usage,
            }
            for fx, er in results
        ],
    }


def _sanitize(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "-", name).strip("-")


def main() -> None:
    parser = argparse.ArgumentParser(description="提取层评测")
    parser.add_argument("--extract-model", default=None)
    parser.add_argument("--classify-model", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    root = default_vault_root()
    taxonomy = TaxonomyManager(root / "taxonomy.yaml")
    leaves = taxonomy.leaf_categories()
    tags = taxonomy.load().tags
    taxonomy_empty = not leaves

    fixtures = load_fixtures(limit=args.limit)
    if not fixtures:
        raise SystemExit(f"没有找到样本:请把图片/.txt + 同名 .yaml 放进 {_FIXTURES}")

    results = process(fixtures, leaves, tags, args.extract_model, args.classify_model)

    extract_model = args.extract_model or os.environ.get("EXTRACT_MODEL", "?")
    classify_model = args.classify_model or os.environ.get("CLASSIFY_MODEL", "?")
    report = build_report(results, extract_model, classify_model, taxonomy_empty)

    _RESULTS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = _RESULTS / f"{ts}_{_sanitize(classify_model)}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"\n结果写入 {out_path}")


if __name__ == "__main__":
    main()
