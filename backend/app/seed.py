"""Demonstration data for SamadhanSetu.

Runs once on startup when the database is empty (``SEED_ON_STARTUP=true``).
Every seeded record that represents a statistic, score, sponsorship or result
is flagged ``is_demo=True`` so the UI can label it **Demo data**.

Two end-to-end scenarios are seeded:

* **Scenario A — the full happy path.** A citizen reports a street-lighting /
  safety problem; AI screening summarises it; a faculty reviewer approves it;
  a university starts a project; students are matched and accept their
  responsibility forms; tasks, milestones and a weekly report exist; the project
  enters a competition and attracts a company sponsorship; a verified impact
  report closes the loop.

* **Scenario B — an honest exclusion.** A citizen asks for a new government
  ration-distribution centre. This depends on government action, so screening
  flags it and a human reviewer formally records it as
  *Excluded — Government-Dependent*. It never enters the student pipeline.

The seed also creates a near-duplicate pair (waterlogging) to demonstrate
related-challenge detection, and a declined low-detail submission.

Run manually::

    python -m backend.app.seed          # from the project root

All demo passwords are ``DemoPass123!``.
"""

from __future__ import annotations

import datetime as dt
import json

from sqlalchemy.orm import Session

from .core import security
from .models import (
    Capability,
    Challenge,
    ChallengeReview,
    Company,
    Competition,
    CompetitionRegistration,
    DEFAULT_CRITERIA,
    Department,
    FacultyProfile,
    ImpactReport,
    JudgeScore,
    MentorReview,
    Milestone,
    MilestoneStatus,
    Project,
    ProjectEvidence,
    ProjectMember,
    ResponsibilityForm,
    Role,
    SolutionResource,
    Sponsorship,
    SponsorshipStatus,
    StudentProfile,
    Submission,
    TalentInvitation,
    Task,
    University,
    User,
    VerificationStatus,
    WeeklyReport,
    utcnow,
)
from .services import screening_service

DEMO_PASSWORD = "DemoPass123!"


def _days(n: int) -> dt.date:
    return dt.date.today() + dt.timedelta(days=n)


def _user(db: Session, email: str, name: str, role: str, *, verified: bool = False,
          bio: str | None = None, capabilities: str | None = None) -> User:
    u = User(
        email=email,
        password_hash=security.hash_password(DEMO_PASSWORD),
        full_name=name,
        role=role,
        capabilities=capabilities,
        is_verified=verified,
        is_active=True,
        bio=bio,
    )
    db.add(u)
    db.flush()
    return u


def seed_if_empty(db: Session) -> None:
    """Seed demonstration data only when the database has no users yet."""
    if db.query(User.id).first() is not None:
        return
    _seed(db)


def _seed(db: Session) -> None:
    # ------------------------------------------------------------------
    # Universities & departments
    # ------------------------------------------------------------------
    u1 = University(
        name="Government Engineering College, Pune", short_name="GEC Pune",
        city="Pune", state="Maharashtra", website="https://gecpune.example.edu",
        description="State engineering college with strong civic-innovation labs.",
        expertise="water systems, embedded electronics, community health",
        is_verified=True,
    )
    u2 = University(
        name="Vidya Institute of Technology", short_name="VIT Nagpur",
        city="Nagpur", state="Maharashtra", website="https://vitnagpur.example.edu",
        description="Technology institute focused on applied sustainability projects.",
        expertise="civil infrastructure, data science, agriculture technology",
        is_verified=True,
    )
    db.add_all([u1, u2])
    db.flush()

    d1_eee = Department(university_id=u1.id, name="EEE", full_name="Electrical & Electronics Engineering",
                        expertise="embedded, power, IoT, sensors")
    d1_cse = Department(university_id=u1.id, name="CSE", full_name="Computer Science & Engineering",
                        expertise="software, machine learning, mobile apps")
    d1_civil = Department(university_id=u1.id, name="CIVIL", full_name="Civil Engineering",
                          expertise="structures, surveying, water, roads")
    d2_cse = Department(university_id=u2.id, name="CSE", full_name="Computer Science & Engineering",
                        expertise="software, data science, GIS")
    d2_agri = Department(university_id=u2.id, name="AGRI", full_name="Agricultural Engineering",
                         expertise="irrigation, crop science, sensors")
    db.add_all([d1_eee, d1_cse, d1_civil, d2_cse, d2_agri])
    db.flush()

    # ------------------------------------------------------------------
    # People
    # ------------------------------------------------------------------
    admin = _user(db, "admin@samadhansetu.demo", "Platform Administrator", Role.ADMIN, verified=True,
                  bio="Runs the platform; provisions staff accounts.")

    reviewer_caps = ",".join(sorted(Capability.DEFAULT_REVIEWER))
    coordinator_caps = ",".join(sorted(Capability.DEFAULT_COORDINATOR))

    faculty_meera = _user(db, "meera.faculty@demo.edu", "Dr. Meera Kulkarni", Role.UNIVERSITY_FACULTY,
                          verified=True, capabilities=reviewer_caps,
                          bio="EEE faculty and reviewer; mentors civic infrastructure teams.")
    faculty_arjun = _user(db, "arjun.faculty@demo.edu", "Prof. Arjun Deshmukh", Role.UNIVERSITY_FACULTY,
                          verified=True, capabilities=reviewer_caps,
                          bio="CSE faculty and reviewer; mentors data and software teams.")
    db.add(FacultyProfile(user_id=faculty_meera.id, university_id=u1.id, department_id=d1_eee.id,
                          title="Associate Professor", expertise="embedded, sensors, power",
                          is_approved=True))
    db.add(FacultyProfile(user_id=faculty_arjun.id, university_id=u1.id, department_id=d1_cse.id,
                          title="Assistant Professor", expertise="machine learning, GIS, apps",
                          is_approved=True))

    uni_admin = _user(db, "univadmin@demo.edu", "Registrar GEC Pune", Role.UNIVERSITY_FACULTY,
                      verified=True, capabilities=coordinator_caps,
                      bio="University coordinator: reviews challenges, assigns mentors and finalises teams.")
    db.add(FacultyProfile(user_id=uni_admin.id, university_id=u1.id, title="University Coordinator",
                          is_approved=True))

    citizens = [
        _user(db, "ramesh.citizen@demo.in", "Ramesh Pawar", Role.CITIZEN,
              bio="Auto-rickshaw driver, Kothrud."),
        _user(db, "sunita.citizen@demo.in", "Sunita Bhosale", Role.CITIZEN,
              bio="Retired teacher, Yerawada."),
    ]

    students_spec = [
        ("aarav.student@demo.edu", "Aarav Sharma", d1_eee, u1, "3rd year", "intermediate",
         "embedded systems, pcb design, sensors, iot, python",
         "street lighting, energy efficiency, safety", 14, "Embedded Engineer"),
        ("divya.student@demo.edu", "Divya Nair", d1_cse, u1, "4th year", "advanced",
         "python, machine learning, flask, gis, data analysis",
         "civic tech, dashboards, mapping", 16, "Software Lead"),
        ("imran.student@demo.edu", "Imran Qureshi", d1_civil, u1, "3rd year", "beginner",
         "surveying, autocad, drainage, site work, teamwork",
         "water, roads, sanitation", 10, "Field Coordinator"),
        ("neha.student@demo.edu", "Neha Patil", d2_cse, u2, "4th year", "intermediate",
         "javascript, react, sql, ui design, testing",
         "accessibility, mobile apps", 12, "Frontend Developer"),
        ("rohit.student@demo.edu", "Rohit Jadhav", d2_agri, u2, "2nd year", "beginner",
         "irrigation, crop science, arduino, sensors",
         "agriculture technology, water saving", 8, "Hardware Tinkerer"),
    ]
    students: list[User] = []
    for email, name, dept, uni, year, level, skills, interests, hours, role_pref in students_spec:
        s = _user(db, email, name, Role.STUDENT)
        db.add(StudentProfile(
            user_id=s.id, university_id=uni.id, department_id=dept.id, academic_year=year,
            experience_level=level, skills=skills, interests=interests,
            availability_hours=hours, preferred_role=role_pref,
            previous_projects="College mini-project; hackathon participant.",
            portfolio_url=f"https://portfolio.example.edu/{name.split()[0].lower()}",
            github_url=f"https://github.com/{name.split()[0].lower()}-demo",
            certifications="None recorded yet.",
            consent_portfolio_share=True, consent_contact_share=(len(students) % 2 == 0),
        ))
        students.append(s)

    company_a = _user(db, "csr.tata@demo.in", "Tata CSR Foundation", Role.COMPANY, verified=True)
    db.add(Company(user_id=company_a.id, name="Tata CSR Foundation",
                   industry="Corporate social responsibility",
                   website="https://tata-csr.example.in",
                   description="Funds student civic-technology pilots in Maharashtra.",
                   is_verified=True))
    company_b = _user(db, "econext@demo.in", "EcoNext Systems Pvt Ltd", Role.COMPANY, verified=True)
    db.add(Company(user_id=company_b.id, name="EcoNext Systems Pvt Ltd",
                   industry="Clean technology",
                   website="https://econext.example.in",
                   description="Provides sensors and mentorship for smart-city projects.",
                   is_verified=True))
    db.flush()

    # ------------------------------------------------------------------
    # Solution repository (prior art students can reuse/adapt)
    # ------------------------------------------------------------------
    sol_light = SolutionResource(
        title="Motion-sensing LED street-light retrofit",
        description=("A retrofit module that dims LED street lights to a low glow when no "
                     "movement is detected and brightens on approach, cutting energy use while "
                     "keeping pathways lit."),
        source_url="https://github.com/example/open-light-retrofit",
        source_label="Open hardware repository",
        technologies="PIR sensor, LED driver, MCU, LoRa",
        license_info="MIT (as stated by the source)",
        limitations="Requires reliable mains power and weatherproof enclosures; no fault reporting.",
        keywords="street light, motion sensor, led, energy, safety, lighting",
        review_status="mentor_verified", is_demo=True,
    )
    sol_drainage = SolutionResource(
        title="Community drain-blockage reporting app",
        description=("A mobile app letting residents photograph a blocked drain with GPS; "
                     "municipal staff triage reports by severity and history."),
        source_label="Internal project archive",
        technologies="Flutter, REST, GIS, PostgreSQL",
        limitations="Depends on the municipality adopting the workflow; pilot only.",
        keywords="drainage, waterlogging, reporting, gis, mobile, water",
        review_status="mentor_verified", is_demo=True,
    )
    sol_agri = SolutionResource(
        title="Soil-moisture based irrigation controller",
        description=("An Arduino-based controller that opens a valve only when soil moisture "
                     "falls below a threshold, reducing water use for drip irrigation."),
        source_label="Internal project archive",
        technologies="Arduino, soil moisture sensor, solenoid valve",
        limitations="Sensor calibration needed per soil type; single-zone only.",
        keywords="irrigation, soil, moisture, water, agriculture, arduino",
        review_status="mentor_verified", is_demo=True,
    )
    db.add_all([sol_light, sol_drainage, sol_agri])
    db.flush()

    # ------------------------------------------------------------------
    # Scenario A — full pipeline
    # ------------------------------------------------------------------
    c_light = Challenge(
        citizen_id=citizens[0].id,
        title="Street lights are broken near the bus depot, it feels unsafe at night",
        description=(
            "The stretch of road between Kothrud bus depot and the vegetable market has eleven "
            "street lights, but at least six have not worked for over two months. Women and "
            "elderly people walking back from the market after 8pm say the road is pitch dark, "
            "there have been two chain-snatching incidents this year, and buses slow down "
            "because drivers cannot see pedestrians crossing."),
        category="electricity",
        city="Pune", locality="Kothrud", city_key="pune",
        location="Kothrud bus depot road, Pune",
        affected_users="Around 600 residents plus daily bus commuters and market vendors.",
        current_situation="Six of eleven lamp posts dark for two months; complaints logged but no repair.",
        desired_outcome="The road should be safely lit at night without wasting power all night.",
        constraints="No new cabling permitted; existing poles should be reused if possible.",
        impact_kind="people_affected", impact_amount=600, impact_unit="residents",
        impact_source="Citizen observation, not counted", impact_is_estimate=True,
        status="approved",
    )
    db.add(c_light)
    db.flush()

    c_ration = Challenge(
        citizen_id=citizens[1].id,
        title="We need a new government ration distribution centre in our ward",
        description=(
            "Our ward has one ration shop for more than 9,000 ration-card holders. On distribution "
            "days queues run for hours. Please build a new government fair-price shop and post "
            "additional government staff so that ration distribution is faster."),
        category="other",
        city="Pune", locality="Yerawada Ward 12", city_key="pune",
        location="Yerawada Ward 12, Pune",
        affected_users="More than 9,000 ration-card holders in the ward.",
        current_situation="One shop, long queues, staff shortage on distribution days.",
        desired_outcome="A new government ration shop with more staff.",
        constraints="Requires government approval and funding.",
        impact_kind="people_affected", impact_amount=9000, impact_unit="ration-card holders",
        impact_source="Citizen estimate", impact_is_estimate=True,
        status="excluded_government",
    )
    db.add(c_ration)
    db.flush()

    c_water = Challenge(
        citizen_id=citizens[1].id,
        title="Rainwater floods the lane outside the school every monsoon",
        description=(
            "Every monsoon the lane outside Zilla Parishad school fills with knee-deep water for "
            "three or four days because drains are blocked with silt and plastic. Children cannot "
            "reach school and two-wheelers stall. We need a way to keep the drain clear and warn "
            "people early."),
        category="water",
        city="Pune", locality="Aundh", city_key="pune",
        location="Aundh, Pune",
        affected_users="Around 120 school children and 40 households on the lane.",
        current_situation="Drains block every year; repeated manual cleaning does not last.",
        desired_outcome="Drains stay clear and residents get early warning before flooding.",
        constraints="Low cost; municipal staff must still do the physical cleaning.",
        impact_kind="people_affected", impact_amount=160, impact_unit="people",
        impact_source="Citizen estimate from the school register", impact_is_estimate=True,
        status="approved",
    )
    c_waste = Challenge(
        citizen_id=citizens[0].id,
        title="Wet and dry waste is mixed because nobody knows which bin is which",
        description=(
            "Our society has two bins but residents mix wet and dry waste because the labels "
            "washed off and there is no feedback. The collection van refuses mixed waste, so it "
            "piles up. We want an easy way to teach and track segregation."),
        category="waste",
        city="Pune", locality="Karve Nagar", city_key="pune",
        location="Karve Nagar, Pune",
        affected_users="32 households and the collection staff.",
        current_situation="Labels gone, no feedback loop, van rejects mixed waste.",
        desired_outcome="Clear labelling and a simple way to see if segregation is improving.",
        constraints="Must work for elderly residents; no smartphone-only solution.",
        impact_kind="unknown", impact_notes="Not clear how often waste is refused or how much piles up.",
        impact_is_estimate=True,
        status="declined",
    )
    c_drain_dup = Challenge(
        citizen_id=citizens[1].id,
        title="Drain near the school chokes every rainy season and water enters classrooms",
        description=(
            "The storm drain next to the school chokes with silt and plastic every rainy season. "
            "Water enters two classrooms and children fall sick. The same drain was cleaned last "
            "year but the problem returned. We need something that stops the blockage recurring "
            "and warns the school early."),
        category="water",
        city="Pune", locality="Aundh", city_key="pune",
        location="Aundh, Pune",
        affected_users="School children and nearby houses",
        current_situation="Blocked drain every monsoon; temporary cleaning only.",
        desired_outcome="Prevent recurring blockage and give early warning.",
        constraints="Low cost.",
        impact_kind="area_affected", impact_amount=200, impact_unit="metres of lane flooded",
        impact_source="Citizen estimate", impact_is_estimate=True,
        status="pending_review",
    )
    db.add_all([c_water, c_waste, c_drain_dup])
    db.flush()

    # AI/rule screening for the four pipeline challenges (rules only — no network in seed)
    for ch in (c_light, c_water, c_ration, c_waste, c_drain_dup):
        screening_service.screen_challenge(db, ch, use_ai=False)

    # Human review decisions (authoritative)
    db.add(ChallengeReview(challenge_id=c_light.id, reviewer_id=faculty_meera.id,
                           decision="approved",
                           reason="Clear local problem suitable for an embedded/energy student team."))
    db.add(ChallengeReview(challenge_id=c_water.id, reviewer_id=faculty_meera.id,
                           decision="approved",
                           reason="Low-cost sensing and early-warning prototype is a good student fit."))
    db.add(ChallengeReview(challenge_id=c_waste.id, reviewer_id=faculty_arjun.id,
                           decision="declined",
                           reason="Too vague and community-behaviour focused. Ask for measurable outcomes."))
    db.add(ChallengeReview(
        challenge_id=c_ration.id, reviewer_id=admin.id, decision="excluded_government",
        reason=("The requested outcome is a new government ration shop with government staff — this "
                "depends on government action and funding, not on a student prototype."),
        overrides_ai=True))
    db.flush()

    # Duplicate/related detection: the two waterlogging reports are near-duplicates
    # in the same city and locality, so they must be linked (and clustered).
    from .services import duplicate_service

    found = duplicate_service.find_duplicates(
        db, description=c_water.description, title=c_water.title,
        city=c_water.city, locality=c_water.locality, category=c_water.category,
        exclude_challenge_id=c_water.id, use_ai=False,
    )
    duplicate_service.link_related(db, c_water, found["candidates"])

    found_dup = duplicate_service.find_duplicates(
        db, description=c_drain_dup.description, title=c_drain_dup.title,
        city=c_drain_dup.city, locality=c_drain_dup.locality, category=c_drain_dup.category,
        exclude_challenge_id=c_drain_dup.id, use_ai=False,
    )
    duplicate_service.link_related(db, c_drain_dup, found_dup["candidates"])

    # ------------------------------------------------------------------
    # Scenario A project
    # ------------------------------------------------------------------
    project = Project(
        challenge_id=c_light.id, university_id=u1.id,
        created_by_id=uni_admin.id, mentor_id=faculty_meera.id,
        title="Adaptive street-lighting retrofit for Kothrud depot road",
        problem_statement=(
            "Design and test a low-cost retrofit so the existing street-light poles on this road "
            "stay safely lit at night while using less power during low-traffic hours."),
        objectives=("1) Characterise the existing lamp posts and load. "
                    "2) Build a motion-sensing dimming module. "
                    "3) Field-test energy and light-level performance. "
                    "4) Produce an installation and safety guide."),
        scope="One 400 m stretch; retrofit modules only; no new cabling.",
        deliverables="Working prototype, field test report, installation guide, impact report.",
        required_skills="embedded systems, sensors, iot, python",
        required_disciplines="EEE,CSE",
        expected_team_size=4, difficulty="intermediate", status="active",
        start_date=_days(-40), deadline=_days(60),
        required_resources="Lab bench, PIR sensors, LED driver modules, lux meter.",
        evaluation_criteria="Measured energy saving, maintained light levels, safety compliance.",
        privacy_constraints="No personal data collected; camera-free.",
        ip_terms="Student team retains authorship; university may publish results with attribution.",
    )
    db.add(project)
    db.flush()

    c_light.status = "in_project"

    # Team: 3 accepted + 1 invited
    team_roles = {
        students[0]: ("Embedded Engineer", 91.0, "Strong embedded/sensor match and street-lighting interest."),
        students[1]: ("Software & Data Lead", 84.5, "Python and data analysis for the field-test dashboard."),
        students[2]: ("Field Coordinator", 72.0, "Civil site experience supports installation and surveying."),
        students[3]: ("Frontend Developer", 63.5, "UI skills for a simple public status page."),
    }
    members: dict[int, ProjectMember] = {}
    for idx, (stu, (role, score, reason)) in enumerate(team_roles.items()):
        m = ProjectMember(
            project_id=project.id, user_id=stu.id, team_role=role,
            status="active" if idx < 3 else "invited",
            match_score=score, match_reason=reason, is_approved=(idx < 3),
            approved_by_id=faculty_meera.id if idx < 3 else None,
        )
        db.add(m)
        db.flush()
        members[stu.id] = m
        form = ResponsibilityForm(
            project_id=project.id, member_id=m.id, role=role,
            responsibilities=f"As {role}, deliver the agreed work for '{project.title}'.",
            expectations="Weekly reporting, safety first, academic integrity, respect privacy.",
            accepted=idx < 3,
            accepted_at=utcnow() if idx < 3 else None,
            snapshot=json.dumps({"role": role, "accepted": idx < 3}) if idx < 3 else None,
        )
        db.add(form)

    # Tasks
    task_defs = [
        ("Site audit and pole inventory", students[2], "completed", "high", _days(-35), _days(-25),
         "Counted 11 poles, 6 unserviceable; measured spacing and existing load."),
        ("Prototype motion-sensing dimming module", students[0], "completed", "critical", _days(-24), _days(-10),
         "PIR + MCU prototype dims to 20% and brightens on approach."),
        ("Field-test energy consumption over one week", students[1], "in_progress", "high", _days(-9), _days(6),
         None),
        ("Public status page for residents", students[3], "todo", "medium", _days(-2), _days(20), None),
    ]
    for title, stu, st, prio, start, due, evidence in task_defs:
        t = Task(
            project_id=project.id, title=title, assignee_id=stu.id, priority=prio,
            status=st, start_date=start, due_date=due, created_by_id=faculty_meera.id,
            evidence_link="https://drive.example.edu/project-a" if evidence else None,
            description=evidence,
        )
        db.add(t)
    db.flush()

    # Milestones
    m1 = Milestone(project_id=project.id, name="Site audit complete",
                   description="Full inventory and load measurement of the stretch.",
                   due_date=_days(-25), expected_deliverables="Audit sheet and photos.",
                   responsible_ids=str(students[2].id), status=MilestoneStatus.APPROVED,
                   mentor_comment="Thorough and clearly documented.", submitted_at=utcnow(),
                   reviewed_at=utcnow(), reviewed_by_id=faculty_meera.id)
    m2 = Milestone(project_id=project.id, name="Working dimming prototype",
                   description="Bench-tested retrofit module meeting light-level targets.",
                   due_date=_days(-8), expected_deliverables="Prototype, bench test log.",
                   responsible_ids=str(students[0].id), status=MilestoneStatus.SUBMITTED,
                   submitted_at=utcnow())
    m3 = Milestone(project_id=project.id, name="Field test and impact report",
                   description="One-week field test with energy and safety measurements.",
                   due_date=_days(30), expected_deliverables="Field report, impact report.",
                   responsible_ids=f"{students[0].id},{students[1].id}",
                   status=MilestoneStatus.PENDING)
    db.add_all([m1, m2, m3])
    db.flush()

    db.add(ProjectEvidence(project_id=project.id, uploader_id=students[2].id,
                           title="Pole inventory sheet", kind="document",
                           url="https://drive.example.edu/audit", description="11 poles catalogued."))
    db.add(ProjectEvidence(project_id=project.id, uploader_id=students[0].id,
                           title="Prototype bench test video", kind="video",
                           url="https://drive.example.edu/proto",
                           description="Dimming behaviour under simulated traffic."))
    db.add(MentorReview(project_id=project.id, milestone_id=m1.id, reviewer_id=faculty_meera.id,
                        verdict="approved", rating=5, comments="Excellent site work and clear evidence.",
                        risks="Weatherproofing still to be validated outdoors."))
    db.add(WeeklyReport(project_id=project.id, student_id=students[0].id, week_of=_days(-7),
                        completed_work="Finished the prototype and bench tests.",
                        current_progress="Prototype stable; preparing for outdoor trial.",
                        blockers="Need a weatherproof enclosure rated for monsoon.",
                        next_steps="Order enclosures; install two trial units.",
                        help_required="Mentor guidance on enclosure selection."))

    # ------------------------------------------------------------------
    # Competition + submission + judging (demo data)
    # ------------------------------------------------------------------
    comp = Competition(
        title="Smart City Student Challenge 2026", scope="cross_university",
        description="Cross-university competition for working civic-technology prototypes.",
        eligibility_rules="Registered student teams with a faculty mentor and a real civic problem.",
        registration_deadline=_days(-20), submission_deadline=_days(25),
        starts_on=_days(-30), ends_on=_days(40), status="active",
        criteria=json.dumps(DEFAULT_CRITERIA), results_published=False,
        created_by_id=admin.id, is_demo=True,
    )
    db.add(comp)
    db.flush()
    db.add(CompetitionRegistration(competition_id=comp.id, project_id=project.id,
                                   registered_by_id=uni_admin.id, status="registered"))
    sub = Submission(
        competition_id=comp.id, project_id=project.id, submitted_by_id=students[0].id,
        title="Adaptive street-lighting retrofit — Kothrud field trial",
        summary="Dimming retrofit that keeps roads lit while cutting night-time energy use.",
        demo_link="https://demo.example.edu/streetlight",
        documentation_link="https://docs.example.edu/streetlight",
        repository_link="https://github.com/example/streetlight-retrofit",
    )
    db.add(sub)
    db.flush()

    judge_scores = {
        faculty_meera: {"technical": 8.5, "innovation": 7.5, "execution": 8.0, "usability": 7.0, "impact": 8.0},
        faculty_arjun: {"technical": 8.0, "innovation": 8.0, "execution": 7.5, "usability": 7.5, "impact": 7.5},
        admin: {"technical": 8.2, "innovation": 7.8, "execution": 8.0, "usability": 7.2, "impact": 8.0},
    }
    for judge, scores in judge_scores.items():
        for crit, val in scores.items():
            db.add(JudgeScore(submission_id=sub.id, judge_id=judge.id, criterion=crit, score=val,
                              feedback="Solid engineering; document the field-test data more."))
    db.flush()

    # ------------------------------------------------------------------
    # Sponsorship + talent invitation (demo data)
    # ------------------------------------------------------------------
    tata = db.query(Company).filter(Company.user_id == company_a.id).first()
    econext = db.query(Company).filter(Company.user_id == company_b.id).first()
    db.add(Sponsorship(project_id=project.id, company_id=tata.id, kind="funding",
                       amount=75000, currency="INR",
                       description="Pilot funding for field hardware and safety testing.",
                       status=SponsorshipStatus.COMMITTED, is_demo=True,
                       status_note="Committed after reviewing the field-test plan."))
    db.add(Sponsorship(project_id=project.id, company_id=econext.id, kind="equipment",
                       description="Donation of ten PIR sensors and outdoor enclosures for the trial.",
                       status=SponsorshipStatus.PROPOSED, is_demo=True))
    db.add(TalentInvitation(company_id=econext.id, student_id=students[0].id, kind="internship",
                            message="We would like to offer Aarav a summer internship on our IoT team.",
                            status="sent", is_demo=True))

    # ------------------------------------------------------------------
    # Verified impact report (closes Scenario A) — demo data
    # ------------------------------------------------------------------
    db.add(ImpactReport(
        project_id=project.id, author_id=faculty_meera.id,
        original_problem=("Six of eleven street lights dark on Kothrud depot road, making the "
                          "road unsafe for evening walkers."),
        objectives="Keep the road safely lit while reducing wasted night-time energy.",
        approach="Retrofit existing poles with a motion-sensing dimming module; no new cabling.",
        prior_solutions_considered="Motion-sensing LED retrofit (open hardware) and plain LED replacement.",
        reused_or_modified="Adapted the open motion-sensing retrofit module to the local pole fitting.",
        tech_stack="PIR sensor, LED driver, microcontroller, Python test logging.",
        prototype="Bench prototype dims to 20% with no movement and brightens within 0.4 s.",
        testing_evidence="Bench test log and time-lapse video; outdoor trial pending.",
        limitations=("Outdoor monsoon trial not yet complete; only two poles trialled; energy saving "
                     "is from bench simulation, not a full-season field measurement."),
        actual_results="Bench simulation shows roughly 40% lower night-time energy use at held light levels.",
        community_feedback="Residents who reviewed the demo asked for the trial on the darkest stretch first.",
        cost_info="Prototype cost about INR 1,900 per pole (demo figure).",
        verification_status=VerificationStatus.VERIFIED,
        follow_up="Outdoor field trial scheduled; results to be added before claiming measured savings.",
        is_demo=True,
    ))

    db.commit()


if __name__ == "__main__":  # pragma: no cover
    from .database import SessionLocal, init_db

    init_db()
    with SessionLocal() as session:
        before = session.query(User.id).count()
        seed_if_empty(session)
        after = session.query(User.id).count()
    print(f"Seed complete. Users before={before}, after={after}. Demo password: {DEMO_PASSWORD}")
