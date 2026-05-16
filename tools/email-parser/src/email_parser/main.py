"""Email parser sidecar.

Single-purpose tool container. Given a raw email payload (headers + body),
returns the canonical fields used by the Incident Intake Agent's step 1:
``from``, ``subject``, ``body``, ``received_at``, attachment list.

This is a TOOL, not an agent — no LLM, no business logic. Pure parsing.
The DI AI Framework forbids tools from being library-imported, so even
this trivial parser runs as its own container.
"""
from __future__ import annotations

import email
from email.message import Message
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="email-parser", version="0.1.0")


class ParseRequest(BaseModel):
    raw: str = Field(..., description="Raw email source (MIME or plain text)")


class ParsedEmail(BaseModel):
    sender: str | None
    subject: str | None
    received_at: str | None
    body: str
    attachment_filenames: list[str]
    headers: dict[str, str]


def _walk_body(msg: Message) -> str:
    if msg.is_multipart():
        parts: list[str] = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    parts.append(payload.decode(errors="replace"))
                elif isinstance(payload, str):
                    parts.append(payload)
        return "\n".join(parts).strip()
    payload = msg.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(errors="replace").strip()
    if isinstance(payload, str):
        return payload.strip()
    return ""


def _attachments(msg: Message) -> list[str]:
    out: list[str] = []
    for part in msg.walk():
        disp = (part.get("Content-Disposition") or "").lower()
        if "attachment" not in disp:
            continue
        name = part.get_filename()
        if name:
            out.append(name)
    return out


@app.post("/parse", response_model=ParsedEmail)
async def parse(req: ParseRequest) -> ParsedEmail:
    try:
        msg = email.message_from_string(req.raw)
    except Exception as exc:  # extremely tolerant — even malformed strings round-trip
        raise HTTPException(status_code=400, detail=f"Cannot parse email: {exc}") from exc
    return ParsedEmail(
        sender=msg.get("From"),
        subject=msg.get("Subject"),
        received_at=msg.get("Date"),
        body=_walk_body(msg),
        attachment_filenames=_attachments(msg),
        headers={k: v for k, v in msg.items()},
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "email-parser", "version": "0.1.0"}
