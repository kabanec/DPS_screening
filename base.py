"""SourceAdapter protocol + SourceRegistry for multi-source DPS ingestion.

Each government-list adapter must satisfy the SourceAdapter protocol:
  short_code  — unique string identifier (e.g. 'US_CSL', 'UN_SC', 'UK_OFSI')
  load()      — async: fetch/refresh entries from upstream
  get_entries() — sync: return current in-memory list of normalised entry dicts
  _entries    — mutable list used for atomic swap during refresh

Normalised entry shape (7 canonical keys):
  id           str            source-scoped identifier
  name         str            primary name
  country      str | None     ISO 2-letter (or empty string)
  source_list  str            e.g. 'SDN', 'Entity List', 'UN_SC'
  aliases      list[str]      alternate names
  programs     str            comma-joined program tags (may be empty)
  address      str | None     free-text address (may be empty)
"""
from __future__ import annotations

import asyncio as _asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple, runtime_checkable

import httpx

logger = logging.getLogger(__name__)

DEFAULT_UA = 'dps-poc/1.0'


class FeedUnavailable(RuntimeError):
    """Every candidate URL for an adapter returned non-2xx or timed out."""


@runtime_checkable
class SourceAdapter(Protocol):
    short_code: str
    _entries: List[Dict[str, Any]]

    async def load(self) -> None: ...
    def get_entries(self) -> List[Dict[str, Any]]: ...


def normalize_entry(
    *,
    id: str,
    name: str,
    country: Optional[str] = None,
    source_list: str,
    aliases: Optional[List[str]] = None,
    programs: Optional[str] = None,
    address: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a dict with exactly the 7 canonical DPS entry keys."""
    return {
        'id': str(id),
        'name': (name or '').strip(),
        'country': (country or '').strip() or None,
        'source_list': source_list,
        'aliases': [a for a in (aliases or []) if a],
        'programs': (programs or '').strip(),
        'address': (address or '').strip() or None,
    }


async def fetch_with_fallback(
    urls: Iterable[str],
    *,
    timeout: float = 60.0,
    max_attempts_per_url: int = 2,
    user_agent: str = DEFAULT_UA,
    accept: Optional[str] = None,
) -> Tuple[bytes, str]:
    """Try each URL in order; return (bytes, url_that_worked).

    Raises FeedUnavailable if every URL fails. Individual failures
    (404/403/timeout/network) are logged at WARNING and the next URL
    is tried. Each URL gets up to ``max_attempts_per_url`` tries with
    exponential backoff (0.5s, 1.5s).
    """
    errors: List[str] = []
    headers: Dict[str, str] = {'User-Agent': user_agent}
    if accept:
        headers['Accept'] = accept

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers=headers
    ) as client:
        for url in urls:
            for attempt in range(1, max_attempts_per_url + 1):
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200 and resp.content:
                        return resp.content, url
                    errors.append(f'{url} [{attempt}] HTTP {resp.status_code}')
                    if resp.status_code in (404, 410):
                        break  # hard not-found — skip remaining attempts
                except (httpx.TimeoutException, httpx.HTTPError) as exc:
                    errors.append(f'{url} [{attempt}] {type(exc).__name__}: {exc}')
                if attempt < max_attempts_per_url:
                    await _asyncio.sleep(0.5 * attempt)
    raise FeedUnavailable(' ; '.join(errors) or 'no urls provided')


class SourceRegistry:
    """Holds all enabled DPS source adapters and provides a unified search view."""

    def __init__(self) -> None:
        self._adapters: Dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter) -> None:
        self._adapters[adapter.short_code] = adapter
        logger.info('SourceRegistry: registered adapter %s', adapter.short_code)

    def get_adapter(self, short_code: str) -> Optional[SourceAdapter]:
        return self._adapters.get(short_code)

    @property
    def adapters(self) -> List[SourceAdapter]:
        return list(self._adapters.values())

    def get_all_entries(self) -> List[Dict[str, Any]]:
        """Combined entries from every registered adapter."""
        entries: List[Dict[str, Any]] = []
        for adapter in self._adapters.values():
            try:
                entries.extend(adapter.get_entries())
            except Exception as exc:
                logger.warning('SourceRegistry.get_all_entries: %s error: %s',
                               adapter.short_code, exc)
        return entries

    async def load_all(self) -> Dict[str, bool]:
        """Reload all adapters. Per-adapter failures do NOT abort others."""
        results: Dict[str, bool] = {}
        for code, adapter in self._adapters.items():
            try:
                await adapter.load()
                results[code] = True
                logger.info('SourceRegistry.load_all: %s OK (%d entries)',
                            code, len(adapter.get_entries()))
            except Exception as exc:
                logger.error('SourceRegistry.load_all: %s failed: %s', code, exc)
                results[code] = False
        return results

    def __len__(self) -> int:
        return len(self._adapters)

    def __repr__(self) -> str:  # pragma: no cover
        return f'SourceRegistry({list(self._adapters.keys())})'
