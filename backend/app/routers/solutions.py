"""Solution repository: prior work students can reuse, adapt, or build beyond.

Entries are only added by authorised staff (faculty / university admin / admin).
Licence information is stored only when the contributor can state it; the UI
never implies a licence that has not been recorded.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from ..core import audit
from ..dependencies import CsrfDep, CurrentUser, DbDep, PageDep, OptionalUser, paginated
from ..models import Project, Role, SolutionResource
from ..schemas import SolutionIn, SolutionOut

router = APIRouter(prefix="/api/solutions", tags=["solutions"])


def _out(s: SolutionResource) -> SolutionOut:
    return SolutionOut.model_validate(s)


@router.get("", response_model=dict)
def list_solutions(
    db: DbDep,
    page: PageDep,
    user: OptionalUser = None,
    q: str | None = Query(default=None),
    review_status: str | None = Query(default=None),
):
    query = db.query(SolutionResource)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            SolutionResource.title.ilike(like)
            | SolutionResource.description.ilike(like)
            | SolutionResource.keywords.ilike(like)
            | SolutionResource.technologies.ilike(like)
        )
    if review_status:
        query = query.filter(SolutionResource.review_status == review_status)
    query = query.order_by(SolutionResource.created_at.desc())
    total = query.count()
    rows = query.offset(page.offset).limit(page.page_size).all()
    return paginated([_out(s) for s in rows], total, page.page, page.page_size)


@router.get("/{solution_id}", response_model=SolutionOut)
def get_solution(solution_id: int, db: DbDep, user: OptionalUser = None):
    s = db.get(SolutionResource, solution_id)
    if not s:
        raise HTTPException(status_code=404, detail="Solution not found.")
    return _out(s)


@router.post("", response_model=SolutionOut, status_code=status.HTTP_201_CREATED)
def create_solution(payload: SolutionIn, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    if user.role not in {Role.ADMIN, *Role.UNIVERSITY_ROLES}:
        raise HTTPException(status_code=403, detail="Only faculty or staff can add repository entries.")
    if payload.source_url and not payload.source_label:
        raise HTTPException(
            status_code=400,
            detail="State where an external source came from (source label) before saving it.",
        )
    s = SolutionResource(
        title=payload.title.strip(),
        description=payload.description.strip(),
        source_url=payload.source_url,
        source_label=payload.source_label or "Internal repository",
        technologies=payload.technologies,
        license_info=payload.license_info,
        limitations=payload.limitations,
        keywords=payload.keywords,
        review_status="mentor_verified",
        added_by_id=user.id,
        is_demo=False,
    )
    db.add(s)
    audit.log(db, user, "solution.added", target_type="solution", metadata={"title": s.title})
    db.commit()
    db.refresh(s)
    return _out(s)


@router.post("/{solution_id}/review", response_model=SolutionOut)
def review_solution(solution_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep,
                    decision: str = Query(...)):
    if user.role not in {Role.ADMIN, *Role.UNIVERSITY_ROLES}:
        raise HTTPException(status_code=403, detail="Only faculty or staff can review repository entries.")
    if decision not in {"mentor_verified", "rejected", "unreviewed"}:
        raise HTTPException(status_code=400, detail="Unknown review status.")
    s = db.get(SolutionResource, solution_id)
    if not s:
        raise HTTPException(status_code=404, detail="Solution not found.")
    s.review_status = decision
    audit.log(db, user, "solution.reviewed", target_type="solution", target_id=s.id,
              metadata={"decision": decision})
    db.commit()
    db.refresh(s)
    return _out(s)


@router.post("/from-project/{project_id}", response_model=SolutionOut, status_code=status.HTTP_201_CREATED)
def add_from_project(project_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    """Publish a completed project as reusable prior art (staff only)."""
    if user.role not in {Role.ADMIN, *Role.UNIVERSITY_ROLES}:
        raise HTTPException(status_code=403, detail="Only faculty or staff can publish prior art.")
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    if p.status not in {"completed", "active"}:
        raise HTTPException(status_code=409, detail="Only active or completed projects can be published.")
    s = SolutionResource(
        title=p.title,
        description=(p.problem_statement or "")[:4000],
        source_label="Completed student project",
        technologies=p.required_skills,
        limitations="Reuse must respect the project's privacy and IP terms recorded at creation.",
        keywords=p.required_skills,
        origin_project_id=p.id,
        review_status="mentor_verified",
        added_by_id=user.id,
        is_demo=False,
    )
    db.add(s)
    audit.log(db, user, "solution.from_project", target_type="solution", target_id=p.id)
    db.commit()
    db.refresh(s)
    return _out(s)
