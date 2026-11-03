"""Fixed public failures for the read-only contact refresh boundary."""


class RefreshLimited(RuntimeError):
    def __init__(self, retry_after):
        self.retry_after = max(1, int(retry_after))
        super().__init__('通讯录刚刚刷新过，请等待十分钟后再试；本次没有重复请求')


class RefreshFailed(RuntimeError):
    def __init__(self, message, status_code=502):
        self.status_code = status_code
        super().__init__(message)
