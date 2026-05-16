"""Closure chain runner.

Same core shape as the Resolution Orchestrator's runner but with a richer
``_resolve_ref`` that dot-walks into BOTH the initial payload and prior-step
artifacts. (The orchestrator-runner factor-out into ``libs/`` remains a
future cleanup; for now we duplicate.)

Saga is disabled — closure is read-only.
"""
from __future__ import annotations

import time
from typing import Any

from a2a_client import A2AClient, A2AClientError
from di_framework_core import AuditType
from observability import audit_span, cat_event, pst_event

from closure.config import ChainStep, OrchestratorConfig


class ClosureRunner:
    def __init__(
        self, *, cfg: OrchestratorConfig, registry_url: str, bearer_provider,
    ) -> None:
        self.cfg = cfg
        self.registry_url = registry_url
        self.bearer_provider = bearer_provider

    async def _client_for(self, capability: str) -> A2AClient:
        token = None
        if self.bearer_provider is not None:
            token = await self.bearer_provider()
        return await A2AClient.from_capability(
            capability, registry_url=self.registry_url, bearer=token,
        )

    async def run(
        self, *, process: str, initial_payload: dict[str, Any], correlation_id: str | None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        chain_outputs: list[dict[str, Any]] = []
        chain_state = "completed"
        failed_step_index: int | None = None

        for idx, step in enumerate(self.cfg.chain):
            payload = self._payload_for_step(step, initial_payload, chain_outputs)
            with audit_span(
                f"closure.{step.step_label}",
                audit_type=AuditType.PLATFORM,
                attributes={"closure.capability": step.capability, "closure.skill": step.skill, "di.process": process},
            ):
                cat_event("chain_step_input", capability=step.capability, payload=str(payload))
                t_start = time.perf_counter()
                try:
                    client = await self._client_for(step.capability)
                    sub_task = await client.message_send(
                        capability=step.skill,
                        parts=[{"kind": "data", "data": payload}],
                        process=process, step=step.step_label,
                    )
                except A2AClientError as exc:
                    chain_state = "failed"
                    failed_step_index = idx
                    chain_outputs.append({
                        "step": step.step_label, "error": str(exc),
                        "duration_ms": (time.perf_counter() - t_start) * 1000.0,
                    })
                    pst_event("downstream_a2a_error", capability=step.capability,
                              error_class=type(exc).__name__)
                    cat_event("chain_step_failure", capability=step.capability, error=str(exc))
                    break

                sub_state = sub_task.status.state.value
                step_output = {
                    "step": step.step_label,
                    "capability": step.capability, "skill": step.skill,
                    "state": sub_state, "task_id": sub_task.id,
                    "artifacts": [
                        {"name": a.name, "data": next((p.data for p in a.parts if p.kind == "data"), None)}  # type: ignore[union-attr]
                        for a in sub_task.artifacts
                    ],
                    "duration_ms": (time.perf_counter() - t_start) * 1000.0,
                    "downstream_correlation_id": sub_task.metadata.di.correlation_id,
                }
                chain_outputs.append(step_output)
                cat_event("chain_step_output", capability=step.capability, state=sub_state)
                pst_event("chain_step_complete", capability=step.capability, state=sub_state)

                if sub_state != "completed":
                    chain_state = sub_state
                    if sub_state in {"failed", "rejected", "canceled"}:
                        failed_step_index = idx
                    break

        total_ms = (time.perf_counter() - started) * 1000.0
        pst_event("closure_complete", duration_ms=total_ms, state=chain_state, step_count=len(chain_outputs))
        return {
            "process": process,
            "correlation_id": correlation_id,
            "chain_state": chain_state,
            "steps": chain_outputs,
            "failed_step_index": failed_step_index,
            "duration_ms": total_ms,
        }

    def _payload_for_step(
        self, step: ChainStep,
        initial_payload: dict[str, Any], chain_outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if step.compose_inputs:
            composite: dict[str, Any] = {}
            for key, ref in step.compose_inputs.items():
                value = self._resolve_ref(ref, initial_payload, chain_outputs)
                if value is not None:
                    composite[key] = value
            return composite if composite else initial_payload
        if chain_outputs and step.forward_field is not None:
            prev = chain_outputs[-1]
            for a in prev.get("artifacts") or []:
                if a.get("name") == step.forward_field and a.get("data"):
                    return a["data"]
        return initial_payload

    @staticmethod
    def _resolve_ref(
        ref: str, initial_payload: dict[str, Any], chain_outputs: list[dict[str, Any]],
    ) -> Any:
        """Resolve a single reference. Supports:

          ``input.<key>``                    — top-level initial-payload field
          ``input.<key>.<subkey>.<...>``     — dot-walk into nested input
          ``<int>``                          — full step record (rare)
          ``<int>.<artifact_name>``          — full artifact data
          ``<int>.<artifact_name>.<field>``  — dot-walk into artifact data
        """
        parts = ref.split(".")
        if not parts:
            return None
        if parts[0] == "input":
            cur: Any = initial_payload
            for p in parts[1:]:
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(p)
                if cur is None:
                    return None
            return cur
        try:
            step_idx = int(parts[0])
        except ValueError:
            return None
        if step_idx >= len(chain_outputs):
            return None
        step_out = chain_outputs[step_idx]
        if len(parts) == 1:
            return step_out
        artifact_name = parts[1]
        data: Any = None
        for a in step_out.get("artifacts") or []:
            if a.get("name") == artifact_name:
                data = a.get("data")
                break
        if data is None:
            return None
        for p in parts[2:]:
            if not isinstance(data, dict):
                return None
            data = data.get(p)
            if data is None:
                return None
        return data
