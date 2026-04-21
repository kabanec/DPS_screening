"""UK FCDO / OFSI Financial Sanctions Targets adapter.

Primary source migrated from ofsistorage.blob.core.windows.net (defunct Jan 2026)
to the new FCDO Sanctions List portal (sanctionslist.fcdo.gov.uk) as of 28 Jan 2026.
The XML schema also changed: root is now <Designations>, entries are <Designation>
with nested <Names>/<Name>/<NameType> (was flat <Target>/<Name6>).

short_code: 'UK_OFSI'
"""
from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from .base import fetch_with_fallback, normalize_entry

logger = logging.getLogger(__name__)

_URLS = [
    # Primary: FCDO Sanctions List (new portal, live as of 28 Jan 2026)
    'https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml',
    # Historical blob storage paths (all 404 as of Apr 2026, kept as last resort)
    'https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.xml',
    'https://ofsistorage.blob.core.windows.net/publishlive/ConList.xml',
    'https://ofsistorage.blob.core.windows.net/publishlive/FinancialSanctionsTargets.xml',
]
_SOURCE_LIST = 'UK_OFSI'
_TIMEOUT = 90.0


class UKOFSIAdapter:
    short_code = 'UK_OFSI'

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []

    async def load(self) -> None:
        raw, used_url = await fetch_with_fallback(
            _URLS, timeout=_TIMEOUT, accept='application/xml,text/xml',
        )
        self._entries = await asyncio.to_thread(self._parse_xml, raw)
        logger.info('UK_OFSI: loaded %d entries from %s', len(self._entries), used_url)

    def get_entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    @staticmethod
    def _parse_xml(xml_bytes: bytes) -> List[Dict[str, Any]]:
        root = ET.fromstring(xml_bytes)
        out: List[Dict[str, Any]] = []

        # New FCDO format: <Designations><Designation>...</Designation></Designations>
        designations = root.findall('Designation')

        # Legacy OFSI format fallback: <TargetsFile><Target>...</Target></TargetsFile>
        if not designations:
            for target in root.findall('.//Target'):
                uid = (target.findtext('UniqueID') or '').strip()
                name = (target.findtext('Name6') or target.findtext('FullName') or '').strip()
                if not name:
                    continue
                country = (target.findtext('Country') or target.findtext('Nationality') or '').strip() or None
                regime = (target.findtext('Regime') or target.findtext('GroupType') or '').strip()
                aliases = []
                for alias_el in target.findall('.//Alias'):
                    a = (alias_el.findtext('Name6') or alias_el.findtext('AliasName') or '').strip()
                    if a and a != name:
                        aliases.append(a)
                out.append(normalize_entry(
                    id=f'UK-{uid or name[:30]}',
                    name=name, country=country, source_list=_SOURCE_LIST,
                    aliases=aliases, programs=regime,
                ))
            return out

        for designation in designations:
            uid = (designation.findtext('UniqueID') or '').strip()

            name = ''
            aliases: List[str] = []
            for name_el in designation.findall('Names/Name'):
                n6 = (name_el.findtext('Name6') or '').strip()
                ntype = (name_el.findtext('NameType') or '').strip()
                if not n6:
                    continue
                if ntype == 'Primary Name' and not name:
                    name = n6
                elif ntype in ('Alias', 'Name Spelling Variation') and n6 != name:
                    aliases.append(n6)

            if not name:
                continue

            country = None
            for addr in designation.findall('Addresses/Address'):
                c = (addr.findtext('AddressCountry') or '').strip()
                if c:
                    country = c
                    break

            regime = (designation.findtext('RegimeName') or '').strip()

            out.append(normalize_entry(
                id=f'UK-{uid or name[:30]}',
                name=name,
                country=country,
                source_list=_SOURCE_LIST,
                aliases=aliases,
                programs=regime,
            ))

        return out
