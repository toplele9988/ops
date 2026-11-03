"""Development transport only. Production transport must be injected by integrator.

There is deliberately no import of WeChat/WAHA clients or configuration reader.
"""


class FakeBridge:
    name = 'fake'

    def __init__(self, outcome='sent'):
        self.outcome, self.calls, self.commands = outcome, [], []

    def send(self, *, channel, conversation_key, text, idempotency_key, actor):
        self.calls.append(dict(channel=channel, conversation_key=conversation_key, text=text,
                               idempotency_key=idempotency_key, actor=actor))
        return self.outcome

    def apply(self, *, kind, payload, operation_key, actor):
        self.commands.append(dict(kind=kind, payload=payload, operation_key=operation_key, actor=actor))
        return 'done'
