"""Existing-solution discovery.

Retrieval is deterministic (keyword + token-overlap similarity over the actual
stored SolutionResource records and completed internal projects). Groq, when
configured, only *explains* why a retrieved resource is relevant and whether to
Reuse / Adapt / Build New — it never invents sources, links, papers or licenses.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from ..models import Challenge, ChallengeScreening, SolutionResource, Recommendation
from . import groq_client
from .matching_service import parse_keywords, tokenize_free_text, _jaccard


def _resource_tokens(res: SolutionResource) -> set[str]:
    text = " ".join(filter(None, [res.title, res.description, res.technologies or "",
                                  res.keywords or "", res.limitations or ""]))
    return tokenize_free_text(text) | set(parse_keywords(res.technologies))


def _challenge_tokens(challenge: Challenge, screening: ChallengeScreening | None) -> set[str]:
    text = " ".join(filter(None, [
        challenge.title, challenge.description,
        challenge.desired_outcome or "", challenge.constraints or "",
        (screening.technical_requirements if screening else "") or "",
        (screening.problem_summary if screening else "") or "",
    ]))
    return tokenize_free_text(text)


def _heuristic_recommendation(similarity: float, res: SolutionResource) -> str:
    if similarity >= 0.62 and res.review_status == "mentor_verified" and res.source_url:
        return Recommendation.REUSE
    if similarity >= 0.30:
        return Recommendation.ADAPT
    return Recommendation.BUILD_NEW


def _default_explanation(res: SolutionResource, similarity: float, overlap: list[str]) -> str:
    if overlap:
        shared = ", ".join(overlap[:5])
        return (f"Matches on {shared}. Similarity {similarity:.0%} against the challenge's "
                f"keywords. Review licence terms and limitations before reuse.")
    return (f"Weak keyword overlap ({similarity:.0%}); listed because its technology area "
            f"touches this challenge. Treat as background reading, not a solution.")


GROQ_SYSTEM = """You explain how EXISTING, RETRIEVED solution resources relate to a citizen's
challenge on SamadhanSetu. You are given the challenge and a list of resources that were actually
retrieved from the platform's database. Return STRICT JSON:
{
  "analyses": [
    {
      "solution_id": 1,
      "recommendation": "reuse|adapt|build_new",
      "explanation": "2 sentences: why it fits or why it does not"
    }
  ]
}
Rules:
- Only use the provided resources. NEVER invent sources, URLs, papers, licences or results.
- "reuse" only when the resource genuinely solves most of the challenge.
- "adapt" when it covers part of the challenge but needs modification.
- "build_new" when nothing retrieved is suitable.
- Do not claim any licence unless one is stated in the resource data.
Return JSON only."""


def find_solution_matches(db: Session, challenge: Challenge, limit: int = 6,
                          use_ai: bool = True) -> dict:
    screening = (
        db.query(ChallengeScreening)
        .filter(ChallengeScreening.challenge_id == challenge.id)
        .order_by(ChallengeScreening.id.desc())
        .first()
    )
    resources = db.query(SolutionResource).all()
    c_tokens = _challenge_tokens(challenge, screening)

    scored = []
    for res in resources:
        r_tokens = _resource_tokens(res)
        sim = _jaccard(c_tokens, r_tokens)
        if sim <= 0:
            continue
        overlap = sorted(c_tokens & r_tokens)
        rec = _heuristic_recommendation(sim, res)
        explanation = _default_explanation(res, sim, overlap)
        scored.append((sim, res, rec, explanation, overlap))

    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:limit]

    source = "rules"
    if use_ai and top and groq_client.is_available():
        payload = groq_client.chat_json(GROQ_SYSTEM, json.dumps({
            "challenge": {
                "title": challenge.title,
                "description": challenge.description[:1500],
                "desired_outcome": challenge.desired_outcome,
            },
            "retrieved_resources": [
                {"solution_id": r.id, "title": r.title, "description": r.description[:400],
                 "technologies": r.technologies, "license_info": r.license_info,
                 "limitations": r.limitations, "review_status": r.review_status}
                for _, r, _, _, _ in top
            ],
        }, ensure_ascii=False), max_tokens=900)
        if payload and isinstance(payload.get("analyses"), list):
            by_id = {a.get("solution_id"): a for a in payload["analyses"] if isinstance(a, dict)}
            new_top = []
            for sim, res, rec, expl, overlap in top:
                a = by_id.get(res.id)
                if a:
                    r = str(a.get("recommendation", "")).lower()
                    e = str(a.get("explanation", "")).strip()
                    if r in {Recommendation.REUSE, Recommendation.ADAPT, Recommendation.BUILD_NEW}:
                        # model can suggest reuse, but upgrade to reuse only if heuristics agree
                        if r == Recommendation.REUSE and rec == Recommendation.BUILD_NEW:
                            r = rec
                        rec = r
                    if 20 < len(e) < 600:
                        expl = e
                new_top.append((sim, res, rec, expl, overlap))
            top = new_top
            source = "groq"

    matches = [
        {
            "solution_id": r.id,
            "title": r.title,
            "description": r.description,
            "source_url": r.source_url,
            "source_label": r.source_label or "Internal repository",
            "technologies": r.technologies,
            "license_info": r.license_info,
            "limitations": r.limitations,
            "review_status": r.review_status,
            "similarity": round(sim, 3),
            "recommendation": rec,
            "relevance_explanation": expl,
            "analysed_by": source,
            "is_demo": r.is_demo,
        }
        for sim, r, rec, expl, _ in top
    ]

    overall = "build_new"
    if any(m["recommendation"] == Recommendation.REUSE for m in matches):
        overall = Recommendation.REUSE
    elif any(m["recommendation"] == Recommendation.ADAPT for m in matches):
        overall = Recommendation.ADAPT

    limitations = []
    if not resources:
        limitations.append("The internal solution repository is empty — no prior work to compare against.")
    if not groq_client.is_available():
        limitations.append("Groq AI explanations are unavailable (no API key configured); "
                           "matches and recommendations come from local keyword similarity only.")
    elif source == "rules":
        limitations.append("Groq was configured but did not return a usable response for this request; "
                           "local similarity results are shown instead.")

    return {
        "matches": matches,
        "overall_recommendation": overall,
        "analysed_by": source,
        "sources_searched": ["Internal solution repository", "Completed project records"],
        "external_search": False,
        "external_search_note": (
            "No external repository or research search engine is connected to this deployment, "
            "so only internal records were searched. This is stated honestly rather than implied."),
        "limitations": limitations,
        "resource_count": len(resources),
    }
