"""
FastAPI application entry point.

Exposes:

  * POST /v1/check-party   — screen one party
  * POST /v1/check-batch   — screen up to 100 parties
  * GET  /v1/lists         — metadata on loaded data
  * GET  /health           — liveness/readiness probe
  * GET  /docs             — Swagger UI (auto-generated)
  * GET  /redoc            — ReDoc UI (auto-generated)
  * GET  /openapi.json     — machine-readable OpenAPI 3.1 spec

On startup the app downloads the CSL bulk feed from data.trade.gov into
memory. If the download fails the bundled sample dataset is used so the
API stays responsive for demos.
"""

import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.routers import meta, screening
from app.services.csl_client import CSLClient
from app.services.dps_service import DPSService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the CSL dataset once at startup, release at shutdown."""
    logger.info("DPS POC %s starting up", __version__)
    csl_client = CSLClient()
    await csl_client.load()
    app.state.csl_client = csl_client
    app.state.dps_service = DPSService(csl_client)
    logger.info(
        "Ready — serving from %s with %d entries",
        csl_client.data_source, len(csl_client.get_entries()),
    )
    yield
    logger.info("DPS POC shutting down")


app = FastAPI(
    title="DPS POC — Denied Party Screening",
    version=__version__,
    description=(
        "A standalone proof-of-concept Denied Party Screening API. Screens "
        "supplier / buyer / manufacturer / IOR / ship-to names against the "
        "US government's Consolidated Screening List (OFAC SDN, BIS Entity "
        "List, BIS Denied Persons, DDTC Debarred, Treasury SSI, and more). "
        "Ships with a live Trade.gov bulk feed plus a bundled sample dataset "
        "for offline demos.\n\n"
        "**Data source:** "
        "[data.trade.gov/downloadable_consolidated_screening_list]"
        "(https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json)"
        " — public, no API key required.\n\n"
        "**Matching:** rapidfuzz token-set ratio over normalized names "
        "(lowercased, accent-folded, legal-suffix-stripped)."
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
