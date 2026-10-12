"""Authentication endpoints: register, login, logout, current user."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from ..config import settings
from ..core import security
from ..dependencies import CurrentUser, CsrfDep, DbDep, OptionalUser
from ..models import (
    Company,
    Department,
    FacultyProfile,
    Role,
    StudentProfile,
    University,
    User,
)
from ..schemas import LoginIn, MessageOut, RegisterIn, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_expire_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterIn, db: DbDep, response: Response, request: Request):
    email = payload.email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    if payload.role == Role.ADMIN:
        # Public registration must never grant platform administrator privileges.
        raise HTTPException(
            status_code=403,
            detail="Platform administrator accounts are provisioned by the operators, not by public registration.")

    user = User(
        email=email,
        password_hash=security.hash_password(payload.password),
        full_name=payload.full_name.strip(),
        role=payload.role,
        is_verified=False,
    )
    db.add(user)
    db.flush()

    if payload.role == Role.STUDENT:
        if payload.university_id and not db.get(University, payload.university_id):
            raise HTTPException(status_code=400, detail="Unknown university.")
        if payload.department_id and not db.get(Department, payload.department_id):
            raise HTTPException(status_code=400, detail="Unknown department.")
        db.add(StudentProfile(
            user_id=user.id,
            university_id=payload.university_id,
            department_id=payload.department_id,
        ))
    elif payload.role == Role.FACULTY:
        db.add(FacultyProfile(
            user_id=user.id,
            university_id=payload.university_id,
            department_id=payload.department_id,
            title=payload.faculty_title,
        ))
    elif payload.role == Role.COMPANY:
        name = (payload.company_name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Company name is required for company accounts.")
        db.add(Company(
            user_id=user.id,
            name=name,
            industry=payload.company_industry,
            website=payload.company_website,
        ))
    elif payload.role == Role.UNIVERSITY_ADMIN:
        if not payload.university_id:
            raise HTTPException(status_code=400, detail="Select the university you administer.")
        db.add(FacultyProfile(user_id=user.id, university_id=payload.university_id, title="Administrator"))

    db.commit()
    db.refresh(user)

    token = security.create_session(db, user, request.headers.get("user-agent"))
    _set_cookie(response, token)
    return _user_out(user)


@router.post("/login", response_model=UserOut)
def login(payload: LoginIn, db: DbDep, response: Response, request: Request):
    user = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    if not user or not security.verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated.")
    token = security.create_session(db, user, request.headers.get("user-agent"))
    _set_cookie(response, token)
    return _user_out(user)


@router.post("/logout", response_model=MessageOut)
def logout(db: DbDep, response: Response, request: Request, csrf: CsrfDep):
    token = request.cookies.get(settings.cookie_name)
    if token:
        security.destroy_session(db, token)
    response.delete_cookie(settings.cookie_name, path="/")
    return MessageOut(message="Signed out.")


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return _user_out(user)


@router.get("/me/optional", response_model=UserOut)
def me_optional(user: OptionalUser):
    if user is None:
        raise HTTPException(status_code=404, detail="Not signed in.")
    return _user_out(user)


def _user_out(user: User) -> UserOut:
    return UserOut.model_validate(user)
