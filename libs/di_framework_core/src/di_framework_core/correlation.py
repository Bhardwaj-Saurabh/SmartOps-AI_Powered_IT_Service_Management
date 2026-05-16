from contextvars import ContextVar
from uuid import uuid4

CORRELATION_ID_HEADER = "X-Correlation-Id"
TRACEPARENT_HEADER = "traceparent"

_correlation_id: ContextVar[str | None] = ContextVar("di_correlation_id", default=None)


def new_correlation_id() -> str:
    return str(uuid4())


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str | None) -> None:
    _correlation_id.set(value)


def ensure_correlation_id(value: str | None = None) -> str:
    """Return the active correlation ID, minting one if neither argument nor
    context has one set. Always stores the result in the context."""
    cid = value or current_correlation_id() or new_correlation_id()
    set_correlation_id(cid)
    return cid
