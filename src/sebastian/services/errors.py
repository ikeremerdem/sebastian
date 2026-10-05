"""Domain errors, mapped to HTTP statuses / MCP error text by the adapters."""


class SebastianError(Exception):
    status_code = 400

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


class NotFound(SebastianError):
    status_code = 404


class Conflict(SebastianError):
    status_code = 409


class Invalid(SebastianError):
    status_code = 422
