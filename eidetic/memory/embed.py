"""Embedding client with offline fallback for eidetic memory.

Uses an OpenAI-compatible embeddings endpoint when available; falls back to
a deterministic lexical embedding so the CLI works fully offline.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import sys
import urllib.error
import urllib.parse
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
# Absent all of them we send no header — an unauthenticated gateway (or a plain
# vLLM endpoint) still works, and a 401 degrades to the lexical fallback.
#
# The two classes are deliberately NOT equivalent. `EIDETIC_EMBED_API_KEY` is
# eidetic's own variable: setting it is an explicit statement about *this*
# client, so it is honoured wherever the operator points the endpoint. The
# others belong to sibling tools and are *borrowed* as a convenience so a
# configured box needs no extra setup — but the operator never paired them with
# eidetic's endpoint, so we won't hand them to an arbitrary host in cleartext.
# See :meth:`EmbedClient._may_send_key`.
_EXPLICIT_API_KEY_VAR = "EIDETIC_EMBED_API_KEY"
_BORROWED_API_KEY_VARS = ("COLLEAGUE_API_KEY", "CULTURE_VLLM_API_KEY")
_API_KEY_VARS = (_EXPLICIT_API_KEY_VAR, *_BORROWED_API_KEY_VARS)

# Emitted at most once per process so a withheld key is never a silent
# degradation — withholding it means a 401, and a 401 means the lexical
# fallback, which is exactly the failure mode this module already hides too well.
_warned_withheld = False


def _resolve_api_key() -> tuple[str | None, bool]:
    """Return ``(key, borrowed)`` for the first API key set in the environment.

    ``borrowed`` is ``True`` when the key came from a sibling tool's variable
    rather than eidetic's own, which restricts where it may be sent.
    """
    explicit = os.environ.get(_EXPLICIT_API_KEY_VAR)
    if explicit:
        return explicit, False
    for var in _BORROWED_API_KEY_VARS:
        key = os.environ.get(var)
        if key:
            return key, True
    return None, False


def _is_local_or_encrypted(base_url: str) -> bool:
    """Return whether *base_url* is loopback or HTTPS.

    Either is a safe destination for a borrowed credential: loopback never
    leaves the machine, and HTTPS is not readable in transit. `lobes tunnel`
    publishes the fleet over HTTPS, so remote is legitimate — it is *cleartext
    to a remote host* that we refuse.
    """
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme == "https":
        return True
    host = (parsed.hostname or "").lower()
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost" or host.endswith(".localhost")


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
        # `is None`, not truthiness: an explicit `api_key=""` is a deliberate
        # "send no Authorization header", and must not fall back to the
        # environment. A caller-supplied key is never treated as borrowed —
        # passing it names this client's credential outright.
        if api_key is None:
            self._api_key, self._api_key_borrowed = _resolve_api_key()
        else:
            self._api_key, self._api_key_borrowed = api_key, False

    # -- request helpers ------------------------------------------------

    def _may_send_key(self) -> bool:
        """Return whether the resolved key may be sent to the configured URL.

        An explicit key always may: the operator named both the credential and
        the endpoint. A *borrowed* one — taken from a sibling tool's variable
        that was never paired with eidetic's endpoint — is withheld from a
        cleartext remote host, so overriding `EIDETIC_EMBED_URL` cannot quietly
        forward another tool's credential off the machine.
        """
        if not self._api_key_borrowed:
            return True
        return _is_local_or_encrypted(self._base_url)

    def _headers(self) -> dict[str, str]:
        """Return request headers, adding bearer auth when a key may be sent."""
        headers = {"Content-Type": "application/json"}
        if not self._api_key:
            return headers
        if not self._may_send_key():
            self._warn_withheld()
            return headers
        headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _warn_withheld(self) -> None:
        """Warn once per process that a borrowed credential was withheld."""
        global _warned_withheld
        if _warned_withheld:
            return
        _warned_withheld = True
        print(
            f"warning: withholding the borrowed API key from {self._base_url} "
            f"(cleartext remote endpoint); requests will be unauthenticated and "
            f"may fall back to local lexical scoring.\n"
            f"hint: set {_EXPLICIT_API_KEY_VAR} to send a credential to this "
            f"endpoint deliberately, or use an https:// URL.",
            file=sys.stderr,
        )

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
