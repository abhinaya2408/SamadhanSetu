"""Public directory (universities/departments) and platform administration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..core import audit
from ..core.permissions import require_admin, require_university_staff
from ..dependencies import CsrfDep, CurrentUser, DbDep, OptionalUser, PageDep, paginated
from ..models import (
    Company,
    Department,
    Project,
    Role,
    University,
    User,
)
from ..schemas import (
    DepartmentOut,
    MessageOut,
    UniversityOut,
)
from ..services import notification_service
from sqlalchemy import func

router = APIRouter(prefix="/api/directory", tags=["directory"])


class UniversityIn(BaseModel):
    name: str = Field(min_length=3, max_length=255)
    short_name: str | None = Field(default=None, max_length=80)
    city: str | None = None
    state: str | None = None
    website: str | None = None
    description: str | None = None
    expertise: str | None = None


class DepartmentIn(BaseModel):
    university_id: int
    name: str = Field(min_length=1, max_length=160)
    full_name: str | None = Field(default=None, max_length=200)
    expertise: str | None = None


class UserAdminPatch(BaseModel):
    is_active: bool | None = None
    is_verified: bool | None = None


def _university_out(db, u: University) -> UniversityOut:
    out = UniversityOut.model_validate(u)
    out.department_count = db.query(func.count(Department.id)).filter(
        Department.university_id == u.id).scalar() or 0
    out.project_count = db.query(func.count(Project.id)).filter(
        Project.university_id == u.id).scalar() or 0
    return out


@router.get("/universities", response_model=dict)
def list_universities(db: DbDep, page: PageDep, user: OptionalUser = None,
                      q: str | None = Query(default=None)):
    query = db.query(University)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter((University.name.ilike(like)) | (University.city.ilike(like))
                             | (University.expertise.ilike(like)))
    query = query.order_by(University.name.asc())
    total = query.count()
    rows = query.offset(page.offset).limit(page.page_size).all()
    return paginated([_university_out(db, u) for u in rows], total, page.page, page.page_size)


@router.get("/universities/{university_id}", response_model=UniversityOut)
def get_university(university_id: int, db: DbDep, user: OptionalUser = None):
    u = db.get(University, university_id)
    if not u:
        raise HTTPException(status_code=404, detail="University not found.")
    return _university_out(db, u)


@router.post("/universities", response_model=UniversityOut, status_code=status.HTTP_201_CREATED)
def create_university(payload: UniversityIn, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    require_admin(user)
    u = University(**payload.model_dump())
    db.add(u)
    audit.log(db, user, "university.created", target_type="university", metadata={"name": u.name})
    db.commit()
    db.refresh(u)
    return _university_out(db, u)


@router.patch("/universities/{university_id}", response_model=UniversityOut)
def update_university(university_id: int, payload: UniversityIn, db: DbDep,
                      user: CurrentUser, csrf: CsrfDep):
    require_admin(user)
    u = db.get(University, university_id)
    if not u:
        raise HTTPException(status_code=404, detail="University not found.")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(u, k, v)
    audit.log(db, user, "university.updated", target_type="university", target_id=u.id)
    db.commit()
    db.refresh(u)
    return _university_out(db, u)


@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(db: DbDep, university_id: int | None = Query(default=None)):
    q = db.query(Department)
    if university_id:
        q = q.filter(Department.university_id == university_id)
    return [DepartmentOut.model_validate(d) for d in q.order_by(Department.name.asc()).all()]


@router.post("/departments", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
def create_department(payload: DepartmentIn, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    require_university_staff(user)
    u = db.get(University, payload.university_id)
    if not u:
        raise HTTPException(status_code=404, detail="University not found.")
    # A university admin may only add departments to their own university.
    if user.role == Role.UNIVERSITY_ADMIN and user.faculty_profile \
            and user.faculty_profile.university_id != u.id:
        raise HTTPException(status_code=403, detail="You can only manage your own university.")
    d = Department(**payload.model_dump())
    db.add(d)
    audit.log(db, user, "department.created", target_type="department", metadata={"name": d.name})
    db.commit()
    db.refresh(d)
    return DepartmentOut.model_validate(d)


# ---------------------------------------------------------------------------
# Admin: user + organisation management
# ---------------------------------------------------------------------------

@router.get("/users", response_model=dict)
def list_users(db: DbDep, user: CurrentUser, page: PageDep,
               role: str | None = Query(default=None),
               q: str | None = Query(default=None)):
    require_admin(user)
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter((User.full_name.ilike(like)) | (User.email.ilike(like)))
    query = query.order_by(User.created_at.desc())
    total = query.count()
    rows = query.offset(page.offset).limit(page.page_size).all()
    items = [{
        "id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role,
        "is_active": u.is_active, "is_verified": u.is_verified, "created_at": u.created_at,
    } for u in rows]
    return paginated(items, total, page.page, page.page_size)


@router.patch("/users/{user_id}", response_model=MessageOut)
def update_user(user_id: int, payload: UserAdminPatch, db: DbDep,
                user: CurrentUser, csrf: CsrfDep):
    require_admin(user)
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.role == Role.ADMIN and target.id != user.id and payload.is_active is False:
        raise HTTPException(status_code=403, detail="You cannot deactivate another administrator.")
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(target, k, v)
    if changes.get("is_verified") and target.company:
        target.company.is_verified = True
    if changes.get("is_verified") and target.role == Role.UNIVERSITY_ADMIN and target.faculty_profile:
        target.faculty_profile.is_approved = True
    audit.log(db, user, "user.updated", target_type="user", target_id=target.id,
              metadata={"changes": changes})
    db.commit()
    return MessageOut(message=f"User {target.full_name} updated.")


@router.post("/companies/{company_id}/verify", response_model=MessageOut)
def verify_company(company_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep,
                   verified: bool = Query(default=True)):
    require_admin(user)
    c = db.get(Company, company_id)
    if not c:
        raise HTTPException(status_code=404, detail="Company not found.")
    c.is_verified = verified
    if c.user:
        notification_service.notify(
            db, c.user_id,
            "Company account verification updated",
            f"Your organisation is now {'verified' if verified else 'unverified'}.",
            kind="info",
        )
    audit.log(db, user, "company.verified", target_type="company", target_id=c.id,
              metadata={"verified": verified})
    db.commit()
    return MessageOut(message=f"Company {'verified' if verified else 'unverified'}.")
