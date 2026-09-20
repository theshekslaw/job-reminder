# job-hunt

**A personal, private job-search pipeline built on Claude Code.** It scrapes job boards,
ranks matches against your profile, tailors a 1-page LaTeX resume + cover letter per job
description, scores keyword coverage, and **stops at a mandatory human review gate** —
you inspect every PDF before anything is recorded or published.

> **Prepare-only, by architectural invariant.** This system never submits applications,
> never logs into job boards, and never automates a browser. You review, you submit.

Built on [ai-job-search](https://github.com/MadsLorentzen/ai-job-search) (vendored as a
git submodule) with [gbrain](https://github.com/garrytan/gbrain) as optional cross-session
memory over MCP.

---

## How it works

```
make scrape                 zero-LLM sweep of portal CLIs (LinkedIn, freehire)
      │                     → normalize → dedup → Postgres (DISCOVERED)
      ▼
make run ARGS="digest"      your daily list, rank-sorted
      │
      ▼  you pick one
/apply <url>  (in Claude Code)   or   make run ARGS="apply <id>"  (headless)
      │
      ├─ evaluate fit (scored 0-100, source-host verified)
      ├─ draft resume from YOUR fixed template (content-only edits, guarded)
      ├─ independent reviewer agent critiques (grounding audit — no fabrication)
      ├─ compile PDF (pdflatex, exactly 1 page) + layout + ATS text-layer checks
      ├─ keyword-coverage gate (≥96 or it blocks honestly — never keyword-stuffs)
      ▼
REVIEW GATE — the run STOPS. You read the PDF.
      │
      ├─ approve → publish_guard uploads to MinIO, archives, records, gbrain event
      ├─ revise  → your notes loop the drafter, gate again
      └─ reject  → closed, nothing published
      │
      ▼  you submit by hand, then:
make run ARGS=... record-outcome  →  applied / interview / offer / hired …
make stats                            states + token spend per application
```

Every application moves through an audited state machine in Postgres:

`DISCOVERED → EVALUATED → DRAFTED → COVERAGE_CHECK → (COVERAGE_BLOCKED →) VALIDATED → AWAITING_APPROVAL → PUBLISHED | REJECTED`

Full design: [.claude/architecture/DESIGN.md](.claude/architecture/DESIGN.md) ·
plan: [.claude/architecture/PLAN.md](.claude/architecture/PLAN.md)

---

## Prerequisites

| Tool | Why | Install (macOS) |
|---|---|---|
| [Claude Code](https://claude.com/claude-code) | runs the workflows | see site |
| Docker Desktop | Postgres + MinIO + Adminer, and the LaTeX fallback | docker.com |
| [bun](https://bun.sh) | portal CLIs + backend | `curl -fsSL https://bun.sh/install \| bash` |
| [uv](https://docs.astral.sh/uv/) | Python tools | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| LaTeX *(optional)* | faster local compiles | BasicTeX — otherwise the `texlive/texlive` Docker image is used automatically |

## Setup

```bash
git clone git@github.com:theshekslaw/job-reminder.git job-hunt && cd job-hunt
cp .env.example .env        # fill in YOUR identity, interests, locations
make setup                  # uv sync + bun install + git hooks + submodule + framework sync
make db-up                  # Postgres :5436 · Adminer :8082 · MinIO :9002 (console :9003)
```

Then personalize (each user brings their own data — none ships with the repo):

1. Drop your resume/CV into `documents/cv/` (PDF + source if you have it); LinkedIn
   export into `documents/linkedin/` for a richer profile.
2. Seed the CV template with your resume: copy your 1-page LaTeX resume to
   `templates/cv/abhishek-default/template.tex` **and** `cv/main_example.tex`
   (see `templates/cv/abhishek-default/TEMPLATE.md` for the required macro structure).
3. In Claude Code, run **`/setup`** — it builds your candidate profile files from the
   documents and asks about goals, deal-breakers, languages.
4. Optional memory: `bun install -g github:garrytan/gbrain#latest-stable && gbrain init --pglite --no-embedding && gbrain sources add jobsearch && claude mcp add gbrain -- gbrain serve --surface verbs`

### How the Claude Code integration works (nothing extra to configure)

Claude Code discovers everything from the repo itself the moment you `cd` into it and run
`claude`:

- **Slash commands** come from `.claude/commands/` — `/shek-apply`, `/apply`, `/scrape`
  (via the job-scraper skill), `/rank`, `/setup`, `/outcome`, `/interview`, `/upskill`…
- **Skills** come from `.claude/skills/` (auto-trigger on topic) and `.agents/skills/`
  (portal CLIs).
- **Pre-approved permissions** ship in `.claude/settings.json`, so the pipeline tools run
  without prompt-spam.
- **CLAUDE.md** carries the behavioral contract (invariants, state machine, memory protocol).

So onboarding a new machine is exactly: clone → `cp .env.example .env` → `make setup` →
`make db-up` → add your documents → `claude` → `/setup` → `/shek-apply`.

## One command: `/shek-apply`

Open Claude Code in the repo and run **`/shek-apply`** — it drives a complete session:

1. **Preflight** — DB up? profile built? (points you to `make db-up` / `/setup` if not)
2. **Scrape** — zero-LLM portal sweep into the DB
3. **Rank** — parallel agents score the fresh candidates against YOUR profile (0-100, with reasons, location + language gates)
4. **You pick** — it presents the ranked table and waits ("top 3" works)
5. **Apply loop** — for each pick: tailor resume + cover letter from your template, independent reviewer critique, compile + 1-page + ATS checks, keyword-coverage gate, then **stops at the review gate** for your approve/revise/reject
6. **Session report** — ready-to-submit table with **apply links**, per-resume keyword-coverage score and fit rating, states of everything touched, and token usage (backend runs from the `runs` table; in-session spend via `/cost`)

It **prepares** applications — you click the apply links and submit. After submitting:
`uv run tools/tracker_db.py record-outcome <id> applied --notes "portal, date"`.

Options: `/shek-apply --skip-scrape` (use what's in the DB), `/shek-apply --limit 20` (rank more candidates).

## Daily use

```bash
make scrape                       # morning sweep (seconds, zero LLM tokens)
make run ARGS="digest"            # what's new, ranked — pick an id
make run ARGS="apply <id>"        # tailor resume for THAT job (headless, stops at gate)
make run ARGS="queue"             # everything waiting for your review
make run ARGS="approve <id>"      # after you've read the PDF
make run ARGS="reject <id> too far from my stack"
make run ARGS="override <id> <reason>"   # recorded human override past a coverage block
make stats                        # applications by state + token usage
make run ARGS="export-csv" > apps.csv    # derived view, never read back
```

Or interactively in Claude Code: `/scrape`, `/apply <url>`, `/rank`, `/interview`,
`/outcome`, `/upskill`, `/setup --section <name>`.

After you submit an application by hand:

```bash
uv run tools/tracker_db.py record-outcome <id> applied --notes "company portal, 2026-09-21"
```

## Where things live

| Data | Store | Browse |
|---|---|---|
| Postings, state machine, transitions, rankings, runs, review packets | Postgres (Docker) | Adminer http://localhost:8082 (server `db`, user/pass/db `jobhunt`) |
| Approved resume PDFs (`{company}/{role}_{username}.pdf`) | MinIO bucket `resumes` | http://localhost:9003 |
| Candidate facts (THE grounding source) | `.claude/skills/job-application-assistant/01-candidate-profile.md` (local-only) |
| Application history / memory | gbrain (optional, source `jobsearch`) |
| Drafts & archives | `cv/`, `cover_letters/`, `documents/applications/` (all local-only) |

## Privacy model

Your data is **fully used locally** — drafts, DB, MinIO — and **never reaches git**:

- Gitignored: `.env`, `documents/`, `state/` (DB+MinIO volumes), `resume/`, the master CV,
  the template skeleton, profile files `01`/`02`, all generated CVs/letters/PDFs.
- `scripts/hooks/pre-push` rejects pushes to any remote except `origin` and scans every
  pushed tree for personal-data paths.
- `tools/security_guards.py` pins the ignore rules and the Claude permissions allowlist (CI-style check in `make test`).
- Before any push, the **`pre-push-review` skill** runs: secrets sweep, PII sweep,
  code-vulnerability review, and the PR-description convention.
- Headless Claude runs get a whitelisted env only — DB/MinIO credentials and API tokens
  never enter prompts.

## Extending

- **New job board:** `/add-portal <site>` — scaffolds a portal CLI against the contract
  (robots.txt check, honest UA, JSON output). Boards that block bots get declined and
  covered via WebSearch `site:` queries instead — see the decision log in
  `.claude/skills/job-scraper/search-queries.md` (Naukri, Wellfound, Indeed are declined there).
- **New resume template:** `/add-template` — any toolchain, activated via the
  `ACTIVE-TEMPLATE` block.
- **Framework updates:** `cd packages/ai-job-search && git pull && cd ../.. && make sync`
  — overlays re-apply; hash-locked conflicts are flagged, never silently clobbered.

## Commands reference

`make help` lists everything. Key targets: `setup` · `db-up`/`db-down`/`db-logs`/`migrate` ·
`sync` · `run ARGS="…"` · `scrape`/`stats`/`queue` · `build`/`run-docker` ·
`compile-example` · `test` · `clean`.

## Troubleshooting

- **`db: cannot reach Postgres`** → `make db-up` (Docker Desktop must be running).
- **Compile fails, no local TeX** → the Makefile falls back to `docker run texlive/texlive` automatically; first run pulls the image.
- **gbrain MCP won't start in a second session** → PGLite is single-writer; one session at a time (the pipeline continues without it).
- **`security_guards` fails after a framework bump** → an overlay drifted; `make sync` prints the conflicting file, update `overlays/…` + `overlays/overlays.lock.json`.
- **Push rejected by hook** → it found a personal-data path or a non-origin remote; that's it doing its job.
