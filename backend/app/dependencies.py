"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Cookie, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from .config import settings
from .core import security
from .database import get_db
from .models import User

DbDep = Annotated[Session, Depends(get_db)]


def get_current_user_optional(
    db: DbDep,
    smds_session: Annotated[Optional[str], Cookie(alias=settings.cookie_name)] = None,
) -> Optional[User]:
    if not smds_session:
        return None
    return security.resolve_session(db, smds_session)


def get_current_user(
    db: DbDep,
    user: Annotated[Optional[User], Depends(get_current_user_optional)] = None,
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="You must be signed in to do that.",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[Optional[User], Depends(get_current_user_optional)]


def require_csrf(
    x_requested_with: Annotated[Optional[str], Header()] = None,
) -> None:
    """Reject state-changing requests missing the custom header (CSRF defence)."""
    if not security.csrf_ok(x_requested_with):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing request header. Please retry from the application.",
        )


CsrfDep = Annotated[None, Depends(require_csrf)]


class PageParams:
    def __init__(
        self,
        page: Annotated[int, Query(ge=1, le=10_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 12,
    ):
        self.page = page
        self.page_size = page_size
        self.offset = (page - 1) * page_size

    @property
    def page_count_hint(self) -> str:
        return f"{self.page},{self.page_size}"


PageDep = Annotated[PageParams, Depends()]


def paginated(items: list, total: int, page: int, page_size: int) -> dict:
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }
