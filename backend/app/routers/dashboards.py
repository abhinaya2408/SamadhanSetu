"""Role dashboards, the impact record, and the admin audit trail."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..core.permissions import require_admin
from ..dependencies import CurrentUser, DbDep, OptionalUser, PageDep, paginated
from ..models import (
    AuditLog,
    Challenge,
    ImpactReport,
    Milestone,
    MilestoneStatus,
    Project,
    Role,
    Task,
    TaskStatus,
    VerificationStatus,
)
from ..schemas import AuditLogOut, ImpactReportOut
from ..services import analytics_service

router = APIRouter(tags=["dashboards"])

STAFF = {Role.ADMIN, Role.FACULTY, Role.UNIVERSITY_ADMIN}


@router.get("/api/public/stats")
def public_stats(db: DbDep):
    """Counts for the public landing page — computed from real records."""
    from sqlalchemy import func
    approved = db.query(func.count(Challenge.id)).filter(
        Challenge.status.in_(["approved", "accepted", "in_project", "completed"])).scalar() or 0
    active_projects = db.query(func.count(Project.id)).filter(
        Project.status.in_(["planning", "active"])).scalar() or 0
    universities = db.query(func.count(func.distinct(Project.university_id))).filter(
        Project.university_id.isnot(None)).scalar() or 0
    verified_impact = db.query(func.count(ImpactReport.id)).filter(
        ImpactReport.verification_status == VerificationStatus.VERIFIED).scalar() or 0
    submitted = db.query(func.count(Challenge.id)).scalar() or 0
    return {
        "approved_challenges": approved,
        "active_projects": active_projects,
        "universities": universities,
        "verified_impact": verified_impact,
        "problems_submitted": submitted,
        "note": "Counts come from this deployment's database. Seeded records are labelled 'Demo data'.",
    }


@router.get("/api/dashboard")
def role_dashboard(db: DbDep, user: CurrentUser):
    """Return the summary that matches the signed-in user's role."""
    role = user.role
    if role == Role.CITIZEN:
        summary = analytics_service.citizen_summary(db, user)
    elif role == Role.STUDENT:
        summary = analytics_service.student_summary(db, user)
    elif role == Role.FACULTY:
        summary = analytics_service.mentor_summary(db, user)
    elif role == Role.UNIVERSITY_ADMIN:
        summary = analytics_service.university_summary(db, user)
    elif role == Role.COMPANY:
        summary = analytics_service.company_summary(db, user)
    elif role == Role.ADMIN:
        summary = analytics_service.admin_summary(db, user)
    else:
        raise HTTPException(status_code=403, detail="Unknown role.")
    return {"role": role, "full_name": user.full_name, "summary": summary}


@router.get("/api/dashboard/action-items")
def action_items(db: DbDep, user: CurrentUser):
    """A compact, role-aware to-do list built from real records."""
    items: list[dict] = []

    if user.role == Role.CITIZEN:
        rows = db.query(Challenge).filter(
            Challenge.citizen_id == user.id,
            Challenge.status.in_(["needs_clarification", "pending_review"])).all()
        for c in rows:
            items.append({
                "kind": "challenge",
                "id": c.id,
                "title": c.title,
                "status": c.status,
                "action": "Answer reviewer questions" if c.status == "needs_clarification" else "Awaiting review",
                "link": f"/challenge.html?id={c.id}",
            })
        for c in db.query(Challenge).filter(Challenge.citizen_id == user.id,
                                            Challenge.status.in_(["accepted", "in_project"]),
                                            Challenge.project.has()).all():
            items.append({
                "kind": "project", "id": c.project.id, "title": c.project.title,
                "status": c.project.status, "action": "View project progress",
                "link": f"/project.html?id={c.project.id}",
            })

    elif user.role == Role.STUDENT:
        tasks = db.query(Task).filter(
            Task.assignee_id == user.id, Task.status != TaskStatus.COMPLETED).all()
        for t in tasks:
            items.append({
                "kind": "task", "id": t.id, "title": t.title,
                "status": "overdue" if t.is_overdue else t.status,
                "action": "Overdue — update your mentor" if t.is_overdue else "Continue working",
                "link": f"/project.html?id={t.project_id}",
            })

    elif user.role in STAFF:
        pending = db.query(Challenge).filter(
            Challenge.status.in_(["submitted", "needs_clarification", "pending_review"])).count()
        if pending:
            items.append({"kind": "review", "id": None, "title": f"{pending} challenges awaiting review",
                          "status": "pending_review", "action": "Open the review queue",
                          "link": "/reviews.html"})
        if user.role == Role.FACULTY:
            submitted = db.query(Milestone).join(Project, Milestone.project_id == Project.id).filter(
                Project.mentor_id == user.id,
                Milestone.status == MilestoneStatus.SUBMITTED).all()
            for m in submitted:
                items.append({
                    "kind": "milestone", "id": m.id, "title": m.name,
                    "status": "submitted_for_review", "action": "Review milestone evidence",
                    "link": f"/project.html?id={m.project_id}",
                })

    return {"items": items, "count": len(items)}


@router.get("/api/impact", response_model=dict)
def list_impact(db: DbDep, page: PageDep, user: OptionalUser = None,
                verification_status: str | None = Query(default=None)):
    """Verified impact record — public-readable, only verified entries by default."""
    q = db.query(ImpactReport)
    if user and user.role in STAFF:
        if verification_status:
            q = q.filter(ImpactReport.verification_status == verification_status)
    else:
        q = q.filter(ImpactReport.verification_status == VerificationStatus.VERIFIED)
    q = q.order_by(ImpactReport.created_at.desc())
    total = q.count()
    rows = q.offset(page.offset).limit(page.page_size).all()
    items = []
    for r in rows:
        o = ImpactReportOut.model_validate(r)
        o.is_demo = r.is_demo
        p = db.get(Project, r.project_id)
        item = o.model_dump()
        item["project_title"] = p.title if p else None
        item["university"] = p.university.name if p and p.university else None
        items.append(item)
    return {
        **paginated(items, total, page.page, page.page_size),
        "note": ("Only reports marked 'verified' by university staff are shown to the public. "
                 "Each report states its own verification status and limitations."),
    }


@router.get("/api/audit", response_model=dict)
def audit_trail(db: DbDep, user: CurrentUser, page: PageDep,
                action: str | None = Query(default=None)):
    require_admin(user)
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    q = q.order_by(AuditLog.created_at.desc())
    total = q.count()
    rows = q.offset(page.offset).limit(page.page_size).all()
    items = []
    for r in rows:
        o = AuditLogOut.model_validate(r)
        o.actor_name = r.actor.full_name if r.actor else None
        items.append(o)
    return paginated(items, total, page.page, page.page_size)
