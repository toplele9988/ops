"""Safe errors shared by the order connector and console routes."""


class OpenError(Exception):
    def __init__(self, message, *, retryable=False, reauthorize=False, unknown=False,
                 retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.reauthorize = reauthorize
        self.unknown = unknown
        self.retry_after = retry_after
