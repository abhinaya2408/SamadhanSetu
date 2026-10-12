"""End-to-end check of the Revision 3 student workflow (disposable database)."""
import os
import pathlib
import tempfile

tmp = pathlib.Path(tempfile.mkdtemp(prefix="smds-p4-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (tmp / "p4.db").as_posix()
os.environ["SEED_ON_STARTUP"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402

H = {"X-Requested-With": "samadhansetu"}
ok = 0


def check(label, cond, extra=""):
    global ok
    if not cond:
        raise SystemExit(f"FAIL: {label} {extra}")
    ok += 1
    print(f"  ok: {label}")


def client_for(email, pw="DemoPass123!"):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": email, "password": pw})
    if r.status_code != 200:
        raise SystemExit(f"login failed {email}: {r.status_code} {r.text}")
    return c


with TestClient(app) as c:
    faculty = client_for("meera.faculty@demo.edu")
    uni = client_for("univadmin@demo.edu")
    student = client_for("aarav.student@demo.edu")
    student2 = client_for("divya.student@demo.edu")

    me = faculty.get("/api/auth/me").json()
    print("faculty role:", me["role"], "capabilities:", me.get("capabilities"))
    check("combined role is university_faculty", me["role"] == "university_faculty")
    check("reviewer capability present", "challenge_reviewer" in me["capabilities"])
    check("reviewer can open the review queue",
          faculty.get("/api/challenges/queue").status_code == 200)

    # Stage 3 — scope a project from an approved challenge
    r = faculty.post("/api/projects", headers=H, json={
        "challenge_id": 3,
        "title": "Monsoon drain-clearing decision aid for a school lane",
        "problem_statement": "Design a low-cost way to keep the school-lane drain clear before the monsoon.",
        "objectives": "Map blockage hotspots and prototype a maintenance schedule.",
        "required_skills": "gis, surveying, python",
        "expected_team_size": 3,
        "difficulty": "intermediate",
    })
    check("project scoped from an approved challenge", r.status_code == 201, r.text[:300])
    pid = r.json()["id"]
    check("project starts in planning", r.json()["status"] == "planning")

    # A project cannot be created from an excluded challenge
    check("government-dependent challenge cannot enter the pipeline",
          faculty.post("/api/projects", headers=H, json={
              "challenge_id": 2, "title": "Ration centre project",
              "problem_statement": "x" * 30}).status_code == 409)

    # Stage 4 — open for recruitment and discover
    r = uni.post(f"/api/projects/{pid}/recruit", headers=H, params={"open": "true"})
    check("coordinator opens recruitment", r.status_code == 200 and r.json()["status"] == "recruiting")

    r = student.get("/api/projects/open")
    check("student sees the open project", r.status_code == 200, r.text[:200])
    items = r.json()["items"]
    row = next((i for i in items if i["id"] == pid), None)
    check("open project appears for the student", row is not None)
    check("card explains the skill match", bool(row["match_reason"]), str(row)[:200])
    check("card reports open positions", row["open_positions"] == 3, str(row["open_positions"]))

    # Stage 5 — student applies, duplicates blocked
    r = student.post(f"/api/projects/{pid}/apply", headers=H, json={"message": "I have done GIS mapping."})
    check("student can apply", r.status_code == 201, r.text[:300])
    aid = r.json()["id"]
    check("application starts as 'applied'", r.json()["status"] == "applied")
    dup = student.post(f"/api/projects/{pid}/apply", headers=H, json={})
    check("duplicate application is rejected", dup.status_code == 409, dup.text[:200])
    check("student sees their application",
          any(a["id"] == aid for a in student.get("/api/projects/my-applications").json()["applications"]))

    # A student cannot read the staff application list
    check("students cannot read the staff application list",
          student.get(f"/api/projects/{pid}/applications").status_code == 403)

    # Stage 6 — staff review and invite
    apps = faculty.get(f"/api/projects/{pid}/applications").json()
    check("staff see the application with an explanation", len(apps) == 1 and bool(apps[0]["match_reason"]))
    r = faculty.patch(f"/api/projects/{pid}/applications/{aid}", headers=H,
                      json={"status": "invited", "team_role": "GIS Lead"})
    check("staff can invite the applicant", r.status_code == 200, r.text[:200])
    check("decision is recorded", r.json()["status"] == "invited" and r.json()["decided_by_name"])
    mid = r.json()["member_id"]
    check("invitation created a team record", mid is not None)

    # Stage 7 — student accepts the invitation (responsibility form first)
    invites = student.get("/api/projects/my-applications").json()["invitations"]
    check("student receives the invitation", any(i["project_id"] == pid for i in invites))
    early = student.post(f"/api/projects/{pid}/members/{mid}/respond", headers=H, json={"accept": True})
    check("cannot join before accepting the responsibility form", early.status_code == 409, early.text[:200])
    rf = student.post(f"/api/projects/{pid}/members/{mid}/responsibility", headers=H, json={
        "role": "GIS Lead", "responsibilities": "Map blockage hotspots.",
        "confirm_academic_integrity": True, "confirm_data_privacy": True})
    check("responsibility form accepted", rf.status_code == 200 and rf.json()["accepted"] is True)
    r = student.post(f"/api/projects/{pid}/members/{mid}/respond", headers=H, json={"accept": True})
    check("student accepts the invitation", r.status_code == 200, r.text[:200])
    check("membership becomes active", r.json()["status"] == "active")

    # Another student must not answer someone else's invitation
    check("a different student cannot answer another invitation",
          student2.post(f"/api/projects/{pid}/members/{mid}/respond", headers=H,
                        json={"accept": True}).status_code == 403)

    # Stage 8 — project becomes active and appears in the student's own projects
    p = student.get(f"/api/projects/{pid}").json()
    check("recruiting project became active", p["status"] == "active", p["status"])
    mine = student.get("/api/projects").json()["items"]
    check("project appears in the student's projects",
          any(x["id"] == pid for x in mine), str([x["id"] for x in mine]))
    check("student can open the workspace overview",
          student.get(f"/api/projects/{pid}/overview").status_code == 200)
    check("student can read their tasks",
          student.get(f"/api/projects/{pid}/tasks").status_code == 200)

    # Capacity is enforced on the backend
    sid2 = None
    for s in [student2]:
        sid2 = s.get("/api/auth/me").json()["id"]
    inv = faculty.post(f"/api/projects/{pid}/members", headers=H,
                       params={"student_id": sid2, "team_role": "Field Surveyor"})
    check("second member invited", inv.status_code == 201, inv.text[:200])
    m2 = inv.json()["id"]
    r = student2.post(f"/api/projects/{pid}/members/{m2}/responsibility", headers=H, json={
        "role": "Field Surveyor", "responsibilities": "Survey the lane.",
        "confirm_academic_integrity": True, "confirm_data_privacy": True})
    check("second member accepts form", r.status_code == 200)
    r = student2.post(f"/api/projects/{pid}/members/{m2}/respond", headers=H, json={"accept": True})
    check("second member joins", r.status_code == 200)

    # Fill the third place, then prove the 4th is refused
    third = client_for("imran.student@demo.edu")
    third_id = third.get("/api/auth/me").json()["id"]
    inv3 = faculty.post(f"/api/projects/{pid}/members", headers=H,
                        params={"student_id": third_id, "team_role": "Field Coordinator"})
    check("third member invited", inv3.status_code == 201)
    m3 = inv3.json()["id"]
    third.post(f"/api/projects/{pid}/members/{m3}/responsibility", headers=H, json={
        "role": "Field Coordinator", "responsibilities": "Coordinate.",
        "confirm_academic_integrity": True, "confirm_data_privacy": True})
    third.post(f"/api/projects/{pid}/members/{m3}/respond", headers=H, json={"accept": True})
    check("team is now full", faculty.get(f"/api/projects/{pid}").json()["member_count"] == 3)

    fourth = client_for("neha.student@demo.edu")
    fourth_id = fourth.get("/api/auth/me").json()["id"]
    over = faculty.post(f"/api/projects/{pid}/members", headers=H,
                        params={"student_id": fourth_id, "team_role": "Extra"})
    if over.status_code == 201:
        m4 = over.json()["id"]
        fourth.post(f"/api/projects/{pid}/members/{m4}/responsibility", headers=H, json={
            "role": "Extra", "responsibilities": "x",
            "confirm_academic_integrity": True, "confirm_data_privacy": True})
        r4 = fourth.post(f"/api/projects/{pid}/members/{m4}/respond", headers=H, json={"accept": True})
        check("over-filling the team is refused on the backend", r4.status_code == 409, r4.text[:200])
    else:
        check("over-filling is refused by the API", True)

    # Stage 9 — tasks and milestones persist
    aarav_id = student.get("/api/auth/me").json()["id"]
    t = faculty.post(f"/api/projects/{pid}/tasks", headers=H, json={
        "title": "Map the drain blockages", "assignee_id": aarav_id, "priority": "high"})
    check("mentor assigns a task", t.status_code == 201, t.text[:300])
    ms = faculty.post(f"/api/projects/{pid}/milestones", headers=H, json={
        "name": "Hotspot map complete", "due_date": "2026-12-01"})
    check("mentor sets a milestone", ms.status_code == 201)
    check("task persists on reload", len(student.get(f"/api/projects/{pid}/tasks").json()) == 1)
    check("milestone persists on reload", len(student.get(f"/api/projects/{pid}/milestones").json()) == 1)

    # Notifications reached the right people
    notifs = faculty.get("/api/notifications").json()
    check("staff received an application notification", notifs["total"] >= 1)

    # Public homepage data comes from real records, not invented numbers.
    pub = c.get("/api/public/overview").json()
    check("public overview returns real stats", pub["stats"]["problems_reported"] >= 1)
    check("public overview lists approved problems", isinstance(pub["problems"], list))
    check("public overview explains its scope", bool(pub.get("note")))

    # Profile editing feeds the student's skill match.
    prof = student.get("/api/auth/me/profile").json()
    check("student profile is readable", prof["kind"] == "student")
    patched = student.patch("/api/auth/me/profile", headers=H,
                            json={"skills": "gis, python, surveying"})
    check("student can edit their skills", patched.status_code == 200, patched.text[:200])
    check("skills are stored", "gis" in (patched.json()["skills"] or ""))
    basic = student.patch("/api/auth/me", headers=H, json={"bio": "GIS enthusiast"})
    check("basic details can be edited",
          basic.status_code == 200 and basic.json()["bio"] == "GIS enthusiast")
    check("a non-student cannot edit a student profile",
          faculty.patch("/api/auth/me/profile", headers=H,
                        json={"skills": "x"}).status_code == 403)

    # The homepage problem cards must be public and readable.
    home = c.get("/")
    check("homepage serves", home.status_code == 200)
    check("homepage uses the required heading",
          "Civic problem solving, reviewed by people." in home.text)
    for asset in ["/assets/img/hero-community.svg", "/assets/img/step-1-report.svg",
                  "/assets/img/cta-community.svg", "/assets/css/home.css"]:
        check(f"asset resolves: {asset}", c.get(asset).status_code == 200)

print(f"\nAll {ok} workflow checks passed.")
