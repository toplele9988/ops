"""Additive r11 mount; existing inbox/deals builders retain their own routes."""
from . import v3_customers_api, v3_ops_drafts_api, v3_ops_queue_api
from .archive.store import ARCHIVE_PATH
from .outbox.store import OUTBOX_PATH


def build(app_module, archive_path=ARCHIVE_PATH, outbox_path=OUTBOX_PATH, *, inbox=None):
    if inbox is None:
        from .channels import shared_inbox
        inbox = shared_inbox()
    return (v3_customers_api.build_parties(app_module, archive_path, inbox=inbox)
            + v3_ops_drafts_api.build(app_module, archive_path, outbox_path)
            + v3_ops_queue_api.build(app_module))
