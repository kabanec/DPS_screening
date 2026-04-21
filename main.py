"""
FastAPI application entry point.

Exposes:

  * POST /v1/check-party   — screen one party against all enabled lists
  * POST /v1/check-batch   — screen up to 100 parties
  * GET  /v1/lists         — per-source metadata (status, counts, freshness)
  * GET  /health           — liveness/readiness probe
  * GET  /docs             — Swagger UI (auto-generated)
  * GET  /redoc            — ReDoc UI (auto-generated)
  * GET  /openapi.json     — machine-readable OpenAPI 3.1 spec

On startup the app concurrently loads every enabled denied-party list
(US CSL bulk feed, UN Security Council Consolidated List, …) into memory
via a shared SourceRegistry. A failure on one source does not abort the
others — callers see degraded per-source status in /v1/lists and /health.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import settings
from app.routers import meta, screening
from app.services.dps_service import DPSService
from app.services.source_registry import SourceAdapter, SourceRegistry
from dps_sources.un_sc import UNSCAdapter
from dps_sources.us_csl import USCSLAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


class _AdapterShim:
    """Wraps a shared dps_sources adapter to expose dps-poc SourceAdapter fields."""

    def __init__(self, inner: Any, *, name: str) -> None:
        self._inner = inner
        self.short_code: str = inner.short_code
        self.name: str = name
        self._loaded_at: Optional[datetime] = None

    async def load(self) -> None:
        await self._inner.load()
        self._loaded_at = datetime.utcnow()

    def get_entries(self) -> List[Dict[str, Any]]:
        out = []
        for e in self._inner.get_entries():
            entry = dict(e)
            # dps-poc matcher reads "source"; shared adapters use "source_list"
            if "source" not in entry:
                entry["source"] = entry.get("source_list", self.short_code)
            out.append(entry)
        return out

    @property
    def data_source(self) -> str:
        if not self._loaded_at:
            return "failed"
        return "live_feed" if self._inner.get_entries() else "failed"

    @property
    def loaded_at(self) -> Optional[datetime]:
        return self._loaded_at


def _build_registry() -> SourceRegistry:
    """Instantiate the enabled adapters based on config toggles."""
    adapters: List[SourceAdapter] = []
    if settings.enable_source_us_csl:
        sample_path = Path(settings.csl_sample_path) if settings.csl_sample_path else None
        adapters.append(_AdapterShim(
            USCSLAdapter(
                feed_url=settings.csl_bulk_url,
                timeout=float(settings.csl_http_timeout),
                use_sample_only=settings.use_sample_only,
                sample_path=sample_path,
            ),
            name="US Consolidated Screening List",
        ))
    if settings.enable_source_un:
        adapters.append(_AdapterShim(
            UNSCAdapter(),
            name="UN Security Council Consolidated List",
        ))
    if not adapters:
        logger.warning(
            "No source adapters enabled — /v1/check-party will return "
            "'passed' for every input. Enable at least one source."
        )
    return SourceRegistry(adapters)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load every enabled denied-party list concurrently at startup."""
    logger.info("DPS POC %s starting up", __version__)
    registry = _build_registry()
    await registry.load_all()
    app.state.sources = registry
    app.state.dps_service = DPSService(registry)
    logger.info(
        "Ready — serving %d entries across %d source(s) (rollup=%s)",
        len(registry.get_entries()),
        len(registry.per_source_summary()),
        registry.data_source,
    )
    yield
    logger.info("DPS POC shutting down")


app = FastAPI(
    title="DPS POC — Denied Party Screening",
    version=__version__,
    description=(
        "A standalone proof-of-concept Denied Party Screening API. Screens "
        "legal entities **and** natural persons — supplier, buyer, "
        "manufacturer, IOR, ship-to — against multiple government sanctions "
        "lists through a single, normalized interface.\n\n"
        "**Sources wired today:**\n"
        "- US Consolidated Screening List (Trade.gov bulk feed) — OFAC SDN, "
        "BIS Entity List, BIS Denied Persons, DDTC Debarred, Treasury SSI, "
        "and 7 more US lists.\n"
        "- UN Security Council Consolidated Sanctions List "
        "(scsanctions.un.org XML feed) — 1267 / 1988 / 1540 regimes.\n\n"
        "**Sources architected for (adapter drop-in):** UK OFSI, EU CFSP, "
        "Canada OSFI, Australia DFAT, Switzerland SECO, Japan METI.\n\n"
        "**Matching:** rapidfuzz token-set ratio over normalized names "
        "(lowercased, accent-folded, legal-suffix-stripped). Same scoring "
        "for Individuals and Entities — the matcher is type-agnostic.\n\n"
        "**Resilience:** every source loads concurrently at startup; one "
        "source failing does not abort the others. Per-source health is "
        "surfaced on `/health` and `/v1/lists`."
    ),
    contact={"name": "TradeShield / DPS POC"},
    license_info={"name": "MIT"},
    lifespan=lifespan,
)

# CORS — open for POC; lock down per deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(meta.router)
app.include_router(screening.router)
