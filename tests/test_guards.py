"""Pure-function tests for the guard tools (no DB required)."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from jobhunt_common import ALLOWED, STATES, application_id, canonical  # noqa: E402


def test_canonical_matches_naming_rule():
    assert canonical("Novo Nordisk A/S") == "novo_nordisk_as"
    assert canonical("  Machine   Learning Engineer ") == "machine_learning_engineer"
    assert canonical(None) == ""


def test_application_id_ignores_source():
    a = application_id("Google", "ML Engineer", "Bengaluru")
    b = application_id("google", "ml engineer", "Bengaluru ")
    assert a == b and len(a) == 16


def test_state_machine_shape():
    assert set(ALLOWED) == set(STATES)
    assert ALLOWED["PUBLISHED"] == [] and ALLOWED["REJECTED"] == []
    assert "PUBLISHED" in ALLOWED["AWAITING_APPROVAL"]
    assert "VALIDATED" in ALLOWED["COVERAGE_BLOCKED"]  # only via HUMAN_OVERRIDE (runtime check)


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, cwd=ROOT, **kw)


def test_keyword_coverage_math(tmp_path):
    f = tmp_path / "cov.json"
    f.write_text(json.dumps({
        "covered": ["a"] * 24, "synonym_only": [], "missing_have": ["x"], "missing_gap": [],
    }))
    r = run([sys.executable, "tools/keyword_coverage.py", "--file", str(f), "--threshold", "96"])
    out = json.loads(r.stdout)
    assert out["keyword_coverage_score"] == 96.0 and out["pass"] is True and r.returncode == 0

    f.write_text(json.dumps({
        "covered": ["a"] * 20, "synonym_only": ["s"] * 2, "missing_have": ["x"], "missing_gap": ["y"],
    }))
    r = run([sys.executable, "tools/keyword_coverage.py", "--file", str(f), "--threshold", "96"])
    out = json.loads(r.stdout)
    assert out["keyword_coverage_score"] == 87.5 and r.returncode == 3


TEMPLATE = ROOT / "resume-example/resume.latext"


def test_template_guard_accepts_content_edit(tmp_path):
    draft = tmp_path / "draft.tex"
    draft.write_text(TEMPLATE.read_text().replace(
        "Software Engineer with", "Machine Learning Engineer with"))
    r = run([sys.executable, "tools/template_guard.py", "--draft", str(draft),
             "--template", str(TEMPLATE)])
    assert r.returncode == 0, r.stderr


def test_template_guard_rejects_preamble_edit(tmp_path):
    draft = tmp_path / "draft.tex"
    draft.write_text(TEMPLATE.read_text().replace(
        "top=0.2in", "top=0.1in"))  # geometry change in the preamble
    r = run([sys.executable, "tools/template_guard.py", "--draft", str(draft),
             "--template", str(TEMPLATE)])
    assert r.returncode == 2


def test_template_guard_rejects_body_newcommand(tmp_path):
    draft = tmp_path / "draft.tex"
    draft.write_text(TEMPLATE.read_text().replace(
        "\\end{document}", "\\newcommand{\\sneaky}{x}\n\\end{document}"))
    r = run([sys.executable, "tools/template_guard.py", "--draft", str(draft),
             "--template", str(TEMPLATE)])
    assert r.returncode == 2
