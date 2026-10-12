"""Dashboard analytics — every number is computed from real database records.

Where a chart is fed by seeded demonstration data, the calling endpoint tags it
so the UI can show the "Demo data" label.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    Challenge,
    ChallengeScreening,
    Competition,
    Company,
    ImpactReport,
    Milestone,
    MilestoneStatus,
    Notification,
    Project,
    ProjectMember,
    Sponsorship,
    Submission,
    Task,
    TaskStatus,
    University,
    User,
)


def _pct(done: int, total: int) -> float:
    return round((done / total) * 100, 1) if total else 0.0


def project_progress(db: Session, project_id: int) -> float:
    tasks = db.query(Task).filter(Task.project_id == project_id).all()
    task_pct = _pct(sum(1 for t in tasks if t.status == TaskStatus.COMPLETED), len(tasks))
    miles = db.query(Milestone).filter(Milestone.project_id == project_id).all()
    ms_pct = _pct(sum(1 for m in miles if m.status == MilestoneStatus.APPROVED), len(miles))
    if tasks and miles:
        return round(task_pct * 0.5 + ms_pct * 0.5, 1)
    return round(task_pct or ms_pct, 1)


def citizen_summary(db: Session, user: User) -> dict:
    challenges = db.query(Challenge).filter(Challenge.citizen_id == user.id).all()
    statuses: dict[str, int] = {}
    for c in challenges:
        statuses[c.status] = statuses.get(c.status, 0) + 1
    accepted = sum(1 for c in challenges if c.status in {"accepted", "in_project", "completed"})
    return {
        "challenges_submitted": len(challenges),
        "under_review": sum(statuses.get(s, 0) for s in ("pending_review", "needs_clarification")),
        "approved": statuses.get("approved", 0),
        "excluded": statuses.get("excluded_government", 0),
        "declined": statuses.get("declined", 0),
        "accepted": accepted,
        "projects_public_updates": sum(1 for c in challenges if c.project is not None),
        "by_status": statuses,
        "demo_data": False,
    }


def student_summary(db: Session, user: User) -> dict:
    memberships = db.query(ProjectMember).filter(
        ProjectMember.user_id == user.id, ProjectMember.status == "active").all()
    project_ids = [m.project_id for m in memberships]
    tasks = db.query(Task).filter(Task.assignee_id == user.id).all() if project_ids else []
    today = dt.date.today()
    return {
        "active_projects": len(project_ids),
        "tasks_assigned": len(tasks),
        "tasks_completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
        "tasks_overdue": sum(1 for t in tasks
                             if t.status != TaskStatus.COMPLETED and t.due_date and t.due_date < today),
        "milestones_completed": db.query(func.count(Milestone.id)).filter(
            Milestone.project_id.in_(project_ids) if project_ids else False,
            Milestone.status == MilestoneStatus.APPROVED).scalar() or 0,
        "responsibility_forms_pending": sum(
            1 for m in memberships
            if not m.responsibility_form or not m.responsibility_form.accepted),
        "unread_notifications": db.query(func.count(Notification.id)).filter(
            Notification.user_id == user.id, Notification.is_read == False).scalar() or 0,  # noqa: E712
        "demo_data": False,
    }


def mentor_summary(db: Session, user: User) -> dict:
    projects = db.query(Project).filter(Project.mentor_id == user.id).all()
    ids = [p.id for p in projects]
    pending_ms = db.query(func.count(Milestone.id)).filter(
        Milestone.project_id.in_(ids) if ids else False,
        Milestone.status == MilestoneStatus.SUBMITTED).scalar() or 0
    overdue = db.query(func.count(Milestone.id)).filter(
        Milestone.project_id.in_(ids) if ids else False,
        Milestone.status == MilestoneStatus.OVERDUE).scalar() or 0
    return {
        "projects_mentored": len(projects),
        "active_projects": sum(1 for p in projects if p.status in {"planning", "active"}),
        "milestones_pending_review": pending_ms,
        "milestones_overdue": overdue,
        "teams": db.query(func.count(func.distinct(ProjectMember.project_id))).filter(
            ProjectMember.project_id.in_(ids) if ids else False,
            ProjectMember.status == "active").scalar() or 0,
        "demo_data": False,
    }


def university_summary(db: Session, user: User) -> dict:
    uni_id = user.faculty_profile.university_id if user.faculty_profile else None
    q = db.query(Project)
    if uni_id:
        q = q.filter(Project.university_id == uni_id)
    projects = q.all()
    ids = [p.id for p in projects]
    eligible = db.query(func.count(Challenge.id)).filter(
        Challenge.status == "approved").scalar() or 0
    milestones = db.query(Milestone).filter(
        Milestone.project_id.in_(ids) if ids else False).all() if ids else []
    return {
        "eligible_challenges": eligible,
        "projects_total": len(projects),
        "projects_active": sum(1 for p in projects if p.status == "active"),
        "projects_completed": sum(1 for p in projects if p.status == "completed"),
        "teams_formed": db.query(func.count(func.distinct(ProjectMember.project_id))).filter(
            ProjectMember.project_id.in_(ids) if ids else False,
            ProjectMember.status == "active").scalar() or 0,
        "milestones_pending_review": sum(1 for m in milestones if m.status == MilestoneStatus.SUBMITTED),
        "completion_rate_pct": _pct(
            sum(1 for p in projects if p.status == "completed"), len(projects)),
        "demo_data": False,
    }


def company_summary(db: Session, user: User) -> dict:
    company = user.company
    offers = db.query(Sponsorship).filter(Sponsorship.company_id == company.id).all() if company else []
    return {
        "projects_discovered": db.query(func.count(Project.id)).filter(
            Project.status.in_({"active", "completed"})).scalar() or 0,
        "offers_sent": len(offers),
        "accepted_commitments": sum(1 for s in offers if s.status in {"accepted", "committed", "disbursed"}),
        "recorded_disbursements": sum(1 for s in offers if s.status == "disbursed"),
        "declined": sum(1 for s in offers if s.status == "declined"),
        "demo_data": False,
    } | _invitation_counts(db, user)


def _invitation_counts(db: Session, user: User) -> dict:
    from ..models import TalentInvitation
    company = user.company
    if not company:
        return {"invitations_sent": 0, "invitations_accepted": 0}
    total = db.query(func.count(TalentInvitation.id)).filter(
        TalentInvitation.company_id == company.id).scalar() or 0
    accepted = db.query(func.count(TalentInvitation.id)).filter(
        TalentInvitation.company_id == company.id,
        TalentInvitation.status == "accepted").scalar() or 0
    return {"invitations_sent": total, "invitations_accepted": accepted}


def admin_summary(db: Session, user: User) -> dict:
    by_cat_rows = db.query(Challenge.category, func.count(Challenge.id)).group_by(
        Challenge.category).all()
    by_status = db.query(Challenge.status, func.count(Challenge.id)).group_by(
        Challenge.status).all()
    return {
        "users_total": db.query(func.count(User.id)).scalar() or 0,
        "users_by_role": dict(db.query(User.role, func.count(User.id)).group_by(User.role).all()),
        "challenges_total": db.query(func.count(Challenge.id)).scalar() or 0,
        "challenges_by_category": {k: v for k, v in by_cat_rows},
        "challenges_by_status": {k: v for k, v in by_status},
        "screening_ai_split": dict(
            db.query(ChallengeScreening.source, func.count(ChallengeScreening.id))
            .group_by(ChallengeScreening.source).all()),
        "universities": db.query(func.count(University.id)).scalar() or 0,
        "universities_verified": db.query(func.count(University.id)).filter(
            University.is_verified == True).scalar() or 0,  # noqa: E712
        "companies": db.query(func.count(Company.id)).scalar() or 0,
        "companies_verified": db.query(func.count(Company.id)).filter(
            Company.is_verified == True).scalar() or 0,  # noqa: E712
        "projects_active": db.query(func.count(Project.id)).filter(
            Project.status.in_({"planning", "active"})).scalar() or 0,
        "projects_completed": db.query(func.count(Project.id)).filter(
            Project.status == "completed").scalar() or 0,
        "competitions": db.query(func.count(Competition.id)).scalar() or 0,
        "submissions": db.query(func.count(Submission.id)).scalar() or 0,
        "sponsorship_offers": db.query(func.count(Sponsorship.id)).scalar() or 0,
        "sponsorship_disbursed": db.query(func.count(Sponsorship.id)).filter(
            Sponsorship.status == "disbursed").scalar() or 0,
        "verified_impact_reports": db.query(func.count(ImpactReport.id)).filter(
            ImpactReport.verification_status == "verified").scalar() or 0,
        "audit_events": db.query(func.count(AuditLog.id)).scalar() or 0,
        "demo_data": False,
    }


def competition_leaderboard(db: Session, competition: Competition) -> dict:
    """Two separate leaderboards: progress (execution) and innovation (judged)."""
    import json as _json

    criteria = _json.loads(competition.criteria or "[]")
    weights = {c["key"]: float(c.get("weight", 0)) for c in criteria}
    weight_total = sum(weights.values()) or 100.0

    subs = db.query(Submission).filter(Submission.competition_id == competition.id).all()

    rows = []
    for sub in subs:
        scores = sub.scores
        by_judge: dict[int, dict[str, float]] = {}
        for s in scores:
            by_judge.setdefault(s.judge_id, {})[s.criterion] = s.score
        agg: dict[str, float] = {}
        if by_judge:
            for crit in weights:
                vals = [j.get(crit) for j in by_judge.values() if crit in j]
                if vals:
                    agg[crit] = sum(vals) / len(vals)
        innovation = None
        if agg:
            innovation = round(sum(agg.get(k, 0.0) * weights[k] for k in weights) / weight_total * 10, 2)

        project = db.get(Project, sub.project_id)
        progress = project_progress(db, sub.project_id) if project else 0.0
        uni = None
        team_members = []
        if project:
            uni = project.university.name if project.university else None
            team_members = [m.user.full_name for m in project.members
                            if m.status == "active" and m.user]
        rows.append({
            "submission_id": sub.id,
            "project_id": sub.project_id,
            "project_title": sub.title,
            "team_name": " / ".join(team_members[:3]) or (project.title if project else "Team"),
            "university": uni,
            "innovation_score": innovation,
            "progress_score": progress,
            "scored_by": len(by_judge),
            "judge_count": len(by_judge),
            "score_breakdown": {k: round(v, 2) for k, v in agg.items()},
        })

    def _rank(items: list[dict], key: str) -> None:
        items.sort(key=lambda r: (r[key] if r[key] is not None else -1), reverse=True)
        for i, r in enumerate(items, start=1):
            r["rank"] = i

    innovation_rows = sorted(rows, key=lambda r: (r["innovation_score"] if r["innovation_score"] is not None else -1), reverse=True)
    progress_rows = sorted(rows, key=lambda r: r["progress_score"], reverse=True)
    for i, r in enumerate(innovation_rows, 1):
        r["innovation_rank"] = i
    for i, r in enumerate(progress_rows, 1):
        r["progress_rank"] = i

    return {
        "competition_id": competition.id,
        "criteria": criteria,
        "weights_total": weight_total,
        "innovation_leaderboard": innovation_rows,
        "progress_leaderboard": progress_rows,
        "results_published": competition.results_published,
        "note": ("Official competition rankings use the published evaluation criteria and "
                 "authorised human judge scores. Progress ranking measures approved milestone "
                 "and task completion, not raw task count."),
    }
