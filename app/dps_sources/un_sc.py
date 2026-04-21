"""UN Security Council Consolidated Sanctions List adapter.

Fetches the XML feed from:
  https://scsanctions.un.org/resources/xml/en/consolidated.xml
  (redirects 302 → Azure Blob Storage — follow_redirects required)

Lists: 1267/1988/1989 Al-Qaida/Taliban, 1540 non-proliferation.

short_code: 'UN_SC'
"""
from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import httpx

from .base import normalize_entry

logger = logging.getLogger(__name__)

_URL = 'https://scsanctions.un.org/resources/xml/en/consolidated.xml'
_SOURCE_LIST = 'UN_SC'
_TIMEOUT = 60.0


class UNSCAdapter:
    """SourceAdapter for the UN Security Council consolidated sanctions list."""

    short_code = 'UN_SC'

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []

    async def load(self) -> None:
        """Fetch XML and parse into _entries."""
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(_URL)
            resp.raise_for_status()
            raw = resp.content

        # CPU-bound XML parse off the event loop
        self._entries = await asyncio.to_thread(self._parse_xml, raw)
        logger.info('UN_SC: loaded %d entries', len(self._entries))

    def get_entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    # ── XML parser ───────────────────────────────────────────────────

    @staticmethod
    def _parse_xml(xml_bytes: bytes) -> List[Dict[str, Any]]:
        root = ET.fromstring(xml_bytes)
        out: List[Dict[str, Any]] = []

        # Individuals
        for ind in root.findall('.//INDIVIDUAL'):
            name_parts = [
                (ind.findtext(tag) or '').strip()
                for tag in ('FIRST_NAME', 'SECOND_NAME', 'THIRD_NAME', 'FOURTH_NAME')
            ]
            name = ' '.join(p for p in name_parts if p)
            if not name:
                continue

            aliases = [
                (a.findtext('ALIAS_NAME') or '').strip()
                for a in ind.findall('INDIVIDUAL_ALIAS')
                if (a.findtext('ALIAS_NAME') or '').strip()
            ]
            country = (ind.findtext('.//NATIONALITY/VALUE') or '').strip() or None
            list_type = (ind.findtext('UN_LIST_TYPE') or 'UN').strip()
            ref = (ind.findtext('REFERENCE_NUMBER') or '').strip()

            out.append(normalize_entry(
                id=f'UN-IND-{ref or name[:30]}',
                name=name,
                country=country,
                source_list=_SOURCE_LIST,
                aliases=aliases,
                programs=list_type,
            ))

        # Entities
        for ent in root.findall('.//ENTITY'):
            name = (ent.findtext('FIRST_NAME') or '').strip()
            if not name:
                continue

            aliases = [
                (a.findtext('ALIAS_NAME') or '').strip()
                for a in ent.findall('ENTITY_ALIAS')
                if (a.findtext('ALIAS_NAME') or '').strip()
            ]
            country = None
            addr_el = ent.find('ENTITY_ADDRESS')
            if addr_el is not None:
                country = (addr_el.findtext('COUNTRY') or '').strip() or None

            list_type = (ent.findtext('UN_LIST_TYPE') or 'UN').strip()
            ref = (ent.findtext('REFERENCE_NUMBER') or '').strip()

            out.append(normalize_entry(
                id=f'UN-ENT-{ref or name[:30]}',
                name=name,
                country=country,
                source_list=_SOURCE_LIST,
                aliases=aliases,
                programs=list_type,
            ))

        return out
