"""DPS source adapters package.

Exports the protocol, registry, and normalisation helper so other modules
can import from one place.
"""
from .base import SourceAdapter, SourceRegistry, normalize_entry

__all__ = ['SourceAdapter', 'SourceRegistry', 'normalize_entry']
