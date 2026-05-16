"""Document formatter sidecar.

Renders structured resolution-note JSON into markdown using named templates.
Phase 1: a small built-in template registry; Phase 2 would mount these
from a versioned templates directory.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="document-formatter", version="0.1.0")


_TEMPLATES: dict[str, str] = {
    "resolution-note-default": (
        "# {title}\n\n"
        "**Affected service:** {affected_service}\n"
        "**Service area:** {service_area}\n\n"
        "## Root cause\n{root_cause}\n\n"
        "## What was done\n{fix_summary}\n\n"
        "## Validation\n{validation}\n\n"
        "## Prevention\n{prevention}\n\n"
        "## What the user saw\n{symptoms_seen_by_user}\n\n"
        "---\n"
        "*Keywords:* {applicable_keywords_csv}\n"
    ),
    "resolution-note-network": (
        "# {title}\n\n"
        "**Service:** {affected_service}  •  **Category:** network\n\n"
        "## What broke\n{root_cause}\n\n"
        "## Fix\n{fix_summary}\n\n"
        "## How to verify\n{validation}\n\n"
        "## Preventing recurrence\n{prevention}\n\n"
        "*User-visible symptom:* {symptoms_seen_by_user}\n"
    ),
    "resolution-note-auth": (
        "# {title} (authentication)\n\n"
        "**Identity surface:** {affected_service}\n\n"
        "## Root cause\n{root_cause}\n\n"
        "## Remediation steps applied\n{fix_summary}\n\n"
        "## Verification\n{validation}\n\n"
        "## Prevention guidance\n{prevention}\n"
    ),
    "resolution-note-saas": (
        "# {title} ({affected_service})\n\n"
        "## Symptom (reporter wording)\n{symptoms_seen_by_user}\n\n"
        "## Cause\n{root_cause}\n\n"
        "## Fix\n{fix_summary}\n\n"
        "## Confirmation\n{validation}\n"
    ),
    "resolution-note-endpoint": (
        "# {title}\n\n"
        "## What was wrong\n{root_cause}\n\n"
        "## Steps applied\n{fix_summary}\n\n"
        "## Check it stuck\n{validation}\n"
    ),
}


class RenderRequest(BaseModel):
    template_id: str
    note: dict[str, Any] = Field(..., description="Structured note fields used as template variables")


class RenderResponse(BaseModel):
    template_id: str
    markdown: str


@app.post("/render", response_model=RenderResponse)
async def render(req: RenderRequest) -> RenderResponse:
    tmpl = _TEMPLATES.get(req.template_id)
    if tmpl is None:
        raise HTTPException(status_code=404, detail=f"Unknown template_id: {req.template_id}")
    fields = dict(req.note)
    # Convert lists → CSV for the *_csv variants.
    for key, value in list(fields.items()):
        if isinstance(value, list):
            fields[f"{key}_csv"] = ", ".join(str(v) for v in value)
    # Provide safe defaults for missing keys so str.format doesn't raise.
    for default_key in ("title", "affected_service", "service_area", "root_cause",
                         "fix_summary", "validation", "prevention",
                         "symptoms_seen_by_user", "applicable_keywords_csv"):
        fields.setdefault(default_key, "")
    try:
        rendered = tmpl.format(**fields)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Template variable missing: {exc}") from exc
    return RenderResponse(template_id=req.template_id, markdown=rendered)


@app.get("/templates")
async def templates() -> dict[str, Any]:
    return {"template_ids": list(_TEMPLATES.keys())}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "document-formatter", "version": "0.1.0",
            "template_count": len(_TEMPLATES)}
