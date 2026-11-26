"""Catalog (BE1): step types and connectable services, served verbatim from the portal exports
(app/data/step_types.json, services.json) via app.services.catalog_data."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import AuthContext, get_auth
from app.services import catalog_data

router = APIRouter(tags=["Catalog"])


@router.get("/catalog/step-types")
def list_step_types(auth: AuthContext = Depends(get_auth)) -> dict:
    return {"data": catalog_data.step_types()}


@router.get("/catalog/services")
def list_services(auth: AuthContext = Depends(get_auth)) -> dict:
    return {"data": catalog_data.services()}
