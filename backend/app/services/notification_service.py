"""In-app notifications and deterministic deadline scanning.

Each deadline notification carries an invisible marker token in its body so the
scan stays idempotent — repeated scans never spam the same user twice for the
same task/milestone/competition state.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from ..models import (
    Milestone,
    MilestoneStatus,
    Notification,
    Project,
    ProjectMember,
    Submission,
    Task,
    TaskStatus,
    Competition,
    utcnow,
)


def notify(db: Session, user_id: int, title: str, body: str = "",
           kind: str = "info", link: str | None = None, commit: bool = False) -> Notification:
    n = Notification(user_id=user_id, title=title[:255], body=body or None, kind=kind, link=link)
    db.add(n)
    if commit:
        db.commit()
    return n


def notify_team(db: Session, project: Project, title: str, body: str = "",
                kind: str = "info", exclude_user_id: int | None = None) -> int:
    members = db.query(ProjectMember).filter(
        ProjectMember.project_id == project.id, ProjectMember.status == "active").all()
    count = 0
    for m in members:
        if exclude_user_id and m.user_id == exclude_user_id:
            continue
        notify(db, m.user_id, title, body, kind, link=f"/project.html?id={project.id}")
        count += 1
    if project.mentor_id and project.mentor_id != exclude_user_id:
        notify(db, project.mentor_id, title, body, kind, link=f"/project.html?id={project.id}")
        count += 1
    return count


def _marker(target: str, key) -> str:
    return f"[{target}:{key}]"


def _already(db: Session, token: str) -> bool:
    return db.query(Notification.id).filter(Notification.body.contains(token)).first() is not None


def scan_deadlines(db: Session) -> dict:
    """Deterministic overdue / upcoming-deadline detection."""
    today = dt.date.today()
    created = 0

    # --- Overdue tasks: notify the whole team once per task ---
    overdue = (
        db.query(Task)
        .filter(Task.status != TaskStatus.COMPLETED,
                Task.due_date.isnot(None), Task.due_date < today)
        .all()
    )
    for task in overdue:
        token = _marker("task_overdue", task.id)
        if _already(db, token):
            continue
        members = db.query(ProjectMember).filter(
            ProjectMember.project_id == task.project_id,
            ProjectMember.status == "active").all()
        for m in members:
            notify(db, m.user_id,
                   f"Overdue task: {task.title}",
                   f"Due {task.due_date:%d %b %Y}. Update the task or flag a blocker. {token}",
                   "deadline", link=f"/project.html?id={task.project_id}&tab=tasks")
            created += 1
        if members:
            _mark(db, token)

    # --- Tasks due within 3 days: notify assignee once ---
    soon = (
        db.query(Task)
        .filter(Task.status != TaskStatus.COMPLETED,
                Task.due_date.isnot(None),
                Task.due_date >= today, Task.due_date <= today + dt.timedelta(days=3))
        .all()
    )
    for task in soon:
        token = _marker("task_due", task.id)
        if not task.assignee_id or _already(db, token):
            continue
        notify(db, task.assignee_id,
               f"Upcoming deadline: {task.title}",
               f"Due {task.due_date:%d %b %Y}. {token}",
               "deadline", link=f"/project.html?id={task.project_id}&tab=tasks")
        created += 1
        _mark(db, token)

    # --- Overdue milestones: flip status + warn mentor once ---
    miles = db.query(Milestone).filter(
        Milestone.status.in_([MilestoneStatus.PENDING, MilestoneStatus.IN_PROGRESS]),
        Milestone.due_date.isnot(None), Milestone.due_date < today).all()
    for ms in miles:
        ms.status = MilestoneStatus.OVERDUE
        token = _marker("milestone_overdue", ms.id)
        proj = db.get(Project, ms.project_id)
        if proj and proj.mentor_id and not _already(db, token):
            notify(db, proj.mentor_id,
                   f"Milestone overdue: {ms.name}",
                   f"Project: {proj.title}. Due {ms.due_date:%d %b %Y}. {token}",
                   "risk", link=f"/project.html?id={ms.project_id}&tab=milestones")
            created += 1
            _mark(db, token)

    # --- Competition deadlines: notify registered teams not yet submitted ---
    for comp in db.query(Competition).filter(
            Competition.status.in_(["upcoming", "active"])).all():
        if not comp.submission_deadline:
            continue
        if not (today <= comp.submission_deadline <= today + dt.timedelta(days=3)):
            continue
        submitted_ids = {r[0] for r in db.query(Submission.project_id)
                         .filter(Submission.competition_id == comp.id).all()}
        registered = db.query(ProjectMember.project_id).filter(
            ProjectMember.status == "active").distinct().all()
        for (pid,) in registered:
            if pid in submitted_ids:
                continue
            token = _marker(f"comp{comp.id}_deadline", pid)
            if _already(db, token):
                continue
            proj = db.get(Project, pid)
            if not proj:
                continue
            created += notify_team(
                db, proj,
                f"Competition deadline approaching: {comp.title}",
                f"Submissions close {comp.submission_deadline:%d %b %Y}. {token}",
                "competition")
            if created:
                _mark(db, token)

    db.commit()
    return {"created": created, "ran_at": utcnow().isoformat()}


def _mark(db: Session, token: str) -> None:
    """No-op guard: markers live inside the notification bodies we just created.

    Kept as a function so the intent is explicit and the scan stays idempotent
    even if the notification creation rules change later.
    """
    return None
