"""Challenge lifecycle: citizen reporting, AI simplification, duplicate checks,
human review, clarifications, evidence uploads.

Key rules enforced here:

* The citizen's own words are stored **verbatim** — AI never rewrites them.
* AI produces a *simplification* and a *recommendation only*. The authoritative
  decision is always a human ``ChallengeReview`` with a written reason.
* Evidence photos are private by default and served only through an authorised
  endpoint.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import or_

from ..core import audit
from ..core.permissions import (
    require_citizen_challenge,
    require_reviewer,
)
from ..dependencies import CsrfDep, CurrentUser, DbDep, PageDep, OptionalUser, paginated
from ..models import (
    DOMAINS,
    IMPACT_KINDS,
    Challenge,
    ChallengeAttachment,
    ChallengeReview,
    ChallengeScreening,
    ChallengeScreeningCorrection,
    ChallengeStatus,
    ClarificationRequest,
    Project,
    Role,
)
from ..schemas import (
    AttachmentOut,
    ChallengeDecisionOut,
    ChallengeIn,
    ChallengeOut,
    ChallengePreviewOut,
    ClarificationAnswer,
    ClarificationIn,
    ClarificationOut,
    DuplicateCheckIn,
    DuplicateCheckOut,
    ProjectOut,
    ReviewIn,
    ReviewOut,
    ScreeningCorrectionIn,
    ScreeningCorrectionOut,
    ScreeningOut,
)
from ..services import (
    duplicate_service,
    image_service,
    notification_service,
    screening_service,
    solution_service,
)

router = APIRouter(prefix="/api/challenges", tags=["challenges"])

REVIEW_OPEN = (
    ChallengeStatus.SUBMITTED,
    ChallengeStatus.NEEDS_CLARIFICATION,
    ChallengeStatus.PENDING_REVIEW,
)

CORRECTABLE_FIELDS = {
    "one_sentence", "simple_explanation", "who_is_affected", "why_it_matters",
    "impact_summary", "possible_causes", "potential_solution", "skills_required",
}


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _screening_out(s: ChallengeScreening | None) -> ScreeningOut | None:
    if s is None:
        return None
    return ScreeningOut.model_validate(s)


def _attachment_out(a: ChallengeAttachment, *, with_url: bool = True) -> AttachmentOut:
    out = AttachmentOut.model_validate(a)
    if with_url:
        if a.challenge_id:
            out.url = f"/api/challenges/{a.challenge_id}/attachments/{a.id}/raw"
        else:
            out.url = f"/api/challenges/attachments/staged/{a.id}/raw"
    return out


def _project_out(p: Project | None) -> ProjectOut | None:
    if p is None:
        return None
    out = ProjectOut.model_validate(p)
    out.mentor_name = p.mentor.full_name if p.mentor else None
    out.university_name = p.university.name if p.university else None
    out.member_count = sum(1 for m in p.members if m.status == "active")
    return out


def _latest_screening(db, challenge_id: int) -> ChallengeScreening | None:
    return (
        db.query(ChallengeScreening)
        .filter(ChallengeScreening.challenge_id == challenge_id)
        .order_by(ChallengeScreening.id.desc())
        .first()
    )


def challenge_out(db, c: Challenge, *, full: bool = True, user=None) -> ChallengeOut:
    out = ChallengeOut.model_validate(c)
    out.citizen_name = c.citizen.full_name if c.citizen else None
    out.impact_label = c.impact_label
    if full:
        out.latest_screening = _screening_out(_latest_screening(db, c.id))
        out.project = _project_out(c.project)
        out.attachments = [_attachment_out(a) for a in c.attachments]
    return out


def _title_from_description(description: str) -> str:
    """Derive a short title when the citizen did not write one."""
    text = " ".join((description or "").split())
    if not text:
        return "Untitled problem"
    first = text.split(". ")[0]
    if len(first) > 90:
        first = first[:90].rsplit(" ", 1)[0] + "…"
    return first[:255]


def _apply_impact(challenge: Challenge, payload: ChallengeIn) -> None:
    impact = payload.impact
    if impact is None:
        challenge.impact_kind = "unknown"
        challenge.impact_is_estimate = True
        return
    challenge.impact_kind = impact.kind
    challenge.impact_amount = impact.amount
    challenge.impact_unit = (impact.unit or "").strip() or None
    challenge.impact_currency = (impact.currency or "").strip().upper() or None
    challenge.impact_notes = (impact.note or "").strip() or None
    # The platform never verifies a citizen's figure, so the estimate flag stays
    # set unless the citizen explicitly told us they measured it — and even then
    # the wording records that it is only their claim.
    challenge.impact_is_estimate = bool(impact.is_estimate)
    default_source = ("Reported by the citizen" if impact.is_estimate
                      else "Citizen says this figure was measured")
    challenge.impact_source = (impact.source or "").strip() or default_source


def _apply_location(challenge: Challenge, payload: ChallengeIn) -> None:
    city = duplicate_service.clean_display(payload.city, 120)
    locality = duplicate_service.clean_display(payload.locality, 160)
    challenge.city = city
    challenge.locality = locality
    challenge.city_key = duplicate_service.normalise_city(city) or None
    explicit = duplicate_service.clean_display(payload.location, 220)
    if explicit:
        challenge.location = explicit
    else:
        parts = [p for p in (locality, city) if p]
        challenge.location = ", ".join(parts) or None


def _make_challenge(user, payload: ChallengeIn) -> Challenge:
    challenge = Challenge(
        citizen_id=user.id,
        description=payload.description.strip(),  # stored verbatim
        title=(payload.title or "").strip() or _title_from_description(payload.description),
        category=payload.category,
        affected_users=(payload.affected_users or "").strip() or None,
        current_situation=(payload.current_situation or "").strip() or None,
        desired_outcome=(payload.desired_outcome or "").strip() or None,
        constraints=(payload.constraints or "").strip() or None,
        client_request_id=payload.client_request_id,
        status=ChallengeStatus.SUBMITTED,
    )
    _apply_location(challenge, payload)
    _apply_impact(challenge, payload)
    return challenge


def _claim_staged_attachments(db, challenge: Challenge, user, staging_key: str | None) -> int:
    """Move files uploaded during the reporting session onto the new challenge."""
    if not staging_key:
        return 0
    rows = db.query(ChallengeAttachment).filter(
        ChallengeAttachment.staging_key == staging_key,
        ChallengeAttachment.uploader_id == user.id,
        ChallengeAttachment.challenge_id.is_(None),
    ).all()
    claimed = 0
    for row in rows:
        # Re-home the file inside the challenge's own directory.
        source = image_service.path_for(row)
        row.challenge_id = challenge.id
        row.staging_key = None
        target_dir = image_service.storage_dir(challenge.id)
        target = target_dir / row.stored_name
        try:
            if source.exists() and source != target:
                target.write_bytes(source.read_bytes())
                source.unlink(missing_ok=True)
        except OSError:
            pass
        claimed += 1
    return claimed


# ---------------------------------------------------------------------------
# Reference data (domains + impact kinds) so the UI never hardcodes them
# ---------------------------------------------------------------------------

@router.get("/reference")
def reference_data():
    """Domains, impact kinds and upload limits used by the reporting form."""
    return {
        "domains": [
            {"key": key, "label": label, "icon": icon, "hint": hint}
            for key, label, icon, hint in DOMAINS
        ],
        "categories": sorted({key for key, *_ in DOMAINS}),
        "impact_kinds": [{"key": k, "label": v} for k, v in IMPACT_KINDS.items()],
        "upload": image_service.describe_limits(),
    }


# ---------------------------------------------------------------------------
# Analyse-before-submit (a preview that is never saved)
# ---------------------------------------------------------------------------

@router.post("/preview", response_model=ChallengePreviewOut)
def preview_challenge(payload: ChallengeIn, db: DbDep, user: CurrentUser, csrf: CsrfDep,
                      use_ai: bool = Query(default=True)):
    """Run the simplification + duplicate check for review, without saving.

    The brief allows the "Analyze" step to preview rather than persist, as long as
    the review experience is clear. The response states plainly that nothing was
    saved, and the stored analysis is generated again at submission time so the
    database never holds an unreviewed preview as if it were a submission.
    """
    if user.role != Role.CITIZEN:
        raise HTTPException(status_code=403, detail="Only citizen accounts can report a problem.")

    transient = _make_challenge(user, payload)  # deliberately not added to the session
    preview = screening_service.preview_analysis(transient, use_ai=use_ai)

    duplicates = duplicate_service.find_duplicates(
        db,
        description=payload.description,
        title=transient.title,
        city=payload.city,
        locality=payload.locality,
        category=payload.category or preview["result"].get("category") or "other",
        use_ai=use_ai,
    )
    return ChallengePreviewOut(
        persisted=False,
        analysis=preview["analysis"],
        duplicates=DuplicateCheckOut.model_validate(duplicates),
        derived_title=transient.title,
        note=("This preview was not saved. Nothing is stored until you press "
              "\"Submit problem\", and the stored analysis is generated fresh at that point."),
    )


@router.post("/duplicates", response_model=DuplicateCheckOut)
def check_duplicates(payload: DuplicateCheckIn, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    """Check a draft report against existing reports in the same city."""
    if user.role != Role.CITIZEN:
        raise HTTPException(status_code=403, detail="Only citizen accounts can report a problem.")
    found = duplicate_service.find_duplicates(
        db,
        description=payload.description,
        title=payload.title,
        city=payload.city,
        locality=payload.locality,
        category=payload.category,
        exclude_challenge_id=payload.exclude_challenge_id,
        use_ai=payload.use_ai,
    )
    return DuplicateCheckOut.model_validate(found)


# ---------------------------------------------------------------------------
# Citizen submission
# ---------------------------------------------------------------------------

@router.post("", response_model=ChallengeOut, status_code=status.HTTP_201_CREATED)
def submit_challenge(payload: ChallengeIn, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    """Submit a societal problem. The citizen's wording is stored verbatim.

    Idempotent when the browser supplies ``client_request_id``: a retried request
    returns the existing report instead of creating a second one.
    """
    if user.role != Role.CITIZEN:
        raise HTTPException(status_code=403, detail="Only citizen accounts can submit societal problems.")

    if payload.client_request_id:
        existing = db.query(Challenge).filter(
            Challenge.citizen_id == user.id,
            Challenge.client_request_id == payload.client_request_id,
        ).first()
        if existing is not None:
            return challenge_out(db, existing, user=user)

    challenge = _make_challenge(user, payload)
    db.add(challenge)
    db.flush()

    claimed = _claim_staged_attachments(db, challenge, user, payload.client_request_id)
    db.flush()  # autoflush is off, so make the claimed rows visible to later reads

    # Non-blocking screening: run synchronously but never let AI failures break submission.
    try:
        screening_service.screen_challenge(db, challenge, use_ai=True)
    except Exception:  # noqa: BLE001 - submission must survive any AI/runtime hiccup
        db.rollback()
        challenge = db.get(Challenge, challenge.id)
        if challenge is None:
            raise HTTPException(status_code=500, detail="Could not save your submission. Please retry.")

    # Duplicate/related detection runs after screening so the detected domain is used.
    duplicates = duplicate_service.find_duplicates(
        db,
        description=challenge.description,
        title=challenge.title,
        city=challenge.city,
        locality=challenge.locality,
        category=challenge.category,
        exclude_challenge_id=challenge.id,
        use_ai=True,
    )
    linked = 0
    try:
        linked = duplicate_service.link_related(db, challenge, duplicates["candidates"])
    except Exception:  # noqa: BLE001 - related links are an enhancement, never a blocker
        pass

    audit.log(db, user, "challenge.submitted", target_type="challenge", target_id=challenge.id,
              metadata={"related_linked": linked,
                        "duplicate_candidates": duplicates["likely_duplicate_count"],
                        "attachments": claimed})
    db.commit()
    db.refresh(challenge)
    return challenge_out(db, challenge, user=user)


@router.get("/mine", response_model=dict)
def my_challenges(db: DbDep, user: CurrentUser, page: PageDep):
    if user.role != Role.CITIZEN:
        raise HTTPException(status_code=403, detail="Only citizen accounts have submitted challenges.")
    q = (
        db.query(Challenge)
        .filter(Challenge.citizen_id == user.id)
        .order_by(Challenge.created_at.desc())
    )
    total = q.count()
    rows = q.offset(page.offset).limit(page.page_size).all()
    items = [challenge_out(db, c, user=user) for c in rows]
    return paginated(items, total, page.page, page.page_size)


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

@router.get("", response_model=dict)
def list_challenges(
    db: DbDep,
    page: PageDep,
    user: OptionalUser = None,
    status_filter: str | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None),
    city: str | None = Query(default=None),
    search: str | None = Query(default=None),
    mine: bool = Query(default=False),
):
    """Public/approved listing, plus reviewer queues when appropriate.

    Students see only challenges approved for student innovation. Reviewers can
    filter to the review queue. Anonymous visitors see approved challenges only.
    """
    q = db.query(Challenge)

    if mine:
        if not user:
            raise HTTPException(status_code=401, detail="Sign in to view your submissions.")
        q = q.filter(Challenge.citizen_id == user.id)
    elif user and user.role in {Role.ADMIN, *Role.UNIVERSITY_ROLES}:
        pass
    else:
        q = q.filter(Challenge.status.in_(
            [ChallengeStatus.APPROVED, ChallengeStatus.ACCEPTED,
             ChallengeStatus.IN_PROJECT, ChallengeStatus.COMPLETED]))

    if status_filter:
        q = q.filter(Challenge.status == status_filter)
    if category:
        q = q.filter(Challenge.category == category)
    if city:
        q = q.filter(Challenge.city_key == duplicate_service.normalise_city(city))
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(or_(Challenge.title.ilike(like), Challenge.description.ilike(like)))

    q = q.order_by(Challenge.created_at.desc())
    total = q.count()
    rows = q.offset(page.offset).limit(page.page_size).all()
    items = [challenge_out(db, c, full=False, user=user) for c in rows]
    return {
        **paginated(items, total, page.page, page.page_size),
        "available_cities": [
            c for (c,) in db.query(Challenge.city).filter(Challenge.city.isnot(None))
            .distinct().order_by(Challenge.city).all()
        ],
    }


@router.get("/queue", response_model=dict)
def review_queue(
    db: DbDep,
    user: CurrentUser,
    page: PageDep,
    status_filter: str | None = Query(default=None, alias="status"),
):
    """Reviewer queue: challenges awaiting a human decision."""
    require_reviewer(user)
    q = db.query(Challenge)
    if status_filter:
        q = q.filter(Challenge.status == status_filter)
    else:
        q = q.filter(Challenge.status.in_(REVIEW_OPEN))
    q = q.order_by(Challenge.created_at.asc())
    total = q.count()
    rows = q.offset(page.offset).limit(page.page_size).all()
    items = [challenge_out(db, c, user=user) for c in rows]
    return paginated(items, total, page.page, page.page_size)


@router.get("/decisions", response_model=dict)
def decided_challenges(
    db: DbDep,
    user: CurrentUser,
    page: PageDep,
    decision: str | None = Query(default=None),
):
    """Reviewer history of decided challenges."""
    require_reviewer(user)
    q = db.query(Challenge).filter(~Challenge.status.in_(REVIEW_OPEN))
    if decision:
        q = q.filter(Challenge.status == decision)
    q = q.order_by(Challenge.updated_at.desc())
    total = q.count()
    rows = q.offset(page.offset).limit(page.page_size).all()
    items = [challenge_out(db, c, user=user) for c in rows]
    return paginated(items, total, page.page, page.page_size)


@router.get("/card-view", response_model=list[ChallengeDecisionOut])
def card_view(db: DbDep, user: CurrentUser, limit: int = Query(default=12, ge=1, le=50)):
    """Compact review cards for dashboards."""
    require_reviewer(user)
    rows = (
        db.query(Challenge)
        .filter(Challenge.status.in_(REVIEW_OPEN))
        .order_by(Challenge.created_at.asc())
        .limit(limit)
        .all()
    )
    out = []
    for c in rows:
        s = _latest_screening(db, c.id)
        out.append(ChallengeDecisionOut(
            id=c.id, title=c.title, category=c.category, status=c.status,
            difficulty=c.difficulty, created_at=c.created_at, location=c.location,
            has_project=c.project is not None,
            government_dependency=s.government_dependency if s else None,
            suitability=s.student_project_suitability if s else None,
        ))
    return out


# ---------------------------------------------------------------------------
# Evidence uploads (private by default)
# ---------------------------------------------------------------------------

def _can_see_attachments(db, user, challenge: Challenge) -> bool:
    if user is None:
        return False
    if user.id == challenge.citizen_id:
        return True
    if user.role in {Role.ADMIN, *Role.UNIVERSITY_ROLES}:
        return True
    project = challenge.project
    if project is not None:
        from ..core.permissions import can_access_project
        if can_access_project(db, user, project):
            return True
    return False


@router.post("/attachments/staged", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload_staged_attachment(
    db: DbDep,
    user: CurrentUser,
    csrf: CsrfDep,
    file: UploadFile = File(...),
    staging_key: str = Query(..., min_length=8, max_length=64),
):
    """Upload evidence while the citizen is still filling in the form.

    The file is validated server-side, metadata-stripped and stored under a
    generated name, owned through the reporting session's ``staging_key``.
    """
    if user.role != Role.CITIZEN:
        raise HTTPException(status_code=403, detail="Only citizen accounts can attach evidence.")

    existing = db.query(ChallengeAttachment).filter(
        ChallengeAttachment.staging_key == staging_key,
        ChallengeAttachment.uploader_id == user.id,
        ChallengeAttachment.challenge_id.is_(None),
    ).count()
    if existing >= image_service.MAX_ATTACHMENTS_PER_SESSION:
        raise HTTPException(
            status_code=409,
            detail=f"You can attach up to {image_service.MAX_ATTACHMENTS_PER_SESSION} photos per report.")

    raw = await file.read(image_service.settings.max_upload_bytes + 1)
    try:
        processed = image_service.process_upload(file.filename or "photo", raw, file.content_type or "")
    except image_service.UploadRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    directory = image_service.storage_dir(None, staging_key)
    stored_name = image_service.generated_name(processed)
    image_service.write_file(directory, stored_name, processed.data)

    row = ChallengeAttachment(
        challenge_id=None,
        staging_key=staging_key,
        uploader_id=user.id,
        stored_name=stored_name,
        original_name=image_service.safe_display_name(file.filename or "photo"),
        content_type=processed.content_type,
        size_bytes=processed.size_bytes,
        sha256=processed.sha256,
        metadata_stripped=processed.metadata_stripped,
        validation_note=processed.note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _attachment_out(row)


@router.post("/{challenge_id}/attachments", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    challenge_id: int,
    db: DbDep,
    user: CurrentUser,
    csrf: CsrfDep,
    file: UploadFile = File(...),
):
    """Attach further evidence to an existing report (owner, before review)."""
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    require_citizen_challenge(user, c)

    if len(c.attachments) >= image_service.MAX_ATTACHMENTS_PER_CHALLENGE:
        raise HTTPException(
            status_code=409,
            detail=f"Up to {image_service.MAX_ATTACHMENTS_PER_CHALLENGE} photos are allowed per report.")

    raw = await file.read(image_service.settings.max_upload_bytes + 1)
    try:
        processed = image_service.process_upload(file.filename or "photo", raw, file.content_type or "")
    except image_service.UploadRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    directory = image_service.storage_dir(c.id)
    stored_name = image_service.generated_name(processed)
    image_service.write_file(directory, stored_name, processed.data)

    row = ChallengeAttachment(
        challenge_id=c.id,
        uploader_id=user.id,
        stored_name=stored_name,
        original_name=image_service.safe_display_name(file.filename or "photo"),
        content_type=processed.content_type,
        size_bytes=processed.size_bytes,
        sha256=processed.sha256,
        metadata_stripped=processed.metadata_stripped,
        validation_note=processed.note,
    )
    db.add(row)
    audit.log(db, user, "challenge.evidence_added", target_type="challenge", target_id=c.id)
    db.commit()
    db.refresh(row)
    return _attachment_out(row)


@router.get("/{challenge_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(challenge_id: int, db: DbDep, user: CurrentUser):
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    if not _can_see_attachments(db, user, c):
        raise HTTPException(status_code=403, detail="You do not have access to this report's evidence.")
    return [_attachment_out(a) for a in c.attachments]


@router.get("/{challenge_id}/attachments/{attachment_id}/raw")
def download_attachment(challenge_id: int, attachment_id: int, db: DbDep, user: CurrentUser):
    """Stream a private evidence photo to an authorised viewer only."""
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    if not _can_see_attachments(db, user, c):
        raise HTTPException(status_code=403, detail="You do not have access to this report's evidence.")
    row = db.get(ChallengeAttachment, attachment_id)
    if not row or row.challenge_id != c.id:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    path = image_service.path_for(row)
    if not Path(path).exists():
        raise HTTPException(status_code=410, detail="This file is no longer available.")
    return FileResponse(
        path,
        media_type=row.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{row.stored_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=0, no-store",
        },
    )


@router.get("/attachments/staged/{attachment_id}/raw")
def download_staged_attachment(attachment_id: int, db: DbDep, user: CurrentUser):
    """A staged upload is visible only to the citizen who uploaded it."""
    row = db.get(ChallengeAttachment, attachment_id)
    if not row or row.challenge_id is not None:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    if row.uploader_id != user.id:
        raise HTTPException(status_code=403, detail="You do not have access to this file.")
    path = image_service.path_for(row)
    if not Path(path).exists():
        raise HTTPException(status_code=410, detail="This file is no longer available.")
    return FileResponse(
        path,
        media_type=row.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{row.stored_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=0, no-store",
        },
    )


@router.delete("/{challenge_id}/attachments/{attachment_id}")
def delete_attachment(challenge_id: int, attachment_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    row = db.get(ChallengeAttachment, attachment_id)
    if not row or row.challenge_id != c.id:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    if row.uploader_id != user.id and user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="Only the person who uploaded a photo can remove it.")
    image_service.delete_file(row)
    db.delete(row)
    audit.log(db, user, "challenge.evidence_removed", target_type="challenge", target_id=c.id)
    db.commit()
    return {"message": "Photo removed."}


@router.delete("/attachments/staged/{attachment_id}")
def delete_staged_attachment(attachment_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    row = db.get(ChallengeAttachment, attachment_id)
    if not row or row.challenge_id is not None:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    if row.uploader_id != user.id:
        raise HTTPException(status_code=403, detail="You do not have access to this file.")
    image_service.delete_file(row)
    db.delete(row)
    db.commit()
    return {"message": "Photo removed."}


# ---------------------------------------------------------------------------
# Single challenge
# ---------------------------------------------------------------------------

@router.get("/{challenge_id}", response_model=ChallengeOut)
def get_challenge(challenge_id: int, db: DbDep, user: OptionalUser = None):
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    privileged = bool(user and user.role in {Role.ADMIN, *Role.UNIVERSITY_ROLES})
    is_owner = bool(user and user.id == c.citizen_id)
    if c.status in ChallengeStatus.BLOCKED and not (privileged or is_owner):
        raise HTTPException(status_code=404, detail="Challenge not found.")
    if c.status in REVIEW_OPEN and not (privileged or is_owner):
        raise HTTPException(status_code=404, detail="Challenge not found.")
    return challenge_out(db, c, user=user)


@router.post("/{challenge_id}/screen", response_model=ScreeningOut)
def rescreen(challenge_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep,
             use_ai: bool = Query(default=True)):
    """Re-run AI/rule screening. Owner or a reviewer only."""
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    if not (c.citizen_id == user.id or user.role in {Role.ADMIN, *Role.UNIVERSITY_ROLES}):
        raise HTTPException(status_code=403, detail="You cannot screen this challenge.")
    s = screening_service.screen_challenge(db, c, use_ai=use_ai)
    audit.log(db, user, "challenge.screened", target_type="challenge", target_id=c.id,
              metadata={"source": s.source})
    db.commit()
    return _screening_out(s)


@router.get("/{challenge_id}/screenings", response_model=list[ScreeningOut])
def screening_history(challenge_id: int, db: DbDep, user: OptionalUser = None):
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    privileged = bool(user and user.role in {Role.ADMIN, *Role.UNIVERSITY_ROLES})
    if not (privileged or (user and user.id == c.citizen_id)):
        raise HTTPException(status_code=403, detail="Not permitted.")
    rows = (
        db.query(ChallengeScreening)
        .filter(ChallengeScreening.challenge_id == c.id)
        .order_by(ChallengeScreening.id.desc())
        .all()
    )
    return [_screening_out(s) for s in rows]


# ---------------------------------------------------------------------------
# Correcting the AI summary (stored separately from the model output)
# ---------------------------------------------------------------------------

@router.get("/{challenge_id}/corrections", response_model=list[ScreeningCorrectionOut])
def list_corrections(challenge_id: int, db: DbDep, user: OptionalUser = None):
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    privileged = bool(user and user.role in {Role.ADMIN, *Role.UNIVERSITY_ROLES})
    if not (privileged or (user and user.id == c.citizen_id)):
        raise HTTPException(status_code=403, detail="Not permitted.")
    rows = (
        db.query(ChallengeScreeningCorrection)
        .filter(ChallengeScreeningCorrection.challenge_id == c.id)
        .order_by(ChallengeScreeningCorrection.id.asc())
        .all()
    )
    out = []
    for r in rows:
        o = ScreeningCorrectionOut.model_validate(r)
        o.corrected_by_name = r.corrected_by.full_name if r.corrected_by else None
        out.append(o)
    return out


@router.post("/{challenge_id}/corrections", response_model=ScreeningCorrectionOut)
def correct_screening(challenge_id: int, payload: ScreeningCorrectionIn, db: DbDep,
                      user: CurrentUser, csrf: CsrfDep):
    """Record a human correction to the AI/rule summary.

    The model's original output is left untouched; the correction is a new row, so
    both the machine output and the human fix remain visible in the history.
    """
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    privileged = user.role in {Role.ADMIN, *Role.UNIVERSITY_ROLES}
    if not (privileged or c.citizen_id == user.id):
        raise HTTPException(status_code=403, detail="You cannot correct this summary.")
    if payload.field not in CORRECTABLE_FIELDS:
        raise HTTPException(status_code=422, detail="That field cannot be corrected.")

    latest = _latest_screening(db, c.id)
    if latest is None:
        raise HTTPException(status_code=409, detail="There is no AI summary to correct yet.")

    original = getattr(latest, payload.field, None)
    if payload.field == "possible_causes":
        original = latest.possible_causes
    if payload.field == "skills_required":
        original = latest.suggested_skills

    row = ChallengeScreeningCorrection(
        challenge_id=c.id,
        screening_id=latest.id,
        field=payload.field,
        original_value=original,
        corrected_value=payload.corrected_value,
        note=payload.note,
        corrected_by_id=user.id,
    )
    db.add(row)
    latest.review_status = "corrected_by_human"
    audit.log(db, user, "challenge.analysis_corrected", target_type="challenge", target_id=c.id,
              reason=payload.note, metadata={"field": payload.field})

    # Tell the citizen when a reviewer corrects their report's summary. A citizen
    # correcting their own summary does not need a notification.
    if privileged and c.citizen_id != user.id:
        notification_service.notify(
            db, c.citizen_id,
            "The summary of your report was corrected",
            f"A reviewer corrected the \"{payload.field.replace('_', ' ')}\" section of the "
            f"analysis for \"{c.title}\".",
            kind="review", link=f"/challenge.html?id={c.id}",
        )
    db.commit()
    db.refresh(row)
    out = ScreeningCorrectionOut.model_validate(row)
    out.corrected_by_name = user.full_name
    return out


# ---------------------------------------------------------------------------
# Human review (the authoritative decision)
# ---------------------------------------------------------------------------

@router.post("/{challenge_id}/review", response_model=ReviewOut)
def review_challenge(challenge_id: int, payload: ReviewIn, db: DbDep,
                     user: CurrentUser, csrf: CsrfDep):
    require_reviewer(user)
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    if c.status in {ChallengeStatus.IN_PROJECT, ChallengeStatus.COMPLETED}:
        raise HTTPException(status_code=409, detail="This challenge is already running as a project.")

    latest = _latest_screening(db, c.id)
    overrides_ai = bool(
        latest and latest.recommendation in {"approve", "exclude", "decline"}
        and latest.recommendation != payload.decision
    )

    review = ChallengeReview(
        challenge_id=c.id,
        reviewer_id=user.id,
        decision=payload.decision,
        reason=payload.reason,
        overrides_ai=overrides_ai,
    )
    db.add(review)

    c.status = payload.decision
    if latest is not None:
        latest.review_status = "human_reviewed"

    notification_service.notify(
        db, c.citizen_id,
        f"Update on your problem: {c.title}",
        f"A reviewer recorded a decision: {payload.decision.replace('_', ' ')}. "
        f"Reason: {payload.reason}",
        kind="review", link=f"/challenge.html?id={c.id}",
    )
    audit.log(db, user, "challenge.reviewed", target_type="challenge", target_id=c.id,
              reason=payload.reason, metadata={"decision": payload.decision, "overrides_ai": overrides_ai})
    db.commit()
    db.refresh(review)

    out = ReviewOut.model_validate(review)
    out.reviewer_name = user.full_name
    return out


@router.get("/{challenge_id}/reviews", response_model=list[ReviewOut])
def challenge_reviews(challenge_id: int, db: DbDep, user: OptionalUser = None):
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    privileged = bool(user and user.role in {Role.ADMIN, *Role.UNIVERSITY_ROLES})
    if not (privileged or (user and user.id == c.citizen_id)):
        raise HTTPException(status_code=403, detail="Not permitted.")
    rows = (
        db.query(ChallengeReview)
        .filter(ChallengeReview.challenge_id == c.id)
        .order_by(ChallengeReview.id.desc())
        .all()
    )
    out = []
    for r in rows:
        o = ReviewOut.model_validate(r)
        o.reviewer_name = r.reviewer.full_name if r.reviewer else None
        out.append(o)
    return out


# ---------------------------------------------------------------------------
# Clarifications
# ---------------------------------------------------------------------------

@router.post("/{challenge_id}/clarifications", response_model=ClarificationOut)
def ask_clarification(challenge_id: int, payload: ClarificationIn, db: DbDep,
                      user: CurrentUser, csrf: CsrfDep):
    require_reviewer(user)
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    req = ClarificationRequest(challenge_id=c.id, question=payload.question, asked_by_id=user.id)
    db.add(req)
    if c.status == ChallengeStatus.SUBMITTED:
        c.status = ChallengeStatus.NEEDS_CLARIFICATION
    notification_service.notify(
        db, c.citizen_id,
        "A reviewer needs more information",
        payload.question,
        kind="review", link=f"/challenge.html?id={c.id}",
    )
    db.commit()
    db.refresh(req)
    out = ClarificationOut.model_validate(req)
    out.asked_by_name = user.full_name
    return out


@router.post("/{challenge_id}/clarifications/{clarification_id}/answer", response_model=ClarificationOut)
def answer_clarification(challenge_id: int, clarification_id: int, payload: ClarificationAnswer,
                         db: DbDep, user: CurrentUser, csrf: CsrfDep):
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    require_citizen_challenge(user, c)
    req = db.get(ClarificationRequest, clarification_id)
    if not req or req.challenge_id != c.id:
        raise HTTPException(status_code=404, detail="Clarification request not found.")
    req.answer = payload.answer
    req.answered_by_id = user.id
    req.answered_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if c.status == ChallengeStatus.NEEDS_CLARIFICATION:
        c.status = ChallengeStatus.PENDING_REVIEW
    db.commit()
    db.refresh(req)
    out = ClarificationOut.model_validate(req)
    out.asked_by_name = req.asked_by.full_name if req.asked_by else None
    out.answered_by_name = user.full_name
    return out


@router.get("/{challenge_id}/clarifications", response_model=list[ClarificationOut])
def list_clarifications(challenge_id: int, db: DbDep, user: OptionalUser = None):
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    privileged = bool(user and user.role in {Role.ADMIN, *Role.UNIVERSITY_ROLES})
    if not (privileged or (user and user.id == c.citizen_id)):
        raise HTTPException(status_code=403, detail="Not permitted.")
    rows = (
        db.query(ClarificationRequest)
        .filter(ClarificationRequest.challenge_id == c.id)
        .order_by(ClarificationRequest.id.asc())
        .all()
    )
    out = []
    for r in rows:
        o = ClarificationOut.model_validate(r)
        o.asked_by_name = r.asked_by.full_name if r.asked_by else None
        o.answered_by_name = r.answered_by.full_name if r.answered_by else None
        out.append(o)
    return out


# ---------------------------------------------------------------------------
# University acceptance
# ---------------------------------------------------------------------------

@router.post("/{challenge_id}/accept", response_model=ChallengeOut)
def accept_challenge(challenge_id: int, db: DbDep, user: CurrentUser, csrf: CsrfDep):
    """A university accepts an approved challenge, moving it toward a project."""
    if user.role not in {Role.ADMIN, *Role.UNIVERSITY_ROLES}:
        raise HTTPException(status_code=403, detail="Only university staff can accept a challenge.")
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    if c.status != ChallengeStatus.APPROVED:
        raise HTTPException(
            status_code=409,
            detail="Only challenges approved for student innovation can be accepted.",
        )
    c.status = ChallengeStatus.ACCEPTED
    notification_service.notify(
        db, c.citizen_id,
        "Your problem has been accepted by a university",
        "A university has accepted your problem and will form a student team project.",
        kind="review", link=f"/challenge.html?id={c.id}",
    )
    audit.log(db, user, "challenge.accepted", target_type="challenge", target_id=c.id)
    db.commit()
    db.refresh(c)
    return challenge_out(db, c, user=user)


# ---------------------------------------------------------------------------
# Related challenges (city-scoped)
# ---------------------------------------------------------------------------

@router.get("/{challenge_id}/related")
def related_challenges(challenge_id: int, db: DbDep, user: OptionalUser = None,
                       refresh: bool = Query(default=False),
                       use_ai: bool = Query(default=False)):
    """Possible duplicates/related reports, filtered to the same city."""
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")

    found = duplicate_service.related_for_challenge(db, c, limit=8, use_ai=use_ai)

    if refresh and found["candidates"]:
        duplicate_service.link_related(db, c, found["candidates"])
        db.commit()

    items = []
    for cand in found["candidates"]:
        items.append({
            "challenge_id": cand["challenge_id"],
            "title": cand["title"],
            "category": cand["category"],
            "city": cand["city"],
            "locality": cand["locality"],
            "status": cand["status"],
            "similarity": cand["similarity"],
            "shared_terms": cand["shared_terms"],
            "reasons": cand["reasons"],
            "source": found["source"],
            "ai_verdict": cand.get("ai_verdict"),
            "ai_assessment": cand.get("ai_assessment"),
            "is_duplicate_candidate": cand["likely_duplicate"],
        })
    items.sort(key=lambda x: x["similarity"], reverse=True)
    return {
        "challenge_id": c.id,
        "city_key": found["city_key"],
        "compared_against": found["compared_against"],
        "related": items,
        "group_key": c.group_key,
        "method": found["method"],
        "note": found["note"],
    }


# ---------------------------------------------------------------------------
# Prior-solution search for a challenge
# ---------------------------------------------------------------------------

@router.get("/{challenge_id}/solutions")
def challenge_solutions(challenge_id: int, db: DbDep, user: OptionalUser = None,
                        limit: int = Query(default=6, ge=1, le=20),
                        use_ai: bool = Query(default=True)):
    c = db.get(Challenge, challenge_id)
    if not c:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    privileged = bool(user and user.role in {Role.ADMIN, *Role.UNIVERSITY_ROLES})
    if c.status in ChallengeStatus.BLOCKED and not (privileged or (user and user.id == c.citizen_id)):
        raise HTTPException(status_code=404, detail="Challenge not found.")
    return solution_service.find_solution_matches(db, c, limit=limit, use_ai=use_ai)
