"""
CSL bulk feed client.

The US International Trade Administration publishes the full Consolidated
Screening List as a single JSON document at a stable, public URL. The file
covers:

  * OFAC — Specially Designated Nationals (SDN)
  * OFAC — Non-SDN Sectoral Sanctions Identifications (SSI)
  * OFAC — Non-SDN Palestinian Legislative Council List (NS-PLC)
  * OFAC — Foreign Sanctions Evaders List (FSE)
  * OFAC — Non-SDN Menu-Based Sanctions List (NS-MBS)
  * BIS — Entity List (EL)
  * BIS — Denied Persons List (DPL)
  * BIS — Unverified List (UVL)
  * BIS — Military End User List (MEU)
  * DDTC — Debarred List (DTC)
  * State — Nonproliferation Sanctions
  * ITA — Israeli Boycott Requester List

No API key is required. The upstream document is ~40-60 MB.

Strategy:
  1. Fetch once at startup.
  2. Keep the parsed list in memory.
  3. Callers use `get_entries()` synchronously.
  4. `refresh()` re-downloads in the background (called by a scheduler or
     on demand from an admin endpoint).

If the fetch fails (DNS, timeout, non-2xx), the service falls back to the
bundled sample data so the POC stays demoable offline.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_csl.json"


class CSLClient:
    """Synchronous-access cache over the Trade.gov CSL bulk feed."""

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []
        self._data_source: str = "uninitialized"
        self._loaded_at: Optional[datetime] = None

    # ── public read accessors ────────────────────────────────────────

    def get_entries(self) -> List[Dict[str, Any]]:
        return self._entries

    @property
    def data_source(self) -> str:
        return self._data_source

    @property
    def loaded_at(self) -> Optional[datetime]:
        return self._loaded_at

    # ── loading ──────────────────────────────────────────────────────

    async def load(self) -> None:
        """Load data on startup. Tries live, falls back to sample."""
        if settings.use_sample_only:
            logger.info("USE_SAMPLE_ONLY=true — loading bundled sample data")
            self._load_sample()
            return

        try:
            await self._fetch_live()
        except Exception as e:
            logger.warning(
                "CSL live fetch failed (%s) — falling back to sample data",
                e,
            )
            self._load_sample()

    async def refresh(self) -> int:
        """Refresh in place. Returns entry count on success."""
        await self.load()
        return len(self._entries)

    # ── internal ─────────────────────────────────────────────────────

    async def _fetch_live(self) -> None:
        logger.info("Fetching CSL bulk feed from %s", settings.csl_bulk_url)
        async with httpx.AsyncClient(timeout=settings.csl_http_timeout) as client:
            resp = await client.get(settings.csl_bulk_url)
            resp.raise_for_status()
            data = resp.json()

        # The feed wraps entries under a "results" key. Older snapshots
        # used the top-level array — tolerate both.
        if isinstance(data, dict) and "results" in data:
            entries = data["results"]
        elif isinstance(data, list):
            entries = data
        else:
            raise ValueError(
                f"Unexpected CSL feed shape: top-level type {type(data).__name__}"
            )

        self._entries = entries
        self._data_source = "live_csl"
        self._loaded_at = datetime.now(timezone.utc)
        logger.info("Loaded %d entries from live CSL feed", len(entries))

    def _load_sample(self) -> None:
        with SAMPLE_PATH.open() as f:
            data = json.load(f)
        self._entries = data.get("results", [])
        self._data_source = "sample"
        self._loaded_at = datetime.now(timezone.utc)
        logger.info("Loaded %d entries from bundled sample", len(self._entries))
