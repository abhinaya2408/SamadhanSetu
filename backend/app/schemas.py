"""Pydantic schemas for request validation and response serialisation."""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=200)
    role: str
    # Optional role-specific onboarding fields
    university_id: Optional[int] = None
    department_id: Optional[int] = None
    company_name: Optional[str] = None
    company_industry: Optional[str] = None
    company_website: Optional[str] = None
    faculty_title: Optional[str] = None

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        from .models import Role
        if v not in Role.ALL:
            raise ValueError(f"Unsupported role: {v}")
        # Platform administrator privileges must NEVER be granted by public registration
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(ORMModel):
    id: int
    email: EmailStr
    full_name: str
    role: str
    bio: Optional[str] = None
    is_verified: bool = False
    created_at: Optional[dt.datetime] = None


class UserPatch(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    bio: Optional[str] = None


class StudentProfileOut(ORMModel):
    id: int
    user_id: int
    university_id: Optional[int] = None
    department_id: Optional[int] = None
    academic_year: Optional[str] = None
    experience_level: str = "beginner"
    skills: Optional[str] = None
    interests: Optional[str] = None
    previous_projects: Optional[str] = None
    portfolio_url: Optional[str] = None
    github_url: Optional[str] = None
    certifications: Optional[str] = None
    availability_hours: int = 10
    preferred_role: Optional[str] = None
    consent_portfolio_share: bool = False
    consent_contact_share: bool = False


class StudentProfilePatch(BaseModel):
    university_id: Optional[int] = None
    department_id: Optional[int] = None
    academic_year: Optional[str] = Field(default=None, max_length=40)
    experience_level: Optional[str] = None
    skills: Optional[str] = None
    interests: Optional[str] = None
    previous_projects: Optional[str] = None
    portfolio_url: Optional[str] = Field(default=None, max_length=255)
    github_url: Optional[str] = Field(default=None, max_length=255)
    certifications: Optional[str] = None
    availability_hours: Optional[int] = Field(default=None, ge=0, le=80)
    preferred_role: Optional[str] = Field(default=None, max_length=120)
    consent_portfolio_share: Optional[bool] = None
    consent_contact_share: Optional[bool] = None

    @field_validator("experience_level")
    @classmethod
    def _lvl(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in {"beginner", "intermediate", "advanced"}:
            raise ValueError("experience_level must be beginner, intermediate or advanced")
        return v


class StudentOut(BaseModel):
    """Listing view for authorised users. Private contact details are never included."""
    id: int
    full_name: str
    department: Optional[str] = None
    university: Optional[str] = None
    academic_year: Optional[str] = None
    experience_level: str = "beginner"
    skills: Optional[str] = None
    interests: Optional[str] = None
    availability_hours: int = 10
    preferred_role: Optional[str] = None
    portfolio_url: Optional[str] = None
    github_url: Optional[str] = None
    consent_portfolio_share: bool = False
    active_projects: int = 0


# ---------------------------------------------------------------------------
# Challenges
# ---------------------------------------------------------------------------

class ImpactIn(BaseModel):
    """Simple, optional impact estimate. Everything may be left unknown."""
    kind: str = "unknown"
    amount: Optional[float] = Field(default=None, ge=0, le=1_000_000_000)
    unit: Optional[str] = Field(default=None, max_length=40)
    currency: Optional[str] = Field(default=None, max_length=10)
    note: Optional[str] = Field(default=None, max_length=500)
    source: Optional[str] = Field(default=None, max_length=160)
    is_estimate: bool = True

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        from .models import IMPACT_KINDS
        v = (v or "unknown").lower().strip()
        return v if v in IMPACT_KINDS else "unknown"


class ChallengeIn(BaseModel):
    """The citizen reporting form.

    Only ``description`` and ``category`` are truly required; everything else may
    be omitted. The description is stored verbatim — AI never rewrites it.
    """
    description: str = Field(min_length=20, max_length=8000)
    category: str = "other"
    # Title is now optional: it is derived from the description when omitted so a
    # citizen is not forced to invent one.
    title: Optional[str] = Field(default=None, max_length=255)

    city: Optional[str] = Field(default=None, max_length=120)
    locality: Optional[str] = Field(default=None, max_length=160)
    location: Optional[str] = Field(default=None, max_length=220)

    impact: Optional[ImpactIn] = None

    affected_users: Optional[str] = Field(default=None, max_length=1000)
    current_situation: Optional[str] = Field(default=None, max_length=3000)
    desired_outcome: Optional[str] = Field(default=None, max_length=3000)
    constraints: Optional[str] = Field(default=None, max_length=3000)

    # Idempotency key generated by the browser for one reporting session.
    client_request_id: Optional[str] = Field(default=None, max_length=64)

    @field_validator("category")
    @classmethod
    def _cat(cls, v: str) -> str:
        from .models import CATEGORIES
        v = (v or "other").lower().strip()
        return v if v in CATEGORIES else "other"

    @field_validator("client_request_id")
    @classmethod
    def _rid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not all(c.isalnum() or c in "-_." for c in v):
            raise ValueError("client_request_id may only contain letters, digits, - _ and .")
        return v


class SimplifiedAnalysis(BaseModel):
    """The citizen-facing simplification of a report (brief section 4).

    Each field maps to one of the numbered items in the brief so the frontend
    can render them without guessing, and so model output is validated before it
    is stored or shown.
    """
    one_sentence: str = ""                      # 1. What is wrong
    simple_explanation: str = ""                # 2. Everyday-English explanation
    who_is_affected: str = ""                   # 3. Facts vs assumptions
    why_it_matters: str = ""                    # 4. Practical consequence
    impact_summary: str = ""                    # 5. Labelled estimate
    possible_causes: list[str] = Field(default_factory=list)     # 6. Hypotheses only
    potential_solution: str = ""                # 7. One or two feasible approaches
    skills_required: list[str] = Field(default_factory=list)     # 8. Skills
    missing_information: list[str] = Field(default_factory=list)  # 9. Max two questions
    government_dependency: bool = False         # 10. Needs official action?
    confidence: Optional[float] = None          # 11. Self-reported, not calibrated
    review_status: str = "ai_complete_awaiting_review"           # 12. AI vs human
    source: str = "rules"                       # 'groq' | 'rules'
    model: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("missing_information")
    @classmethod
    def _at_most_two(cls, v: list[str]) -> list[str]:
        return [str(x)[:300] for x in v if str(x).strip()][:2]

    @field_validator("possible_causes")
    @classmethod
    def _cap_causes(cls, v: list[str]) -> list[str]:
        return [str(x)[:300] for x in v if str(x).strip()][:4]

    @field_validator("confidence")
    @classmethod
    def _conf(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        return round(min(1.0, max(0.0, float(v))), 2)


class DuplicateCandidate(BaseModel):
    challenge_id: int
    title: str
    category: str
    city: Optional[str] = None
    locality: Optional[str] = None
    status: str
    similarity: float
    text_similarity: float = 0.0
    shared_terms: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    same_city: bool = False
    same_locality: bool = False
    same_domain: bool = False
    likely_duplicate: bool = False
    ai_assessment: Optional[str] = None
    ai_verdict: Optional[str] = None            # duplicate | related | different | unknown


class DuplicateCheckIn(BaseModel):
    description: str = Field(min_length=10, max_length=8000)
    title: Optional[str] = Field(default=None, max_length=255)
    city: Optional[str] = Field(default=None, max_length=120)
    locality: Optional[str] = Field(default=None, max_length=160)
    category: str = "other"
    exclude_challenge_id: Optional[int] = None
    use_ai: bool = True


class DuplicateCheckOut(BaseModel):
    candidates: list[DuplicateCandidate] = Field(default_factory=list)
    city_key: Optional[str] = None
    compared_against: int = 0
    likely_duplicate_count: int = 0
    source: str = "rules"
    method: str = ""
    note: str = ""


class ChallengePreviewOut(BaseModel):
    """Result of the pre-submission "Analyze my problem" step. Nothing is saved."""
    persisted: bool = False
    analysis: SimplifiedAnalysis
    duplicates: DuplicateCheckOut
    derived_title: str = ""
    note: str = ""


class ScreeningCorrectionIn(BaseModel):
    field: str = Field(min_length=2, max_length=60)
    corrected_value: str = Field(min_length=1, max_length=2000)
    note: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("field")
    @classmethod
    def _known_field(cls, v: str) -> str:
        allowed = {
            "one_sentence", "simple_explanation", "who_is_affected", "why_it_matters",
            "impact_summary", "possible_causes", "potential_solution", "skills_required",
        }
        v = (v or "").strip()
        if v not in allowed:
            raise ValueError(f"field must be one of {sorted(allowed)}")
        return v


class ScreeningCorrectionOut(ORMModel):
    id: int
    field: str
    original_value: Optional[str] = None
    corrected_value: str
    note: Optional[str] = None
    corrected_by_name: Optional[str] = None
    created_at: dt.datetime


class AttachmentOut(ORMModel):
    id: int
    original_name: str
    content_type: str
    size_bytes: int
    metadata_stripped: bool = False
    validation_note: Optional[str] = None
    created_at: dt.datetime
    url: Optional[str] = None


class ScreeningOut(ORMModel):
    id: int
    source: str
    model: Optional[str] = None
    problem_summary: Optional[str] = None
    category: Optional[str] = None
    affected_users: Optional[str] = None
    technical_requirements: Optional[str] = None
    missing_information: Optional[str] = None
    government_dependency: bool = False
    student_project_suitability: str = "uncertain"
    recommendation: str = "human_review"
    explanation: Optional[str] = None
    suggested_skills: Optional[str] = None
    clarification_questions: Optional[str] = None
    confidence: Optional[float] = None
    one_sentence: Optional[str] = None
    simple_explanation: Optional[str] = None
    who_is_affected: Optional[str] = None
    why_it_matters: Optional[str] = None
    impact_summary: Optional[str] = None
    possible_causes: Optional[str] = None
    potential_solution: Optional[str] = None
    review_status: str = "ai_complete_awaiting_review"
    created_at: dt.datetime


class ReviewIn(BaseModel):
    decision: str
    reason: str = Field(min_length=10, max_length=2000)

    @field_validator("decision")
    @classmethod
    def _dec(cls, v: str) -> str:
        allowed = {
            "approved", "excluded_government", "declined",
            "needs_clarification", "pending_review",
        }
        if v not in allowed:
            raise ValueError(f"decision must be one of {sorted(allowed)}")
        return v


class ReviewOut(ORMModel):
    id: int
    decision: str
    reason: str
    overrides_ai: bool
    created_at: dt.datetime
    reviewer_name: Optional[str] = None


class ClarificationIn(BaseModel):
    question: str = Field(min_length=5, max_length=1000)


class ClarificationAnswer(BaseModel):
    answer: str = Field(min_length=2, max_length=2000)


class ClarificationOut(ORMModel):
    id: int
    question: str
    answer: Optional[str] = None
    created_at: dt.datetime
    answered_at: Optional[dt.datetime] = None
    asked_by_name: Optional[str] = None
    answered_by_name: Optional[str] = None


class ProjectOut(ORMModel):
    id: int
    challenge_id: Optional[int] = None
    university_id: Optional[int] = None
    mentor_id: Optional[int] = None
    title: str
    problem_statement: str
    objectives: Optional[str] = None
    scope: Optional[str] = None
    deliverables: Optional[str] = None
    required_skills: Optional[str] = None
    required_disciplines: Optional[str] = None
    expected_team_size: int = 4
    difficulty: str = "intermediate"
    status: str = "planning"
    start_date: Optional[dt.date] = None
    deadline: Optional[dt.date] = None
    required_resources: Optional[str] = None
    evaluation_criteria: Optional[str] = None
    privacy_constraints: Optional[str] = None
    ip_terms: Optional[str] = None
    created_at: Optional[dt.datetime] = None
    mentor_name: Optional[str] = None
    university_name: Optional[str] = None
    member_count: int = 0
    progress_pct: float = 0.0


class ChallengeOut(ORMModel):
    id: int
    citizen_id: int
    title: str
    description: str
    category: str
    location: Optional[str] = None
    city: Optional[str] = None
    locality: Optional[str] = None
    city_key: Optional[str] = None
    affected_users: Optional[str] = None
    current_situation: Optional[str] = None
    desired_outcome: Optional[str] = None
    constraints: Optional[str] = None
    impact_kind: Optional[str] = None
    impact_amount: Optional[float] = None
    impact_unit: Optional[str] = None
    impact_currency: Optional[str] = None
    impact_source: Optional[str] = None
    impact_is_estimate: bool = True
    impact_notes: Optional[str] = None
    impact_label: Optional[str] = None
    status: str
    difficulty: Optional[str] = None
    group_key: Optional[str] = None
    created_at: dt.datetime
    updated_at: Optional[dt.datetime] = None
    citizen_name: Optional[str] = None
    latest_screening: Optional[ScreeningOut] = None
    attachments: list[AttachmentOut] = Field(default_factory=list)
    project: Optional[ProjectOut] = None


class ChallengeDecisionOut(BaseModel):
    """Compact card view."""
    id: int
    title: str
    category: str
    status: str
    difficulty: Optional[str] = None
    created_at: dt.datetime
    location: Optional[str] = None
    has_project: bool = False
    government_dependency: Optional[bool] = None
    suitability: Optional[str] = None


# ---------------------------------------------------------------------------
# Projects, members, tasks, milestones
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    challenge_id: int
    title: str = Field(min_length=6, max_length=255)
    problem_statement: str = Field(min_length=20, max_length=6000)
    objectives: Optional[str] = None
    scope: Optional[str] = None
    deliverables: Optional[str] = None
    required_skills: Optional[str] = None
    required_disciplines: Optional[str] = None
    expected_team_size: int = Field(default=4, ge=1, le=20)
    difficulty: str = "intermediate"
    mentor_id: Optional[int] = None
    start_date: Optional[dt.date] = None
    deadline: Optional[dt.date] = None
    required_resources: Optional[str] = None
    evaluation_criteria: Optional[str] = None
    privacy_constraints: Optional[str] = None
    ip_terms: Optional[str] = None


class ProjectPatch(BaseModel):
    title: Optional[str] = None
    problem_statement: Optional[str] = None
    objectives: Optional[str] = None
    scope: Optional[str] = None
    deliverables: Optional[str] = None
    required_skills: Optional[str] = None
    required_disciplines: Optional[str] = None
    expected_team_size: Optional[int] = None
    difficulty: Optional[str] = None
    mentor_id: Optional[int] = None
    status: Optional[str] = None
    start_date: Optional[dt.date] = None
    deadline: Optional[dt.date] = None
    required_resources: Optional[str] = None
    evaluation_criteria: Optional[str] = None
    privacy_constraints: Optional[str] = None
    ip_terms: Optional[str] = None


class MatchCandidate(BaseModel):
    student_id: int
    name: str
    department: Optional[str] = None
    experience_level: str = "beginner"
    skills: Optional[str] = None
    score: float
    breakdown: dict[str, float]
    reason: str


class MatchOut(BaseModel):
    project_id: int
    algorithm: str
    weights: dict[str, float]
    missing_capabilities: list[str]
    candidates: list[MatchCandidate]
    source: str = "rules"  # rules | groq-assisted explanations
    explanation: Optional[str] = None


class MemberAdd(BaseModel):
    user_id: int
    team_role: Optional[str] = Field(default="member", max_length=120)
    match_score: Optional[float] = None
    match_reason: Optional[str] = None


class MemberPatch(BaseModel):
    team_role: Optional[str] = None
    status: Optional[str] = None      # invited|active|declined|removed
    is_approved: Optional[bool] = None


class MemberOut(ORMModel):
    id: int
    project_id: int
    user_id: int
    team_role: str
    status: str
    match_score: Optional[float] = None
    match_reason: Optional[str] = None
    is_approved: bool = False
    joined_at: Optional[dt.datetime] = None
    name: Optional[str] = None
    department: Optional[str] = None
    skills: Optional[str] = None
    responsibility_accepted: bool = False


class ResponsibilityAccept(BaseModel):
    role: str = Field(min_length=2, max_length=120)
    responsibilities: Optional[str] = None
    confirm_academic_integrity: bool
    confirm_data_privacy: bool

    @field_validator("confirm_academic_integrity", "confirm_data_privacy")
    @classmethod
    def _must(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You must confirm all statements to accept the responsibility form.")
        return v


class ResponsibilityOut(ORMModel):
    id: int
    project_id: int
    version: str
    role: str
    responsibilities: Optional[str] = None
    expectations: Optional[str] = None
    accepted: bool
    accepted_at: Optional[dt.datetime] = None


class TaskIn(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    description: Optional[str] = None
    assignee_id: Optional[int] = None
    priority: str = "medium"
    status: str = "todo"
    start_date: Optional[dt.date] = None
    due_date: Optional[dt.date] = None
    evidence_link: Optional[str] = Field(default=None, max_length=500)

    @field_validator("priority")
    @classmethod
    def _prio(cls, v: str) -> str:
        return v if v in {"low", "medium", "high", "critical"} else "medium"

    @field_validator("status")
    @classmethod
    def _st(cls, v: str) -> str:
        from .models import TaskStatus
        return v if v in TaskStatus.ALL else TaskStatus.TODO


class TaskPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assignee_id: Optional[int] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    start_date: Optional[dt.date] = None
    due_date: Optional[dt.date] = None
    evidence_link: Optional[str] = None
    review_comment: Optional[str] = None


class TaskOut(ORMModel):
    id: int
    project_id: int
    title: str
    description: Optional[str] = None
    assignee_id: Optional[int] = None
    assignee_name: Optional[str] = None
    priority: str
    status: str
    start_date: Optional[dt.date] = None
    due_date: Optional[dt.date] = None
    evidence_link: Optional[str] = None
    review_comment: Optional[str] = None
    is_overdue: bool = False
    created_at: Optional[dt.datetime] = None
    updated_at: Optional[dt.datetime] = None


class MilestoneIn(BaseModel):
    name: str = Field(min_length=3, max_length=255)
    description: Optional[str] = None
    due_date: Optional[dt.date] = None
    expected_deliverables: Optional[str] = None
    responsible_ids: Optional[list[int]] = None


class MilestonePatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[dt.date] = None
    expected_deliverables: Optional[str] = None
    status: Optional[str] = None
    evidence_link: Optional[str] = Field(default=None, max_length=500)
    mentor_comment: Optional[str] = None


class MilestoneOut(ORMModel):
    id: int
    project_id: int
    name: str
    description: Optional[str] = None
    due_date: Optional[dt.date] = None
    expected_deliverables: Optional[str] = None
    responsible_ids: Optional[str] = None
    evidence_link: Optional[str] = None
    status: str
    mentor_comment: Optional[str] = None
    submitted_at: Optional[dt.datetime] = None
    reviewed_at: Optional[dt.datetime] = None


class WeeklyReportIn(BaseModel):
    completed_work: str = Field(min_length=3, max_length=4000)
    current_progress: str = Field(min_length=3, max_length=4000)
    blockers: Optional[str] = Field(default=None, max_length=2000)
    next_steps: Optional[str] = Field(default=None, max_length=2000)
    help_required: Optional[str] = Field(default=None, max_length=2000)
    week_of: Optional[dt.date] = None


class WeeklyReportOut(ORMModel):
    id: int
    project_id: int
    student_id: int
    student_name: Optional[str] = None
    week_of: dt.date
    completed_work: str
    current_progress: str
    blockers: Optional[str] = None
    next_steps: Optional[str] = None
    help_required: Optional[str] = None
    created_at: dt.datetime


class MentorReviewIn(BaseModel):
    milestone_id: Optional[int] = None
    verdict: str = "neutral"
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    comments: str = Field(min_length=3, max_length=4000)
    risks: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("verdict")
    @classmethod
    def _v(cls, v: str) -> str:
        return v if v in {"approved", "changes_requested", "neutral"} else "neutral"


class MentorReviewOut(ORMModel):
    id: int
    project_id: int
    milestone_id: Optional[int] = None
    verdict: str
    rating: Optional[int] = None
    comments: str
    risks: Optional[str] = None
    created_at: dt.datetime
    reviewer_name: Optional[str] = None


class EvidenceIn(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    url: Optional[str] = Field(default=None, max_length=500)
    kind: str = "document"
    description: Optional[str] = None
    task_id: Optional[int] = None


class EvidenceOut(ORMModel):
    id: int
    project_id: int
    title: str
    url: Optional[str] = None
    kind: str
    description: Optional[str] = None
    uploader_name: Optional[str] = None
    created_at: dt.datetime


# ---------------------------------------------------------------------------
# Solutions
# ---------------------------------------------------------------------------

class SolutionIn(BaseModel):
    title: str = Field(min_length=4, max_length=255)
    description: str = Field(min_length=10, max_length=4000)
    source_url: Optional[str] = Field(default=None, max_length=500)
    source_label: Optional[str] = Field(default=None, max_length=220)
    technologies: Optional[str] = None
    license_info: Optional[str] = Field(default=None, max_length=220)
    limitations: Optional[str] = None
    keywords: Optional[str] = None


class SolutionOut(ORMModel):
    id: int
    title: str
    description: str
    source_url: Optional[str] = None
    source_label: Optional[str] = None
    technologies: Optional[str] = None
    license_info: Optional[str] = None
    limitations: Optional[str] = None
    review_status: str
    is_demo: bool = False
    created_at: dt.datetime


class SolutionMatchOut(BaseModel):
    solution_id: int
    title: str
    description: str
    source_url: Optional[str] = None
    source_label: Optional[str] = None
    technologies: Optional[str] = None
    license_info: Optional[str] = None
    limitations: Optional[str] = None
    review_status: str
    similarity: float
    recommendation: str           # reuse | adapt | build_new
    relevance_explanation: str
    analysed_by: str              # rules | groq
    is_demo: bool = False


# ---------------------------------------------------------------------------
# Competitions
# ---------------------------------------------------------------------------

class CriterionIn(BaseModel):
    key: str
    label: str
    weight: float = Field(gt=0, le=100)


class CompetitionIn(BaseModel):
    title: str = Field(min_length=6, max_length=255)
    description: Optional[str] = None
    scope: str = "cross_university"
    university_id: Optional[int] = None
    eligibility_rules: Optional[str] = None
    registration_deadline: Optional[dt.date] = None
    submission_deadline: Optional[dt.date] = None
    starts_on: Optional[dt.date] = None
    ends_on: Optional[dt.date] = None
    criteria: Optional[list[CriterionIn]] = None

    @field_validator("scope")
    @classmethod
    def _s(cls, v: str) -> str:
        return v if v in {"within_university", "cross_university"} else "cross_university"


class CompetitionOut(ORMModel):
    id: int
    title: str
    description: Optional[str] = None
    scope: str
    university_id: Optional[int] = None
    eligibility_rules: Optional[str] = None
    registration_deadline: Optional[dt.date] = None
    submission_deadline: Optional[dt.date] = None
    starts_on: Optional[dt.date] = None
    ends_on: Optional[dt.date] = None
    status: str
    criteria: Optional[str] = None
    results_published: bool = False
    is_demo: bool = False
    registered_projects: int = 0
    submission_count: int = 0


class SubmissionIn(BaseModel):
    project_id: int
    title: str = Field(min_length=4, max_length=255)
    summary: Optional[str] = None
    demo_link: Optional[str] = Field(default=None, max_length=500)
    documentation_link: Optional[str] = Field(default=None, max_length=500)
    repository_link: Optional[str] = Field(default=None, max_length=500)


class ScoreIn(BaseModel):
    criterion: str
    score: float = Field(ge=0, le=10)
    feedback: Optional[str] = Field(default=None, max_length=2000)


class ScoreOut(ORMModel):
    id: int
    submission_id: int
    judge_id: int
    judge_name: Optional[str] = None
    criterion: str
    score: float
    feedback: Optional[str] = None


class SubmissionOut(ORMModel):
    id: int
    competition_id: int
    project_id: int
    title: str
    summary: Optional[str] = None
    demo_link: Optional[str] = None
    documentation_link: Optional[str] = None
    repository_link: Optional[str] = None
    status: str
    created_at: dt.datetime
    project_title: Optional[str] = None
    university_name: Optional[str] = None
    team_name: Optional[str] = None
    total_score: Optional[float] = None
    score_breakdown: Optional[dict[str, float]] = None
    progress_score: Optional[float] = None
    judge_count: int = 0


class LeaderboardRow(BaseModel):
    rank: int
    submission_id: Optional[int] = None
    project_id: int
    project_title: str
    team_name: str
    university: Optional[str] = None
    innovation_score: Optional[float] = None
    progress_score: Optional[float] = None
    combined: Optional[float] = None
    scored_by: int = 0
    is_demo: bool = False


# ---------------------------------------------------------------------------
# Sponsorship / talent
# ---------------------------------------------------------------------------

class SponsorshipIn(BaseModel):
    kind: str = "funding"
    amount: Optional[float] = Field(default=None, ge=0)
    currency: str = "INR"
    description: Optional[str] = Field(default=None, max_length=3000)

    @field_validator("kind")
    @classmethod
    def _k(cls, v: str) -> str:
        allowed = {"funding", "equipment", "software", "dataset", "cloud", "mentorship"}
        return v if v in allowed else "funding"


class SponsorshipPatch(BaseModel):
    status: str
    status_note: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("status")
    @classmethod
    def _s(cls, v: str) -> str:
        from .models import SponsorshipStatus
        if v not in SponsorshipStatus.ALL:
            raise ValueError("Unsupported sponsorship status")
        return v


class SponsorshipOut(ORMModel):
    id: int
    project_id: int
    company_id: int
    company_name: Optional[str] = None
    project_title: Optional[str] = None
    kind: str
    amount: Optional[float] = None
    currency: str
    description: Optional[str] = None
    status: str
    status_note: Optional[str] = None
    is_demo: bool = False
    created_at: dt.datetime
    updated_at: Optional[dt.datetime] = None


class InvitationIn(BaseModel):
    student_id: int
    kind: str = "internship"
    message: str = Field(min_length=5, max_length=2000)

    @field_validator("kind")
    @classmethod
    def _k(cls, v: str) -> str:
        return v if v in {"internship", "recruitment"} else "internship"


class InvitationOut(ORMModel):
    id: int
    company_id: int
    company_name: Optional[str] = None
    student_id: int
    student_name: Optional[str] = None
    kind: str
    message: str
    status: str
    is_demo: bool = False
    created_at: dt.datetime


# ---------------------------------------------------------------------------
# Notifications, audit, dashboards, misc
# ---------------------------------------------------------------------------

class NotificationOut(ORMModel):
    id: int
    title: str
    body: Optional[str] = None
    kind: str
    link: Optional[str] = None
    is_read: bool
    created_at: dt.datetime


class AuditLogOut(ORMModel):
    id: int
    actor_id: Optional[int] = None
    actor_name: Optional[str] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    reason: Optional[str] = None
    created_at: dt.datetime


class ImpactReportIn(BaseModel):
    original_problem: Optional[str] = None
    objectives: Optional[str] = None
    approach: Optional[str] = None
    prior_solutions_considered: Optional[str] = None
    reused_or_modified: Optional[str] = None
    tech_stack: Optional[str] = None
    prototype: Optional[str] = None
    testing_evidence: Optional[str] = None
    limitations: Optional[str] = None
    actual_results: Optional[str] = None
    community_feedback: Optional[str] = None
    cost_info: Optional[str] = None
    verification_status: str = "proposed"
    follow_up: Optional[str] = None


class ImpactReportOut(ORMModel):
    id: int
    project_id: int
    original_problem: Optional[str] = None
    objectives: Optional[str] = None
    approach: Optional[str] = None
    prior_solutions_considered: Optional[str] = None
    reused_or_modified: Optional[str] = None
    tech_stack: Optional[str] = None
    prototype: Optional[str] = None
    testing_evidence: Optional[str] = None
    limitations: Optional[str] = None
    actual_results: Optional[str] = None
    community_feedback: Optional[str] = None
    cost_info: Optional[str] = None
    verification_status: str
    follow_up: Optional[str] = None
    is_demo: bool = False
    created_at: dt.datetime


class UniversityOut(ORMModel):
    id: int
    name: str
    short_name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    website: Optional[str] = None
    description: Optional[str] = None
    expertise: Optional[str] = None
    is_verified: bool = False
    department_count: int = 0
    project_count: int = 0


class DepartmentOut(ORMModel):
    id: int
    university_id: int
    name: str
    full_name: Optional[str] = None
    expertise: Optional[str] = None


class AcceptIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


class HealthOut(BaseModel):
    status: str
    app: str
    database: str
    groq_configured: bool
    groq_enabled: bool
    groq_model: str
    groq_provider: str = "Groq"
    ai_mode: str
    demo_data_seeded: bool
    version: str = "1.0.0"
    detail: Optional[str] = None


class MessageOut(BaseModel):
    message: str
    detail: Optional[Any] = None
