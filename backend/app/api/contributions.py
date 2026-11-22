"""Community data-contribution submission and review listing endpoints."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_service
from app.schemas.models import ContributionIn, ContributionOut
from app.services.transit_service import TransitService

router = APIRouter(prefix="/api/contributions", tags=["contributions"])


@router.get("", response_model=list[ContributionOut], summary="List community transit data submissions")
def list_contributions(
    status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    limit: int = Query(50, ge=1, le=200),
    svc: TransitService = Depends(get_service),
) -> list[ContributionOut]:
    con = svc.connect_db()
    try:
        sql = (
            "SELECT c.contribution_id, COALESCE(u.display_name, 'Anonymous') AS contributor_name, "
            "c.kind, c.payload_json, c.status, c.submitted_at "
            "FROM contributions c LEFT JOIN contributors u ON c.contributor_id = u.contributor_id "
        )
        params: list = []
        if status:
            sql += "WHERE c.status = ? "
            params.append(status)
        sql += "ORDER BY c.contribution_id DESC LIMIT ?"
        params.append(limit)
        rows = con.execute(sql, params).fetchall()
        return [
            ContributionOut(
                contribution_id=r["contribution_id"],
                contributor_name=r["contributor_name"],
                kind=r["kind"],
                payload=json.loads(r["payload_json"]),
                status=r["status"],
                submitted_at=r["submitted_at"],
            )
            for r in rows
        ]
    finally:
        con.close()


@router.post("", response_model=ContributionOut, status_code=201, summary="Submit a community stop/route contribution")
def create_contribution(
    body: ContributionIn,
    svc: TransitService = Depends(get_service),
) -> ContributionOut:
    con = svc.connect_db()
    try:
        with con:
            existing = con.execute(
                "SELECT contributor_id FROM contributors WHERE display_name = ? AND COALESCE(contact, '') = ?",
                (body.contributor_name, body.contact),
            ).fetchone()
            if existing:
                contributor_id = existing["contributor_id"]
            else:
                cur = con.execute(
                    "INSERT INTO contributors (display_name, contact) VALUES (?, ?)",
                    (body.contributor_name, body.contact or None),
                )
                contributor_id = cur.lastrowid

            cur = con.execute(
                "INSERT INTO contributions (contributor_id, kind, payload_json, status) VALUES (?, ?, ?, 'pending')",
                (contributor_id, body.kind, json.dumps(body.payload, ensure_ascii=False)),
            )
            cid = cur.lastrowid
            row = con.execute(
                "SELECT contribution_id, kind, payload_json, status, submitted_at FROM contributions WHERE contribution_id = ?",
                (cid,),
            ).fetchone()
        return ContributionOut(
            contribution_id=row["contribution_id"],
            contributor_name=body.contributor_name,
            kind=row["kind"],
            payload=json.loads(row["payload_json"]),
            status=row["status"],
            submitted_at=row["submitted_at"],
        )
    finally:
        con.close()
