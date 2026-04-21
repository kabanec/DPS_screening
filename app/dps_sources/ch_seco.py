"""Switzerland SECO sanctions list adapter.

Source migrated from seco.admin.ch JSON (discontinued Dec 2023) to the SESAM
portal XML feed (sesam.search.admin.ch). XML format is completely different:
structured by sanctions-program + target/individual/entity hierarchy.

short_code: 'CH_SECO'
"""
from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .base import fetch_with_fallback, normalize_entry

logger = logging.getLogger(__name__)

_URLS = [
    # Primary: SESAM portal (XML, replaced old JSON endpoint Dec 2023)
    'https://www.sesam.search.admin.ch/sesam-search-web/pages/downloadXmlGesamtliste.xhtml'
    '?lang=en&action=downloadXmlGesamtlisteAction',
    # Fallback: old seco.admin.ch JSON path (404 as of Apr 2026, retained for safety)
    'https://www.seco.admin.ch/dam/seco/de/dokumente/Aussenwirtschaft/'
    'Wirtschaftliche_Landesversorgung/Embargomassnahmen/Sanktionslisten/'
    'seco-sanclist.json.download.json/seco-sanclist.json',
]
_SOURCE_LIST = 'CH_SECO'
_TIMEOUT = 120.0  # 38 MB XML feed


class CHSECOAdapter:
    short_code = 'CH_SECO'

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []

    async def load(self) -> None:
        raw, used_url = await fetch_with_fallback(
            _URLS, timeout=_TIMEOUT, accept='application/xml,text/xml',
        )
        self._entries = await asyncio.to_thread(self._parse_xml, raw)
        logger.info('CH_SECO: loaded %d entries from %s', len(self._entries), used_url)

    def get_entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    @staticmethod
    def _parse_xml(xml_bytes: bytes) -> List[Dict[str, Any]]:
        root = ET.fromstring(xml_bytes)
        out: List[Dict[str, Any]] = []

        # Build sanctions-set-ssid → program name map
        ss_to_program: Dict[str, str] = {}
        for prog in root.findall('sanctions-program'):
            prog_name = ''
            for pk in prog.findall('program-key'):
                if pk.get('lang') == 'eng':
                    prog_name = pk.text or ''
                    break
            for sset in prog.findall('sanctions-set'):
                ssid = sset.get('ssid', '')
                if ssid:
                    ss_to_program[ssid] = prog_name

        # Iterate targets (direct children of root)
        for target in root.findall('target'):
            ss_id = (target.findtext('sanctions-set-id') or '').strip()
            program = ss_to_program.get(ss_id, '')

            for subject_tag in ('individual', 'entity'):
                subject = target.find(subject_tag)
                if subject is None:
                    continue

                # Primary identity (main=true)
                primary_name = ''
                primary_country: Optional[str] = None
                aliases: List[str] = []
                uid = target.get('ssid', '')

                for identity in subject.findall('identity'):
                    is_main = identity.get('main', 'false') == 'true'
                    assembled = _assemble_name(identity)
                    if not assembled:
                        continue
                    if is_main and not primary_name:
                        primary_name = assembled
                        primary_country = _extract_country(identity)
                    elif assembled != primary_name:
                        aliases.append(assembled)

                if not primary_name:
                    continue

                out.append(normalize_entry(
                    id=f'CH-{uid or primary_name[:30]}',
                    name=primary_name,
                    country=primary_country,
                    source_list=_SOURCE_LIST,
                    aliases=aliases,
                    programs=program,
                ))

        return out


def _assemble_name(identity: ET.Element) -> str:
    """Assemble a full name from name-part elements in order."""
    name_el = identity.find('name')
    if name_el is None:
        return ''

    # whole-name type → single value
    for np in name_el.findall('name-part'):
        if np.get('name-part-type') == 'whole-name':
            v = (np.findtext('value') or '').strip()
            if v:
                return v

    # Ordered name parts (family-name, given-name, father-name, ...)
    parts: List[tuple] = []
    for np in name_el.findall('name-part'):
        order = int(np.get('order', '99'))
        v = (np.findtext('value') or '').strip()
        if v:
            parts.append((order, v))
    parts.sort()
    return ' '.join(p for _, p in parts)


def _extract_country(identity: ET.Element) -> Optional[str]:
    """Return ISO-2 code from the first nationality element."""
    nat = identity.find('nationality')
    if nat is None:
        return None
    country = nat.find('country')
    if country is None:
        return None
    return country.get('iso-code') or None
