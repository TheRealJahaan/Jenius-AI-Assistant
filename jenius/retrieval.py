"""Finding the context notes that match a question: TF-IDF cosine similarity.

Deterministic, free and fast for the few hundred notes a dataset has. Moved here from the original
assistant.py so the web assistant and Claude's search_notes tool share it.
"""
import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Protocol


class NoteLike(Protocol):
    note_code: str
    applies_to: str
    text: str


def tokenise(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _normalise(vec: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {t: v / norm for t, v in vec.items()}


class NoteIndex:
    def __init__(self, notes: Sequence[NoteLike]):
        self.notes = list(notes)
        docs = [tokenise(f"{n.applies_to} {n.text}") for n in self.notes]
        df = Counter(t for doc in docs for t in set(doc))
        n = len(docs)
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}  # smoothed IDF
        self.vectors = [self._vector(doc) for doc in docs]

    def _vector(self, tokens: list[str]) -> dict[str, float]:
        if not tokens:
            return {}
        counts = Counter(tokens)
        return _normalise({t: c / len(tokens) * self.idf.get(t, 0.0) for t, c in counts.items()})

    def search(self, query: str, top_k: int = 3) -> list[tuple[NoteLike, float]]:
        q = self._vector(tokenise(query))
        scored = [(note, sum(q.get(t, 0.0) * v for t, v in vec.items()))
                  for note, vec in zip(self.notes, self.vectors, strict=True)]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [(note, round(score, 4)) for note, score in scored[:top_k] if score > 0]
