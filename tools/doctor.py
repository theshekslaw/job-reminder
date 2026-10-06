#!/usr/bin/env python3
"""doctor.py — is this workspace set up correctly? (`make doctor`)

Checks .env against .env.example (prints set/unset, NEVER values), local tools, the DB
and its migrations, MinIO, the candidate profile, and the integrations that cannot be
checked from a shell (Gmail connector, cmux) — each with a one-line fix.

Exit codes: 0 no failures (warnings allowed) · 1 at least one FAIL
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".claude/skills/job-application-assistant/01-candidate-profile.md"

REQUIRED = ["JOB_INTERESTS", "JOB_LOCATIONS", "RESUME_USERNAME", "KEYWORD_COVERAGE_THRESHOLD",
            "DATABASE_URL", "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY",
            "MINIO_BUCKET"]
RECOMMENDED = ["USER_NAME", "USER_EMAIL", "GITHUB_URL", "LINKEDIN_URL", "GMAIL_ADDRESS"]
# Identity links that must agree with the profile (the only source drafts read).
PROFILE_LINKS = ["USER_EMAIL", "GITHUB_URL", "LINKEDIN_URL", "PORTFOLIO_URL", "LEETCODE_URL",
                 "NAUKRI_PROFILE_URL"]
SECRET_LIKE = re.compile(r"(PASSWORD|PASSWD|COOKIE|SESSION|OTP)", re.I)

OK, WARN, FAIL, INFO = "OK", "WARN", "FAIL", "INFO"


# ── pure helpers (unit-tested) ──────────────────────────────────────────────

def parse_env(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def env_checks(example: dict, env: dict) -> list[tuple[str, str, str]]:
    rows = []
    for k in REQUIRED:
        rows.append((OK, f"env {k}", "set") if env.get(k) else
                    (FAIL, f"env {k}", "unset — copy it from .env.example"))
    for k in RECOMMENDED:
        rows.append((OK, f"env {k}", "set") if env.get(k) else
                    (WARN, f"env {k}", "unset — recommended"))
    missing = sorted(set(example) - set(env))
    if missing:
        rows.append((WARN, "env keys", f"in .env.example but not .env: {', '.join(missing)}"))
    secrets = sorted(k for k in env if SECRET_LIKE.search(k))
    if secrets:
        rows.append((FAIL, "env secrets", f"remove {', '.join(secrets)} — board/Gmail credentials "
                     "never go in .env (you log in yourself; Gmail uses the connector)"))
    if env.get("MINIO_SECRET_KEY") == "change-me-local-only":
        rows.append((WARN, "env MINIO_SECRET_KEY", "still the example value — fine for local-only"))
    return rows


def profile_checks(text: str | None, env: dict) -> list[tuple[str, str, str]]:
    if text is None:
        return [(FAIL, "profile", "01-candidate-profile.md missing — run /setup")]
    rows = []
    if "[YOUR_" in text:
        rows.append((FAIL, "profile", "still has [YOUR_…] placeholders — run /setup"))
    else:
        rows.append((OK, "profile", "filled in"))
    pending = text.count("[PENDING")
    if pending:
        rows.append((WARN, "profile", f"{pending} [PENDING] item(s) — confirm them when you can"))
    low = text.lower()
    for k in PROFILE_LINKS:
        v = re.sub(r"^(https?://)?(www\.)?", "", env.get(k, "").strip().rstrip("/").lower())
        if v and v not in low:
            rows.append((WARN, f"profile vs env {k}",
                         "differs from the profile — the profile wins; update one of them"))
    return rows


# ── live checks ─────────────────────────────────────────────────────────────

def tool_checks() -> list[tuple[str, str, str]]:
    rows = []
    for t, fix in (("uv", "brew install uv"), ("bun", "curl -fsSL https://bun.sh/install | bash"),
                   ("docker", "install Docker Desktop")):
        rows.append((OK, f"tool {t}", "found") if shutil.which(t) else (FAIL, f"tool {t}", fix))
    if shutil.which("pdflatex"):
        rows.append((OK, "tool pdflatex", "found"))
    else:
        img = subprocess.run(["docker", "images", "-q", "texlive/texlive"], capture_output=True,
                             text=True) if shutil.which("docker") else None
        rows.append((OK, "tool pdflatex", "via texlive docker image") if img and img.stdout.strip()
                    else (WARN, "tool pdflatex", "none — `docker pull texlive/texlive:latest`"))
    cmux = os.environ.get("CMUX_BIN") or shutil.which("cmux") or \
        "/Applications/cmux.app/Contents/Resources/bin/cmux"
    if Path(cmux).exists():
        rows.append((INFO, "cmux", "installed — assisted apply needs Claude Code started from a "
                     "cmux terminal"))
    else:
        rows.append((WARN, "cmux", "not installed — /assist-apply unavailable (links still work)"))
    return rows


def db_checks() -> list[tuple[str, str, str]]:
    try:
        import psycopg
        url = os.environ.get("DATABASE_URL", "postgres://jobhunt:jobhunt@localhost:5436/jobhunt")
        with psycopg.connect(url, connect_timeout=3) as c:
            applied = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
    except Exception as e:  # noqa: BLE001
        return [(FAIL, "database", f"unreachable ({type(e).__name__}) — `make db-up`")]
    files = sorted(p.stem for p in (ROOT / "db/migrations").glob("*.sql"))
    pending = [f for f in files if f not in applied]
    return [(OK, "database", "reachable"),
            (OK, "migrations", f"{len(files)} applied") if not pending else
            (FAIL, "migrations", f"pending {pending} — `make migrate`")]


def minio_checks() -> list[tuple[str, str, str]]:
    try:
        from minio import Minio
        ep = os.environ.get("MINIO_ENDPOINT", "http://localhost:9002")
        client = Minio(ep.replace("http://", "").replace("https://", ""),
                       access_key=os.environ.get("MINIO_ACCESS_KEY", ""),
                       secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
                       secure=ep.startswith("https"))
        bucket = os.environ.get("MINIO_BUCKET", "resumes")
        ok = client.bucket_exists(bucket)
        return [(OK, "minio", f"bucket {bucket} ready") if ok else
                (FAIL, "minio", f"bucket {bucket} missing — `make db-up` creates it")]
    except Exception as e:  # noqa: BLE001
        return [(WARN, "minio", f"unreachable ({type(e).__name__}) — `make db-up`; "
                 "publishing falls back to resume/")]


def main() -> int:
    example = parse_env((ROOT / ".env.example").read_text())
    env_path = ROOT / ".env"
    if not env_path.exists():
        print("FAIL  .env missing — `cp .env.example .env`, fill it in, re-run `make doctor`")
        return 1
    env = parse_env(env_path.read_text())
    os.environ.update({k: v for k, v in env.items() if k not in os.environ})

    rows = env_checks(example, env)
    rows += profile_checks(PROFILE.read_text() if PROFILE.exists() else None, env)
    rows += tool_checks() + db_checks() + minio_checks()
    rows.append((INFO, "gmail", "cannot be checked from a shell — connect it in claude.ai "
                 "Settings → Connectors → Gmail; /gmail-sync verifies it"))

    width = max(len(r[1]) for r in rows)
    for status, name, detail in rows:
        print(f"{status:<5} {name:<{width}}  {detail}")
    fails = sum(r[0] == FAIL for r in rows)
    warns = sum(r[0] == WARN for r in rows)
    print(f"\ndoctor: {fails} fail, {warns} warn")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
