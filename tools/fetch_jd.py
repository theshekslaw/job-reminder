#!/usr/bin/env python3
"""fetch_jd.py — fill in full job descriptions for scraped postings (zero-LLM).

Scrapers often store an empty or truncated description. Resume tailoring and the
ATS score need the full posting, so this tool fetches it:

  * LinkedIn sources  → the portal CLI's own `detail` command (public jobs-guest
                         endpoints, the same ones the scraper already uses). Also
                         records whether the posting is still active.
  * freehire sources   → the freehire CLI's `detail` (by slug), then the host below.
  * any other host     → only if tools/robots_check.py allows the path; the text is
                         taken from the page's schema.org JobPosting JSON-LD, then
                         from known description containers.
  * blocked / failed   → left as-is with a note; paste the JD into /apply instead.

The fetched text is UNTRUSTED third-party data. It is stored, never executed or
followed; links inside it are never fetched.

    uv run tools/fetch_jd.py --application-id <id> [--force]
    uv run tools/fetch_jd.py --missing [--limit 25] [--state DISCOVERED]

Exit codes: 0 ok (individual failures are reported in the JSON) · 1 error
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jobhunt_common import REPO_ROOT, get_conn  # noqa: E402

# A stored description at or under this length may be a scraper truncation.
TRUNCATION_SUSPECT = 2000
MAX_STORE = 20000
LINKEDIN_CLI = ".agents/skills/linkedin-search/cli/src/cli.ts"
FREEHIRE_CLI = ".agents/skills/freehire-search/cli/src/cli.ts"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")


# ── text extraction (pure functions, unit-tested) ────────────────────────────

def html_to_text(fragment: str) -> str:
    # JSON-LD descriptions are often entity-escaped HTML ("&lt;p&gt;…"): decode first.
    if "&lt;" in fragment and "<" not in fragment:
        fragment = html.unescape(fragment)
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", fragment)
    s = re.sub(r"(?i)<li[^>]*>", "\n- ", s)
    s = re.sub(r"(?i)<(br|/p|/li|/ul|/ol|/h[1-6]|/div|/tr)\b[^>]*>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = s.replace("\r", "")
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def _jsonld_objects(page: str):
    for m in re.finditer(
            r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page):
        try:
            data = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        stack = [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
            elif isinstance(obj, dict):
                yield obj
                if "@graph" in obj:
                    stack.append(obj["@graph"])


def extract_description(page: str) -> tuple[str | None, str]:
    """Return (text, method). Prefers structured data over markup heuristics."""
    for obj in _jsonld_objects(page):
        t = obj.get("@type")
        types = t if isinstance(t, list) else [t]
        if "JobPosting" in types and obj.get("description"):
            return html_to_text(str(obj["description"])), "jsonld"
    for pat, name in (
        (r'(?is)class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', "linkedin-markup"),
        (r'(?is)<div[^>]+class="[^"]*job[-_]?description[^"]*"[^>]*>(.*?)</div>\s*</div>', "job-description-div"),
        (r'(?is)<section[^>]+class="[^"]*job[-_]?description[^"]*"[^>]*>(.*?)</section>', "job-description-section"),
    ):
        m = re.search(pat, page)
        if m:
            text = html_to_text(m.group(1))
            if len(text) > 200:
                return text, name
    return None, "none"


def linkedin_job_id(url: str | None, source_id: str | None) -> str | None:
    for cand in (source_id or "", url or ""):
        m = re.search(r"(\d{8,})", cand)
        if m:
            return m.group(1)
    return None


# ── fetchers ────────────────────────────────────────────────────────────────

def fetch_linkedin(job_id: str) -> dict:
    r = subprocess.run(["bun", "run", LINKEDIN_CLI, "detail", job_id, "--format", "json"],
                       capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    if r.returncode != 0:
        return {"ok": False, "note": f"linkedin detail failed: {r.stderr.strip()[:200]}"}
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "note": "linkedin detail returned non-JSON"}
    desc = (d.get("description") or "").strip()
    if not desc:
        return {"ok": False, "note": "linkedin detail had no description",
                "active": d.get("isActive")}
    return {"ok": True, "text": desc, "method": "linkedin-detail", "active": d.get("isActive")}


def fetch_freehire(slug: str) -> dict:
    r = subprocess.run(["bun", "run", FREEHIRE_CLI, "detail", slug, "--format", "json"],
                       capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    if r.returncode != 0:
        return {"ok": False, "note": f"freehire detail failed: {r.stderr.strip()[:200]}"}
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "note": "freehire detail returned non-JSON"}
    if isinstance(d, list):
        d = d[0] if d else {}
    desc = (d.get("description") or "").strip()
    if not desc:
        return {"ok": False, "note": "freehire detail had no description"}
    return {"ok": True, "text": desc, "method": "freehire-detail", "active": None}


def fetch_generic(url: str) -> dict:
    import robots_check  # local module; same gate the web-research escalation uses
    rc, msg = robots_check.gate(url)
    if rc != 0:
        return {"ok": False, "note": f"robots: {msg}"}
    r = subprocess.run(
        ["curl", "-sS", "-L", "--max-redirs", "5", "--max-time", "20", "-A", BROWSER_UA,
         "-H", "Accept: text/html,*/*", "-w", "\n%{http_code}", "--", url],
        capture_output=True, text=True, timeout=30)
    body, _, code = r.stdout.rpartition("\n")
    if code.strip() != "200":
        return {"ok": False, "note": f"HTTP {code.strip() or 'error'}"}
    text, method = extract_description(body)
    if not text:
        return {"ok": False, "note": "no JobPosting JSON-LD or known description container"}
    return {"ok": True, "text": text, "method": method, "active": None}


def fetch_one(sources: list[tuple[str, str, str]]) -> dict:
    """sources: [(source, source_id, url)]. Portal CLIs first (LinkedIn, freehire),
    then the posting's own host where robots.txt allows."""
    tried = []

    def rank(s):
        if "linkedin" in (s[0] + (s[2] or "")):
            return 0
        return 1 if "freehire" in s[0] else 2
    for source, source_id, url in sorted(sources, key=rank):
        if "freehire" in source and source_id:
            res = fetch_freehire(source_id)
            if not res["ok"] and url and url.startswith(("http://", "https://")):
                tried.append(f"{source}: {res['note']}")
                res = fetch_generic(url)
        elif "linkedin" in (source + (url or "")):
            jid = linkedin_job_id(url, source_id)
            if not jid:
                tried.append("linkedin: no job id")
                continue
            res = fetch_linkedin(jid)
        elif url and url.startswith(("http://", "https://")):
            res = fetch_generic(url)
        else:
            tried.append(f"{source}: no URL")
            continue
        if res["ok"]:
            res["tried"] = tried
            return res
        tried.append(f"{source}: {res['note']}")
        if res.get("active") is False:
            return {"ok": False, "note": "posting closed", "active": False, "tried": tried}
    return {"ok": False, "note": "; ".join(tried) or "no sources", "tried": tried}


# ── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--application-id")
    g.add_argument("--missing", action="store_true",
                   help="fetch for postings whose description is empty or possibly truncated")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--state", default="DISCOVERED",
                    help="with --missing: only applications in this state (default DISCOVERED)")
    ap.add_argument("--force", action="store_true", help="refetch even if already fetched")
    args = ap.parse_args()

    conn = get_conn()
    if args.application_id:
        ids = [args.application_id]
    else:
        ids = [r[0] for r in conn.execute(
            """SELECT p.application_id FROM job_postings p
               JOIN applications a USING (application_id)
               WHERE a.state = %s
                 AND (p.description IS NULL OR length(p.description) <= %s)
                 AND (p.description_fetched_at IS NULL OR %s)
                 AND p.posting_active IS DISTINCT FROM false
               ORDER BY p.scraped_at DESC LIMIT %s""",
            (args.state, TRUNCATION_SUSPECT, args.force, args.limit)).fetchall()]

    results = []
    for app_id in ids:
        sources = conn.execute(
            "SELECT source, source_id, url FROM job_sources WHERE application_id=%s "
            "ORDER BY first_seen_at", (app_id,)).fetchall()
        cur = conn.execute("SELECT coalesce(length(description),0) FROM job_postings "
                           "WHERE application_id=%s", (app_id,)).fetchone()
        if cur is None:
            results.append({"application_id": app_id, "ok": False, "note": "unknown application"})
            continue
        res = fetch_one(sources)
        if res["ok"] and (args.force or len(res["text"]) > cur[0]):
            conn.execute(
                """UPDATE job_postings SET description=%s, description_source=%s,
                          description_fetched_at=now(), posting_active=%s,
                          description_fetch_note=NULL
                   WHERE application_id=%s""",
                (res["text"][:MAX_STORE], res["method"], res.get("active"), app_id))
            results.append({"application_id": app_id, "ok": True, "method": res["method"],
                            "chars": len(res["text"]), "active": res.get("active")})
        else:
            note = res.get("note") if not res["ok"] else "fetched text not longer than stored"
            conn.execute(
                """UPDATE job_postings SET description_fetched_at=now(),
                          description_fetch_note=%s,
                          posting_active=COALESCE(%s, posting_active)
                   WHERE application_id=%s""",
                (note, res.get("active"), app_id))
            results.append({"application_id": app_id, "ok": False, "note": note,
                            "active": res.get("active")})
        conn.commit()

    print(json.dumps({"fetched": sum(r["ok"] for r in results),
                      "failed": sum(not r["ok"] for r in results),
                      "closed": sum(r.get("active") is False for r in results),
                      "results": results}, indent=2))


if __name__ == "__main__":
    main()
