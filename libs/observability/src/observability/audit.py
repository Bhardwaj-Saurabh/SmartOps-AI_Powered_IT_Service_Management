"""CAT/PST classification helpers.

DI AI Framework §6.3: every span MUST be tagged ``audit.type`` ∈ {confidential,
platform}. PII never on platform spans. The API here makes the classification
a required argument so it cannot be forgotten by accident.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span

from di_framework_core import AuditType, current_correlation_id

_tracer = trace.get_tracer(__name__)


@contextmanager
def audit_span(
    name: str,
    *,
    audit_type: AuditType,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span]:
    """Open an OTEL span pre-tagged with the audit classification + correlation id."""
    attrs: dict[str, Any] = {"audit.type": audit_type.value}
    cid = current_correlation_id()
    if cid is not None:
        attrs["di.correlation_id"] = cid
    if attributes:
        attrs.update(attributes)
    with _tracer.start_as_current_span(name, attributes=attrs) as span:
        yield span


def cat_event(name: str, **fields: Any) -> None:
    """Add a CAT-classified event to the current span. Use for sensitive payloads."""
    span = trace.get_current_span()
    span.add_event(name, attributes={"audit.type": AuditType.CONFIDENTIAL.value, **fields})


def pst_event(name: str, **fields: Any) -> None:
    """Add a PST-classified event to the current span. Anonymised fields only."""
    span = trace.get_current_span()
    span.add_event(name, attributes={"audit.type": AuditType.PLATFORM.value, **fields})
