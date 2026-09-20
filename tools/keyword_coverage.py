#!/usr/bin/env python3
"""keyword_coverage.py — score how much of the JD's extracted requirement
vocabulary the resume covers (honest name: this is NOT a full ATS score).

    score = 100 × (covered + 0.5·synonym_only)
                 / (covered + synonym_only + missing_have + missing_gap)

Input: a coverage JSON file written by the /apply pipeline's keyword audit:
    {"covered": [...], "synonym_only": [...], "missing_have": [...], "missing_gap": [...]}
or explicit counts via flags.

Records the score on the application row when --application-id is given.
Exit codes: 0 pass · 3 below threshold · 1 error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jobhunt_common import get_conn  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="coverage JSON file from the keyword audit")
    ap.add_argument("--covered", type=int)
    ap.add_argument("--synonym-only", type=int, default=0)
    ap.add_argument("--missing-have", type=int)
    ap.add_argument("--missing-gap", type=int)
    ap.add_argument("--application-id")
    ap.add_argument("--threshold", type=float,
                    default=float(os.environ.get("KEYWORD_COVERAGE_THRESHOLD", 96)))
    args = ap.parse_args()

    if args.file:
        data = json.loads(Path(args.file).read_text())
        covered = len(data.get("covered", []))
        synonym = len(data.get("synonym_only", []))
        missing_have = len(data.get("missing_have", []))
        missing_gap = len(data.get("missing_gap", []))
    elif args.covered is not None and args.missing_have is not None and args.missing_gap is not None:
        covered, synonym = args.covered, args.synonym_only
        missing_have, missing_gap = args.missing_have, args.missing_gap
    else:
        print("keyword_coverage: pass --file or all of --covered/--missing-have/--missing-gap",
              file=sys.stderr)
        sys.exit(1)

    denom = covered + synonym + missing_have + missing_gap
    score = 100.0 if denom == 0 else round(100 * (covered + 0.5 * synonym) / denom, 2)
    passed = score >= args.threshold

    if args.application_id:
        conn = get_conn()
        conn.execute(
            "UPDATE applications SET keyword_coverage_score=%s, updated_at=now() "
            "WHERE application_id=%s",
            (score, args.application_id),
        )
        conn.commit()

    print(json.dumps({
        "keyword_coverage_score": score,
        "threshold": args.threshold,
        "pass": passed,
        "counts": {"covered": covered, "synonym_only": synonym,
                   "missing_have": missing_have, "missing_gap": missing_gap},
        "note": None if passed else
            "below threshold — revise honestly (emphasize real experience) or, if the JD "
            "demands skills the candidate lacks, block instead of keyword-stuffing",
    }))
    sys.exit(0 if passed else 3)


if __name__ == "__main__":
    main()
