"""Google A2A protocol types (a2aproject.github.io/A2A specification).

Implemented directly against the spec — no third-party SDK. Only the slots
actually used in this project are modelled. Extra fields are preserved via
``model_config = ConfigDict(extra="allow")`` so spec evolution doesn't break
us.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from di_framework_core import DIEnvelope, TaskStatus


# ─── Parts ──────────────────────────────────────────────────────────────────
class _PartBase(BaseModel):
    model_config = ConfigDict(extra="allow")


class TextPart(_PartBase):
    kind: Literal["text"] = "text"
    text: str


class DataPart(_PartBase):
    kind: Literal["data"] = "data"
    data: dict[str, Any]


class FilePart(_PartBase):
    kind: Literal["file"] = "file"
    file: dict[str, Any]  # spec allows {bytes: base64} | {uri: ...}


Part = Annotated[TextPart | DataPart | FilePart, Field(discriminator="kind")]


# ─── Message ────────────────────────────────────────────────────────────────
class MessageMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    di: DIEnvelope = Field(default_factory=DIEnvelope)


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: Literal["user", "agent"]
    parts: list[Part]
    messageId: str = Field(default_factory=lambda: str(uuid4()))
    taskId: str | None = None
    contextId: str | None = None
    referenceTaskIds: list[str] = Field(default_factory=list)
    metadata: MessageMetadata = Field(default_factory=MessageMetadata)


# ─── Task ───────────────────────────────────────────────────────────────────
class TaskStatusModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    state: TaskStatus
    message: Message | None = None
    timestamp: str | None = None


class TaskArtifact(BaseModel):
    model_config = ConfigDict(extra="allow")
    artifactId: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    parts: list[Part]


class TaskMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    di: DIEnvelope = Field(default_factory=DIEnvelope)


class Task(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(default_factory=lambda: str(uuid4()))
    contextId: str = Field(default_factory=lambda: str(uuid4()))
    status: TaskStatusModel
    artifacts: list[TaskArtifact] = Field(default_factory=list)
    history: list[Message] = Field(default_factory=list)
    metadata: TaskMetadata = Field(default_factory=TaskMetadata)
    kind: Literal["task"] = "task"


# ─── Streaming events ───────────────────────────────────────────────────────
class TaskStatusUpdateEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: Literal["status-update"] = "status-update"
    taskId: str
    contextId: str
    status: TaskStatusModel
    final: bool = False


class TaskArtifactUpdateEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: Literal["artifact-update"] = "artifact-update"
    taskId: str
    contextId: str
    artifact: TaskArtifact
    lastChunk: bool = True


# ─── Agent Card schemas ─────────────────────────────────────────────────────
class AgentSkill(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    inputModes: list[str] | None = None
    outputModes: list[str] | None = None


class AgentCapabilities(BaseModel):
    model_config = ConfigDict(extra="allow")
    streaming: bool = True
    pushNotifications: bool = False
    stateTransitionHistory: bool = True


class AgentCard(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    description: str
    url: str
    version: str
    capabilities: AgentCapabilities
    defaultInputModes: list[str] = Field(default_factory=lambda: ["text/plain", "application/json"])
    defaultOutputModes: list[str] = Field(default_factory=lambda: ["text/plain", "application/json"])
    skills: list[AgentSkill]
    securitySchemes: dict[str, Any] = Field(default_factory=dict)
    security: list[dict[str, list[str]]] = Field(default_factory=list)
    provider: dict[str, Any] | None = None
    documentationUrl: str | None = None


# ─── JSON-RPC envelopes ─────────────────────────────────────────────────────
class JSONRPCRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    jsonrpc: Literal["2.0"]
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    id: str | int | None = None


class JSONRPCError(BaseModel):
    code: int
    message: str
    data: Any | None = None


class JSONRPCResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    result: Any | None = None
    error: JSONRPCError | None = None
