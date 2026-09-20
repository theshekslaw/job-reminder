# job-hunt — Personal Job-Search Backend (Plan v2)

## Context

`job-hunt` (this folder, `the repo root`) is the **main repo** — a small, auditable application pipeline, not just a Claude Code workflow. It consumes:

- **ai-job-search** as a *vendored package* (git submodule → sync into the workspace). It is a Markdown-prompt framework, not an npm package, so "package" = pinned submodule + a sync script that copies the framework files in and re-applies our overlays. Upgrades = bump submodule, re-sync.
- **gbrain** as an *external tool* (global bun install + MCP server), never vendored. Memory/history only — **optional, non-critical** (see Phase D).

**Invariants (architectural, not polite instructions):**
1. **NEVER SUBMIT.** Scrape ✅ Evaluate ✅ Draft ✅ Validate ✅ Publish ✅ — Submit ❌. No browser automation for job boards, no submission APIs, no job-board login flows. The user submits manually.
2. **Side effects require APPROVED state.** The DB publish record, archive, resume publish, and the gbrain event are code-gated behind the state machine, not prompt-gated.
3. **PII never reaches git.** To be precise: your personal data (name, email, phone, work history, documents) IS fully used — in the profile, every draft, every PDF, the DB, and MinIO; that's the product. The rule is only about **storage location**: personal artifacts never get committed/pushed to GitHub (gitignored + pre-push hook + `security_guards.py`), they live in local files, the local DB, and local MinIO.
4. **Fact hierarchy.** Tier 1 authoritative facts (education, employment, skills, projects, certs, location, notice period) live ONLY in `01-candidate-profile.md`. Tier 2 history (applied/interviewed/rejected) lives in gbrain. Tier 3 derived data (fit score, ATS keywords, tailoring) is computed from JD + Tier 1. **Tier 2/3 can never create Tier 1 facts.**

### Three boundaries

```
DATA BOUNDARY        JobPosting (normalized) + candidate profile
      ↓
AI PIPELINE          evaluate → draft → compile → validate (ATS)
      ↓
HUMAN BOUNDARY       blocking review gate (approve / revise / reject)
      ↓
SIDE-EFFECT BOUNDARY publish PDF · archive · DB publish record · gbrain event
```

### Application state machine

```
DISCOVERED → EVALUATED → DRAFTED → COVERAGE_CHECK
                             ↑          ├─ score ≥ threshold ────────→ VALIDATED
                             ├──────────┤ score < threshold, retries left → DRAFTED
                             │          └─ retries exhausted → COVERAGE_BLOCKED
                             │                                      └─ HUMAN_OVERRIDE → VALIDATED
                             │
VALIDATED → AWAITING_APPROVAL ├─ REVISE → DRAFTED
                              ├─ REJECT → REJECTED
                              └─ APPROVE → PUBLISHED
```

State lives in Postgres (`applications` table, Phase B). A `HUMAN_OVERRIDE` past a blocked coverage check is an explicit recorded transition — `override_reason`, `override_actor`, `override_at`, `original_coverage_score` — never a hidden boolean, so `state=VALIDATED, coverage=71` is always explainable later.

**Identity: job identity is separate from source identity.** `application_id = sha256(canonical_company + canonical_role + canonical_location)[:16]` (canonicalization shared between `tools/job_key.py` and TS). Source sightings are evidence, not identity: a `job_sources` table maps `application_id → (source, source_id, url, first_seen_at)`, so the same Google ML Engineer posting seen on LinkedIn (id 12345) and Naukri (id 98765) is ONE application with two source rows.

**Dedup hierarchy (evidence for merging into one application_id):** (1) `source + source_id` already known → (2) canonical URL → (3) company + normalized title + location (the identity hash) → (4) fuzzy title/description similarity flagged for human confirmation.

---

## Repo layout

```
job-hunt/
├── PLAN.md
├── README.md                     # personal, prepare-only, seeded from ai-job-search
├── .env                          # GITIGNORED — see below
├── .env.example                  # committed template
├── packages/
│   └── ai-job-search/            # git submodule → theshekslaw/ai-job-search (pinned SHA)
├── overlays/                     # our patches, re-applied on every sync
│   ├── apply.md.patch            # review gate + ATS gate + publish step
│   └── ...
├── scripts/
│   ├── sync-framework.ts         # submodule → workspace copy + overlay apply
│   └── hooks/pre-push            # remote + PII guard
├── Makefile                      # single entry point: setup, db-up, sync, scrape, stats, test…
├── Dockerfile                    # backend image (bun + uv-managed Python tools)
├── pyproject.toml + uv.lock      # Python deps via uv (pypdf, psycopg, minio, pytest…)
├── package.json + bun.lock       # TS deps for src/ and portal CLIs
├── docker-compose.yml            # Postgres 16 (records DB) + Adminer UI + MinIO (resume store)
├── db/
│   └── migrations/               # SQL migrations (init tables)
├── src/                          # the "backend": bun/TS CLI `job-hunt`
│   ├── cli.ts                    # scrape | apply | approve | stats | queue | db up
│   ├── db.ts                     # Postgres client (bun `postgres`), repositories
│   ├── state.ts                  # state machine + APPROVED guard (DB-backed)
│   ├── schema.ts                 # JobPosting normalized type
│   └── runlog.ts                 # per-run tokens/duration/stage log → runs table
├── state/                        # GITIGNORED: pgdata/ docker volume, local caches
├── .claude/
│   ├── commands/                 # synced from framework + OURS: stats.md, queue.md
│   └── skills/                   # synced (job-application-assistant, job-scraper, upskill)
├── .agents/skills/               # portal CLIs (linkedin, freehire, + naukri new)
├── tools/                        # synced + NEW: ats_score.py, publish_guard.py
├── documents/  cv/  cover_letters/  resume/  company_research/   # all GITIGNORED
└── CLAUDE.md                     # profile + workflow contract + memory protocol
```

### .env (gitignored; `.env.example` committed)

```
USER_NAME=abhishek
USER_EMAIL=...
USER_PHONE=...
JOB_INTERESTS="software engineer,machine learning engineer"
JOB_LOCATIONS="Bengaluru, India;Remote"
KEYWORD_COVERAGE_THRESHOLD=96
RESUME_USERNAME=abhishek
# Portal API tokens: commented placeholders ONLY — add a real secret only when a
# legitimate supported integration actually exists (no credential graveyard).
# LINKEDIN_API_TOKEN=
GBRAIN_SOURCE=jobsearch
OVERLEAF_GIT_URL=...              # optional, premium git-bridge
DATABASE_URL=postgres://jobhunt:jobhunt@localhost:5433/jobhunt
MINIO_ENDPOINT=http://localhost:9000
MINIO_ACCESS_KEY=jobhunt
MINIO_SECRET_KEY=...              # local-only credentials
MINIO_BUCKET=resumes
```

**Credential policy (honest version):** secrets live only in `.env`, read as `<SERVICE>_API_TOKEN` per the portal contract, and are added **only when a board offers a legitimate authenticated API we actually integrate** — until then they exist solely as commented placeholders in `.env.example`. We do NOT do logged-in headless-browser scraping of LinkedIn/Indeed/Naukri — that violates their ToS and risks your accounts; scraping stays on public endpoints.

**Env isolation rule:** headless Claude runs get a **whitelist**, never `process.env` wholesale — `JOB_INTERESTS`, `JOB_LOCATIONS`, `RESUME_USERNAME`, `KEYWORD_COVERAGE_THRESHOLD` (identity fields like email/phone reach drafts via the profile files, not env). API tokens, `DATABASE_URL`, and MinIO secrets are NEVER injected into Claude's environment or prompts; only the deterministic Python/TS tools read them.

### Resume template (fixed, user-supplied)

Every generated resume uses the user's own template at `resume-example/resume.latext` (rendered sample: `resume-example/machine_learning_abhishek_pandey.pdf`):

- Jake's-resume-style single-column, `\documentclass[10pt]{article}`, letterpaper, tight margins, **exactly 1 page**.
- Standard packages only (titlesec, enumitem, hyperref, fancyhdr, tabularx, geometry, xcolor) → compiles with plain **pdflatex** — no moderncv, no fontspec. This replaces the framework's moderncv/lualatex default for the CV.
- Section order: Header → Professional Summary → Technical Skills (two-column tabular) → Experience (company `\resumeSubheading` + overall bullets + per-project `\resumeExperienceProject` blocks) → Projects → Education → Achievements & Certifications.
- Fixed macros (`\resumeItem`, `\resumeSubheading`, `\resumeExperienceProject`, `\resumeProjectHeading`, list wrappers) — the drafter edits *content* inside these macros only, never the preamble/macros. Known pitfalls to encode in the manifest: escape `%` in metrics, `---` em-dashes, company headings are NOT list items.
- Registered via the framework's own mechanism: `templates/cv/abhishek-default/{template.tex, TEMPLATE.md}` + activation through `/add-template --use abhishek-default` (injects the ACTIVE-TEMPLATE block that `/apply` reads: `CV_EXT=.tex`, `CV_COMPILE=pdflatex`, page limit 1). The manifest carries **machine-readable constraints** — `editable_regions: [summary, skills_rows, experience_bullets, project_selection]`, `forbidden_regions: [preamble, macro_definitions, geometry, packages]` — and a `tools/template_guard.py` validates every LLM diff against them before compilation (preamble/macro change → reject).
- Tailoring = targeted Edits to a copy of the master (summary keywords, skills rows, bullet emphasis/reorder, project selection) — never a from-scratch regeneration (token efficiency + layout safety).
- `verify_pdf.py --pages 1` (CLAUDE.md overlay: CV is 1 page, not the framework's 2).
- **Published filename matches the user's example:** `resume/{company}/{role}_{username}.pdf` (e.g. `resume/google/machine_learning_abhishek_pandey.pdf`).
- Cover letter keeps the framework default (cover.cls, xelatex) unless a custom one is supplied later.

---

### Tooling

- **uv** manages all Python: `pyproject.toml` + `uv.lock` declare the tools' deps (`pypdf`, `psycopg[binary]`, `minio`, `python-dotenv`, `pytest`); everything runs as `uv run tools/ats_score.py …` — no global pip, no venv juggling. The framework's Python tools (`verify_pdf.py`, `security_guards.py`, `job_key.py`, …) run under the same `uv run`.
- **bun** manages TypeScript: `src/` backend CLI + portal CLIs (`bun install`, `bun test`).
- **Makefile** is the single entry point so nobody memorizes commands:
  ```
  make setup      # uv sync + bun install + git hooks + submodule init + framework sync
  make db-up      # docker compose up -d (postgres + adminer + minio, bucket init)
  make db-down / make migrate
  make sync       # re-sync framework from submodule + re-apply overlays
  make scrape / make stats / make queue        # wrap `bun run job-hunt …`
  make compile-example                          # sanity-compile the master CV
  make test       # uv run pytest + bun test + tools/lint_skills.py + security_guards.py
  ```
- **Dockerfile** builds the backend image (oven/bun base + uv + the Python tools) so `job-hunt` can run containerized alongside the compose stack later; LaTeX is NOT baked into it (huge) — instead `make compile` prefers local pdflatex and falls back to the `texlive/texlive:latest-small` image (`docker run -v $PWD:/work texlive … pdflatex`) so a TeX install isn't strictly required on the host.

## Phase A — Bootstrap the job-hunt repo

1. `git init` in job-hunt, **default branch `main`** (no hard-coded master; push with `git branch --show-current`).
2. Create **private** `theshekslaw/job-hunt` (gh if authed as theshekslaw, else user creates in web UI); `git remote add origin git@github.com-shek:theshekslaw/job-hunt.git`; local identity `theshekslaw <pandeyabhishek1518@gmail.com>`.
3. `git submodule add git@github.com-shek:theshekslaw/ai-job-search.git packages/ai-job-search`, pinned to a reviewed SHA.
4. `scripts/sync-framework.ts`: copies `.claude/`, `.agents/`, `tools/`, `cv/main_example.tex`, `cover_letters/` scaffolding, `documents/README.md`, `.gitignore` rules from the submodule into the repo root, then applies `overlays/`. Idempotent; run after every submodule bump.
5. **Defense in depth for PII** (not just a disabled push URL):
   - `.gitignore`: framework rules + `resume/`, `state/`, `.env`.
   - `tools/security_guards.py`: extend `REQUIRED_IGNORE_RULES` with `resume/`, `state/`, `.env` (same commit as .gitignore).
   - `scripts/hooks/pre-push` (installed via `git config core.hooksPath scripts/hooks`): **reject any push to a remote other than `origin`**, and reject if the pushed tree contains `documents/`, `cv/main_*` (except example), `resume/`, `state/`, `.env`, or the tracker.
6. Tooling scaffold: `pyproject.toml` (uv), `package.json` (bun), `Makefile`, `Dockerfile`, `docker-compose.yml` — `make setup && make db-up && make test` must pass on a fresh clone.
7. `.env.example`, README (personal, prepare-only), first push to `main`.

**Verify:** repo PRIVATE; `git push` to origin works; a test push to any other remote is rejected by the hook; `python3 tools/security_guards.py` passes.

## Phase B — Records DB (Docker), schema, state machine, side-effect guard

1. **Records DB + object store in Docker:** `docker-compose.yml` with Postgres 16 on port 5433 (avoids clashing with any local 5432), Adminer at `localhost:8080` for browsing records, and **MinIO** (S3-compatible, free) at `localhost:9000` (console `localhost:9001`) as the **resume store**. Volumes under `state/pgdata/` and `state/minio/` (gitignored). `job-hunt db up|down|migrate` wraps `docker compose` (MinIO bucket `resumes` auto-created on first up). SQL migrations in `db/migrations/`:
   - `job_postings` — normalized postings: `(application_id PK, company, title, location, remote_type, description, salary, posted_at, scraped_at)`.
   - `job_sources` — source sightings: `(application_id FK, source, source_id, url, first_seen_at)` — one job, N boards.
   - `applications` — state machine: `(application_id FK, state, fit_rating, keyword_coverage_score, revision_count, cv_path, resume_path, override_reason, override_actor, override_at, original_coverage_score, created_at, updated_at)`.
   - `transitions` — audit trail: every state change `(application_id, from_state, to_state, reason, actor, at)`.
   - `rankings` — /rank explainability: `(application_id, rank_score, matched_requirements, missing_requirements, reason, model, at)` — so "why was this discarded?" is always answerable.
   - `runs` — observability: `(run_id UUID PK, parent_run_id, application_id, command, stage, attempt, model, tokens_in, tokens_out, duration_ms, result, at)` — parent/child correlation lets one /apply run show its evaluation, draft attempts, coverage checks, and revisions as a tree.
   - `review_packets` — everything the human gate needs, persisted before any headless process exits: `(application_id, draft_tex_path, pdf_path, coverage_report JSONB, tailoring_decisions JSONB, reviewer_output JSONB, created_at)` — `job-hunt queue` reconstructs full review context from this.
   **The DB is the ONLY system of record — there is no tracker CSV.** Framework commands that read/write `job_search_tracker.csv` (`/outcome`, `/interview`, `/gmail-sync`, `/rank`, `/html-report`, `/upskill`) are overlaid to call `uv run tools/tracker_db.py <query|record> …` against Postgres instead. An optional `job-hunt export-csv` produces a human-readable dump on demand (derived view, never read back).
2. `src/schema.ts` — normalized `JobPosting`: `{source, source_id, url, company, title, location, remote_type, description, salary?, posted_at?, scraped_at}`. Every portal CLI already outputs `{id,title,company,location,date,url}`; a thin normalizer maps portal JSON → JobPosting. `/apply` never cares which portal produced the JD.
3. `application_id` derivation (extend `tools/job_key.py` so Python and TS agree on one canonicalization).
4. `src/state.ts` — the state machine above, DB-backed; transitions validated and recorded in `transitions`.
5. `tools/publish_guard.py` — the ONLY entry point that writes side effects (DB publish record, archive dir, resume upload, gbrain event). First line of defense: reads the application's state from the DB — `if state != "APPROVED": raise RuntimeError("Publication requires explicit approval")`. The prompt in apply.md calls this script instead of doing raw writes — generation is separated from side effects, and the guard is testable.
6. **Graceful degradation:** if Docker/Postgres is down, everything that records state fails fast with "run `make db-up`" — drafting can still be exercised interactively, but nothing is published or recorded without the DB. No shadow state files.

**Verify:** `docker compose up -d` + migrations apply; unit tests — illegal transitions raise; `publish_guard.py` refuses non-APPROVED; same JD from two portals yields one `application_id` with two `job_sources` rows; Adminer shows the tables.

## Phase C — Pipeline: review gate + ATS ≥96 + publish

Overlay on `apply.md` (framework's drafter→reviewer pipeline stays intact):

1. **Keyword-coverage gate** (honest name — this measures how much of the JD's extracted requirement vocabulary the resume covers; a real ATS also judges parsing, structure, and semantics, so we don't call it an "ATS score"): new `tools/keyword_coverage.py` computes `score = 100 × covered / (covered + missing_have + missing_gap)` (synonym-only counts 0.5) from the Step-5d table. Gate: revise-and-recompile loop until `score ≥ KEYWORD_COVERAGE_THRESHOLD` (default 96 from .env) **or 3 iterations** → then state `COVERAGE_BLOCKED` with the gap report; proceeding requires the explicit `HUMAN_OVERRIDE` transition (reason/actor/original score recorded). The framework's "never keyword-stuff" rule stays — a JD demanding skills you don't have blocks honestly instead of stuffing.
2. **Human review gate** (state AWAITING_APPROVAL): present absolute PDF path, tailoring-decisions summary, keyword-coverage score + table, fit rating — then STOP. Approve → `publish_guard.py` runs (DB `state=PUBLISHED` + archive + resume upload + gbrain event). Revise → loop drafter with your notes. Reject → state REJECTED, nothing published.
3. Publish target: **MinIO is the primary resume store** — `publish_guard.py` uploads the approved PDF to bucket `resumes` at key `{company}/{role}_{username}.pdf` (matches the user's example `machine_learning_abhishek_pandey.pdf`; role prefix prevents two-roles-at-one-company collision) and records `resume_path = s3://resumes/{company}/{role}_{username}.pdf` in the `applications` row. Browse/download via the MinIO console (`localhost:9001`) or `job-hunt open <application_id>` (presigned URL / local download). If MinIO is down, the guard falls back to the local `resume/{company}/` folder (gitignored) and marks the row for re-upload — a storage outage never blocks an approval.

**Verify:** pytest on keyword_coverage + template_guard; an /apply dry-run where the gate blocks, one revision loops, approval publishes, and `git status` stays clean.

**Milestone note:** the first end-to-end milestone may publish to the local `resume/` folder; the MinIO backend lands immediately after — storage sits behind one interface in `publish_guard.py`, so swapping is contained.

## Phase D — gbrain (optional, non-critical memory)

1. Install: `bun install -g github:garrytan/gbrain#latest-stable` (GitHub only — the npm `gbrain` is a different project); `gbrain init --pglite --no-embedding`; `gbrain sources add jobsearch`; `claude mcp add gbrain -- gbrain serve --surface verbs`.
2. **Non-critical contract** in CLAUDE.md's Memory Protocol: `/apply` requires profile + JD; gbrain is OPTIONAL — unavailable (PGLite single-writer lock, not installed, crashed) → log one warning line and continue. Only explicit memory commands surface gbrain failures.
3. Writes: on approval → `remember` kind:event (company, role, date, application_id, status) under source `jobsearch`; on `/outcome` → status-change events. Reads: `/apply` start → `recall` company history as context only.
4. Fact hierarchy enforced: profile facts → `01-candidate-profile.md` FIRST, mirror to gbrain second (apply.md:7-11 strips anything not grounded there — gbrain is never a draft-grounding source).

**Verify:** remember → new session → recall round-trip; kill gbrain and confirm /apply still completes with a warning.

## Phase E — Portals

1. Keep `linkedin-search` (public jobs-guest endpoints; `--location "Bengaluru, Karnataka, India"` etc.) and `freehire-search`; Danish portals stay `enabled: false`.
2. **Naukri:** new portal CLI via `/add-portal` (robots.txt check, honest UA, `search`+`detail`, `--format json`, backoff, zero deps, live-query test). Output feeds the JobPosting normalizer.
3. **Indeed:** Cloudflare-hostile → WebSearch `site:indeed.com` queries in `search-queries.md` are the PRIMARY path; a CLI only if a public endpoint proves stable.
4. **Wellfound:** feasibility probe first (robots.txt + unauthenticated job-page fetch); auth-walled → decline per contract, record the decision.
5. `search-queries.md` generated from `.env` `JOB_INTERESTS` × `JOB_LOCATIONS` by the sync script, so changing interests is a .env edit.
6. `python3 tools/lint_skills.py` after each portal.

**Verify:** each portal returns parseable JSON → normalizer → dedup produces stable application_ids; a job seen on two boards collapses to one entry.

## Phase F — Backend CLI + Claude Code integration + observability

The "backend" is `src/cli.ts` (`bun run job-hunt <cmd>`), driving Claude Code **headless** (`claude -p "/scrape" --output-format json`) so runs are scriptable and usage is capturable:

0. **`src/claude-runner.ts` — the ONLY place that touches the raw CLI envelope.** `ClaudeRunner.run({command, prompt, model, timeout, allowedEnv})` → `ClaudeRunResult {status, output, usage, duration, model, error}`. The rest of the backend never parses `claude -p` output directly, so a CLI format change is a one-file fix. It also enforces the env whitelist (no secrets into Claude's environment).
1. `job-hunt scrape` — runs /scrape headless via the runner, normalizes + dedups into state (DISCOVERED), logs tokens/duration into `runs`.
2. `job-hunt apply <application_id|url>` — runs /apply; the human gate still happens interactively (headless runs stop at AWAITING_APPROVAL; approval itself is `job-hunt approve <id>` or interactive). **Before the headless process exits it persists the full review packet** (draft .tex, PDF, coverage report, tailoring decisions, reviewer output → `review_packets` + files under `documents/applications/<application_id>/`), so `job-hunt queue` can reconstruct complete review context later.
3. `job-hunt stats` + **`/stats` slash command**: applications by state (drafted/applied/interview/offer/rejected — one SQL query over `applications` + `transitions`), per-run and cumulative **token usage** (from the `runs` table), average pipeline durations, revision counts, ATS score distribution.
4. `job-hunt queue` / `/queue` — SELECT over `applications WHERE state='AWAITING_APPROVAL'` — what's awaiting your review right now.
5. **Run log** = the `runs` table (Phase B): `{application_id, stage, model, tokens_in, tokens_out, duration_ms, result, at}` — lightweight observability browsable in Adminer, no Prometheus; humanity has suffered enough.

**Verify:** `job-hunt scrape` end-to-end headless; `/stats` renders correct counts against seeded rows; token numbers match the headless JSON usage fields.

## Phase G — LaTeX rendering: local first, Overleaf optional

- **Primary: local compile** — **pdflatex** for the CV (the user's article-class template needs only standard packages: `tlmgr install titlesec enumitem` on BasicTeX if missing) and xelatex for the framework cover letter (cover.cls + bundled fonts). Local compile is required regardless, because `verify_pdf.py`/`verify_layout.py`/ATS extraction all need the PDF locally, and it's free and instant. Also: `pypdf`, optionally Poppler.
- **Overleaf (optional, P2):** Overleaf has no free compile API; its git-bridge needs a premium account. If `OVERLEAF_GIT_URL` is set, an optional `job-hunt overleaf-sync <id>` pushes the approved `.tex` to the Overleaf project so you can view/polish online. Overleaf is a viewing/editing convenience — never in the critical path.

## Phase H — Token-efficiency measures (fewer tokens per resume)

1. **Rank before apply:** `/rank` triages in batches (~5 jobs/agent, posting text only, no research) so full drafter→reviewer spend happens only on jobs above threshold.
2. **Diff-based drafting:** the drafter edits `cv/main_example.tex` (master) with targeted Edits rather than regenerating the document — the framework's token-efficiency rules (never re-Read files in context; pass reviewer content inline, apply.md:13-15) are kept by the overlay.
3. **Model tiering:** headless runs pass `--model` — cheap model (Haiku) for scrape normalization and rank; the strong model only for draft + review. Compile/layout/ATS loops are zero-LLM Python.
4. **Caches honored:** `company_research/*.json` TTL-30d (reviewer reuses), `seen_jobs.json` dedup means a JD is never evaluated twice, gbrain `recall` is budget-packed.
5. **Compact grounding:** keep `01-candidate-profile.md` tight (facts, not prose) — it's read on every draft.
6. `/stats` exposes tokens-per-application so regressions are visible.

## Phase I — Setup + acceptance test

1. Prereq check: bun, **uv**, **Docker Desktop** (Postgres + MinIO + Adminer via `make db-up`; also the texlive-image compile fallback), TeX optional locally (pdflatex for the CV, xelatex for the cover letter — else the docker texlive fallback), `claude` CLI. Then `make setup`.
2. **You provide:** CV → `documents/cv/`, LinkedIn export → `documents/linkedin/`, filled `.env`.
3. `/setup` (from-documents path) populates CLAUDE.md + skills 01–07 + master CV; sync script generates `search-queries.md` from .env.
4. Sanity-compile example CV + cover letter.
5. `job-hunt scrape` (LinkedIn or Naukri) → normalized, deduped, state DISCOVERED.
6. **Acceptance test** on one real JD: evaluate → draft → coverage gate (observe a sub-96 draft loop) → AWAITING_APPROVAL blocks → request one revision → approve → `publish_guard` writes the DB publish record + archive + uploads `s3://resumes/<company>/<role>_abhishek.pdf` (or local `resume/` in the first milestone) + gbrain event → `/stats` shows the application with token counts; review packet visible via `job-hunt queue` history.
7. `git status` clean of PII; push `main`.

---

## Priorities (from review)

| P | Item | Phase |
|---|------|-------|
| P0 | Hard approval state machine + publish guard (incl. COVERAGE_BLOCKED → HUMAN_OVERRIDE) | B, C |
| P0 | Pre-push protection (hook + guards) | A |
| P0 | Normalized JobPosting + job identity separate from source identity (`job_sources`) | B |
| P0 | DB is the sole system of record — no tracker CSV (overlay framework commands to query DB) | B |
| P0 | ClaudeRunner adapter isolating the raw CLI envelope | F |
| P1 | gbrain non-critical | D |
| P1 | Generation/side-effect separation + review-packet persistence | B, C, F |
| P1 | Never-submit invariant (no submission code paths, no board logins) | all |
| P1 | MinIO resume backend (local `resume/` for the first milestone) | C |
| P2 | Pipeline logging + /stats | F |
| P2 | `main` branch, no hard-coded master | A |
| P2 | Overleaf sync | G |

### Source of truth (one job each)

| Data | Source of truth |
|------|-----------------|
| Candidate facts (Tier 1) | `01-candidate-profile.md` |
| Pipeline state, transitions, rankings, runs | Postgres |
| Resume artifacts | MinIO (local `resume/` until the MinIO milestone) |
| History/context (Tier 2) | gbrain |
| CSV export | derived view via `job-hunt export-csv`, never read back |

## Risks
- **Coverage 96 vs. honesty:** a hard 96 on a mismatched JD would force keyword stuffing; the gate therefore blocks (`COVERAGE_BLOCKED`) and reports the gap, with the recorded `HUMAN_OVERRIDE` as the only way past. Threshold is .env-tunable.
- **Indeed blocking:** near-certain; WebSearch is the primary Indeed path.
- **Wellfound auth-wall:** likely; graceful decline; LinkedIn + Naukri + freehire cover the need.
- **Board credentials:** storing them is fine (gitignored .env); *using* them for automated login/apply is a ToS/account-ban risk and stays out of scope by invariant #1.
- **Submodule drift:** framework updates land via submodule bump + re-sync; overlays may need rebasing — sync script diffs and warns.
- **PGLite single-writer:** second concurrent session loses gbrain; pipeline continues (non-critical contract).
- **Docker dependency:** Postgres/MinIO down → anything that records state fails fast with "run `make db-up`" (no shadow state files); MinIO-only outages fall back to local `resume/` with re-upload on next `db up`. PII note: `state/pgdata/` and `state/minio/` volumes hold your data locally and are gitignored.
- **PII:** gitignore + guards + pre-push hook + private repo; `resume/`, `state/`, `.env`, `documents/` never version-controlled.

## Critical files (new/edited)
- `scripts/sync-framework.ts`, `scripts/hooks/pre-push`, `overlays/apply.md.patch`
- `Makefile`, `Dockerfile`, `pyproject.toml`/`uv.lock`, `docker-compose.yml`, `db/migrations/*.sql`
- `src/{cli,db,state,schema,runlog,claude-runner}.ts`, `tools/{keyword_coverage,template_guard,publish_guard,tracker_db}.py`, extended `tools/job_key.py`
- `templates/cv/abhishek-default/{template.tex,TEMPLATE.md}` (from `resume-example/resume.latext`)
- `.claude/commands/{stats,queue}.md`, `CLAUDE.md` (Memory Protocol, invariants, RESUME_USERNAME)
- `.env.example`, `.gitignore` + `tools/security_guards.py` (same-commit rule)
