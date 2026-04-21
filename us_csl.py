"""US Consolidated Screening List (CSL) DPS source adapter.

Standalone httpx-based adapter with no framework coupling.

Constructor kwargs let callers override the feed URL, timeout, and
sample-fallback path so the adapter works in sandbox / offline environments.

short_code: 'US_CSL'
Feed: https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json
Lists: OFAC SDN, Entity List, DPL, UVL, ISN, FSE, SSI, CAPTA, MEU, CMIC, NS-MBS
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .base import normalize_entry

logger = logging.getLogger(__name__)

_FEED_URL = (
    "https://data.trade.gov/downloadable_consolidated_screening_list"
    "/v1/consolidated.json"
)
_TIMEOUT = 90.0


class USCSLAdapter:
    """Standalone httpx adapter for the Trade.gov CSL bulk JSON feed."""

    short_code = "US_CSL"

    def __init__(
        self,
        feed_url: str = _FEED_URL,
        timeout: float = _TIMEOUT,
        use_sample_only: bool = False,
        sample_path: Optional[Path] = None,
    ) -> None:
        self._feed_url = feed_url
        self._timeout = timeout
        self._use_sample_only = use_sample_only
        self._sample_path = sample_path
        self._entries: List[Dict[str, Any]] = []

    async def load(self) -> None:
        """Fetch the CSL bulk feed and normalise all entries into memory."""
        if self._use_sample_only and self._sample_path:
            self._load_sample()
            return
        try:
            await self._fetch_live()
        except Exception as exc:
            logger.warning("US_CSL: live fetch failed (%s)", exc)
            if self._sample_path:
                logger.info("US_CSL: falling back to sample data")
                self._load_sample()
            else:
                raise

    def get_entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    # ── internals ────────────────────────────────────────────────────────

    async def _fetch_live(self) -> None:
        logger.info("US_CSL: fetching bulk feed from %s", self._feed_url)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._feed_url)
            resp.raise_for_status()
            data = resp.json()

        raw = data.get("results", data) if isinstance(data, dict) else data
        self._entries = [self._normalize(e) for e in raw]
        logger.info("US_CSL: loaded %d entries from live feed", len(self._entries))

    def _load_sample(self) -> None:
        data = json.loads(Path(self._sample_path).read_bytes())  # type: ignore[arg-type]
        raw = data.get("results", data) if isinstance(data, dict) else data
        self._entries = [self._normalize(e) for e in raw]
        logger.info("US_CSL: loaded %d entries from sample", len(self._entries))

    @staticmethod
    def _normalize(entry: Dict[str, Any]) -> Dict[str, Any]:
        """Map CSL JSON fields to the canonical 7-key normalized shape."""
        addrs = entry.get("addresses") or []
        address: Optional[str] = None
        if addrs:
            first = addrs[0]
            address = ", ".join(
                filter(None, [first.get("address"), first.get("city"), first.get("country")])
            ) or None

        programs_raw = entry.get("programs") or []
        programs = (
            ", ".join(programs_raw)
            if isinstance(programs_raw, list)
            else str(programs_raw)
        )

        return normalize_entry(
            id=str(entry.get("id", "")),
            name=entry.get("name", ""),
            country=entry.get("country"),
            source_list=entry.get("list_type") or entry.get("source", "US_CSL"),
            aliases=entry.get("alt_names") or [],
            programs=programs,
            address=address,
        )
