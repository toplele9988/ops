"""Shared, purpose-scoped knowledge access. Importing this package performs no I/O."""

from .hub import Hub, default_hub

__all__ = ['Hub', 'default_hub']
