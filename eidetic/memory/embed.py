"""Embedding client with offline fallback for eidetic memory.

Uses an OpenAI-compatible embeddings endpoint when available; falls back to
a deterministic lexical embedding so the CLI works fully offline.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from typing import Any

# -----------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------

_DEFAULT_BASE_URL = "http://localhost:8101/v1"
_DEFAULT_MODEL = "text-embedding-3-small"
_EMBED_DIM = 128
_EMBED_TIMEOUT: float = float(os.environ.get("EIDETIC_EMBED_TIMEOUT", "10"))


# -----------------------------------------------------------------------
# Cosine similarity
# -----------------------------------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    """Return the cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a <= 0.0 or mag_b <= 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


# -----------------------------------------------------------------------
# Deterministic local embedding (offline fallback)
# -----------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Split *text* into whitespace tokens (lower-cased)."""
    return text.lower().split()


def _hash_float(token: str, dim: int) -> list[float]:
    """Hash a single token into a *dim*-length float vector in [-1, 1]."""
    vec: list[float] = []
    for i in range(dim):
        h = hashlib.sha256(f"{token}:{i}".encode()).digest()
        # Use first 8 bytes as a float in [0, 1), then scale to [-1, 1)
        raw = int.from_bytes(h[:8], "big") / (1 << 64)
        vec.append(2.0 * raw - 1.0)
    return vec


def _local_embed(text: str, dim: int = _EMBED_DIM) -> list[float]:
    """Deterministic lexical embedding: hash tokens and average."""
    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * dim
    acc = [0.0] * dim
    for token in tokens:
        vec = _hash_float(token, dim)
        for i in range(dim):
            acc[i] += vec[i]
    for i in range(dim):
        acc[i] /= len(tokens)
    # L2-normalise
    norm = math.sqrt(sum(x * x for x in acc))
    if norm > 0.0:
        acc = [x / norm for x in acc]
    return acc


# -----------------------------------------------------------------------
# EmbedClient
# -----------------------------------------------------------------------


class EmbedClient:
    """Client for remote embeddings with offline fallback."""

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self._base_url = (
            base_url or os.environ.get("EIDETIC_EMBED_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._model = model or os.environ.get("EIDETIC_EMBED_MODEL") or _DEFAULT_MODEL

    # -- public API -----------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return embeddings for *texts* (vectors only).

        Thin wrapper over :meth:`embed_detect` for callers that don't care
        whether the remote endpoint or the offline fallback produced them.
        """
        return self.embed_detect(texts)[0]

    def embed_detect(self, texts: list[str]) -> tuple[list[list[float]], bool]:
        """Return ``(embeddings, online)`` for *texts*.

        POSTs to the configured endpoint; on any connection error falls back
        to a deterministic local lexical embedding. ``online`` is ``True`` only
        when the remote endpoint answered — callers (e.g. hybrid recall) use it
        to avoid fusing meaningless hash-fallback cosine scores.
        """
        try:
            return self._remote_embed(texts), True
        except Exception:
            return [_local_embed(t) for t in texts], False

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        """Return a score per document indicating relevance to *query*.

        Uses a remote reranker when configured; otherwise falls back to a
        deterministic lexical overlap score.
        """
        try:
            return self._remote_rerank(query, docs)
        except Exception:
            return self._local_rerank(query, docs)

    # -- remote helpers ------------------------------------------------

    def _remote_embed(self, texts: list[str]) -> list[list[float]]:
        url = f"{self._base_url}/embeddings"
        payload = json.dumps({"model": self._model, "input": texts}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(
            req, timeout=_EMBED_TIMEOUT
        ) as resp:  # noqa: S310  # nosec B310
            body = json.loads(resp.read())
        # Sort by index to preserve input order
        items: list[tuple[int, list[float]]] = []
        for item in body["data"]:
            items.append((item["index"], item["embedding"]))
        items.sort(key=lambda t: t[0])
        return [emb for _, emb in items]

    def _remote_rerank(self, query: str, docs: list[str]) -> list[float]:
        url = f"{self._base_url}/rerank"
        payload = json.dumps(
            {
                "model": self._model,
                "query": query,
                "documents": docs,
            }
        ).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(
            req, timeout=_EMBED_TIMEOUT
        ) as resp:  # noqa: S310  # nosec B310
            body = json.loads(resp.read())
        results: list[dict[str, Any]] = body.get("results", body)
        # Build a map index -> score, then return in doc order. vLLM / Jina /
        # Cohere rerankers return `relevance_score`; some servers use `score`.
        score_map: dict[int, float] = {
            r["index"]: r.get("relevance_score", r.get("score", 0.0)) for r in results
        }
        return [score_map.get(i, 0.0) for i in range(len(docs))]

    # -- local fallbacks -----------------------------------------------

    def _local_rerank(self, query: str, docs: list[str]) -> list[float]:
        """Deterministic lexical overlap score (Jaccard-like)."""
        q_tokens = set(_tokenize(query))
        scores: list[float] = []
        for doc in docs:
            d_tokens = set(_tokenize(doc))
            if not q_tokens or not d_tokens:
                scores.append(0.0)
                continue
            overlap = len(q_tokens & d_tokens)
            union = len(q_tokens | d_tokens)
            scores.append(overlap / union)
        return scores
