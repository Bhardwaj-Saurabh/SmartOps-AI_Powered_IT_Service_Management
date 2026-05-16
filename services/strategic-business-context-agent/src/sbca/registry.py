"""In-memory Capability Registry for Phase 1.

Collocated with the SBCA per docs/architecture.md "Capability Registry —
minimum Phase-1 behavior". Splits out into its own service in Phase 3 when
orchestrators arrive — callers don't notice (different A2A skill IDs are
already in use).
"""
from __future__ import annotations

from threading import RLock
from typing import Any


class CapabilityRegistry:
    def __init__(self, seed: list[dict[str, Any]] | None = None) -> None:
        self._lock = RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        if seed:
            for entry in seed:
                self._entries[entry["name"]] = entry

    def register(self, entry: dict[str, Any]) -> None:
        name = entry["name"]
        with self._lock:
            self._entries[name] = entry

    def deregister(self, name: str) -> None:
        with self._lock:
            self._entries.pop(name, None)

    def lookup(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            return self._entries.get(name)

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries.values())
