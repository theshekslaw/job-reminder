"""ATS score v2 (keyword_coverage.py) and JD extraction (fetch_jd.py) — no DB, no network."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import keyword_coverage as kc  # noqa: E402
from fetch_jd import extract_description, linkedin_job_id  # noqa: E402

RESUME = """Abhishek Pandey
+91-9999999999 | someone@example.com
Professional Summary
Software Engineer building RAG pipelines with LangGraph and Qdrant vector search.
Technical Skills
Python, PyTorch, Docker, Kubernetes, Azure OpenAI
Experience
Acme Pvt Ltd Sep 2024 – Present
Built a from-
scratch PyTorch engine and LiteLLM-based gateway.
Education
B.Tech Jul 2021 – Jul 2025
"""


def req(term, status, priority="required", **kw):
    return {"term": term, "status": status, "priority": priority, **kw}


def v2(*reqs):
    return {"version": 2, "requirements": list(reqs)}


LEX = kc.Semantic("lexical")


def test_weighting_required_counts_triple():
    res = kc.score_v2(v2(req("Python", "covered"), req("Go", "missing_gap", "preferred")), None, LEX)
    assert res["keyword_coverage_score"] == 75.0  # 3/(3+1)
    assert res["required_coverage"] == 100.0 and res["preferred_coverage"] == 0.0


def test_alternatives_satisfy_one_item():
    res = kc.score_v2(v2(req("CrewAI", "covered", alternatives=["LangGraph", "Semantic Kernel"])),
                      RESUME, LEX)
    assert res["keyword_coverage_score"] == 100.0 and not res["downgrades"]


def test_covered_claim_absent_from_text_is_downgraded():
    res = kc.score_v2(v2(req("TensorFlow", "covered")), RESUME, LEX)
    assert res["final_status"]["missing_have"] == ["TensorFlow"]
    assert res["downgrades"][0]["from"] == "covered"


def test_missing_gap_is_never_upgraded():
    # "vector search" is literally in the resume, but a declared gap stays a gap.
    res = kc.score_v2(v2(req("vector search", "missing_gap")), RESUME, LEX)
    assert res["final_status"]["missing_gap"] == ["vector search"]
    assert res["keyword_coverage_score"] == 0.0


def test_lexical_mode_does_not_downgrade_synonyms():
    res = kc.score_v2(v2(req("semantic retrieval", "synonym_only")), RESUME, LEX)
    assert res["final_status"]["synonym_only"] == ["semantic retrieval"]


def test_eligibility_items_reported_not_scored():
    res = kc.score_v2(v2(req("Python", "covered"),
                         req("3+ years", "missing_gap", kind="eligibility")), RESUME, LEX)
    assert res["keyword_coverage_score"] == 100.0
    assert res["eligibility"][0]["term"] == "3+ years"


def test_hyphenation_artefacts_still_match():
    norm, sq = kc.normalize(RESUME), kc.squash(RESUME)
    assert kc.literal_present("from-scratch", norm, sq)
    assert kc.literal_present("LiteLLM-based", norm, sq)
    assert not kc.literal_present("Go", norm, sq)  # short term: word boundary only


def test_parseability_checks():
    p = kc.parseability(RESUME)
    assert p["checks"]["email_literal"]["points"] == 15
    assert p["checks"]["phone_literal"]["points"] == 15
    assert p["checks"]["section_headings"]["points"] == 20
    assert p["checks"]["hyphenation"]["points"] == 8  # one from-\nscratch break
    bad = kc.parseability("(cid:12)(cid:13) no contact here")
    assert bad["checks"]["clean_text"]["points"] == 0 and bad["score"] < 40


def test_cli_v2_gate_and_write(tmp_path):
    f, t = tmp_path / "cov.json", tmp_path / "resume.txt"
    f.write_text(json.dumps(v2(req("Python", "covered"), req("RAG", "covered"),
                               req("Go", "missing_gap", "preferred"))))
    t.write_text(RESUME)
    r = subprocess.run([sys.executable, "tools/keyword_coverage.py", "--file", str(f),
                        "--resume-text", str(t), "--semantic", "lexical", "--threshold", "75",
                        "--write"], capture_output=True, text=True, cwd=ROOT)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["pass"] is True
    assert out["keyword_coverage_score"] == 85.71 and out["parseability_score"] >= 80
    assert json.loads(f.read_text())["result"]["pass"] is True


def test_jsonld_extraction_and_linkedin_id():
    page = ('<html><script type="application/ld+json">{"@type":"JobPosting",'
            '"description":"&lt;p&gt;Build &lt;b&gt;RAG&lt;/b&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"}'
            '</script></html>')
    text, method = extract_description(page)
    assert method == "jsonld" and "Build RAG" in text and "- Python" in text
    assert linkedin_job_id("https://in.linkedin.com/jobs/view/x-at-y-4462102274", None) == "4462102274"


def test_missing_have_forces_revise_unless_accepted(tmp_path):
    f, t = tmp_path / "cov.json", tmp_path / "resume.txt"
    f.write_text(json.dumps(v2(req("Python", "covered"), req("Kafka", "missing_have", "preferred"))))
    t.write_text(RESUME)
    base = [sys.executable, "tools/keyword_coverage.py", "--file", str(f), "--resume-text", str(t),
            "--semantic", "lexical", "--threshold", "50"]
    r = subprocess.run(base, capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 5 and json.loads(r.stdout)["must_add"] == ["Kafka"]
    r = subprocess.run(base + ["--accept-missing-have", "no room on page"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0


def test_mark_applied_helpers(tmp_path):
    import mark_applied as ma
    assert ma.split_s3("s3://resumes/acme/ml_user.pdf") == ("resumes", "acme/ml_user.pdf")
    assert ma.split_s3("resume/acme/x.pdf") is None
    outside = tmp_path / "x.pdf"
    outside.write_bytes(b"%PDF")
    assert ma.delete_local([outside]) == [] and outside.exists()  # never outside the repo
