"""Top-level FastAPI app builder for an A2A agent.

Exposes:
  * /.well-known/agent-card.json — Agent Card discovery
  * /                            — JSON-RPC 2.0 endpoint (message/send, message/stream, tasks/get, tasks/cancel)
  * /health, /ready              — DI AI Framework §6.2

DI envelope wiring (architecture.md "A2A envelope contract"):
  * di.correlation_id minted if absent, propagated to OTEL baggage + spans
  * di.requires_human surfaced as state INPUT_REQUIRED, never as a custom state
  * di.failed_step + error_class populated on Task FAILED responses
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from di_framework_core import (
    AgentError,
    AuditType,
    DIEnvelope,
    ensure_correlation_id,
    SemanticPlaneError,
    TaskStatus,
    set_correlation_id,
)
from observability import audit_span, mount_health
from observability.health import HealthCheck

from a2a_server.agent_card import AgentCardSpec, build_agent_card_router
from a2a_server.auth import KeycloakAuth, extract_bearer
from a2a_server.handlers import HandlerRegistry, initial_task, working
from a2a_server.models import (
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusModel,
    TaskStatusUpdateEvent,
)
from a2a_server.tasks import InMemoryTaskStore, TaskStore


# JSON-RPC error codes (spec + A2A extensions)
_JSONRPC_PARSE = -32700
_JSONRPC_INVALID_REQUEST = -32600
_JSONRPC_METHOD_NOT_FOUND = -32601
_JSONRPC_INVALID_PARAMS = -32602
_JSONRPC_INTERNAL = -32603
_A2A_TASK_NOT_FOUND = -32001
_A2A_TASK_NOT_CANCELABLE = -32002
_A2A_UNSUPPORTED_OPERATION = -32004


@dataclass
class AgentApp:
    """Holds the wiring; ``.fastapi`` is the actual ASGI app."""

    fastapi: FastAPI
    registry: HandlerRegistry
    store: TaskStore


def _err(req_id: object, code: int, message: str, *, data: object | None = None) -> JSONResponse:
    return JSONResponse(
        JSONRPCResponse(id=req_id, error=JSONRPCError(code=code, message=message, data=data)).model_dump(
            exclude_none=True
        )
    )


def _ok(req_id: object, result: object) -> JSONResponse:
    return JSONResponse(JSONRPCResponse(id=req_id, result=result).model_dump(exclude_none=True))


def build_app(
    *,
    agent_card: AgentCardSpec,
    registry: HandlerRegistry,
    health: HealthCheck,
    auth: KeycloakAuth,
    store: TaskStore | None = None,
) -> AgentApp:
    app = FastAPI(title=agent_card.card.name, version=agent_card.card.version)
    app.include_router(build_agent_card_router(agent_card))
    mount_health(app, health)

    task_store: TaskStore = store or InMemoryTaskStore()
    verifier = auth.verifier()

    @app.middleware("http")
    async def _correlation_middleware(request: Request, call_next):
        # The middleware mints the correlation id eagerly so health/auth logs
        # are correlatable too. It uses the incoming header if provided.
        incoming = request.headers.get("X-Correlation-Id")
        cid = ensure_correlation_id(incoming)
        try:
            response: Response = await call_next(request)
        finally:
            set_correlation_id(None)
        response.headers["X-Correlation-Id"] = cid
        return response

    async def _authenticate(request: Request) -> dict[str, object]:
        if verifier is None:
            return {"sub": "dev-bypass"}
        token = await extract_bearer(request)
        return await verifier.verify(token)

    @app.post("/")
    async def jsonrpc(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception as exc:
            return _err(None, _JSONRPC_PARSE, f"Invalid JSON: {exc}")

        try:
            rpc = JSONRPCRequest.model_validate(payload)
        except Exception as exc:
            return _err(payload.get("id"), _JSONRPC_INVALID_REQUEST, f"Invalid JSON-RPC envelope: {exc}")

        try:
            await _authenticate(request)
        except HTTPException as exc:
            return _err(rpc.id, _A2A_UNSUPPORTED_OPERATION, exc.detail, data={"http_status": exc.status_code})

        if rpc.method == "message/send":
            return await _message_send(rpc, task_store, registry)
        if rpc.method == "message/stream":
            return await _message_stream(rpc, task_store, registry)
        if rpc.method == "tasks/get":
            return await _tasks_get(rpc, task_store)
        if rpc.method == "tasks/cancel":
            return await _tasks_cancel(rpc, task_store)

        return _err(rpc.id, _JSONRPC_METHOD_NOT_FOUND, f"Method '{rpc.method}' not supported")

    return AgentApp(fastapi=app, registry=registry, store=task_store)


# ─── method implementations ────────────────────────────────────────────────
def _ingest_message(params: dict) -> Message:
    raw = params.get("message")
    if raw is None:
        raise ValueError("params.message is required")
    return Message.model_validate(raw)


def _failed_from_error(task: Task, exc: Exception, *, step: int | None = None) -> Task:
    envelope = task.metadata.di
    envelope.error_class = type(exc).__name__
    envelope.reason = str(exc)
    if step is not None:
        envelope.failed_step = step
    task.status = TaskStatusModel(state=TaskStatus.FAILED, message=task.status.message)
    return task


async def _message_send(
    rpc: JSONRPCRequest, store: TaskStore, registry: HandlerRegistry
) -> JSONResponse:
    try:
        message = _ingest_message(rpc.params)
    except Exception as exc:
        return _err(rpc.id, _JSONRPC_INVALID_PARAMS, str(exc))

    cid = ensure_correlation_id(message.metadata.di.correlation_id)
    message.metadata.di.correlation_id = cid

    envelope = DIEnvelope(
        capability=message.metadata.di.capability,
        correlation_id=cid,
        process=message.metadata.di.process,
        step=message.metadata.di.step,
    )

    task = initial_task(message, envelope)
    task.metadata.di = envelope
    await store.put(task)

    capability, handler, _ = registry.resolve(message)
    if handler is None:
        return _err(
            rpc.id,
            _A2A_UNSUPPORTED_OPERATION,
            f"No handler registered for capability '{capability}'",
        )

    task = working(task)
    await store.update(task)

    start = time.perf_counter()
    with audit_span(
        f"a2a.message_send:{capability}",
        audit_type=AuditType.PLATFORM,
        attributes={"di.capability": capability, "di.process": envelope.process or ""},
    ):
        try:
            task = await handler(message, task)
        except SemanticPlaneError as exc:
            task = _failed_from_error(task, exc, step=exc.step)
        except AgentError as exc:
            task = _failed_from_error(task, exc, step=exc.step)
        except Exception as exc:  # last-resort guard
            task = _failed_from_error(task, exc)

    task.metadata.di.duration_ms = (time.perf_counter() - start) * 1000.0
    await store.update(task)
    return _ok(rpc.id, task.model_dump(mode="json", exclude_none=True))


async def _message_stream(
    rpc: JSONRPCRequest, store: TaskStore, registry: HandlerRegistry
) -> Response:
    try:
        message = _ingest_message(rpc.params)
    except Exception as exc:
        return _err(rpc.id, _JSONRPC_INVALID_PARAMS, str(exc))

    cid = ensure_correlation_id(message.metadata.di.correlation_id)
    message.metadata.di.correlation_id = cid

    envelope = DIEnvelope(
        capability=message.metadata.di.capability,
        correlation_id=cid,
        process=message.metadata.di.process,
        step=message.metadata.di.step,
    )
    task = initial_task(message, envelope)
    task.metadata.di = envelope
    await store.put(task)

    capability, _, streamer = registry.resolve(message)
    if streamer is None:
        return _err(
            rpc.id,
            _A2A_UNSUPPORTED_OPERATION,
            f"No streaming handler for capability '{capability}'",
        )

    async def event_source():
        try:
            async for event in streamer(message, task):
                payload = JSONRPCResponse(
                    id=rpc.id, result=event.model_dump(mode="json", exclude_none=True)
                ).model_dump(exclude_none=True)
                yield {"data": payload}
        except Exception as exc:
            err = JSONRPCResponse(
                id=rpc.id,
                error=JSONRPCError(code=_JSONRPC_INTERNAL, message=str(exc)),
            ).model_dump(exclude_none=True)
            yield {"data": err}

    return EventSourceResponse(event_source())


async def _tasks_get(rpc: JSONRPCRequest, store: TaskStore) -> JSONResponse:
    task_id = rpc.params.get("id")
    if not task_id:
        return _err(rpc.id, _JSONRPC_INVALID_PARAMS, "params.id required")
    task = await store.get(task_id)
    if task is None:
        return _err(rpc.id, _A2A_TASK_NOT_FOUND, f"Task '{task_id}' not found")
    return _ok(rpc.id, task.model_dump(mode="json", exclude_none=True))


async def _tasks_cancel(rpc: JSONRPCRequest, store: TaskStore) -> JSONResponse:
    task_id = rpc.params.get("id")
    if not task_id:
        return _err(rpc.id, _JSONRPC_INVALID_PARAMS, "params.id required")
    task = await store.get(task_id)
    if task is None:
        return _err(rpc.id, _A2A_TASK_NOT_FOUND, f"Task '{task_id}' not found")
    if task.status.state in {
        TaskStatus.COMPLETED,
        TaskStatus.CANCELED,
        TaskStatus.FAILED,
        TaskStatus.REJECTED,
    }:
        return _err(rpc.id, _A2A_TASK_NOT_CANCELABLE, f"Task in terminal state {task.status.state}")
    task.status = TaskStatusModel(state=TaskStatus.CANCELED, message=task.status.message)
    await store.update(task)
    return _ok(rpc.id, task.model_dump(mode="json", exclude_none=True))


# Re-exports for type symmetry with handlers.py
__all__ = ["AgentApp", "build_app", "TaskArtifactUpdateEvent", "TaskStatusUpdateEvent", "status"]
