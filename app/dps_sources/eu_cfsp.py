"""EU Common Foreign and Security Policy (CFSP) consolidated sanctions adapter.

Source: https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content
Format: XML (EU Financial Sanctions Database v1.1 schema)
short_code: 'EU_CFSP'

Note: The bare endpoint now returns 403. A public token parameter
(base64 "token-2017") is required; no account registration needed.
"""
from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from .base import fetch_with_fallback, normalize_entry

logger = logging.getLogger(__name__)

# Public token required since early 2026; token-2017 is publicly documented.
_URLS = [
    'https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw',
    'https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content',
]
_SOURCE_LIST = 'EU_CFSP'
_TIMEOUT = 180.0  # 24 MB file; needs ~150 s on a slow link


class EUCFSPAdapter:
    short_code = 'EU_CFSP'

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []

    async def load(self) -> None:
        raw, used_url = await fetch_with_fallback(
            _URLS, timeout=_TIMEOUT, accept='application/xml,text/xml',
        )
        self._entries = await asyncio.to_thread(self._parse_xml, raw)
        logger.info('EU_CFSP: loaded %d entries from %s', len(self._entries), used_url)

    def get_entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    @staticmethod
    def _parse_xml(xml_bytes: bytes) -> List[Dict[str, Any]]:
        root = ET.fromstring(xml_bytes)
        out: List[Dict[str, Any]] = []

        for subject in root.findall('.//{*}sanctionEntity'):
            uid = subject.get('euReferenceNumber') or subject.get('logicalId') or ''

            # Name — try nameAlias first, then entity name elements
            name = ''
            for name_el in subject.findall('{*}nameAlias'):
                if name_el.get('strong') == 'true':
                    name = (name_el.get('wholeName') or
                            name_el.findtext('{*}wholeName') or '').strip()
                    if name:
                        break
            if not name:
                for name_el in subject.findall('{*}nameAlias'):
                    name = (name_el.get('wholeName') or
                            name_el.findtext('{*}wholeName') or '').strip()
                    if name:
                        break
            if not name:
                continue

            aliases = []
            for alias_el in subject.findall('{*}nameAlias'):
                a = (alias_el.get('wholeName') or
                     alias_el.findtext('{*}wholeName') or '').strip()
                if a and a != name:
                    aliases.append(a)

            country = None
            for addr in subject.findall('{*}address'):
                country = (addr.get('countryIso2Code') or
                           addr.findtext('{*}countryIso2Code') or '').strip() or None
                if country:
                    break

            regulation = subject.findtext('{*}regulation/{*}publicationTitle') or ''

            out.append(normalize_entry(
                id=f'EU-{uid or name[:30]}',
                name=name,
                country=country,
                source_list=_SOURCE_LIST,
                aliases=aliases,
                programs=regulation[:200] if regulation else '',
            ))

        return out
