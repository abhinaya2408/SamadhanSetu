"""In-app notifications and deterministic deadline scanning."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..dependencies import CsrfDep, CurrentUser, DbDep, PageDep, paginated
from ..models import Notification, Role
from ..schemas import MessageOut, NotificationOut

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=dict)
def list_notifications(db: DbDep, user: CurrentUser, page: PageDep,
                       unread_only: bool = Query(default=False)):
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        q = q.filter(Notification.is_read == False)  # noqa: E712
    q = q.order_by(Notification.created_at.desc(), Notification.id.desc())
    total = q.count()
    rows = q.offset(page.offset).limit(page.page_size).all()
    return paginated([NotificationOut.model_validate(n) for n in rows], total, page.page, page.page_size)


@router.get("/unread-count")
def unread_count(db: DbDep, user: CurrentUser):
    count = db.query(Notification).filter(
        Notification.user_id == user.id, Notification.is_read == False).count()  # noqa: E712
    return {"unread": count}


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    n = db.get(Notification, notification_id)
    if not n or n.user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification not found.")
    n.is_read = True
    db.commit()
    db.refresh(n)
    return NotificationOut.model_validate(n)


@router.post("/read-all", response_model=MessageOut)
def mark_all_read(db: DbDep, user: CurrentUser, csrf: CsrfDep):
    updated = db.query(Notification).filter(
        Notification.user_id == user.id, Notification.is_read == False).update(  # noqa: E712
        {Notification.is_read: True})
    db.commit()
    return MessageOut(message=f"{updated} notifications marked as read.")


@router.post("/scan", response_model=MessageOut)
def run_scan(db: DbDep, user: CurrentUser, csrf: CsrfDep):
    """Run the deadline scanner. Available to staff; safe to call repeatedly."""
    if user.role not in {Role.ADMIN, Role.FACULTY, Role.UNIVERSITY_ADMIN}:
        raise HTTPException(status_code=403, detail="Only staff can trigger the deadline scan.")
    from ..services import notification_service
    result = notification_service.scan_deadlines(db)
    return {"message": "Deadline scan complete.", "detail": result}
