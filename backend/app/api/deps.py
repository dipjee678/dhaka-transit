"""FastAPI dependency helpers."""
from __future__ import annotations

from fastapi import Request

from app.services.transit_service import TransitService


def get_service(request: Request) -> TransitService:
    return request.app.state.transit
