"""End-to-end smoke test for SamadhanSetu.

Runs against a throwaway SQLite database seeded with the demonstration data, so
it never touches ``instance/samadhansetu.db``.

Usage (from the project root)::

    python -m tests.test_smoke

It exits non-zero on the first failed assertion, so it also works in CI or as a
manual pre-commit check.
"""

from __future__ import annotations

import os
import struct
import tempfile
import pathlib
import sys
import zlib

# Configure a disposable database BEFORE importing the app (config reads env once).
_TMP_DIR = pathlib.Path(tempfile.mkdtemp(prefix="smds-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}"
os.environ["SEED_ON_STARTUP"] = "true"
os.environ.setdefault("APP_ENV", "test")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402

H = {"X-Requested-With": "samadhansetu"}
PASSED = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    global PASSED
    if not condition:
        print(f"  FAIL: {label} {extra}")
        sys.exit(1)
    PASSED += 1
    print(f"  ok: {label}")


def tiny_png() -> bytes:
    """Build a valid 32x32 PNG without needing an image library."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    width = height = 32
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type
        for x in range(width):
            rows += bytes(((x * 7 + y * 3) % 256, (x * 11) % 256, (y * 5) % 256))
    idat = zlib.compress(bytes(rows), 1)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def client_for(email: str, password: str = "DemoPass123!") -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        raise SystemExit(f"login failed for {email}: {r.status_code} {r.text}")
    return c


def main() -> None:
    with TestClient(app) as c:
        print("Health & seeding")
        h = c.get("/api/health").json()
        check("database ok", h["database"] == "ok", str(h))
        check("demo data seeded", h["demo_data_seeded"] is True)
        stats = c.get("/api/public/stats").json()
        check("approved challenges seeded", stats["approved_challenges"] >= 2)
        check("verified impact seeded", stats["verified_impact"] >= 1)

        # Blocked problems are hidden from anonymous visitors; a reviewer can see them.
        reviewer_view = client_for("admin@samadhansetu.demo")

        print("Scenario B is an honest exclusion")
        check("blocked problem is hidden from anonymous visitors",
              c.get("/api/challenges/2").status_code == 404)
        b = reviewer_view.get("/api/challenges/2").json()
        check("ration problem excluded as government-dependent",
              b["status"] == "excluded_government", b["status"])
        check("screening flagged government dependency",
              bool(b["latest_screening"]["government_dependency"]))

        print("Scenario A is a running project")
        a = c.get("/api/challenges/1").json()
        check("street-lighting problem is in a project", a["status"] == "in_project", a["status"])
        check("project attached", a["project"] is not None)
        check("approved by a human reviewer",
              reviewer_view.get("/api/challenges/1/reviews").json()[0]["decision"] == "approved")

        print("RBAC is enforced on the backend")
        anon = TestClient(app)
        check("anonymous cannot open the review queue",
              anon.get("/api/challenges/queue").status_code == 401)
        citizen = client_for("ramesh.citizen@demo.in")
        check("citizen cannot review", citizen.get("/api/challenges/queue").status_code == 403)
        check("citizen cannot list platform users",
              citizen.get("/api/directory/users").status_code == 403)
        check("non-admin cannot read the audit trail",
              citizen.get("/api/audit").status_code == 403)

        print("CSRF header required on state changes")
        check("missing X-Requested-With is rejected",
              citizen.post("/api/challenges/2/screen", json={}).status_code == 403)

        print("Citizen submission pipeline")
        new = citizen.post("/api/challenges", headers=H, json={
            "title": "Broken footpath near the primary health centre",
            "description": "The footpath is broken and uneven for 200 metres; elderly patients "
                           "trip and there is no ramp anywhere along the stretch.",
            "category": "accessibility", "location": "Ward 4",
        })
        check("submission accepted", new.status_code == 201, new.text[:200])
        cid = new.json()["id"]
        check("screening record created", new.json()["latest_screening"] is not None)
        check("parked for human review, not auto-approved",
              new.json()["status"] in {"pending_review", "needs_clarification"},
              new.json()["status"])

        faculty = client_for("meera.faculty@demo.edu")
        r = faculty.post(f"/api/challenges/{cid}/review", headers=H, json={
            "decision": "approved",
            "reason": "Clear local accessibility problem suitable for a civil/CSE student team.",
        })
        check("faculty can approve", r.status_code == 200 and r.json()["decision"] == "approved")

        uni = client_for("univadmin@demo.edu")
        r = uni.post(f"/api/challenges/{cid}/accept", headers=H, json={})
        check("university admin can accept", r.status_code == 200 and r.json()["status"] == "accepted")

        r = faculty.post("/api/projects", headers=H, json={
            "challenge_id": cid, "title": "Accessible footpath and ramp prototype",
            "problem_statement": "Design a low-cost level footpath with a compliant ramp.",
            "required_skills": "surveying, autocad, civil", "expected_team_size": 3,
        })
        check("project created from accepted problem", r.status_code == 201, r.text[:200])
        pid = r.json()["id"]

        match = faculty.get(f"/api/projects/{pid}/match").json()
        check("matching returns candidates", len(match["candidates"]) >= 1)
        top = match["candidates"][0]
        check("every candidate carries an explanation", bool(top.get("reason")))

        inv = faculty.post(f"/api/projects/{pid}/members", headers=H,
                           params={"student_id": top["student_id"], "team_role": "Field Lead",
                                   "match_score": top["score"]})
        check("invitation created", inv.status_code == 201 and inv.json()["status"] == "invited")
        mid = inv.json()["id"]

        admin = client_for("admin@samadhansetu.demo")
        users = admin.get("/api/directory/users", params={"role": "student", "page_size": 50}).json()
        email = next(u["email"] for u in users["items"] if u["id"] == top["student_id"])
        student = client_for(email)
        r = student.post(f"/api/projects/{pid}/members/{mid}/responsibility", headers=H, json={
            "role": "Field Lead", "responsibilities": "Survey and document the footpath.",
            "confirm_academic_integrity": True, "confirm_data_privacy": True,
        })
        check("responsibility form accepted", r.status_code == 200 and r.json()["accepted"] is True)

        r = faculty.post(f"/api/projects/{pid}/tasks", headers=H, json={
            "title": "Survey the footpath", "assignee_id": top["student_id"],
            "priority": "high", "status": "todo",
        })
        check("task created", r.status_code == 201)

        r = faculty.post(f"/api/projects/{pid}/milestones", headers=H, json={
            "name": "Survey complete", "due_date": "2026-12-01"})
        check("milestone created", r.status_code == 201)

        r = student.get(f"/api/projects/{pid}/overview")
        check("student can see their project overview", r.status_code == 200,
              f"{r.status_code} {r.text[:120]}")

        print("Sponsorship, talent & competitions")
        company = client_for("csr.tata@demo.in")
        r = company.post("/api/sponsorships", headers=H, params={"project_id": pid},
                         json={"kind": "funding", "amount": 25000, "description": "Materials"})
        check("company can propose sponsorship", r.status_code == 201, r.text[:200])
        sid = r.json()["id"]
        check("a company cannot self-accept its own offer",
              company.patch(f"/api/sponsorships/{sid}/status", headers=H,
                            json={"status": "accepted"}).status_code == 403)
        check("project mentor can accept the offer",
              faculty.patch(f"/api/sponsorships/{sid}/status", headers=H,
                            json={"status": "accepted"}).status_code == 200)
        check("only the company records disbursement",
              company.post(f"/api/sponsorships/{sid}/disburse", headers=H, json={}).status_code == 200)

        student_id = users["items"][0]["id"]
        r = company.post("/api/talent/invitations", headers=H, json={
            "student_id": student_id, "kind": "internship",
            "message": "We would like to invite you to interview for a summer role."})
        check("company can invite an opted-in student", r.status_code == 201, r.text[:200])

        # Project 1 is registered in the seeded competition already.
        r = client_for("aarav.student@demo.edu").post(
            "/api/competitions/1/submissions", headers=H,
            json={"project_id": 1, "title": "Street-lighting field trial entry",
                  "summary": "Adaptive dimming retrofit"})
        check("registered team can submit", r.status_code == 201, r.text[:200])
        sub_id = r.json()["id"]
        r = faculty.post(f"/api/competitions/1/submissions/{sub_id}/scores", headers=H,
                         json={"criterion": "innovation", "score": 9.0, "feedback": "Strong"})
        check("authorised judge can score", r.status_code == 200, r.text[:200])
        lb = faculty.get("/api/competitions/1/leaderboard").json()
        check("innovation leaderboard populated", len(lb) >= 1 and lb[0]["innovation_score"] is not None)
        p_lb = faculty.get("/api/competitions/1/leaderboard", params={"kind": "progress"}).json()
        check("progress leaderboard kept separate",
              p_lb[0]["progress_score"] is not None and p_lb[0]["combined"] is None)

        print("Solutions & impact")
        r = faculty.post("/api/solutions", headers=H, json={
            "title": "Modular kerb ramp", "description": "A reusable ramp section for footpaths.",
            "source_label": "Internal repository", "technologies": "concrete, steel"})
        check("faculty can add a repository entry", r.status_code == 201)
        sol = faculty.get(f"/api/challenges/{cid}/solutions").json()
        check("solution search runs without claiming web search",
              sol["external_search"] is False)

        impact = c.get("/api/impact").json()
        check("verified impact is public", impact["total"] >= 1)
        check("impact carries a limitations statement",
              any(i.get("limitations") for i in impact["items"]))

        print("Simplified reporting form: reference data, preview, city & impact")
        ref = c.get("/api/challenges/reference").json()
        check("reporting form domains come from the API", len(ref["domains"]) >= 10)
        check("domain keys are valid categories",
              all(d["key"] in ref["categories"] for d in ref["domains"]))
        check("impact kinds are offered", len(ref["impact_kinds"]) >= 6)
        check("upload limits are described", ref["upload"]["private_by_default"] is True)

        mine_before = citizen.get("/api/challenges/mine").json()["total"]
        prev = citizen.post("/api/challenges/preview", headers=H, json={
            "description": ("Every monsoon the lane outside the Aundh school floods with knee-deep "
                            "water because the drain is blocked with silt and plastic. Children "
                            "cannot reach school and two-wheelers stall."),
            "category": "water", "city": "Pune", "locality": "Aundh",
            "impact": {"kind": "area_affected", "amount": 200, "unit": "metres", "is_estimate": True},
        }, params={"use_ai": "false"})
        check("preview returns 200", prev.status_code == 200, prev.text[:300])
        pv = prev.json()
        check("preview says nothing was saved", pv["persisted"] is False)
        check("preview gives a one-sentence problem", bool(pv["analysis"]["one_sentence"]))
        check("preview gives a plain explanation", len(pv["analysis"]["simple_explanation"]) > 20)
        check("preview explains who is affected", bool(pv["analysis"]["who_is_affected"]))
        check("preview lists possible causes as hypotheses", len(pv["analysis"]["possible_causes"]) >= 1)
        check("preview suggests a solution", bool(pv["analysis"]["potential_solution"]))
        check("preview asks at most two questions",
              len(pv["analysis"]["missing_information"]) <= 2)
        check("preview flags rules-only mode honestly",
              pv["analysis"]["source"] == "rules" and any("rules engine" in w for w in pv["analysis"]["warnings"]))
        check("preview found the seeded Aundh duplicate",
              any(d["challenge_id"] == 3 or d["same_locality"] for d in pv["duplicates"]["candidates"]),
              str(pv["duplicates"]["candidates"][:2]))
        mine_after = citizen.get("/api/challenges/mine").json()["total"]
        check("preview did not persist a challenge", mine_after == mine_before,
              f"{mine_before} -> {mine_after}")

        print("Duplicate detection by city")
        dup = citizen.post("/api/challenges/duplicates", headers=H, json={
            "description": ("Six of the eleven street lights on the Kothrud bus depot road have not "
                            "worked for two months. It is pitch dark after 8pm and unsafe for women "
                            "and elderly people walking to the market."),
            "title": "Street lights not working near the depot",
            "city": "pune", "locality": "Kothrud", "category": "electricity", "use_ai": False,
        })
        check("duplicate check returns 200", dup.status_code == 200, dup.text[:200])
        dj = dup.json()
        check("city is normalised", dj["city_key"] == "pune", str(dj["city_key"]))
        check("same-city candidate found", any(x["challenge_id"] == 1 for x in dj["candidates"]))
        cand = next(x for x in dj["candidates"] if x["challenge_id"] == 1)
        check("candidate is flagged a likely duplicate", cand["likely_duplicate"] is True,
              str(cand["similarity"]))
        check("candidate explains itself", bool(cand["reasons"]) and bool(cand["shared_terms"]))
        check("method states the city filter", "same city" in dj["method"].lower())
        far = citizen.post("/api/challenges/duplicates", headers=H, json={
            "description": "Identical wording to the street light report but a completely different place.",
            "title": "Street lights not working near the depot", "city": "Chennai",
            "locality": "Kothrud", "category": "electricity", "use_ai": False,
        }).json()
        check("a different city finds no candidates", far["candidates"] == [],
              str(far["candidates"][:2]))

        print("Evidence photos: validation, privacy, idempotent submission")
        stag = "report-session-abc123"
        png = tiny_png()
        up = citizen.post("/api/challenges/attachments/staged", headers=H,
                          params={"staging_key": stag},
                          files={"file": ("broken-lamp.png", png, "image/png")})
        check("valid photo accepted", up.status_code == 201, up.text[:300])
        aid = up.json()["id"]
        check("server sniffed the real content type", up.json()["content_type"] == "image/png")
        check("metadata handling is recorded", up.json()["metadata_stripped"] is True,
              str(up.json()))
        bad = citizen.post("/api/challenges/attachments/staged", headers=H,
                           params={"staging_key": stag},
                           files={"file": ("evil.png", b"not really an image" * 20, "image/png")})
        check("non-image content is rejected on the backend", bad.status_code == 422, bad.text[:200])
        check("anonymous cannot read a staged photo",
              TestClient(app).get(f"/api/challenges/attachments/staged/{aid}/raw").status_code == 401)
        other = client_for("sunita.citizen@demo.in")
        check("another citizen cannot read a staged photo",
              other.get(f"/api/challenges/attachments/staged/{aid}/raw").status_code == 403)
        check("the uploader can read it back",
              citizen.get(f"/api/challenges/attachments/staged/{aid}/raw").status_code == 200)

        payload = {
            "description": ("The footpath outside the primary health centre is broken for 200 metres "
                            "and there is no ramp, so elderly patients and wheelchair users cannot "
                            "reach the entrance safely."),
            "category": "accessibility", "city": "Pune", "locality": "Aundh",
            "impact": {"kind": "people_affected", "amount": 300, "unit": "patients per week"},
            "client_request_id": stag,
        }
        first = citizen.post("/api/challenges", headers=H, json=payload)
        check("submission with evidence accepted", first.status_code == 201, first.text[:300])
        fid = first.json()["id"]
        check("city is stored and normalised",
              first.json()["city_key"] == "pune" and first.json()["city"] == "Pune")
        check("locality is stored", first.json()["locality"] == "Aundh")
        check("impact is labelled as an unverified estimate",
              "not verified" in (first.json()["latest_screening"]["impact_summary"] or "").lower()
              or "estimate" in (first.json()["latest_screening"]["impact_summary"] or "").lower(),
              str(first.json()["latest_screening"]["impact_summary"]))
        check("impact label is human readable",
              "People affected" in (first.json()["impact_label"] or ""),
              str(first.json()["impact_label"]))
        check("staged photo was claimed by the report",
              len(first.json()["attachments"]) == 1, str(first.json()["attachments"]))
        check("title was derived from the description",
              len(first.json()["title"]) > 10, first.json()["title"])
        check("citizen wording is stored verbatim",
              first.json()["description"] == payload["description"])

        again = citizen.post("/api/challenges", headers=H, json=payload)
        check("retrying with the same request id is idempotent",
              again.status_code == 201 and again.json()["id"] == fid,
              f"{again.status_code} {again.json().get('id')}")
        check("no duplicate report was created",
              citizen.get("/api/challenges/mine").json()["total"] == mine_before + 1,
              str(citizen.get("/api/challenges/mine").json()["total"]))

        print("Evidence stays private on a submitted report")
        check("anonymous cannot list the report's evidence",
              TestClient(app).get(f"/api/challenges/{fid}/attachments").status_code == 401)
        check("another citizen cannot read the report's photo",
              other.get(f"/api/challenges/{fid}/attachments/{aid}/raw").status_code == 403)
        check("owner can read the report's photo",
              citizen.get(f"/api/challenges/{fid}/attachments/{aid}/raw").status_code == 200)
        check("reviewer can read the report's photo",
              faculty.get(f"/api/challenges/{fid}/attachments/{aid}/raw").status_code == 200)

        print("Correcting the AI summary keeps both versions")
        corr = citizen.post(f"/api/challenges/{fid}/corrections", headers=H, json={
            "field": "who_is_affected", "corrected_value": "About 300 patients a week, plus staff.",
            "note": "I counted the people at the counter on Monday."})
        check("citizen can correct the summary", corr.status_code == 200, corr.text[:250])
        check("correction records the original value",
              (corr.json()["original_value"] or "") != "", str(corr.json()))
        check("correction is attributed", corr.json()["corrected_by_name"] == "Ramesh Pawar")
        after = citizen.get(f"/api/challenges/{fid}").json()
        check("model output is not overwritten",
              after["latest_screening"]["who_is_affected"] != "About 300 patients a week, plus staff.")
        check("screening is marked as corrected",
              after["latest_screening"]["review_status"] == "corrected_by_human",
              after["latest_screening"]["review_status"])
        check("correction history is retrievable",
              len(citizen.get(f"/api/challenges/{fid}/corrections").json()) == 1)
        check("an unknown field cannot be corrected",
              citizen.post(f"/api/challenges/{fid}/corrections", headers=H,
                           json={"field": "government_dependency", "corrected_value": "no"}
                           ).status_code == 422)
        check("an unrelated citizen cannot correct it",
              other.post(f"/api/challenges/{fid}/corrections", headers=H,
                         json={"field": "one_sentence", "corrected_value": "mine now"}
                         ).status_code == 403)

        print("Related reports carry an explanation, not just a score")
        rel = reviewer_view.get("/api/challenges/3/related").json()
        check("seeded waterlogging pair is detected as related",
              any(r["challenge_id"] == 5 for r in rel["related"]), str(rel["related"][:2]))
        check("related entry explains why",
              all(r["reasons"] for r in rel["related"]))
        check("related response states it is a prompt, not proof",
              "not proof" in rel["note"] or "human reviewer" in rel["note"])
        check("likely duplicates are clustered",
              rel["group_key"] is not None, str(rel["group_key"]))

        print("Notifications")
        n = citizen.get("/api/notifications").json()
        check("citizen received notifications", n["total"] >= 1)
        scan = faculty.post("/api/notifications/scan", headers=H, json={})
        check("staff can run the deadline scan", scan.status_code == 200)

    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    main()
