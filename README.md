# job-hunt

**Personal, private job-search pipeline.** Scrapes job boards, evaluates fit, tailors a
1-page LaTeX resume + cover letter per JD with Claude Code, and stops at a **mandatory
human review gate** before anything is recorded or published.

> **Prepare-only.** This system never submits applications, never logs into job boards,
> and never automates a browser. The user reviews every resume and submits by hand.

Seeded conceptually from [MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search)
(vendored as a git submodule under `packages/`); [gbrain](https://github.com/garrytan/gbrain)
provides optional cross-session memory over MCP.

## Docs

- [.claude/architecture/PLAN.md](.claude/architecture/PLAN.md) — phased implementation plan
- [.claude/architecture/DESIGN.md](.claude/architecture/DESIGN.md) — boundaries, invariants, state machine, patterns

## Quick start

```bash
cp .env.example .env       # fill in your details
make setup                 # uv sync + bun install + hooks + submodule + framework sync
make db-up                 # Postgres + Adminer + MinIO (Docker)
# put your CV in documents/cv/, LinkedIn export in documents/linkedin/
# then in Claude Code: /setup
```

## Privacy

Your personal data (documents, generated CVs, resumes, application history) is fully used
**locally** — files, Postgres, MinIO — and is **never committed to git**. Enforced by
`.gitignore`, `tools/security_guards.py`, and a pre-push hook that rejects pushes to any
remote except the private origin.
