"""Per-mode ranking for eidetic recall.

One place, four modes — every backend gathers candidates (scope + filter) then
delegates ranking here, so the modes behave identically across files/mongo/neo4j:

- ``exact``       — case-insensitive verbatim substring match (``--case-sensitive``
                    tightens it). Score is the coverage ratio ``len(query)/len(text)``.
                    Non-matching records are dropped. No embeddings.
- ``keyword``     — BM25 lexical scoring over the candidate set. Records with no
                    query-term overlap are dropped. No embeddings.
- ``approximate`` — vector cosine over freshly embedded query + candidate text
                    (today's semantic behaviour). Needs the embed server.
- ``hybrid``      — weighted alpha blend of min-max-normalised approximate +
                    keyword scores: ``alpha*vector + (1-alpha)*keyword``. When the
                    embed server is offline (hash fallback), ``alpha`` collapses to
                    0 so hybrid never fuses meaningless cosine — it degrades to
                    keyword-only ranking.

The embed server is only ever touched by ``approximate`` and ``hybrid``; ``exact``
and ``keyword`` are pure lexical and work fully offline.
"""

from __future__ import annotations

import math
import re

from eidetic.cli._errors import EXIT_USER_ERROR, CliError
from eidetic.memory.embed import EmbedClient, cosine
from eidetic.memory.record import Record

# Lexical tokeniser for keyword/BM25: alphanumeric runs, lower-cased. Unlike the
# embedding tokeniser (whitespace split), this strips punctuation so "Iceland."
# matches the query "iceland".
_WORD_RE = re.compile(r"[a-z0-9]+")


def _kw_tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


MODES: tuple[str, ...] = ("exact", "approximate", "keyword", "hybrid")
DEFAULT_MODE = "hybrid"
DEFAULT_ALPHA = 0.5

_BM25_K1 = 1.5
_BM25_B = 0.75


def rank(
    mode: str,
    query: str,
    candidates: list[Record],
    embed: EmbedClient,
    top_k: int,
    *,
    alpha: float = DEFAULT_ALPHA,
    case_sensitive: bool = False,
) -> list[Record]:
    """Rank *candidates* for *query* under *mode*, returning the top-k records.

    *candidates* must already be scope/filter-filtered by the backend. Each
    returned record carries a non-None ``score``.
    """
    if mode not in MODES:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"unknown recall mode: {mode!r}",
            remediation=f"--mode must be one of: {', '.join(MODES)}",
        )
    if not 0.0 <= alpha <= 1.0:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--alpha must be between 0.0 and 1.0 (got {alpha})",
            remediation="pass e.g. --alpha 0.5",
        )

    if mode == "exact":
        scored = _score_exact(query, candidates, case_sensitive)
    elif mode == "keyword":
        scored = _score_keyword(query, candidates)
    elif mode == "approximate":
        scored = _score_approximate(query, candidates, embed)
    else:  # hybrid
        scored = _score_hybrid(query, candidates, embed, alpha)

    scored.sort(key=lambda t: t[0], reverse=True)
    results: list[Record] = []
    for score, record in scored[:top_k]:
        record.score = score
        results.append(record)
    return results


# -- exact -----------------------------------------------------------------


def _score_exact(
    query: str, candidates: list[Record], case_sensitive: bool
) -> list[tuple[float, Record]]:
    needle = query if case_sensitive else query.lower()
    scored: list[tuple[float, Record]] = []
    for record in candidates:
        hay = record.text if case_sensitive else record.text.lower()
        if needle and needle in hay and record.text:
            # Coverage ratio: a record that *is* the query scores ~1.0; a long
            # doc that merely contains it scores lower. Always in (0, 1].
            scored.append((min(1.0, len(query) / len(record.text)), record))
    return scored


# -- keyword (BM25) --------------------------------------------------------


def _bm25_scores(query: str, candidates: list[Record]) -> list[float]:
    """Return a BM25 score per candidate (parallel to *candidates*)."""
    docs = [_kw_tokenize(c.text) for c in candidates]
    n = len(docs)
    if n == 0:
        return []
    q_terms = _kw_tokenize(query)
    avgdl = sum(len(d) for d in docs) / n if n else 0.0

    df: dict[str, int] = {}
    for doc in docs:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1

    scores: list[float] = []
    for doc in docs:
        dl = len(doc)
        tf: dict[str, int] = {}
        for term in doc:
            tf[term] = tf.get(term, 0) + 1
        score = 0.0
        for term in q_terms:
            freq = tf.get(term, 0)
            if freq == 0:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            denom = freq + _BM25_K1 * (1 - _BM25_B + _BM25_B * (dl / avgdl if avgdl else 0.0))
            score += idf * (freq * (_BM25_K1 + 1)) / denom
        scores.append(score)
    return scores


def _score_keyword(query: str, candidates: list[Record]) -> list[tuple[float, Record]]:
    scores = _bm25_scores(query, candidates)
    # Drop records with no query-term overlap — keyword search returns matches.
    return [(s, rec) for s, rec in zip(scores, candidates) if s > 0.0]


# -- approximate (vector cosine) -------------------------------------------


def _embed_query_and_docs(
    query: str, candidates: list[Record], embed: EmbedClient
) -> tuple[list[float], list[list[float]], bool]:
    """Embed query + candidate texts in one batch; return (q_emb, doc_embs, online).

    Texts are embedded fresh at query time (not read from storage) so vector
    dimensions always match and the online/offline status is uniform across
    backends.
    """
    texts = [query] + [c.text for c in candidates]
    vectors, online = embed.embed_detect(texts)
    return vectors[0], vectors[1:], online


def _score_approximate(
    query: str, candidates: list[Record], embed: EmbedClient
) -> list[tuple[float, Record]]:
    if not candidates:
        return []
    q_emb, doc_embs, _ = _embed_query_and_docs(query, candidates, embed)
    return [(cosine(q_emb, d), rec) for d, rec in zip(doc_embs, candidates)]


# -- hybrid (weighted alpha blend) -----------------------------------------


def _minmax(values: list[float]) -> list[float]:
    """Normalise *values* to [0, 1]. All-equal -> 1.0 if positive else 0.0."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0 if hi > 0.0 else 0.0] * len(values)
    span = hi - lo
    return [(v - lo) / span for v in values]


def _score_hybrid(
    query: str, candidates: list[Record], embed: EmbedClient, alpha: float
) -> list[tuple[float, Record]]:
    if not candidates:
        return []
    q_emb, doc_embs, online = _embed_query_and_docs(query, candidates, embed)
    vec_scores = [cosine(q_emb, d) for d in doc_embs]
    kw_scores = _bm25_scores(query, candidates)

    # Offline embeddings are meaningless hash vectors — fall back to keyword-only.
    eff_alpha = alpha if online else 0.0

    vec_norm = _minmax(vec_scores)
    kw_norm = _minmax(kw_scores)
    blended = [
        (eff_alpha * vec_norm[i] + (1.0 - eff_alpha) * kw_norm[i], candidates[i])
        for i in range(len(candidates))
    ]
    # A 0.0 blend means neither the vector nor the keyword signal placed the
    # record above the batch floor — drop it as a non-match. This keeps hybrid
    # consistent with keyword mode (and with offline hybrid, which collapses to
    # keyword-only) instead of padding top-k with irrelevant records.
    return [(score, rec) for score, rec in blended if score > 0.0]
