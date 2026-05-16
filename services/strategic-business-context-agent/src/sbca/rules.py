"""Business-rules backend for the SBCA stub.

Loads every YAML file under ``SBCA_RULES_DIR``. Each top-level key in those
files becomes a queryable rule "domain". Supports a simple ``context``
match (e.g. ``service_area: network``) when the YAML branches on it.

A real SBCA in Phase 4 will replace this with versioned governance storage;
the A2A capability surface stays the same.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Any

import yaml


class Rules:
    def __init__(self, rules_dir: str | Path) -> None:
        self._rules_dir = Path(rules_dir)
        self._lock = RLock()
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        merged: dict[str, Any] = {}
        for yaml_file in sorted(self._rules_dir.glob("*.yaml")):
            payload = yaml.safe_load(yaml_file.read_text()) or {}
            if not isinstance(payload, Mapping):
                continue
            for key, value in payload.items():
                merged[key] = value
        with self._lock:
            self._data = merged

    def domains(self) -> list[str]:
        with self._lock:
            return sorted(self._data.keys())

    def lookup(self, domain: str, context: Mapping[str, Any] | None = None) -> Any:
        """Return the rule value, resolving simple ``by_service_area`` branches.

        Raises KeyError if the domain doesn't exist. The caller (SBCA handler)
        converts this to an A2A failed task, which the agent's semantic_client
        then converts to a SemanticPlaneError — never a fallback.
        """
        with self._lock:
            if domain not in self._data:
                raise KeyError(domain)
            rule = self._data[domain]

        if isinstance(rule, Mapping) and "by_service_area" in rule and context:
            service_area = (context or {}).get("service_area")
            branches = rule.get("by_service_area", {})
            if service_area and service_area in branches:
                return branches[service_area]
            return rule.get("default", rule)
        return rule
