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

# Aligned to the reference deployment documented in README.md and
# docs/contract.md; drift-tested by tests/test_embed_default_drift.py.
#
# The endpoint is the **lobes fleet gateway**, which fronts every role
# (cortex/embedder/reranker/...) on ONE OpenAI-compatible port. The per-role
# vLLM containers (`model-gear-vllm-embed`, `-rerank`) listen on :8000 inside
# the container network but are NOT published to the host, so any per-gear host
# port is unreachable. `lobes endpoint embedder` / `lobes capabilities` report
# the live value.
#
# eidetic-cli#28 aligned these constants to :8002 to end a code-vs-wrapper
# divergence — but :8002 was never host-reachable, so all surfaces agreed on an
# endpoint that always 401s/refuses. Because embed_detect() swallows every
# exception and falls back to a lexical hash vector, that failure was silent:
# `approximate`/`hybrid` recall kept answering, just not semantically.
_DEFAULT_BASE_URL = "http://localhost:8001/v1"
_DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"
# The gateway routes on the request's `model` field, so /v1/rerank must name the
# reranker gear — sending the embedding model here misroutes the request.
_DEFAULT_RERANK_MODEL = "Qwen/Qwen3-Reranker-0.6B"
_EMBED_DIM = 128
_EMBED_TIMEOUT: float = float(os.environ.get("EIDETIC_EMBED_TIMEOUT", "10"))

# The gateway enforces a bearer token. Checked in order; the first set wins.
# `EIDETIC_EMBED_API_KEY` is eidetic's own name; the others are the deployment
# variables the local fleet already exports, so a configured box needs no extra
# setup. Absent all three we send no header — an unauthenticated gateway (or a
# plain vLLM endpoint) still works, and a 401 degrades to the lexical fallback.
_API_KEY_VARS = (
    "EIDETIC_EMBED_API_KEY",
    "COLLEAGUE_API_KEY",
    "CULTURE_VLLM_API_KEY",
)


def _resolve_api_key() -> str | None:
    """Return the first API key set among :data:`_API_KEY_VARS`, else ``None``."""
    for var in _API_KEY_VARS:
        key = os.environ.get(var)
        if key:
            return key
    return None


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

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        rerank_model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("EIDETIC_EMBED_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._model = model or os.environ.get("EIDETIC_EMBED_MODEL") or _DEFAULT_MODEL
        self._rerank_model = (
            rerank_model or os.environ.get("EIDETIC_RERANK_MODEL") or _DEFAULT_RERANK_MODEL
        )
        self._api_key = api_key or _resolve_api_key()

    # -- request helpers ------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Return request headers, adding bearer auth when a key is configured."""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

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
            headers=self._headers(),
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
                "model": self._rerank_model,
                "query": query,
                "documents": docs,
            }
        ).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers=self._headers(),
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
