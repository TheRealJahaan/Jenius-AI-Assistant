"""Uploading, listing, reviewing and exporting datasets."""
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response as RawResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from ...billing import limits_for
from ...config import Settings
from ...ingest import UploadError, anomalies_csv, create_dataset, parse_notes, parse_shipments
from ...models import Anomaly, Dataset, Shipment, User
from ..deps import (
    get_db,
    get_owned_dataset,
    get_settings,
    get_visible_dataset,
    optional_user,
    rate_limit,
    require_user,
    visible_datasets_filter,
)
from ..schemas import AnomalyOut, DatasetDetail, DatasetOut, ReviewIn, RouteStat

router = APIRouter(prefix="/api/datasets", tags=["datasets"])
CPTK = Shipment.freight_cost_inr / (Shipment.quantity_tonnes * Shipment.distance_km)
log = logging.getLogger("jenius.datasets")


def dataset_out(dataset: Dataset, user: User | None) -> DatasetOut:
    out = DatasetOut.model_validate(dataset)
    out.is_mine = user is not None and dataset.owner_id == user.id
    return out


def read_limited(upload: UploadFile, max_bytes: int, label: str) -> bytes:
    chunks, size = [], 0
    while chunk := upload.file.read(1024 * 1024):
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(413, f"The {label} file is larger than {max_bytes // (1024 * 1024)} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("", response_model=list[DatasetOut])
def list_datasets(user: User | None = Depends(optional_user), db: DbSession = Depends(get_db)):
    rows = db.scalars(
        select(Dataset).where(visible_datasets_filter(user)).order_by(Dataset.is_demo.desc(), Dataset.created_at.desc())
    )
    return [dataset_out(d, user) for d in rows]


@router.post("", response_model=DatasetOut, status_code=201)
def upload_dataset(  # not async: parsing and analysis run in the thread pool, not the event loop
    request: Request,
    name: str = Form(..., min_length=1, max_length=120),
    shipments: UploadFile = File(...),
    notes: UploadFile | None = File(None),
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    rate_limit(request, "upload", str(user.id), 10, 3600)
    limits = limits_for(user, settings)
    owned = db.scalar(select(func.count(Dataset.id)).where(Dataset.owner_id == user.id)) or 0
    if owned >= limits.max_datasets:
        raise HTTPException(403, f"Your plan holds {limits.max_datasets} datasets. Delete one or upgrade to Pro.")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    shipments_raw = read_limited(shipments, max_bytes, "shipments")
    notes_raw = read_limited(notes, max_bytes, "notes") if notes is not None and notes.filename else b""
    try:
        shipment_rows = parse_shipments(shipments_raw, limits.max_rows)
        note_rows = parse_notes(notes_raw) if notes_raw.strip() else []
    except UploadError as err:
        raise HTTPException(422, {"message": str(err), "problems": err.problems}) from None
    dataset = create_dataset(db, name=" ".join(name.split()), owner_id=user.id, shipments=shipment_rows,
                             notes=note_rows)
    log.info("dataset uploaded user=%s dataset=%s rows=%d", user.id, dataset.id, len(shipment_rows))
    return dataset_out(dataset, user)


@router.get("/{dataset_id}", response_model=DatasetDetail)
def get_dataset(dataset_id: uuid.UUID, user: User | None = Depends(optional_user), db: DbSession = Depends(get_db)):
    dataset = get_visible_dataset(db, dataset_id, user)
    per_route = db.execute(
        select(Shipment.route, func.min(Shipment.route_type), func.count(), func.sum(Shipment.freight_cost_inr),
               func.avg(CPTK))
        .where(Shipment.dataset_id == dataset.id).group_by(Shipment.route).order_by(Shipment.route)
    ).all()
    review = dict(db.execute(select(Anomaly.needs_review, func.count()).where(Anomaly.dataset_id == dataset.id)
                             .group_by(Anomaly.needs_review)).all())
    return DatasetDetail(
        **dataset_out(dataset, user).model_dump(),
        total_freight_cost_inr=round(float(sum(r[3] or 0 for r in per_route)), 2),
        needs_review=review.get(True, 0),
        justified=review.get(False, 0),
        routes=[RouteStat(route=route, route_type=rtype, shipments=n, freight_cost_inr=round(float(cost), 2),
                          avg_cost_per_tonne_km=round(float(cptk), 3))
                for route, rtype, n, cost, cptk in per_route],
    )


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: uuid.UUID, user: User = Depends(require_user), db: DbSession = Depends(get_db)):
    dataset = get_owned_dataset(db, dataset_id, user)
    db.delete(dataset)
    db.commit()
    return {"ok": True}


@router.get("/{dataset_id}/anomalies", response_model=list[AnomalyOut])
def list_anomalies(dataset_id: uuid.UUID, status: str = "all", user: User | None = Depends(optional_user),
                   db: DbSession = Depends(get_db)):
    dataset = get_visible_dataset(db, dataset_id, user)
    q = select(Anomaly).where(Anomaly.dataset_id == dataset.id)
    if status == "needs_review":
        q = q.where(Anomaly.needs_review.is_(True))
    elif status == "justified":
        q = q.where(Anomaly.needs_review.is_(False))
    elif status != "all":
        raise HTTPException(422, "status must be all, needs_review or justified")
    return db.scalars(q.order_by(Anomaly.week_of.desc(), Anomaly.route)).all()


@router.patch("/{dataset_id}/anomalies/{anomaly_id}", response_model=AnomalyOut)
def review_anomaly(dataset_id: uuid.UUID, anomaly_id: int, body: ReviewIn, user: User = Depends(require_user),
                   db: DbSession = Depends(get_db)):
    dataset = get_owned_dataset(db, dataset_id, user)
    anomaly = db.scalar(select(Anomaly).where(Anomaly.id == anomaly_id, Anomaly.dataset_id == dataset.id))
    if anomaly is None:
        raise HTTPException(404, "Anomaly not found.")
    anomaly.review_status = body.review_status
    db.commit()
    return anomaly


@router.get("/{dataset_id}/anomalies.csv")
def export_anomalies(dataset_id: uuid.UUID, user: User | None = Depends(optional_user),
                     db: DbSession = Depends(get_db)):
    dataset = get_visible_dataset(db, dataset_id, user)
    rows = db.scalars(select(Anomaly).where(Anomaly.dataset_id == dataset.id)
                      .order_by(Anomaly.route, Anomaly.week_of)).all()
    return RawResponse(anomalies_csv(rows), media_type="text/csv; charset=utf-8",
                       headers={"Content-Disposition": 'attachment; filename="jenius_anomalies.csv"'})
