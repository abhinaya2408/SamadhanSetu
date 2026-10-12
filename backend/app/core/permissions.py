"""Role- and capability-based access control helpers.

Every protected endpoint enforces permissions on the BACKEND. Frontend
hiding of buttons is convenience only, never security.

Revision 3 merges the old ``faculty`` and ``university_admin`` roles into a single
``university_faculty`` role. What a person may actually do is decided by the
**capabilities** they hold within that role (challenge reviewer, project mentor,
university coordinator), so a merged login never means unrestricted access.
The legacy role strings are still recognised so nothing breaks mid-migration.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from ..models import Capability, Challenge, Project, ProjectMember, Role, User

REVIEWER_ROLES = {Role.ADMIN} | Role.UNIVERSITY_ROLES
STAFF_ROLES = {Role.ADMIN} | Role.UNIVERSITY_ROLES

# A user with no explicit capabilities still gets sensible defaults for their
# legacy role, so an un-migrated account keeps exactly the access it had.
_LEGACY_DEFAULTS = {
    Role.FACULTY: Capability.DEFAULT_REVIEWER,
    Role.UNIVERSITY_ADMIN: Capability.DEFAULT_COORDINATOR,
}


def capabilities_of(user: User) -> set[str]:
    """Return the capability set for a user, falling back to legacy defaults."""
    if user is None:
        return set()
    if user.role == Role.ADMIN:
        return set(Capability.ALL)  # platform administrators hold every capability
    raw = (getattr(user, "capabilities", None) or "").strip()
    if raw:
        parsed = {c.strip() for c in raw.split(",") if c.strip()}
        return {c for c in parsed if c in Capability.ALL} or set(_LEGACY_DEFAULTS.get(user.role, set()))
    return set(_LEGACY_DEFAULTS.get(user.role, set()))


def has_capability(user: User, capability: str) -> bool:
    if user is None:
        return False
    if user.role == Role.ADMIN:
        return True
    if user.role not in Role.UNIVERSITY_ROLES:
        return False
    return capability in capabilities_of(user)


def require_capability(user: User, capability: str) -> None:
    if not has_capability(user, capability):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account does not have the '" + capability.replace("_", " ") + "' capability.",
        )


def require_roles(user: User, *roles: str) -> None:
    if user is None or user.role not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action.",
        )


def require_reviewer(user: User) -> None:
    """Human eligibility reviewers: platform admin or a University & Faculty user
    holding the challenge-reviewer capability (or a coordinator)."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You do not have permission to perform this action.")
    if user.role == Role.ADMIN:
        return
    if user.role in Role.UNIVERSITY_ROLES and (
            Capability.CHALLENGE_REVIEWER in capabilities_of(user)
            or Capability.UNIVERSITY_COORDINATOR in capabilities_of(user)):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                        detail="Only authorised reviewers can do that.")


def require_admin(user: User) -> None:
    require_roles(user, Role.ADMIN)


def require_company(user: User) -> None:
    require_roles(user, Role.COMPANY)


def require_university_staff(user: User) -> None:
    """Any University & Faculty user, or a platform administrator."""
    require_roles(user, Role.ADMIN, *Role.UNIVERSITY_ROLES)


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
    if user.role in Role.UNIVERSITY_ROLES and project.university_id and _user_university(user) == project.university_id:
        return True
    return get_membership(db, user, project.id) is not None


def require_project_access(db, user: User, project: Project) -> None:
    if not can_access_project(db, user, project):
        raise HTTPException(status_code=403, detail="You do not have access to this project.")


def require_project_staff(db, user: User, project: Project) -> None:
    """Mentor / university coordinator / platform admin."""
    if user.role == Role.ADMIN:
        return
    if project.mentor_id == user.id or project.created_by_id == user.id:
        return
    if user.role in Role.UNIVERSITY_ROLES and project.university_id and _user_university(user) == project.university_id:
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
