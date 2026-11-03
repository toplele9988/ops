"""Internal supplier, product and freight archive. No network or channel writes."""

from .store import ARCHIVE_PATH, Store

__all__ = ['ARCHIVE_PATH', 'Store']
