"""Company sponsorships and mentorship offers for student projects.

A sponsorship is a recorded offer with an honest status timeline. Nothing is
marked 'disbursed' unless a company explicitly records it — the platform never
implies money moved.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from ..core import audit
from ..dependencies import CsrfDep, CurrentUser, DbDep, OptionalUser, PageDep, paginated
from ..models import (
    Company,
    Project,
    Role,
    Sponsorship,
    SponsorshipStatus,
    User,
)
from ..schemas import SponsorshipIn, SponsorshipOut, SponsorshipPatch
from ..services import notification_service

router = APIRouter(prefix="/api/sponsorships", tags=["sponsorships"])

STAFF = {Role.ADMIN, *Role.UNIVERSITY_ROLES}


def _company_of(user: User) -> Company | None:
    return user.company


def _out(s: Sponsorship) -> SponsorshipOut:
    o = SponsorshipOut.model_validate(s)
    o.company_name = s.company.name if s.company else None
    o.project_title = s.project.title if s.project else None
    return o


@router.get("", response_model=dict)
def list_sponsorships(db: DbDep, page: PageDep, user: OptionalUser = None,
                      project_id: int | None = Query(default=None),
                      mine: bool = Query(default=False)):
    q = db.query(Sponsorship)
    if user and user.role == Role.COMPANY:
        company = _company_of(user)
        if mine and not company:
            raise HTTPException(status_code=400, detail="No company profile is linked to this account.")
        if company:
            q = q.filter(Sponsorship.company_id == company.id)
    if project_id:
        q = q.filter(Sponsorship.project_id == project_id)
    q = q.order_by(Sponsorship.created_at.desc())
    total = q.count()
    rows = q.offset(page.offset).limit(page.page_size).all()
    return paginated([_out(s) for s in rows], total, page.page, page.page_size)


@router.get("/{sponsorship_id}", response_model=SponsorshipOut)
def get_sponsorship(sponsorship_id: int, db: DbDep, user: OptionalUser = None):
    s = db.get(Sponsorship, sponsorship_id)
    if not s:
        raise HTTPException(status_code=404, detail="Sponsorship not found.")
    return _out(s)


@router.post("", response_model=SponsorshipOut, status_code=status.HTTP_201_CREATED)
def propose_sponsorship(payload: SponsorshipIn, db: DbDep, user: CurrentUser, csrf: CsrfDep,
                        project_id: int = Query(...)):
    if user.role != Role.COMPANY:
        raise HTTPException(status_code=403, detail="Only company accounts can propose sponsorship.")
    company = _company_of(user)
    if not company:
        raise HTTPException(status_code=400, detail="No company profile is linked to this account.")
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    s = Sponsorship(
        project_id=p.id, company_id=company.id, kind=payload.kind,
        amount=payload.amount, currency=payload.currency, description=payload.description,
        status=SponsorshipStatus.PROPOSED,
    )
    db.add(s)
    db.flush()
    if p.mentor_id:
        notification_service.notify(
            db, p.mentor_id, f"New sponsorship offer for '{p.title}'",
            f"{company.name} proposed {payload.kind}. Review and respond.",
            kind="sponsorship", link=f"/project.html?id={p.id}",
        )
    audit.log(db, user, "sponsorship.proposed", target_type="sponsorship", target_id=s.id,
              metadata={"project_id": p.id, "kind": payload.kind})
    db.commit()
    db.refresh(s)
    return _out(s)


@router.patch("/{sponsorship_id}/status", response_model=SponsorshipOut)
def update_status(sponsorship_id: int, payload: SponsorshipPatch, db: DbDep,
                  user: CurrentUser, csrf: CsrfDep):
    s = db.get(Sponsorship, sponsorship_id)
    if not s:
        raise HTTPException(status_code=404, detail="Sponsorship not found.")
    company = _company_of(user)
    is_owner_company = company is not None and s.company_id == company.id
    p = s.project
    is_project_staff = user.role in STAFF or (p and (p.mentor_id == user.id or p.created_by_id == user.id))
    if not (is_owner_company or is_project_staff):
        raise HTTPException(status_code=403, detail="You cannot modify this sponsorship.")

    # Company may only withdraw its own offer; the project side accepts/declines/records.
    if is_owner_company and not is_project_staff and payload.status not in {SponsorshipStatus.DECLINED}:
        raise HTTPException(
            status_code=403,
            detail="A company can only withdraw (decline) its own offer; acceptance is recorded by the project team.",
        )
    s.status = payload.status
    s.status_note = payload.status_note
    audit.log(db, user, "sponsorship.status", target_type="sponsorship", target_id=s.id,
              reason=payload.status_note, metadata={"status": payload.status})
    db.commit()
    db.refresh(s)
    return _out(s)


@router.post("/{sponsorship_id}/disburse", response_model=SponsorshipOut)
def record_disbursement(sponsorship_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    """Only the sponsoring company can record that a commitment was actually paid."""
    s = db.get(Sponsorship, sponsorship_id)
    if not s:
        raise HTTPException(status_code=404, detail="Sponsorship not found.")
    company = _company_of(user)
    if not company or s.company_id != company.id:
        raise HTTPException(status_code=403,
                            detail="Only the sponsoring company can record a disbursement.")
    if s.status not in {SponsorshipStatus.ACCEPTED, SponsorshipStatus.COMMITTED}:
        raise HTTPException(status_code=409,
                            detail="Accept or commit the sponsorship before recording a disbursement.")
    s.status = SponsorshipStatus.DISBURSED
    p = s.project
    if p and p.mentor_id:
        notification_service.notify(
            db, p.mentor_id, f"Sponsorship recorded as disbursed for '{p.title}'",
            f"{company.name} marked their {s.kind} contribution as disbursed.",
            kind="sponsorship", link=f"/project.html?id={p.id}",
        )
    audit.log(db, user, "sponsorship.disbursed", target_type="sponsorship", target_id=s.id)
    db.commit()
    db.refresh(s)
    return _out(s)
