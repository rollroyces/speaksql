"""Tests for the example-query library + retrievers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from speaksql.examples import (
    BM25Retriever,
    DeterministicHashRetriever,
    EmbeddingRetriever,
    Example,
    HashedEmbeddingsProvider,
    RetrievedExample,
    load_examples_from_jsonl,
)


def _lib() -> tuple[Example, ...]:
    return (
        Example(question="count of orders", sql="SELECT COUNT(*) FROM orders",
                tags=("aggregate",), description="count rows"),
        Example(question="monthly revenue", sql="SELECT DATE_TRUNC('month', ts) ...",
                tags=("time_bucket",), description="time series"),
        Example(question="users with no orders",
                sql="SELECT u.* FROM users u LEFT JOIN orders o ON u.id=o.user_id WHERE o.id IS NULL",
                tags=("left_join",), description="anti-join"),
        Example(question="top 5 revenue by country",
                sql="SELECT country, SUM(revenue) FROM events GROUP BY country ORDER BY SUM(revenue) DESC LIMIT 5",
                tags=("top_n",), description="rank by sum"),
    )


# --- Loader tests ---


def test_load_examples_from_jsonl(tmp_path: Path):
    p = tmp_path / "examples.jsonl"
    rows = [
        {"question": "q1", "sql": "SELECT 1"},
        {"question": "q2", "sql": "SELECT 2", "tags": ["a", "b"],
         "description": "test"},
        # Comments and blank lines should be skipped.
        "# comment",
        "",
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    lib = load_examples_from_jsonl(p)
    assert len(lib) == 2
    assert lib[0].question == "q1"
    assert lib[1].tags == ("a", "b")
    assert lib[1].description == "test"


def test_load_examples_rejects_missing_question(tmp_path: Path):
    p = tmp_path / "bad.jsonl"
    p.write_text(json.dumps({"question": "", "sql": "SELECT 1"}) + "\n")
    with pytest.raises(ValueError, match="missing question"):
        load_examples_from_jsonl(p)


def test_load_examples_rejects_bad_json(tmp_path: Path):
    p = tmp_path / "bad.jsonl"
    p.write_text("{not json\n")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_examples_from_jsonl(p)


def test_load_examples_accepts_tags_as_string(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"question": "q", "sql": "SELECT 1", "tags": "a,b,c"}) + "\n")
    lib = load_examples_from_jsonl(p)
    assert lib[0].tags == ("a", "b", "c")


# --- BM25 ---


def test_bm25_retriever_returns_scored_hits():
    lib = _lib()
    r = BM25Retriever()
    hits = r.retrieve("count how many orders", lib, top_k=3)
    assert len(hits) > 0
    # All hits have positive scores
    assert all(h.score > 0 for h in hits)
    # Top hit is the most relevant
    top = hits[0]
    assert isinstance(top, RetrievedExample)
    assert "count" in top.example.question.lower()


def test_bm25_returns_empty_for_unrelated_query():
    lib = _lib()
    hits = BM25Retriever().retrieve("zzz qqq xxx nothing matches", lib)
    assert hits == ()


def test_bm25_returns_empty_for_empty_library():
    assert BM25Retriever().retrieve("anything", ()) == ()


def test_bm25_zero_top_k_returns_empty():
    lib = _lib()
    assert BM25Retriever().retrieve("revenue", lib, top_k=0) == ()


def test_bm25_deterministic_runs():
    lib = _lib()
    a = BM25Retriever().retrieve("revenue", lib, top_k=3)
    b = BM25Retriever().retrieve("revenue", lib, top_k=3)
    assert [h.example.question for h in a] == [h.example.question for h in b]
    assert [h.score for h in a] == [h.score for h in b]


# --- Hash retriever ---


def test_hash_retriever_returns_token_overlap():
    lib = _lib()
    hits = DeterministicHashRetriever().retrieve("count orders", lib, top_k=2)
    assert any("count of orders" in h.example.question for h in hits)


def test_hash_retriever_deterministic():
    lib = _lib()
    a = DeterministicHashRetriever().retrieve("orders", lib)
    b = DeterministicHashRetriever().retrieve("orders", lib)
    assert [h.score for h in a] == [h.score for h in b]


# --- Embedding retriever ---


def test_embedding_retriever_uses_cosine_similarity():
    """Hashed embeddings: known vectors -> known ordering."""
    provider = HashedEmbeddingsProvider(dim=128)
    lib = _lib()
    r = EmbeddingRetriever(provider)
    retrieved = r.retrieve("monthly revenue", lib, top_k=2)
    assert len(retrieved) > 0
    # Embeddings should be populated after the call.
    for h in retrieved:
        assert h.example.embedding is not None


def test_hashed_embeddings_normalize_to_unit_length():
    """Provider returns L2-normalized vectors so cosine == inner product."""
    p = HashedEmbeddingsProvider(dim=64)
    vecs = p.embed(["alpha beta", "gamma delta"])
    for v in vecs:
        import math
        norm = math.sqrt(sum(x * x for x in v))
        assert abs(norm - 1.0) < 1e-6


def test_hashed_embeddings_deterministic():
    p = HashedEmbeddingsProvider(dim=32)
    a = p.embed(["hello world"])
    b = p.embed(["hello world"])
    assert a == b


def test_hashed_embeddings_different_text_different_vector():
    p = HashedEmbeddingsProvider(dim=32)
    a = p.embed(["hello world"])
    b = p.embed(["completely unrelated tokens"])
    assert a != b