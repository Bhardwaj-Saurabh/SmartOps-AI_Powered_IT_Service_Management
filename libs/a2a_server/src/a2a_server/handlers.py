"""Capability handlers and JSON-RPC method dispatch.

A capability handler is a coroutine that turns an incoming ``Message`` into
either a terminal ``Task`` or a stream of update events. The handler is
written by the agent author; everything else (transport, lifecycle, DI
envelope wiring, audit spans) is library territory.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from di_framework_core import DIEnvelope, TaskStatus

from a2a_server.models import (
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusModel,
    TaskStatusUpdateEvent,
)

CapabilityHandler = Callable[[Message, Task], Awaitable[Task]]
StreamingHandler = Callable[
    [Message, Task],
    AsyncIterator[Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent],
]


@dataclass
class HandlerRegistry:
    """Maps ``di.capability`` values to the function that runs them."""

    handlers: dict[str, CapabilityHandler]
    streaming: dict[str, StreamingHandler]

    @classmethod
    def empty(cls) -> "HandlerRegistry":
        return cls(handlers={}, streaming={})

    def register(self, capability: str, fn: CapabilityHandler) -> None:
        self.handlers[capability] = fn

    def register_streaming(self, capability: str, fn: StreamingHandler) -> None:
        self.streaming[capability] = fn

    def resolve(self, message: Message) -> tuple[str, CapabilityHandler | None, StreamingHandler | None]:
        cap = message.metadata.di.capability or ""
        return cap, self.handlers.get(cap), self.streaming.get(cap)


def initial_task(message: Message, envelope: DIEnvelope) -> Task:
    """Build the ``submitted`` Task for a new incoming message."""
    return Task(
        contextId=message.contextId or envelope.correlation_id or "",
        status=TaskStatusModel(state=TaskStatus.SUBMITTED, message=message),
        history=[message],
    )


def working(task: Task) -> Task:
    task.status = TaskStatusModel(state=TaskStatus.WORKING, message=task.status.message)
    return task
