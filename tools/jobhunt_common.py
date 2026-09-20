"""Shared helpers for job-hunt guard tools (not part of the synced framework).

Keep canonical() in EXACT parity with src/schema.ts — both derive
application_id = sha256("company|title|location")[:16].
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:  # dotenv is in pyproject; direct python calls may lack it
    pass


def canonical(s: str | None) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


def application_id(company: str, title: str, location: str | None) -> str:
    key = f"{canonical(company)}|{canonical(title)}|{canonical(location)}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def get_conn():
    import psycopg

    url = os.environ.get("DATABASE_URL", "postgres://jobhunt:jobhunt@localhost:5436/jobhunt")
    try:
        return psycopg.connect(url, connect_timeout=5)
    except Exception as e:  # noqa: BLE001
        print(f"db: cannot reach Postgres ({e}) — run `make db-up` first", file=sys.stderr)
        sys.exit(1)


# State machine — EXACT parity with src/state.ts
STATES = [
    "DISCOVERED", "EVALUATED", "DRAFTED", "COVERAGE_CHECK", "COVERAGE_BLOCKED",
    "VALIDATED", "AWAITING_APPROVAL", "REJECTED", "PUBLISHED",
]
ALLOWED = {
    "DISCOVERED": ["EVALUATED", "REJECTED"],
    "EVALUATED": ["DRAFTED", "REJECTED"],
    "DRAFTED": ["COVERAGE_CHECK"],
    "COVERAGE_CHECK": ["VALIDATED", "DRAFTED", "COVERAGE_BLOCKED"],
    "COVERAGE_BLOCKED": ["VALIDATED", "REJECTED"],  # VALIDATED only via HUMAN_OVERRIDE
    "VALIDATED": ["AWAITING_APPROVAL"],
    "AWAITING_APPROVAL": ["DRAFTED", "REJECTED", "PUBLISHED"],
    "REJECTED": [],
    "PUBLISHED": [],
}


class IllegalTransition(Exception):
    pass


def transition(conn, app_id: str, to: str, reason: str | None = None, actor: str = "system"):
    """Validated state change + audit row, in one transaction (parity with state.ts)."""
    with conn.transaction():
        row = conn.execute(
            "SELECT state FROM applications WHERE application_id = %s FOR UPDATE", (app_id,)
        ).fetchone()
        if row is None:
            raise IllegalTransition(f"unknown application {app_id}")
        frm = row[0]
        if to not in ALLOWED.get(frm, []):
            raise IllegalTransition(f"illegal transition {frm} → {to} for {app_id}")
        if frm == "COVERAGE_BLOCKED" and to == "VALIDATED":
            if not reason or actor == "system":
                raise IllegalTransition(
                    "COVERAGE_BLOCKED → VALIDATED requires HUMAN_OVERRIDE: "
                    "explicit reason and a human actor"
                )
            conn.execute(
                """UPDATE applications SET override_reason=%s, override_actor=%s,
                   override_at=now(), original_coverage_score=keyword_coverage_score
                   WHERE application_id=%s""",
                (reason, actor, app_id),
            )
        conn.execute(
            "UPDATE applications SET state=%s, updated_at=now() WHERE application_id=%s",
            (to, app_id),
        )
        conn.execute(
            """INSERT INTO transitions (application_id, from_state, to_state, reason, actor)
               VALUES (%s, %s, %s, %s, %s)""",
            (app_id, frm, to, reason, actor),
        )
