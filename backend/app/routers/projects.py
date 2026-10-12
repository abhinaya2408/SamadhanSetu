"""Project workspace: team formation, responsibility forms, tasks, milestones,
evidence, weekly reports, mentor reviews and impact reports.

Permission model (enforced here, not in the UI):
- University staff (faculty / university admin / platform admin) manage a project.
- Students only see projects they are active members of.
- Every member must accept a Responsibility Form before they can be assigned work.
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, HTTPException, Query, status

from ..core import audit
from ..core.permissions import (
    can_access_project,
    require_project_access,
    require_project_staff,
)
from ..dependencies import CurrentUser, CsrfDep, DbDep, OptionalUser, PageDep, paginated
from ..models import (
    Challenge,
    ChallengeStatus,
    ImpactReport,
    MentorReview,
    Milestone,
    MilestoneStatus,
    Project,
    ProjectEvidence,
    ProjectMember,
    ResponsibilityForm,
    Role,
    Task,
    TaskStatus,
    User,
    VerificationStatus,
    WeeklyReport,
    utcnow,
)
from ..schemas import (
    EvidenceIn,
    EvidenceOut,
    ImpactReportIn,
    ImpactReportOut,
    MemberOut,
    MemberPatch,
    MentorReviewIn,
    MentorReviewOut,
    MessageOut,
    MilestoneIn,
    MilestoneOut,
    MilestonePatch,
    ProjectCreate,
    ProjectOut,
    ProjectPatch,
    ResponsibilityAccept,
    ResponsibilityOut,
    TaskIn,
    TaskOut,
    TaskPatch,
    WeeklyReportIn,
    WeeklyReportOut,
)
from ..services import analytics_service, matching_service, notification_service

router = APIRouter(prefix="/api/projects", tags=["projects"])

STAFF = {Role.ADMIN, Role.FACULTY, Role.UNIVERSITY_ADMIN}


# ---------------------------------------------------------------------------
# Serialisers
# ---------------------------------------------------------------------------

def _project_out(db, p: Project, *, with_progress: bool = True) -> ProjectOut:
    out = ProjectOut.model_validate(p)
    out.mentor_name = p.mentor.full_name if p.mentor else None
    out.university_name = p.university.name if p.university else None
    out.member_count = sum(1 for m in p.members if m.status == "active")
    if with_progress:
        out.progress_pct = analytics_service.project_progress(db, p.id)
    return out


def _member_out(m: ProjectMember) -> MemberOut:
    out = MemberOut.model_validate(m)
    out.name = m.user.full_name if m.user else None
    profile = m.user.student_profile if m.user else None
    if profile:
        out.department = profile.department.name if profile.department else None
        out.skills = profile.skills
    out.responsibility_accepted = bool(m.responsibility_form and m.responsibility_form.accepted)
    return out


def _task_out(t: Task) -> TaskOut:
    out = TaskOut.model_validate(t)
    out.assignee_name = t.assignee.full_name if t.assignee else None
    out.is_overdue = t.is_overdue
    return out


def _project_or_404(db, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    return p


def _require_staff(user: User) -> None:
    if user is None or user.role not in STAFF:
        raise HTTPException(status_code=403, detail="Only university staff can manage projects.")


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    _require_staff(user)
    challenge = db.get(Challenge, payload.challenge_id)
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    if challenge.status not in {ChallengeStatus.APPROVED, ChallengeStatus.ACCEPTED}:
        raise HTTPException(
            status_code=409,
            detail="A challenge must be approved (and optionally accepted) before a project is created.",
        )
    if challenge.project:
        raise HTTPException(status_code=409, detail="This challenge already has a project.")

    university_id = None
    if user.faculty_profile:
        university_id = user.faculty_profile.university_id
    if payload.mentor_id:
        mentor = db.get(User, payload.mentor_id)
        if not mentor or mentor.role not in {Role.FACULTY, Role.UNIVERSITY_ADMIN, Role.ADMIN}:
            raise HTTPException(status_code=400, detail="Selected mentor is not a faculty member.")

    p = Project(
        challenge_id=challenge.id,
        university_id=university_id,
        created_by_id=user.id,
        mentor_id=payload.mentor_id or (user.id if user.role == Role.FACULTY else None),
        title=payload.title.strip(),
        problem_statement=payload.problem_statement.strip(),
        objectives=payload.objectives,
        scope=payload.scope,
        deliverables=payload.deliverables,
        required_skills=payload.required_skills,
        required_disciplines=payload.required_disciplines,
        expected_team_size=payload.expected_team_size,
        difficulty=payload.difficulty,
        status="planning",
        start_date=payload.start_date,
        deadline=payload.deadline,
        required_resources=payload.required_resources,
        evaluation_criteria=payload.evaluation_criteria,
        privacy_constraints=payload.privacy_constraints,
        ip_terms=payload.ip_terms,
    )
    db.add(p)
    db.flush()

    challenge.status = ChallengeStatus.IN_PROJECT
    notification_service.notify(
        db, challenge.citizen_id,
        "A student project has been started for your problem",
        f"'{p.title}' is now underway. You will receive progress updates through this account.",
        kind="info", link=f"/project.html?id={p.id}",
    )
    audit.log(db, user, "project.created", target_type="project", target_id=p.id,
              metadata={"challenge_id": challenge.id})
    db.commit()
    db.refresh(p)
    return _project_out(db, p)


@router.get("", response_model=dict)
def list_projects(db: DbDep, page: PageDep, user: OptionalUser = None,
                  scope: str = Query(default="auto"),
                  status_filter: str | None = Query(default=None, alias="status")):
    q = db.query(Project)
    if user is None:
        # anonymous: public only sees projects that opted into public updates
        q = q.filter(Project.status == "completed")
    elif user.role in STAFF:
        if scope == "mine" and user.faculty_profile:
            q = q.filter((Project.mentor_id == user.id)
                         | (Project.university_id == user.faculty_profile.university_id))
    elif user.role == Role.STUDENT:
        member_ids = [m.project_id for m in db.query(ProjectMember).filter(
            ProjectMember.user_id == user.id, ProjectMember.status == "active").all()]
        q = q.filter(Project.id.in_(member_ids or [-1]))
    elif user.role == Role.COMPANY:
        if scope != "all":
            q = q.filter(Project.status.in_(["active", "completed"]))
    if status_filter:
        q = q.filter(Project.status == status_filter)

    q = q.order_by(Project.created_at.desc())
    total = q.count()
    rows = q.offset(page.offset).limit(page.page_size).all()
    items = [_project_out(db, p, with_progress=True) for p in rows]
    return paginated(items, total, page.page, page.page_size)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: DbDep, user: OptionalUser = None):
    p = _project_or_404(db, project_id)
    if p.status != "completed":
        if user is None or not can_access_project(db, user, p):
            raise HTTPException(status_code=403, detail="You do not have access to this project.")
    return _project_out(db, p)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, payload: ProjectPatch, db: DbDep,
                   user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    require_project_staff(db, user, p)
    data = payload.model_dump(exclude_unset=True)
    allowed_status = {"planning", "active", "on_hold", "completed", "cancelled"}
    if "status" in data and data["status"] not in allowed_status:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(allowed_status)}")
    if "mentor_id" in data and data["mentor_id"]:
        mentor = db.get(User, data["mentor_id"])
        if not mentor or mentor.role not in {Role.FACULTY, Role.UNIVERSITY_ADMIN, Role.ADMIN}:
            raise HTTPException(status_code=400, detail="Selected mentor is not a faculty member.")
    for k, v in data.items():
        setattr(p, k, v)
    audit.log(db, user, "project.updated", target_type="project", target_id=p.id,
              metadata={"fields": list(data.keys())})
    db.commit()
    db.refresh(p)
    return _project_out(db, p)


@router.get("/{project_id}/overview")
def project_overview(project_id: int, db: DbDep, user: CurrentUser):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    tasks = p.tasks
    miles = p.milestones
    return {
        "project": _project_out(db, p).model_dump(),
        "progress_pct": analytics_service.project_progress(db, p.id),
        "tasks": {
            "total": len(tasks),
            "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
            "overdue": sum(1 for t in tasks if t.is_overdue),
            "by_status": {s: sum(1 for t in tasks if t.status == s) for s in TaskStatus.ALL},
        },
        "milestones": {
            "total": len(miles),
            "approved": sum(1 for m in miles if m.status == MilestoneStatus.APPROVED),
            "overdue": sum(1 for m in miles if m.status == MilestoneStatus.OVERDUE),
        },
        "members": [_member_out(m).model_dump() for m in p.members if m.status == "active"],
        "pending_responsibility_forms": sum(
            1 for m in p.members
            if m.status == "active" and not (m.responsibility_form and m.responsibility_form.accepted)),
        "demo_data": False,
    }


# ---------------------------------------------------------------------------
# Team formation
# ---------------------------------------------------------------------------

@router.get("/{project_id}/match")
def match_candidates(project_id: int, db: DbDep, user: CurrentUser,
                     limit: int = Query(default=12, ge=1, le=40),
                     use_ai: bool = Query(default=True)):
    p = _project_or_404(db, project_id)
    require_project_staff(db, user, p)
    return matching_service.match_students(db, p, limit=limit)


@router.get("/{project_id}/members", response_model=list[MemberOut])
def list_members(project_id: int, db: DbDep, user: CurrentUser):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    return [_member_out(m) for m in p.members]


@router.post("/{project_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def invite_member(project_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep,
                  student_id: int = Query(...), team_role: str = Query(default="member"),
                  match_score: float | None = Query(default=None),
                  match_reason: str | None = Query(default=None)):
    p = _project_or_404(db, project_id)
    require_project_staff(db, user, p)
    student = db.get(User, student_id)
    if not student or student.role != Role.STUDENT or not student.student_profile:
        raise HTTPException(status_code=404, detail="Student not found.")
    existing = db.query(ProjectMember).filter(
        ProjectMember.project_id == p.id, ProjectMember.user_id == student_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="This student is already on the team.")

    m = ProjectMember(
        project_id=p.id, user_id=student_id, team_role=team_role,
        status="invited", match_score=match_score, match_reason=match_reason,
    )
    db.add(m)
    db.flush()
    db.add(ResponsibilityForm(
        project_id=p.id, member_id=m.id, role=team_role,
        responsibilities=f"As {team_role} on '{p.title}', contribute to the agreed deliverables.",
        expectations="Attend agreed sessions, report weekly, follow academic integrity and privacy rules.",
    ))
    notification_service.notify(
        db, student_id,
        f"Invitation to join '{p.title}'",
        "You have been invited to a project team. Review and accept your responsibility form.",
        kind="invitation", link=f"/project.html?id={p.id}",
    )
    audit.log(db, user, "project.member_invited", target_type="project", target_id=p.id,
              metadata={"student_id": student_id})
    db.commit()
    db.refresh(m)
    return _member_out(m)


@router.patch("/{project_id}/members/{member_id}", response_model=MemberOut)
def update_member(project_id: int, member_id: int, payload: MemberPatch, db: DbDep,
                  user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    m = db.get(ProjectMember, member_id)
    if not m or m.project_id != p.id:
        raise HTTPException(status_code=404, detail="Team member not found.")

    data = payload.model_dump(exclude_unset=True)
    # A student may accept/decline their own invitation; staff may approve/manage.
    is_self = m.user_id == user.id
    is_staff = user.role in STAFF or (p.mentor_id == user.id) or (p.created_by_id == user.id)
    if not (is_self or is_staff):
        raise HTTPException(status_code=403, detail="You cannot modify this membership.")
    if is_self and not is_staff:
        if set(data) - {"status"}:
            raise HTTPException(status_code=403, detail="You may only accept or decline your own invitation.")
        if data.get("status") not in {"active", "declined"}:
            raise HTTPException(status_code=400, detail="Choose to accept or decline the invitation.")

    for k, v in data.items():
        setattr(m, k, v)
    if data.get("status") == "active" and is_self:
        # joining requires an accepted responsibility form when one exists
        if m.responsibility_form and not m.responsibility_form.accepted:
            raise HTTPException(
                status_code=409,
                detail="Please accept your responsibility form before joining the team.",
            )
        notification_service.notify(
            db, p.mentor_id or p.created_by_id,
            f"{user.full_name} joined '{p.title}'",
            "A student accepted their team invitation.", kind="info", link=f"/project.html?id={p.id}",
        )
    audit.log(db, user, "project.member_updated", target_type="project", target_id=p.id,
              metadata={"member_id": member_id, "changes": data})
    db.commit()
    db.refresh(m)
    return _member_out(m)


@router.get("/{project_id}/members/{member_id}/responsibility", response_model=ResponsibilityOut)
def get_responsibility(project_id: int, member_id: int, db: DbDep, user: CurrentUser):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    m = db.get(ProjectMember, member_id)
    if not m or m.project_id != p.id or not m.responsibility_form:
        raise HTTPException(status_code=404, detail="Responsibility form not found.")
    return ResponsibilityOut.model_validate(m.responsibility_form)


@router.post("/{project_id}/members/{member_id}/responsibility", response_model=ResponsibilityOut)
def accept_responsibility(project_id: int, member_id: int, payload: ResponsibilityAccept,
                          db: DbDep, user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    m = db.get(ProjectMember, member_id)
    if not m or m.project_id != p.id:
        raise HTTPException(status_code=404, detail="Team member not found.")
    if m.user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the member can accept their own responsibility form.")
    form = m.responsibility_form
    if not form:
        form = ResponsibilityForm(project_id=p.id, member_id=m.id)
        db.add(form)
    form.role = payload.role
    form.responsibilities = payload.responsibilities or form.responsibilities
    form.accepted = True
    form.accepted_at = utcnow()
    form.snapshot = json.dumps({
        "role": form.role,
        "responsibilities": form.responsibilities,
        "expectations": form.expectations,
        "academic_integrity_confirmed": payload.confirm_academic_integrity,
        "data_privacy_confirmed": payload.confirm_data_privacy,
    }, ensure_ascii=False)
    if m.status == "invited":
        m.status = "active"
    notification_service.notify(
        db, p.mentor_id or p.created_by_id,
        f"Responsibility form accepted by {user.full_name}",
        "The member confirmed their role and commitments.", kind="info", link=f"/project.html?id={p.id}",
    )
    audit.log(db, user, "project.responsibility_accepted", target_type="project", target_id=p.id,
              metadata={"member_id": member_id})
    db.commit()
    db.refresh(form)
    return ResponsibilityOut.model_validate(form)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@router.get("/{project_id}/tasks", response_model=list[TaskOut])
def list_tasks(project_id: int, db: DbDep, user: CurrentUser,
               assignee: int | None = Query(default=None),
               task_status: str | None = Query(default=None, alias="status")):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    q = db.query(Task).filter(Task.project_id == p.id)
    if assignee:
        q = q.filter(Task.assignee_id == assignee)
    if task_status:
        q = q.filter(Task.status == task_status)
    rows = q.order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.id.desc()).all()
    return [_task_out(t) for t in rows]


@router.post("/{project_id}/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(project_id: int, payload: TaskIn, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    require_project_staff(db, user, p)
    if payload.assignee_id:
        _assert_active_member(db, p, payload.assignee_id)
    t = Task(
        project_id=p.id, title=payload.title.strip(), description=payload.description,
        assignee_id=payload.assignee_id, priority=payload.priority, status=payload.status,
        start_date=payload.start_date, due_date=payload.due_date,
        evidence_link=payload.evidence_link, created_by_id=user.id,
    )
    db.add(t)
    db.flush()
    if t.assignee_id:
        notification_service.notify(
            db, t.assignee_id, f"New task assigned: {t.title}",
            f"Due {t.due_date:%d %b %Y}." if t.due_date else "No due date set.",
            kind="task", link=f"/project.html?id={p.id}",
        )
    audit.log(db, user, "task.created", target_type="task", target_id=t.id)
    db.commit()
    db.refresh(t)
    return _task_out(t)


@router.patch("/{project_id}/tasks/{task_id}", response_model=TaskOut)
def update_task(project_id: int, task_id: int, payload: TaskPatch, db: DbDep,
                user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    t = db.get(Task, task_id)
    if not t or t.project_id != p.id:
        raise HTTPException(status_code=404, detail="Task not found.")

    staff = user.role in STAFF or p.mentor_id == user.id or p.created_by_id == user.id
    data = payload.model_dump(exclude_unset=True)
    if not staff:
        # a member may progress their own task, but not reassign or review it
        if t.assignee_id != user.id:
            raise HTTPException(status_code=403, detail="You can only update tasks assigned to you.")
        if set(data) - {"status", "evidence_link", "description"}:
            raise HTTPException(status_code=403, detail="Members cannot change assignment, priority or review notes.")
    if "assignee_id" in data and data["assignee_id"]:
        _assert_active_member(db, p, data["assignee_id"])
    for k, v in data.items():
        setattr(t, k, v)
    if data.get("status") == TaskStatus.COMPLETED and staff:
        t.review_comment = data.get("review_comment", t.review_comment)
        if t.assignee_id:
            notification_service.notify(
                db, t.assignee_id, f"Task completed: {t.title}",
                "A mentor marked your task complete.", kind="task", link=f"/project.html?id={p.id}",
            )
    audit.log(db, user, "task.updated", target_type="task", target_id=t.id,
              metadata={"changes": data})
    db.commit()
    db.refresh(t)
    return _task_out(t)


@router.delete("/{project_id}/tasks/{task_id}", response_model=MessageOut)
def delete_task(project_id: int, task_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    require_project_staff(db, user, p)
    t = db.get(Task, task_id)
    if not t or t.project_id != p.id:
        raise HTTPException(status_code=404, detail="Task not found.")
    db.delete(t)
    audit.log(db, user, "task.deleted", target_type="task", target_id=task_id)
    db.commit()
    return MessageOut(message="Task deleted.")


def _assert_active_member(db, p: Project, user_id: int):
    m = db.query(ProjectMember).filter(
        ProjectMember.project_id == p.id, ProjectMember.user_id == user_id,
        ProjectMember.status == "active").first()
    if not m:
        raise HTTPException(status_code=400, detail="Selected assignee is not an active team member.")


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------

@router.get("/{project_id}/milestones", response_model=list[MilestoneOut])
def list_milestones(project_id: int, db: DbDep, user: CurrentUser):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    rows = db.query(Milestone).filter(Milestone.project_id == p.id).order_by(
        Milestone.due_date.is_(None), Milestone.due_date.asc(), Milestone.id.asc()).all()
    return [MilestoneOut.model_validate(m) for m in rows]


@router.post("/{project_id}/milestones", response_model=MilestoneOut, status_code=status.HTTP_201_CREATED)
def create_milestone(project_id: int, payload: MilestoneIn, db: DbDep,
                     user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    require_project_staff(db, user, p)
    m = Milestone(
        project_id=p.id, name=payload.name.strip(), description=payload.description,
        due_date=payload.due_date, expected_deliverables=payload.expected_deliverables,
        responsible_ids=",".join(str(i) for i in (payload.responsible_ids or [])) or None,
        status=MilestoneStatus.PENDING,
    )
    db.add(m)
    db.flush()
    if payload.responsible_ids:
        for uid in payload.responsible_ids:
            notification_service.notify(
                db, uid, f"New milestone assigned: {m.name}",
                f"Due {m.due_date:%d %b %Y}." if m.due_date else "No due date set.",
                kind="deadline", link=f"/project.html?id={p.id}",
            )
    audit.log(db, user, "milestone.created", target_type="milestone", target_id=m.id)
    db.commit()
    db.refresh(m)
    return MilestoneOut.model_validate(m)


@router.patch("/{project_id}/milestones/{milestone_id}", response_model=MilestoneOut)
def update_milestone(project_id: int, milestone_id: int, payload: MilestonePatch, db: DbDep,
                     user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    m = db.get(Milestone, milestone_id)
    if not m or m.project_id != p.id:
        raise HTTPException(status_code=404, detail="Milestone not found.")

    data = payload.model_dump(exclude_unset=True)
    staff = user.role in STAFF or p.mentor_id == user.id or p.created_by_id == user.id
    if not staff:
        allowed = {"status", "evidence_link", "description", "due_date"}
        if set(data) - allowed:
            raise HTTPException(status_code=403, detail="Only the mentor can review or rename milestones.")
        # Students may only move between in-progress and submitted-for-review.
        if data.get("status") not in {None, MilestoneStatus.IN_PROGRESS, MilestoneStatus.SUBMITTED}:
            raise HTTPException(status_code=403, detail="You may only submit a milestone for review.")

    if data.get("status") == MilestoneStatus.SUBMITTED:
        m.submitted_at = utcnow()
        notification_service.notify(
            db, p.mentor_id or p.created_by_id,
            f"Milestone submitted for review: {m.name}",
            "A team has submitted milestone evidence for your review.",
            kind="review", link=f"/project.html?id={p.id}",
        )
    if staff and data.get("status") in {MilestoneStatus.APPROVED, MilestoneStatus.CHANGES_REQUESTED}:
        m.reviewed_at = utcnow()
        m.reviewed_by_id = user.id
    for k, v in data.items():
        setattr(m, k, v)
    audit.log(db, user, "milestone.updated", target_type="milestone", target_id=m.id,
              metadata={"changes": data})
    db.commit()
    db.refresh(m)
    return MilestoneOut.model_validate(m)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@router.get("/{project_id}/evidence", response_model=list[EvidenceOut])
def list_evidence(project_id: int, db: DbDep, user: CurrentUser):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    rows = db.query(ProjectEvidence).filter(ProjectEvidence.project_id == p.id).order_by(
        ProjectEvidence.id.desc()).all()
    out = []
    for e in rows:
        o = EvidenceOut.model_validate(e)
        o.uploader_name = e.uploader.full_name if e.uploader else None
        out.append(o)
    return out


@router.post("/{project_id}/evidence", response_model=EvidenceOut, status_code=status.HTTP_201_CREATED)
def add_evidence(project_id: int, payload: EvidenceIn, db: DbDep,
                 user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    ev = ProjectEvidence(
        project_id=p.id, task_id=payload.task_id, uploader_id=user.id,
        title=payload.title.strip(), url=payload.url, kind=payload.kind,
        description=payload.description,
    )
    db.add(ev)
    audit.log(db, user, "evidence.added", target_type="project", target_id=p.id)
    db.commit()
    db.refresh(ev)
    o = EvidenceOut.model_validate(ev)
    o.uploader_name = user.full_name
    return o


# ---------------------------------------------------------------------------
# Weekly reports
# ---------------------------------------------------------------------------

@router.get("/{project_id}/reports", response_model=list[WeeklyReportOut])
def list_reports(project_id: int, db: DbDep, user: CurrentUser,
                 student_id: int | None = Query(default=None)):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    q = db.query(WeeklyReport).filter(WeeklyReport.project_id == p.id)
    if student_id:
        q = q.filter(WeeklyReport.student_id == student_id)
    rows = q.order_by(WeeklyReport.week_of.desc(), WeeklyReport.id.desc()).all()
    out = []
    for r in rows:
        o = WeeklyReportOut.model_validate(r)
        o.student_name = r.student.full_name if r.student else None
        out.append(o)
    return out


@router.post("/{project_id}/reports", response_model=WeeklyReportOut, status_code=status.HTTP_201_CREATED)
def submit_report(project_id: int, payload: WeeklyReportIn, db: DbDep,
                  user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    membership = db.query(ProjectMember).filter(
        ProjectMember.project_id == p.id, ProjectMember.user_id == user.id,
        ProjectMember.status == "active").first()
    if not membership and user.role not in STAFF:
        raise HTTPException(status_code=403, detail="Only active team members submit weekly reports.")
    week = payload.week_of or dt.date.today()
    r = WeeklyReport(
        project_id=p.id, student_id=user.id, week_of=week,
        completed_work=payload.completed_work, current_progress=payload.current_progress,
        blockers=payload.blockers, next_steps=payload.next_steps,
        help_required=payload.help_required,
    )
    db.add(r)
    if p.mentor_id and payload.blockers:
        notification_service.notify(
            db, p.mentor_id, f"Blocker reported on '{p.title}'",
            payload.blockers, kind="risk", link=f"/project.html?id={p.id}",
        )
    audit.log(db, user, "report.submitted", target_type="project", target_id=p.id)
    db.commit()
    db.refresh(r)
    o = WeeklyReportOut.model_validate(r)
    o.student_name = user.full_name
    return o


# ---------------------------------------------------------------------------
# Mentor reviews
# ---------------------------------------------------------------------------

@router.get("/{project_id}/reviews", response_model=list[MentorReviewOut])
def list_reviews(project_id: int, db: DbDep, user: CurrentUser):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    rows = db.query(MentorReview).filter(MentorReview.project_id == p.id).order_by(
        MentorReview.id.desc()).all()
    out = []
    for r in rows:
        o = MentorReviewOut.model_validate(r)
        o.reviewer_name = r.reviewer.full_name if r.reviewer else None
        out.append(o)
    return out


@router.post("/{project_id}/reviews", response_model=MentorReviewOut, status_code=status.HTTP_201_CREATED)
def add_review(project_id: int, payload: MentorReviewIn, db: DbDep,
               user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    require_project_staff(db, user, p)
    r = MentorReview(
        project_id=p.id, milestone_id=payload.milestone_id, reviewer_id=user.id,
        verdict=payload.verdict, rating=payload.rating, comments=payload.comments,
        risks=payload.risks,
    )
    db.add(r)
    if payload.verdict == "changes_requested":
        notification_service.notify_team(
            db, p, f"Changes requested by mentor on '{p.title}'",
            payload.comments, kind="review",
        )
    audit.log(db, user, "project.reviewed", target_type="project", target_id=p.id,
              metadata={"verdict": payload.verdict})
    db.commit()
    db.refresh(r)
    o = MentorReviewOut.model_validate(r)
    o.reviewer_name = user.full_name
    return o


# ---------------------------------------------------------------------------
# Impact report
# ---------------------------------------------------------------------------

@router.get("/{project_id}/impact", response_model=ImpactReportOut | None)
def get_impact(project_id: int, db: DbDep, user: OptionalUser = None):
    p = _project_or_404(db, project_id)
    if p.status != "completed":
        if user is None or not can_access_project(db, user, p):
            raise HTTPException(status_code=403, detail="You do not have access to this project.")
    r = db.query(ImpactReport).filter(ImpactReport.project_id == p.id).order_by(
        ImpactReport.id.desc()).first()
    if not r:
        raise HTTPException(status_code=404, detail="No impact report recorded yet.")
    return ImpactReportOut.model_validate(r)


@router.post("/{project_id}/impact", response_model=ImpactReportOut)
def upsert_impact(project_id: int, payload: ImpactReportIn, db: DbDep,
                  user: CurrentUser, csrf: CsrfDep):
    p = _project_or_404(db, project_id)
    require_project_access(db, user, p)
    if payload.verification_status not in VerificationStatus.ALL:
        raise HTTPException(status_code=400, detail="Unsupported verification status.")
    if payload.verification_status == VerificationStatus.VERIFIED and user.role not in STAFF:
        raise HTTPException(status_code=403,
                            detail="Only university staff can mark impact as verified.")
    r = db.query(ImpactReport).filter(ImpactReport.project_id == p.id).order_by(
        ImpactReport.id.desc()).first()
    if not r:
        r = ImpactReport(project_id=p.id, author_id=user.id)
        db.add(r)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    audit.log(db, user, "impact.updated", target_type="project", target_id=p.id,
              metadata={"verification_status": r.verification_status})
    db.commit()
    db.refresh(r)
    return ImpactReportOut.model_validate(r)
