from enum import StrEnum


class AuditType(StrEnum):
    """DI AI Framework §6.3 dual audit trail classification.

    Every log/span MUST be tagged with exactly one of these. Mixing them on a
    single span is a §6.3 MUST violation. The OTEL Collector splits the two
    streams into separate stores: CAT (encrypted, 7-year retention, RBAC+MFA)
    and PST (anonymised, 90-day retention).
    """

    CONFIDENTIAL = "confidential"
    PLATFORM = "platform"

    @property
    def otel_attribute(self) -> tuple[str, str]:
        return ("audit.type", self.value)
