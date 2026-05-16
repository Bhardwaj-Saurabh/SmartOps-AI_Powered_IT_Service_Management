"""Triage chain runner.

Reads the ``chain:`` list from agent.yaml and executes A2A calls against
each named capability via the Capability Registry. The orchestrator never
hardcodes a peer URL.

Composability is the whole point: a sibling orchestrator for a different
business process can reuse the same tactical agents simply by listing
their capability names with a different ``process`` value.
"""
from __future__ import annotations

import time
from typing import Any

from a2a_client import A2AClient, A2AClientError
from di_framework_core import AgentError, AuditType
from observability import audit_span, cat_event, pst_event

from triage.config import ChainStep, OrchestratorConfig


class TriageRunner:
    def __init__(
        self,
        *,
        cfg: OrchestratorConfig,
        registry_url: str,
        bearer_provider,
    ) -> None:
        self.cfg = cfg
        self.registry_url = registry_url
        self.bearer_provider = bearer_provider

    async def _client_for(self, capability: str) -> A2AClient:
        token = None
        if self.bearer_provider is not None:
            token = await self.bearer_provider()
        return await A2AClient.from_capability(
            capability,
            registry_url=self.registry_url,
            bearer=token,
        )

    @staticmethod
    def _extract_data_artifact(task_dict: dict[str, Any], name: str) -> dict[str, Any] | None:
        for artifact in task_dict.get("artifacts") or []:
            if artifact.get("name") == name:
                for part in artifact.get("parts") or []:
                    if part.get("kind") == "data":
                        return part.get("data") or {}
        return None

    async def run(
        self,
        *,
        process: str,
        initial_payload: dict[str, Any],
        correlation_id: str | None,
    ) -> dict[str, Any]:
        """Execute the configured chain. Returns a dict suitable for emitting
        as an A2A artifact.

        Short-circuits and forwards the state up if any step returns a non-
        completed task (e.g. INPUT_REQUIRED from Incident Intake when fields
        are missing, or FAILED from any agent).
        """
        start = time.perf_counter()
        chain_outputs: list[dict[str, Any]] = []
        chain_state = "completed"
        carry: dict[str, Any] = initial_payload
        failed_step_index: int | None = None

        for idx, step in enumerate(self.cfg.chain):
            payload = self._payload_for_step(step, carry, chain_outputs)
            with audit_span(
                f"triage.{step.step_label}",
                audit_type=AuditType.PLATFORM,
                attributes={"triage.capability": step.capability, "triage.skill": step.skill, "di.process": process},
            ):
                cat_event("chain_step_input", capability=step.capability, payload=str(payload))
                t_start = time.perf_counter()
                try:
                    client = await self._client_for(step.capability)
                    sub_task = await client.message_send(
                        capability=step.skill,
                        parts=[{"kind": "data", "data": payload}],
                        process=process,
                        step=step.step_label,
                    )
                except A2AClientError as exc:
                    chain_state = "failed"
                    failed_step_index = idx
                    chain_outputs.append({"step": step.step_label, "error": str(exc), "duration_ms": (time.perf_counter() - t_start) * 1000.0})
                    pst_event("downstream_a2a_error", capability=step.capability, error_class=type(exc).__name__)
                    cat_event("chain_step_failure", capability=step.capability, error=str(exc))
                    break

                sub_state = sub_task.status.state.value
                step_output = {
                    "step": step.step_label,
                    "capability": step.capability,
                    "skill": step.skill,
                    "state": sub_state,
                    "task_id": sub_task.id,
                    "artifacts": [
                        {
                            "name": a.name,
                            "data": next(
                                (p.data for p in a.parts if p.kind == "data"),  # type: ignore[union-attr]
                                None,
                            ),
                        }
                        for a in sub_task.artifacts
                    ],
                    "duration_ms": (time.perf_counter() - t_start) * 1000.0,
                    "downstream_correlation_id": sub_task.metadata.di.correlation_id,
                    "downstream_confidence": sub_task.metadata.di.confidence,
                    "requires_human": sub_task.metadata.di.requires_human,
                }
                chain_outputs.append(step_output)
                cat_event("chain_step_output", capability=step.capability, state=sub_state)
                pst_event("chain_step_complete", capability=step.capability, state=sub_state)

                # Short-circuit on anything other than COMPLETED.
                if sub_state != "completed":
                    chain_state = sub_state
                    if sub_state in {"failed", "rejected", "canceled"}:
                        failed_step_index = idx
                    break

                # Forward field selection for the NEXT step happens in _payload_for_step.

        total_ms = (time.perf_counter() - start) * 1000.0
        pst_event("triage_complete", duration_ms=total_ms, state=chain_state, step_count=len(chain_outputs))

        return {
            "process": process,
            "correlation_id": correlation_id,
            "chain_state": chain_state,
            "steps": chain_outputs,
            "failed_step_index": failed_step_index,
            "duration_ms": total_ms,
        }

    def _payload_for_step(
        self,
        step: ChainStep,
        initial_payload: dict[str, Any],
        chain_outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not chain_outputs or step.forward_field is None:
            return initial_payload
        prev = chain_outputs[-1]
        for artifact in prev.get("artifacts") or []:
            if artifact.get("name") == step.forward_field and artifact.get("data"):
                return artifact["data"]
        # Fall back: pass the raw payload again if the named artifact isn't there.
        return initial_payload
