# Search Queries for Job Scraper

<!-- Customized for Abhishek Pandey — AI/ML + backend engineering, India + Remote.
     Regenerate the interest/location terms from .env (JOB_INTERESTS / JOB_LOCATIONS)
     when they change. -->

## Installed portal CLIs (primary for `/scrape`)

`/scrape` discovers every portal skill under `.agents/skills/*/SKILL.md` and runs its CLI first. Active CLIs in this workspace: **`linkedin-search`** (use `-l "Bengaluru, Karnataka, India"`, `-l "Gurugram, Haryana, India"`, and no-location for remote) and **`freehire-search`** (tech roles, ~50 ATS systems, no location filter — filter results by location after). Danish demo portals are disabled. The backend equivalent is `make scrape`, which runs the same CLIs zero-LLM.

The `site:` query templates below are the **WebSearch fallback** — for boards whose portal CLIs were evaluated and DECLINED (see decision log), for company career pages, or when a CLI fails.

## Portal decision log (2026-09-20)

| Board | Decision | Reason | Coverage path |
|---|---|---|---|
| linkedin.com | ✅ CLI (`linkedin-search`) | public jobs-guest endpoints | primary |
| freehire.me | ✅ CLI (`freehire-search`) | public JSON API | primary |
| naukri.com | ❌ CLI declined | robots.txt explicitly blocks AI agents (Claude-User, claudebot, GPTBot…); job API is recaptcha-gated (HTTP 406) | WebSearch `site:` queries below |
| wellfound.com | ❌ CLI declined | Cloudflare Turnstile bot-challenge on listing pages | WebSearch `site:` queries below |
| indeed.com | ❌ CLI declined | Cloudflare-hostile, known scraper blocker | WebSearch `site:` queries below |

Do not build scrapers for the declined boards — the decline is the portal contract working as intended. Re-evaluate only if a board ships an official API.

**Language scope:** all queries in **English** only (the working language for these roles and markets).

## Search Sites

Primary (CLI-covered):
- **linkedin.com/jobs** — via `linkedin-search` CLI with `-l` per location
- **freehire.me** — via `freehire-search` CLI (tech/ATS aggregate)

Secondary (WebSearch fallback):
- **naukri.com** — India's largest board (site: queries)
- **wellfound.com** — startup/AI roles (site: queries)
- **indeed.com** — general (site: queries)
- Company career pages via `site:` searches for target companies

## Query Categories

Combine each query with a location term (Bengaluru, Gurugram, India, Remote) where shown.

### Priority 1: AI / Machine Learning Engineering

Strongest direction: production ML systems, LLM fine-tuning, RAG, agentic AI, model serving.

```
site:naukri.com "machine learning engineer" bengaluru
site:naukri.com "AI engineer" gurugram OR bengaluru
site:naukri.com "LLM" engineer india
site:wellfound.com "machine learning engineer" india OR remote
site:wellfound.com "AI engineer" remote
site:indeed.com "machine learning engineer" bengaluru
site:linkedin.com/jobs "GenAI engineer" india
```

Title variants: Machine Learning Engineer, AI Engineer, GenAI Engineer, LLM Engineer, MLOps Engineer, Applied Scientist.

### Priority 2: Backend / Software Engineering (AI-adjacent)

```
site:naukri.com "software engineer" python bengaluru
site:naukri.com "backend engineer" fastapi OR python india
site:wellfound.com "backend engineer" python remote
site:indeed.com "software engineer" python gurugram
```

Title variants: Software Engineer, Backend Engineer, Python Developer, Platform Engineer.

### Priority 3: Specialized / niche

```
site:naukri.com "MLOps" OR "model serving" india
site:wellfound.com "RAG" OR "LLM" startup remote
site:linkedin.com/jobs "inference" engineer vLLM OR kserve
```

## Location terms

- Bengaluru, Karnataka, India
- Gurugram, Haryana, India
- Remote (India-eligible / global)
