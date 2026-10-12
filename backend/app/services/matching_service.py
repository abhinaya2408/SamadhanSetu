"""Student capability matching.

Deterministic, explainable scoring — the algorithm lives here and is the
authoritative score. Groq may only improve the human-readable explanation.

Score (0-100) = weighted sum of:
  skill overlap         0.40  (configurable via env)
  relevant experience   0.20
  interest alignment    0.15
  availability          0.15
  team diversity bonus  0.10  (added while composing a balanced team)

Weights are documented in README.md and DESIGN.md. Department is a signal for
the diversity component only — never a restriction.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Project, StudentProfile, User, ProjectMember
from . import groq_client

EXPERIENCE_LEVEL_SCORE = {"beginner": 0.5, "intermediate": 0.78, "advanced": 1.0}

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "with", "in", "on", "to",
    "using", "use", "based", "system", "app", "new",
}

SKILL_SYNONYMS = {
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "js": "javascript",
    "ts": "typescript",
    "db": "database",
    "sql": "sql",
    "iot": "iot",
    "pcb": "hardware design",
    "cad": "mechanical design",
    "nlp": "natural language processing",
    "dl": "deep learning",
    "ui": "ui/ux design",
    "ux": "ui/ux design",
    "fullstack": "web development",
    "full-stack": "web development",
    "backend": "web development",
    "frontend": "web development",
    "android": "mobile development",
    "flutter": "mobile development",
}


def _norm_token(token: str) -> str:
    token = token.strip().lower()
    token = SKILL_SYNONYMS.get(token, token)
    token = re.sub(r"[^a-z0-9+#. ]", "", token)
    token = re.sub(r"\s+", " ", token).strip()
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token


def parse_keywords(text: Optional[str]) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[,\n;|/]+", text)
    out: list[str] = []
    for p in parts:
        tok = _norm_token(p)
        if tok and tok not in STOPWORDS and len(tok) > 1:
            out.append(tok)
    # also keep multi-word phrases as-is, plus their single words
    return list(dict.fromkeys(out))


def tokenize_free_text(text: Optional[str]) -> set[str]:
    if not text:
        return set()
    words = re.findall(r"[a-z0-9+#.]{2,}", text.lower())
    return {_norm_token(w) for w in words if _norm_token(w) and _norm_token(w) not in STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    return len(inter) / min(len(a), len(b))


def _skill_overlap(required: list[str], have: list[str]) -> tuple[float, list[str], list[str]]:
    """Return (score 0-1, matched skills, unmatched skills)."""
    if not required:
        return 0.5, [], []
    req_set = set(required)
    have_set = set(have)
    matched = [r for r in required if r in have_set or any(r in h or h in r for h in have_set if len(h) > 3)]
    unmatched = [r for r in required if r not in matched]
    return len(matched) / len(req_set), matched, unmatched


def score_student(project: Project, profile: StudentProfile, user: User,
                  required_skills: list[str], project_tokens: set[str]) -> dict:
    have = parse_keywords(profile.skills)
    interests = parse_keywords(profile.interests)

    skill_score, matched, unmatched = _skill_overlap(required_skills, have)
    # small bonus for relevant past experience wording
    exp_tokens = tokenize_free_text(profile.previous_projects)
    exp_bonus = min(0.2, _jaccard(project_tokens, exp_tokens) * 0.5)
    experience_score = min(1.0, EXPERIENCE_LEVEL_SCORE.get(profile.experience_level, 0.5) + exp_bonus)

    interest_score = _jaccard(set(interests) | set(have), project_tokens)
    interest_score = min(1.0, interest_score * 2.2)  # scale: overlap of a few terms is meaningful

    availability_score = min(1.0, (profile.availability_hours or 0) / 18.0)

    breakdown = {
        "skill": round(skill_score * 100, 1),
        "experience": round(experience_score * 100, 1),
        "interest": round(interest_score * 100, 1),
        "availability": round(availability_score * 100, 1),
        "diversity": 0.0,
    }

    w = settings.match_weights
    base = (
        skill_score * w["skill"]
        + experience_score * w["experience"]
        + interest_score * w["interest"]
        + availability_score * w["availability"]
    ) * 100

    dept = profile.department.name if profile.department else None
    reason = _build_reason(matched, unmatched, profile, dept, breakdown, project)

    return {
        "student_id": user.id,
        "name": user.full_name,
        "department": dept,
        "experience_level": profile.experience_level,
        "skills": profile.skills,
        "score": round(min(base, 95.0), 1),  # diversity bonus is added later
        "base_score": round(base, 1),
        "breakdown": {k: v for k, v in breakdown.items() if k != "diversity"},
        "reason": reason,
        "matched": matched,
        "unmatched": unmatched,
    }


def _build_reason(matched, unmatched, profile: StudentProfile, dept, breakdown, project: Project) -> str:
    parts = []
    if matched:
        parts.append(f"Has {len(matched)} of the required skills ({', '.join(matched[:4])})")
    else:
        parts.append("No directly matching listed skills yet")
    lvl = profile.experience_level
    parts.append(f"{lvl} experience level")
    if profile.previous_projects:
        parts.append("has prior project work")
    if profile.interests:
        parts.append(f"interests include {', '.join(parse_keywords(profile.interests)[:3])}")
    parts.append(f"available ~{profile.availability_hours} h/week")
    if dept:
        parts.append(f"from {dept}")
    text = "; ".join(parts) + "."
    if unmatched:
        text += f" Would need support with: {', '.join(unmatched[:3])}."
    return text


def match_students(db: Session, project: Project, limit: int = 12) -> dict:
    """Rank eligible students for a project, composing a diverse shortlist."""
    required_skills = parse_keywords(project.required_skills)
    project_tokens = tokenize_free_text(
        " ".join(filter(None, [project.title, project.problem_statement,
                               project.objectives or "", project.required_skills or ""])))
    disciplines = parse_keywords(project.required_disciplines)

    existing = {
        m.user_id for m in db.query(ProjectMember).filter(
            ProjectMember.project_id == project.id,
            ProjectMember.status.in_(["active", "invited"]),
        ).all()
    }

    profiles = (
        db.query(StudentProfile, User)
        .join(User, User.id == StudentProfile.user_id)
        .filter(User.is_active == True)  # noqa: E712
        .all()
    )

    candidates = []
    for profile, user in profiles:
        if user.id in existing:
            continue
        cand = score_student(project, profile, user, required_skills, project_tokens)
        cand["_profile"] = profile
        candidates.append(cand)

    candidates.sort(key=lambda c: c["score"], reverse=True)

    # Team composition: greedy pick with a diversity bonus for uncovered disciplines
    target = max(1, min(project.expected_team_size or 4, limit))
    covered: set[str] = set()
    covered_skills: set[str] = set()
    picked: list[dict] = []
    remaining = list(candidates)

    w_div = settings.match_weights["diversity"]
    while len(picked) < target and remaining:
        best, best_val = None, -1.0
        for cand in remaining:
            dept = (cand["department"] or "").lower()
            diverse = 1.0 if any(d.lower() in dept or dept in d.lower() for d in disciplines) and \
                not any(d.lower() in " ".join(covered).lower() for d in [cand["department"] or ""]) else 0.0
            # full diversity credit when this candidate's discipline is not yet covered
            if cand["department"] and cand["department"] not in covered:
                diverse = 1.0
            value = cand["base_score"] * (1 - w_div) + diverse * w_div * 100
            # small synergy: candidates covering still-missing skills get a nudge
            synergy = 10 * len([s for s in cand["matched"] if s not in covered_skills]) / max(1, len(cand["matched"] or ["x"]))
            value += synergy
            if value > best_val:
                best, best_val = cand, value
        if best is None:
            break
        best["score"] = round(min(best["base_score"] * (1 - w_div) + best_val * w_div + 8, 98.0), 1)
        best["breakdown"]["diversity"] = round(w_div * 100, 1)
        picked.append(best)
        if best["department"]:
            covered.add(best["department"])
        covered_skills.update(best["matched"])
        remaining.remove(best)

    missing = [s for s in required_skills if s not in covered_skills]

    for cand in picked:
        cand.pop("_profile", None)
        cand.pop("matched", None)
        cand.pop("unmatched", None)
        cand.pop("base_score", None)

    result = {
        "project_id": project.id,
        "algorithm": "deterministic weighted match (skill/experience/interest/availability + diversity)",
        "weights": settings.match_weights,
        "missing_capabilities": missing,
        "candidates": picked,
        "source": "rules",
        "explanation": None,
    }

    if groq_client.is_available() and picked:
        explanation = groq_client.chat_text(
            "You help faculty mentors understand student-team recommendations. "
            "In 3 short sentences, explain what kind of team composition the list below suggests "
            "and which capability gaps the mentor should cover. Be concrete and do not invent facts.",
            json.dumps({
                "project": project.title,
                "required_skills": required_skills,
                "missing_capabilities": missing,
                "recommended": [
                    {"name": c["name"], "department": c["department"], "skills": c["skills"], "score": c["score"]}
                    for c in picked[:6]
                ],
            }, ensure_ascii=False),
            max_tokens=300,
        )
        if explanation:
            result["explanation"] = explanation
            result["source"] = "groq-assisted explanations"

    return result

