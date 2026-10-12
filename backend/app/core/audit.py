"""Audit trail helper.

Every consequential action (decisions, approvals, status transitions) is
recorded with the acting user, the reason, and a timestamp so the platform has
an accountable history — the AI's screening output is never the decision.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import AuditLog, User


def log(
    db: Session,
    actor: Optional[User],
    action: str,
    *,
    target_type: Optional[str] = None,
    target_id: Any = None,
    reason: Optional[str] = None,
    metadata: Optional[dict] = None,
    commit: bool = False,
) -> AuditLog:
    row = AuditLog(
        actor_id=actor.id if actor else None,
        action=action[:80],
        target_type=(target_type or "")[:60] or None,
        target_id=str(target_id)[:60] if target_id is not None else None,
        reason=reason[:2000] if reason else None,
        metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
    )
    db.add(row)
    if commit:
        db.commit()
    return row
