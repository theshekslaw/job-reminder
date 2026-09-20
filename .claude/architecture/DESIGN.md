# job-hunt — Architecture & Design Patterns

> Companion to [PLAN.md](PLAN.md) (phased implementation plan). This doc is the stable
> design reference: boundaries, invariants, state machine, and the patterns the code follows.

## System overview

```
                  ┌───────────────────┐
                  │   Job Sources     │
                  │ LinkedIn/Naukri/  │
                  │ Indeed/Wellfound  │
                  └─────────┬─────────┘
                            ↓
                    ┌──────────────┐
                    │ Portal        │      pattern: Adapter
                    │ Adapters      │      (one CLI per board, one JSON contract)
                    └──────┬───────┘
                           ↓
                    ┌──────────────┐
                    │ JobPosting   │      normalized schema — /apply never
                    │ Normalizer   │      knows which board a JD came from
                    └──────┬───────┘
                           ↓
                    ┌──────────────┐
                    │ Dedup + ID   │      application_id = hash(company+role+location)
                    └──────┬───────┘      job_sources = per-board sightings (evidence)
                           ↓
                ┌──────────────────────┐
                │   AI Pipeline        │   evaluate → draft → compile
                │   (Claude Code)      │   → keyword-coverage check
                └──────────┬───────────┘
                           ↓
                ┌──────────────────────┐
                │ AWAITING_APPROVAL    │   HUMAN BOUNDARY (blocking)
                │ approve/revise/reject│
                └──────────┬───────────┘
                           ↓
                  ┌─────────────────┐
                  │ publish_guard   │      pattern: Guard — the ONLY writer
                  └────────┬────────┘      of side effects; checks state==APPROVED
                           ↓
             ┌─────────────┼─────────────┐
             ↓             ↓             ↓
          Postgres       MinIO         gbrain
        (state/audit)  (resume PDFs)  (history event)
```

## The three boundaries

1. **Data boundary** — normalized `JobPosting` + candidate profile in.
2. **Human boundary** — every generated resume stops at `AWAITING_APPROVAL`; nothing proceeds without an explicit decision.
3. **Side-effect boundary** — publish/archive/record happen only through `publish_guard.py`.

## Invariants (enforced in code, not prompts)

| # | Invariant | Enforcement |
|---|-----------|-------------|
| 1 | **NEVER SUBMIT** — the system prepares applications; the user submits | no submission code paths, no board logins, no browser automation |
| 2 | **Side effects require APPROVED** | `publish_guard.py` reads state from Postgres and raises otherwise |
| 3 | **PII never reaches git** (data is fully used locally — DB, PDFs, MinIO; it just never gets pushed) | `.gitignore` + `security_guards.py` + pre-push hook (origin-only + PII tree scan) |
| 4 | **Tier 2/3 data never creates Tier 1 facts** | fact hierarchy below |
| 5 | **Template preamble/macros are immutable** | `template_guard.py` rejects diffs outside `editable_regions` |
| 6 | **No secrets in Claude's environment** | `ClaudeRunner` env whitelist |

## Fact hierarchy (LLM grounding)

| Tier | What | Source of truth | Can flow to |
|------|------|-----------------|-------------|
| 1 | Authoritative candidate facts (education, employment, skills, projects) | `.claude/skills/job-application-assistant/01-candidate-profile.md` | drafts, gbrain mirror |
| 2 | History (applied/interviewed/rejected at X) | gbrain (source `jobsearch`) | context for drafting, never facts |
| 3 | Derived (fit score, coverage, tailoring) | Postgres, computed per JD | reporting |

## Application state machine

```
DISCOVERED → EVALUATED → DRAFTED → COVERAGE_CHECK
                             ↑          ├─ score ≥ threshold ────────→ VALIDATED
                             ├──────────┤ score < threshold, retries → DRAFTED
                             │          └─ exhausted → COVERAGE_BLOCKED
                             │                   └─ HUMAN_OVERRIDE → VALIDATED
                             │                      (reason/actor/score recorded)
VALIDATED → AWAITING_APPROVAL ├─ REVISE → DRAFTED
                              ├─ REJECT → REJECTED
                              └─ APPROVE → PUBLISHED
```

Every transition is a row in `transitions` (audit trail). Overrides are explicit recorded
transitions, never hidden booleans.

## Design patterns in use

- **Adapter** — `src/claude-runner.ts` is the only code touching the raw `claude -p` JSON envelope (`ClaudeRunResult {status, output, usage, duration, model, error}`); portal CLIs adapt each board to one JSON contract.
- **Guard** — `tools/publish_guard.py` (side effects), `tools/template_guard.py` (LaTeX diffs), `scripts/hooks/pre-push` (git).
- **Repository / single system of record** — Postgres owns pipeline state; no tracker CSV (export-only view via `job-hunt export-csv`).
- **Overlay / vendored upstream** — ai-job-search is a pinned git submodule; `scripts/sync-framework.ts` copies it in and re-applies `overlays/` patches, so upstream updates are a submodule bump.
- **Graceful degradation** — gbrain optional (warn + continue); MinIO falls back to local `resume/`; DB down = fail fast, no shadow state.
- **Fixed-template drafting** — the LLM edits content inside fixed macros of the master `resume-example/resume.latext`; layout can't drift, tokens stay low.

## Storage (one job each)

| Data | Store |
|------|-------|
| Pipeline state, transitions, rankings, runs, review packets | Postgres (Docker, port 5433) |
| Resume PDFs (`{company}/{role}_{username}.pdf`) | MinIO bucket `resumes` (local `resume/` until MinIO milestone) |
| Candidate facts | profile markdown files |
| History/context | gbrain (PGLite, MCP) |

## Tooling

`uv` (Python), `bun` (TypeScript), `Makefile` (single entry point), `docker-compose.yml`
(Postgres + Adminer + MinIO), `Dockerfile` (backend image; LaTeX via local pdflatex or
the texlive Docker image fallback).
