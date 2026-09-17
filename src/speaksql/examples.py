"""Example-query library for NL→SQL few-shot retrieval.

This is the SpeakSQL analogue of Fabric's "example queries" feature:
users provide (natural-language question, canonical SQL) pairs, and
at query time we rank them by similarity to the user's question and
inject the top-K as few-shot examples in the LLM system prompt.

Three retriever implementations ship:

* `BM25Retriever` — token-frequency ranking (no external API, free,
  surprisingly good for short NL/SQL pairs)
* `EmbeddingRetriever` — cosine over dense vectors; pluggable via
  the `EmbeddingsProvider` Protocol so users can wire OpenAI /
  Azure OpenAI / anything else
* `DeterministicHashRetriever` — exact-match fallback used by the
  eval harness when no embeddings are configured

Adding more retrievers (e.g. FAISS, pgvector) is a matter of
implementing the `Retriever` Protocol.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Example:
    """A single (NL question, canonical SQL) pair.

    Optional fields help retrieval and explanation:

    * `tags` — free-form labels used by the BM25 retriever for
      token-level matches (e.g. ``["joins", "revenue"]``)
    * `description` — human-readable note about WHEN to use this
      example, fed to the LLM as a hint
    * `embedding` — populated by `EmbeddingRetriever.embed()` for
      cosine search; not used by BM25
    """

    question: str
    sql: str
    tags: tuple[str, ...] = ()
    description: str = ""
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True)
class RetrievedExample:
    """One example returned by a `Retriever`, with the score it earned."""

    example: Example
    score: float


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_examples_from_jsonl(path: str | Path) -> tuple[Example, ...]:
    """Load a JSONL file of (question, sql, tags, description) rows.

    Schema is forgiving — only ``question`` and ``sql`` are required.
    Tolerant of ``"sql"`` or ``"canonical"`` as the SQL key, and
    ``"tags"`` may be a string, list, or tuple.
    """
    examples: list[Example] = []
    p = Path(path)
    with p.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # Strip trailing commas so a JSON-array-style file works too.
            if line.endswith(","):
                line = line[:-1].strip()
                if not line:
                    continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{p}:{lineno}: invalid JSON in example library: {exc}"
                ) from exc
            if isinstance(row, str):
                # Tolerate plain-string lines (ignored).
                continue
            if not isinstance(row, dict):
                raise TypeError(
                    f"{p}:{lineno}: expected JSON object, got {type(row).__name__}"
                )
            q = (row.get("question") or "").strip()
            sql = (row.get("sql") or row.get("canonical") or "").strip()
            if not q or not sql:
                raise ValueError(
                    f"{p}:{lineno}: example missing question or sql: {row!r}"
                )
            tags_raw = row.get("tags") or ()
            if isinstance(tags_raw, str):
                tags: tuple[str, ...] = tuple(
                    t.strip() for t in tags_raw.split(",") if t.strip()
                )
            elif isinstance(tags_raw, list):
                tags = tuple(str(t) for t in tags_raw)
            elif isinstance(tags_raw, tuple):
                tags = tags_raw
            else:
                raise TypeError(
                    f"{p}:{lineno}: 'tags' must be string, list, or tuple"
                )
            description = (row.get("description") or "").strip()
            examples.append(
                Example(
                    question=q,
                    sql=sql,
                    tags=tags,
                    description=description,
                )
            )
    return tuple(examples)


# ---------------------------------------------------------------------------
# Retriever Protocol + implementations
# ---------------------------------------------------------------------------


class Retriever(Protocol):
    """A scorer over an in-memory example library.

    Implementations MUST be deterministic for a given (query, library)
    pair so eval results are reproducible. Implementations MUST NOT
    mutate the input library.
    """

    def retrieve(
        self,
        query: str,
        library: Sequence[Example],
        top_k: int = 5,
    ) -> tuple[RetrievedExample, ...]: ...


# --- Tokenizer shared by BM25 + HashRetriever ---


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|\d+")


def _tokenize(text: str) -> list[str]:
    """Lowercased word/number tokens. Keeps SQL identifiers intact.

    SQL keywords, column names, and English words all tokenize to the
    same shape so BM25 can match across languages without a stopword
    list (the IDF stage naturally downranks common terms).
    """
    return _TOKEN_RE.findall(text.lower())


# --- BM25 ---


class BM25Retriever:
    """Okapi BM25 (k1=1.5, b=0.75) over the question + tags + description.

    Pure-Python; no numpy/scipy dependency. Linear scan over the library,
    which is fine for the realistic example-query-library sizes (10s-1000s
    of pairs). For larger libraries, swap in an EmbeddingRetriever
    backed by a vector index.
    """

    K1 = 1.5
    B = 0.75

    def retrieve(
        self,
        query: str,
        library: Sequence[Example],
        top_k: int = 5,
    ) -> tuple[RetrievedExample, ...]:
        if not library or top_k <= 0:
            return ()

        # Build per-doc token counts and document frequencies on the fly.
        # Each "doc" is question + tags + description (concat).
        docs: list[list[str]] = []
        df: Counter[str] = Counter()
        doc_lens: list[int] = []
        for ex in library:
            blob = " ".join([ex.question, *ex.tags, ex.description])
            toks = _tokenize(blob)
            docs.append(toks)
            doc_lens.append(len(toks))
            for term in set(toks):
                df[term] += 1

        n = len(docs)
        avgdl = sum(doc_lens) / n if n else 0.0

        q_tokens = _tokenize(query)
        if not q_tokens:
            return ()

        scores: list[float] = []
        for idx, doc in enumerate(docs):
            dl = doc_lens[idx]
            tf: Counter[str] = Counter(doc)
            s = 0.0
            for qt in q_tokens:
                f = tf.get(qt, 0)
                if f == 0:
                    continue
                # IDF: log(1 + (N - df + 0.5) / (df + 0.5))
                idf = math.log1p((n - df[qt] + 0.5) / (df[qt] + 0.5))
                num = f * (self.K1 + 1)
                den = f + self.K1 * (1 - self.B + self.B * dl / (avgdl or 1.0))
                s += idf * num / den
            scores.append(s)

        # Sort by score descending, keep top_k with positive score.
        ranked = sorted(
            ((scores[i], i) for i in range(n) if scores[i] > 0),
            key=lambda x: -x[0],
        )[:top_k]
        return tuple(
            RetrievedExample(example=library[i], score=s) for s, i in ranked
        )


# --- Deterministic hash (used by eval) ---


class DeterministicHashRetriever:
    """Hash-based retriever for testing without any ML dep.

    Tokenizes both query and example.question; the score is the Jaccard
    similarity between the token sets. Deterministic across runs because
    no floating-point math is involved.
    """

    def retrieve(
        self,
        query: str,
        library: Sequence[Example],
        top_k: int = 5,
    ) -> tuple[RetrievedExample, ...]:
        q = set(_tokenize(query))
        if not q:
            return ()

        def jaccard(a: set[str], b: set[str]) -> float:
            if not a or not b:
                return 0.0
            inter = len(a & b)
            union = len(a | b)
            return inter / union if union else 0.0

        scored = []
        for ex in library:
            ex_tokens = set(_tokenize(ex.question))
            s = jaccard(q, ex_tokens)
            if s > 0:
                scored.append((s, ex))
        scored.sort(key=lambda x: -x[0])
        return tuple(
            RetrievedExample(example=ex, score=s) for s, ex in scored[:top_k]
        )


# --- Embedding-based ---


class EmbeddingsProvider(Protocol):
    """Pluggable embedding model. Implementations should be deterministic."""

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """Return one vector per input text, all of the same length."""
        ...


class EmbeddingRetriever:
    """Cosine similarity over dense embeddings.

    Embeddings are computed lazily — we embed the query once at the top
    of `retrieve()` and pre-compute any missing example embeddings in
    one batch.
    """

    def __init__(self, provider: EmbeddingsProvider) -> None:
        self._provider = provider

    def retrieve(
        self,
        query: str,
        library: Sequence[Example],
        top_k: int = 5,
    ) -> tuple[RetrievedExample, ...]:
        if not library or top_k <= 0:
            return ()

        # Build a list of examples whose embeddings are missing.
        to_embed: list[str] = []
        to_embed_idx: list[int] = []
        for i, ex in enumerate(library):
            if ex.embedding is None:
                to_embed.append(" ".join([ex.question, *ex.tags]))
                to_embed_idx.append(i)

        if to_embed:
            new_vectors = self._provider.embed(to_embed)
            # Mutating library is forbidden by the Protocol; return a
            # shallow-copied library with embeddings attached.
            new_library = list(library)
            for vec, idx in zip(new_vectors, to_embed_idx, strict=False):
                ex = new_library[idx]
                new_library[idx] = Example(
                    question=ex.question,
                    sql=ex.sql,
                    tags=ex.tags,
                    description=ex.description,
                    embedding=tuple(vec),
                )
            library = new_library

        q_vec = self._provider.embed([query])[0]
        scores: list[tuple[float, Example]] = []
        for ex in library:
            if ex.embedding is None:
                continue
            s = _cosine(q_vec, ex.embedding)
            if s > 0:
                scores.append((s, ex))
        scores.sort(key=lambda x: -x[0])
        return tuple(
            RetrievedExample(example=ex, score=s) for s, ex in scores[:top_k]
        )


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# --- Built-in offline embedding provider (hashed bag-of-words) ---


class HashedEmbeddingsProvider:
    """Deterministic offline embedding via feature hashing.

    Maps each token to one of `dim` buckets via SHA-1, then accumulates
    ±1 counts. Result is a sparse-ish dense vector with stable inner
    product. Not as good as a real model but works for offline eval
    and CI; users who want better recall plug in OpenAI / etc.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self._dim = dim

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        out: list[tuple[float, ...]] = []
        for t in texts:
            v = [0.0] * self._dim
            for tok in _tokenize(t):
                h = hashlib.sha1(tok.encode("utf-8")).digest()
                bucket = int.from_bytes(h[:4], "big") % self._dim
                sign = 1.0 if (h[4] & 1) else -1.0
                v[bucket] += sign
            # L2-normalize so cosine == inner product.
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append(tuple(x / norm for x in v))
        return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


__all__ = [
    "BM25Retriever",
    "DeterministicHashRetriever",
    "EmbeddingRetriever",
    "EmbeddingsProvider",
    "Example",
    "HashedEmbeddingsProvider",
    "RetrievedExample",
    "Retriever",
    "load_examples_from_jsonl",
]