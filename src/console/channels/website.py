"""Reserved website source. See docs/channels.md for the proposed webhook schema."""
from pathlib import Path
from .common import CachedAdapter

WEBSITE_PATH = Path('/var/lib/imgw/ops-app/website-inbound.sqlite3')


class WebsiteAdapter(CachedAdapter):
    name, label = 'website', '独立站（预留）'

    def __init__(self, path=WEBSITE_PATH):
        super().__init__()
        self.path = Path(path)

    def _read(self, now):
        # No ingestion contract has been deployed. Even an existing directory/file
        # is not evidence of a usable website channel; never create anything here.
        return None, False, {}
