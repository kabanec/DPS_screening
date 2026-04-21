"""
Meta endpoints — health probe and loaded-data summary.
"""

from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter

from app import __version__
from app.models import AdapterSummary, HealthResponse, ListsResponse, ListSummary

router = APIRouter(tags=["meta"])


def _sources():
    from app.main import app
    return app.state.sources


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness + readiness probe",
    description=(
        "Returns `status='ok'` when at least one source adapter loaded data. "
        "Returns `status='degraded'` when the service is running but every "
        "adapter failed or returned zero entries."
    ),
)
def health() -> HealthResponse:
    sources = _sources()
    entries = sources.get_entries()
    status = "ok" if entries else "degraded"
    return HealthResponse(
        status=status,
        version=__version__,
        data_source=sources.data_source,
        total_entries=len(entries),
    )


@router.get(
    "/v1/lists",
    response_model=ListsResponse,
    tags=["screening"],
    summary="Metadata about every loaded denied-party list",
    description=(
        "Returns per-adapter load status (US_CSL, UN, …) plus per-source-list "
        "row counts (OFAC SDN, BIS EL, UN Consolidated, …). Useful for "
        "admin dashboards and for sanity-checking that every expected list "
        "is present and fresh."
    ),
)
def lists() -> ListsResponse:
    sources = _sources()
    entries = sources.get_entries()

    counter = Counter(e.get("source", "Unknown") for e in entries)
    summaries = [
        ListSummary(source=src, entry_count=n)
        for src, n in sorted(counter.items(), key=lambda kv: kv[0])
    ]

    adapters = [AdapterSummary(**a) for a in sources.per_source_summary()]

    loaded = sources.loaded_at or datetime.now(timezone.utc)
    return ListsResponse(
        data_source=sources.data_source,
        loaded_at=loaded.isoformat(),
        total_entries=len(entries),
        adapters=adapters,
        lists=summaries,
    )
