"""
Fuzzy name matching over CSL entries.

The matcher normalizes both the query name and each candidate name, then
scores them using rapidfuzz's `token_set_ratio` — this handles word-order
differences (e.g. "Acme Trading Co." vs "Trading Company, Acme") and is
tolerant of punctuation and common suffixes.

Scoring is deliberately simple and explainable: every candidate gets one
float in [0.0, 1.0]. Callers interpret:

  - score ≥ MATCH_FAIL_SCORE  → 'failed' (DO NOT SHIP)
  - score ≥ MATCH_MIN_SCORE   → 'manual_review'
  - score <  MATCH_MIN_SCORE  → dropped (below noise floor)

Normalization drops common legal suffixes (LLC, Inc., Ltd., Co., Corp.,
GmbH, S.A., etc.) so "ACME, Inc." matches "ACME" cleanly. Country filter
is NOT used to drop matches — sanctions lists often omit the country or
use ambiguous values — but it IS captured in the response for the caller.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz

from app.config import settings

# Suffixes we strip before matching. Extend as needed.
_LEGAL_SUFFIXES = {
    "llc", "l.l.c", "inc", "inc.", "incorporated",
    "ltd", "ltd.", "limited",
    "co", "co.", "company",
    "corp", "corp.", "corporation",
    "gmbh", "ag", "sa", "s.a", "s.a.", "bv", "b.v", "n.v",
    "plc", "pty", "ltda", "oao", "ooo",
    "trading", "company",
}

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Lowercase + strip accents + drop punctuation + drop legal suffixes."""
    if not name:
        return ""
    # Fold to ASCII where possible.
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = name.lower()
    name = _PUNCT_RE.sub(" ", name)
    tokens = [t for t in name.split() if t and t not in _LEGAL_SUFFIXES]
    return _WS_RE.sub(" ", " ".join(tokens)).strip()


def score_pair(query: str, candidate: str) -> float:
    """Return a 0.0–1.0 similarity score between two names."""
    q = normalize_name(query)
    c = normalize_name(candidate)
    if not q or not c:
        return 0.0
    # token_set_ratio handles word-order + repeated-token noise robustly.
    return fuzz.token_set_ratio(q, c) / 100.0


def _collect_candidate_names(entry: Dict[str, Any]) -> List[str]:
    """
    Pull every searchable name from a single CSL entry — the primary name
    plus all aliases. The feed uses `name` for the canonical string and
    `alt_names` (list[str]) for aliases.
    """
    names: List[str] = []
    primary = entry.get("name")
    if primary:
        names.append(primary)
    alt = entry.get("alt_names") or []
    if isinstance(alt, list):
        names.extend(str(a) for a in alt if a)
    return names


def _entry_country(entry: Dict[str, Any]) -> Optional[str]:
    """CSL country fields vary — prefer explicit 2-letter code, fall back."""
    # Newer feed format.
    for key in ("country", "citizenships", "nationalities"):
        val = entry.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict) and first.get("country"):
                return first["country"]
    # Older shape: addresses[0].country
    addresses = entry.get("addresses") or []
    if isinstance(addresses, list) and addresses:
        first = addresses[0]
        if isinstance(first, dict) and first.get("country"):
            return first["country"]
    return None


def find_matches(
    query_name: str,
    entries: List[Dict[str, Any]],
    min_score: Optional[float] = None,
    max_results: int = 25,
) -> List[Dict[str, Any]]:
    """
    Score `query_name` against every entry's canonical + alias names.
    Return the top matches at or above `min_score`, sorted descending.

    The returned dicts use the 6-key canonical shape:

        {
            'matched_name': str,
            'match_score': float,
            'source': str,
            'list_type': str | None,
            'country': str | None,
            'programs': str,
            'source_info_url': str | None,
        }
    """
    threshold = min_score if min_score is not None else settings.match_min_score

    results: List[Dict[str, Any]] = []

    for entry in entries:
        best_score = 0.0
        best_name = None
        for cand in _collect_candidate_names(entry):
            s = score_pair(query_name, cand)
            if s > best_score:
                best_score = s
                best_name = cand
        if best_score >= threshold and best_name is not None:
            programs = entry.get("programs") or entry.get("sanctions_programs") or []
            if isinstance(programs, list):
                programs_str = ", ".join(str(p) for p in programs)
            else:
                programs_str = str(programs)

            results.append(
                {
                    "matched_name": best_name,
                    "match_score": round(best_score, 4),
                    "source": entry.get("source", "Unknown CSL source"),
                    "list_type": entry.get("type") or entry.get("list_type"),
                    "country": _entry_country(entry),
                    "programs": programs_str,
                    "source_info_url": entry.get("source_information_url")
                    or entry.get("source_list_url"),
                }
            )

    results.sort(key=lambda m: m["match_score"], reverse=True)
    return results[:max_results]
