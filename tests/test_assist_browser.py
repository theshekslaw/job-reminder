"""assist_browser.py guard tests — fake cmux binary, no DB, no network, no live site."""

import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import assist_browser as ab  # noqa: E402

FAKE = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_CMUX_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if "eval" in args:
    script = args[args.index("--script") + 1]
    if "DataTransfer" in script:
        print(json.dumps(json.dumps({"ok": True, "name": "final_resume.pdf", "size": 9})))
    else:
        print(os.environ.get("FAKE_DESCRIBE", json.dumps({"found": True, "tag": "a"})))
else:
    print("ok")
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "cmux"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    logf = tmp_path / "calls.jsonl"
    monkeypatch.setenv("CMUX_BIN", str(fake))
    monkeypatch.setenv("FAKE_CMUX_LOG", str(logf))
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / "final_resume.pdf").write_bytes(b"%PDF-1.4\n")
    ctx = {"state": "PUBLISHED", "company": "Acme", "title": "ML", "url": "https://x.test/apply",
           "folder": folder, "pdf": folder / "final_resume.pdf"}
    monkeypatch.setattr(ab, "app_context", lambda app_id, need_published: ctx)

    def calls():
        return [json.loads(l) for l in logf.read_text().splitlines()] if logf.exists() else []
    return SimpleNamespace(calls=calls, folder=folder, monkeypatch=monkeypatch)


def describe_as(env, **info):
    env.monkeypatch.setenv("FAKE_DESCRIBE", json.dumps({"found": True, **info}))


def ns(cmd, **kw):
    return SimpleNamespace(cmd=cmd, application_id="a1", surface="surface:1", **kw)


@pytest.mark.parametrize("info,blocked", [
    ({"tag": "button", "type": "submit", "text": "Submit application", "inForm": True}, True),
    ({"tag": "button", "type": "", "text": "Send", "inForm": True}, True),
    ({"tag": "input", "type": "submit", "text": "Finish", "inForm": True}, True),
    ({"tag": "a", "type": "", "text": "Review and submit", "inForm": False}, True),
    ({"tag": "input", "type": "image", "text": "", "inForm": True}, True),
    ({"tag": "button", "type": "submit", "text": "Next", "inForm": True}, False),
    ({"tag": "button", "type": "button", "text": "Upload resume", "inForm": True}, False),
    ({"tag": "a", "type": "", "text": "Apply on company website", "inForm": False}, False),
])
def test_is_submit_like(info, blocked):
    assert ab.is_submit_like({"found": True, **info})[0] is blocked


def test_click_submit_refused_and_never_reaches_cmux(env):
    describe_as(env, tag="button", type="submit", text="Submit application", inForm=True)
    with pytest.raises(ab.Refused):
        ab.run(ns("click", selector="#go"))
    assert not any("click" in c for c in env.calls())  # only the describe eval ran


def test_click_next_allowed(env):
    describe_as(env, tag="button", type="submit", text="Next", inForm=True)
    ab.run(ns("click", selector="#next"))
    assert any(c[-2:] == ["click", "#next"] for c in env.calls())


def test_enter_key_refused(env):
    with pytest.raises(ab.Refused):
        ab.run(ns("press", key="Enter"))
    assert env.calls() == []


def test_password_fill_refused(env):
    describe_as(env, tag="input", type="password", name="pwd")
    with pytest.raises(ab.Refused):
        ab.run(ns("fill", selector="#pwd", text="hunter2"))
    assert not any("fill" in c for c in env.calls())


def test_upload_sends_only_approved_pdf_and_logs(env):
    res = ab.run(ns("upload", selector="input[type=file]"))
    assert res["uploaded"] is True and res["file"] == "final_resume.pdf"
    script = next(c for c in env.calls() if "eval" in c)[-1]
    assert "DataTransfer" in script and '"final_resume.pdf"' in script
    log = (env.folder / "assist_log.jsonl").read_text()
    assert '"cmd": "upload"' in log


def test_parse_eval_variants():
    assert ab.parse_eval(json.dumps({"found": True}))["found"] is True
    assert ab.parse_eval(json.dumps(json.dumps({"ok": 1})))["ok"] == 1
    assert ab.parse_eval(json.dumps({"result": json.dumps({"found": False})}))["found"] is False


@pytest.mark.parametrize("info,blocked", [
    ({"tag": "button", "type": "submit", "text": "Apply", "inForm": True}, True),
    ({"tag": "input", "type": "submit", "text": "Apply now", "inForm": True}, True),
    ({"tag": "button", "type": "", "text": "Easy Apply", "inForm": True}, True),
    ({"tag": "button", "type": "submit", "text": "Review", "inForm": True}, True),
    ({"tag": "a", "type": "", "text": "Apply", "inForm": False}, False),
    ({"tag": "button", "type": "button", "text": "Easy Apply", "inForm": False}, False),
])
def test_apply_inside_form_is_treated_as_submit(info, blocked):
    assert ab.is_submit_like({"found": True, **info})[0] is blocked
