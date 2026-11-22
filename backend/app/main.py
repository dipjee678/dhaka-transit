"""FastAPI entry point for Dhaka Transit.

Run locally from backend/:
    uvicorn app.main:app --reload --port 8000

Interactive OpenAPI docs are served at ``/docs`` and ``/redoc``.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import contributions, dataset, health, plan, routes, stops
from app.config import Settings
from app.services.transit_service import TransitService


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.transit = TransitService(cfg)
        yield

    application = FastAPI(
        title="Dhaka Transit API",
        description=(
            "Open Public Transport Dataset (GTFS) and Multimodal Route Planner REST API "
            "for Dhaka, Bangladesh (MRT Line 6 + Bus + Walking)."
        ),
        version="0.5.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(health.router)
    application.include_router(stops.router)
    application.include_router(routes.router)
    application.include_router(plan.router)
    application.include_router(dataset.router)
    application.include_router(contributions.router)

    @application.get("/api", tags=["root"])
    def api_root() -> dict:
        return {
            "name": "Dhaka Transit API",
            "version": application.version,
            "docs": "/docs",
            "health": "/api/health",
            "dataset": "/api/dataset",
            "plan": "/api/plan",
        }

    if cfg.frontend_dir.is_dir():
        application.mount("/", StaticFiles(directory=cfg.frontend_dir, html=True), name="frontend")

    return application


app = create_app()
