"""Expire completed AI result slots without touching saved editorial content."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import AIPreviewResult, AITitleResult


def cleanup_ai_results(session: Session, *, dry_run: bool = False, now: datetime | None = None) -> dict:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=7)
    counts = {}
    for model in (AIPreviewResult, AITitleResult):
        condition = (
            model.status.in_(("succeeded", "failed")),
            func.coalesce(model.finished_at, model.requested_at) < cutoff,
        )
        if dry_run:
            count = session.scalar(select(func.count()).select_from(model).where(*condition))
        else:
            count = session.execute(delete(model).where(*condition)).rowcount
        counts[model.__tablename__] = count
    return {"dry_run": dry_run, "cutoff": cutoff.isoformat(), "counts": counts}
