#!/usr/bin/env python3
"""publish_guard.py — the ONLY entry point that writes publish side effects
(invariant #2, DESIGN.md). Refuses unless the application is AWAITING_APPROVAL
and a human actor approves.

Side effects, in order:
  1. upload the reviewed PDF to MinIO at {company}/{title}_{RESUME_USERNAME}.pdf
     (falls back to local resume/ if MinIO is down — storage never blocks approval)
  2. archive a copy under documents/applications/<company>_<title>/
  3. optional gbrain event (warn-and-continue if gbrain is absent)
  4. transition AWAITING_APPROVAL → PUBLISHED with the audit row

Exit codes: 0 published · 2 refused (wrong state / no packet) · 1 error
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jobhunt_common import REPO_ROOT, canonical, get_conn, transition  # noqa: E402


def refuse(msg: str) -> None:
    print(f"publish_guard: REFUSED — {msg}", file=sys.stderr)
    sys.exit(2)


def upload_resume(pdf: Path, key: str) -> tuple[str, list[str]]:
    """Returns (resume_path, warnings). MinIO first, local resume/ fallback."""
    warnings: list[str] = []
    endpoint = os.environ.get("MINIO_ENDPOINT", "http://localhost:9002")
    bucket = os.environ.get("MINIO_BUCKET", "resumes")
    try:
        from minio import Minio

        client = Minio(
            endpoint.replace("http://", "").replace("https://", ""),
            access_key=os.environ.get("MINIO_ACCESS_KEY", "jobhunt"),
            secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
            secure=endpoint.startswith("https"),
        )
        client.fput_object(bucket, key, str(pdf), content_type="application/pdf")
        return f"s3://{bucket}/{key}", warnings
    except Exception as e:  # noqa: BLE001
        warnings.append(f"MinIO upload failed ({e}); falling back to local resume/")
        dest = REPO_ROOT / "resume" / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf, dest)
        return str(dest.relative_to(REPO_ROOT)), warnings


def gbrain_event(company: str, title: str, app_id: str) -> str | None:
    """Optional memory write — non-critical by contract (DESIGN.md)."""
    if shutil.which("gbrain") is None:
        return "gbrain not installed — skipped memory event"
    fact = f"Prepared and approved a tailored resume for {title} at {company} on {date.today().isoformat()}"
    cmd = [
        "gbrain", "remember", fact,
        "--entity", f"companies/{canonical(company)}",
        "--kind", "event",
        "--provenance", f"job-hunt publish_guard, application {app_id}",
        "--json",
    ]
    src = os.environ.get("GBRAIN_SOURCE")
    if src:
        cmd += ["--source", src]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30, check=True)
        return None
    except Exception as e:  # noqa: BLE001
        return f"gbrain event failed ({e}) — continuing (memory is non-critical)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--application-id", required=True)
    ap.add_argument("--approved-by", default=os.environ.get("USER") or "human")
    args = ap.parse_args()
    app_id = args.application_id

    conn = get_conn()
    row = conn.execute(
        """SELECT a.state, p.company, p.title,
                  (SELECT pdf_path FROM review_packets
                   WHERE application_id = a.application_id
                   ORDER BY created_at DESC LIMIT 1)
           FROM applications a JOIN job_postings p USING (application_id)
           WHERE a.application_id = %s""",
        (app_id,),
    ).fetchone()

    if row is None:
        refuse(f"unknown application {app_id}")
    state, company, title, pdf_path = row

    if state != "AWAITING_APPROVAL":
        refuse(f"state is {state}, publication requires AWAITING_APPROVAL "
               "(the review gate must run first)")
    if not pdf_path:
        refuse("no review packet with a PDF exists — the pipeline must persist one before approval")
    pdf = (REPO_ROOT / pdf_path) if not Path(pdf_path).is_absolute() else Path(pdf_path)
    if not pdf.exists():
        refuse(f"review-packet PDF missing on disk: {pdf}")

    username = os.environ.get("RESUME_USERNAME", "user")
    key = f"{canonical(company)}/{canonical(title)}_{username}.pdf"

    resume_path, warnings = upload_resume(pdf, key)

    archive = REPO_ROOT / "documents" / "applications" / f"{canonical(company)}_{canonical(title)}"
    archive.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf, archive / "final_resume.pdf")

    warn = gbrain_event(company, title, app_id)
    if warn:
        warnings.append(warn)

    conn.execute(
        "UPDATE applications SET resume_path=%s WHERE application_id=%s",
        (resume_path, app_id),
    )
    transition(conn, app_id, "PUBLISHED", reason="approved by human review",
               actor=args.approved_by)
    conn.commit()

    print(f"published: {company} — {title}")
    print(f"  resume:  {resume_path}")
    print(f"  archive: {archive.relative_to(REPO_ROOT)}/final_resume.pdf")
    for w in warnings:
        print(f"  ⚠ {w}")


if __name__ == "__main__":
    main()
