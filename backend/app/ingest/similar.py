"""录入时按内容找疑似重复（SCHEMA 2.5）：名字不同、但讲的是同一个概念的已有笔记。

两步：向量找出内容最接近的几条已有概念笔记当候选，再让文本模型逐对判断是不是同一个概念。
只靠相似度分不开——实测同一概念的两种写法相似度 0.59–0.80，一对不同的概念 0.55–0.83，完全重叠。

整个过程是锦上添花：没配向量模型、接口失败、模型判断失败，都当作「没有发现重复」，不让录入失败。
顺带把新笔记的向量算出来交还给调用方存下，免得后台再算一遍。
"""

import logging
from array import array
from collections.abc import Sequence

from app.extract import client
from app.extract.same import Card, judge_same_concept
from app.search.query import refs_by_paths
from app.search.vectors import VectorIndex, normalize, note_text
from app.vault.models import Note, NoteRef
from app.vault.store import VaultStore

logger = logging.getLogger(__name__)

# 相似度不到 VectorIndex.thresholds().duplicate 的不当候选（按向量模型取）。
# 定得偏低：它只决定「要不要问模型一句」，是不是重复由模型说了算
# 每条新笔记最多拿几条已有笔记去问模型
CANDIDATES_PER_NOTE = 2
_SEARCH_POOL = 8


def find_similar(
    store: VaultStore,
    vectors: VectorIndex | None,
    notes: Sequence[Note],
    skip: Sequence[bool] = (),
) -> tuple[list[NoteRef | None], list[array | None]]:
    """notes 是这次录入刚组装好、还没落盘的概念笔记。返回 (每条疑似与之重复的已有笔记, 每条的向量)。

    skip[i] 为真的那条已经按名字查出重复了，不用再找，但向量照算。
    只和**已有的**概念笔记比：这时新笔记都还没进索引，同一次录入产出的笔记之间自然不会互相比。
    """
    nothing: tuple[list[NoteRef | None], list[array | None]] = ([None] * len(notes), [None] * len(notes))
    if vectors is None or not vectors.enabled() or not notes:
        return nothing
    try:
        embedded = [normalize(v) for v in client.embed_texts([note_text(note) for note in notes])]
    except Exception as exc:
        logger.warning("新笔记的向量没算出来，这次只按名字查重复: %s", exc)
        return nothing

    # 每条新笔记的候选：内容最接近的几条已有概念笔记
    candidates: list[tuple[int, NoteRef]] = []
    least = vectors.thresholds().duplicate
    for i, vector in enumerate(embedded):
        if i < len(skip) and skip[i]:
            continue
        hits = [(p, s) for p, s in vectors.search(vector, _SEARCH_POOL) if s >= least]
        refs = {ref.path: ref for ref in refs_by_paths(vectors.index, [p for p, _ in hits])}
        close = [refs[p] for p, _ in hits if p in refs and refs[p].type == "concept" and refs[p].id]
        candidates += [(i, ref) for ref in close[:CANDIDATES_PER_NOTE]]

    matches: list[NoteRef | None] = [None] * len(notes)
    if candidates:
        try:
            pairs = [(_card(notes[i]), _card(store.read_note(ref.path))) for i, ref in candidates]
            verdicts = judge_same_concept(pairs)
        except Exception as exc:
            logger.warning("判断内容是否重复失败，这次只按名字查重复: %s", exc)
            verdicts = []
        for (i, ref), same in zip(candidates, verdicts):
            # 候选按相似度从高到低排：一条新笔记有两条候选都被判成重复时，取更接近的那条
            if same and matches[i] is None:
                matches[i] = ref
    return matches, list(embedded)


def _card(note: Note) -> Card:
    fm = note.frontmatter
    return Card(title=fm.title, aliases=tuple(fm.aliases), summary=fm.summary, text=_own_text(note))


def _own_text(note: Note) -> str:
    """note_text 里去掉开头的标题、别名、概要那几行，只留正文里属于它自己的内容。"""
    lines = note_text(note).split("\n")
    head = 1 + bool(note.frontmatter.aliases) + bool(note.frontmatter.summary)
    return "\n".join(lines[head:])
