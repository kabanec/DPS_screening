"""
Screening endpoints — the core of the API.
"""

from fastapi import APIRouter, Depends

from app.models import (
    CheckBatchRequest,
    CheckBatchResponse,
    CheckPartyRequest,
    CheckPartyResponse,
)
from app.services.dps_service import DPSService

router = APIRouter(prefix="/v1", tags=["screening"])


def get_dps_service() -> DPSService:
    """
    Dependency provider — resolved from app.state in main.py at request
    time. Using a getter (rather than importing a global) keeps the
    service mockable in tests.
    """
    from app.main import app  # local import avoids circular dep
    return app.state.dps_service


@router.post(
    "/check-party",
    response_model=CheckPartyResponse,
    summary="Screen a single party against every loaded denied-party list",
    description=(
        "Runs the supplied name (company, vessel, or natural person) against "
        "every in-memory entry across every enabled source — US CSL (OFAC "
        "SDN, BIS Entity List, BIS Denied Persons, DDTC Debarred, Treasury "
        "SSI, …), UN Security Council Consolidated List, and any other "
        "adapters wired into the registry — using fuzzy token-set matching. "
        "Returns all matches at or above MATCH_MIN_SCORE, classifies the "
        "aggregate result as `passed` / `manual_review` / `failed`, and "
        "flags whether human review is required."
    ),
    responses={
        200: {
            "description": "Screening completed successfully (status in body).",
        },
        422: {
            "description": "Validation error — see FastAPI default error shape.",
        },
    },
)
def check_party(
    req: CheckPartyRequest,
    svc: DPSService = Depends(get_dps_service),
) -> CheckPartyResponse:
    return svc.check_party(req)


@router.post(
    "/check-batch",
    response_model=CheckBatchResponse,
    summary="Screen up to 100 parties in a single call",
    description=(
        "Bulk variant of `/check-party`. Useful for screening every party "
        "on a customs invoice (seller, buyer, manufacturer, IOR, ship-to) "
        "in one round trip. Returns per-party results plus aggregate flags "
        "`any_failed` and `any_manual_review` for quick triage."
    ),
)
def check_batch(
    req: CheckBatchRequest,
    svc: DPSService = Depends(get_dps_service),
) -> CheckBatchResponse:
    return svc.check_batch(req)
