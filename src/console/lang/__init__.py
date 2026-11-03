"""Internal language assistance; this package has no sending adapter."""
from .service import Lang
from .storage import LANG_PATH, RateLimited

__all__ = ('Lang', 'LANG_PATH', 'RateLimited')
