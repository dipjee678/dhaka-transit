"""Open GTFS dataset metadata, file downloads, validation & gap report endpoints."""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import get_service
from app.schemas.models import DatasetFileOut, DatasetSummaryOut
from app.services.transit_service import TransitService
from transit.gtfs.io import read_feed, read_table
from transit.gtfs.validator import summarize, validate_feed

router = APIRouter(prefix="/api/dataset", tags=["dataset"])

ATTRIBUTION_TEXT = (
    "Dhaka Transit dataset — contains OpenStreetMap data © OpenStreetMap contributors "
    "(ODbL 1.0) and official public transit information from DMTCL."
)


@router.get("", response_model=DatasetSummaryOut, summary="GTFS dataset statistics, files, provenance, and attribution")
def dataset_summary(svc: TransitService = Depends(get_service)) -> DatasetSummaryOut:
    gtfs_dir = svc.settings.gtfs_dir
    files_out: list[DatasetFileOut] = []
    for p in sorted(gtfs_dir.glob("*.txt")):
        files_out.append(DatasetFileOut(
            filename=p.name,
            rows=len(read_table(p)),
            size_bytes=p.stat().st_size,
        ))

    record_provs = [p for p in svc.feed.provenance.values() if not p.field]
    prov_cat = dict(Counter(p.category.value for p in record_provs))
    prov_stat = dict(Counter(p.status.value for p in record_provs))

    val_rep = svc.read_report_json("validation_report.json")
    gap_rep = svc.read_report_json("gap_report.json")
    info = svc.feed.feed_info

    return DatasetSummaryOut(
        feed_version=info.get("feed_version", "unknown"),
        publisher_name=info.get("feed_publisher_name", "Dhaka Transit"),
        feed_start_date=info.get("feed_start_date", ""),
        feed_end_date=info.get("feed_end_date", ""),
        attribution=ATTRIBUTION_TEXT,
        counts={
            "agencies": len(svc.feed.agencies),
            "stations": len(svc.feed.stations),
            "boarding_stops": len(svc.feed.boarding_stops),
            "routes": len(svc.feed.routes),
            "trips": len(svc.feed.trips),
            "shapes": len(svc.feed.shapes),
            "footpaths": len(svc.footpaths),
            "osm_bus_stop_inventory": len(svc.osm_bus_stops),
        },
        provenance_by_category=prov_cat,
        provenance_by_status=prov_stat,
        files=files_out,
        data_sources=[
            {
                "source_id": s.source_id, "name": s.name, "url": s.url,
                "license": s.license, "category": s.category, "retrieved_at": s.retrieved_at, "notes": s.notes,
            }
            for s in svc.feed.data_sources.values()
        ],
        validation_summary=val_rep.get("summary") if val_rep else None,
        gap_summary=(
            {
                "osm_snapshot": gap_rep.get("osm_snapshot"),
                "osm_bus_relations": len(gap_rep.get("osm_bus_relations", [])),
                "curated_bus_routes": gap_rep.get("curated_bus_routes", 0),
                "osm_bus_inventory_total": gap_rep.get("osm_bus_inventory", {}).get("total", 0),
                "verified_fare_pairs": gap_rep.get("metro", {}).get("verified_fare_pairs", 0),
            }
            if gap_rep
            else None
        ),
    )


@router.get("/download", summary="Download the complete Dhaka GTFS feed archive (dhaka_gtfs.zip)")
def download_gtfs_zip(svc: TransitService = Depends(get_service)) -> FileResponse:
    zip_path = svc.settings.gtfs_dir / "dhaka_gtfs.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="dhaka_gtfs.zip not found — run pipeline.build_gtfs")
    return FileResponse(path=zip_path, media_type="application/zip", filename="dhaka_gtfs.zip")


@router.get("/files/{filename}", summary="Download an individual GTFS .txt file")
def download_gtfs_file(filename: str, svc: TransitService = Depends(get_service)) -> FileResponse:
    # Reject path traversal strictly: only allow exact existing top-level *.txt files.
    if "/" in filename or "\\" in filename or ".." in filename or not filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="Invalid GTFS filename")
    path = svc.settings.gtfs_dir / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"GTFS file '{filename}' not found")
    return FileResponse(path=path, media_type="text/csv; charset=utf-8", filename=filename)


@router.get("/validation", summary="Latest GTFS validation report (errors, warnings, and stats)")
def validation_report(svc: TransitService = Depends(get_service)) -> dict:
    rep = svc.read_report_json("validation_report.json")
    if rep is not None:
        return rep
    issues = validate_feed(read_feed(svc.settings.gtfs_dir))
    return {"summary": summarize(issues), "issues": [i.to_dict() for i in issues]}


@router.get("/gaps", summary="Data coverage and gap analysis report")
def gap_report(svc: TransitService = Depends(get_service)) -> dict:
    rep = svc.read_report_json("gap_report.json")
    if rep is None:
        raise HTTPException(status_code=404, detail="gap_report.json not generated yet")
    return rep


@router.get("/evaluation", summary="Algorithm performance and accuracy evaluation report")
def evaluation_report(svc: TransitService = Depends(get_service)) -> dict:
    rep = svc.read_report_json("evaluation_report.json")
    if rep is None:
        raise HTTPException(status_code=404, detail="evaluation_report.json not generated yet")
    return rep
