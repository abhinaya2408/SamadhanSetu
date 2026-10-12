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
    require_roles,
)
from ..dependencies import CurrentUser, CsrfDep, DbDep, OptionalUser, PageDep, paginated
from ..models import (
    ApplicationStatus,
    Challenge,
    ChallengeStatus,
    ImpactReport,
    MentorReview,
    Milestone,
    MilestoneStatus,
    Project,
    ProjectApplication,
    ProjectEvidence,
    ProjectMember,
    ProjectStatus,
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
    ApplicationDecision,
    ApplicationIn,
    ApplicationOut,
    EvidenceIn,
    EvidenceOut,
    ImpactReportIn,
    ImpactReportOut,
    MemberOut,
    MemberPatch,
    MentorReviewIn,
    MentorReviewOut,
    MemberRespond,
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

STAFF = {Role.ADMIN, *Role.UNIVERSITY_ROLES}


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
        if not mentor or mentor.role not in {Role.ADMIN, *Role.UNIVERSITY_ROLES}:
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


# ---------------------------------------------------------------------------
# Recruitment: projects a student can join, and the application workflow
# ---------------------------------------------------------------------------

def _open_positions(db, p: Project) -> int:
    """Places left, counting both confirmed members and outstanding invitations.

    Checked on the backend so a full team cannot be over-filled by a stale page.
    """
    taken = sum(1 for m in p.members if m.status in {"active", "invited"})
    return max(0, (p.expected_team_size or 4) - taken)


def _team_full(p: Project, excluding_member_id: int | None = None) -> bool:
    """True when there is no room left, ignoring the member being activated.

    ``_open_positions`` already counts a pending invitation, so activating that same
    member must not look like an overflow.
    """
    taken = sum(1 for m in p.members
                if m.status in {"active", "invited"} and m.id != excluding_member_id)
    return taken >= (p.expected_team_size or 4)


def _maybe_activate_project(p: Project) -> None:
    """A project opened for recruitment becomes live once a member has accepted.

    The activation rule is deliberately simple and explicit: at least one accepted
    team member. Nothing activates before a student actually accepts.
    """
    active = sum(1 for m in p.members if m.status == "active")
    if p.status == ProjectStatus.RECRUITING and active >= 1:
        p.status = ProjectStatus.ACTIVE


def _project_staff_ids(db, p: Project) -> list[int]:
    """The people who should hear about activity on a project."""
    ids = set()
    if p.mentor_id:
        ids.add(p.mentor_id)
    if p.created_by_id:
        ids.add(p.created_by_id)
    return [i for i in ids if i]


def _application_out(db, a: ProjectApplication) -> ApplicationOut:
    out = ApplicationOut.model_validate(a)
    if a.student:
        out.student_name = a.student.full_name
        prof = a.student.student_profile
        if prof:
            out.student_department = prof.department.name if prof.department else None
            out.student_skills = prof.skills
            out.student_experience = prof.experience_level
    if a.project:
        out.project_title = a.project.title
    if a.decided_by:
        out.decided_by_name = a.decided_by.full_name
    member = db.query(ProjectMember).filter(
        ProjectMember.project_id == a.project_id, ProjectMember.user_id == a.student_id).first()
    out.member_id = member.id if member else None
    return out


@router.get("/open")
def open_projects(db: DbDep, user: CurrentUser, limit: int = Query(default=24, ge=1, le=60)):
    """Projects currently open for recruitment, with a per-student match explanation.

    This is the discovery surface: once a project is set to ``recruiting`` it appears
    here, and each card explains how the signed-in student's skills line up with what
    the project needs. The match is a transparent signal, never a decision.
    """
    require_roles(user, Role.STUDENT)
    rows = (db.query(Project)
            .filter(Project.status == ProjectStatus.RECRUITING)
            .order_by(Project.created_at.desc())
            .limit(limit).all())

    items = []
    profile = user.student_profile
    for p in rows:
        if profile:
            match = matching_service.explain_student_match(p, profile, user)
        else:
            match = {"score": None,
                     "reason": "Add your skills to your profile to see a match.",
                     "matched": [], "unmatched": []}
        app = db.query(ProjectApplication).filter(
            ProjectApplication.project_id == p.id,
            ProjectApplication.student_id == user.id).first()
        items.append({
            "id": p.id,
            "title": p.title,
            "problem_statement": p.problem_statement,
            "objectives": p.objectives,
            "deliverables": p.deliverables,
            "required_skills": p.required_skills,
            "difficulty": p.difficulty,
            "expected_team_size": p.expected_team_size,
            "deadline": p.deadline.isoformat() if p.deadline else None,
            "university_name": p.university.name if p.university else None,
            "mentor_name": p.mentor.full_name if p.mentor else None,
            "open_positions": _open_positions(db, p),
            "match_score": match["score"],
            "match_reason": match["reason"],
            "matched_skills": match["matched"],
            "missing_skills": match["unmatched"],
            "my_application_status": app.status if app else None,
        })
    return {
        "items": items,
        "total": len(items),
        "note": ("Only projects a university has opened for recruitment appear here. "
                 "The match explanation is a transparent search signal - the mentor decides."),
    }


@router.get("/my-applications")
def my_applications(db: DbDep, user: CurrentUser):
    """A student's own applications and outstanding invitations."""
    require_roles(user, Role.STUDENT)
    apps = (db.query(ProjectApplication)
            .filter(ProjectApplication.student_id == user.id)
            .order_by(ProjectApplication.created_at.desc()).all())
    invites = (db.query(ProjectMember)
               .filter(ProjectMember.user_id == user.id, ProjectMember.status == "invited").all())
    invitation_items = []
    for m in invites:
        invitation_items.append({
            "member_id": m.id,
            "project_id": m.project_id,
            "project_title": m.project.title if m.project else None,
            "team_role": m.team_role,
            "match_score": m.match_score,
            "match_reason": m.match_reason,
            "deadline": m.project.deadline.isoformat() if m.project and m.project.deadline else None,
            "mentor_name": m.project.mentor.full_name if m.project and m.project.mentor else None,
        })
    return {
        "applications": [_application_out(db, a).model_dump() for a in apps],
        "invitations": invitation_items,
    }


@router.post("/{project_id}/apply", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def apply_to_project(project_id: int, payload: ApplicationIn, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    """A student expresses interest in an open project. Duplicates are impossible."""
    require_roles(user, Role.STUDENT)
    p = _project_or_404(db, project_id)
    if p.status != ProjectStatus.RECRUITING:
        raise HTTPException(status_code=409, detail="This project is not open for applications.")
    if not user.student_profile:
        raise HTTPException(status_code=400, detail="Complete your student profile before applying.")
    if db.query(ProjectMember).filter(
            ProjectMember.project_id == p.id, ProjectMember.user_id == user.id).first():
        raise HTTPException(status_code=409, detail="You are already on this team.")
    if db.query(ProjectApplication).filter(
            ProjectApplication.project_id == p.id, ProjectApplication.student_id == user.id).first():
        raise HTTPException(status_code=409, detail="You have already applied to this project.")
    if _open_positions(db, p) <= 0:
        raise HTTPException(status_code=409, detail="This team is already full.")

    match = matching_service.explain_student_match(p, user.student_profile, user)
    app = ProjectApplication(
        project_id=p.id, student_id=user.id, message=payload.message,
        status=ApplicationStatus.APPLIED,
        match_score=match["score"], match_reason=match["reason"],
    )
    db.add(app)
    db.flush()
    for staff_id in _project_staff_ids(db, p):
        notification_service.notify(
            db, staff_id, f"New application for '{p.title}'",
            f"{user.full_name} applied to join. {match['reason']}",
            kind="review", link=f"/project.html?id={p.id}&tab=applications")
    audit.log(db, user, "application.submitted", target_type="project", target_id=p.id,
              metadata={"application_id": app.id})
    db.commit()
    db.refresh(app)
    return _application_out(db, app)


@router.get("/{project_id}/applications", response_model=list[ApplicationOut])
def list_applications(project_id: int, db: DbDep, user: CurrentUser,
                      app_status: str | None = Query(default=None, alias="status")):
    """Authorised staff see every application for a project, with match explanations."""
    p = _project_or_404(db, project_id)
    require_project_staff(db, user, p)
    q = db.query(ProjectApplication).filter(ProjectApplication.project_id == p.id)
    if app_status:
        q = q.filter(ProjectApplication.status == app_status)
    rows = q.order_by(ProjectApplication.match_score.desc().nullslast(),
                      ProjectApplication.created_at.desc()).all()
    return [_application_out(db, a) for a in rows]


@router.patch("/{project_id}/applications/{application_id}", response_model=ApplicationOut)
def decide_application(project_id: int, application_id: int, payload: ApplicationDecision,
                       db: DbDep, user: CurrentUser, csrf: CsrfDep):
    """Staff decision on an application: review, invite, accept or decline.

    Inviting or accepting creates the team record (and its responsibility form).
    Capacity is checked here, transactionally, so a team can never be over-filled.
    """
    p = _project_or_404(db, project_id)
    require_project_staff(db, user, p)
    a = db.get(ProjectApplication, application_id)
    if not a or a.project_id != p.id:
        raise HTTPException(status_code=404, detail="Application not found.")
    if a.status in {ApplicationStatus.WITHDRAWN}:
        raise HTTPException(status_code=409, detail="This application was withdrawn.")

    new_status = payload.status
    member = db.query(ProjectMember).filter(
        ProjectMember.project_id == p.id, ProjectMember.user_id == a.student_id).first()

    if new_status in {ApplicationStatus.INVITED, ApplicationStatus.ACCEPTED}:
        if not (member and member.status in {"active", "invited"}):
            if _open_positions(db, p) <= 0:
                raise HTTPException(status_code=409, detail="This team is already full.")
            team_role = (payload.team_role or "member").strip() or "member"
            if not member:
                member = ProjectMember(
                    project_id=p.id, user_id=a.student_id, team_role=team_role,
                    status="invited", match_score=a.match_score, match_reason=a.match_reason,
                )
                db.add(member)
                db.flush()
            else:
                member.team_role = team_role
                member.status = "invited"
            if not member.responsibility_form:
                db.add(ResponsibilityForm(
                    project_id=p.id, member_id=member.id, role=team_role,
                    responsibilities=f"As {team_role} on '{p.title}', contribute to the agreed deliverables.",
                    expectations=("Attend agreed sessions, report weekly, follow academic integrity "
                                  "and privacy rules."),
                ))
        notification_service.notify(
            db, a.student_id,
            (f"Invitation to join '{p.title}'" if new_status == ApplicationStatus.INVITED
             else f"You have been accepted into '{p.title}'"),
            "Open the project to accept your responsibility form and join the team.",
            kind="invitation", link=f"/project.html?id={p.id}")
    elif new_status == ApplicationStatus.DECLINED:
        notification_service.notify(
            db, a.student_id, f"Application update for '{p.title}'",
            payload.decision_note or "Your application was not taken forward this time.",
            kind="info", link=f"/project.html?id={p.id}")

    a.status = new_status
    a.decision_note = payload.decision_note
    a.decided_by_id = user.id
    audit.log(db, user, "application.decided", target_type="project", target_id=p.id,
              metadata={"application_id": a.id, "status": new_status})
    db.commit()
    db.refresh(a)
    return _application_out(db, a)


@router.post("/{project_id}/members/{member_id}/respond", response_model=MemberOut)
def respond_to_invitation(project_id: int, member_id: int, payload: MemberRespond,
                          db: DbDep, user: CurrentUser, csrf: CsrfDep):
    """A student accepts or declines their own team invitation.

    Accepting requires an accepted responsibility form, and joins the team only when
    there is room - the membership is never marked active before the student says yes.
    """
    p = _project_or_404(db, project_id)
    m = db.get(ProjectMember, member_id)
    if not m or m.project_id != p.id:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    if m.user_id != user.id:
        raise HTTPException(status_code=403, detail="This invitation is not yours.")

    if payload.accept and m.status == "active":
        # Accepting the responsibility form already joins the team; answering again
        # is a harmless no-op rather than an error.
        _maybe_activate_project(p)
        db.commit()
        return _member_out(m)
    if not payload.accept and m.status == "active":
        raise HTTPException(status_code=409,
                            detail="You are already an active member of this team.")
    if m.status != "invited":
        raise HTTPException(status_code=409, detail="This invitation has already been answered.")

    if payload.accept:
        if _team_full(p, m.id):
            raise HTTPException(status_code=409, detail="This team is already full.")
        if m.responsibility_form and not m.responsibility_form.accepted:
            raise HTTPException(
                status_code=409,
                detail="Accept your responsibility form before joining the team.")
        m.status = "active"
        _maybe_activate_project(p)
        notification_service.notify(
            db, p.mentor_id or p.created_by_id, f"{user.full_name} accepted their invitation",
            f"They have joined '{p.title}'.", kind="info", link=f"/project.html?id={p.id}")
    else:
        m.status = "declined"
        notification_service.notify(
            db, p.mentor_id or p.created_by_id, f"{user.full_name} declined their invitation",
            payload.note or "They declined to join this team.", kind="info",
            link=f"/project.html?id={p.id}")

    audit.log(db, user, "project.invitation_answered", target_type="project", target_id=p.id,
              metadata={"member_id": member_id, "accept": payload.accept})
    db.commit()
    db.refresh(m)
    return _member_out(m)


@router.post("/{project_id}/recruit", response_model=ProjectOut)
def set_recruitment(project_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep,
                    open: bool = Query(default=True)):
    """Open or close a project for student applications (authorised staff only)."""
    p = _project_or_404(db, project_id)
    require_project_staff(db, user, p)
    p.status = ProjectStatus.RECRUITING if open else ProjectStatus.PLANNING
    audit.log(db, user, "project.recruitment_changed", target_type="project", target_id=p.id,
              metadata={"open": open})
    db.commit()
    db.refresh(p)
    return _project_out(db, p)


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
    allowed_status = ProjectStatus.ALL
    if "status" in data and data["status"] not in allowed_status:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(allowed_status)}")
    if "mentor_id" in data and data["mentor_id"]:
        mentor = db.get(User, data["mentor_id"])
        if not mentor or mentor.role not in {Role.ADMIN, *Role.UNIVERSITY_ROLES}:
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
    if _team_full(p):
        raise HTTPException(status_code=409, detail="This team is already full.")

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
        # Joining the team is a capacity-checked transition, never a free upgrade.
        if _team_full(p, m.id):
            raise HTTPException(status_code=409, detail="This team is already full.")
        m.status = "active"
    _maybe_activate_project(p)
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
