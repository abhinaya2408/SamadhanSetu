"""Company talent discovery and invitations.

Privacy rule: a company only ever sees students who explicitly opted in to
sharing their portfolio (``consent_portfolio_share``). Direct contact details are
never exposed — the invitation goes through the platform.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from ..core import audit
from ..dependencies import CsrfDep, CurrentUser, DbDep, PageDep, paginated
from ..models import Company, ProjectMember, Role, StudentProfile, TalentInvitation, User
from ..schemas import InvitationIn, InvitationOut, StudentOut
from ..services import notification_service

router = APIRouter(prefix="/api/talent", tags=["talent"])


def _company(user: User) -> Company:
    c = user.company
    if not c:
        raise HTTPException(status_code=400, detail="No company profile is linked to this account.")
    return c


def _student_out(db, profile: StudentProfile, user: User) -> StudentOut:
    active = db.query(ProjectMember).filter(
        ProjectMember.user_id == user.id, ProjectMember.status == "active").count()
    out = StudentOut(
        id=user.id, full_name=user.full_name,
        department=profile.department.name if profile.department else None,
        university=profile.university.name if profile.university else None,
        academic_year=profile.academic_year, experience_level=profile.experience_level,
        skills=profile.skills, interests=profile.interests,
        availability_hours=profile.availability_hours, preferred_role=profile.preferred_role,
        portfolio_url=profile.portfolio_url if profile.consent_portfolio_share else None,
        github_url=profile.github_url if profile.consent_portfolio_share else None,
        consent_portfolio_share=profile.consent_portfolio_share,
        active_projects=active,
    )
    return out


@router.get("/students", response_model=dict)
def browse_students(db: DbDep, user: CurrentUser, page: PageDep,
                    q: str | None = Query(default=None),
                    department: str | None = Query(default=None),
                    experience: str | None = Query(default=None)):
    if user.role != Role.COMPANY:
        raise HTTPException(status_code=403, detail="Only company accounts can browse students.")
    query = (
        db.query(StudentProfile, User)
        .join(User, User.id == StudentProfile.user_id)
        .filter(StudentProfile.consent_portfolio_share == True,  # noqa: E712
                User.is_active == True)  # noqa: E712
    )
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            (User.full_name.ilike(like)) | (StudentProfile.skills.ilike(like))
            | (StudentProfile.interests.ilike(like)))
    if experience:
        query = query.filter(StudentProfile.experience_level == experience)
    if department:
        from ..models import Department
        dept_ids = [d.id for d in db.query(Department).filter(Department.name.ilike(f"%{department}%")).all()]
        query = query.filter(StudentProfile.department_id.in_(dept_ids or [-1]))

    total = query.count()
    rows = query.order_by(User.full_name.asc()).offset(page.offset).limit(page.page_size).all()
    items = [_student_out(db, p, u) for p, u in rows]
    return {
        **paginated(items, total, page.page, page.page_size),
        "consent_note": ("Only students who opted in to portfolio sharing appear here. "
                         "Contact details are never disclosed."),
    }


@router.post("/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
def send_invitation(payload: InvitationIn, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    if user.role != Role.COMPANY:
        raise HTTPException(status_code=403, detail="Only company accounts can send invitations.")
    company = _company(user)
    student = db.get(User, payload.student_id)
    if not student or student.role != Role.STUDENT:
        raise HTTPException(status_code=404, detail="Student not found.")
    profile = student.student_profile
    kind = payload.kind if payload.kind in {"internship", "recruitment"} else "internship"
    inv = TalentInvitation(
        company_id=company.id, student_id=student.id, kind=kind, message=payload.message,
    )
    db.add(inv)
    db.flush()
    notification_service.notify(
        db, student.id,
        f"{company.name} sent you a {kind} opportunity",
        payload.message,
        kind="invitation", link="/dashboard.html",
    )
    # Record consent visibility at invitation time for accountability.
    audit.log(db, user, "talent.invitation_sent", target_type="user", target_id=student.id,
              metadata={"kind": kind, "portfolio_consent": bool(profile and profile.consent_portfolio_share)})
    db.commit()
    db.refresh(inv)
    o = InvitationOut.model_validate(inv)
    o.company_name = company.name
    o.student_name = student.full_name
    return o


@router.get("/invitations", response_model=dict)
def list_invitations(db: DbDep, user: CurrentUser, page: PageDep,
                     box: str = Query(default="auto")):
    q = db.query(TalentInvitation)
    if user.role == Role.COMPANY:
        q = q.filter(TalentInvitation.company_id == _company(user).id)
    elif user.role == Role.STUDENT:
        q = q.filter(TalentInvitation.student_id == user.id)
    else:
        raise HTTPException(status_code=403, detail="Not permitted.")
    q = q.order_by(TalentInvitation.created_at.desc())
    total = q.count()
    rows = q.offset(page.offset).limit(page.page_size).all()
    items = []
    for inv in rows:
        o = InvitationOut.model_validate(inv)
        o.company_name = inv.company.name if inv.company else None
        o.student_name = inv.student.full_name if inv.student else None
        items.append(o)
    return paginated(items, total, page.page, page.page_size)


@router.patch("/invitations/{invitation_id}", response_model=InvitationOut)
def respond_invitation(invitation_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep,
                       decision: str = Query(...)):
    if user.role != Role.STUDENT:
        raise HTTPException(status_code=403, detail="Only the invited student can respond.")
    inv = db.get(TalentInvitation, invitation_id)
    if not inv or inv.student_id != user.id:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    if decision not in {"accepted", "declined"}:
        raise HTTPException(status_code=400, detail="decision must be 'accepted' or 'declined'.")
    inv.status = decision
    if inv.company and inv.company.user_id:
        notification_service.notify(
            db, inv.company.user_id,
            f"Invitation {decision} by a student",
            f"The student {decision} your {inv.kind} opportunity.",
            kind="invitation",
        )
    audit.log(db, user, "talent.invitation_answered", target_type="user", target_id=user.id,
              metadata={"invitation_id": inv.id, "decision": decision})
    db.commit()
    db.refresh(inv)
    o = InvitationOut.model_validate(inv)
    o.company_name = inv.company.name if inv.company else None
    o.student_name = user.full_name
    return o
