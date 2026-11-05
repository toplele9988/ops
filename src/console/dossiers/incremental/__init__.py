"""Daily incremental supplier dossier worker. Importing never opens a database."""
from .worker import Incremental

__all__ = ['Incremental']
