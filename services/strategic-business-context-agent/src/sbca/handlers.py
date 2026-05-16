"""A2A capability handlers for the SBCA: rule queries + capability registry."""
from __future__ import annotations

from typing import Any

from di_framework_core import AuditType, SemanticPlaneError, TaskStatus
from observability import audit_span

from a2a_server import (
    DataPart,
    HandlerRegistry,
    Message,
    Task,
    TextPart,
)
from a2a_server.handlers import CapabilityHandler
from a2a_server.models import TaskArtifact, TaskStatusModel

from sbca.registry import CapabilityRegistry
from sbca.rules import Rules


def _data_part(message: Message) -> dict[str, Any]:
    for part in message.parts:
        if isinstance(part, DataPart):
            return part.data
    raise ValueError("Message has no DataPart")


def build_registry(rules: Rules, registry: CapabilityRegistry) -> HandlerRegistry:
    hr = HandlerRegistry.empty()
    hr.register("semantic.query_rule", _query_rule_handler(rules))
    hr.register("capability_registry.register", _cap_register_handler(registry))
    hr.register("capability_registry.deregister", _cap_deregister_handler(registry))
    hr.register("capability_registry.lookup", _cap_lookup_handler(registry))
    return hr


def _query_rule_handler(rules: Rules) -> CapabilityHandler:
    async def handle(message: Message, task: Task) -> Task:
        try:
            payload = _data_part(message)
            domain = payload["domain"]
            context = payload.get("context") or {}
        except (KeyError, ValueError) as exc:
            raise SemanticPlaneError(f"Bad SBCA request: {exc}") from exc

        with audit_span(
            "sbca.query_rule",
            audit_type=AuditType.PLATFORM,
            attributes={"sbca.domain": domain},
        ):
            try:
                value = rules.lookup(domain, context)
            except KeyError as exc:
                raise SemanticPlaneError(f"Unknown rule domain: {domain}") from exc

        task.artifacts.append(
            TaskArtifact(
                name="rule",
                parts=[DataPart(data={"domain": domain, "value": value})],
            )
        )
        task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
        return task

    return handle


def _cap_register_handler(registry: CapabilityRegistry) -> CapabilityHandler:
    async def handle(message: Message, task: Task) -> Task:
        payload = _data_part(message)
        registry.register(payload)
        task.artifacts.append(
            TaskArtifact(name="registered", parts=[TextPart(text=f"OK: {payload.get('name')}")])
        )
        task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
        return task

    return handle


def _cap_deregister_handler(registry: CapabilityRegistry) -> CapabilityHandler:
    async def handle(message: Message, task: Task) -> Task:
        payload = _data_part(message)
        registry.deregister(payload["name"])
        task.artifacts.append(
            TaskArtifact(name="deregistered", parts=[TextPart(text=f"OK: {payload['name']}")])
        )
        task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
        return task

    return handle


def _cap_lookup_handler(registry: CapabilityRegistry) -> CapabilityHandler:
    async def handle(message: Message, task: Task) -> Task:
        payload = _data_part(message)
        entry = registry.lookup(payload["name"])
        task.artifacts.append(
            TaskArtifact(
                name="capability",
                parts=[DataPart(data={"entry": entry})],
            )
        )
        task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
        return task

    return handle
