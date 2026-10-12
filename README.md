# SamadhanSetu

**From real problems to real solutions.**

SamadhanSetu is a working full-stack platform that connects a societal problem a
citizen describes to a university student team that can build and verify a
solution. AI assists with screening and matching; **humans make every decision.**

> This is a demonstration build. Seeded records are labelled **Demo data** in the
> interface, and the platform is explicit about what it does and does not do
> (see [Honesty & limitations](#honesty--limitations)).

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Demo accounts](#demo-accounts)
- [Demonstration scenarios](#demonstration-scenarios)
- [Roles & permissions](#roles--permissions)
- [AI: Groq + rule-based fallback](#ai-groq--rule-based-fallback)
- [API overview](#api-overview)
- [Project layout](#project-layout)
- [Configuration](#configuration)
- [Honesty & limitations](#honesty--limitations)

---

## What it does

1. **Citizens** report a problem in a short three-step form — in their own words
   (stored verbatim), with an optional impact estimate and optional photos.
2. **AI explains it simply**: one sentence, a plain-English explanation, who is
   affected, why it matters, the labelled impact, possible causes, a feasible
   student approach, the skills needed, and at most two follow-up questions.
   AI only ever *suggests* — the analysis carries a “awaiting human review” status.
3. **Similar reports are found by city** before and after submission, scored on
   shared wording, locality and domain, with a reason line for every candidate.
   Reports are never merged or deleted automatically.
4. **Human reviewers** (faculty / university admin / platform admin) approve,
   request clarification, exclude as government-dependent, or decline — each with
   a written reason stored in an audit trail. Anyone involved can **correct** the
   AI summary; corrections are stored separately, never overwriting the model output.
5. **Universities** accept approved problems and start projects. Student matching
   is a transparent, deterministic weighted score with a written reason per student.
6. **Student teams** accept a Responsibility Form, then work through tasks,
   milestones, evidence and weekly reports, reviewed by a mentor.
7. **Competitions, sponsorships and impact reports** capture results honestly:
   judge scores and execution progress are ranked **separately**, and only
   verified impact reports reach the public page.

Every stage works on the same database rows, and the problem page shows the whole
journey in one timeline (reported → analysed → reviewed → accepted → project →
impact) with cross-links to the project and the impact record.

---

## Reporting a problem — what the citizen sees

A deliberately small form (this was the highest-priority change in the last revision):

| Step | Fields | Required? |
|---|---|---|
| 1 · Describe | “What's happening in your community?” + a domain card | **yes** |
| 2 · Where and how big | city, area, impact type (+ amount/unit when useful), photos, optional context | city only |
| 3 · Review | the plain-language summary, possible duplicates, the original wording | — |

Details that matter:

- **“I don't know” is a real answer.** Impact is optional, and a supplied figure is stored with its
  amount, unit, currency, source and estimate flag — never presented as a verified loss.
- **No fake controls.** The microphone button only exists when the browser can actually do speech
  recognition, and no geolocation is requested (there is no reverse-geocoding service — the city is typed).
- **Preview is not a submission.** “Analyze my problem” calls `POST /api/challenges/preview`, which
  writes nothing; the stored analysis is generated fresh on submit.
- **Reviews can be corrected.** `POST /api/challenges/{id}/corrections` adds a new row holding the
  corrected text, the original machine output, the author and the reason.

### Evidence photos

- Validated **on the server**: the file must actually be a JPEG/PNG/WebP by magic bytes, within the
  size limit, and it is stored under a name generated from its content hash (no client filename ever
  becomes a path).
- **Metadata is stripped** — when Pillow is installed the image is fully decoded and re-encoded; without
  it a dependency-free JPEG/PNG/WebP segment stripper removes EXIF/GPS/comment chunks. The method used
  is recorded per file and shown in the interface.
- **Private by default**: there is no public `/uploads` route. Images stream only from
  `/api/challenges/{id}/attachments/{aid}/raw`, authorised for the reporter, reviewers, or a member of
  the resulting project.
- Uploads made while the form is still open are *staged* against the reporting session and claimed by
  the report on submit.

---

## Architecture

- **Backend** — FastAPI + SQLAlchemy 2.0, SQLite by default (`instance/samadhansetu.db`).
  Sessions use httpOnly cookies plus a custom request header (`X-Requested-With`)
  for CSRF defence. Passwords are stored only as salted PBKDF2-HMAC-SHA256 hashes.
- **Frontend** — dependency-free HTML/CSS/vanilla JS served by the same FastAPI app.
  A small `API` client (`assets/js/api.js`), UI helpers (`assets/js/ui.js`) and a
  shared shell (`assets/js/app.js`) drive every page.
- **AI** — the official `groq` Python SDK (`llama-3.3-70b-versatile` by default).
  Every AI call is wrapped so that if Groq is missing, slow or returns unusable
  output, the deterministic rule-based path is used instead and the UI says so.

There is **no** external repository or web search: prior-solution matching reads
only this deployment's internal solution repository.

---

## Quick start

Requirements: Python 3.11+.

```bash
# 1. Install dependencies
python -m pip install -r requirements.txt

# 2. (optional) configure
cp .env.example .env      # then edit .env; adding a GROQ_API_KEY enables AI mode

# 3. Run the app (from the project root)
python -m uvicorn backend.app.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000>. On first start the database is created and seeded
with demonstration data (disable with `SEED_ON_STARTUP=false`).

Run from inside `backend/` instead with:

```bash
cd backend && python -m uvicorn app.main:app --reload --port 8000
```

Re-seed manually (only seeds when the database has no users):

```bash
python -m backend.app.seed
```

Health check: <http://127.0.0.1:8000/api/health> reports the database status,
whether Groq is configured, and the active AI mode.

---

## Demo accounts

All demonstration passwords are **`DemoPass123!`**.

| Role | Email |
| --- | --- |
| Citizen | `ramesh.citizen@demo.in` |
| Citizen | `sunita.citizen@demo.in` |
| Student | `aarav.student@demo.edu` |
| Student | `divya.student@demo.edu` |
| Student | `imran.student@demo.edu` |
| Student | `neha.student@demo.edu` |
| Student | `rohit.student@demo.edu` |
| Faculty / mentor | `meera.faculty@demo.edu` |
| Faculty / mentor | `arjun.faculty@demo.edu` |
| University admin | `univadmin@demo.edu` |
| Company / sponsor | `csr.tata@demo.in` |
| Company / sponsor | `econext@demo.in` |
| Platform administrator | `admin@samadhansetu.demo` |

The sign-in page has one-click buttons to fill in some of these.

Platform administrator accounts can **only** be provisioned by operators; public
registration never grants admin.

---

## Demonstration scenarios

The seed script creates two deliberately different end-to-end stories.

### Scenario A — the full pipeline

A citizen reports broken street lighting on a route to a bus depot (safety). The
platform screens it, a faculty reviewer approves it, a university accepts it and
starts a project, students are matched and accept their responsibility forms,
tasks/milestones/weekly reports are tracked, the project enters a competition and
attracts a company sponsorship, and a **verified impact report** closes the loop —
including its limitations (the outdoor monsoon trial is still pending).

### Scenario B — an honest exclusion

A citizen asks for a **new government ration distribution centre with more
government staff**. Screening flags government dependency, and a human reviewer
formally records it as *Excluded — Government-Dependent*. It never enters the
student pipeline. This demonstrates that the platform does not pretend every
problem can be solved by a student prototype.

The seed also includes a near-duplicate pair (two waterlogging reports) to
demonstrate related-challenge detection, and a vague submission that was declined.

---

## Roles & permissions

Permissions are enforced **on the backend**; hiding a button in the interface is
never treated as security.

| Role | Can do |
| --- | --- |
| `citizen` | Submit problems, answer clarifications, track status |
| `student` | Join teams, accept responsibility forms, work on tasks, submit reports/evidence |
| `faculty` | Review problems, mentor projects, match students, review milestones, verify impact |
| `university_admin` | Everything faculty can, plus accept challenges and manage the university |
| `company` | Browse opted-in students, propose sponsorships, record disbursements, send invitations |
| `admin` | Platform administration, verify organisations, audit trail |

Privacy rules: companies see students only when the student opted in to portfolio
sharing; students see only projects they are active members of.

---

## AI: Groq + rule-based fallback

- Provider: **Groq** via the official `groq` SDK
  (default model `llama-3.3-70b-versatile`).
- If `GROQ_API_KEY` is absent, or a call fails/returns unusable JSON, the
  deterministic rules engine is used and the record is marked `source = "rules"`.
  The response also explains which path was taken.
- **Simplification.** Screening produces both the reviewer-facing fields and the
  citizen-facing block (one sentence, plain explanation, who is affected, why it
  matters, impact, possible causes, potential solution, skills, at most two
  questions). The whole block is validated through the Pydantic model
  `SimplifiedAnalysis` before storage or display.
- **Duplicate detection.** Deterministic scoring first (`0.60 × shared wording +
  0.20 same locality + 0.12 same domain + 0.08 same city`, after a database filter
  on the normalised city). Groq may add a semantic verdict on the closest matches,
  and can only promote a borderline case when textual evidence also supports it.
  No report is ever merged or deleted.
- **Corrections.** Human corrections to any simplified field are stored as their own
  rows (`ChallengeScreeningCorrection`) with the original value and a reason. The
  model output is never overwritten, and the screening is marked `corrected_by_human`.
- Groq is called **server-side only**; the browser never sees the API key.
- **AI never makes a decision.** Screening writes a *recommendation*; the
  authoritative record is always a human `ChallengeReview` with a written reason.
  Any self-reported model confidence is labelled as not being a calibrated
  probability.
- **Matching** is deterministic and inspectable: skills 40%, experience 20%,
  interest 15%, availability 15%, diversity 10% (weights configurable in `.env`),
  with a human-readable reason per candidate.

---

## API overview

Interactive docs: `/docs` (Swagger) and `/redoc`.

| Area | Prefix |
| --- | --- |
| Auth | `/api/auth` |
| Challenges (submit, preview/analyse, duplicate check, screen, review, clarify, correct, accept, related, prior solutions) | `/api/challenges` |
| Reporting reference data (domains, impact kinds, upload limits) | `/api/challenges/reference` |
| Evidence photos (staged and attached, private) | `/api/challenges/attachments/*`, `/api/challenges/{id}/attachments/*` |
| AI-summary corrections | `/api/challenges/{id}/corrections` |
| Duplicate check by city | `POST /api/challenges/duplicates` |
| Projects (team, tasks, milestones, evidence, reports, reviews, impact) | `/api/projects` |
| Solution repository | `/api/solutions` |
| Competitions (registration, submissions, judging, leaderboards) | `/api/competitions` |
| Sponsorships | `/api/sponsorships` |
| Talent discovery & invitations | `/api/talent` |
| Dashboards, action items, impact, audit, public stats | `/api/dashboard`, `/api/impact`, `/api/audit`, `/api/public/stats` |
| Notifications & deadline scan | `/api/notifications` |
| Directory (universities, departments) & admin | `/api/directory` |
| Health | `/api/health` |

State-changing requests must include the header `X-Requested-With: samadhansetu`
(the bundled JS client does this automatically).

---

## Project layout

```
backend/app/
  config.py          settings from environment
  database.py        engine, session, init + additive migrations
  models.py          SQLAlchemy models (all entities)
  schemas.py         Pydantic request/response models (incl. SimplifiedAnalysis)
  dependencies.py    FastAPI dependencies (auth, CSRF, pagination)
  core/              security, permissions (RBAC), audit
  services/          groq_client, screening, duplicate, image, matching,
                     solution, notification, analytics
  routers/           auth, challenges, projects, solutions, competitions,
                     sponsorships, talent, dashboards, notifications, directory
  main.py            FastAPI app, health, error handlers, static frontend
  seed.py            demonstration data (scenarios A & B)
  uploads/           private evidence storage (never served statically)
frontend/
  *.html             public pages, auth pages and role workspaces
  assets/css/        design-system.css, styles.css, dashboard.css, responsive.css
  assets/js/         api.js, ui.js, app.js
DESIGN.md            the unified visual design language + Revision 2 checklist
tests/               self-contained end-to-end smoke test
```

---

## Configuration

Copy `.env.example` to `.env`. Key settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///instance/samadhansetu.db` | Database connection |
| `SECRET_KEY` | random per process | Session-token hashing / stability |
| `GROQ_API_KEY` | *(empty)* | Enables Groq AI; empty ⇒ rules-only mode |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model id |
| `GROQ_ENABLED` | `true` | Master switch for AI calls |
| `SEED_ON_STARTUP` | `true` | Seed demonstration data on first run |
| `SESSION_COOKIE_NAME` | `smds_session` | Session cookie name |
| `COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `MATCH_WEIGHT_*` | 40/20/15/15/10 | Deterministic matching weights |

---

## Honesty & limitations

This build is deliberately careful about its claims.

- **No web-wide search.** Prior-solution matching reads only the internal solution
  repository, and says so wherever results are shown.
- **No unsupervised AI decisions.** AI never approves a problem, assigns a student,
  scores a submission, or verifies impact.
- **Model confidence is self-reported** by the model and is not a calibrated
  probability.
- **Progress and innovation are ranked separately** for competitions; they are
  never merged into one misleading number.
- **Demo data is labelled** (`Demo data`) wherever seeded records are shown.
- **Impact reports state their own limitations**, and only reports a university
  marked *verified* appear on the public impact page.
- **Not production-hardened.** SQLite and a single process are used. Session
  cookies are `SameSite=Lax` + `HttpOnly` with a CSRF header, but there is no
  rate limiting, email verification, password reset, or malware scanning of
  uploaded files. Do not deploy this as-is for real citizen data.
- **Uploads are validated, not virus-scanned.** Files are accepted only if their magic bytes match
  JPEG/PNG/WebP and they fit the size limit; they are re-encoded when Pillow is available and served
  with `nosniff` + a fixed content type. There is no antivirus or full content-disarm step.
- **No location lookup.** The city is typed by the citizen; the platform does not read device
  location and does not reverse-geocode coordinates.
- **Rules-only out of the box.** With no `GROQ_API_KEY` the instance runs in rules-only mode and says so
  in the interface, the API response and `/api/health`. The Groq-assisted paths are implemented but
  **were not exercised against a live key** during development.
- **Duplicate detection is a prompt, not a verdict.** It ranks candidates and explains itself; a human
  reviewer decides whether reports should be handled together.
- **No external integrations** (payments, e-signature, GIS, SMS) are connected;
  links are stored as URLs only.

---

## License

Demonstration project. The visual design language is documented in `DESIGN.md` and
derives from supplied machine-generated design references; no third-party brand
assets are reproduced.
