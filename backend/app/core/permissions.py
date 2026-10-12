"""Role-based access control helpers.

Every protected endpoint enforces permissions on the BACKEND. Frontend
hiding of buttons is convenience only, never security.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from ..models import Role, User, Project, ProjectMember, Challenge

REVIEWER_ROLES = {Role.ADMIN, Role.FACULTY, Role.UNIVERSITY_ADMIN}
STAFF_ROLES = {Role.ADMIN, Role.FACULTY, Role.UNIVERSITY_ADMIN}


def require_roles(user: User, *roles: str) -> None:
    if user is None or user.role not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action.",
        )


def require_reviewer(user: User) -> None:
    """Human eligibility reviewers: platform admin, faculty, university admin."""
    require_roles(user, Role.ADMIN, Role.FACULTY, Role.UNIVERSITY_ADMIN)


def require_admin(user: User) -> None:
    require_roles(user, Role.ADMIN)


def require_company(user: User) -> None:
    require_roles(user, Role.COMPANY)


def require_university_staff(user: User) -> None:
    require_roles(user, Role.UNIVERSITY_ADMIN, Role.FACULTY, Role.ADMIN)


# --- Project-level checks ---------------------------------------------------


def get_membership(db, user: User, project_id: int) -> ProjectMember | None:
    return db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user.id,
        ProjectMember.status == "active",
    ).first()


def can_access_project(db, user: User, project: Project) -> bool:
    if user.role == Role.ADMIN:
        return True
    if project.mentor_id == user.id or project.created_by_id == user.id:
        return True
    if user.role == Role.UNIVERSITY_ADMIN and project.university_id and _user_university(user) == project.university_id:
        return True
    return get_membership(db, user, project.id) is not None


def require_project_access(db, user: User, project: Project) -> None:
    if not can_access_project(db, user, project):
        raise HTTPException(status_code=403, detail="You do not have access to this project.")


def require_project_staff(db, user: User, project: Project) -> None:
    """Mentor / university admin / platform admin."""
    if user.role == Role.ADMIN:
        return
    if project.mentor_id == user.id or project.created_by_id == user.id:
        return
    if user.role == Role.UNIVERSITY_ADMIN and project.university_id and _user_university(user) == project.university_id:
        return
    raise HTTPException(status_code=403, detail="Only the project mentor or university staff can do this.")


def _user_university(user: User) -> int | None:
    if user.faculty_profile:
        return user.faculty_profile.university_id
    if user.student_profile:
        return user.student_profile.university_id
    return None


def owns_challenge(user: User, challenge: Challenge) -> bool:
    return challenge.citizen_id == user.id


def require_citizen_challenge(user: User, challenge: Challenge) -> None:
    if user.role != Role.CITIZEN or challenge.citizen_id != user.id:
        raise HTTPException(status_code=403, detail="This challenge does not belong to you.")
