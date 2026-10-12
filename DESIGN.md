# SamadhanSetu — Unified Design System

**Tagline:** From Real Problems to Real Solutions.

This document defines the single visual language used across every page of SamadhanSetu.

## Design references

Two references were supplied and **both were successfully accessed** during build:

1. **Airbnb design reference** (`https://getdesign.md/airbnb/design-md`) — the full token file was
   supplied inline as `DESIGN-airbnb.md` (version: alpha). Its tokens, component specs, elevation
   rules and responsive breakpoints were extracted and are cited below.
2. **Apple design reference** (`https://getdesign.md/apple/design-md`) — the full token file was
   supplied inline as `DESIGN-apple.md` (version: alpha). Its tokens, component specs, elevation
   rules and responsive breakpoints were extracted and are cited below.

> Both sources are machine-generated design analyses (version: alpha). They were used as token
> sources, not copied page layouts or branding. No Airbnb or Apple logo, proprietary copy, or
> full page layout is reproduced in SamadhanSetu.

## The unified language: "Warm Precision"

The two references are combined into **one** language — not two styles:

| Quality | Source | How SamadhanSetu applies it |
|---|---|---|
| Warm, human, welcoming | Airbnb | Coral accent, rounded cards (14/20px), pill CTAs and search, friendly empty states, badge system |
| Restrained, precise, calm | Apple | Generous whitespace (80px sections), hairline borders instead of heavy shadows, tight display tracking, 17px body copy at 1.47 leading, one shadow tier only |
| Single accent colour | Both | Exactly **one** brand accent (coral) carries every primary action — never a second brand hue |
| Modest type weights | Airbnb | Headlines at 500–600, body at 400 — no decorative 800 weights |
| Alternating surfaces | Apple | Light (`--canvas`) ↔ soft (`--surface-soft`) full-bleed bands create rhythm without borders |

## Colour tokens

Defined as CSS custom properties in `frontend/assets/css/design-system.css`.

### Brand accent
- `--color-primary` `#e4573d` — restrained coral (a deliberate midpoint between Airbnb Rausch `#ff385c`
  and Apple's quieter interaction colours; kept restrained per brief)
- `--color-primary-active` `#c8462f` — press state (Airbnb `primary-active` pattern)
- `--color-primary-soft` `#fdeeeb` — soft tint for badges/focus halos
- `--color-primary-disabled` `#f6c9c1` — disabled fill (Airbnb `primary-disabled` pattern)
- `--color-on-primary` `#ffffff`

### Surfaces
- `--color-canvas` `#ffffff`
- `--color-surface-soft` `#f7f6f4` (warm white — Airbnb `surface-soft` `#f7f7f7`, warmed)
- `--color-surface-strong` `#efece8`
- `--color-surface-inverse` `#1d1d1f` (Apple `ink` used as dark tile)

### Text
- `--color-ink` `#1d1d1f` (Apple `ink`)
- `--color-body` `#3f3f3f` (Airbnb `body`)
- `--color-muted` `#6a6a6a` (Airbnb `muted`)
- `--color-muted-soft` `#929292`
- `--color-on-dark` `#ffffff`
- `--color-muted-on-dark` `#cccccc` (Apple `body-muted`)

### Borders
- `--color-hairline` `#e6e3df` (warm hairline; Apple `#e0e0e0` / Airbnb `#dddddd` average)
- `--color-hairline-soft` `#efece8`
- `--color-border-strong` `#c1c1c1`

### Semantic
- `--color-success` `#1f7a4d`
- `--color-warning` `#b26a00`
- `--color-error` `#c13515` (Airbnb `primary-error-text`)
- `--color-error-hover` `#b32505`
- `--color-info` `#2b6cb0`
- `--color-scrim` `rgba(0,0,0,.5)` (Airbnb `scrim`)

## Typography

Stack: `Inter, -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, "Segoe UI", Roboto, sans-serif`.
Inter is the documented open-source substitute for both Airbnb Cereal VF and Apple SF Pro (both
reference files name Inter as the substitute).

| Token | Size | Weight | Line height | Tracking | Reference |
|---|---|---|---|---|---|
| `--fs-hero` | clamp 34–56px | 600 | 1.07 | -0.02em | Apple `hero-display` |
| `--fs-h1` | 40px | 600 | 1.10 | -0.01em | Apple `display-lg` |
| `--fs-h2` | 34px | 600 | 1.2 | -0.01em | Apple `display-md` |
| `--fs-h3` | 24px | 600 | 1.25 | -0.01em | Airbnb `display-sm` scaled |
| `--fs-lead` | 21px | 400 | 1.5 | 0 | Airbnb `body-md` scaled |
| `--fs-body` | 17px | 400 | 1.47 | -0.005em | Apple `body` (17px rule) |
| `--fs-body-sm` | 16px | 400 | 1.5 | 0 | Airbnb `body-md` |
| `--fs-caption` | 14px | 400/500 | 1.43 | 0 | Airbnb `body-sm`/`caption` |
| `--fs-badge` | 11–13px | 600 | 1.2 | 0 | Airbnb `badge` |
| `--fs-fine` | 12px | 400 | 1.4 | 0 | Apple `fine-print` |

Rules:
- Weight ladder: 300 / 400 / 500 / 600 / 700 only (no 800+).
- Display sizes use negative tracking ("Apple tight"); micro labels never do.
- Body is 17px on marketing pages, 16px in dense dashboard tables.

## Spacing

Base unit 4px (Airbnb), section rhythm 80px (Apple).

`--space-1:4px · 2:8px · 3:12px · 4:16px · 5:24px · 6:32px · 7:48px · 8:64px · 9:80px`

- Card padding: 24px (both references agree).
- Section padding: 80px desktop / 48px mobile (Apple `spacing.section`, compressed at phone).
- Grid gutters: 16–24px.

## Radii

`--radius-xs 4px · sm 8px (Apple) / Airbnb buttons 8px · md 14px (Airbnb `rounded.md`) · lg 20px · xl 32px · pill 9999px`

- Buttons: 8px for form/utility buttons (Airbnb `button-primary`), **pill** for hero CTAs and search (both).
- Cards: 14px (Airbnb `property-card`) for content cards, 20px for feature cards.
- Full-bleed marketing bands: 0.

## Elevation

**One shadow tier only** (Airbnb). No shadows on text, no stacked elevation ramps (Apple).

- `--shadow-1: 0 1px 2px rgba(0,0,0,.04), 0 4px 12px rgba(0,0,0,.06)` — cards at rest
- `--shadow-2: 0 0 0 1px rgba(0,0,0,.02), 0 2px 6px rgba(0,0,0,.04), 0 4px 8px rgba(0,0,0,.10)` — Airbnb's exact card-hover/dropdown shadow
- `--shadow-product: 3px 5px 30px rgba(0,0,0,.22)` — reserved for hero artwork only (Apple's single product shadow)
- Focus: `0 0 0 3px var(--color-primary-soft)` + 2px primary outline — always visible for keyboard users.

## Components

| Component | Spec |
|---|---|
| `.btn` primary | coral fill, white, 8px radius, 48px height, 500 weight; press → `--color-primary-active` |
| `.btn--pill` | pill radius, used for hero CTAs and search submit |
| `.btn--secondary` | white fill, 1px `--color-ink` outline, ink text |
| `.btn--ghost` | transparent, ink text, underline on hover (Airbnb `button-tertiary-text`) |
| `.btn--danger` | `--color-error` fill |
| `.card` | white, 14px radius, 1px hairline, `--shadow-1`; hover → `--shadow-2` + -2px lift |
| `.badge` | pill, 11–13px/600; status variants map to semantic colours with 8% tinted backgrounds |
| `.input` | white, 1px hairline, 8px radius, 48px height; focus → 2px ink border + coral halo (Airbnb `text-input`) |
| `.search-pill` | white pill, 56–64px, hairline + `--shadow-1`, coral pill submit orb (Airbnb `search-bar-pill` + `search-orb`) |
| `.site-header` | sticky, white, 1px bottom hairline, 72px (Airbnb `top-nav` 80px compressed for dashboards) |
| `.site-footer` | `--color-surface-soft`, 64px padding, column heads 14px/600, legal row 12px (Airbnb `footer-light`) |
| `.band` | full-bleed, alternating `--canvas` / `--surface-soft` (Apple light-tile rhythm, warmed) |
| `.band--dark` | `--surface-inverse` tile with white text (Apple `product-tile-dark`) |
| `.status-pill` | status label, tinted background, always text (never colour alone) |
| `.empty-state` | icon + title + explanation + action; used for every empty list |
| `.spinner` / `.skeleton` | loading indicators, disabled under `prefers-reduced-motion` where possible |
| `.toast` | bottom-right confirmation, auto-dismiss, `role="status"` |
| `.modal` | `dialog`-style, scrim `rgba(0,0,0,.5)`, focus-trapped, Esc to close (Airbnb `scrim`) |
| `.table` | hairline row dividers, sticky header, collapses to cards ≤640px |
| `.kpi` | stat tile: value 32px/600 tabular, label 13px muted, optional demo-data tag |

## Motion

- `--dur-fast 120ms · --dur 180ms · --dur-slow 280ms`, `--ease cubic-bezier(.2,.6,.2,1)`.
- Hover lift on cards only. Press state `transform: scale(.97)` (Apple `scale(.95)` pattern, softened).
- All non-essential motion removed under `@media (prefers-reduced-motion: reduce)`.

## Demo-data labelling

Any statistic, score, sponsorship or result that comes from seeded demonstration records carries a
visible **“Demo data”** tag (`.tag-demo`). Real authenticated-user data is never tagged.

## Accessibility

- Semantic landmarks (`header/nav/main/footer/aside`), one `h1` per page, ordered heading levels.
- Visible `:focus-visible` rings on every interactive element; 44px minimum touch targets.
- All form inputs have `<label>` associations; errors announced via `role="alert"`.
- Status never communicated by colour alone — always paired with text.
- Colour contrast: body text ≥ 7:1 on canvas, muted ≥ 4.5:1.

## Responsive breakpoints

Apple's ladder, simplified to five tokens:

| Token | Width | Behaviour |
|---|---|---|
| `--bp-sm` | 640px | grids → 1 column, tables → stacked cards |
| `--bp-md` | 834px | nav collapses to hamburger (Apple), hero type steps down |
| `--bp-lg` | 1068px | 3→2 column grids |
| `--bp-xl` | 1280px | full layout, content max-width engaged |
| content lock | 1440px | outer margins absorb extra width |

Containers: `--container-prose 980px` (Apple text sections), `--container 1200px`, `--container-wide 1440px`.

---

# Revision 2 — "one connected system"

This section is the implementation checklist for the second revision: what was wrong, what
was changed, and where to look. It is written from an inspection of the code as it stood, not
from a plan. New UI primitives added in this revision are documented at the end.

## What the audit found

| Problem | Evidence in the code |
|---|---|
| Long, intimidating citizen form | `frontend/submit.html` was one screen with **six** large text areas plus a card grid |
| AI output was reviewer-facing, not citizen-facing | `screening_service.analyse_rules()` produced neutral/technical copy only |
| Duplicate detection was not really by city | `routers/challenges.py::_similarity` compared the new report against **every** row with a fixed 0.12 keyword threshold; no city normalisation, no stored city field |
| Dashboards were static lists | `frontend/dashboard.html` rendered KPIs + one list; no tabs, no filters, no charts |
| Pages felt disconnected | no shared record-level navigation; a problem, its project and its impact were only linked by a single "open project" button |
| No evidence uploads despite the DB column | `Challenge.attachment_path` existed but nothing wrote to it |
| AI summary could not be corrected | no way to record that a model summary was wrong |
| Pydantic validation errors could 500 | `main.py::validation_handler` put Pydantic v2 `ctx.error` (a `ValueError`) straight into `JSONResponse` → not JSON serialisable |

## Checklist (all implemented)

**Reporting experience (§3)**
- [x] `frontend/submit.html` rebuilt as a three-step progressive form: *Describe* → *Where and how big*
      → *Review*, with a shared `UI.stepper()` progress indicator and Back/preview on every step.
- [x] Required fields reduced to **description + domain + city**. Title, impact, photos and all
      context are optional; the title is derived from the description when omitted.
- [x] Domain picker is a grid of selectable cards (`.choice`), driven by `GET /api/challenges/reference`
      so the UI never hardcodes the list.
- [x] Impact is a simple choice (`IMPACT_KINDS`) with amount/unit/currency/notes only when relevant,
      and an explicit "Other / Not sure". Amount, unit, currency, source and estimate-vs-measured are all
      stored (`Challenge.impact_*`), and an estimate is never presented as verified.
- [x] Photo evidence: client-side previews, per-file progress, remove buttons, size and type errors —
      and the same validation, plus more, enforced on the server (see below).
- [x] No fake voice button: the microphone appears **only** when `SpeechRecognition` exists in the
      browser, and no location permission is requested (we have no reverse-geocoding service — the city
      is typed).
- [x] "Analyze my problem" previews without saving; "Submit problem" is the only write. A changed
      report must be re-analysed before it can be submitted, and `client_request_id` makes submission
      idempotent server-side.

**AI simplification (§4)** — `backend/app/services/screening_service.py`
- [x] Every screening now also produces the citizen-facing block: `one_sentence`,
      `simple_explanation`, `who_is_affected`, `why_it_matters`, `impact_summary`, `possible_causes`,
      `potential_solution`, `skills_required`, `missing_information` (max 2), `government_dependency`,
      `confidence`, `review_status` — stored on `ChallengeScreening`.
- [x] The whole block is validated through the Pydantic model `SimplifiedAnalysis` before it is stored
      or shown, so model output is treated as untrusted input.
- [x] `review_status` distinguishes `ai_complete_awaiting_review` from `human_reviewed` and
      `corrected_by_human`; the UI shows which one applies.
- [x] Technical detail moved under a `<details>` "More details" disclosure; the citizen's original
      wording is always shown unchanged under "Your original words".
- [x] Corrections are **separate rows** (`ChallengeScreeningCorrection`) — original value, corrected
      value, author, reason, timestamp. The model output is never overwritten.
- [x] Rules-only runs are labelled as rules-only in the API (`source`, `warnings`) and in the UI.

**Duplicate detection by city (§5)** — `backend/app/services/duplicate_service.py`
- [x] City and locality are normalised (`normalise_city`, `CITY_ALIASES`, suffix stripping) and stored
      as `Challenge.city_key` / `locality`, so "Pune ", "pune," and "Pune City" bucket together.
- [x] Candidates are filtered **in the database** by `city_key`, then scored deterministically:
      `0.60 × shared wording + 0.20 same locality + 0.12 same domain + 0.08 same city`.
- [x] Groq may add a *semantic verdict* (`duplicate | related | different`) on the closest matches and
      can only promote a borderline case when there is also real textual evidence.
- [x] Nothing is ever merged or deleted: the result is a list of candidates with reasons, and likely
      duplicates share a `group_key` cluster.

**Connected system (§1)**
- [x] `UI.journey()` renders one timeline (reported → analysed → reviewed → accepted → project →
      impact) from the same records on the problem page.
- [x] `UI.breakdown`, `UI.bars`, `UI.donut` and `UI.tabs` turn the dashboards into an interactive
      workspace (Overview / Work / Activity) with real filters — every chart is computed from rows the
      signed-in role is allowed to read.
- [x] `GET /api/challenges` gained `city` and `search` filters plus a real `available_cities` list.

**Security fixes found during the audit**
- [x] `/uploads` is **no longer a public static mount**. Evidence is streamed only through
      `GET /api/challenges/{id}/attachments/{aid}/raw`, which checks ownership, reviewer role or
      project membership, and sends `X-Content-Type-Options: nosniff` + no-store caching.
- [x] Uploads are validated server-side: magic-byte sniffing (`sniff_type`), size limit, generated
      filename from the content hash, path traversal impossible (no client input in the path).
- [x] Metadata is stripped: Pillow decode + re-encode when available, otherwise a dependency-free
      JPEG/PNG/WebP segment stripper. The method actually used is recorded on the row and shown in the UI.
- [x] `main.py::validation_handler` now runs errors through `jsonable_encoder`, fixing a 500 on any
      validator that raises `ValueError`.

**Data migration** — `backend/app/database.py`
- [x] `migrate_additive()` adds new columns to an existing SQLite database (never drops or rewrites),
      creates missing indexes, and backfills `challenges.city_key` from `challenges.city`, so the
      existing demonstration database is upgraded in place rather than discarded.

## New UI primitives (revision 2)

| Component | Purpose |
|---|---|
| `.stepper` | progress indicator for multi-step forms |
| `.choice-grid` / `.choice` | large selectable cards (domain picker) |
| `.segmented` / `.segmented__option` | compact single-choice control (impact type) |
| `.dropzone` / `.upload-item` | evidence upload with previews, progress and removal |
| `.summary-hero` | the "In simple words" headline block |
| `.insight` / `.insight-grid` | one labelled answer block from the AI summary |
| `details.disclosure` | progressive disclosure for advanced technical detail |
| `.dup-card` | a possible-duplicate candidate with its reasons |
| `.journey` | the report → solution timeline |
| `.tabs` | dashboard sections |
| `.donut` / `.chart` / `.breakdown` | dependency-free SVG/HTML charts |
| `.evidence-strip` / `.evidence-thumb` | private evidence photos |
| `.record-links` / `.record-link` | "one connected system" cross-links between records |

All of them reuse the existing tokens only — no new colours, no second accent, one shadow tier,
and every non-essential transition is still removed under `prefers-reduced-motion`.
