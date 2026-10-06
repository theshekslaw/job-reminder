#!/usr/bin/env python3
"""assist_browser.py — the ONLY path through which Claude drives a browser to fill
an application form (assisted apply). It wraps the cmux in-app browser CLI.

Contract (CLAUDE.md invariant 1): the browser may FILL forms; only the HUMAN submits;
Claude never logs in or handles credentials.

What this wrapper enforces (an accident guard, not a sandbox — cmux itself is not
locked down; the human Submit click is the real guarantee):
  * refuses clicks on submit-like controls (type=submit / image, a <button> with no
    type inside a <form>, or text such as Submit, Send application, Finish, Confirm);
    "Next / Continue / Back" steps of multi-page forms are allowed ("Apply"/"Review"
    inside a form are treated as submission);
  * refuses the Enter key (implicit form submission) — only navigation keys pass;
  * refuses typing into password / one-time-code fields — the user logs in;
  * exposes NO raw JavaScript eval — only internally generated, fixed scripts;
  * uploads ONLY the approved resume for the given application (the PDF publish_guard
    archived), never an arbitrary local file;
  * requires the application to be PUBLISHED (human-approved) before opening;
  * logs every action to documents/applications/<company>_<title>/assist_log.jsonl.

cmux must be running and the caller must be a process started inside cmux
(cmux rejects other callers). Override the binary with CMUX_BIN.

    uv run tools/assist_browser.py open       --application-id ID [--url URL]
    uv run tools/assist_browser.py snapshot   --application-id ID --surface S
    uv run tools/assist_browser.py fill       --application-id ID --surface S --selector CSS --text T
    uv run tools/assist_browser.py select     --application-id ID --surface S --selector CSS --value V
    uv run tools/assist_browser.py check      --application-id ID --surface S --selector CSS
    uv run tools/assist_browser.py click      --application-id ID --surface S --selector CSS
    uv run tools/assist_browser.py press      --application-id ID --surface S --key Tab
    uv run tools/assist_browser.py frame      --application-id ID --surface S --selector CSS|main
    uv run tools/assist_browser.py goto       --application-id ID --surface S --url URL
    uv run tools/assist_browser.py upload     --application-id ID --surface S --selector CSS
    uv run tools/assist_browser.py screenshot --application-id ID --surface S

Exit codes: 0 ok · 1 error · 4 refused by guard
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jobhunt_common import REPO_ROOT, canonical  # noqa: E402

CMUX_APP_BIN = "/Applications/cmux.app/Contents/Resources/bin/cmux"
STRONG_SUBMIT = re.compile(
    r"\b(submit|send( my)? application|finish|confirm|complete( my)? application|"
    r"place order|apply now and submit|review and submit)\b", re.I)
# In-form submit controls: only unambiguous step words pass ("Apply" inside a form is
# often the final submission, so it is NOT a step word here).
STEP_OK = re.compile(r"^\s*(next|continue|save and continue|back|previous)\b", re.I)
ALLOWED_KEYS = {"Tab", "Shift+Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft",
                "ArrowRight", "Home", "End", "PageUp", "PageDown", "Space"}
SECRET_FIELD = re.compile(r"password|passcode|otp|one[- ]?time|verification code|2fa|cvv", re.I)

DESCRIBE_JS = """(() => {
  const el = document.querySelector(%s);
  if (!el) return JSON.stringify({found: false});
  const form = el.closest('form');
  return JSON.stringify({
    found: true, tag: el.tagName.toLowerCase(), type: (el.getAttribute('type') || '').toLowerCase(),
    text: (el.innerText || el.value || '').trim().slice(0, 120),
    aria: el.getAttribute('aria-label') || '', name: el.getAttribute('name') || '',
    id: el.id || '', autocomplete: el.getAttribute('autocomplete') || '',
    inForm: !!form, formAction: form ? (form.getAttribute('action') || '') : ''
  });
})()"""

UPLOAD_JS = """(() => {
  const el = document.querySelector(%(sel)s);
  if (!el) return JSON.stringify({ok: false, error: 'no element'});
  if ((el.getAttribute('type') || '').toLowerCase() !== 'file')
    return JSON.stringify({ok: false, error: 'not a file input'});
  const bin = atob(%(b64)s); const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const file = new File([bytes], %(name)s, {type: 'application/pdf'});
  const dt = new DataTransfer(); dt.items.add(file); el.files = dt.files;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  const f = el.files[0];
  return JSON.stringify({ok: !!f && el.files.length === 1 && f.name === %(name)s,
                         name: f ? f.name : null, size: f ? f.size : 0});
})()"""


class Refused(Exception):
    pass


# ── guard logic (pure, unit-tested) ─────────────────────────────────────────

def is_submit_like(info: dict) -> tuple[bool, str]:
    if not info.get("found"):
        return False, "element not found"
    label = " ".join(str(info.get(k) or "") for k in ("text", "aria", "name", "id"))
    if STRONG_SUBMIT.search(label):
        return True, f"label looks like final submission: {label.strip()[:60]!r}"
    tag, typ = info.get("tag"), info.get("type")
    if typ == "image" or (tag == "input" and typ == "submit"):
        if STEP_OK.search(info.get("text") or info.get("aria") or ""):
            return False, "multi-step navigation control"
        return True, f"{tag}[type={typ}] submits the form"
    if tag == "button" and info.get("inForm") and typ in ("", "submit"):
        if STEP_OK.search(info.get("text") or info.get("aria") or ""):
            return False, "multi-step navigation control"
        return True, "a <button> without type=button inside a <form> submits it"
    return False, "not a submit control"


def key_allowed(key: str) -> bool:
    return key in ALLOWED_KEYS


def is_secret_field(info: dict) -> bool:
    if info.get("type") == "password" or info.get("autocomplete") in ("current-password",
                                                                     "new-password", "one-time-code"):
        return True
    return bool(SECRET_FIELD.search(" ".join(str(info.get(k) or "")
                                             for k in ("name", "id", "aria"))))


def upload_script(selector: str, pdf: Path) -> str:
    return UPLOAD_JS % {"sel": json.dumps(selector),
                        "b64": json.dumps(base64.b64encode(pdf.read_bytes()).decode()),
                        "name": json.dumps(pdf.name)}


# ── cmux plumbing ───────────────────────────────────────────────────────────

def cmux_bin() -> str:
    return os.environ.get("CMUX_BIN") or shutil.which("cmux") or CMUX_APP_BIN


def cmux(*args: str, timeout: int = 60) -> str:
    r = subprocess.run([cmux_bin(), "browser", *args], capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()
        if "only processes started inside cmux" in msg:
            msg += " — run Claude Code from a cmux terminal to use assisted apply"
        raise RuntimeError(f"cmux browser {args[:2]} failed: {msg[:300]}")
    return r.stdout.strip()


def parse_eval(out: str):
    """cmux eval output → JSON value. Tolerates a JSON-encoded string or a wrapper."""
    val = out
    for _ in range(3):
        try:
            val = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            break
        if isinstance(val, dict) and "result" in val and "found" not in val and "ok" not in val:
            val = val["result"]
        if not isinstance(val, str):
            return val
    raise RuntimeError(f"could not parse cmux eval output: {out[:200]}")


def describe(surface: str, selector: str) -> dict:
    return parse_eval(cmux(surface, "eval", "--script", DESCRIBE_JS % json.dumps(selector)))


# ── application context ─────────────────────────────────────────────────────

def app_context(app_id: str, need_published: bool) -> dict:
    from jobhunt_common import get_conn
    conn = get_conn()
    row = conn.execute(
        """SELECT a.state, p.company, p.title,
                  (SELECT url FROM job_sources WHERE application_id = a.application_id
                   ORDER BY first_seen_at LIMIT 1)
           FROM applications a JOIN job_postings p USING (application_id)
           WHERE a.application_id = %s""", (app_id,)).fetchone()
    if row is None:
        raise Refused(f"unknown application {app_id}")
    state, company, title, url = row
    if need_published and state != "PUBLISHED":
        raise Refused(f"state is {state}: assisted apply needs a human-approved resume "
                      f"(PUBLISHED). Approve it first: make run ARGS=\"approve {app_id}\"")
    folder = REPO_ROOT / "documents" / "applications" / f"{canonical(company)}_{canonical(title)}"
    return {"state": state, "company": company, "title": title, "url": url, "folder": folder,
            "pdf": folder / "final_resume.pdf"}


def log(ctx: dict, entry: dict) -> None:
    ctx["folder"].mkdir(parents=True, exist_ok=True)
    with open(ctx["folder"] / "assist_log.jsonl", "a") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}) + "\n")


# ── commands ────────────────────────────────────────────────────────────────

def run(args) -> dict:
    ctx = app_context(args.application_id, need_published=args.cmd in ("open", "upload"))
    s = getattr(args, "surface", None)

    if args.cmd == "open":
        url = args.url or ctx["url"]
        if not url:
            raise Refused("no apply URL recorded; pass --url")
        out = cmux("open", url, "--focus", "true")
        res = {"opened": url, "cmux": out,
               "reminder": "Log in yourself if the site asks. Claude never handles credentials."}
    elif args.cmd == "snapshot":
        res = {"snapshot": cmux(s, "snapshot", "--interactive", "--compact")}
    elif args.cmd == "fill":
        info = describe(s, args.selector)
        if is_secret_field(info):
            raise Refused("refusing to type into a password / verification-code field — "
                          "the user enters credentials themselves")
        cmux(s, "fill", args.selector, args.text)
        res = {"filled": args.selector, "chars": len(args.text)}
    elif args.cmd == "select":
        cmux(s, "select", args.selector, args.value)
        res = {"selected": args.selector, "value": args.value}
    elif args.cmd == "check":
        info = describe(s, args.selector)
        submit, why = is_submit_like(info)
        if submit:
            raise Refused(f"refusing to check a submit control: {why}")
        cmux(s, "check", args.selector)
        res = {"checked": args.selector}
    elif args.cmd == "click":
        info = describe(s, args.selector)
        submit, why = is_submit_like(info)
        if submit:
            raise Refused(f"refusing to click — {why}. Only the user submits.")
        if not info.get("found"):
            raise Refused(f"element not found: {args.selector}")
        cmux(s, "click", args.selector)
        res = {"clicked": args.selector, "why_allowed": why}
    elif args.cmd == "press":
        if not key_allowed(args.key):
            raise Refused(f"key {args.key!r} not allowed (Enter can submit a form); "
                          f"allowed: {sorted(ALLOWED_KEYS)}")
        cmux(s, "press", args.key)
        res = {"pressed": args.key}
    elif args.cmd == "frame":
        res = {"frame": cmux(s, "frame", args.selector)}
    elif args.cmd == "goto":
        res = {"navigated": args.url, "cmux": cmux(s, "goto", args.url)}
    elif args.cmd == "upload":
        pdf = ctx["pdf"]
        if not pdf.exists():
            raise Refused(f"approved resume not found at {pdf} (publish_guard archives it there)")
        out = parse_eval(cmux(s, "eval", "--script", upload_script(args.selector, pdf), timeout=120))
        if not (isinstance(out, dict) and out.get("ok")):
            res = {"uploaded": False, "detail": out,
                   "manual": f"Attach manually: {pdf.resolve()}"}
        else:
            res = {"uploaded": True, "file": out.get("name"), "bytes": out.get("size")}
    elif args.cmd == "screenshot":
        path = ctx["folder"] / f"assist_{time.strftime('%Y%m%d_%H%M%S')}.png"
        ctx["folder"].mkdir(parents=True, exist_ok=True)
        cmux(s, "screenshot", "--out", str(path))
        res = {"screenshot": str(path)}
    else:  # pragma: no cover - argparse restricts
        raise Refused(f"unknown command {args.cmd}")

    log(ctx, {"cmd": args.cmd, **{k: v for k, v in vars(args).items()
                                  if k in ("selector", "url", "key", "value")},
              "result": {k: v for k, v in res.items() if k != "snapshot"}})
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, *extra, surface=True):
        p = sub.add_parser(name)
        p.add_argument("--application-id", required=True)
        if surface:
            p.add_argument("--surface", required=True)
        for e in extra:
            p.add_argument(f"--{e}", required=True)
        return p

    p = add("open", surface=False)
    p.add_argument("--url")
    add("snapshot")
    add("fill", "selector", "text")
    add("select", "selector", "value")
    add("check", "selector")
    add("click", "selector")
    add("press", "key")
    add("frame", "selector")
    add("goto", "url")
    add("upload", "selector")
    add("screenshot")
    args = ap.parse_args()

    try:
        print(json.dumps(run(args), indent=2))
    except Refused as e:
        print(json.dumps({"refused": str(e)}), file=sys.stderr)
        sys.exit(4)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
