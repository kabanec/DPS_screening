"""
Pydantic request/response models.

These models are the source of truth for the OpenAPI schema surfaced at
/docs and /openapi.json — if you change a field, the spec updates
automatically.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ── Requests ────────────────────────────────────────────────────────────

class CheckPartyRequest(BaseModel):
    """Screen a single party against the Consolidated Screening List."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=300,
        description="Legal or trade name of the party to screen.",
        examples=["ACME Trading Company"],
    )
    country: Optional[str] = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 country code (e.g. 'IR', 'CN').",
        examples=["IR"],
    )
    address: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Optional street address — used to boost match precision.",
    )
    party_type: Optional[str] = Field(
        default="unknown",
        max_length=50,
        description=(
            "Caller-supplied label for downstream auditing — 'supplier', "
            "'buyer', 'manufacturer', 'ior', 'ship_to', etc."
        ),
        examples=["supplier"],
    )


class CheckBatchRequest(BaseModel):
    """Screen multiple parties in one call."""

    parties: List[CheckPartyRequest] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of parties to screen (up to 100 per request).",
    )


# ── Responses ───────────────────────────────────────────────────────────

class Match(BaseModel):
    """A single match against an entry on a denied-party list."""

    matched_name: str = Field(description="The listed name that matched.")
    match_score: float = Field(
        ge=0.0, le=1.0,
        description="Similarity score between the input name and the listed name (0.0–1.0).",
    )
    source: str = Field(
        description=(
            "Human-readable source list name (e.g. 'OFAC Specially Designated "
            "Nationals', 'Entity List (EL) - Bureau of Industry and Security')."
        ),
    )
    list_type: Optional[str] = Field(
        default=None,
        description="Short code for the source list (e.g. 'SDN', 'EL', 'DPL').",
    )
    country: Optional[str] = Field(
        default=None,
        description="Listed country for this entry (may be None if the list is global).",
    )
    programs: str = Field(
        default="",
        description=(
            "Sanctions programs the entry is listed under (e.g. 'IRAN', "
            "'NPWMD', 'SDGT'). May be empty."
        ),
    )
    source_info_url: Optional[str] = Field(
        default=None,
        description="Authoritative URL for this listing (if provided by the source).",
    )


CheckStatus = Literal["passed", "manual_review", "failed"]


class CheckPartyResponse(BaseModel):
    """Result of screening a single party."""

    party_name: str = Field(description="The input name that was screened.")
    party_type: str = Field(description="Echoed from the request (or 'unknown').")
    check_status: CheckStatus = Field(
        description=(
            "'passed' = no meaningful matches; 'manual_review' = partial "
            "match(es) warranting human review; 'failed' = at least one "
            "high-confidence match (treat as DO NOT SHIP)."
        ),
    )
    requires_manual_review: bool = Field(
        description="Convenience flag: True for 'manual_review' and 'failed'.",
    )
    matches: List[Match] = Field(
        default_factory=list,
        description="All matches at or above the configured MATCH_MIN_SCORE, sorted descending.",
    )
    screened_at: str = Field(
        description="ISO-8601 UTC timestamp of when this screening was performed.",
    )
    data_source: str = Field(
        description=(
            "Which data source answered this check: 'live_csl' = Trade.gov "
            "bulk feed cached at startup; 'sample' = bundled fallback used "
            "when the live fetch failed or USE_SAMPLE_ONLY is set."
        ),
    )


class CheckBatchResponse(BaseModel):
    """Result of a batch screening call."""

    results: List[CheckPartyResponse]
    count: int = Field(description="Number of parties screened.")
    any_failed: bool = Field(description="True if any party returned check_status='failed'.")
    any_manual_review: bool = Field(
        description="True if any party returned check_status='manual_review'.",
    )


# ── Meta ────────────────────────────────────────────────────────────────

class ListSummary(BaseModel):
    """Row-count summary for a single source list inside the CSL feed."""

    source: str
    entry_count: int


class ListsResponse(BaseModel):
    """Metadata about the currently loaded CSL dataset."""

    data_source: str = Field(description="'live_csl' or 'sample'.")
    loaded_at: str = Field(description="ISO-8601 timestamp of when the data was loaded.")
    total_entries: int
    lists: List[ListSummary]


class HealthResponse(BaseModel):
    """Liveness + readiness probe payload."""

    status: Literal["ok", "degraded"]
    version: str
    data_source: str
    total_entries: int
