"""Resolution chain runner.

Generalises the Triage Orchestrator's pattern in two ways:

1. ``compose_inputs`` references support ``input.<key>`` (the caller's
   original payload) in addition to ``<step_idx>.<artifact_name>``. This
   lets a step pull from BOTH the original triaged-incident shape AND
   prior agents' artifacts in one composite payload.

2. A Saga compensation table (Stage 4b will populate it for Automated Fix
   rollback on Verification failure). Stage 4a leaves it empty.

(Long term, the orchestrator runner belongs in ``libs/orchestrator_runner``
shared with Triage. Stage 4a keeps it inline so we don't refactor Triage
mid-stage; the lib factor-out is a separate cleanup commit.)
"""
from __future__ import annotations

import time
from typing import Any

from a2a_client import A2AClient, A2AClientError
from di_framework_core import AgentError, AuditType
from observability import audit_span, cat_event, pst_event

from resolution.config import ChainStep, OrchestratorConfig


class ResolutionRunner:
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

    async def run(
        self,
        *,
        process: str,
        initial_payload: dict[str, Any],
        correlation_id: str | None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        chain_outputs: list[dict[str, Any]] = []
        chain_state = "completed"
        failed_step_index: int | None = None

        for idx, step in enumerate(self.cfg.chain):
            payload = self._payload_for_step(step, initial_payload, chain_outputs)
            with audit_span(
                f"resolution.{step.step_label}",
                audit_type=AuditType.PLATFORM,
                attributes={
                    "resolution.capability": step.capability,
                    "resolution.skill": step.skill,
                    "di.process": process,
                },
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
                    chain_outputs.append({
                        "step": step.step_label, "error": str(exc),
                        "duration_ms": (time.perf_counter() - t_start) * 1000.0,
                    })
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

                if sub_state != "completed":
                    chain_state = sub_state
                    if sub_state in {"failed", "rejected", "canceled"}:
                        failed_step_index = idx
                    break

        # ─── Saga compensation (Stage 4b — real execution) ─────────────────
        saga_actions: list[dict[str, Any]] = []
        if self.cfg.saga.enabled and self.cfg.saga.compensations:
            saga_actions = await self._run_saga(
                chain_outputs=chain_outputs,
                failed_step_index=failed_step_index,
                process=process,
            )

        total_ms = (time.perf_counter() - started) * 1000.0
        pst_event("resolution_complete", duration_ms=total_ms, state=chain_state, step_count=len(chain_outputs))

        return {
            "process": process,
            "correlation_id": correlation_id,
            "chain_state": chain_state,
            "steps": chain_outputs,
            "failed_step_index": failed_step_index,
            "saga_compensations": saga_actions,
            "duration_ms": total_ms,
        }

    async def _run_saga(
        self,
        *,
        chain_outputs: list[dict[str, Any]],
        failed_step_index: int | None,
        process: str,
    ) -> list[dict[str, Any]]:
        """Evaluate each configured compensation against the chain outputs.
        Fire the action when a trigger matches. Returns one entry per fired
        compensation (or per planned-but-not-fired, for audit).

        Trigger evaluation:
          * ``on_step_failure``        — step's state is failed/rejected/canceled
          * ``on_artifact_predicate``  — step COMPLETED and the named artifact's
                                        named field equals the configured value

        Action execution: resolve ``params_from_artifact`` references against
        chain outputs, then call ``capability.skill`` via A2AClient. Record
        success/failure in the returned audit record.
        """
        executed: list[dict[str, Any]] = []
        for comp in self.cfg.saga.compensations:
            trig = comp.trigger
            fires = False
            why = ""

            if trig.on_step_failure is not None and failed_step_index is not None:
                if trig.on_step_failure.step_index == failed_step_index:
                    fires = True
                    why = f"step {failed_step_index} failed"

            if not fires and trig.on_artifact_predicate is not None:
                pred = trig.on_artifact_predicate
                if pred.step_index < len(chain_outputs):
                    step_out = chain_outputs[pred.step_index]
                    if step_out.get("state") == "completed":
                        artifact = self._artifact_data(step_out, pred.artifact) or {}
                        if pred.field in artifact and artifact[pred.field] == pred.equals:
                            fires = True
                            why = (
                                f"step {pred.step_index} {pred.artifact}."
                                f"{pred.field}={artifact[pred.field]}"
                            )

            if not fires:
                continue

            # Resolve action params.
            params: dict[str, Any] = {}
            for key, ref in comp.action.params_from_artifact.items():
                value = self._resolve_dotted(ref, chain_outputs)
                if value is not None:
                    params[key] = value
            params.setdefault("reason", comp.action.reason or why)

            cat_event("saga_planned", capability=comp.action.capability,
                      skill=comp.action.skill, trigger=why, params=str(params))
            pst_event("saga_triggered", capability=comp.action.capability, skill=comp.action.skill)

            outcome: dict[str, Any] = {
                "capability": comp.action.capability,
                "skill": comp.action.skill,
                "trigger": why,
                "params": params,
                "executed": False,
                "succeeded": False,
            }
            try:
                client = await self._client_for(comp.action.capability)
                sub_task = await client.message_send(
                    capability=comp.action.skill,
                    parts=[{"kind": "data", "data": params}],
                    process=process,
                    step="resolution.saga",
                )
                outcome["executed"] = True
                outcome["downstream_state"] = sub_task.status.state.value
                outcome["succeeded"] = sub_task.status.state.value == "completed"
                outcome["downstream_artifacts"] = [
                    {
                        "name": a.name,
                        "data": next(
                            (p.data for p in a.parts if p.kind == "data"),  # type: ignore[union-attr]
                            None,
                        ),
                    }
                    for a in sub_task.artifacts
                ]
                cat_event("saga_executed", capability=comp.action.capability,
                          state=sub_task.status.state.value)
                pst_event("saga_succeeded" if outcome["succeeded"] else "saga_failed",
                          capability=comp.action.capability)
            except A2AClientError as exc:
                outcome["error"] = str(exc)
                cat_event("saga_failed", capability=comp.action.capability, error=str(exc))
                pst_event("saga_failed", capability=comp.action.capability,
                          error_class=type(exc).__name__)

            executed.append(outcome)
        return executed

    @staticmethod
    def _artifact_data(step_output: dict[str, Any], artifact_name: str) -> dict[str, Any] | None:
        for artifact in step_output.get("artifacts") or []:
            if artifact.get("name") == artifact_name:
                return artifact.get("data") or {}
        return None

    @classmethod
    def _resolve_dotted(
        cls, ref: str, chain_outputs: list[dict[str, Any]],
    ) -> Any:
        """Resolve ``<step_idx>.<artifact_name>.<field_path>`` against chain
        outputs. ``field_path`` may dot-walk into nested dict structures."""
        parts = ref.split(".")
        if len(parts) < 2:
            return None
        try:
            step_idx = int(parts[0])
        except ValueError:
            return None
        if step_idx >= len(chain_outputs):
            return None
        artifact_name = parts[1]
        data = cls._artifact_data(chain_outputs[step_idx], artifact_name)
        if data is None:
            return None
        # Walk remaining parts into the data dict.
        cur: Any = data
        for p in parts[2:]:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
            if cur is None:
                return None
        return cur

    def _payload_for_step(
        self,
        step: ChainStep,
        initial_payload: dict[str, Any],
        chain_outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Composite mode — supports BOTH input.<key> and <idx>.<artifact> refs.
        if step.compose_inputs:
            composite: dict[str, Any] = {}
            for key, ref in step.compose_inputs.items():
                value = self._resolve_ref(ref, initial_payload, chain_outputs)
                if value is not None:
                    composite[key] = value
            return composite if composite else initial_payload

        # Simple mode — forward a single named artifact's data from the most recent step.
        if chain_outputs and step.forward_field is not None:
            prev = chain_outputs[-1]
            for artifact in prev.get("artifacts") or []:
                if artifact.get("name") == step.forward_field and artifact.get("data"):
                    return artifact["data"]
        return initial_payload

    @staticmethod
    def _resolve_ref(
        ref: str,
        initial_payload: dict[str, Any],
        chain_outputs: list[dict[str, Any]],
    ) -> Any:
        """Resolve a single ``compose_inputs`` reference.

        ``input.<key>``               → ``initial_payload[<key>]``
        ``<int>.<artifact_name>``     → ``chain_outputs[<int>].artifacts[name=<artifact_name>].data``
        """
        try:
            head, rest = ref.split(".", 1)
        except ValueError:
            return None
        if head == "input":
            return initial_payload.get(rest)
        try:
            step_idx = int(head)
        except ValueError:
            return None
        if step_idx >= len(chain_outputs):
            return None
        source_step = chain_outputs[step_idx]
        for artifact in source_step.get("artifacts") or []:
            if artifact.get("name") == rest:
                return artifact.get("data")
        return None
