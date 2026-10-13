"""问答用的混合检索：字面检索和向量检索各出一份名单，按名次合并（SCHEMA 第 5 节 POST /api/ask）。

字面检索（query.retrieve_ranked）擅长「问题里直接出现了笔记的名字」；
向量检索擅长「说的是同一件事，但用词完全不同」。两边都排在前面的笔记最可信。
向量索引不可用（没配模型、接口失败、还没算出来）时，结果就是纯字面检索的结果。
"""

import math
from collections.abc import Sequence

from app.search.index import SearchIndex
from app.search.query import refs_by_paths, retrieve_ranked
from app.search.vectors import VectorIndex
from app.vault.models import NoteRef

# 向量这一路最多取几条候选
VECTOR_CANDIDATES = 12
# 相似度低于这个数的不要：向量检索永远能排出「最接近的」几条，哪怕库里根本没有相关的笔记。
# 不设下限的话，问一个库里没有的问题也会带回几条不相干的笔记，回答就不会老实说「知识库里没有」了。
# 0.33 是对着智谱的向量模型实测定的：问题和相关笔记的相似度在 0.39–0.56，和不相干的笔记最高 0.23
VECTOR_MIN_SCORE = 0.33
# 也不要比第一名差太多的
VECTOR_MARGIN = 0.12
# 名次合并（RRF）里的常数：越大，名次靠前的优势越小。60 是通用的取值
_RRF_K = 60


def retrieve(
    index: SearchIndex,
    vectors: VectorIndex | None,
    question: str,
    limit: int = 5,
    keywords: Sequence[str] = (),
) -> list[NoteRef]:
    """返回与 question 最相关的至多 limit 条笔记，最相关的在前。"""
    pool = max(limit * 2, 10)
    ranked = retrieve_ranked(index, question, pool, keywords=keywords)
    query = vectors.embed_query(question) if vectors is not None else None
    if vectors is None or query is None:
        return [ref for ref, _weak in ranked[:limit]]

    hits = vectors.search(query, VECTOR_CANDIDATES)
    floor = max(VECTOR_MIN_SCORE, hits[0][1] - VECTOR_MARGIN) if hits else VECTOR_MIN_SCORE
    hits = [(path, score) for path, score in hits if score >= floor]

    # 字面检索里只靠正文碎片撞上的笔记，意思上也得沾边才留；还没有向量的新笔记不筛
    lexical: list[NoteRef] = []
    for ref, weak in ranked:
        vector = vectors.vector_of(ref.path) if weak else None
        if vector is None or len(vector) != len(query) or math.sumprod(query, vector) >= floor:
            lexical.append(ref)
    if not hits:
        return lexical[:limit]

    scores: dict[str, float] = {}
    for rank, ref in enumerate(lexical):
        scores[ref.path] = scores.get(ref.path, 0.0) + 1 / (_RRF_K + rank + 1)
    for rank, (path, _score) in enumerate(hits):
        scores[path] = scores.get(path, 0.0) + 1 / (_RRF_K + rank + 1)

    refs = {ref.path: ref for ref in lexical}
    missing = [path for path, _score in hits if path not in refs]
    refs.update({ref.path: ref for ref in refs_by_paths(index, missing)})
    # 分数相同时字面检索的在前：它命中的是名字，更确定
    order = {ref.path: i for i, ref in enumerate(lexical)}
    best = sorted(
        (path for path in scores if path in refs),
        key=lambda path: (-scores[path], order.get(path, len(order)), path),
    )
    return [refs[path] for path in best[:limit]]
