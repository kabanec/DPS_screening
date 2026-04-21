"""
Multi-source denied-party list registry.

Wraps N source clients (US CSL, UN, UK OFSI, EU CFSP, etc.) and exposes
one unified `.get_entries()` surface to the matcher + DPS service. The
matcher is source-agnostic — it just sees a flat list of normalized
entries and scores names against all of them at once.

Adding a new denied-party list is a 3-step drop-in:

  1. Write an adapter class (see un_client.py as a reference) that exposes:
       - name            : str    (human-readable list name)
       - short_code      : str    (e.g. 'UN', 'UK_OFSI', 'EU_CFSP')
       - async load()    : fetches + normalizes + caches entries
       - get_entries()   : returns List[Dict] in the 7-key normalized shape
       - data_source     : str    ('live_*' / 'sample' / 'failed' / 'disabled')
       - loaded_at       : datetime | None

  2. Instantiate it in app/main.py lifespan.

  3. Add it to the SourceRegistry. Done — no changes to matcher,
     dps_service, or routers.

Every entry returned by any adapter MUST conform to the canonical shape:

    {
        "id":           str,                 # stable per-source id
        "name":         str,                 # primary searchable name
        "alt_names":    List[str],           # aliases
        "source":       str,                 # human-readable list name
        "type":         "Individual" | "Entity" | "Vessel" | "Aircraft" | "Other",
        "list_type":    str | None,          # short code from the source
        "country":      str | None,          # ISO-2 or country name
        "programs":     List[str] | str,     # sanctions programs
        "source_information_url": str | None # link back to the authoritative listing
    }
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


class SourceAdapter(Protocol):
    """Structural contract every list-source adapter must satisfy."""

    name: str
    short_code: str

    async def load(self) -> None: ...
    def get_entries(self) -> List[Dict[str, Any]]: ...
    @property
    def data_source(self) -> str: ...
    @property
    def loaded_at(self) -> Optional[datetime]: ...


class SourceRegistry:
    """Aggregates multiple denied-party-list adapters behind one interface."""

    def __init__(self, adapters: List[SourceAdapter]) -> None:
        self._adapters = list(adapters)

    async def load_all(self) -> None:
        """Load every adapter concurrently; failures do not abort the set."""
        await asyncio.gather(
            *(self._safe_load(a) for a in self._adapters),
            return_exceptions=False,
        )

    @staticmethod
    async def _safe_load(adapter: SourceAdapter) -> None:
        try:
            await adapter.load()
        except Exception:
            logger.exception("Adapter %s.load() crashed", adapter.short_code)

    # ── unified read surface ───────────────────────────────────────

    def get_entries(self) -> List[Dict[str, Any]]:
        """Flat, matcher-ready list of every entry across every adapter."""
        out: List[Dict[str, Any]] = []
        for a in self._adapters:
            out.extend(a.get_entries())
        return out

    @property
    def data_source(self) -> str:
        """
        Roll-up tag for response payloads:

          - 'multi_source' if 2+ adapters loaded non-empty data
          - the single adapter's data_source if only one loaded
          - 'failed' if none loaded
        """
        live = [a for a in self._adapters if a.get_entries()]
        if not live:
            return "failed"
        if len(live) == 1:
            return live[0].data_source
        return "multi_source"

    @property
    def loaded_at(self) -> Optional[datetime]:
        """Earliest non-null load timestamp — 'freshness' is bounded by the oldest."""
        stamps = [a.loaded_at for a in self._adapters if a.loaded_at]
        return min(stamps) if stamps else None

    # ── per-adapter introspection (used by /v1/lists and /health) ──

    def per_source_summary(self) -> List[Dict[str, Any]]:
        return [
            {
                "short_code": a.short_code,
                "name": a.name,
                "status": a.data_source,
                "entry_count": len(a.get_entries()),
                "loaded_at": a.loaded_at.isoformat() if a.loaded_at else None,
            }
            for a in self._adapters
        ]
