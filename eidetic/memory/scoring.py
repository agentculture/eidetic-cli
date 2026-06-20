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
from datetime import datetime, timezone

from eidetic.cli._errors import EXIT_USER_ERROR, CliError
from eidetic.memory.embed import EmbedClient, cosine
from eidetic.memory.record import DATE_UNKNOWN, Record

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

# -- signal strength (freshness) tunables ----------------------------------
#
# These port the proven QQ near-linear decay model. They are module-level
# constants so they can be tuned in one place without touching call sites.

# Per-day decay applied to both age and recall-staleness. ~0.01 means a record
# loses ~1% of its base strength per day of staleness and its age-factor halves
# at roughly 1 / DECAY_RATE = 100 days old.
DECAY_RATE: float = 0.01

# Baseline strength of a just-created, just-recalled record before any decay or
# access bonus is applied. Set at the neutral midpoint (0.5) so a fresh but
# never-recalled record sits exactly at neutral, leaving full headroom for the
# access bonus (+0.5) to register before the [0, 1] clamp saturates.
SIGNAL_BASE: float = 0.5

# Per-recall access bonus and its ceiling: each recall adds 0.05 up to +0.5.
_ACCESS_BONUS_PER_RECALL: float = 0.05
_ACCESS_BONUS_CAP: float = 0.5

# Link-corroboration term (frame v7 — UNDECIDED magnitude). A record with more
# corroborating links is, in principle, more trustworthy/durable; the weight of
# that effect is not yet decided, so it defaults to 0.0 (an exact no-op). When
# v7 lands, raise this to a small tunable value — the hook is already wired into
# signal_strength so only this constant needs to change.
CORROBORATION_WEIGHT: float = 0.0

# How strongly a non-neutral signal nudges the lexical/vector score in the
# blend. The blend is multiplicative around the neutral midpoint so that a
# neutral signal is an exact identity (see _blend_signal).
SIGNAL_BLEND_BETA: float = 0.25

# The neutral signal value: the freshness blend subtracts this midpoint so that
# a record sitting exactly at neutral neither lifts nor lowers its score.
_NEUTRAL_SIGNAL: float = 0.5


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 date/datetime string into an aware UTC datetime.

    Returns ``None`` when *value* is missing, the DATE_UNKNOWN sentinel, or
    unparseable — callers treat ``None`` as "no temporal information here".
    Naive datetimes are assumed UTC so arithmetic against ``now`` is consistent.
    """
    if not value or value == DATE_UNKNOWN:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _days_between(later: datetime, earlier: datetime) -> float:
    """Non-negative number of days from *earlier* to *later* (clamped at 0)."""
    delta = (later - earlier).total_seconds() / 86400.0
    return delta if delta > 0.0 else 0.0


def is_temporally_neutral(record: Record) -> bool:
    """True when a record carries no real temporal data.

    A neutral record (``created == DATE_UNKNOWN`` AND ``recall_count == 0`` AND
    ``last_recall is None``) must rank exactly as it does without any freshness
    blend — this is the back-compat invariant that keeps legacy/undated fixtures
    green. ``links`` is intentionally excluded: corroboration alone does not make
    a record "temporal", and the corroboration weight defaults to 0 anyway.
    """
    return (
        record.created == DATE_UNKNOWN and record.recall_count == 0 and record.last_recall is None
    )


def signal_strength(record: Record, now: str | datetime) -> float:
    """Pure, deterministic freshness/durability signal in [0, 1].

    Ports the QQ near-linear model::

        access_bonus = min(CAP, recall_count * PER_RECALL)
        age_factor   = 1 / (1 + days_since_creation * DECAY_RATE)
        staleness    = days_since_recall * DECAY_RATE
        strength     = (base - staleness + access_bonus + corroboration) * age_factor

    clamped to [0, 1]. ``now`` is supplied by the caller (never read from the
    clock inside this function) so the result is deterministic and testable.

    DATE_UNKNOWN / unparseable handling: when ``created`` carries no date the age
    term is decay-neutral (``age_factor == 1.0``); when ``last_recall`` carries
    no date the staleness term is 0. Undated/legacy records are therefore not
    penalised.
    """
    now_dt = _parse_dt(now) if isinstance(now, str) else now.astimezone(timezone.utc)
    if now_dt is None:
        # An unparseable 'now' would make the whole signal meaningless; fall back
        # to a fully neutral signal rather than guessing.
        return _NEUTRAL_SIGNAL

    access_bonus = min(_ACCESS_BONUS_CAP, record.recall_count * _ACCESS_BONUS_PER_RECALL)

    created_dt = _parse_dt(record.created)
    if created_dt is None:
        age_factor = 1.0  # decay-neutral: undated records get no age penalty
    else:
        days_old = _days_between(now_dt, created_dt)
        age_factor = 1.0 / (1.0 + days_old * DECAY_RATE)

    recall_dt = _parse_dt(record.last_recall)
    if recall_dt is None:
        staleness = 0.0  # never recalled (or undated recall) -> no staleness penalty
    else:
        staleness = _days_between(now_dt, recall_dt) * DECAY_RATE

    corroboration = CORROBORATION_WEIGHT * len(record.links)

    strength = (SIGNAL_BASE - staleness + access_bonus + corroboration) * age_factor
    return max(0.0, min(1.0, strength))


def _blend_signal(score: float, record: Record, now: str | datetime) -> float:
    """Multiplicatively nudge *score* by the record's freshness signal.

    Identity on neutral records: a temporally-neutral record returns *score*
    unchanged (the early return), and even for a non-neutral record sitting
    exactly at the neutral signal midpoint the factor is ``1 + beta*0 == 1``.
    Only records carrying real temporal data move::

        final = score * (1 + beta * (signal - neutral))

    Because the factor is >= 1 - beta*neutral > 0 for sane beta, the blend never
    flips a positive score negative and preserves the drop-non-matches contract
    (a 0.0 lexical score stays 0.0).
    """
    if is_temporally_neutral(record):
        return score
    signal = signal_strength(record, now)
    return score * (1.0 + SIGNAL_BLEND_BETA * (signal - _NEUTRAL_SIGNAL))


def _apply_blend(
    scored: list[tuple[float, Record]], now: str | datetime
) -> list[tuple[float, Record]]:
    """Apply the freshness blend to every (score, record) pair."""
    return [(_blend_signal(score, rec, now), rec) for score, rec in scored]


def rank(
    mode: str,
    query: str,
    candidates: list[Record],
    embed: EmbedClient,
    top_k: int,
    *,
    alpha: float = DEFAULT_ALPHA,
    case_sensitive: bool = False,
    now: str | datetime | None = None,
) -> list[Record]:
    """Rank *candidates* for *query* under *mode*, returning the top-k records.

    *candidates* must already be scope/filter-filtered by the backend. Each
    returned record carries a non-None ``score``.

    After the per-mode lexical/vector pass, a freshness blend nudges the score
    of every record that carries real temporal data (see :func:`_blend_signal`);
    records with neutral/absent temporal data are left exactly as scored, so
    pre-temporal callers and fixtures see unchanged scores and ordering.

    ``now`` (ISO-8601 string or datetime) is threaded through so the blend is
    deterministic in tests; it defaults to the current UTC time only here at the
    public entry point — the inner signal function never reads the clock itself.
    """
    if now is None:
        now = datetime.now(timezone.utc)

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

    # Freshness blend: identity on neutral records, a multiplicative nudge for
    # records carrying real temporal data. Applied uniformly across all four
    # modes so freshness behaves identically regardless of scorer.
    scored = _apply_blend(scored, now)

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


def _bm25_doc_score(
    doc: list[str],
    q_terms: list[str],
    df: dict[str, int],
    n: int,
    avgdl: float,
) -> float:
    """BM25 score of one tokenised *doc* against *q_terms* (collection stats df/n/avgdl)."""
    dl = len(doc)
    norm_dl = dl / avgdl if avgdl else 0.0
    tf: dict[str, int] = {}
    for term in doc:
        tf[term] = tf.get(term, 0) + 1
    score = 0.0
    for term in q_terms:
        freq = tf.get(term, 0)
        if freq == 0:
            continue
        idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
        denom = freq + _BM25_K1 * (1 - _BM25_B + _BM25_B * norm_dl)
        score += idf * (freq * (_BM25_K1 + 1)) / denom
    return score


def _bm25_scores(query: str, candidates: list[Record]) -> list[float]:
    """Return a BM25 score per candidate (parallel to *candidates*)."""
    docs = [_kw_tokenize(c.text) for c in candidates]
    n = len(docs)
    if n == 0:
        return []
    q_terms = _kw_tokenize(query)
    avgdl = sum(len(d) for d in docs) / n

    df: dict[str, int] = {}
    for doc in docs:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1

    return [_bm25_doc_score(doc, q_terms, df, n, avgdl) for doc in docs]


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
