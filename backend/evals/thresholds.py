"""实测一个向量模型的相似度门槛，给 providers.yaml 的 embed_thresholds 用。

    uv run python -m evals.thresholds [--model X]

向量模型取自当前设置（.env 或设置页）里「向量」这一环选的服务商；--model 只换模型名。
题集在 evals/thresholds_zh.yaml。会调用真实的向量接口（几十段短文字，花不了几分钱）。

三个门槛的含义（app/search/hybrid.py、app/ingest/similar.py）：
    floor      问答时向量命中至少要这么像。要高过「库里没有答案的问题」能排出的最高分，低过「该找到的笔记」的分
    margin     比第一名差这么多以上的不要
    duplicate  录入查重时至少这么像才拿去问模型。定得偏低：只决定要不要问，是不是重复由模型说了算
"""

import argparse
import math
import statistics
from array import array
from pathlib import Path

import yaml

from app import settings
from app.extract import client
from app.search.vectors import embedding_text, normalize

_FIXTURE = Path(__file__).parent / "thresholds_zh.yaml"
# margin 的参照：智谱的向量模型在本题集上「该找到的笔记的平均分 - 库里没有的问题的平均最高分」，margin 定的是 0.12
_REFERENCE_SPREAD = 0.262
_REFERENCE_MARGIN = 0.12
# duplicate 比「同一概念的两条笔记」的最低相似度再低这么多：宁可多问模型一句
_DUPLICATE_SLACK = 0.04


def _embed(model: str | None, texts: list[str]) -> list[array]:
    out: list[array] = []
    for start in range(0, len(texts), 16):
        out += [normalize(v) for v in client.embed_texts(texts[start : start + 16], override=model)]
    return out


def _note(title: str, text: str) -> str:
    return embedding_text(title, [], text, text)


def _hits(query: array, notes: dict[str, array], floor: float, margin: float) -> list[str]:
    scored = sorted(((math.sumprod(query, v), title) for title, v in notes.items()), reverse=True)[:12]
    cut = max(floor, scored[0][0] - margin)
    return [title for score, title in scored if score >= cut]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="只换模型名，服务商仍是设置里向量这一环选的那家")
    args = parser.parse_args()
    endpoint = settings.load().endpoint("embed")
    model = args.model or endpoint.model
    data = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))

    titles = list(data["notes"])
    notes = dict(zip(titles, _embed(model, [_note(t, data["notes"][t]) for t in titles])))
    related_q = _embed(model, [q for q, _ in data["related"]])
    unrelated_q = _embed(model, list(data["unrelated"]))

    def pair_scores(pairs: list) -> list[float]:
        flat = [_note(*next(iter(item.items()))) for pair in pairs for item in pair]
        vectors = _embed(model, flat)
        return [math.sumprod(vectors[i], vectors[i + 1]) for i in range(0, len(vectors), 2)]

    same = pair_scores(data["same"])
    different = pair_scores(data["different"])

    right = [math.sumprod(v, notes[want]) for (_q, want), v in zip(data["related"], related_q)]
    wrong_top = [max(math.sumprod(v, n) for n in notes.values()) for v in unrelated_q]
    # 相关问题里，该找到的那条没排第一时，它比第一名差多少：margin 至少要容得下
    behind = [
        max(math.sumprod(v, n) for n in notes.values()) - math.sumprod(v, notes[want])
        for (_q, want), v in zip(data["related"], related_q)
    ]

    if max(wrong_top) < min(right):
        floor = (max(wrong_top) + min(right)) / 2
    else:  # 有重叠：取错得最少的那个
        candidates = sorted(set(right + wrong_top))
        floor = min(
            candidates,
            key=lambda f: sum(s < f for s in right) + sum(s >= f for s in wrong_top),
        )
    spread = statistics.mean(right) - statistics.mean(wrong_top)
    margin = _REFERENCE_MARGIN * spread / _REFERENCE_SPREAD if _REFERENCE_SPREAD else _REFERENCE_MARGIN
    margin = max(margin, max(behind) + 0.01)
    duplicate = min(same) - _DUPLICATE_SLACK

    print(f"服务商 {endpoint.provider}，模型 {model}，{len(next(iter(notes.values())))} 维\n")
    print(f"该找到的笔记       min {min(right):.3f}  mean {statistics.mean(right):.3f}")
    print(f"库里没有的问题最高 max {max(wrong_top):.3f}  mean {statistics.mean(wrong_top):.3f}")
    print(f"  两者之间{'有' if max(wrong_top) < min(right) else '没有'}空隙；spread {spread:.3f}；该找到的落后第一名最多 {max(behind):.3f}")
    print(f"同一概念的两条笔记 min {min(same):.3f}  mean {statistics.mean(same):.3f}")
    print(f"相关但不同的概念   max {max(different):.3f}  mean {statistics.mean(different):.3f}\n")

    def report(label: str, f: float, m: float, d: float) -> None:
        found = noise = 0
        for (_q, want), v in zip(data["related"], related_q):
            got = _hits(v, notes, f, m)
            found += want in got
            noise += len(got) - (want in got)
        false = sum(bool(_hits(v, notes, f, m)) for v in unrelated_q)
        print(
            f"{label:<6} floor {f:.2f} margin {m:.2f} duplicate {d:.2f}：相关问题找到 {found}/{len(right)}，"
            f"顺带别的笔记 {noise} 条；库里没有的问题带回笔记 {false}/{len(wrong_top)}；"
            f"查重候选：同一概念 {sum(s >= d for s in same)}/{len(same)}，不同概念 {sum(s >= d for s in different)}/{len(different)}"
        )

    report("默认", 0.33, 0.12, 0.55)
    report("建议", floor, margin, duplicate)
    print("\n写进 providers.yaml 对应服务商的 embed_thresholds：")
    print(f"      {model}: {{floor: {floor:.2f}, margin: {margin:.2f}, duplicate: {duplicate:.2f}}}")


if __name__ == "__main__":
    main()
