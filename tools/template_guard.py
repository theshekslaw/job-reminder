#!/usr/bin/env python3
"""template_guard.py — validate a drafted resume .tex against the master template
(invariant #5, DESIGN.md): the LLM edits CONTENT inside the fixed macros only.

Checks:
  1. The preamble (everything before \\begin{document}) is IDENTICAL to the
     template's, modulo trailing whitespace.
  2. The body adds no structural commands: \\usepackage, \\newcommand,
     \\renewcommand, \\geometry, \\documentclass, \\titleformat, \\setlist.
  3. Exactly one \\begin{document} / \\end{document} pair.

Exit codes: 0 ok · 2 violation · 1 error
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATES = [
    REPO_ROOT / "templates/cv/abhishek-default/template.tex",
    REPO_ROOT / "resume-example/resume.latext",
]

FORBIDDEN_IN_BODY = re.compile(
    r"^[^%\n]*\\(usepackage|newcommand|renewcommand|geometry|documentclass|titleformat|setlist)\b",
    re.MULTILINE,
)


def split_doc(text: str, name: str) -> tuple[str, str]:
    marker = "\\begin{document}"
    if text.count(marker) != 1 or text.count("\\end{document}") != 1:
        print(f"template_guard: VIOLATION — {name} must contain exactly one "
              "\\begin{document} / \\end{document} pair", file=sys.stderr)
        sys.exit(2)
    pre, body = text.split(marker, 1)
    return pre, body


def norm_lines(s: str) -> list[str]:
    return [ln.rstrip() for ln in s.splitlines() if ln.strip() != ""]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True, help="drafted .tex to validate")
    ap.add_argument("--template", help="master template (default: registered template)")
    args = ap.parse_args()

    draft_path = Path(args.draft)
    if not draft_path.exists():
        print(f"template_guard: draft not found: {draft_path}", file=sys.stderr)
        sys.exit(1)

    template_path = Path(args.template) if args.template else next(
        (p for p in DEFAULT_TEMPLATES if p.exists()), None)
    if template_path is None or not template_path.exists():
        print("template_guard: no template found (looked for "
              + ", ".join(str(p) for p in DEFAULT_TEMPLATES) + ")", file=sys.stderr)
        sys.exit(1)

    draft_pre, draft_body = split_doc(draft_path.read_text(), str(draft_path))
    tmpl_pre, _ = split_doc(template_path.read_text(), str(template_path))

    if norm_lines(draft_pre) != norm_lines(tmpl_pre):
        diff = list(difflib.unified_diff(
            norm_lines(tmpl_pre), norm_lines(draft_pre),
            fromfile="template preamble", tofile="draft preamble", lineterm="", n=1,
        ))[:30]
        print("template_guard: VIOLATION — preamble/macros were modified. "
              "The drafter may only edit content inside the fixed macros.", file=sys.stderr)
        print("\n".join(diff), file=sys.stderr)
        sys.exit(2)

    hits = FORBIDDEN_IN_BODY.findall(draft_body)
    if hits:
        print("template_guard: VIOLATION — body adds structural commands: "
              + ", ".join(sorted(set("\\" + h for h in hits))), file=sys.stderr)
        sys.exit(2)

    print(f"template_guard: OK ({draft_path.name} conforms to {template_path.name})")


if __name__ == "__main__":
    main()
