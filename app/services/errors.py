class WalletError(Exception):
    """Domain error carrying the HTTP status the API should surface.

    Keeps services free of FastAPI imports; app.main installs one handler that
    turns these into JSON responses.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class InsufficientFunds(WalletError):
    def __init__(self) -> None:
        super().__init__(400, "Insufficient funds")


class NotFound(WalletError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class BadRequest(WalletError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class Conflict(WalletError):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)
