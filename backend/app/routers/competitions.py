"""Competitions: registration, submissions, judge scoring, leaderboards.

Two rankings are always kept distinct:
- innovation ranking = authorised human judge scores against published criteria
- progress ranking = approved milestone/task completion in the project

They are never merged into one number, because they measure different things.
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, HTTPException, Query, status

from ..core import audit
from ..core.permissions import require_reviewer
from ..dependencies import CurrentUser, CsrfDep, DbDep, OptionalUser, PageDep, paginated
from ..models import (
    Competition,
    CompetitionRegistration,
    DEFAULT_CRITERIA,
    JudgeScore,
    Project,
    ProjectMember,
    Role,
    Submission,
    User,
)
from ..schemas import (
    CompetitionIn,
    CompetitionOut,
    LeaderboardRow,
    MessageOut,
    ScoreIn,
    ScoreOut,
    SubmissionIn,
    SubmissionOut,
)
from ..services import analytics_service

router = APIRouter(prefix="/api/competitions", tags=["competitions"])

STAFF = {Role.ADMIN, Role.FACULTY, Role.UNIVERSITY_ADMIN}


def _criteria_json(payload) -> str:
    if not payload:
        return json.dumps(DEFAULT_CRITERIA)
    total = sum(c.weight for c in payload)
    if total <= 0:
        raise HTTPException(status_code=400, detail="Evaluation criteria weights must be positive.")
    return json.dumps([c.model_dump() for c in payload])


def _competition_out(db, c: Competition) -> CompetitionOut:
    out = CompetitionOut.model_validate(c)
    out.registered_projects = db.query(CompetitionRegistration).filter(
        CompetitionRegistration.competition_id == c.id).count()
    out.submission_count = db.query(Submission).filter(
        Submission.competition_id == c.id).count()
    return out


def _submission_out(db, s: Submission) -> SubmissionOut:
    out = SubmissionOut.model_validate(s)
    project = db.get(Project, s.project_id)
    if project:
        out.project_title = project.title
        out.university_name = project.university.name if project.university else None
        members = [m.user.full_name for m in project.members if m.status == "active" and m.user]
        out.team_name = " / ".join(members[:3]) or project.title
        out.progress_score = analytics_service.project_progress(db, project.id)
    scores = s.scores
    if scores:
        out.judge_count = len({sc.judge_id for sc in scores})
        breakdown: dict[str, float] = {}
        for crit in {sc.criterion for sc in scores}:
            vals = [sc.score for sc in scores if sc.criterion == crit]
            breakdown[crit] = round(sum(vals) / len(vals), 2)
        out.score_breakdown = breakdown
        crit = json.loads(db.get(Competition, s.competition_id).criteria or "[]")
        weights = {c["key"]: float(c.get("weight", 0)) for c in crit}
        wtotal = sum(weights.values()) or 100.0
        out.total_score = round(sum(breakdown.get(k, 0.0) * w for k, w in weights.items()) / wtotal * 10, 2)
    return out


def _can_manage_project(db, user: User, project: Project) -> bool:
    if user.role in STAFF:
        return True
    if project.mentor_id == user.id or project.created_by_id == user.id:
        return True
    return db.query(ProjectMember).filter(
        ProjectMember.project_id == project.id, ProjectMember.user_id == user.id,
        ProjectMember.status == "active").first() is not None


# ---------------------------------------------------------------------------
# Competitions
# ---------------------------------------------------------------------------

@router.get("", response_model=dict)
def list_competitions(db: DbDep, page: PageDep, user: OptionalUser = None,
                      scope: str | None = Query(default=None),
                      status_filter: str | None = Query(default=None, alias="status")):
    q = db.query(Competition)
    if scope:
        q = q.filter(Competition.scope == scope)
    if status_filter:
        q = q.filter(Competition.status == status_filter)
    q = q.order_by(Competition.submission_deadline.is_(None), Competition.submission_deadline.desc())
    total = q.count()
    rows = q.offset(page.offset).limit(page.page_size).all()
    return paginated([_competition_out(db, c) for c in rows], total, page.page, page.page_size)


@router.get("/{competition_id}", response_model=CompetitionOut)
def get_competition(competition_id: int, db: DbDep, user: OptionalUser = None):
    c = db.get(Competition, competition_id)
    if not c:
        raise HTTPException(status_code=404, detail="Competition not found.")
    return _competition_out(db, c)


@router.post("", response_model=CompetitionOut, status_code=status.HTTP_201_CREATED)
def create_competition(payload: CompetitionIn, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    if user.role not in STAFF:
        raise HTTPException(status_code=403, detail="Only university staff can create competitions.")
    criteria = _criteria_json(payload.criteria)
    today = dt.date.today()
    c = Competition(
        title=payload.title.strip(), description=payload.description, scope=payload.scope,
        university_id=payload.university_id if payload.scope == "within_university" else None,
        eligibility_rules=payload.eligibility_rules,
        registration_deadline=payload.registration_deadline,
        submission_deadline=payload.submission_deadline,
        starts_on=payload.starts_on, ends_on=payload.ends_on,
        criteria=criteria,
        status="active" if (payload.starts_on and payload.starts_on <= today) or payload.starts_on is None else "upcoming",
        created_by_id=user.id,
    )
    db.add(c)
    audit.log(db, user, "competition.created", target_type="competition", metadata={"title": c.title})
    db.commit()
    db.refresh(c)
    return _competition_out(db, c)


@router.patch("/{competition_id}", response_model=CompetitionOut)
def update_competition(competition_id: int, payload: CompetitionIn, db: DbDep,
                       user: CurrentUser, csrf: CsrfDep):
    if user.role not in STAFF:
        raise HTTPException(status_code=403, detail="Only university staff can edit competitions.")
    c = db.get(Competition, competition_id)
    if not c:
        raise HTTPException(status_code=404, detail="Competition not found.")
    data = payload.model_dump(exclude_unset=True)
    if "criteria" in data:
        c.criteria = _criteria_json(payload.criteria)
        data.pop("criteria")
    for k, v in data.items():
        setattr(c, k, v)
    audit.log(db, user, "competition.updated", target_type="competition", target_id=c.id)
    db.commit()
    db.refresh(c)
    return _competition_out(db, c)


@router.post("/{competition_id}/publish", response_model=CompetitionOut)
def publish_results(competition_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    if user.role not in STAFF:
        raise HTTPException(status_code=403, detail="Only university staff can publish results.")
    c = db.get(Competition, competition_id)
    if not c:
        raise HTTPException(status_code=404, detail="Competition not found.")
    c.results_published = True
    if c.ends_on and c.ends_on < dt.date.today():
        c.status = "completed"
    audit.log(db, user, "competition.results_published", target_type="competition", target_id=c.id)
    db.commit()
    db.refresh(c)
    return _competition_out(db, c)


# ---------------------------------------------------------------------------
# Registration & submission
# ---------------------------------------------------------------------------

@router.post("/{competition_id}/register", response_model=MessageOut,
             status_code=status.HTTP_201_CREATED)
def register_project(competition_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep,
                     project_id: int = Query(...)):
    c = db.get(Competition, competition_id)
    if not c:
        raise HTTPException(status_code=404, detail="Competition not found.")
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    if not _can_manage_project(db, user, p):
        raise HTTPException(status_code=403, detail="You do not manage this project.")
    if c.registration_deadline and c.registration_deadline < dt.date.today():
        raise HTTPException(status_code=409, detail="The registration deadline has passed.")
    if c.scope == "within_university" and c.university_id and p.university_id != c.university_id:
        raise HTTPException(status_code=409, detail="This competition is open only to its host university.")
    exists = db.query(CompetitionRegistration).filter(
        CompetitionRegistration.competition_id == c.id,
        CompetitionRegistration.project_id == p.id).first()
    if exists:
        raise HTTPException(status_code=409, detail="This project is already registered.")
    db.add(CompetitionRegistration(
        competition_id=c.id, project_id=p.id, registered_by_id=user.id, status="registered"))
    audit.log(db, user, "competition.registered", target_type="competition", target_id=c.id,
              metadata={"project_id": p.id})
    db.commit()
    return MessageOut(message="Project registered for the competition.")


@router.get("/{competition_id}/registrations")
def list_registrations(competition_id: int, db: DbDep, user: CurrentUser):
    c = db.get(Competition, competition_id)
    if not c:
        raise HTTPException(status_code=404, detail="Competition not found.")
    rows = db.query(CompetitionRegistration).filter(
        CompetitionRegistration.competition_id == c.id).all()
    out = []
    for r in rows:
        p = db.get(Project, r.project_id)
        out.append({
            "id": r.id, "project_id": r.project_id,
            "project_title": p.title if p else None,
            "university": p.university.name if p and p.university else None,
            "status": r.status, "created_at": r.created_at,
        })
    return {"competition_id": c.id, "items": out, "total": len(out)}


@router.post("/{competition_id}/submissions", response_model=SubmissionOut,
             status_code=status.HTTP_201_CREATED)
def create_submission(competition_id: int, payload: SubmissionIn, db: DbDep,
                      user: CurrentUser, csrf: CsrfDep):
    c = db.get(Competition, competition_id)
    if not c:
        raise HTTPException(status_code=404, detail="Competition not found.")
    p = db.get(Project, payload.project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    if not _can_manage_project(db, user, p):
        raise HTTPException(status_code=403, detail="You do not manage this project.")
    if c.submission_deadline and c.submission_deadline < dt.date.today():
        raise HTTPException(status_code=409, detail="The submission deadline has passed.")
    registered = db.query(CompetitionRegistration).filter(
        CompetitionRegistration.competition_id == c.id,
        CompetitionRegistration.project_id == p.id).first()
    if not registered:
        raise HTTPException(status_code=409, detail="Register the project before submitting.")
    s = Submission(
        competition_id=c.id, project_id=p.id, submitted_by_id=user.id,
        title=payload.title.strip(), summary=payload.summary, demo_link=payload.demo_link,
        documentation_link=payload.documentation_link, repository_link=payload.repository_link,
    )
    db.add(s)
    audit.log(db, user, "competition.submitted", target_type="competition", target_id=c.id,
              metadata={"project_id": p.id})
    db.commit()
    db.refresh(s)
    return _submission_out(db, s)


@router.get("/{competition_id}/submissions", response_model=list[SubmissionOut])
def list_submissions(competition_id: int, db: DbDep, user: OptionalUser = None):
    c = db.get(Competition, competition_id)
    if not c:
        raise HTTPException(status_code=404, detail="Competition not found.")
    rows = db.query(Submission).filter(Submission.competition_id == c.id).order_by(
        Submission.created_at.asc()).all()
    # Teams always see their own submissions; scores are only shown once published to non-staff.
    out = []
    for s in rows:
        o = _submission_out(db, s)
        if not c.results_published and not (user and user.role in STAFF):
            o.total_score = None
            o.score_breakdown = None
        out.append(o)
    return out


@router.post("/{competition_id}/submissions/{submission_id}/scores", response_model=ScoreOut)
def score_submission(competition_id: int, submission_id: int, payload: ScoreIn, db: DbDep,
                     user: CurrentUser, csrf: CsrfDep):
    require_reviewer(user)
    c = db.get(Competition, competition_id)
    s = db.get(Submission, submission_id)
    if not c or not s or s.competition_id != c.id:
        raise HTTPException(status_code=404, detail="Submission not found.")
    criteria = json.loads(c.criteria or "[]")
    keys = {x["key"] for x in criteria}
    if payload.criterion not in keys:
        raise HTTPException(status_code=400, detail=f"criterion must be one of {sorted(keys)}")
    score = db.query(JudgeScore).filter(
        JudgeScore.submission_id == s.id, JudgeScore.judge_id == user.id,
        JudgeScore.criterion == payload.criterion).first()
    if not score:
        score = JudgeScore(submission_id=s.id, judge_id=user.id, criterion=payload.criterion)
        db.add(score)
    score.score = payload.score
    score.feedback = payload.feedback
    audit.log(db, user, "competition.scored", target_type="submission", target_id=s.id,
              metadata={"criterion": payload.criterion, "score": payload.score})
    db.commit()
    db.refresh(score)
    out = ScoreOut.model_validate(score)
    out.judge_name = user.full_name
    return out


@router.get("/{competition_id}/leaderboard", response_model=list[LeaderboardRow])
def leaderboard(competition_id: int, db: DbDep, user: OptionalUser = None,
                kind: str = Query(default="innovation")):
    c = db.get(Competition, competition_id)
    if not c:
        raise HTTPException(status_code=404, detail="Competition not found.")
    data = analytics_service.competition_leaderboard(db, c)
    if kind == "progress":
        rows = data["progress_leaderboard"]
        key = "progress_rank"
    else:
        rows = data["innovation_leaderboard"]
        key = "innovation_rank"
    out = []
    for r in rows:
        out.append(LeaderboardRow(
            rank=r.get(key, r.get("rank", 0)),
            submission_id=r.get("submission_id"),
            project_id=r["project_id"],
            project_title=r["project_title"],
            team_name=r["team_name"],
            university=r.get("university"),
            innovation_score=r.get("innovation_score"),
            progress_score=r.get("progress_score"),
            combined=None,
            scored_by=r.get("scored_by", 0),
            is_demo=False,
        ))
    return out
