#!/usr/bin/env python3
"""tracker_db.py — the DB query/record interface for framework commands.
Replaces every job_search_tracker.csv read/write: the DB is the ONLY system
of record (DESIGN.md). All output is JSON so agent prompts can consume it.

    ensure --company C --title T [--location L] [--url U] [--source S] [--description-file F]
                                upsert a posting (for /apply on a pasted URL/text); prints application_id
    candidates --limit N        DISCOVERED apps not yet ranked (for /rank)
    list [--state S]            applications overview
    status <application_id>     one application: posting, state, transitions, outcomes
    record-rank <id> --score N --reason ... [--matched j] [--missing j] [--model m]
    set-fit <id> --fit N
    record-packet <id> --pdf P --tex T [--coverage-file f] [--decisions-file f] [--reviewer-file f]
    transition <id> <to> [--reason r] [--actor a]
    record-outcome <id> <outcome> [--notes n] [--source s]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jobhunt_common import (  # noqa: E402
    IllegalTransition, application_id, get_conn, transition,
)


def jprint(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def load_json_arg(path: str | None):
    if not path:
        return None
    return json.loads(Path(path).read_text())


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ensure")
    p.add_argument("--company", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--location")
    p.add_argument("--url")
    p.add_argument("--source", default="manual")
    p.add_argument("--description-file")

    p = sub.add_parser("candidates")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("list")
    p.add_argument("--state")

    p = sub.add_parser("status")
    p.add_argument("application_id")

    p = sub.add_parser("record-rank")
    p.add_argument("application_id")
    p.add_argument("--score", type=int, required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--matched", help="JSON array of matched requirements")
    p.add_argument("--missing", help="JSON array of missing requirements")
    p.add_argument("--model")

    p = sub.add_parser("set-fit")
    p.add_argument("application_id")
    p.add_argument("--fit", type=int, required=True)

    p = sub.add_parser("record-packet")
    p.add_argument("application_id")
    p.add_argument("--pdf", required=True)
    p.add_argument("--tex", required=True)
    p.add_argument("--coverage-file")
    p.add_argument("--decisions-file")
    p.add_argument("--reviewer-file")

    p = sub.add_parser("transition")
    p.add_argument("application_id")
    p.add_argument("to")
    p.add_argument("--reason")
    p.add_argument("--actor", default="system")

    p = sub.add_parser("record-outcome")
    p.add_argument("application_id")
    p.add_argument("outcome")
    p.add_argument("--notes")
    p.add_argument("--source", default="user")

    args = ap.parse_args()
    conn = get_conn()

    if args.cmd == "ensure":
        app_id = application_id(args.company, args.title, args.location)
        desc = Path(args.description_file).read_text()[:20000] if args.description_file else None
        conn.execute(
            """INSERT INTO job_postings (application_id, company, title, location, description)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (application_id) DO UPDATE
                 SET description = COALESCE(EXCLUDED.description, job_postings.description)""",
            (app_id, args.company, args.title, args.location, desc),
        )
        created = conn.execute(
            """INSERT INTO applications (application_id) VALUES (%s)
               ON CONFLICT (application_id) DO NOTHING RETURNING application_id""",
            (app_id,),
        ).fetchone()
        if created:
            conn.execute(
                """INSERT INTO transitions (application_id, to_state, reason, actor)
                   VALUES (%s, 'DISCOVERED', %s, 'system')""",
                (app_id, f"registered via {args.source}"),
            )
        conn.execute(
            """INSERT INTO job_sources (application_id, source, source_id, url)
               VALUES (%s, %s, %s, %s) ON CONFLICT (source, source_id) DO NOTHING""",
            (app_id, args.source, args.url or f"{args.company}:{args.title}", args.url),
        )
        conn.commit()
        state = conn.execute(
            "SELECT state FROM applications WHERE application_id=%s", (app_id,)
        ).fetchone()[0]
        jprint({"application_id": app_id, "state": state, "created": bool(created)})

    elif args.cmd == "candidates":
        rows = conn.execute(
            """SELECT a.application_id, p.company, p.title, p.location, p.description,
                      s.url
               FROM applications a
               JOIN job_postings p USING (application_id)
               LEFT JOIN LATERAL (SELECT url FROM job_sources
                                  WHERE application_id = a.application_id
                                  ORDER BY first_seen_at LIMIT 1) s ON true
               WHERE a.state = 'DISCOVERED'
                 AND NOT EXISTS (SELECT 1 FROM rankings r
                                 WHERE r.application_id = a.application_id)
               ORDER BY p.scraped_at DESC LIMIT %s""",
            (args.limit,),
        ).fetchall()
        jprint([{"application_id": r[0], "company": r[1], "title": r[2],
                 "location": r[3], "description": (r[4] or "")[:2000], "url": r[5]}
                for r in rows])

    elif args.cmd == "list":
        q = """SELECT a.application_id, p.company, p.title, a.state, a.fit_rating,
                      a.keyword_coverage_score, a.outcome, a.updated_at
               FROM applications a JOIN job_postings p USING (application_id)"""
        params: tuple = ()
        if args.state:
            q += " WHERE a.state = %s"
            params = (args.state,)
        q += " ORDER BY a.updated_at DESC LIMIT 100"
        rows = conn.execute(q, params).fetchall()
        jprint([{"application_id": r[0], "company": r[1], "title": r[2], "state": r[3],
                 "fit": r[4], "coverage": float(r[5]) if r[5] is not None else None,
                 "outcome": r[6], "updated": r[7]} for r in rows])

    elif args.cmd == "status":
        app = conn.execute(
            """SELECT a.state, a.fit_rating, a.keyword_coverage_score, a.resume_path,
                      a.outcome, p.company, p.title, p.location
               FROM applications a JOIN job_postings p USING (application_id)
               WHERE a.application_id = %s""",
            (args.application_id,),
        ).fetchone()
        if app is None:
            print(f"unknown application {args.application_id}", file=sys.stderr)
            sys.exit(1)
        trans = conn.execute(
            "SELECT from_state, to_state, reason, actor, at FROM transitions "
            "WHERE application_id = %s ORDER BY at",
            (args.application_id,),
        ).fetchall()
        outcomes = conn.execute(
            "SELECT outcome, notes, source, at FROM outcome_events "
            "WHERE application_id = %s ORDER BY at",
            (args.application_id,),
        ).fetchall()
        jprint({
            "application_id": args.application_id,
            "company": app[5], "title": app[6], "location": app[7],
            "state": app[0], "fit": app[1],
            "coverage": float(app[2]) if app[2] is not None else None,
            "resume_path": app[3], "outcome": app[4],
            "transitions": [{"from": t[0], "to": t[1], "reason": t[2],
                             "actor": t[3], "at": t[4]} for t in trans],
            "outcome_events": [{"outcome": o[0], "notes": o[1], "source": o[2],
                                "at": o[3]} for o in outcomes],
        })

    elif args.cmd == "record-rank":
        conn.execute(
            """INSERT INTO rankings (application_id, rank_score, matched_requirements,
                                     missing_requirements, reason, model)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (args.application_id, args.score,
             json.dumps(json.loads(args.matched)) if args.matched else None,
             json.dumps(json.loads(args.missing)) if args.missing else None,
             args.reason, args.model),
        )
        conn.commit()
        jprint({"ok": True, "application_id": args.application_id, "rank_score": args.score})

    elif args.cmd == "set-fit":
        conn.execute(
            "UPDATE applications SET fit_rating=%s, updated_at=now() WHERE application_id=%s",
            (args.fit, args.application_id),
        )
        conn.commit()
        jprint({"ok": True, "application_id": args.application_id, "fit": args.fit})

    elif args.cmd == "record-packet":
        conn.execute(
            """INSERT INTO review_packets (application_id, draft_tex_path, pdf_path,
                                           coverage_report, tailoring_decisions, reviewer_output)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (args.application_id, args.tex, args.pdf,
             json.dumps(load_json_arg(args.coverage_file)) if args.coverage_file else None,
             json.dumps(load_json_arg(args.decisions_file)) if args.decisions_file else None,
             json.dumps(load_json_arg(args.reviewer_file)) if args.reviewer_file else None),
        )
        conn.commit()
        jprint({"ok": True, "application_id": args.application_id, "pdf": args.pdf})

    elif args.cmd == "transition":
        try:
            transition(conn, args.application_id, args.to,
                       reason=args.reason, actor=args.actor)
            conn.commit()
            jprint({"ok": True, "application_id": args.application_id, "state": args.to})
        except IllegalTransition as e:
            print(f"tracker_db: {e}", file=sys.stderr)
            sys.exit(2)

    elif args.cmd == "record-outcome":
        row = conn.execute(
            "SELECT state FROM applications WHERE application_id=%s",
            (args.application_id,),
        ).fetchone()
        if row is None:
            print(f"unknown application {args.application_id}", file=sys.stderr)
            sys.exit(1)
        if row[0] != "PUBLISHED":
            print(f"tracker_db: outcomes only apply to PUBLISHED applications "
                  f"(state is {row[0]})", file=sys.stderr)
            sys.exit(2)
        conn.execute(
            """INSERT INTO outcome_events (application_id, outcome, notes, source)
               VALUES (%s, %s, %s, %s)""",
            (args.application_id, args.outcome, args.notes, args.source),
        )
        conn.execute(
            "UPDATE applications SET outcome=%s, outcome_updated_at=now() "
            "WHERE application_id=%s",
            (args.outcome, args.application_id),
        )
        conn.commit()
        jprint({"ok": True, "application_id": args.application_id, "outcome": args.outcome})


if __name__ == "__main__":
    main()
