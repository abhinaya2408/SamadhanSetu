"""Intelligent duplicate detection, scoped by city (brief section 5).

The pipeline is deliberately staged so it stays fast, explainable and honest:

1. **Normalise** the city and optional locality into comparable keys.
2. **Filter in the database** to challenges in the same city (plus open cases with
   no recorded city), so we never compare against the whole table.
3. **Score** each candidate deterministically from the citizen's own words:
   token overlap of title + description, same domain, same locality.
4. **Optionally ask Groq** whether two descriptions describe the *same underlying
   problem*. The model may only refine the label and add a sentence of reasoning —
   it can never merge records, and its verdict is explicitly marked as a model
   opinion. If Groq is unavailable the rule score alone decides.

Nothing here ever merges or deletes a report. Duplicate detection produces
*candidates* for the citizen and the human reviewer.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Challenge, RelatedChallenge
from . import groq_client
from .matching_service import STOPWORDS, tokenize_free_text

# Similarity at/above which we actively warn "this may already be reported".
LIKELY_DUPLICATE = 0.45
# Similarity at/above which we list a report as "related".
RELATED_FLOOR = 0.16

# Weights for the combined score. Text overlap dominates; the location and domain
# signals confirm it. Documented here (and returned in the API response) so the
# number is never a black box — the same reason the UI shows a per-candidate
# explanation.
WEIGHT_TEXT = 0.60
WEIGHT_LOCALITY = 0.20
WEIGHT_DOMAIN = 0.12
WEIGHT_CITY = 0.08

# Common spelling variants so "Bengaluru" and "Bangalore" bucket together.
CITY_ALIASES: dict[str, str] = {
    "bengaluru": "bangalore",
    "bombay": "mumbai",
    "madras": "chennai",
    "calcutta": "kolkata",
    "gurgaon": "gurugram",
    "trivandrum": "thiruvananthapuram",
    "mysore": "mysuru",
    "baroda": "vadodara",
    "pondicherry": "puducherry",
    "allahabad": "prayagraj",
}

_CITY_SUFFIXES = (
    " municipal corporation", " municipal council", " city corporation",
    " corporation", " city", " district", " taluk", " taluka", " tehsil", " mandal",
)

DUPLICATE_GROQ_SYSTEM = """You compare two citizen problem reports from the same city.
Decide whether they describe the SAME underlying problem (not merely the same topic).
Return STRICT JSON only:
{"verdict": "duplicate" | "related" | "different", "reason": "one short sentence"}
Rules:
- "duplicate" only when a single fix would resolve both reports.
- "related" when they share a cause or location but need different work.
- "different" when they are separate problems.
- Base your answer only on the two texts. Do not invent facts. No extra keys."""


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalise_city(value: Optional[str]) -> str:
    """Lowercase, strip punctuation/qualifiers and apply known aliases."""
    text = (value or "").strip().lower()
    if not text:
        return ""
    text = text.split(",")[0]
    text = re.sub(r"[^a-z0-9\s.-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for suffix in _CITY_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    text = CITY_ALIASES.get(text, text)
    return text


def clean_display(value: Optional[str], limit: int) -> Optional[str]:
    """Trim a user-supplied place name for storage/display."""
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit] or None


def normalise_locality(value: Optional[str]) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s.-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def _tokens(*texts: Optional[str]) -> set[str]:
    tokens: set[str] = set()
    for t in texts:
        tokens |= tokenize_free_text(t)
    return {t for t in tokens if t not in STOPWORDS}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def text_similarity(description_a: str, description_b: str,
                    title_a: str = "", title_b: str = "") -> tuple[float, list[str]]:
    """Weighted overlap of the citizen's own wording. Returns (score, shared terms)."""
    body_a, body_b = _tokens(description_a), _tokens(description_b)
    head_a, head_b = _tokens(title_a), _tokens(title_b)
    body = _overlap(body_a, body_b)
    head = _overlap(head_a, head_b)
    shared = sorted((body_a & body_b) | (head_a & head_b))
    # The description carries most of the signal; a shared title is a strong hint.
    score = body * 0.75 + head * 0.25
    if head >= 0.5:
        score = max(score, 0.35)
    return round(min(1.0, score), 4), shared


def score_candidate(
    *,
    text_sim: float,
    same_city: bool,
    same_locality: bool,
    same_domain: bool,
) -> float:
    """Deterministic combined score in 0..1 (see the WEIGHT_* constants)."""
    score = text_sim * WEIGHT_TEXT
    if same_locality:
        score += WEIGHT_LOCALITY
    if same_domain:
        score += WEIGHT_DOMAIN
    if same_city:
        score += WEIGHT_CITY
    return round(min(1.0, score), 4)


def _reasons(text_sim: float, shared: list[str], same_locality: bool,
             same_domain: bool, city_label: str) -> list[str]:
    out: list[str] = []
    if city_label:
        out.append(f"Same city ({city_label})")
    if same_locality:
        out.append("Same locality or area")
    if same_domain:
        out.append("Same problem domain")
    if shared:
        preview = ", ".join(shared[:8])
        out.append(f"Shared wording: {preview}")
    if text_sim >= 0.6:
        out.append("Most of the wording in both reports overlaps")
    elif text_sim >= 0.3:
        out.append("Noticeable wording overlap")
    return out


# ---------------------------------------------------------------------------
# Optional semantic check (Groq) — refines the label, never decides
# ---------------------------------------------------------------------------

def _groq_verdict(candidate_text: str, new_text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (verdict, reason) from Groq, or (None, None) when unavailable."""
    if not groq_client.is_available():
        return None, None
    payload = groq_client.chat_json(
        DUPLICATE_GROQ_SYSTEM,
        json.dumps({"report_a": candidate_text[:2500], "report_b": new_text[:2500]},
                   ensure_ascii=False),
        temperature=0.0,
        max_tokens=200,
    )
    if not payload:
        return None, None
    verdict = str(payload.get("verdict", "")).strip().lower()
    if verdict not in {"duplicate", "related", "different"}:
        return None, None
    reason = payload.get("reason")
    reason = str(reason).strip()[:300] if isinstance(reason, str) and reason.strip() else None
    return verdict, reason


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_duplicates(
    db: Session,
    *,
    description: str,
    title: Optional[str] = None,
    city: Optional[str] = None,
    locality: Optional[str] = None,
    category: str = "other",
    exclude_challenge_id: Optional[int] = None,
    use_ai: bool = True,
    limit: int = 6,
) -> dict:
    """Find possible duplicate/related reports in the same city."""
    city_key = normalise_city(city)
    locality_key = normalise_locality(locality)

    query = db.query(Challenge)
    if exclude_challenge_id:
        query = query.filter(Challenge.id != exclude_challenge_id)
    if city_key:
        # Scope to the same normalised city, exactly as the brief requires.
        query = query.filter(Challenge.city_key == city_key)
    candidates = query.all()

    scored: list[dict] = []

    for other in candidates:
        other_locality_key = normalise_locality(other.locality)
        same_locality = bool(locality_key) and locality_key == other_locality_key
        same_domain = (other.category or "other") == (category or "other")
        sim, shared = text_similarity(
            description, other.description or "",
            title or "", other.title or "")

        score = score_candidate(
            text_sim=sim,
            same_city=bool(city_key) and other.city_key == city_key,
            same_locality=same_locality,
            same_domain=same_domain,
        )
        # A "related" entry must always be explainable by shared wording, so a
        # location/domain coincidence alone is never listed.
        if not shared and score < LIKELY_DUPLICATE:
            continue

        reasons = _reasons(sim, shared, same_locality, same_domain,
                           other.city or (city or ""))

        scored.append({
            "challenge": other,
            "similarity": score,
            "text_similarity": sim,
            "shared_terms": shared[:12],
            "reasons": reasons,
            "same_city": bool(city_key) and other.city_key == city_key,
            "same_locality": same_locality,
            "same_domain": same_domain,
        })

    scored.sort(key=lambda r: r["similarity"], reverse=True)
    scored = scored[:limit]

    source = "rules"
    if use_ai and scored:
        new_text = f"{title or ''}\n{description}"
        verdicts = 0
        for row in scored:
            other = row["challenge"]
            verdict, reason = _groq_verdict(
                f"{other.title}\n{other.description}", new_text)
            if verdict is None:
                continue
            verdicts += 1
            row["ai_verdict"] = verdict
            row["ai_assessment"] = reason
            if verdict == "duplicate" and row["similarity"] >= 0.30:
                # A model opinion may only promote a borderline case, and only
                # together with real textual evidence.
                row["similarity"] = round(min(1.0, row["similarity"] + 0.15), 4)
                row["reasons"] = row["reasons"] + [
                    "AI semantic check suggests one fix could resolve both reports"]
            elif verdict == "different" and row["similarity"] < 0.5:
                row["reasons"] = row["reasons"] + [
                    "AI semantic check suggests these are separate problems"]
        if verdicts:
            source = "rules+groq"

    results = []
    for row in scored:
        other = row["challenge"]
        results.append({
            "challenge_id": other.id,
            "title": other.title,
            "category": other.category,
            "city": other.city,
            "locality": other.locality,
            "status": other.status,
            "similarity": row["similarity"],
            "text_similarity": row["text_similarity"],
            "shared_terms": row["shared_terms"],
            "reasons": row["reasons"],
            "same_city": row["same_city"],
            "same_locality": row["same_locality"],
            "same_domain": row["same_domain"],
            "likely_duplicate": row["similarity"] >= LIKELY_DUPLICATE,
            "ai_assessment": row.get("ai_assessment"),
            "ai_verdict": row.get("ai_verdict"),
        })

    return {
        "candidates": results,
        "city_key": city_key or None,
        "compared_against": len(candidates),
        "likely_duplicate_count": sum(1 for r in results if r["likely_duplicate"]),
        "source": source,
        "method": (
            "Filtered to the same city, then scored as 0.60 x shared wording in the "
            "citizen's own text + 0.20 same locality + 0.12 same domain + 0.08 same city"
            + (", with an AI semantic check on the closest matches." if source == "rules+groq"
               else ". No AI semantic check was used for this run.")
        ),
        "note": (
            "These are possible duplicates, not proof. A human reviewer decides whether "
            "reports should be merged or handled together, and no report is ever deleted "
            "or rewritten by this check."
        ),
    }


def link_related(db: Session, challenge: Challenge, candidates: list[dict]) -> int:
    """Persist related-challenge rows for a newly submitted challenge.

    Existing links are never duplicated (``related_challenges`` has a unique
    constraint on the ordered pair) and autoflush is disabled in this project, so
    pairs already written in this call are also tracked in memory.
    """
    created = 0
    db.flush()  # push any pending inserts so the lookup below sees them
    known: set[tuple[int, int]] = {
        (row[0], row[1]) for row in db.query(
            RelatedChallenge.challenge_id, RelatedChallenge.related_challenge_id).all()
    }
    for cand in candidates:
        if cand["similarity"] < RELATED_FLOOR:
            continue
        other = db.get(Challenge, cand["challenge_id"])
        if other is None or other.id == challenge.id:
            continue
        source = "groq" if cand.get("ai_verdict") else "rules"
        for a, b in ((challenge.id, other.id), (other.id, challenge.id)):
            if (a, b) in known:
                continue
            db.add(RelatedChallenge(
                challenge_id=a, related_challenge_id=b,
                similarity=cand["similarity"], source=source,
            ))
            known.add((a, b))
            created += 1

    # Group likely duplicates so the UI can show one cluster of related reports.
    strong = [c for c in candidates if c["similarity"] >= LIKELY_DUPLICATE]
    if strong:
        # Reuse the oldest group key in the cluster so reports stay together.
        keys = []
        for cand in strong:
            other = db.get(Challenge, cand["challenge_id"])
            if other is not None and other.group_key:
                keys.append(other.group_key)
        key = sorted(keys)[0] if keys else f"cluster-{min(c['challenge_id'] for c in strong)}"
        challenge.group_key = key
        for cand in strong:
            other = db.get(Challenge, cand["challenge_id"])
            if other is not None:
                other.group_key = key
    return created


def related_for_challenge(db: Session, challenge: Challenge, *, limit: int = 8,
                          use_ai: bool = False) -> dict:
    """Same-city neighbours of an existing challenge, newest first."""
    found = find_duplicates(
        db,
        description=challenge.description or "",
        title=challenge.title,
        city=challenge.city or challenge.location,
        locality=challenge.locality,
        category=challenge.category,
        exclude_challenge_id=challenge.id,
        use_ai=use_ai,
        limit=limit,
    )
    return found
