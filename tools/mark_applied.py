#!/usr/bin/env python3
"""mark_applied.py — record that the USER submitted an application, then remove the
resume files that are no longer needed.

Requires state PUBLISHED (human-approved resume). Records the `applied` outcome
(same as `tracker_db.py record-outcome <id> applied`), then deletes:
  * the MinIO object (or local resume/ fallback) at applications.resume_path
  * the review-packet PDF (cv/main_<company>_<role>.pdf)
  * the archived documents/applications/<company>_<title>/final_resume.pdf
Kept: resume.tex source, coverage.json, job_posting.md, form answers — small, and enough
to rebuild the exact PDF for interview prep. --keep-files skips deletion.

    uv run tools/mark_applied.py --application-id ID --notes "LinkedIn, 2026-10-06" [--keep-files]

Exit codes: 0 ok · 2 refused (wrong state / already applied) · 1 error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jobhunt_common import REPO_ROOT, canonical, get_conn  # noqa: E402


def split_s3(path: str) -> tuple[str, str] | None:
    """'s3://bucket/a/b.pdf' → ('bucket', 'a/b.pdf'); anything else → None."""
    if not path or not path.startswith("s3://"):
        return None
    bucket, _, key = path[5:].partition("/")
    return (bucket, key) if bucket and key else None


def delete_local(paths: list[Path]) -> list[str]:
    removed = []
    for p in paths:
        try:
            p = p.resolve()
            p.relative_to(REPO_ROOT)  # never delete outside the repo
        except (ValueError, OSError):
            continue
        if p.is_file() and p.suffix == ".pdf":
            p.unlink()
            removed.append(str(p.relative_to(REPO_ROOT)))
    return removed


def delete_minio(bucket: str, key: str) -> str | None:
    """Returns a warning string on failure, None on success."""
    endpoint = os.environ.get("MINIO_ENDPOINT", "http://localhost:9002")
    try:
        from minio import Minio
        client = Minio(endpoint.replace("http://", "").replace("https://", ""),
                       access_key=os.environ.get("MINIO_ACCESS_KEY", "jobhunt"),
                       secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
                       secure=endpoint.startswith("https"))
        client.remove_object(bucket, key)
        return None
    except Exception as e:  # noqa: BLE001
        return f"MinIO delete failed for s3://{bucket}/{key}: {e}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--application-id", required=True)
    ap.add_argument("--notes", default="")
    ap.add_argument("--keep-files", action="store_true")
    args = ap.parse_args()
    app_id = args.application_id

    conn = get_conn()
    row = conn.execute(
        """SELECT a.state, a.outcome, a.resume_path, p.company, p.title,
                  (SELECT pdf_path FROM review_packets WHERE application_id = a.application_id
                   ORDER BY created_at DESC LIMIT 1)
           FROM applications a JOIN job_postings p USING (application_id)
           WHERE a.application_id = %s""", (app_id,)).fetchone()
    if row is None:
        print(f"mark_applied: unknown application {app_id}", file=sys.stderr)
        sys.exit(1)
    state, outcome, resume_path, company, title, pdf_path = row
    if state != "PUBLISHED":
        print(f"mark_applied: REFUSED — state is {state}; approve the resume first "
              f"(make run ARGS=\"approve {app_id}\")", file=sys.stderr)
        sys.exit(2)

    already = conn.execute(
        "SELECT 1 FROM outcome_events WHERE application_id=%s AND outcome='applied'",
        (app_id,)).fetchone()
    if not already:
        conn.execute("""INSERT INTO outcome_events (application_id, outcome, notes, source)
                        VALUES (%s, 'applied', %s, 'user')""", (app_id, args.notes))
        conn.execute("UPDATE applications SET outcome='applied', outcome_updated_at=now() "
                     "WHERE application_id=%s", (app_id,))
        conn.commit()

    removed, warnings = [], []
    if not args.keep_files:
        folder = REPO_ROOT / "documents" / "applications" / f"{canonical(company)}_{canonical(title)}"
        local = [folder / "final_resume.pdf"]
        if pdf_path:
            local.append(REPO_ROOT / pdf_path if not Path(pdf_path).is_absolute() else Path(pdf_path))
        s3 = split_s3(resume_path or "")
        if s3:
            w = delete_minio(*s3)
            if w:
                warnings.append(w)
            else:
                removed.append(resume_path)
        elif resume_path:
            local.append(REPO_ROOT / resume_path)
        removed += delete_local(local)
        if removed and not any(w.startswith("MinIO") for w in warnings):
            conn.execute("UPDATE applications SET resume_path=%s WHERE application_id=%s",
                         (f"deleted after apply (was {resume_path})" if resume_path else None,
                          app_id))
            conn.commit()

    print(json.dumps({"ok": True, "application_id": app_id, "outcome": "applied",
                      "already_recorded": bool(already), "removed": removed,
                      "warnings": warnings,
                      "kept": "resume.tex + coverage.json + job_posting.md (rebuild PDF for interviews)"},
                     indent=2))


if __name__ == "__main__":
    main()
