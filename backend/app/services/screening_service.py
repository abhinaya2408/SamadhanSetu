"""Challenge understanding + eligibility screening.

Two layers:
1. Deterministic rule-based analysis — always available, documented, testable.
2. Groq LLM assistance — enriches the analysis when configured; its output is
   untrusted input, validated field-by-field, and NEVER a final decision.

Critical business rule: challenges that require direct government action,
budget approval, official administrative decisions or public infrastructure
construction are flagged government-dependent and kept out of the
student-project pipeline. Only a human reviewer's decision is recorded as final.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from sqlalchemy.orm import Session

from ..models import (
    CATEGORIES,
    Challenge,
    ChallengeScreening,
    ChallengeStatus,
)
from . import groq_client

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

GOVERNMENT_DEPENDENCY_PATTERNS: list[tuple[str, str]] = [
    (r"\b(construct|construction|build)\s+a\s+new\s+(public\s+)?(bus stop|bus shelter|road|bridge|building|flyover|toilet|drain|pipeline|school|hospital|market)\b",
     "Requests physical construction of public infrastructure"),
    (r"\b(new|additional|extra)\s+(public\s+)?bus\s+(stop|route|service)\b",
     "Requests a change to public transport routes/services"),
    (r"\b(government|municipal(ity)?|council|panchayat|bbmp|corporation)\b.{0,60}\b(approve|approval|sanction|authori[sz]e|allocate|release|fund)\b",
     "Depends on a government approval or budget decision"),
    (r"\b(allocate|release|approve|sanction)\b.{0,40}\b(budget|funds?|government funds?|public funds?)\b",
     "Depends on a government budget allocation"),
    (r"\b(official|administrative|executive)\s+(action|decision|order|approval)\b",
     "Requires an official administrative decision"),
    (r"\b(pass|enact|change|amend)\s+(a\s+)?(law|rule|regulation|policy|legislation)\b",
     "Requires legislative or policy change"),
    (r"\b(award|tender|contract|contractor)\b.{0,50}\b(government|municipal|public works|pwd)\b",
     "Depends on a government tender or contract award"),
    (r"\b(land|plot|site)\s+(allotment|allocation|acquisition|sanction)\b",
     "Depends on government land allocation"),
    (r"\bcompensat(e|ion)\b.{0,40}\b(government|state|authority)\b",
     "Depends on government compensation"),
    (r"\bpass\s+a\s+resolution\b",
     "Requires an official council resolution"),
]

CONSTRUCTION_ONLY_PATTERNS = r"\b(construct|construction|sanction|inaugurat)\b"

STUDENT_SUITABLE_PATTERNS: list[tuple[str, str]] = [
    (r"\bprototype\b", "Prototype development"),
    (r"\b(app|application|software|portal|dashboard|website|tool|platform)\b", "Software / application development"),
    (r"\b(monitor(ing)?|track(ing)?|detect(ion)?|sensor|iot)\b", "Monitoring / sensing solution"),
    (r"\b(low[- ]cost|affordable|cheap|budget)\b", "Cost-constrained engineering design"),
    (r"\b(analy[sz](e|is)|data|dataset|machine learning|ml|ai|model)\b", "Data analysis / ML"),
    (r"\b(design|develop|build|create|implement|engineer)\b", "Buildable technical deliverable"),
    (r"\b(system|device|gadget|equipment)\b", "System or device deliverable"),
    (r"\b(mobile|android|ios)\b", "Mobile application"),
    (r"\b(arduino|raspberry pi|embedded|firmware|pcb)\b", "Embedded / hardware work"),
]

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "water": ["water", "drinking water", "quality", "contamination", "pipe", "leak", "irrigation", "flood", "drainage", "sewage"],
    "electricity": ["electric", "electricity", "power", "voltage", "short circuit", "wiring", "transformer", "street light", "solar", "energy"],
    "transport": ["bus", "transport", "traffic", "vehicle", "commut", "railway", "parking", "road safety", "tracking"],
    "agriculture": ["crop", "farm", "agriculture", "soil", "pest", "harvest", "irrigation", "livestock", "yield"],
    "waste": ["waste", "garbage", "trash", "recycl", "segregation", "landfill", "litter", "plastic"],
    "health": ["health", "hospital", "disease", "clinic", "medical", "patient", "hygiene", "mental health"],
    "education": ["school", "student", "education", "classroom", "learning", "library", "exam"],
    "safety": ["safety", "accident", "hazard", "emergency", "disaster", "fire", "crime", "disaster preparedness"],
    "accessibility": ["accessib", "disab", "wheelchair", "blind", "visually impaired", "deaf", "inclusive"],
    "environment": ["pollution", "air quality", "environment", "tree", "green", "emission", "noise", "climate"],
}

SKILL_KEYWORDS: dict[str, list[str]] = {
    "python": ["python", "script", "automation"],
    "data analysis": ["data", "analy", "statistics", "dataset", "pandas"],
    "machine learning": ["machine learning", "ml", "ai ", " model", "predict", "classification"],
    "iot": ["iot", "sensor", "arduino", "raspberry pi", "embedded", "firmware"],
    "embedded c": ["embedded", "firmware", "microcontroller", "arduino"],
    "web development": ["website", "web app", "portal", "frontend", "backend", "html", "full stack"],
    "mobile development": ["mobile app", "android", "ios", "flutter", "react native"],
    "ui/ux design": ["ui", "ux", "interface", "design", "figma", "accessible"],
    "database": ["database", "sql", "storage", "records"],
    "electrical systems": ["electr", "wiring", "circuit", "voltage", "power", "relay", "contactor"],
    "mechanical design": ["mechanical", "cad", "3d print", "enclosure", "structure"],
    "gis/mapping": ["map", "gis", "location", "geo", "gps"],
    "project management": ["plan", "schedule", "stakeholder", "report"],
}

SUITABILITY_STRONG = 4  # number of "student-buildable" signals for a confident suitable

GROQ_SYSTEM = """You are the screening assistant for SamadhanSetu, a student-innovation platform.
You analyse a citizen's problem description and return STRICT JSON only, matching exactly this shape:
{
  "problem_summary": "one or two sentences, neutral",
  "category": "one of: water, electricity, transport, agriculture, waste, health, education, safety, accessibility, environment, other",
  "affected_users": "who is affected",
  "technical_requirements": "comma separated technical needs",
  "missing_information": ["specific missing facts"],
  "government_dependency": true/false,
  "student_project_suitability": "suitable|uncertain|unsuitable",
  "recommendation": "approve|human_review|exclude|decline",
  "explanation": "2-3 plain sentences a citizen can understand",
  "suggested_skills": ["skill", "skill"],
  "clarification_questions": ["question", "question"],
  "confidence": 0.0,
  "one_sentence": "the problem in one short sentence",
  "simple_explanation": "two or three SHORT everyday-English sentences",
  "who_is_affected": "who experiences this, stating clearly what is known and what is an assumption",
  "why_it_matters": "the practical consequence, without exaggeration",
  "impact_summary": "summarise any supplied impact figure and label an unverified estimate as an estimate; write 'Not yet measured.' when nothing was supplied",
  "possible_causes": ["possible cause", "possible cause"],
  "potential_solution": "one or two feasible student approaches (software, engineering, research or prototype)",
  "skills_required": ["skill", "skill"]
}
Rules:
- Write for a member of the public: short sentences, everyday words, no jargon.
- A request that needs direct government construction, budget approval, public
  infrastructure decisions, tenders or official administrative action MUST get
  government_dependency=true and recommendation="exclude" (or "decline" when it
  is simply not a technical problem at all).
- A request to BUILD a prototype, app, sensor, analysis or tool may be suitable
  even in the same domain (e.g. a bus-tracking app is fine; constructing a bus
  stop is not).
- possible_causes are HYPOTHESES only. Never state a cause as established fact.
- Never claim something is verified or measured when the citizen did not say so.
  confidence is your self-assessment only, not a probability.
- Do not invent facts that are not in the text.
Return JSON only."""

# --- Plain-language templates for the deterministic simplification -----------

WHY_IT_MATTERS: dict[str, str] = {
    "water": "Missing or unsafe water and blocked drainage affect health and daily "
             "routine, and the problem usually gets worse every rainy season.",
    "electricity": "Unreliable power and broken lighting affect safety after dark and "
                   "interrupt work, study and small businesses.",
    "transport": "Poor transport access makes travel slower, costlier and less safe, and "
                 "it hits hardest for people without a private vehicle.",
    "agriculture": "Farm losses reduce household income immediately and are difficult to "
                   "recover within the same season.",
    "waste": "Uncollected or mixed waste causes smells, pests and water contamination, and "
             "it costs more public money to clean up later.",
    "health": "Delays in health services put pressure on families and can turn a manageable "
              "problem into an emergency.",
    "education": "When learning is interrupted, students fall behind and often do not catch up.",
    "safety": "A safety hazard can cause injury or loss of life, and the risk usually grows "
              "the longer it is left.",
    "accessibility": "Services that ignore access needs exclude people from work, school and "
                     "public life.",
    "environment": "Environmental damage builds up quietly and becomes far more expensive to "
                   "reverse later.",
    "other": "This problem affects daily life for the people who experience it, and it is "
             "unlikely to resolve on its own.",
}

POTENTIAL_SOLUTIONS: dict[str, str] = {
    "water": "Build a low-cost monitoring or reporting prototype — for example a sensor "
             "that flags a blocked drain, or a phone-based fault log that records photos, "
             "location and how often it happens.",
    "electricity": "Build a fault-reporting and maintenance-tracking prototype that records "
                   "which lights or lines fail and helps maintenance staff spot repeat "
                   "failures, paired with a low-cost sensing or dimming module for the "
                   "affected hardware.",
    "transport": "Build a transport information or reporting prototype, such as a live "
                 "arrival display or a route-problem survey that turns commuter reports "
                 "into data a transport office can act on.",
    "agriculture": "Build a low-cost sensing or scheduling prototype — for example "
                   "soil-moisture based irrigation control, or a pest-sighting log that "
                   "warns farmers early.",
    "waste": "Build a segregation-support or pickup-tracking prototype, such as a simple "
             "collection-status tool that also works for residents without a smartphone.",
    "health": "Build a screening, triage or appointment-tracking prototype that reduces "
              "waiting time and shows staff where demand is concentrated.",
    "education": "Build an offline-friendly learning or attendance tool that keeps working "
                 "with patchy power and connectivity.",
    "safety": "Build a hazard-mapping or early-warning prototype where residents report a "
              "risk and the dangerous spots appear on a shared map.",
    "accessibility": "Build an accessibility audit and reporting tool that captures barriers "
                     "with photos and turns them into an actionable, prioritised list.",
    "environment": "Build a low-cost environmental monitoring prototype, for example a "
                   "dust or noise sensor node, or a pollution-report log with location.",
    "other": "Scope a small prototype with a university mentor first — for example a "
             "reporting tool that documents the problem with photos, location and "
             "frequency so the responsible body has usable evidence.",
}

POSSIBLE_CAUSES: dict[str, list[str]] = {
    "water": ["Silt and plastic blocking drains before the rains",
              "No recurring maintenance schedule",
              "Drainage capacity smaller than current runoff"],
    "electricity": ["Failed lamps or fittings that were never replaced",
                    "No easy way for residents to report a fault",
                    "Wiring or load problems on this stretch"],
    "transport": ["Vehicle and route supply below demand",
                  "No reliable information about delays",
                  "Road layout that is unsafe for pedestrians"],
    "agriculture": ["Unpredictable water supply",
                    "Pests or disease with no early detection",
                    "Limited access to affordable inputs or advice"],
    "waste": ["Waste not separated at source",
              "Pickup schedule missed or not visible to residents",
              "No feedback when separation is done incorrectly"],
    "health": ["Distance to the nearest facility",
               "Staff shortage at peak hours",
               "Cost or paperwork barriers"],
    "education": ["Weak infrastructure or missing materials",
                  "Teacher or staff shortage",
                  "Interrupted attendance caused by weather, transport or safety"],
    "safety": ["Unlit or obstructed routes",
               "No quick way to report a hazard",
               "Traffic behaviour and enforcement gaps"],
    "accessibility": ["Buildings and paths designed without access needs",
                      "No map of accessible facilities",
                      "Information not provided in accessible formats"],
    "environment": ["Local emission sources such as traffic, waste burning or industry",
                    "No monitoring to confirm the source",
                    "Seasonal weather amplifying the problem"],
    "other": ["The root cause has not been established yet",
              "Responsibility for the problem is unclear",
              "No reliable record of how often it happens"],
}

SOLUTION_VERBS = r"\b(prototype|app|application|software|portal|dashboard|sensor|monitor|track|detect|design|develop|build|create|implement|system|device|model|plan)\b"


# ---------------------------------------------------------------------------
# Deterministic analysis
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _matches(pattern: str, text: str) -> Optional[str]:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return m.group(0) if m else None


def detect_government_dependency(text: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for pattern, label in GOVERNMENT_DEPENDENCY_PATTERNS:
        hit = _matches(pattern, text)
        if hit:
            reasons.append(f"{label} (matched: “{hit}”)")
    return bool(reasons), reasons


def detect_category(*texts: str) -> str:
    blob = " ".join(t.lower() for t in texts if t)
    best, best_hits = "other", 0
    for cat, words in CATEGORY_KEYWORDS.items():
        hits = sum(1 for w in words if w in blob)
        if hits > best_hits:
            best, best_hits = cat, hits
    return best


def detect_skills(text: str) -> list[str]:
    blob = text.lower()
    return [skill for skill, words in SKILL_KEYWORDS.items() if any(w in blob for w in words)]


def suitable_signals(text: str) -> list[str]:
    blob = text.lower()
    return [label for pattern, label in STUDENT_SUITABLE_PATTERNS if re.search(pattern, blob)]


def _first_sentences(text: str, limit: int = 2) -> str:
    text = _clean_text(text)
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:limit])[:400]


# ---------------------------------------------------------------------------
# Plain-language simplification (deterministic — always available)
# ---------------------------------------------------------------------------

def _short(value: Optional[str], limit: int) -> str:
    text = _clean_text(value or "")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:.") + "…"


def _place(challenge: Challenge) -> str:
    parts = [p for p in (challenge.locality, challenge.city) if p]
    if parts:
        return ", ".join(parts)
    return _clean_text(challenge.location or "")


def impact_summary(challenge: Challenge) -> str:
    """Summarise the citizen's impact entry, always labelling an estimate."""
    label = challenge.impact_label
    if not label:
        return ("Not yet measured. The citizen did not provide a figure, and no verified "
                "measurement exists.")
    if challenge.impact_is_estimate:
        prefix = "Citizen estimate — not verified."
    else:
        prefix = "Citizen says this figure was measured — not independently verified."
    source = f" Source: {challenge.impact_source}." if challenge.impact_source else ""
    extra = f" {_short(challenge.impact_notes, 160)}" if challenge.impact_notes else ""
    return f"{prefix} {label}.{source}{extra}"


def simplify_rules(challenge: Challenge) -> dict:
    """Produce the citizen-facing fields without any network access."""
    body = " ".join(filter(None, [
        challenge.title, challenge.description, challenge.current_situation or "",
        challenge.desired_outcome or "", challenge.affected_users or "",
    ]))
    category = detect_category(challenge.title, challenge.description,
                               challenge.desired_outcome or "")
    place = _place(challenge)

    first = _clean_text((challenge.description or challenge.title or "").strip())
    sentence = _first_sentences(first, 1).rstrip() or _clean_text(challenge.title or "")
    one_sentence = _short(sentence, 180)
    if one_sentence and one_sentence[-1] not in ".!?":
        one_sentence += "."

    explanation = _first_sentences(challenge.description or challenge.title or "", 3)
    if place:
        explanation = f"{explanation} Reported from {place}." if explanation else f"Reported from {place}."
    if len(explanation) > 480:
        explanation = _short(explanation, 480)

    if (challenge.affected_users or "").strip():
        who = ("The citizen reports that these people are affected: "
               f"{_short(challenge.affected_users, 300)} "
               "This is what the citizen observed — it has not been independently counted.")
    else:
        who = ("People who live or work in this area are likely to be affected, but the exact "
               "number is not yet known. No figure was supplied with this report.")

    skills = detect_skills(body)
    signals = suitable_signals(body)
    causes = POSSIBLE_CAUSES.get(category, POSSIBLE_CAUSES["other"])
    solution = POTENTIAL_SOLUTIONS.get(category, POTENTIAL_SOLUTIONS["other"])
    if signals:
        solution = "A feasible student approach: " + solution
    else:
        solution = ("No clearly buildable deliverable is obvious from the text yet, so the first "
                    "step is for a university mentor to scope one. A likely direction: " + solution)
    if skills:
        solution = f"{solution} Likely skills: {', '.join(skills[:5])}."

    return {
        "one_sentence": one_sentence,
        "simple_explanation": explanation,
        "who_is_affected": who,
        "why_it_matters": WHY_IT_MATTERS.get(category, WHY_IT_MATTERS["other"]),
        "impact_summary": impact_summary(challenge),
        "possible_causes": causes[:3],
        "potential_solution": solution,
        "skills_required": skills[:8],
        "missing_information": [],  # filled by analyse_rules
        "government_dependency": False,  # filled by analyse_rules
    }


def analyse_rules(challenge: Challenge) -> dict:
    """Deterministic screening — the always-available fallback."""
    body = " ".join(filter(None, [
        challenge.title, challenge.description, challenge.current_situation or "",
        challenge.desired_outcome or "", challenge.constraints or "",
    ]))
    gov_dep, gov_reasons = detect_government_dependency(body)
    signals = suitable_signals(body)
    skills = detect_skills(body)
    category = detect_category(challenge.title, challenge.description, challenge.desired_outcome or "")

    missing: list[str] = []
    if not (challenge.affected_users or "").strip():
        missing.append("Who exactly is affected? (e.g. residents, students, ward members)")
    if not (challenge.desired_outcome or "").strip():
        missing.append("What would a successful outcome look like?")
    if not (challenge.constraints or "").strip():
        missing.append("Are there budget, location, safety or data constraints to respect?")
    if len(_clean_text(challenge.description)) < 120:
        missing.append("Please describe the problem in more detail — frequency, scale, and current workarounds.")
    if not (challenge.location or "").strip():
        missing.append("Where does this problem occur? (city / district level is enough)")

    questions: list[str] = []
    if missing:
        # The brief allows at most two genuinely necessary follow-up questions.
        questions = [
            "Who currently experiences this problem and how often?",
            "What happens today without a solution?",
            "What should a student prototype be able to demonstrate?",
            "Are there safety, privacy or budget constraints we must respect?",
        ][: max(1, min(2, len(missing)))]

    construction_only = bool(re.search(CONSTRUCTION_ONLY_PATTERNS, (challenge.desired_outcome or challenge.description).lower()))

    if gov_dep:
        suitability = "unsuitable"
        recommendation = "exclude"
        explanation = (
            "This request appears to require direct government action — "
            + gov_reasons[0].split(" (")[0].lower()
            + ". SamadhanSetu is a student-innovation platform: we cannot approve, fund or carry out "
            "official administrative or infrastructure decisions. If you can reframe the need as a "
            "buildable prototype, application or analysis (for example a tracking app instead of new "
            "infrastructure), students may be able to help with that part."
        )
        confidence = 0.9
    elif construction_only and len(signals) < 2:
        suitability = "unsuitable"
        recommendation = "decline"
        explanation = (
            "The requested outcome is physical construction or an official sanction rather than a "
            "research or prototyping deliverable, so it does not fit a student project pipeline."
        )
        confidence = 0.7
    elif len(signals) >= SUITABILITY_STRONG and len(missing) <= 2:
        suitability = "suitable"
        recommendation = "approve"
        explanation = (
            f"We identified {len(signals)} student-buildable signals "
            f"({', '.join(signals[:3]).lower()}) and the description contains enough detail for a "
            "student team to scope a prototype. A human reviewer still confirms the final decision."
        )
        confidence = 0.75
    elif len(signals) >= 2:
        suitability = "uncertain"
        recommendation = "human_review"
        explanation = (
            "The problem could become a student project, but some essential details are missing or "
            "the expected outcome is not yet concrete enough. A reviewer will confirm the direction."
        )
        confidence = 0.5
    else:
        suitability = "uncertain" if construction_only else "unsuitable"
        recommendation = "human_review" if not construction_only else "decline"
        explanation = (
            "We could not identify a clearly buildable technical deliverable in this submission. "
            "A human reviewer will look at it; clarifying questions are attached."
        )
        confidence = 0.4

    summary = _first_sentences(challenge.description or challenge.title)

    # Citizen-facing simplification reuses the same detected facts.
    simple = simplify_rules(challenge)
    simple["possible_causes"] = simple["possible_causes"][:3]
    simple["missing_information"] = missing[:2]  # the brief allows at most two questions
    simple["government_dependency"] = gov_dep
    simple["skills_required"] = skills[:8] or simple["skills_required"]

    return {
        "problem_summary": summary,
        "category": category,
        "affected_users": _clean_text(challenge.affected_users or "") or None,
        "technical_requirements": ", ".join(skills[:6]) if skills else None,
        "missing_information": missing,
        "government_dependency": gov_dep,
        "government_reasons": gov_reasons,
        "student_project_suitability": suitability,
        "recommendation": recommendation,
        "explanation": explanation,
        "suggested_skills": skills[:8],
        "clarification_questions": questions,
        "confidence": confidence,
        # --- plain-language block ---
        "one_sentence": simple["one_sentence"],
        "simple_explanation": simple["simple_explanation"],
        "who_is_affected": simple["who_is_affected"],
        "why_it_matters": simple["why_it_matters"],
        "impact_summary": simple["impact_summary"],
        "possible_causes": simple["possible_causes"],
        "potential_solution": simple["potential_solution"],
    }


# ---------------------------------------------------------------------------
# Groq-assisted analysis (validated, non-authoritative)
# ---------------------------------------------------------------------------

def _validate_groq(payload: dict, fallback: dict) -> dict:
    """Merge a model response into the rules result, keeping only safe values."""
    merged = dict(fallback)
    merged["source"] = "groq"

    def _str(key: str, max_len: int = 1200):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            merged[key] = v.strip()[:max_len]

    def _list(key: str, max_len: int = 10):
        v = payload.get(key)
        if isinstance(v, list):
            items = [str(x).strip()[:200] for x in v if str(x).strip()]
            merged[key] = items[:max_len]
        elif isinstance(v, str) and v.strip():
            merged[key] = [s.strip()[:200] for s in v.split(",") if s.strip()][:max_len]

    _str("problem_summary", 500)
    _str("affected_users")
    _str("technical_requirements")
    _str("explanation", 1500)

    # Plain-language block (section 4 of the brief).
    _str("one_sentence", 260)
    _str("simple_explanation", 900)
    _str("who_is_affected", 700)
    _str("why_it_matters", 600)
    _str("impact_summary", 600)
    _str("potential_solution", 1200)
    if isinstance(payload.get("possible_causes"), list):
        causes = [str(x).strip()[:300] for x in payload["possible_causes"] if str(x).strip()]
        if causes:
            merged["possible_causes"] = causes[:4]
    if isinstance(payload.get("skills_required"), list):
        req = [str(x).strip()[:80] for x in payload["skills_required"] if str(x).strip()]
        if req:
            merged["skills_required"] = req[:10]

    cat = str(payload.get("category", "")).lower().strip()
    if cat in CATEGORIES:
        merged["category"] = cat

    if isinstance(payload.get("government_dependency"), bool):
        # The model can only ADD caution, never remove a rule-detected dependency.
        merged["government_dependency"] = fallback["government_dependency"] or payload["government_dependency"]

    suit = str(payload.get("student_project_suitability", "")).lower().strip()
    if suit in {"suitable", "uncertain", "unsuitable"}:
        if merged["government_dependency"]:
            suit = "unsuitable"
        merged["student_project_suitability"] = suit

    rec = str(payload.get("recommendation", "")).lower().strip()
    allowed = {"approve", "human_review", "exclude", "decline"}
    if rec in allowed:
        if merged["government_dependency"] and rec in {"approve"}:
            rec = "exclude"  # policy guardrail: LLM cannot approve a government-dependent case
        merged["recommendation"] = rec

    _list("missing_information", 6)
    _list("clarification_questions", 2)  # the brief allows at most two questions
    _list("suggested_skills", 10)
    if not merged.get("skills_required"):
        merged["skills_required"] = list(merged.get("suggested_skills") or [])

    conf = payload.get("confidence")
    if isinstance(conf, (int, float)) and 0 <= float(conf) <= 1:
        merged["confidence"] = round(float(conf), 2)  # self-report, not a verified probability

    return merged


def analyse_with_groq(challenge: Challenge, fallback: dict) -> dict:
    """Try Groq; on any failure return the deterministic result unchanged."""
    if not groq_client.is_available():
        return dict(fallback, source="rules")

    user = json.dumps({
        "title": challenge.title,
        "description": challenge.description[:3000],
        "category_given_by_citizen": challenge.category,
        "affected_users": challenge.affected_users,
        "current_situation": challenge.current_situation,
        "desired_outcome": challenge.desired_outcome,
        "constraints": challenge.constraints,
        "rules_pre_analysis": {
            "government_dependency": fallback["government_dependency"],
            "government_reasons": fallback["government_reasons"],
            "category_guess": fallback["category"],
        },
    }, ensure_ascii=False)

    payload = groq_client.chat_json(GROQ_SYSTEM, user, max_tokens=1000)
    if not payload:
        return dict(fallback, source="rules")
    return _validate_groq(payload, fallback)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def simplified_from_result(challenge: Challenge, result: dict) -> dict:
    """Validate the merged rules/AI result into the citizen-facing contract."""
    from ..schemas import SimplifiedAnalysis

    source = result.get("source", "rules")
    payload = {
        "one_sentence": result.get("one_sentence") or "",
        "simple_explanation": result.get("simple_explanation") or "",
        "who_is_affected": result.get("who_is_affected") or "",
        "why_it_matters": result.get("why_it_matters") or "",
        "impact_summary": result.get("impact_summary") or impact_summary(challenge),
        "possible_causes": result.get("possible_causes") or [],
        "potential_solution": result.get("potential_solution") or "",
        "skills_required": result.get("skills_required") or [],
        "missing_information": result.get("missing_information") or [],
        "government_dependency": bool(result.get("government_dependency")),
        "confidence": result.get("confidence"),
        "review_status": "ai_complete_awaiting_review",
        "source": source,
        "model": groq_client.settings.groq_model if source == "groq" else None,
    }
    validated = SimplifiedAnalysis.model_validate(payload)
    return validated.model_dump()


def preview_analysis(challenge: Challenge, use_ai: bool = True) -> dict:
    """Run the full analysis for the pre-submission preview.

    Nothing is written to the database: the caller passes a *transient* Challenge
    instance and receives the validated simplification plus the raw rule result.
    """
    rules_result = analyse_rules(challenge)
    result = analyse_with_groq(challenge, rules_result) if use_ai else dict(rules_result, source="rules")
    analysis = simplified_from_result(challenge, result)
    if not use_ai or analysis["source"] != "groq":
        analysis["warnings"] = [
            "This analysis was produced by the built-in rules engine because Groq was not "
            "available or not enabled. It is not an AI model output."
        ]
    else:
        analysis["warnings"] = [
            "Produced by " + str(analysis.get("model") or "the configured Groq model")
            + ". Model output is treated as untrusted input and a human reviewer still decides."
        ]
    return {"analysis": analysis, "result": result}


def screen_challenge(db: Session, challenge: Challenge, use_ai: bool = True) -> ChallengeScreening:
    """Run screening and persist a NEW screening record (history preserved)."""
    rules_result = analyse_rules(challenge)
    result = analyse_with_groq(challenge, rules_result) if use_ai else dict(rules_result, source="rules")
    simple = simplified_from_result(challenge, result)

    screening = ChallengeScreening(
        challenge_id=challenge.id,
        source=result.get("source", "rules"),
        model=groq_client.settings.groq_model if result.get("source") == "groq" else None,
        problem_summary=result.get("problem_summary"),
        category=result.get("category"),
        affected_users=result.get("affected_users"),
        technical_requirements=result.get("technical_requirements"),
        missing_information=json.dumps(result.get("missing_information", []), ensure_ascii=False),
        government_dependency=bool(result.get("government_dependency")),
        student_project_suitability=result.get("student_project_suitability", "uncertain"),
        recommendation=result.get("recommendation", "human_review"),
        explanation=result.get("explanation"),
        suggested_skills=", ".join(result.get("suggested_skills", [])[:8]) or None,
        clarification_questions=json.dumps(result.get("clarification_questions", []), ensure_ascii=False),
        confidence=result.get("confidence"),
        one_sentence=simple["one_sentence"] or None,
        simple_explanation=simple["simple_explanation"] or None,
        who_is_affected=simple["who_is_affected"] or None,
        why_it_matters=simple["why_it_matters"] or None,
        impact_summary=simple["impact_summary"] or None,
        possible_causes=json.dumps(simple["possible_causes"], ensure_ascii=False),
        potential_solution=simple["potential_solution"] or None,
        review_status="ai_complete_awaiting_review",
    )
    db.add(screening)
    db.flush()

    # Update the challenge category with the detected one (citizen text untouched)
    if screening.category and screening.category in CATEGORIES:
        challenge.category = screening.category

    # Status: screening never makes a final decision — park for human review.
    # A government-dependent case is flagged but still needs a reviewer to
    # record the formal exclusion.
    if challenge.status in {ChallengeStatus.SUBMITTED, ChallengeStatus.NEEDS_CLARIFICATION}:
        if screening.student_project_suitability == "uncertain" and (
            result.get("missing_information") or result.get("clarification_questions")
        ):
            challenge.status = ChallengeStatus.NEEDS_CLARIFICATION
        else:
            challenge.status = ChallengeStatus.PENDING_REVIEW

    db.commit()
    db.refresh(screening)
    return screening


def screening_to_dict(screening: ChallengeScreening) -> dict:
    return {
        "missing_information": json.loads(screening.missing_information or "[]"),
        "clarification_questions": json.loads(screening.clarification_questions or "[]"),
        "possible_causes": json.loads(screening.possible_causes or "[]"),
    }
