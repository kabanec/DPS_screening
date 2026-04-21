"""Canada consolidated autonomous sanctions adapter.

Source corrected to Global Affairs Canada (SEMA list), not OSFI. OSFI manages
prudential banking regulation — the trade sanctions list is published by GAC.

short_code: 'CA_OSFI'
Feed: https://www.international.gc.ca/.../sema-lmes.xml
Format: XML (<data-set><record> with LastName/GivenName/EntityOrShip/Aliases/Country/Schedule)
"""
from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .base import fetch_with_fallback, normalize_entry

logger = logging.getLogger(__name__)

_URLS = [
    # Primary: Global Affairs Canada SEMA consolidated list (correct publisher)
    'https://www.international.gc.ca/world-monde/assets/office_docs/'
    'international_relations-relations_internationales/sanctions/sema-lmes.xml',
    # Legacy OSFI path (was always wrong; 404 confirmed Apr 2026)
    'https://www.osfi-bsif.gc.ca/Eng/fi-if/aut-aut/documents/consolidatedlist.xml',
]
_SOURCE_LIST = 'CA_OSFI'
_TIMEOUT = 60.0


class CAOSFIAdapter:
    short_code = 'CA_OSFI'

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []

    async def load(self) -> None:
        raw, used_url = await fetch_with_fallback(
            _URLS, timeout=_TIMEOUT, accept='application/xml,text/xml',
        )
        self._entries = await asyncio.to_thread(self._parse_xml, raw)
        logger.info('CA_OSFI: loaded %d entries from %s', len(self._entries), used_url)

    def get_entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    @staticmethod
    def _parse_xml(xml_bytes: bytes) -> List[Dict[str, Any]]:
        root = ET.fromstring(xml_bytes)
        out: List[Dict[str, Any]] = []

        for record in root.findall('.//record'):
            item = (record.findtext('Item') or '').strip()

            # Entity/ship name takes precedence over personal name fields
            entity_name = (record.findtext('EntityOrShip') or '').strip()
            last = (record.findtext('LastName') or '').strip()
            given = (record.findtext('GivenName') or '').strip()

            if entity_name:
                name = entity_name
            elif last:
                name = f'{given} {last}'.strip() if given else last
            else:
                continue

            # Country is bilingual "English / Français" — take the English part
            country_raw = (record.findtext('Country') or '').strip()
            country: Optional[str] = country_raw.split('/')[0].strip() or None

            schedule = (record.findtext('Schedule') or '').strip()

            # Aliases field is a plain-text comma/semicolon-separated string
            aliases_raw = (record.findtext('Aliases') or '').strip()
            aliases: List[str] = []
            if aliases_raw:
                for a in aliases_raw.replace(';', ',').split(','):
                    a = a.strip()
                    if a and a != name:
                        aliases.append(a)

            out.append(normalize_entry(
                id=f'CA-{item or name[:30]}',
                name=name,
                country=country,
                source_list=_SOURCE_LIST,
                aliases=aliases,
                programs=schedule,
            ))

        return out
