"""Application configuration with environment-variable overrides."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _default_cors() -> list[str]:
    raw = os.getenv("DHAKA_CORS_ORIGINS", "*")
    return [o.strip() for o in raw.split(",") if o.strip()]


@dataclass
class Settings:
    gtfs_dir: Path = field(default_factory=lambda: Path(os.getenv("DHAKA_GTFS_DIR", ROOT / "data" / "gtfs" / "dhaka")))
    db_path: Path = field(default_factory=lambda: Path(os.getenv("DHAKA_DB_PATH", ROOT / "dhaka_transit.sqlite")))
    curated_dir: Path = field(default_factory=lambda: Path(os.getenv("DHAKA_CURATED_DIR", ROOT / "data" / "curated")))
    reports_dir: Path = field(default_factory=lambda: Path(os.getenv("DHAKA_REPORTS_DIR", ROOT / "data" / "reports")))
    frontend_dir: Path = field(default_factory=lambda: Path(os.getenv("DHAKA_FRONTEND_DIR", ROOT / "frontend")))
    schema_path: Path = field(default_factory=lambda: ROOT / "database" / "schema_sqlite.sql")
    cors_origins: list[str] = field(default_factory=_default_cors)
    default_walk_radius_m: float = 800.0
    max_transfer_walk_m: float = 500.0
