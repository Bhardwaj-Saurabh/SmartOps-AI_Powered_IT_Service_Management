"""Taxonomy lookup sidecar.

Single-purpose: validate a (service_area, category) pair against a versioned
ITSM taxonomy, and answer "given this service, what area/category fits?"
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="taxonomy-lookup", version="0.1.0")


_TAXONOMY_PATH = os.environ.get("TAXONOMY_PATH", "/app/data/itsm_taxonomy.yaml")
_taxonomy: dict[str, Any] = yaml.safe_load(Path(_TAXONOMY_PATH).read_text())


class ValidateRequest(BaseModel):
    service_area: str
    category: str | None = None


class ValidateResponse(BaseModel):
    service_area_valid: bool
    category_valid: bool
    taxonomy_version: str


class LookupByServiceRequest(BaseModel):
    service: str


class LookupByServiceResponse(BaseModel):
    matched: bool
    service_area: str | None
    category: str | None
    taxonomy_version: str


@app.post("/validate", response_model=ValidateResponse)
async def validate(req: ValidateRequest) -> ValidateResponse:
    area = (_taxonomy.get("service_areas") or {}).get(req.service_area)
    if area is None:
        return ValidateResponse(service_area_valid=False, category_valid=False, taxonomy_version=_taxonomy["version"])
    if req.category is None:
        return ValidateResponse(service_area_valid=True, category_valid=True, taxonomy_version=_taxonomy["version"])
    return ValidateResponse(
        service_area_valid=True,
        category_valid=req.category in (area.get("categories") or []),
        taxonomy_version=_taxonomy["version"],
    )


@app.post("/lookup_by_service", response_model=LookupByServiceResponse)
async def lookup_by_service(req: LookupByServiceRequest) -> LookupByServiceResponse:
    services = _taxonomy.get("services") or {}
    entry = services.get(req.service.lower())
    if entry is None:
        return LookupByServiceResponse(matched=False, service_area=None, category=None, taxonomy_version=_taxonomy["version"])
    return LookupByServiceResponse(
        matched=True,
        service_area=entry["service_area"],
        category=entry["category"],
        taxonomy_version=_taxonomy["version"],
    )


@app.get("/taxonomy")
async def full_taxonomy() -> dict[str, Any]:
    return _taxonomy


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "taxonomy-lookup", "version": "0.1.0", "taxonomy_version": _taxonomy["version"]}
