"""SQLAlchemy models for SamadhanSetu.

Logical entities (per project spec §21) are implemented here; a few tightly
related concepts share a table where that simplifies the schema without losing
functionality (e.g. AI screening results + human decisions are separate tables,
related-challenge links carry their own similarity rows).
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Enumerations (kept as module-level constant containers; stored as strings)
# ---------------------------------------------------------------------------

class Role:
    CITIZEN = "citizen"
    STUDENT = "student"
    FACULTY = "faculty"
    UNIVERSITY_ADMIN = "university_admin"
    COMPANY = "company"
    ADMIN = "admin"

    ALL = {CITIZEN, STUDENT, FACULTY, UNIVERSITY_ADMIN, COMPANY, ADMIN}


class ChallengeStatus:
    SUBMITTED = "submitted"
    NEEDS_CLARIFICATION = "needs_clarification"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"                     # Approved for Student Innovation
    EXCLUDED_GOV = "excluded_government"      # Excluded — Government-Dependent
    DECLINED = "declined"                     # Declined — Not Suitable
    ACCEPTED = "accepted"                     # Accepted by University
    IN_PROJECT = "in_project"
    COMPLETED = "completed"

    ALL = {
        SUBMITTED, NEEDS_CLARIFICATION, PENDING_REVIEW, APPROVED, EXCLUDED_GOV,
        DECLINED, ACCEPTED, IN_PROJECT, COMPLETED,
    }
    # Statuses that must never enter the student-project pipeline
    BLOCKED = {EXCLUDED_GOV, DECLINED}


class Recommendation:
    REUSE = "reuse"
    ADAPT = "adapt"
    BUILD_NEW = "build_new"


class TaskStatus:
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    UNDER_REVIEW = "under_review"
    COMPLETED = "completed"
    ALL = {TODO, IN_PROGRESS, UNDER_REVIEW, COMPLETED}


class MilestoneStatus:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted_for_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    OVERDUE = "overdue"
    ALL = {PENDING, IN_PROGRESS, SUBMITTED, CHANGES_REQUESTED, APPROVED, OVERDUE}


class SponsorshipStatus:
    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    ACCEPTED = "accepted"
    COMMITTED = "committed"
    DISBURSED = "disbursed"
    DECLINED = "declined"
    ALL = {PROPOSED, UNDER_REVIEW, ACCEPTED, COMMITTED, DISBURSED, DECLINED}


class VerificationStatus:
    PROPOSED = "proposed"
    DEMONSTRATED = "demonstrated"
    VERIFIED = "verified"
    LONG_TERM = "long_term_unestablished"
    ALL = {PROPOSED, DEMONSTRATED, VERIFIED, LONG_TERM}


CATEGORIES = [
    "water", "electricity", "transport", "agriculture", "waste",
    "health", "education", "safety", "accessibility", "environment", "other",
]

# Citizen-facing domains (label + the stored category key it maps to).
# Kept next to CATEGORIES so the API, the seed and the UI never drift apart.
DOMAINS: list[tuple[str, str, str, str]] = [
    # (key, label, icon, one-line help text)
    ("water", "Water and sanitation", "🚰", "Supply, quality, drains, sewage, flooding"),
    ("agriculture", "Agriculture", "🌾", "Crops, irrigation, soil, livestock, farm income"),
    ("environment", "Environment and pollution", "🌫️", "Air quality, noise, emissions, climate"),
    ("waste", "Waste and recycling", "♻️", "Garbage, plastic, segregation, collection"),
    ("transport", "Transportation", "🚌", "Buses, roads, traffic, last-mile access"),
    ("education", "Education", "🎓", "Schools, learning, libraries, access to study"),
    ("health", "Healthcare", "🩺", "Clinics, disease, hygiene, mental health"),
    ("electricity", "Energy and electricity", "💡", "Power cuts, lighting, solar, wiring safety"),
    ("safety", "Public safety", "🛡️", "Accidents, hazards, crime, emergency response"),
    ("accessibility", "Accessibility", "♿", "Disability access, inclusive design, signage"),
    ("safety", "Disaster preparedness", "🌊", "Flood, fire, heat, early warning"),
    ("other", "Community infrastructure", "🏘️", "Shared facilities and public spaces"),
    ("other", "Other", "✳️", "Something that does not fit the list above"),
]

# Simple impact types a citizen can pick without inventing numbers.
IMPACT_KINDS: dict[str, str] = {
    "people_affected": "People affected",
    "financial_loss": "Estimated financial loss",
    "time_lost": "Time lost",
    "area_affected": "Area affected",
    "environmental": "Environmental impact",
    "unknown": "Other / Not sure",
}

DIFFICULTIES = ["beginner", "intermediate", "advanced"]


# ---------------------------------------------------------------------------
# 1. User + auth session
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    bio: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)  # org verification (university/company)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    student_profile: Mapped[Optional["StudentProfile"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan")
    faculty_profile: Mapped[Optional["FacultyProfile"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan")
    company: Mapped[Optional["Company"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan")
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")


class AuthSession(Base):
    """Server-side session record; the cookie holds a random opaque token."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))

    user: Mapped["User"] = relationship(backref="sessions")


# ---------------------------------------------------------------------------
# 2-5. Profiles, university, department, company
# ---------------------------------------------------------------------------

class University(Base):
    __tablename__ = "universities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[Optional[str]] = mapped_column(String(80))
    city: Mapped[Optional[str]] = mapped_column(String(120))
    state: Mapped[Optional[str]] = mapped_column(String(120))
    website: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    expertise: Mapped[Optional[str]] = mapped_column(Text)  # comma separated research areas
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    departments: Mapped[list["Department"]] = relationship(
        back_populates="university", cascade="all, delete-orphan")


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)   # e.g. "EEE", "CSE"
    full_name: Mapped[Optional[str]] = mapped_column(String(200))
    expertise: Mapped[Optional[str]] = mapped_column(Text)           # comma separated keywords

    university: Mapped["University"] = relationship(back_populates="departments")


class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    university_id: Mapped[Optional[int]] = mapped_column(ForeignKey("universities.id", ondelete="SET NULL"))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    academic_year: Mapped[Optional[str]] = mapped_column(String(40))    # e.g. "3rd year"
    experience_level: Mapped[str] = mapped_column(String(20), default="beginner")  # beginner|intermediate|advanced
    skills: Mapped[Optional[str]] = mapped_column(Text)      # comma separated skill keywords
    interests: Mapped[Optional[str]] = mapped_column(Text)   # comma separated interests
    previous_projects: Mapped[Optional[str]] = mapped_column(Text)
    portfolio_url: Mapped[Optional[str]] = mapped_column(String(255))
    github_url: Mapped[Optional[str]] = mapped_column(String(255))
    certifications: Mapped[Optional[str]] = mapped_column(Text)
    availability_hours: Mapped[int] = mapped_column(Integer, default=10)  # hours/week
    preferred_role: Mapped[Optional[str]] = mapped_column(String(120))
    # Consent flags — companies only see students who opted in
    consent_portfolio_share: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_contact_share: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="student_profile")
    university: Mapped[Optional["University"]] = relationship()
    department: Mapped[Optional["Department"]] = relationship()


class FacultyProfile(Base):
    __tablename__ = "faculty_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    university_id: Mapped[Optional[int]] = mapped_column(ForeignKey("universities.id", ondelete="SET NULL"))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    title: Mapped[Optional[str]] = mapped_column(String(120))   # e.g. "Associate Professor"
    expertise: Mapped[Optional[str]] = mapped_column(Text)      # comma separated
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="faculty_profile")
    university: Mapped[Optional["University"]] = relationship()
    department: Mapped[Optional["Department"]] = relationship()


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(220), nullable=False)
    industry: Mapped[Optional[str]] = mapped_column(String(160))
    website: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship(back_populates="company")


# ---------------------------------------------------------------------------
# 8-11. Challenges, screening, reviews, related challenges, solutions
# ---------------------------------------------------------------------------

class Challenge(Base):
    __tablename__ = "challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    citizen_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Original citizen submission — preserved verbatim, never rewritten by AI
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="other", index=True)
    location: Mapped[Optional[str]] = mapped_column(String(220))
    affected_users: Mapped[Optional[str]] = mapped_column(Text)
    current_situation: Mapped[Optional[str]] = mapped_column(Text)
    desired_outcome: Mapped[Optional[str]] = mapped_column(Text)
    constraints: Mapped[Optional[str]] = mapped_column(Text)
    attachment_path: Mapped[Optional[str]] = mapped_column(String(255))

    # Structured location: ``city`` is the searchable city, ``locality`` the
    # optional area/ward. ``city_key`` is the normalised form used for
    # duplicate detection, so "Pune " and "pune," bucket together.
    city: Mapped[Optional[str]] = mapped_column(String(120))
    locality: Mapped[Optional[str]] = mapped_column(String(160))
    # Indexed through the composite ix_challenge_city_status below rather than a
    # second single-column index.
    city_key: Mapped[Optional[str]] = mapped_column(String(120))

    # Simple impact estimate. An estimate is NEVER treated as a verified loss.
    impact_kind: Mapped[Optional[str]] = mapped_column(String(40))
    impact_amount: Mapped[Optional[float]] = mapped_column(Float)
    impact_unit: Mapped[Optional[str]] = mapped_column(String(40))
    impact_currency: Mapped[Optional[str]] = mapped_column(String(10))
    impact_source: Mapped[Optional[str]] = mapped_column(String(160))
    impact_is_estimate: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("1"))
    impact_notes: Mapped[Optional[str]] = mapped_column(Text)

    # Idempotency key supplied by the reporting form so a double-tap cannot
    # create two identical submissions.
    client_request_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    status: Mapped[str] = mapped_column(String(40), default=ChallengeStatus.SUBMITTED, index=True)
    difficulty: Mapped[Optional[str]] = mapped_column(String(20))
    group_key: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # related-challenge cluster

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    citizen: Mapped["User"] = relationship()
    screenings: Mapped[list["ChallengeScreening"]] = relationship(
        back_populates="challenge", cascade="all, delete-orphan")
    reviews: Mapped[list["ChallengeReview"]] = relationship(
        back_populates="challenge", cascade="all, delete-orphan")
    clarifications: Mapped[list["ClarificationRequest"]] = relationship(
        back_populates="challenge", cascade="all, delete-orphan")
    attachments: Mapped[list["ChallengeAttachment"]] = relationship(
        back_populates="challenge", cascade="all, delete-orphan")
    project: Mapped[Optional["Project"]] = relationship(back_populates="challenge", uselist=False)

    @property
    def impact_label(self) -> Optional[str]:
        """Human phrase for the stored impact estimate, or None when unknown."""
        if not self.impact_kind or self.impact_kind == "unknown":
            return None
        label = IMPACT_KINDS.get(self.impact_kind, "Impact")
        if self.impact_amount is not None:
            amount = f"{self.impact_amount:,.0f}" if float(self.impact_amount).is_integer() else f"{self.impact_amount:,.2f}"
            unit = f" {self.impact_unit}" if self.impact_unit else ""
            currency = f"{self.impact_currency} " if self.impact_currency else ""
            return f"{label}: {currency}{amount}{unit}"
        return f"{label}: {self.impact_notes}" if self.impact_notes else label


class ChallengeScreening(Base):
    """AI / rules generated screening analysis — stored SEPARATELY from human decisions."""

    __tablename__ = "challenge_screenings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(20), default="rules")  # 'groq' | 'rules'
    model: Mapped[Optional[str]] = mapped_column(String(120))        # model id when source='groq'
    problem_summary: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(String(40))
    affected_users: Mapped[Optional[str]] = mapped_column(Text)
    technical_requirements: Mapped[Optional[str]] = mapped_column(Text)
    missing_information: Mapped[Optional[str]] = mapped_column(Text)   # JSON array as text
    government_dependency: Mapped[bool] = mapped_column(Boolean, default=False)
    student_project_suitability: Mapped[str] = mapped_column(String(20), default="uncertain")  # suitable|uncertain|unsuitable
    recommendation: Mapped[str] = mapped_column(String(20), default="human_review")  # approve|human_review|exclude|decline
    explanation: Mapped[Optional[str]] = mapped_column(Text)
    suggested_skills: Mapped[Optional[str]] = mapped_column(Text)  # comma separated
    clarification_questions: Mapped[Optional[str]] = mapped_column(Text)  # JSON array as text
    confidence: Mapped[Optional[float]] = mapped_column(Float)  # model self-report; NOT a verified probability

    # --- Citizen-friendly simplification (section 4 of the revision brief) ---
    one_sentence: Mapped[Optional[str]] = mapped_column(Text)        # "What is wrong?"
    simple_explanation: Mapped[Optional[str]] = mapped_column(Text)  # 2-3 everyday sentences
    who_is_affected: Mapped[Optional[str]] = mapped_column(Text)     # facts vs assumptions
    why_it_matters: Mapped[Optional[str]] = mapped_column(Text)
    impact_summary: Mapped[Optional[str]] = mapped_column(Text)
    possible_causes: Mapped[Optional[str]] = mapped_column(Text)     # JSON array as text
    potential_solution: Mapped[Optional[str]] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(
        String(40), default="ai_complete_awaiting_review", nullable=False,
        server_default="ai_complete_awaiting_review")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    challenge: Mapped["Challenge"] = relationship(back_populates="screenings")
    corrections: Mapped[list["ChallengeScreeningCorrection"]] = relationship(
        back_populates="screening", cascade="all, delete-orphan")


class ChallengeScreeningCorrection(Base):
    """A human correction to an AI/rule screening field.

    The original model output is never overwritten: the correction is stored as
    its own row so the audit trail keeps both the machine output and the human
    fix, with its own reason and author.
    """

    __tablename__ = "challenge_screening_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id", ondelete="CASCADE"), index=True)
    screening_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("challenge_screenings.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(60), nullable=False)
    original_value: Mapped[Optional[str]] = mapped_column(Text)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    corrected_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    screening: Mapped[Optional["ChallengeScreening"]] = relationship(back_populates="corrections")
    corrected_by: Mapped[Optional["User"]] = relationship()


class ChallengeAttachment(Base):
    """An uploaded piece of evidence (photo) for a challenge.

    Files are stored under ``backend/app/uploads/challenges/<challenge id>/`` with
    a generated name, are **private by default**, and are only served through an
    authorised endpoint — never from a public static directory.

    Evidence is uploaded while the citizen is still filling in the form, so a row
    may exist *before* the challenge does: such a row has ``challenge_id IS NULL``
    and is owned through ``staging_key`` (the reporting session's request id).
    On submit, the staged rows are claimed by the new challenge.
    """

    __tablename__ = "challenge_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"), index=True)
    staging_key: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    uploader_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False)   # generated, never trusted
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)  # display only, escaped
    content_type: Mapped[str] = mapped_column(String(60), nullable=False)    # sniffed server-side
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    metadata_stripped: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("0"))
    validation_note: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    challenge: Mapped[Optional["Challenge"]] = relationship(back_populates="attachments")
    uploader: Mapped[Optional["User"]] = relationship()


class ChallengeReview(Base):
    """Human reviewer decision — the authoritative record."""

    __tablename__ = "challenge_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    decision: Mapped[str] = mapped_column(String(40), nullable=False)  # one of ChallengeStatus values or 'needs_clarification'
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    overrides_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    challenge: Mapped["Challenge"] = relationship(back_populates="reviews")
    reviewer: Mapped["User"] = relationship()


class ClarificationRequest(Base):
    __tablename__ = "clarification_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    asked_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    answer: Mapped[Optional[str]] = mapped_column(Text)
    answered_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    answered_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

    challenge: Mapped["Challenge"] = relationship(back_populates="clarifications")
    asked_by: Mapped[Optional["User"]] = relationship(foreign_keys=[asked_by_id])
    answered_by: Mapped[Optional["User"]] = relationship(foreign_keys=[answered_by_id])


class RelatedChallenge(Base):
    __tablename__ = "related_challenges"
    __table_args__ = (
        UniqueConstraint("challenge_id", "related_challenge_id", name="uq_related_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id", ondelete="CASCADE"), index=True)
    related_challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id", ondelete="CASCADE"), index=True)
    similarity: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(20), default="rules")  # rules|groq
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class SolutionResource(Base):
    __tablename__ = "solution_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(500))
    source_label: Mapped[Optional[str]] = mapped_column(String(220))  # e.g. "Internal project archive"
    technologies: Mapped[Optional[str]] = mapped_column(Text)         # comma separated
    license_info: Mapped[Optional[str]] = mapped_column(String(220))  # only when verified
    limitations: Mapped[Optional[str]] = mapped_column(Text)
    keywords: Mapped[Optional[str]] = mapped_column(Text)             # retrieval keywords
    origin_project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    review_status: Mapped[str] = mapped_column(String(30), default="unreviewed")  # unreviewed|mentor_verified|rejected
    added_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    added_by: Mapped[Optional["User"]] = relationship()
    origin_project: Mapped[Optional["Project"]] = relationship()


# ---------------------------------------------------------------------------
# 13-20. Projects, members, responsibility forms, tasks, milestones, evidence,
#         mentor reviews, weekly reports
# ---------------------------------------------------------------------------

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge_id: Mapped[Optional[int]] = mapped_column(ForeignKey("challenges.id", ondelete="SET NULL"), index=True)
    university_id: Mapped[Optional[int]] = mapped_column(ForeignKey("universities.id", ondelete="SET NULL"))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    mentor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    problem_statement: Mapped[str] = mapped_column(Text, nullable=False)
    objectives: Mapped[Optional[str]] = mapped_column(Text)
    scope: Mapped[Optional[str]] = mapped_column(Text)
    deliverables: Mapped[Optional[str]] = mapped_column(Text)
    required_skills: Mapped[Optional[str]] = mapped_column(Text)        # comma separated
    required_disciplines: Mapped[Optional[str]] = mapped_column(Text)   # e.g. "EEE,CSE"
    expected_team_size: Mapped[int] = mapped_column(Integer, default=4)
    difficulty: Mapped[str] = mapped_column(String(20), default="intermediate")
    status: Mapped[str] = mapped_column(String(30), default="planning", index=True)
    required_resources: Mapped[Optional[str]] = mapped_column(Text)
    evaluation_criteria: Mapped[Optional[str]] = mapped_column(Text)
    privacy_constraints: Mapped[Optional[str]] = mapped_column(Text)
    ip_terms: Mapped[Optional[str]] = mapped_column(Text)  # IP / licensing / publication conditions
    start_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    deadline: Mapped[Optional[dt.date]] = mapped_column(Date, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    challenge: Mapped[Optional["Challenge"]] = relationship(back_populates="project")
    university: Mapped[Optional["University"]] = relationship()
    creator: Mapped["User"] = relationship(foreign_keys=[created_by_id])
    mentor: Mapped[Optional["User"]] = relationship(foreign_keys=[mentor_id])
    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    milestones: Mapped[list["Milestone"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    team_role: Mapped[str] = mapped_column(String(120), default="member")  # student role on team
    status: Mapped[str] = mapped_column(String(30), default="active")      # invited|active|declined|removed
    match_score: Mapped[Optional[float]] = mapped_column(Float)
    match_reason: Mapped[Optional[str]] = mapped_column(Text)   # explainable recommendation text
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)  # faculty approval
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    joined_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    project: Mapped["Project"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    approved_by: Mapped[Optional["User"]] = relationship(foreign_keys=[approved_by_id])
    responsibility_form: Mapped[Optional["ResponsibilityForm"]] = relationship(
        back_populates="member", uselist=False, cascade="all, delete-orphan")


class ResponsibilityForm(Base):
    __tablename__ = "responsibility_forms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("project_members.id", ondelete="CASCADE"), unique=True)
    version: Mapped[str] = mapped_column(String(20), default="1.0")
    role: Mapped[str] = mapped_column(String(120), default="member")
    responsibilities: Mapped[Optional[str]] = mapped_column(Text)
    expectations: Mapped[Optional[str]] = mapped_column(Text)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    snapshot: Mapped[Optional[str]] = mapped_column(Text)  # JSON snapshot of form contents at acceptance

    project: Mapped["Project"] = relationship()
    member: Mapped["ProjectMember"] = relationship(back_populates="responsibility_form")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    priority: Mapped[str] = mapped_column(String(20), default="medium")  # low|medium|high|critical
    status: Mapped[str] = mapped_column(String(30), default=TaskStatus.TODO, index=True)
    start_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    due_date: Mapped[Optional[dt.date]] = mapped_column(Date, index=True)
    evidence_link: Mapped[Optional[str]] = mapped_column(String(500))
    review_comment: Mapped[Optional[str]] = mapped_column(Text)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    project: Mapped["Project"] = relationship(back_populates="tasks")
    assignee: Mapped[Optional["User"]] = relationship(foreign_keys=[assignee_id])
    creator: Mapped[Optional["User"]] = relationship(foreign_keys=[created_by_id])

    @property
    def is_overdue(self) -> bool:
        return bool(self.due_date) and self.status != TaskStatus.COMPLETED and self.due_date < dt.date.today()


class Milestone(Base):
    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    due_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    expected_deliverables: Mapped[Optional[str]] = mapped_column(Text)
    responsible_ids: Mapped[Optional[str]] = mapped_column(Text)  # comma separated user ids
    evidence_link: Mapped[Optional[str]] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default=MilestoneStatus.PENDING, index=True)
    mentor_comment: Mapped[Optional[str]] = mapped_column(Text)
    submitted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    project: Mapped["Project"] = relationship(back_populates="milestones")
    reviewer: Mapped[Optional["User"]] = relationship(foreign_keys=[reviewed_by_id])


class ProjectEvidence(Base):
    __tablename__ = "project_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    uploader_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(40), default="document")  # document|photo|video|code|dataset|other
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    uploader: Mapped[Optional["User"]] = relationship()


class MentorReview(Base):
    __tablename__ = "mentor_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    milestone_id: Mapped[Optional[int]] = mapped_column(ForeignKey("milestones.id", ondelete="SET NULL"))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    verdict: Mapped[str] = mapped_column(String(30), default="neutral")  # approved|changes_requested|neutral
    rating: Mapped[Optional[int]] = mapped_column(Integer)               # 1-5
    comments: Mapped[str] = mapped_column(Text, default="")
    risks: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    reviewer: Mapped["User"] = relationship()


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    week_of: Mapped[dt.date] = mapped_column(Date, default=lambda: dt.date.today())
    completed_work: Mapped[str] = mapped_column(Text, default="")
    current_progress: Mapped[str] = mapped_column(Text, default="")
    blockers: Mapped[Optional[str]] = mapped_column(Text)
    next_steps: Mapped[Optional[str]] = mapped_column(Text)
    help_required: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    student: Mapped["User"] = relationship()


# ---------------------------------------------------------------------------
# 21-24. Competitions, registrations, submissions, judge scores
# ---------------------------------------------------------------------------

DEFAULT_CRITERIA = [
    {"key": "technical", "label": "Technical Quality and Reliability", "weight": 25},
    {"key": "innovation", "label": "Innovation and Problem-Solving", "weight": 25},
    {"key": "execution", "label": "Milestone Completion and Execution", "weight": 20},
    {"key": "usability", "label": "Usability and Community Feedback", "weight": 15},
    {"key": "impact", "label": "Documented, Verified Impact", "weight": 15},
]


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(40), default="cross_university")  # within_university|cross_university
    university_id: Mapped[Optional[int]] = mapped_column(ForeignKey("universities.id", ondelete="SET NULL"))
    eligibility_rules: Mapped[Optional[str]] = mapped_column(Text)
    registration_deadline: Mapped[Optional[dt.date]] = mapped_column(Date)
    submission_deadline: Mapped[Optional[dt.date]] = mapped_column(Date, index=True)
    starts_on: Mapped[Optional[dt.date]] = mapped_column(Date)
    ends_on: Mapped[Optional[dt.date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="upcoming", index=True)  # upcoming|active|completed
    criteria: Mapped[Optional[str]] = mapped_column(Text)   # JSON array [{key,label,weight}]
    results_published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class CompetitionRegistration(Base):
    __tablename__ = "competition_registrations"
    __table_args__ = (UniqueConstraint("competition_id", "project_id", name="uq_registration"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    registered_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="registered")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    submitted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    demo_link: Mapped[Optional[str]] = mapped_column(String(500))
    documentation_link: Mapped[Optional[str]] = mapped_column(String(500))
    repository_link: Mapped[Optional[str]] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="submitted")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)

    scores: Mapped[list["JudgeScore"]] = relationship(back_populates="submission", cascade="all, delete-orphan")


class JudgeScore(Base):
    __tablename__ = "judge_scores"
    __table_args__ = (UniqueConstraint("submission_id", "judge_id", "criterion", name="uq_score"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"), index=True)
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    criterion: Mapped[str] = mapped_column(String(60), nullable=False)   # criteria key
    score: Mapped[float] = mapped_column(Float, nullable=False)          # 0..10 (scaled to 100)
    feedback: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    submission: Mapped["Submission"] = relationship(back_populates="scores")
    judge: Mapped["User"] = relationship()


# ---------------------------------------------------------------------------
# 25-28. Sponsorship, notification, audit log, impact report
# ---------------------------------------------------------------------------

class Sponsorship(Base):
    __tablename__ = "sponsorships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="funding")  # funding|equipment|software|dataset|cloud|mentorship
    amount: Mapped[Optional[float]] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), default="INR")
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default=SponsorshipStatus.PROPOSED, index=True)
    status_note: Mapped[Optional[str]] = mapped_column(Text)  # reason / review note on status change
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    company: Mapped["Company"] = relationship()
    project: Mapped["Project"] = relationship()


class TalentInvitation(Base):
    __tablename__ = "talent_invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="internship")  # internship|recruitment
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="sent")      # sent|accepted|declined|withdrawn
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    company: Mapped["Company"] = relationship()
    student: Mapped["User"] = relationship()


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40), default="info")  # info|task|deadline|review|invitation|sponsorship|risk
    link: Mapped[Optional[str]] = mapped_column(String(255))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)

    user: Mapped["User"] = relationship(back_populates="notifications")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[Optional[str]] = mapped_column(String(60))
    target_id: Mapped[Optional[str]] = mapped_column(String(60))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)

    actor: Mapped[Optional["User"]] = relationship()


class ImpactReport(Base):
    __tablename__ = "impact_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    original_problem: Mapped[Optional[str]] = mapped_column(Text)
    objectives: Mapped[Optional[str]] = mapped_column(Text)
    approach: Mapped[Optional[str]] = mapped_column(Text)
    prior_solutions_considered: Mapped[Optional[str]] = mapped_column(Text)
    reused_or_modified: Mapped[Optional[str]] = mapped_column(Text)
    tech_stack: Mapped[Optional[str]] = mapped_column(Text)
    prototype: Mapped[Optional[str]] = mapped_column(Text)
    testing_evidence: Mapped[Optional[str]] = mapped_column(Text)
    limitations: Mapped[Optional[str]] = mapped_column(Text)
    actual_results: Mapped[Optional[str]] = mapped_column(Text)
    community_feedback: Mapped[Optional[str]] = mapped_column(Text)
    cost_info: Mapped[Optional[str]] = mapped_column(String(220))
    verification_status: Mapped[str] = mapped_column(String(40), default=VerificationStatus.PROPOSED)
    follow_up: Mapped[Optional[str]] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    author: Mapped[Optional["User"]] = relationship()


Index("ix_challenge_status_category", Challenge.status, Challenge.category)
Index("ix_task_project_status", Task.project_id, Task.status)
Index("ix_project_status_deadline", Project.status, Project.deadline)
Index("ix_challenge_city_status", Challenge.city_key, Challenge.status)
