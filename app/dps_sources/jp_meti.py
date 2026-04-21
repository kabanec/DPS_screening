"""Japan METI Foreign User List (FUL) adapter.

Source: https://www.meti.go.jp/policy/anpo/law05.html
Format: HTML index page → PDF download(s)
short_code: 'JP_METI'

This adapter is **default-off** (DPS_ENABLE_JP_METI env toggle required).
The METI Foreign User List (外国ユーザーリスト) is published as a PDF.
pypdf is required for PDF text extraction.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx

from .base import normalize_entry

logger = logging.getLogger(__name__)

_INDEX_URL = 'https://www.meti.go.jp/policy/anpo/law05.html'
_BASE_URL = 'https://www.meti.go.jp'
_SOURCE_LIST = 'JP_METI'
_TIMEOUT = 90.0


class JPMETIAdapter:
    """Default-off: requires DPS_ENABLE_JP_METI=true."""

    short_code = 'JP_METI'

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []

    async def load(self) -> None:
        pdf_url = await self._find_pdf_url()
        if not pdf_url:
            logger.warning('JP_METI: could not find PDF link on index page')
            return

        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(pdf_url)
            resp.raise_for_status()
            raw = resp.content

        self._entries = await asyncio.to_thread(self._parse_pdf, raw)
        logger.info('JP_METI: loaded %d entries from %s', len(self._entries), pdf_url)

    def get_entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    # ── internals ────────────────────────────────────────────────────

    async def _find_pdf_url(self) -> Optional[str]:
        """Scrape the METI index page for the FUL PDF download link."""
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(_INDEX_URL)
                resp.raise_for_status()
                html = resp.text
            # Look for PDF links matching the FUL pattern
            for match in re.finditer(r'href=["\']([^"\']*ful[^"\']*\.pdf)["\']', html, re.I):
                return urljoin(_BASE_URL, match.group(1))
            # Fallback: any PDF on the page
            for match in re.finditer(r'href=["\']([^"\']*law05[^"\']*\.pdf)["\']', html, re.I):
                return urljoin(_BASE_URL, match.group(1))
        except Exception as exc:
            logger.warning('JP_METI: index page fetch failed: %s', exc)
        return None

    @staticmethod
    def _parse_pdf(pdf_bytes: bytes) -> List[Dict[str, Any]]:
        try:
            import pypdf
        except ImportError:
            logger.error('JP_METI: pypdf is required — pip install pypdf')
            return []

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        text = '\n'.join(
            page.extract_text() or '' for page in reader.pages
        )
        return JPMETIAdapter._extract_entries_from_text(text)

    @staticmethod
    def _extract_entries_from_text(text: str) -> List[Dict[str, Any]]:
        """Best-effort extraction of entity names from FUL PDF text."""
        out: List[Dict[str, Any]] = []
        seen: set = set()

        # The FUL PDF has lines with entity name, country, and flag reasons
        # Pattern: lines starting with a number followed by entity name
        for i, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line or len(line) < 3:
                continue
            # Match numbered entries: "1. ACME Corp (Iran)"
            m = re.match(r'^\d+[\.\)]\s+(.+?)(?:\s*\(([A-Z][a-z]+)\))?$', line)
            if m:
                name = m.group(1).strip()
                country_name = (m.group(2) or '').strip()
                if len(name) < 2 or name in seen:
                    continue
                seen.add(name)
                # Map common country names to ISO 2-letter (basic mapping)
                _COUNTRY_MAP = {
                    'Iran': 'IR', 'China': 'CN', 'Russia': 'RU', 'North Korea': 'KP',
                    'Pakistan': 'PK', 'India': 'IN', 'Belarus': 'BY', 'Syria': 'SY',
                }
                country = _COUNTRY_MAP.get(country_name) or (
                    country_name[:2].upper() if len(country_name) == 2 else None
                )
                out.append(normalize_entry(
                    id=f'JP-{i}-{name[:20]}',
                    name=name,
                    country=country,
                    source_list=_SOURCE_LIST,
                    programs='FUL',
                ))

        return out
