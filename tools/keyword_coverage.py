#!/usr/bin/env python3
"""keyword_coverage.py — ATS-style resume score: weighted requirement coverage plus
a parseability sub-score. Honest name: no tool can reproduce a specific employer's
ATS ranking; this measures the two things every ATS depends on — can it read the
resume, and does the resume contain the posting's requirements.

Two input formats (a coverage JSON written by /apply's keyword audit):

v1 (legacy, unchanged math):
    {"covered": [...], "synonym_only": [...], "missing_have": [...], "missing_gap": [...]}
    score = 100 × (covered + 0.5·synonym_only) / (all four)

v2:
    {"version": 2, "requirements": [
        {"term": "LangGraph", "priority": "required" | "preferred",
         "alternatives": ["LangChain", "CrewAI"],   # "X, Y, or equivalent" = ONE item
         "kind": "skill" | "eligibility",            # eligibility (years, degree, location)
                                                     #   is reported, not keyword-scored
         "status": "covered" | "synonym_only" | "missing_have" | "missing_gap",
         "evidence": "where it appears / why absent"}]}

    weights: required 3, preferred 1 · credit: covered 1, synonym_only 0.5, missing 0
    keyword_coverage_score = 100 × Σ weight·credit / Σ weight   (skill items only)

Honesty rails (enforced here, not left to the prompt), active with --resume-text:
  * a "covered" item whose term/alternatives are absent from the resume text is
    downgraded (to synonym_only if semantically supported, else missing_have);
  * a "synonym_only" item with no semantic support is downgraded to missing_have
    (embedding backend only — the lexical fallback is too weak to judge synonyms);
  * "missing_gap" is NEVER upgraded by anything — gaps stay gaps.

Semantic backend: fastembed (optional; `uv sync --group semantic`, ~130 MB model
download on first use) or a lexical fallback. Choose with --semantic.

Gate: pass = score ≥ threshold AND parseability ≥ parseability-threshold (when text given).
Defaults: KEYWORD_COVERAGE_THRESHOLD (85), PARSEABILITY_THRESHOLD (80) — chosen
defaults, not calibrated against interview outcomes yet.

Records the score on the application row when --application-id is given;
--write stores the full result back into the coverage file under "result".
Profile skills missing from the resume ("missing_have") must be added before the gate
finishes: exit 5 lists them under "must_add". Only --accept-missing-have "<reason>"
(e.g. no room on the 1-page template) lets the gate run with them still missing.

Exit codes: 0 pass · 3 below threshold · 5 revise (add must_add skills) · 1 error
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobhunt_common  # noqa: E402,F401  (loads .env so thresholds match headless runs)

WEIGHTS = {"required": 3.0, "preferred": 1.0}
CREDIT = {"covered": 1.0, "synonym_only": 0.5, "missing_have": 0.0, "missing_gap": 0.0}
STATUSES = tuple(CREDIT)
DEFAULT_THRESHOLD = 85.0
DEFAULT_PARSE_THRESHOLD = 80.0

# Semantic cut-offs (cosine for embeddings, coverage ratio for lexical).
EMB_SYNONYM_SUPPORT = 0.55
EMB_COVERED_AS_SYNONYM = 0.80
LEX_COVERED_AS_SYNONYM = 0.75


# ── text normalisation & literal matching ───────────────────────────────────

def normalize(text: str) -> str:
    t = text.lower()
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)  # re-join line-break hyphenation
    t = re.sub(r"\s+", " ", t)
    return t


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", text.lower())


def literal_present(term: str, norm_text: str, squashed_text: str) -> bool:
    nt = normalize(term).strip()
    if not nt:
        return False
    if len(nt) <= 3:  # short tokens (C++, AWS, SQL): word-boundary match only
        return re.search(r"(?<![a-z0-9])" + re.escape(nt) + r"(?![a-z0-9])", norm_text) is not None
    if nt in norm_text:
        return True
    st = squash(term)
    # squashed comparison catches extraction artefacts ("from-scratch"→"fromscratch",
    # "LiteLLM-based"→"LiteLLMbased"); only for longer terms to avoid false hits.
    return len(st) >= 6 and st in squashed_text


# ── semantic similarity ─────────────────────────────────────────────────────

_STOP = {"and", "or", "the", "a", "an", "of", "for", "to", "in", "with", "on", "x", "y",
         "experience", "knowledge", "familiarity", "strong", "understanding", "skills"}


def _tokens(s: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9+#]+", s.lower()):
        if w in _STOP or len(w) < 2:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > 4 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.add(w)
    return out


def chunk_text(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;:!?])\s+|\n+|•", text)
    return [p.strip() for p in parts if len(p.strip()) > 3]


class Semantic:
    def __init__(self, mode: str):
        self.backend = "lexical"
        self._model = None
        if mode in ("auto", "embeddings"):
            try:
                from fastembed import TextEmbedding  # type: ignore
                self._model = TextEmbedding("BAAI/bge-small-en-v1.5")
                self.backend = "embeddings"
            except Exception as e:  # noqa: BLE001
                if mode == "embeddings":
                    print(f"keyword_coverage: embeddings unavailable ({e}); using lexical",
                          file=sys.stderr)
        if mode == "off":
            self.backend = "off"
        self._cache: dict[str, list] = {}

    def _embed(self, texts: list[str]):
        import numpy as np  # fastembed depends on numpy
        missing = [t for t in texts if t not in self._cache]
        if missing:
            for t, v in zip(missing, self._model.embed(missing)):
                v = np.asarray(v, dtype=float)
                self._cache[t] = v / (np.linalg.norm(v) or 1.0)
        return [self._cache[t] for t in texts]

    def best(self, phrases: list[str], chunks: list[str]) -> tuple[float, str]:
        if self.backend == "off" or not chunks or not phrases:
            return 0.0, ""
        best_score, best_chunk = 0.0, ""
        if self.backend == "embeddings":
            cvecs = self._embed(chunks)
            for p in phrases:
                pv = self._embed([p])[0]
                for c, cv in zip(chunks, cvecs):
                    s = float(pv @ cv)
                    if s > best_score:
                        best_score, best_chunk = s, c
        else:
            for p in phrases:
                pt = _tokens(p)
                if not pt:
                    continue
                for c in chunks:
                    s = len(pt & _tokens(c)) / len(pt)
                    if s > best_score:
                        best_score, best_chunk = s, c
        return round(best_score, 3), best_chunk[:160]


# ── parseability ────────────────────────────────────────────────────────────

HEADINGS = {
    "summary": r"\b(professional summary|summary|profile|objective|about me)\b",
    "skills": r"\b(technical skills|skills|core competencies)\b",
    "experience": r"\b(experience|employment|work history)\b",
    "education": r"\b(education|academic)\b",
}
DATE_RX = (r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}\b"
           r"|\b(19|20)\d{2}\s*[–-]\s*((19|20)\d{2}|present|current)\b")


def parseability(text: str) -> dict:
    checks = {}
    garbage = len(re.findall(r"\(cid:\d+\)", text)) + text.count("�")
    checks["clean_text"] = {"points": 25 if garbage == 0 else 0, "max": 25,
                            "detail": f"{garbage} garbage markers"}
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    checks["email_literal"] = {"points": 15 if email else 0, "max": 15,
                               "detail": "found" if email else "no literal email"}
    phone = re.search(r"\+?\d[\d\s().-]{8,}\d", text)
    checks["phone_literal"] = {"points": 15 if phone else 0, "max": 15,
                               "detail": "found" if phone else "no literal phone"}
    low = text.lower()
    found = [k for k, rx in HEADINGS.items() if re.search(rx, low)]
    checks["section_headings"] = {"points": round(20 * len(found) / len(HEADINGS), 1),
                                  "max": 20, "detail": f"found {found}"}
    ndates = len(re.findall(DATE_RX, low))
    checks["dates"] = {"points": 15 if ndates >= 3 else 5 * ndates, "max": 15,
                       "detail": f"{ndates} parseable dates"}
    hyph = re.findall(r"([A-Za-z]{2,})-\n([a-z]{2,})", text)
    checks["hyphenation"] = {"points": max(0, 10 - 2 * len(hyph)), "max": 10,
                             "detail": [f"{a}-{b}" for a, b in hyph][:8] or "none"}
    score = round(sum(c["points"] for c in checks.values()), 1)
    return {"score": score, "checks": checks}


# ── scoring ─────────────────────────────────────────────────────────────────

def _pct(num: float, den: float) -> float:
    return 100.0 if den == 0 else round(100 * num / den, 2)


def score_v2(data: dict, text: str | None, sem: Semantic) -> dict:
    reqs = data.get("requirements") or []
    norm_t = normalize(text) if text else ""
    sq_t = squash(text) if text else ""
    chunks = chunk_text(text) if text else []
    downgrades, hints, eligibility, problems = [], [], [], []
    scored = []

    for r in reqs:
        term = str(r.get("term", "")).strip()
        prio = r.get("priority", "required")
        status = r.get("status")
        if prio not in WEIGHTS:
            problems.append(f"{term!r}: unknown priority {prio!r}")
            prio = "required"
        if status not in STATUSES:
            problems.append(f"{term!r}: unknown status {status!r} (treated as missing_gap)")
            status = "missing_gap"
        if r.get("kind") == "eligibility":
            eligibility.append({"term": term, "priority": prio, "status": status,
                                "evidence": r.get("evidence")})
            continue
        phrases = [term] + [str(a) for a in r.get("alternatives") or []]
        final = status
        if text is not None:
            if status == "covered" and not any(literal_present(p, norm_t, sq_t) for p in phrases):
                s, chunk = sem.best(phrases, chunks)
                cut = EMB_COVERED_AS_SYNONYM if sem.backend == "embeddings" else LEX_COVERED_AS_SYNONYM
                final = "synonym_only" if s >= cut else "missing_have"
                downgrades.append({"term": term, "from": "covered", "to": final,
                                   "why": "term not in resume text", "similarity": s,
                                   "closest": chunk})
            elif status == "synonym_only" and sem.backend == "embeddings":
                s, chunk = sem.best(phrases, chunks)
                if s < EMB_SYNONYM_SUPPORT:
                    final = "missing_have"
                    downgrades.append({"term": term, "from": "synonym_only", "to": final,
                                       "why": "no semantic support in resume", "similarity": s,
                                       "closest": chunk})
            elif status == "missing_have":
                s, chunk = sem.best(phrases, chunks)
                hints.append({"term": term, "closest": chunk, "similarity": s})
            # missing_gap: never touched.
        scored.append((prio, final, term))

    def cov(filter_prio=None):
        items = [(p, s) for p, s, _ in scored if filter_prio in (None, p)]
        num = sum(WEIGHTS[p] * CREDIT[s] for p, s in items)
        den = sum(WEIGHTS[p] for p, _ in items)
        return _pct(num, den)

    counts = {s: sum(1 for _, f, _ in scored if f == s) for s in STATUSES}
    by_status = {s: [t for _, f, t in scored if f == s] for s in STATUSES}
    return {
        "keyword_coverage_score": cov(),
        "required_coverage": cov("required"),
        "preferred_coverage": cov("preferred"),
        "counts": counts,
        "final_status": by_status,
        "downgrades": downgrades,
        "missing_have_hints": hints,
        "eligibility": eligibility,
        "problems": problems,
    }


def score_v1(data: dict) -> dict:
    covered = len(data.get("covered", []))
    synonym = len(data.get("synonym_only", []))
    missing_have = len(data.get("missing_have", []))
    missing_gap = len(data.get("missing_gap", []))
    denom = covered + synonym + missing_have + missing_gap
    return {
        "keyword_coverage_score": 100.0 if denom == 0 else round(100 * (covered + 0.5 * synonym) / denom, 2),
        "counts": {"covered": covered, "synonym_only": synonym,
                   "missing_have": missing_have, "missing_gap": missing_gap},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="coverage JSON file from the keyword audit (v1 or v2)")
    ap.add_argument("--covered", type=int)
    ap.add_argument("--synonym-only", type=int, default=0)
    ap.add_argument("--missing-have", type=int)
    ap.add_argument("--missing-gap", type=int)
    ap.add_argument("--resume-text", help="text layer dumped by verify_pdf.py --dump-text")
    ap.add_argument("--semantic", choices=["auto", "embeddings", "lexical", "off"], default="auto")
    ap.add_argument("--application-id")
    ap.add_argument("--write", action="store_true", help="store the result in --file under 'result'")
    ap.add_argument("--accept-missing-have", metavar="REASON",
                    help="run the gate even though profile skills are missing from the resume")
    ap.add_argument("--threshold", type=float,
                    default=float(os.environ.get("KEYWORD_COVERAGE_THRESHOLD", DEFAULT_THRESHOLD)))
    ap.add_argument("--parseability-threshold", type=float,
                    default=float(os.environ.get("PARSEABILITY_THRESHOLD", DEFAULT_PARSE_THRESHOLD)))
    args = ap.parse_args()

    text = Path(args.resume_text).read_text(errors="replace") if args.resume_text else None
    data = None
    if args.file:
        data = json.loads(Path(args.file).read_text())
        if data.get("version") == 2:
            sem = Semantic(args.semantic if text is not None else "off")
            res = score_v2(data, text, sem)
            res["version"] = 2
            res["semantic_backend"] = sem.backend if text is not None else "n/a (no --resume-text)"
        else:
            res = score_v1(data)
    elif args.covered is not None and args.missing_have is not None and args.missing_gap is not None:
        res = score_v1({"covered": [0] * args.covered, "synonym_only": [0] * args.synonym_only,
                        "missing_have": [0] * args.missing_have, "missing_gap": [0] * args.missing_gap})
    else:
        print("keyword_coverage: pass --file or all of --covered/--missing-have/--missing-gap",
              file=sys.stderr)
        sys.exit(1)

    parse = parseability(text) if text is not None else None
    score = res["keyword_coverage_score"]
    passed_kw = score >= args.threshold
    passed_parse = parse is None or parse["score"] >= args.parseability_threshold
    passed = passed_kw and passed_parse

    out = {**res, "threshold": args.threshold, "pass": passed}
    if parse is not None:
        out["parseability_score"] = parse["score"]
        out["parseability_threshold"] = args.parseability_threshold
        out["parseability"] = parse["checks"]
    if not passed:
        out["note"] = ("below threshold — revise honestly (emphasize real experience) or, if the "
                       "JD demands skills the candidate lacks, block instead of keyword-stuffing"
                       if not passed_kw else
                       "parseability below threshold — fix the template/text layer, not the keywords")
    else:
        out["note"] = None

    # v2 only: the legacy v1 format keeps its original gate behaviour.
    must_add = res.get("final_status", {}).get("missing_have") or []
    revise = bool(must_add) and not args.accept_missing_have
    if must_add:
        out["must_add"] = must_add
        out["accepted_missing_have"] = args.accept_missing_have
    if revise:
        out["pass"] = False
        out["note"] = ("revise: these skills are in the profile but not in the resume — add them "
                       "where they fit truthfully, recompile, and re-run (or pass "
                       "--accept-missing-have '<reason>')")

    if args.application_id:
        conn = jobhunt_common.get_conn()
        conn.execute(
            "UPDATE applications SET keyword_coverage_score=%s, updated_at=now() "
            "WHERE application_id=%s",
            (score, args.application_id),
        )
        conn.commit()

    if args.write and args.file and data is not None:
        data["result"] = out
        Path(args.file).write_text(json.dumps(data, indent=1))

    print(json.dumps(out))
    sys.exit(5 if revise else (0 if passed else 3))


if __name__ == "__main__":
    main()
