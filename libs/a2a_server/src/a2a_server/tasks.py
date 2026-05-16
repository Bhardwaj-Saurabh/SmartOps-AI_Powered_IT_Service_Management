"""In-memory Task store. Sufficient for Phase 1.

State is stateless from the agent process's perspective — for Phase 2 we
swap this for a Redis-backed implementation behind the same ``TaskStore``
protocol. The agent code never sees the difference.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from a2a_server.models import Task


class TaskStore(Protocol):
    async def put(self, task: Task) -> None: ...
    async def get(self, task_id: str) -> Task | None: ...
    async def update(self, task: Task) -> None: ...
    async def list_by_context(self, context_id: str) -> list[Task]: ...


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = asyncio.Lock()

    async def put(self, task: Task) -> None:
        async with self._lock:
            self._tasks[task.id] = task

    async def get(self, task_id: str) -> Task | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def update(self, task: Task) -> None:
        async with self._lock:
            self._tasks[task.id] = task

    async def list_by_context(self, context_id: str) -> list[Task]:
        async with self._lock:
            return [t for t in self._tasks.values() if t.contextId == context_id]
