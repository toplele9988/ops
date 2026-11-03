"""Production assembly: FakeBridge only, local source reads, explicit lifecycle.

Import and route construction do not open databases or start background work.
The installer initializes the private store before the host starts this worker.
"""
from pathlib import Path
from contextlib import asynccontextmanager
from console.archive.store import ARCHIVE_PATH
from console.inbox.store import INBOUND_PATH, WECHAT_PATH
from console.kb import default_hub
from .bridge import FakeBridge
from .archive_events import ArchiveEvents
from .gaps import Gaps
from .model import AiTask
from .runtime import build_operator, QuotePolicyFromRound
from .sources import Source, LocalInbox
from .worker import Worker

OPERATOR_PATH = Path('/var/lib/imgw/ops-app/operator/operator.sqlite3')
DOSSIERS_PATH = Path('/var/lib/imgw/ops-app/dossiers/dossiers.sqlite3')


def sources():
    from console.v3_waha_knowledge import _bridge_identity
    return [Source('alibaba', INBOUND_PATH, 'alibaba'),
            Source('wechat-groups', WECHAT_PATH, 'wechat'),
            Source('wechat-private', WECHAT_PATH, 'private'),
            Source('whatsapp-live', '/var/lib/imgw/whatsapp/messages.sqlite3', 'whatsapp', authorize=_bridge_identity),
            Source('whatsapp-history', '/var/lib/imgw/whatsapp-history-private/history.sqlite3', 'whatsapp', authorize=_bridge_identity)]


def assemble(*, operator_path=OPERATOR_PATH, archive_path=ARCHIVE_PATH,
             inputs=None, hub=None, model=None, dossiers_path=DOSSIERS_PATH,
             language=None, quote_policy=None):
    inputs = sources() if inputs is None else inputs
    inbox = LocalInbox(inputs)
    if language is None:
        from console.lang import Lang
        lang = Lang(inbox=inbox)
        language = lambda key: lang.read('contact', {'conversation_key':key})['contact']
    if quote_policy is None:
        from console.quotes.service import Quotes
        quote_policy = QuotePolicyFromRound(Quotes(archive_path))
    operator, events = build_operator(
        operator_path=operator_path, archive_path=archive_path, inbox=inbox,
        hub=default_hub() if hub is None else hub,
        quote_policy=quote_policy, language=language,
        model=AiTask() if model is None else model, bridge=FakeBridge())
    worker = Worker(operator,events,inputs,gaps=Gaps(operator,dossiers_path),archive_events=ArchiveEvents(operator,events),seed_at_tail=True)
    return operator, worker


def build(app_module):
    from console.v3_operator_api import build as api
    operator, worker = assemble()
    app_module.operator_worker = worker
    return api(app_module,service=operator)


def attach_lifecycle(application, app_module):
    worker = app_module.operator_worker
    previous = application.router.lifespan_context
    @asynccontextmanager
    async def lifespan(app):
        async with previous(app) as state:
            worker.start()
            try:
                yield state
            finally:
                worker.stop()
    application.router.lifespan_context = lifespan
