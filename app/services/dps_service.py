"""
High-level screening orchestrator.

This is the module HTTP handlers call. It:

  1. Accepts a CheckPartyRequest.
  2. Runs the name against the in-memory CSL via the matcher.
  3. Classifies the result into 'passed' / 'manual_review' / 'failed'.
  4. Returns a CheckPartyResponse suitable for direct FastAPI return.

The thresholds live in config.Settings (match_min_score, match_fail_score)
so a caller can tune strictness without code changes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from app.config import settings
from app.models import (
    CheckBatchRequest,
    CheckBatchResponse,
    CheckPartyRequest,
    CheckPartyResponse,
    Match,
)
from app.services.csl_client import CSLClient
from app.services.matcher import find_matches

logger = logging.getLogger(__name__)


class DPSService:
    def __init__(self, csl_client: CSLClient) -> None:
        self.csl = csl_client

    # ── single ───────────────────────────────────────────────────────

    def check_party(self, req: CheckPartyRequest) -> CheckPartyResponse:
        raw = find_matches(
            req.name,
            self.csl.get_entries(),
            min_score=settings.match_min_score,
        )

        # Classify the aggregate status.
        top_score = raw[0]["match_score"] if raw else 0.0
        if top_score >= settings.match_fail_score:
            status = "failed"
        elif top_score >= settings.match_min_score:
            status = "manual_review"
        else:
            status = "passed"

        matches: List[Match] = [Match(**m) for m in raw]

        return CheckPartyResponse(
            party_name=req.name,
            party_type=req.party_type or "unknown",
            check_status=status,
            requires_manual_review=status in ("manual_review", "failed"),
            matches=matches,
            screened_at=datetime.now(timezone.utc).isoformat(),
            data_source=self.csl.data_source,
        )

    # ── batch ────────────────────────────────────────────────────────

    def check_batch(self, req: CheckBatchRequest) -> CheckBatchResponse:
        results = [self.check_party(p) for p in req.parties]
        return CheckBatchResponse(
            results=results,
            count=len(results),
            any_failed=any(r.check_status == "failed" for r in results),
            any_manual_review=any(
                r.check_status == "manual_review" for r in results
            ),
        )
