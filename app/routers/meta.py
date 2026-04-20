"""
Meta endpoints — health probe and loaded-data summary.
"""

from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter

from app import __version__
from app.models import HealthResponse, ListsResponse, ListSummary

router = APIRouter(tags=["meta"])


def _csl():
    from app.main import app
    return app.state.csl_client


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness + readiness probe",
    description=(
        "Returns `status='ok'` when the CSL dataset is loaded and non-empty. "
        "Returns `status='degraded'` when the service is running but has no "
        "data (e.g. initial fetch failed and sample fallback was empty)."
    ),
)
def health() -> HealthResponse:
    csl = _csl()
    entries = csl.get_entries()
    status = "ok" if entries else "degraded"
    return HealthResponse(
        status=status,
        version=__version__,
        data_source=csl.data_source,
        total_entries=len(entries),
    )


@router.get(
    "/v1/lists",
    response_model=ListsResponse,
    tags=["screening"],
    summary="Metadata about the currently loaded CSL dataset",
    description=(
        "Returns counts per source list. Useful for sanity-checking that "
        "every expected list (OFAC SDN, BIS EL, BIS DPL, DDTC, SSI, etc.) "
        "is present, and for showing loaded-at timestamps in admin UIs."
    ),
)
def lists() -> ListsResponse:
    csl = _csl()
    entries = csl.get_entries()
    counter = Counter(e.get("source", "Unknown") for e in entries)
    summaries = [
        ListSummary(source=src, entry_count=n)
        for src, n in sorted(counter.items(), key=lambda kv: kv[0])
    ]
    loaded = csl.loaded_at or datetime.now(timezone.utc)
    return ListsResponse(
        data_source=csl.data_source,
        loaded_at=loaded.isoformat(),
        total_entries=len(entries),
        lists=summaries,
    )
